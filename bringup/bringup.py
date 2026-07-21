# SPDX-FileCopyrightText: © 2026 Joonatan Alanampa
# SPDX-License-Identifier: Apache-2.0
"""
TinyRV32 silicon bring-up — MicroPython for the TinyTapeout demo board RP2040.

Copy this file and sw/hello.bin to the demo board's filesystem, then:

    >>> import bringup as bu
    >>> bu.main()                  # flash + boot + self-check, PASS/FAIL table
    >>> bu.flash_program("hello.bin")   # just program the flash
    >>> bu.run()                   # just boot and listen
    >>> bu.sweep_fmax()            # how fast does this die really run?

What is being proven, in order — each step is a precondition for the next, so
the first FAIL is the one that matters:

  1. bus drive    the RP2040 can drive the uio bank at all (Pmod seated?).
  2. flash id     W25Q128 answers 9Fh with EF 40 18 -> Pmod wiring is good.
  3. flash QE     status-2 bit 1. THE hazard on this chip: sw/hello.c writes
                  QSPI_CFG=3 as its first statement, so if QE is clear the
                  very next instruction fetch is a 6Bh quad read into a device
                  that is not in quad mode -> the CPU hangs with no output and
                  nothing to see. Checked, and set if the user allows it.
  4. psram        ID + a write/read pattern. crt0 puts .data, .bss AND THE
                  STACK here; dead PSRAM means hello.c faults before printing.
  5. program      erase + page-program + full read-back verify of hello.bin.
  6. bus release  RP2040 uio back to inputs BEFORE the ASIC is enabled, or the
                  two of them fight over the same eight pins.
  7. uart         the banner and fib(10)=55, decoded from uo[0].
  8. halted+leds  ecall raised uo[1]; LEDs (uo[7:2]) hold 55 = 0b110111.

Two facts drive the awkward parts of the design:

  * clock_project_PWM() retunes the RP2040 system clock and settles for a
    NEARBY frequency; it only warns. UART framing dies at ~3% clock error, so
    the achieved PWM frequency is read back and the baud rate is derived from
    THAT, never from the request.
  * uo[0] is not guaranteed to land on a UART-capable RP2040 pin (the GPIO
    number moves between demo-board revisions). If machine.UART cannot be
    built on it we bit-bang the receiver instead — and since MicroPython
    cannot sample 115200 baud (8.7 us/bit) by hand, we simply CLOCK THE CHIP
    SLOWER so the baud rate comes down with it. The CPU does not care; only
    wall-clock time changes. UART_DIV is fixed in the RTL, so baud tracks the
    project clock exactly.

Constants come from src/project.sv and sw/hello.c; keep them in sync.
"""

import time

try:
    from machine import Pin
except ImportError:  # pragma: no cover - lets the file be imported on a host
    Pin = None

# ---------------------------------------------------------------- design facts
DESIGN = "tt_um_joonatanalanampa_rv32"
CLK_HZ = 25_000_000  # info.yaml clock_hz
UART_DIV = 217  # rv32_core #(.UART_DIV(217)) -> 115200 baud at 25 MHz

# uio bank — the TinyTapeout QSPI Pmod pinout (src/project.sv header)
CS_FLASH = 0
SD0 = 1  # MOSI in 1-bit mode
SD1 = 2  # MISO in 1-bit mode
SCK = 3
SD2 = 4
SD3 = 5
CS_RAM = 6
CS2 = 7

# The RP2040 drives everything except SD1 while it owns the bus.
BUS_OE = (1 << CS_FLASH) | (1 << SD0) | (1 << SCK) | (1 << CS_RAM) | (1 << CS2)
CS_IDLE = (1 << CS_FLASH) | (1 << CS_RAM) | (1 << CS2)  # all chip selects high

# uo bank
BIT_UART = 0
BIT_HALTED = 1
LED_LSB = 2  # uo[7:2] = led[5:0]

# expectations
FLASH_JEDEC = b"\xef\x40\x18"  # W25Q128JV
PSRAM_MFR = 0x0D  # AP Memory (APS6404L)
BANNER = "Hello from my own CPU!"
FIB_LINE = "fib(10)=55"
LED_EXPECT = 55  # hello.c: LED_REG = fib(10)

# flash commands
CMD_JEDEC = 0x9F
CMD_WREN = 0x06
CMD_RDSR1 = 0x05
CMD_RDSR2 = 0x35
CMD_WRSR2 = 0x31
CMD_SECTOR_ERASE = 0x20
CMD_PAGE_PROGRAM = 0x02
CMD_READ = 0x03
QE_BIT = 0x02  # status-2 bit 1

# bit-banged-receiver target baud. 4800 gives ~208 us/bit, which MicroPython
# can sample comfortably; 115200 (8.7 us/bit) it cannot.
BITBANG_BAUD = 4800


def clock_for_baud(baud):
    """Project clock that yields `baud`, given the RTL's fixed divider."""
    return int(baud * UART_DIV)


# ------------------------------------------------------- demo-board API shim
# Written against tt-micropython-firmware v2.0.0 (repo commit f34d9f0,
# microcotb 81f2498) — bringup/README.md records what was read there. Every
# call still probes and falls back: the chips land ~mid-2027 and this API has
# drifted before. If a future firmware renames something, this is the only
# section that should need touching.
#
# Firmware facts this shim depends on:
#   * DemoBoard.get() is the singleton accessor.
#   * tt.ui_in / tt.uo_out / tt.uio_in / tt.uio_out / tt.uio_oe_pico are
#     microcotb IO ports, not ints: read with int(), write with .value = x
#     (DemoBoard.__setattr__ forwards `tt.uio_in = x` to the port too).
#     port[i] is a sampled Logic BIT, LSB-indexed — NOT a machine.Pin.
#   * Raw machine.Pin objects live at tt.pins.uo_out<N>.raw_pin. The bidir
#     pins are tt.pins.uio0 .. uio7 (NOT `uio_in5` — the docstring in
#     demoboard.py says that, but GPIOMap defines `uio0`..`uio7`).
#   * RPMode has SAFE / ASIC_RP_CONTROL / ASIC_MANUAL_INPUTS only. There is
#     no ASIC_ON_BOARD.
#   * ASIC_RP_CONTROL (Pins.begin_asiconboard) turns only the ui_in pins into
#     outputs; the uio bank stays input. Driving the QSPI Pmod therefore means
#     setting uio_oe_pico ourselves — and clearing it again before the ASIC
#     is enabled, or both ends drive the same eight pins.
#   * clock_project_PWM() returns the PWM object; .freq() is the frequency
#     actually achieved, which is what baud must be derived from.


class Board:
    def __init__(self, tt):
        self.tt = tt
        self._oe = 0

    # -- construction ----------------------------------------------------
    @classmethod
    def open(cls):
        from ttboard.demoboard import DemoBoard

        try:
            tt = DemoBoard.get()  # singleton accessor
        except AttributeError:
            tt = DemoBoard()
        self = cls(tt)
        self._take_control()
        return self

    def _take_control(self):
        try:
            from ttboard.mode import RPMode

            self.tt.mode = RPMode.ASIC_RP_CONTROL
        except Exception as e:  # noqa: BLE001 - firmware variance is expected
            log("note: could not set ASIC_RP_CONTROL (%s)" % e)

    # -- design selection -------------------------------------------------
    def select(self, name=DESIGN):
        shuttle = self.tt.shuttle
        proj = None
        try:
            if shuttle.has(name):
                proj = shuttle.get(name)
        except AttributeError:
            pass
        if proj is None:
            proj = getattr(shuttle, name, None)  # ProjectMux.__getattr__
        if proj is None and hasattr(shuttle, "find"):
            hits = shuttle.find(name)
            proj = hits[0] if hits else None
        if proj is None:
            raise RuntimeError(
                "%s not found on this shuttle — is the firmware's shuttle "
                "index up to date?" % name
            )
        proj.enable()
        return proj

    def deselect(self):
        """Disconnect every design from the pins before we drive the bus.

        Holding reset is NOT enough. src/project.sv ties uio_oe bits 0, 3, 6
        and 7 to constant 1, so the ASIC drives CS0, SCK, CS1 and CS2 whenever
        it is connected — reset or not. Only the TT mux can take it off the
        bus, and shuttle.disable() does that (selects project 0, holds cena
        low). Without this the RP2040 and the die would fight over the same
        four pins for the whole programming run.
        """
        try:
            self.tt.shuttle.disable()
            return True
        except AttributeError:
            log("warning: shuttle.disable() missing — cannot guarantee the die")
            log("         is off the uio bank while programming")
            return False

    # -- clock / reset -----------------------------------------------------
    def clock(self, hz):
        """Start the project clock; return the frequency ACTUALLY achieved.

        clock_project_PWM only logs a warning when it cannot hit the request,
        and UART framing has no tolerance for that, so the caller must use the
        returned value rather than what it asked for.
        """
        pwm = self.tt.clock_project_PWM(hz)
        try:
            actual = pwm.freq()
        except AttributeError:
            actual = hz
        return float(actual)

    def clock_stop(self):
        try:
            self.tt.clock_project_stop()
        except AttributeError:
            pass

    def reset_pulse(self, settle_ms=5):
        self.tt.reset_project(True)
        time.sleep_ms(settle_ms)
        self.tt.reset_project(False)

    # -- uio bank ----------------------------------------------------------
    def bus_take(self, oe=BUS_OE, idle=CS_IDLE):
        """Point the RP2040 at the QSPI Pmod: chip selects high, then drive."""
        self.write_uio(idle)
        self.set_oe(oe)
        self.write_uio(idle)

    def bus_release(self):
        """Hand the eight uio pins back before the ASIC is enabled."""
        self.set_oe(0)

    def set_oe(self, mask):
        self._oe = mask
        try:
            self.tt.uio_oe_pico.value = mask
            return
        except AttributeError:
            pass
        # older/newer firmware without the OE port: set pin modes by hand
        pins = getattr(self.tt, "pins", None)
        if pins is None:
            raise RuntimeError("no uio_oe_pico and no tt.pins — cannot drive uio")
        for i in range(8):
            p = getattr(pins, "uio%d" % i, None)
            if p is None:
                raise RuntimeError("cannot resolve tt.pins.uio%d" % i)
            p.mode = Pin.OUT if (mask >> i) & 1 else Pin.IN

    def get_oe(self):
        try:
            return int(self.tt.uio_oe_pico)
        except (TypeError, AttributeError):
            return self._oe

    def write_uio(self, value):
        self.tt.uio_in.value = value

    def read_uio(self):
        try:
            return int(self.tt.uio_out)
        except (TypeError, AttributeError):
            return int(self.tt.uio_in)

    # -- uo bank -----------------------------------------------------------
    def get_uo(self):
        try:
            return int(self.tt.uo_out)  # IO.__int__ -> reads the port
        except (TypeError, AttributeError):
            return int(self.tt.uo_out.value)

    def uo_pin(self, index):
        """Raw machine.Pin for uo[index], or None.

        NOTE tt.uo_out[index] is *not* one — the ports are microcotb IO
        objects, so indexing returns a sampled Logic bit. Pins live in tt.pins.
        """
        pins = getattr(self.tt, "pins", None)
        cand = getattr(pins, "uo_out%d" % index, None) if pins is not None else None
        if cand is None:
            return None
        raw = getattr(cand, "raw_pin", None)  # StandardPin wraps machine.Pin
        if raw is not None:
            return raw
        if Pin is not None and isinstance(cand, Pin):
            return cand
        return None


# ------------------------------------------------------------ bit-banged SPI
# Mode 0, MSB first, one bit = two port writes (data+SCK low, then SCK high).
# The RP2040 has hardware SPI, but it cannot be used here: the uio bank lands
# on a GPIO run whose SPI-capable functions do not line up with this Pmod's
# SCK/MOSI/MISO assignment (and the GPIO numbers move between board revs).


class Spi:
    def __init__(self, board):
        self.board = board
        self.shadow = CS_IDLE

    def _w(self, value):
        self.shadow = value
        self.board.write_uio(value)

    def _cs(self, bit, level):
        v = self.shadow
        v = (v | (1 << bit)) if level else (v & ~(1 << bit))
        self._w(v)

    def xfer(self, tx, rx_len=0, cs=CS_FLASH):
        """One transaction: assert CS, shift out `tx`, shift in `rx_len`."""
        base = (self.shadow | CS_IDLE) & ~(1 << SCK)
        self._w(base)
        self._cs(cs, 0)
        base = self.shadow & ~(1 << SCK)

        for byte in tx:
            for i in range(7, -1, -1):
                b = (base & ~(1 << SD0)) | (((byte >> i) & 1) << SD0)
                self._w(b)  # data valid, SCK low
                self._w(b | (1 << SCK))  # rising edge: device samples
                base = b
        # Return SCK low before reading. Without this the first read bit gets
        # no rising edge of its own (the byte loop leaves SCK high), so the
        # transaction clocks 7 edges for 8 bits and every response is shifted.
        self._w(base)
        rx = bytearray()
        for _ in range(rx_len):
            v = 0
            for _ in range(8):
                self._w(base | (1 << SCK))
                v = (v << 1) | ((self.board.read_uio() >> SD1) & 1)
                self._w(base)
            rx.append(v)
        self._cs(cs, 1)
        return bytes(rx)

    # -- flash helpers -----------------------------------------------------
    def wait_ready(self, timeout_ms=5000):
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if not (self.xfer(bytes([CMD_RDSR1]), 1)[0] & 1):  # WIP
                return True
            time.sleep_ms(1)
        return False

    def jedec(self):
        return self.xfer(bytes([CMD_JEDEC]), 3)

    def read_sr2(self):
        return self.xfer(bytes([CMD_RDSR2]), 1)[0]

    def write_sr2(self, value):
        self.xfer(bytes([CMD_WREN]))
        self.xfer(bytes([CMD_WRSR2, value]))
        return self.wait_ready()

    def flash_read(self, addr, length):
        return self.xfer(
            bytes([CMD_READ, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF]),
            length,
        )

    def psram_id(self):
        # APS6404: 9Fh + 24-bit address, then MFR, KGD, EID[47:0]
        return self.xfer(bytes([CMD_JEDEC, 0, 0, 0]), 2, cs=CS_RAM)

    def psram_write(self, addr, data):
        self.xfer(
            bytes([0x02, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF])
            + bytes(data),
            cs=CS_RAM,
        )

    def psram_read(self, addr, length):
        return self.xfer(
            bytes([CMD_READ, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF]),
            length,
            cs=CS_RAM,
        )


# ------------------------------------------------------------------- receiver


def _busy_until(t0_us, offset_us):
    """Spin until `offset_us` after t0_us (ticks_us domain, wrap-safe)."""
    target = time.ticks_add(t0_us, int(offset_us))
    while time.ticks_diff(target, time.ticks_us()) > 0:
        pass


def bitbang_rx(pin, baud, timeout_ms, want=None, max_bytes=512):
    """Software 8N1 receiver on a raw machine.Pin.

    Returns (bytes, framing_errors). Stops early once `want` (a list of
    substrings) has all been seen, so a healthy die does not burn the whole
    timeout, while a sick one still gets the full window.
    """
    bit_us = 1e6 / baud
    out = bytearray()
    framing = 0
    deadline = time.ticks_add(time.ticks_ms(), int(timeout_ms))
    while time.ticks_diff(deadline, time.ticks_ms()) > 0 and len(out) < max_bytes:
        if pin.value():
            continue  # line idle high; wait for a start bit
        t0 = time.ticks_us()
        v = 0
        for i in range(8):
            _busy_until(t0, bit_us * (1.5 + i))
            v |= pin.value() << i
        _busy_until(t0, bit_us * 9.5)
        if not pin.value():
            framing += 1  # stop bit was not high: baud mismatch or noise
        out.append(v)
        if want:
            try:
                text = bytes(out).decode()
            except (UnicodeError, ValueError):
                continue
            if all(w in text for w in want):
                break
    return bytes(out), framing


def hw_uart(pin, baud):
    """A hardware UART on `pin`, or None if this pin/board cannot do it."""
    if pin is None:
        return None
    try:
        from machine import UART

        return UART(1, baudrate=int(baud), rx=pin, tx=None, timeout=200)
    except Exception:  # noqa: BLE001 - not UART-capable on this board rev
        return None


def hw_uart_rx(uart, timeout_ms, want=None, max_bytes=512):
    out = bytearray()
    deadline = time.ticks_add(time.ticks_ms(), int(timeout_ms))
    while time.ticks_diff(deadline, time.ticks_ms()) > 0 and len(out) < max_bytes:
        if uart.any():
            chunk = uart.read()
            if chunk:
                out += chunk
        if want:
            try:
                text = bytes(out).decode()
            except (UnicodeError, ValueError):
                continue
            if all(w in text for w in want):
                break
    return bytes(out), 0


# ------------------------------------------------------------------- checks

_results = []
_skipped = []


def log(msg):
    print(msg)


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    log("  [%s] %-22s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def skip(name, why):
    """Record a check we cannot honestly make. Never counts as a pass."""
    _skipped.append((name, why))
    log("  [SKIP] %-22s %s" % (name, why))


def check_bus_drive(board, spi):
    """Does the RP2040 actually own the eight uio pins?

    Walks the chip selects and reads them back. A dead result here means the
    Pmod is unseated or the OE never took — and every check below would then
    fail for a reason that has nothing to do with the die.
    """
    board.bus_take()
    ok = True
    detail = "all driven pins follow"
    for bit in (CS_FLASH, CS_RAM, CS2, SCK):
        for level in (0, 1):
            v = (spi.shadow | (1 << bit)) if level else (spi.shadow & ~(1 << bit))
            spi._w(v)
            got = (board.read_uio() >> bit) & 1
            if got != level:
                ok = False
                detail = "uio[%d] stuck at %d" % (bit, got)
    spi._w(CS_IDLE)
    return check("bus drive", ok, detail)


def check_flash_id(spi):
    jid = spi.jedec()
    return check(
        "flash id",
        jid == FLASH_JEDEC,
        "%s (expect %s = W25Q128JV)" % (jid.hex(), FLASH_JEDEC.hex()),
    )


def check_flash_qe(spi, set_if_clear=True):
    """Status-2 bit 1. See the module docstring: hello.c hangs without it.

    docs/info.md calls the QE bit "factory-set on the QSPI Pmod"; that has
    never been verified on hardware, which is exactly why it is checked here
    rather than assumed.
    """
    sr2 = spi.read_sr2()
    if sr2 & QE_BIT:
        return check("flash QE bit", True, "already set (SR2=0x%02X)" % sr2)
    if not set_if_clear:
        return check("flash QE bit", False, "CLEAR (SR2=0x%02X) — hello.c will hang" % sr2)
    log("      QE clear (SR2=0x%02X) — setting it (non-volatile write)" % sr2)
    spi.write_sr2(sr2 | QE_BIT)
    sr2 = spi.read_sr2()
    return check(
        "flash QE bit",
        bool(sr2 & QE_BIT),
        "set, now SR2=0x%02X" % sr2
        if sr2 & QE_BIT
        else "REFUSED to set (SR2=0x%02X) — build hello.c with QSPI_CFG=2" % sr2,
    )


def check_psram(spi, addr=0x001000):
    """ID, then a real write/read. The stack lives here."""
    ident = spi.psram_id()
    check(
        "psram id",
        len(ident) >= 1 and ident[0] == PSRAM_MFR,
        "mfr 0x%02X kgd 0x%02X (expect mfr 0x%02X)"
        % (ident[0] if ident else 0, ident[1] if len(ident) > 1 else 0, PSRAM_MFR),
    )
    pattern = bytes([(i * 7 + 0x5A) & 0xFF for i in range(16)])
    spi.psram_write(addr, pattern)
    back = spi.psram_read(addr, len(pattern))
    return check(
        "psram read/write",
        back == pattern,
        "16 bytes at 0x%06X round-trip" % addr
        if back == pattern
        else "wrote %s, read %s" % (pattern.hex(), back.hex()),
    )


def flash_program(spi, data, base=0x000000, verify=True):
    """Erase + page-program + read-back verify. Returns True on match."""
    nsectors = (len(data) + 0xFFF) // 0x1000
    log("      erasing %d sector(s), programming %d bytes" % (nsectors, len(data)))
    for s in range(nsectors):
        a = base + s * 0x1000
        spi.xfer(bytes([CMD_WREN]))
        spi.xfer(bytes([CMD_SECTOR_ERASE, (a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF]))
        if not spi.wait_ready():
            return False
    for off in range(0, len(data), 256):
        a = base + off
        spi.xfer(bytes([CMD_WREN]))
        spi.xfer(
            bytes([CMD_PAGE_PROGRAM, (a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF])
            + data[off : off + 256]
        )
        if not spi.wait_ready():
            return False
    if not verify:
        return True
    return spi.flash_read(base, len(data)) == data


def check_program(spi, data, base=0x000000):
    return check(
        "flash program+verify",
        flash_program(spi, data, base),
        "%d bytes at 0x%06X" % (len(data), base),
    )


def check_bus_released(board):
    """The RP2040 must be off the bus before the ASIC drives it."""
    oe = board.get_oe()
    return check(
        "bus released",
        oe == 0,
        "uio_oe_pico=0x%02X" % oe if oe else "RP2040 off the uio bank",
    )


def check_uart(text, framing):
    ok_banner = check("uart banner", BANNER in text, repr(text[:48]))
    ok_fib = check("uart fib(10)", FIB_LINE in text, repr(text[-24:]) if text else "no data")
    check("uart framing", framing == 0, "%d bad stop bits" % framing)
    return ok_banner and ok_fib


def check_halted(board, timeout_ms=500):
    """Wait for uo[1], do not sample it once.

    The receiver stops as soon as it has seen the strings it wanted, which is
    at the last character of "fib(10)=55" — but the program still has a
    newline to push out and an ECALL to execute after that. Sampling HALTED
    right there races the CPU and reports a working chip as broken.
    """
    deadline = time.ticks_add(time.ticks_ms(), int(timeout_ms))
    v = 0
    while True:
        v = (board.get_uo() >> BIT_HALTED) & 1
        if v or time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            break
    return check("halted", bool(v), "uo[1]=%d" % v)


def check_leds(board):
    v = (board.get_uo() >> LED_LSB) & 0x3F
    return check(
        "leds",
        v == LED_EXPECT,
        "0b%06d = %d (expect %d)" % (int(bin(v)[2:]), v, LED_EXPECT),
    )


# ---------------------------------------------------------------------- main


def _read_image(path):
    with open(path, "rb") as f:
        return f.read()


def main(path="hello.bin", clk_hz=None, set_qe=True, listen_ms=None):
    """Full bring-up: program the flash, boot the CPU, check what it says.

    Returns True if every check passed.
    """
    del _results[:]
    del _skipped[:]
    log("TinyRV32 bring-up — %s" % DESIGN)
    board = Board.open()
    spi = Spi(board)

    log("\n1. memory Pmod (RP2040 owns the bus, no design selected)")
    board.deselect()  # the die drives CS/SCK whenever it is connected
    if not check_bus_drive(board, spi):
        log("\nbus is dead — is the QSPI Pmod seated on the bidir header?")
        return _summary()
    if not check_flash_id(spi):
        log("\nno flash — nothing below can work; stopping.")
        return _summary()
    check_flash_qe(spi, set_if_clear=set_qe)
    check_psram(spi)

    log("\n2. program %s" % path)
    image = _read_image(path)
    if not check_program(spi, image):
        log("\nflash did not verify — stopping before we boot garbage.")
        return _summary()

    log("\n3. hand the bus over and boot")
    board.bus_release()  # RP2040 off the pins BEFORE the die is connected
    check_bus_released(board)
    board.select()

    pin = board.uo_pin(BIT_UART)
    uart = hw_uart(pin, CLK_HZ / UART_DIV)
    if clk_hz is None:
        clk_hz = CLK_HZ if uart is not None else clock_for_baud(BITBANG_BAUD)
    actual = board.clock(clk_hz)
    baud = actual / UART_DIV
    log("      clock requested %.3f MHz, achieved %.3f MHz -> %.0f baud"
        % (clk_hz / 1e6, actual / 1e6, baud))
    if abs(actual - clk_hz) / clk_hz > 0.02:
        log("      (PWM could not hit the request — baud derived from the "
            "achieved clock, as it must be)")

    if uart is not None:
        uart = hw_uart(pin, baud)  # rebuild at the baud the die will really use
    if uart is None and pin is None:
        skip("uart banner", "no raw Pin for uo[0] and no hardware UART")
        skip("uart fib(10)", "no raw Pin for uo[0] and no hardware UART")
        text, framing = "", 0
    else:
        if listen_ms is None:
            # one character is 10 bits; allow the whole banner plus boot time
            listen_ms = max(3000, int(10_000 * 10 / baud) + 2000)
        board.reset_pulse()
        want = [BANNER, FIB_LINE]
        if uart is not None:
            log("      listening on the hardware UART for up to %d ms" % listen_ms)
            raw, framing = hw_uart_rx(uart, listen_ms, want)
        else:
            log("      bit-banging the receiver at %.0f baud for up to %d ms"
                % (baud, listen_ms))
            raw, framing = bitbang_rx(pin, baud, listen_ms, want)
        try:
            text = raw.decode()
        except (UnicodeError, ValueError):
            text = "".join(chr(c) if 32 <= c < 127 or c == 10 else "?" for c in raw)

    log("\n4. what the CPU said")
    for line in text.splitlines():
        log("      | %s" % line)
    if text or pin is not None or uart is not None:
        check_uart(text, framing)
    check_halted(board)
    check_leds(board)
    return _summary()


def _summary():
    failed = [n for n, ok, _ in _results if not ok]
    log("\n%d/%d checks passed" % (len(_results) - len(failed), len(_results)))
    if _skipped:
        log("NOT VERIFIED (%d skipped): %s"
            % (len(_skipped), ", ".join(n.strip() for n, _ in _skipped)))
    if failed:
        log("FAILED: %s" % ", ".join(failed))
    elif _skipped:
        log("no failures, but the skipped checks above were never made.")
    else:
        log("ALL PASS — the CPU boots from flash and runs C. That is the chip.")
    return not failed


def run(clk_hz=None, listen_ms=None):
    """Boot whatever is already in the flash and print the UART (no programming)."""
    del _results[:]
    del _skipped[:]
    board = Board.open()
    board.bus_release()
    board.select()
    pin = board.uo_pin(BIT_UART)
    uart = hw_uart(pin, CLK_HZ / UART_DIV)
    if clk_hz is None:
        clk_hz = CLK_HZ if uart is not None else clock_for_baud(BITBANG_BAUD)
    actual = board.clock(clk_hz)
    baud = actual / UART_DIV
    if uart is not None:
        uart = hw_uart(pin, baud)
    if listen_ms is None:
        listen_ms = max(3000, int(10_000 * 10 / baud) + 2000)
    board.reset_pulse()
    want = [BANNER, FIB_LINE]
    if uart is not None:
        raw, _ = hw_uart_rx(uart, listen_ms, want)
    elif pin is not None:
        raw, _ = bitbang_rx(pin, baud, listen_ms, want)
    else:
        raise RuntimeError("no way to receive uo[0] on this firmware")
    text = raw.decode("utf-8") if raw else ""
    log(text)
    return text


def sweep_fmax(path="hello.bin", start_hz=None, stop_hz=50_000_000,
               step_hz=5_000_000):
    """Raise the clock until the CPU stops printing the right thing.

    Signoff says timing-clean at 25 MHz; this measures what the real die does.
    Needs a hardware UART — bit-banging cannot follow the baud rate up.

    NOTE the flash must already hold hello.bin (run main() first).
    """
    board = Board.open()
    board.bus_release()
    board.select()
    pin = board.uo_pin(BIT_UART)
    if pin is None:
        raise RuntimeError("no raw Pin for uo[0]")
    if start_hz is None:
        start_hz = CLK_HZ
    last_good = None
    hz = start_hz
    while hz <= stop_hz:
        actual = board.clock(hz)
        baud = actual / UART_DIV
        uart = hw_uart(pin, baud)
        if uart is None:
            raise RuntimeError("sweep_fmax needs a hardware UART on uo[0]")
        board.reset_pulse()
        raw, _ = hw_uart_rx(uart, 3000, [BANNER, FIB_LINE])
        try:
            text = raw.decode()
        except (UnicodeError, ValueError):
            text = ""
        ok = BANNER in text and FIB_LINE in text
        log("%6.2f MHz requested / %6.2f MHz achieved: %s"
            % (hz / 1e6, actual / 1e6, "OK" if ok else "BROKEN"))
        if not ok:
            break
        last_good = actual
        hz += step_hz
    board.clock(CLK_HZ)
    board.reset_pulse()
    log("highest working clock: %s"
        % ("%.2f MHz" % (last_good / 1e6) if last_good else "none"))
    return last_good


if __name__ == "__main__":
    main()

# SPDX-FileCopyrightText: © 2026 Joonatan Alanampa
# SPDX-License-Identifier: Apache-2.0
"""
Host-side test for bringup.py — run on CPython, no hardware.

    python bringup/test_bringup_host.py

The chips land ~mid-2027, so the bring-up script would otherwise sit untested
for a year. This stubs `machine`, `time` and `ttboard` with a virtual demo
board and runs the REAL script against it — unmodified, imported as-is.

What the virtual board actually models (not mocks):

  * the uio bank at BIT level. The script bit-bangs SPI by writing whole uio
    bytes; this model watches CS/SCK edges in those writes and runs a W25Q128
    and an APS6404 state machine off them. So the script's clock phasing,
    MSB-first ordering and read alignment are genuinely exercised — the
    missing-eighth-edge bug in `Spi.xfer` was caught here, not by reading it.
  * the UART as a WAVEFORM on uo[0]: the level is computed from virtual time,
    baud and the message, so the script's software receiver has to find start
    bits, sample mid-bit and check stop bits for real.
  * time costs. A port access costs 40 us, a raw pin read 6 us, a ticks_us()
    call 2 us — which is why bit-banging 115200 baud is impossible and the
    script has to slow the project clock down instead. Setting these to zero
    would make the receiver look like it works at any baud rate.

And the faults, because a bring-up script that cannot fail is worthless:
every one of them must be caught by the RIGHT check.

If TT_FIRMWARE_SRC points at a checkout of tt-micropython-firmware's microcotb
submodule (`.../microcotb/src`), the virtual board wires up the REAL microcotb
IO port class instead of a stand-in, so the script's read/write idioms are
exercised against shipping firmware code. CI does this at a pinned commit; a
set-but-unusable value is a hard error, never a silent fallback.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# Import the real firmware port class FIRST, while `time` is still CPython's:
# microcotb does `from time import monotonic`, which the stubbed time module
# installed below cannot provide.
FW_SRC = os.environ.get("TT_FIRMWARE_SRC")
REAL_IO = None
if FW_SRC:
    sys.path.insert(0, FW_SRC)
    from microcotb.ports.io import IO as REAL_IO  # noqa: N811

IMAGE_PATH = os.path.join(REPO, "sw", "hello.bin")

# uio bit positions (must match src/project.sv)
CS_FLASH, SD0, SD1, SCK, SD2, SD3, CS_RAM, CS2 = 0, 1, 2, 3, 4, 5, 6, 7

UART_DIV = 217
BOOT_CYCLES = 200_000  # crt0: .data copy + .bss zero, before the first char
MESSAGE = b"Hello from my own CPU!\nfib(10)=55\n"
LED_VALUE = 55

# --- cost model: what each operation costs in wall-clock microseconds -------
PORT_US = 40.0  # one microcotb port read or write
PIN_US = 6.0  # one raw machine.Pin.value()
TICKS_US = 2.0  # one time.ticks_us() call (the busy-wait's inner loop)


class VClock:
    def __init__(self):
        self.us = 0.0

    def advance(self, us):
        self.us += us


CLOCK = VClock()


# --------------------------------------------------------------- SPI devices
class SpiDevice:
    """Common shell: CS framing, MSB-first shifting, response bit timing.

    Host phasing (see Spi.xfer): data is set with SCK low, the device samples
    on the RISING edge, and presents its next output bit on the FALLING edge
    so the host can sample it after the next rise.
    """

    def __init__(self):
        self.selected = False
        self.reset_txn()

    def reset_txn(self):
        self.rx = bytearray()
        self.shift = 0
        self.nbits = 0
        self.resp = b""
        self.resp_pos = 0
        self.out_bit = 1

    def cs(self, asserted):
        if asserted and not self.selected:
            self.reset_txn()
            self.selected = True
        elif not asserted and self.selected:
            self.selected = False
            self.finish()

    def rising(self, mosi):
        if not self.selected:
            return
        self.shift = ((self.shift << 1) | (mosi & 1)) & 0xFF
        self.nbits += 1
        if self.nbits == 8:
            self.nbits = 0
            self.rx.append(self.shift)
            self.shift = 0
            self.byte_in(self.rx[0], len(self.rx))

    def falling(self):
        if not self.selected:
            return
        if self.resp_pos < len(self.resp) * 8:
            byte = self.resp[self.resp_pos // 8]
            self.out_bit = (byte >> (7 - self.resp_pos % 8)) & 1
            self.resp_pos += 1

    def arm(self, data):
        self.resp = bytes(data)
        self.resp_pos = 0

    def addr_of(self, lo=1):
        return (self.rx[lo] << 16) | (self.rx[lo + 1] << 8) | self.rx[lo + 2]

    def byte_in(self, cmd, count):
        pass

    def finish(self):
        pass


class Flash(SpiDevice):
    """W25Q128JV, enough of it: 9F/05/35/31/06/20/02/03."""

    SIZE = 0x10000

    def __init__(self, present=True, qe=True, qe_settable=True, program_sticks=True):
        self.mem = bytearray(b"\xff" * self.SIZE)
        self.present = present
        self.sr2 = 0x02 if qe else 0x00
        self.qe_settable = qe_settable
        self.program_sticks = program_sticks
        self.wel = False
        super().__init__()

    def byte_in(self, cmd, count):
        if not self.present:
            self.arm(b"\x00" * 8)
            return
        if cmd == 0x9F and count == 1:
            self.arm(b"\xef\x40\x18")
        elif cmd == 0x05 and count == 1:
            self.arm(b"\x00" * 8)  # never busy: the model programs instantly
        elif cmd == 0x35 and count == 1:
            self.arm(bytes([self.sr2]) * 8)
        elif cmd == 0x03 and count == 4:
            a = self.addr_of()
            self.arm(self.mem[a:] + self.mem[:a])

    def finish(self):
        if not self.present or not self.rx:
            return
        cmd = self.rx[0]
        if cmd == 0x06:
            self.wel = True
        elif cmd == 0x31 and len(self.rx) >= 2:
            if self.wel and self.qe_settable:
                self.sr2 = self.rx[1]
            self.wel = False
        elif cmd == 0x20 and len(self.rx) >= 4:
            if self.wel:
                a = self.addr_of() & ~0xFFF
                self.mem[a : a + 0x1000] = b"\xff" * 0x1000
            self.wel = False
        elif cmd == 0x02 and len(self.rx) >= 4:
            if self.wel and self.program_sticks:
                a = self.addr_of()
                data = self.rx[4:]
                for i, b in enumerate(data):
                    # real NOR flash can only clear bits
                    self.mem[(a + i) % self.SIZE] &= b
            self.wel = False


class Psram(SpiDevice):
    """APS6404L: 9F (ID), 02 (write), 03 (read)."""

    SIZE = 0x10000

    def __init__(self, present=True):
        self.mem = bytearray(self.SIZE)
        self.present = present
        super().__init__()

    def byte_in(self, cmd, count):
        if not self.present:
            self.arm(b"\xff" * 8)
            return
        if cmd == 0x9F and count == 4:
            self.arm(b"\x0d\x5d\x00\x00\x00\x00\x00\x01")
        elif cmd == 0x03 and count == 4:
            a = self.addr_of()
            self.arm(self.mem[a:] + self.mem[:a])

    def finish(self):
        if not self.present or len(self.rx) < 4:
            return
        if self.rx[0] == 0x02:
            a = self.addr_of()
            for i, b in enumerate(self.rx[4:]):
                self.mem[(a + i) % self.SIZE] = b


# --------------------------------------------------------------------- board
class Die:
    """Pin-level model of tt_um_joonatanalanampa_rv32 plus its Pmod."""

    def __init__(self, fault=None, image=b""):
        self.fault = fault
        self.image = image
        self.flash = Flash(
            present=(fault != "no_flash"),
            qe=(fault not in ("qe_clear", "qe_stubborn")),
            qe_settable=(fault != "qe_stubborn"),
            program_sticks=(fault != "bad_program"),
        )
        self.psram = Psram(present=(fault != "dead_psram"))
        self.bus_alive = fault != "no_pmod"

        self.oe = 0
        self.rp_val = 0xFF
        self.prev = 0xFF

        self.enabled = False
        self.in_reset = True
        self.clk = 25_000_000
        self.t_reset = 0.0
        self.fmax = None  # set by the sweep test: clock this die stops at
        self.contention = False  # RP2040 and die drove the uio bank together

    # -- uio bus ---------------------------------------------------------
    def _check_contention(self):
        """Both ends driving uio at once.

        src/project.sv ties uio_oe[0,3,6,7] to constant 1, so a CONNECTED die
        drives CS0/SCK/CS1/CS2 whether or not it is in reset. If the RP2040
        also has output enables on while the design is selected, the two are
        shorted together on four pins — which is why the script must
        shuttle.disable() before programming and bus_release() before select().
        """
        if self.oe and self.enabled:
            self.contention = True

    def set_oe(self, mask):
        self.oe = mask if self.bus_alive else 0
        self._check_contention()

    def write_uio(self, value):
        self._check_contention()
        return self._write_uio(value)

    def _write_uio(self, value):
        value = int(value) & 0xFF
        self.rp_val = value
        driven = value if self.bus_alive else 0xFF
        prev, self.prev = self.prev, driven
        if not self.bus_alive:
            return
        # chip selects first, so a CS edge in the same write is seen before
        # any clock edge that accompanies it
        for bit, dev in ((CS_FLASH, self.flash), (CS_RAM, self.psram)):
            was, now = (prev >> bit) & 1, (driven >> bit) & 1
            if was != now:
                dev.cs(now == 0)
        was, now = (prev >> SCK) & 1, (driven >> SCK) & 1
        if was != now:
            mosi = (driven >> SD0) & 1
            for dev in (self.flash, self.psram):
                if now:
                    dev.rising(mosi)
                else:
                    dev.falling()

    def read_uio(self):
        if not self.bus_alive:
            return 0xFF
        v = self.rp_val & self.oe
        miso = 1
        for dev in (self.flash, self.psram):
            if dev.selected:
                miso = dev.out_bit
        v = (v & ~(1 << SD1)) | (miso << SD1)
        return v & 0xFF

    # -- the CPU ----------------------------------------------------------
    def boots(self):
        """Does the program actually get to run?"""
        if not self.enabled or self.in_reset or not self.image:
            return False
        if self.fault == "cpu_dead":
            return False
        if self.fmax is not None and self.clk > self.fmax:
            return False  # past its Fmax the pipeline stops working
        if self.flash.mem[: len(self.image)] != self.image:
            return False  # garbage in flash: no banner
        if not (self.flash.sr2 & 0x02):
            return False  # hello.c sets QSPI_CFG=3 -> quad read into a
            # device with QE clear -> hangs on the next fetch
        if not self.psram.present:
            return False  # crt0 puts the stack here
        return True

    def message(self):
        return MESSAGE if self.boots() else b""

    def baud(self):
        return self.clk / UART_DIV

    def boot_s(self):
        return BOOT_CYCLES / self.clk

    def _t(self):
        return (CLOCK.us - self.t_reset) / 1e6

    def bytes_done(self):
        if not self.boots():
            return 0
        t = self._t() - self.boot_s()
        if t <= 0:
            return 0
        return min(int(t * self.baud() / 10), len(self.message()))

    def uart_level(self):
        """The 8N1 waveform on uo[0], as a function of virtual time."""
        msg = self.message()
        if not msg:
            return 1  # idle high (or dead)
        t = self._t() - self.boot_s()
        if t <= 0:
            return 1
        bitpos = t * self.baud()
        idx = int(bitpos // 10)
        if idx >= len(msg):
            return 1
        phase = bitpos - idx * 10
        if phase < 1:
            return 0  # start bit
        if phase < 9:
            return (msg[idx] >> int(phase - 1)) & 1  # LSB first
        return 1  # stop bit

    def done(self):
        return bool(self.message()) and self.bytes_done() >= len(self.message())

    def uo(self):
        finished = self.done()
        halted = 1 if (finished and self.fault != "no_halt") else 0
        leds = LED_VALUE if (finished and self.fault != "bad_leds") else 0
        return (leds << 2) | (halted << 1) | self.uart_level()


DIE = Die()


# ---------------------------------------------------------- fake `machine`
class FakePin:
    IN = 0  # machine.Pin.IN / .OUT as MicroPython defines them on rp2
    OUT = 1

    def __init__(self, bit):
        self.bit = bit

    def value(self, v=None):
        CLOCK.advance(PIN_US)
        return (DIE.uo() >> self.bit) & 1


class FakeUART:
    """Hardware UART on uo[0] — only exists when the test enables it."""

    available = False

    def __init__(self, _id, baudrate=115200, rx=None, tx=None, timeout=0):
        if not FakeUART.available:
            raise ValueError("uo[0] is not on a UART-capable pin on this board")
        self.baud = baudrate
        self.pos = 0

    def _ready(self):
        return DIE.bytes_done()

    def any(self):
        CLOCK.advance(PORT_US)
        return max(0, self._ready() - self.pos)

    def read(self):
        n = self._ready()
        chunk = DIE.message()[self.pos : n]
        self.pos = n
        return bytes(chunk) if chunk else None


machine = types.ModuleType("machine")
machine.Pin = FakePin
machine.UART = FakeUART
sys.modules["machine"] = machine


# ------------------------------------------------------------- fake `time`
faketime = types.ModuleType("time")
faketime.sleep_ms = lambda ms: CLOCK.advance(ms * 1000.0)


def _ticks_us():
    CLOCK.advance(TICKS_US)
    return int(CLOCK.us)


faketime.ticks_us = _ticks_us
faketime.ticks_ms = lambda: int(CLOCK.us / 1000.0)
faketime.ticks_add = lambda t, d: t + d
faketime.ticks_diff = lambda a, b: a - b
sys.modules["time"] = faketime


# ------------------------------------------------------- fake ttboard package
class FakeByteReg:
    """Stand-in for a microcotb IO port (used when the real one is absent)."""

    def __init__(self, setter=None, getter=None):
        self._set, self._get = setter, getter

    def __int__(self):
        return self._get()

    @property
    def value(self):
        return self._get()

    @value.setter
    def value(self, v):
        self._set(v)


def make_port(name, getter, setter=None):
    if REAL_IO is not None:
        return REAL_IO(name, 8, getter, setter)
    return FakeByteReg(setter=setter, getter=getter)


class FakeStandardPin:
    """tt.pins.uo_outN — the firmware's StandardPin wraps a machine.Pin."""

    def __init__(self, bit):
        self.raw_pin = FakePin(bit)


class FakeUioPin:
    """tt.pins.uioN — mode is what sets direction when uio_oe_pico is absent."""

    def __init__(self, index):
        self.index = index
        self._mode = 0

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, m):
        self._mode = m
        bit = 1 << self.index
        DIE.set_oe((DIE.oe | bit) if m == FakePin.OUT else (DIE.oe & ~bit))


class FakePins:
    def __init__(self):
        for i in range(8):
            setattr(self, "uo_out%d" % i, FakeStandardPin(i))
            setattr(self, "uio%d" % i, FakeUioPin(i))


class FakePWM:
    def __init__(self, hz):
        self._hz = hz

    def freq(self):
        return self._hz


class FakeProject:
    def __init__(self, name):
        self.name = name

    def enable(self):
        DIE.enabled = True


class FakeShuttle:
    def __init__(self):
        self.proj = FakeProject("tt_um_joonatanalanampa_rv32")

    def has(self, name):
        return name == "tt_um_joonatanalanampa_rv32"

    def get(self, name):
        return self.proj

    def disable(self):
        DIE.enabled = False


class FakeDemoBoard:
    """The demo board as the pinned firmware actually presents it."""

    PWM_EXACT = True  # set False to model a PWM that misses the request

    def __init__(self):
        self.shuttle = FakeShuttle()
        self.mode = None
        self.pins = FakePins()
        self.ui_in = make_port("ui_in", lambda: 0, lambda v: None)
        self.uo_out = make_port("uo_out", self._get_uo)
        self.uio_in = make_port("uio_in", self._get_uio, self._set_uio)
        self.uio_out = make_port("uio_out", self._get_uio)
        self.uio_oe_pico = make_port("uio_oe_pico", lambda: DIE.oe, self._set_oe)

    def _set_oe(self, v):
        DIE.set_oe(int(v))

    def _set_uio(self, v):
        CLOCK.advance(PORT_US)
        DIE.write_uio(int(v))

    def _get_uio(self):
        CLOCK.advance(PORT_US)
        return DIE.read_uio()

    def _get_uo(self):
        CLOCK.advance(PORT_US)
        return DIE.uo()

    def clock_project_PWM(self, hz, **kw):
        actual = hz if self.PWM_EXACT else int(hz * 0.93)
        DIE.clk = actual
        return FakePWM(actual)

    def clock_project_stop(self):
        pass

    def reset_project(self, asserted):
        DIE.in_reset = bool(asserted)
        if not asserted:
            DIE.t_reset = CLOCK.us

    @classmethod
    def get(cls):
        return cls()


class NoOePicoDemoBoard(FakeDemoBoard):
    """Firmware without uio_oe_pico — the per-pin `.mode` fallback path."""

    def __init__(self):
        super().__init__()
        del self.uio_oe_pico


ttboard = types.ModuleType("ttboard")
demoboard = types.ModuleType("ttboard.demoboard")
demoboard.DemoBoard = FakeDemoBoard
mode_mod = types.ModuleType("ttboard.mode")


class RPMode:
    SAFE = 0
    ASIC_RP_CONTROL = 1
    ASIC_MANUAL_INPUTS = 2


mode_mod.RPMode = RPMode
ttboard.demoboard = demoboard
ttboard.mode = mode_mod
sys.modules["ttboard"] = ttboard
sys.modules["ttboard.demoboard"] = demoboard
sys.modules["ttboard.mode"] = mode_mod


# --------------------------------------------------------------------- tests
import bringup as bu  # noqa: E402  (must follow the stubs)

with open(IMAGE_PATH, "rb") as _f:
    IMAGE = _f.read()


def setup(fault=None, board_cls=FakeDemoBoard, uart=False, pwm_exact=True):
    global DIE
    DIE = Die(fault, IMAGE)
    CLOCK.us = 0.0
    FakeUART.available = uart
    demoboard.DemoBoard = board_cls
    board_cls.PWM_EXACT = pwm_exact


def run(fault=None, **kw):
    setup(fault, **kw)
    ok = bu.main(path=IMAGE_PATH)
    # Never acceptable, in any scenario, healthy or faulted: the RP2040 and a
    # connected die driving the same four uio pins against each other.
    assert not DIE.contention, (
        "bus contention — the script drove uio while the design was selected "
        "(fault=%s). uio_oe[0,3,6,7] are hard 1 in project.sv, so reset does "
        "not save you; only shuttle.disable() does." % fault)
    failed = [n for n, passed, _ in bu._results if not passed]
    skipped = [n.strip() for n, _ in bu._skipped]
    return ok, failed, skipped


def banner(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    print("ports: %s" % ("REAL microcotb IO from %s" % FW_SRC if REAL_IO
                         else "stand-in (set TT_FIRMWARE_SRC for the real one)"))

    banner("GOOD DIE (bit-banged receiver, no hardware UART)")
    ok, failed, skipped = run()
    assert ok, "good die must pass every check, but these failed: %s" % failed
    assert not skipped, "nothing should have been skipped: %s" % skipped
    assert DIE.flash.mem[: len(IMAGE)] == IMAGE, "hello.bin never reached the flash"
    assert DIE.oe == 0, "the RP2040 was left driving the uio bank"
    # the whole point of the slow-clock trick: bit-banging only works down here
    assert DIE.clk < 2_000_000, (
        "expected the script to slow the project clock for the software "
        "receiver, but it ran at %.1f MHz" % (DIE.clk / 1e6))
    print("-> %d checks, bus released, flash holds the image, clock %.3f MHz"
          % (len(bu._results), DIE.clk / 1e6))

    banner("GOOD DIE (hardware UART available -> full-speed 25 MHz)")
    ok, failed, _ = run(uart=True)
    assert ok, "good die with a hardware UART failed: %s" % failed
    assert DIE.clk == 25_000_000, (
        "with a hardware UART the script should run the chip at 25 MHz, got %s"
        % DIE.clk)
    print("-> ran at %.1f MHz over the hardware UART" % (DIE.clk / 1e6))

    banner("PWM MISSES THE REQUEST BY 7% (baud must follow the real clock)")
    ok, failed, _ = run(uart=True, pwm_exact=False)
    assert ok, (
        "the script derived baud from the requested clock, not the achieved "
        "one — these failed: %s" % failed)
    print("-> still decoded: baud came from pwm.freq(), not the request")

    banner("FIRMWARE WITHOUT uio_oe_pico (per-pin .mode fallback)")
    ok, failed, _ = run(board_cls=NoOePicoDemoBoard)
    assert ok, "the .mode fallback path failed: %s" % failed
    assert DIE.oe == 0, "fallback path left the bus driven"
    print("-> drove and released the bus through tt.pins.uioN.mode")

    banner("QE BIT CLEAR ON ARRIVAL (the script must FIX it, not fail)")
    setup("qe_clear")
    ok = bu.main(path=IMAGE_PATH)
    assert ok, "a clear-but-settable QE bit should be repaired, not fatal"
    assert DIE.flash.sr2 & 0x02, "QE bit was never actually set"
    print("-> QE set, chip then booted in quad mode")

    banner("QE BIT CLEAR AND set_qe=False (must refuse to boot blind)")
    setup("qe_clear")
    ok = bu.main(path=IMAGE_PATH, set_qe=False)
    assert not ok, "QE left clear was reported healthy"
    failed = [n for n, p, _ in bu._results if not p]
    assert "flash QE bit" in failed, "QE not flagged; failures were %s" % failed
    assert any("uart" in f for f in failed), (
        "hello.c writes QSPI_CFG=3, so a clear QE must also kill the UART "
        "output — the model or the expectation is wrong; failures: %s" % failed)
    print("-> caught at the QE check AND at the silent UART, as on a bench")

    for fault, must_catch in (
        ("no_pmod", "bus drive"),
        ("no_flash", "flash id"),
        ("qe_stubborn", "flash QE bit"),
        ("dead_psram", "psram"),
        ("bad_program", "flash program+verify"),
        ("cpu_dead", "uart banner"),
        ("no_halt", "halted"),
        ("bad_leds", "leds"),
    ):
        banner("FAULT INJECTED: %s" % fault)
        ok, failed, _ = run(fault)
        assert not ok, "%s die was reported healthy" % fault
        assert any(must_catch in f for f in failed), (
            "%s die failed the wrong checks: %s" % (fault, failed))
        print("-> correctly caught by: %s" % ", ".join(failed))

    banner("BIT-BANGING 115200 BAUD MUST NOT SILENTLY 'WORK'")
    # If this ever passes, the cost model has been zeroed out and every
    # timing conclusion in this file is worthless.
    setup()
    DIE.enabled = True
    DIE.in_reset = False
    DIE.clk = 25_000_000
    DIE.t_reset = CLOCK.us
    raw, framing = bu.bitbang_rx(FakePin(0), 25_000_000 / UART_DIV, 500,
                                 [bu.BANNER])
    assert bu.BANNER.encode() not in raw, (
        "the software receiver decoded 115200 baud, which MicroPython cannot "
        "do — the virtual time costs must have been lost")
    print("-> failed to decode, as it must (%d bytes, %d framing errors)"
          % (len(raw), framing))

    banner("sweep_fmax() — never exercised by main()")
    setup(uart=True)
    bu.main(path=IMAGE_PATH)  # get the image into the flash first
    DIE.fmax = 37_000_000  # this virtual die stops working above 37 MHz
    last = bu.sweep_fmax(start_hz=25_000_000, stop_hz=60_000_000,
                         step_hz=5_000_000)
    assert last is not None, "sweep found no working clock at all"
    assert 34e6 <= last <= 38e6, (
        "sweep_fmax reported %.1f MHz for a die that breaks at 37 MHz"
        % (last / 1e6))
    print("-> found %.1f MHz for a die that breaks at 37.0 MHz" % (last / 1e6))

    print("\nALL HOST TESTS PASSED")


if __name__ == "__main__":
    main()

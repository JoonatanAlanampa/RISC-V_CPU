# bringup.py — TinyRV32 bring-up on the TinyTapeout demo board (RP2040,
# MicroPython with the ttboard SDK). Written 2027-chip-day-ready in 2026:
# every step mirrors a cocotb test that already passes in simulation.
#
# Usage (mpremote / Thonny REPL on the demo board, hello.bin copied over):
#   import bringup
#   bringup.flash_program("hello.bin")   # program the QSPI Pmod flash
#   bringup.run()                        # select TinyRV32, clock, reset
#                                        # -> prints the UART banner
#
# !! VERIFY-ON-HARDWARE: written against the ttboard SDK as documented at
# https://github.com/TinyTapeout/tt-micropython-firmware in 2026. Pin
# attribute names and the shuttle handle may need touching up once the
# real TTSKY26c firmware image is in hand. The logic won't change.

import time

from ttboard.demoboard import DemoBoard
from ttboard.mode import RPMode

PROJECT = "tt_um_joonatanalanampa_rv32"
CLOCK_HZ = 25_000_000        # matches sim; PSRAM tCEM needs >= 25 MHz for
                             # burst reads, and UART_DIV=217 assumes it
BAUD = 115200

tt = DemoBoard.get()

# QSPI Pmod wiring (uio index): CS0=0, SD0=1, SD1=2, SCK=3, SD2=4, SD3=5
_CS0, _SD0, _SD1, _SCK = 0, 1, 2, 3


# ------------------------------------------------------------- flash access
# With the ASIC held in reset (or another design selected), the RP2040 owns
# the Pmod pins and can program the W25Q128 directly: WREN 06h, sector
# erase 20h, page program 02h, status poll 05h.

def _pins_for_flash():
    tt.mode = RPMode.ASIC_RP_CONTROL     # RP2040 drives the uio bank
    tt.reset_project(True)               # keep TinyRV32 off the bus
    cs = tt.pins.uio[_CS0]; cs.init(cs.OUT, value=1)
    sck = tt.pins.uio[_SCK]; sck.init(sck.OUT, value=0)
    mosi = tt.pins.uio[_SD0]; mosi.init(mosi.OUT, value=0)
    miso = tt.pins.uio[_SD1]; miso.init(miso.IN)
    return cs, sck, mosi, miso


def _xfer(cs, sck, mosi, miso, tx, rx_len=0):
    """Bit-banged SPI mode 0, MSB first."""
    cs.value(0)
    for byte in tx:
        for i in range(7, -1, -1):
            mosi.value((byte >> i) & 1)
            sck.value(1)
            sck.value(0)
    rx = bytearray()
    for _ in range(rx_len):
        b = 0
        for _ in range(8):
            sck.value(1)
            b = (b << 1) | miso.value()
            sck.value(0)
        rx.append(b)
    cs.value(1)
    return bytes(rx)


def _wait_ready(cs, sck, mosi, miso):
    while _xfer(cs, sck, mosi, miso, b"\x05", 1)[0] & 1:   # WIP bit
        time.sleep_ms(1)


def flash_program(path, base=0x000000):
    """Erase + program + verify a binary image into the Pmod flash."""
    data = open(path, "rb").read()
    pins = _pins_for_flash()
    cs, sck, mosi, miso = pins

    jedec = _xfer(*pins, b"\x9f", 3)
    print("flash JEDEC id:", jedec.hex())   # W25Q128 -> ef4018

    nsectors = (len(data) + 0xFFF) // 0x1000
    for s in range(nsectors):
        a = base + s * 0x1000
        _xfer(*pins, b"\x06")               # WREN
        _xfer(*pins, bytes([0x20, a >> 16 & 0xFF, a >> 8 & 0xFF, a & 0xFF]))
        _wait_ready(*pins)
    for off in range(0, len(data), 256):
        a = base + off
        chunk = data[off:off + 256]
        _xfer(*pins, b"\x06")
        _xfer(*pins, bytes([0x02, a >> 16 & 0xFF, a >> 8 & 0xFF, a & 0xFF]) + chunk)
        _wait_ready(*pins)

    back = _xfer(*pins, bytes([0x03, base >> 16 & 0xFF, base >> 8 & 0xFF,
                               base & 0xFF]), len(data))
    assert back == data, "flash verify FAILED"
    print("flash programmed + verified:", len(data), "bytes")


# ------------------------------------------------------------- run + listen

def run(listen_s=10):
    """Select TinyRV32, start the clock, release reset, print the UART."""
    tt.mode = RPMode.ASIC_ON_BOARD       # hand the uio bank to the ASIC
    tt.shuttle.tt_um_joonatanalanampa_rv32.enable()
    tt.clock_project_PWM(CLOCK_HZ)

    # UART RX on uo[0]. VERIFY-ON-HARDWARE: use the hardware UART if the
    # muxed RP2040 pin is UART-capable; ttboard exposes the raw Pin here.
    from machine import UART, Pin
    uart = UART(0, baudrate=BAUD, rx=Pin(tt.pins.uo[0].raw_pin), tx=None)

    tt.reset_project(True)
    time.sleep_ms(10)
    tt.reset_project(False)

    deadline = time.ticks_add(time.ticks_ms(), int(listen_s * 1000))
    out = b""
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if uart.any():
            out += uart.read()
        if (int(tt.pins.uo[1].value()) == 1) and out.endswith(b"\n"):
            break                        # HALTED pin high and line finished
    print(out.decode())                  # expect: Hello from my own CPU!
                                         #         fib(10)=55
    led = 0
    for i in range(6):
        led |= int(tt.pins.uo[2 + i].value()) << i
    print("LEDs:", bin(led), "(expect 0b110111 = 55)")
    print("HALTED:", int(tt.pins.uo[1].value()))
    return out

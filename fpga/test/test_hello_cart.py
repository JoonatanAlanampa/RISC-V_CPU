# test_hello_cart.py — THE pre-hardware rehearsal: the real sw/hello.bin
# (the exact bytes flash_cartridge.py will write) XIP-boots through the
# cartridge pin permutation, switches to QUAD mid-program, recurses fib(10)
# with its stack in the PSRAM model, and says hello on the UART.
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.queue import Queue
from cocotb.triggers import FallingEdge, Timer, with_timeout

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "test"))
from test import SpiMem  # the proven serial+quad behavioral model

HELLO_BIN = ROOT / "sw" / "hello.bin"
EXPECTED = "Hello from my own CPU!\nfib(10)=55\n"
BIT_NS = 8681


class UartRx:
    """Continuous 115200 8N1 listener on ftdi_rxd (never misses a start)."""

    def __init__(self, dut):
        self.dut = dut
        self.q = Queue()
        cocotb.start_soon(self._listen())

    async def _listen(self):
        while True:
            await FallingEdge(self.dut.ftdi_rxd)
            await Timer(BIT_NS + BIT_NS // 2, "ns")
            val = 0
            for i in range(8):
                val |= int(self.dut.ftdi_rxd.value) << i
                await Timer(BIT_NS, "ns")
            self.q.put_nowait(val)

    async def get(self, timeout_ms=400):
        return await with_timeout(self.q.get(), timeout_ms, "ms")


SKIPS = [0, None]  # X-event count, last-time (diagnostic only)


def _bit(ch, default):
    """Resolve one logic char like the real wire would: X from an undefined
    register store is still a driven level on silicon; pull-ups win on Z."""
    return 1 if ch == "1" else 0 if ch == "0" else default


async def wire_spi_bus(dut, flash, ram):
    """Wire-level port of test/test.py's spi_bus: the models see only the
    header nets, through the cartridge-side lane mapping in tb_hello.sv.
    X-tolerant per signal — dropping whole edges on X loses SCK/CS events
    and desyncs the model forever (sim pessimism; real pins always have
    SOME level). CS resolves toward deselected (cartridge pull-ups), SCK
    holds its previous value, X data bits become defined garbage."""
    prev_sck = 0
    while True:
        await FallingEdge(dut.clk)
        sck_s = str(dut.m_sck.value)
        cs0_s = str(dut.m_cs0.value)
        cs1_s = str(dut.m_cs1.value)
        sd_s = str(dut.m_sd.value)
        if "x" in (sck_s + cs0_s + cs1_s + sd_s).lower():
            SKIPS[0] += 1
            SKIPS[1] = cocotb.utils.get_sim_time("ns")
        sck = _bit(sck_s, prev_sck)
        cs0 = _bit(cs0_s, 1)
        cs1 = _bit(cs1_s, 1)
        io = 0
        for i, ch in enumerate(reversed(sd_s)):   # str is MSB-first
            io |= _bit(ch, 1) << i
        sel = flash if cs0 == 0 else (ram if cs1 == 0 else None)
        for dev in (flash, ram):
            if dev is not sel:
                dev.deselect()
        if sel is not None:
            if sck and not prev_sck:
                sel.on_rise(io)
            elif prev_sck and not sck:
                sel.on_fall()
            dut.mdl_oe.value = sel.out_mask
            dut.mdl_o.value = sel.out_val
        else:
            dut.mdl_oe.value = 0
        prev_sck = sck


@cocotb.test(timeout_time=600, timeout_unit="ms")
async def hello_from_cartridge(dut):
    mapb = int(cocotb.plusargs.get("MAPB", 0))
    cocotb.start_soon(Clock(dut.clk, 40, "ns").start())

    flash = SpiMem(1 << 16, writable=False)
    ram = SpiMem(1 << 23, writable=True)   # full 8 MB: stack at the top
    text = HELLO_BIN.read_bytes()
    flash.mem[:len(text)] = text
    cocotb.start_soon(wire_spi_bus(dut, flash, ram))

    rx = UartRx(dut)
    got = ""
    while len(got) < len(EXPECTED):
        got += chr(await rx.get())
        if got != EXPECTED[:len(got)]:
            raise AssertionError(f"UART diverged: {got!r}")
    dut._log.info("UART said: %r (mapping %s)", got, "B" if mapb else "A")

    await Timer(200, "us")                 # let the final store + ecall land
    led = int(dut.led.value)
    assert (led >> 7) & 1 == 1, "halted LED"
    assert led & 0x3F == 55 & 0x3F, f"LED shows {led & 0x3F}, want fib(10)"
    dut._log.info("halted with fib(10)=55 on the LEDs — full stack rehearsed")

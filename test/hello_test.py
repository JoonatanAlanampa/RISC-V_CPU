# SPDX-FileCopyrightText: © 2026 Joonatan Alanampa
# SPDX-License-Identifier: Apache-2.0
#
# The iconic milestone, ASIC edition: GCC-compiled C (sw/hello.c, RV32E)
# executes in place from the SPI flash model, recursing fib(10) with its
# stack in PSRAM, and speaks over the UART pin — decoded here at 115200
# baud, exactly as the real chip will be heard.
#
# Run:  python sw/build.py hello && TEST_MODULE=hello_test python run.py

from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, with_timeout

from test import SpiMem, spi_bus

HELLO_BIN = Path(__file__).parent.parent / "sw" / "hello.bin"
UART_DIV = 217  # clk per bit @ 25 MHz / 115200 baud

EXPECTED = "Hello from my own CPU!\nfib(10)=55\n"


async def uart_decoder(dut, chars):
    """8N1 decoder on uo_out[0]."""
    while True:
        # wait for start bit (idle high -> low)
        while True:
            await RisingEdge(dut.clk)
            v = dut.uo_out.value
            if v.is_resolvable and (int(v) & 1) == 0:
                break
        await ClockCycles(dut.clk, UART_DIV + UART_DIV // 2)  # mid bit 0
        c = 0
        for i in range(8):
            c |= (int(dut.uo_out.value) & 1) << i
            await ClockCycles(dut.clk, UART_DIV)
        chars.append(chr(c))  # stop-bit period doubles as re-sync gap


@cocotb.test()
async def test_hello(dut):
    clock = Clock(dut.clk, 40, unit="ns")  # 25 MHz
    cocotb.start_soon(clock.start())

    flash = SpiMem(1 << 16, writable=False)
    ram = SpiMem(1 << 23, writable=True)  # full 8 MB: stack at the top
    text = HELLO_BIN.read_bytes()
    flash.mem[:len(text)] = text
    cocotb.start_soon(spi_bus(dut, flash, ram))

    chars = []
    cocotb.start_soon(uart_decoder(dut, chars))

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    core = dut.user_project.core
    await with_timeout(RisingEdge(core.halted), 200, "ms")
    # drain any final character still on the wire
    await ClockCycles(dut.clk, UART_DIV * 12)

    text_out = "".join(chars)
    dut._log.info("UART said: %r", text_out)
    assert text_out == EXPECTED, f"UART output {text_out!r} != {EXPECTED!r}"

    led = (int(dut.uo_out.value) >> 2) & 0x3F
    assert led == 55 & 0x3F, f"LEDs show {led:#04x}, expected fib(10)=55"

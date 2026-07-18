# SPDX-FileCopyrightText: © 2026 Joonatan Alanampa
# SPDX-License-Identifier: Apache-2.0
#
# Official rv32ui riscv-tests, executed by TinyRV32 in place from the SPI
# flash model with .data preloaded in the PSRAM model (built by
# build_riscv_tests.py). Pass/fail protocol: ecall halts the core with
# a0 == 1 on pass, (testnum << 1) | 1 on failure.
#
# Run:  TEST_MODULE=riscv_suite python run.py
# Optionally RISCV_GLOB=s* to run a subset, QUAD=1 to run the entire suite
# with quad-mode memory (QSPI_CFG deposited to 3 after each reset — the
# register holds a deposit because the RTL only assigns it on MMIO writes).

import fnmatch
import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, with_timeout

from test import SpiMem, spi_bus

BINS = Path(__file__).parent / "riscv_bins"
RAM_BASE = 0x01000000


@cocotb.test()
async def test_riscv_suite(dut):
    clock = Clock(dut.clk, 40, unit="ns")  # 25 MHz
    cocotb.start_soon(clock.start())

    flash = SpiMem(1 << 16, writable=False)
    ram = SpiMem(1 << 16, writable=True)
    cocotb.start_soon(spi_bus(dut, flash, ram))

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    core = dut.user_project.core

    pattern = os.environ.get("RISCV_GLOB", "*")
    tests = sorted(p.stem[:-5] for p in BINS.glob("*.text.bin")
                   if fnmatch.fnmatch(p.stem[:-5], pattern))
    assert tests, f"no tests match {pattern!r} in {BINS}"

    failures = []
    for name in tests:
        text = (BINS / f"{name}.text.bin").read_bytes()
        data = (BINS / f"{name}.data.bin").read_bytes()

        flash.mem[:] = bytes(len(flash.mem))
        flash.mem[:len(text)] = text
        ram.mem[:] = bytes(len(ram.mem))
        ram.mem[:len(data)] = data
        flash.deselect()
        ram.deselect()

        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 10)
        dut.rst_n.value = 1
        if os.environ.get("QUAD") == "1":
            await ClockCycles(dut.clk, 2)
            core.qspi_cfg.value = 3

        verdict = "TIMEOUT"
        try:
            # ~130 clk per instruction over serial SPI; 40 ms of sim time
            # is a 1M-cycle budget
            await with_timeout(RisingEdge(core.halted), 40, "ms")
            a0 = int(core.rf.regs[10].value)
            if a0 == 1:
                verdict = "PASS"
            else:
                verdict = f"FAIL (test #{a0 >> 1})"
        except Exception:
            pass

        dut._log.info("%-10s %s", name, verdict)
        if verdict != "PASS":
            failures.append(f"{name}: {verdict}")

    assert not failures, f"{len(failures)}/{len(tests)} failed: {failures}"
    dut._log.info("=== rv32ui: ALL %d PASS (fence_i, ma_data skipped) ===",
                  len(tests))

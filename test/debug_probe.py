# Temporary debug probe: run with COCOTB_TEST_MODULES=debug_probe
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

from test import SpiMem, spi_bus, smoke_program


def peek(sig):
    try:
        v = sig.value
        return str(v)
    except Exception as e:
        return f"<{e}>"


@cocotb.test()
async def probe(dut):
    clock = Clock(dut.clk, 40, unit="ns")
    cocotb.start_soon(clock.start())

    flash = SpiMem(1 << 16, writable=False)
    ram = SpiMem(1 << 16, writable=True)
    for i, insn in enumerate(smoke_program()):
        flash.mem[4 * i:4 * i + 4] = insn.to_bytes(4, "little")
    cocotb.start_soon(spi_bus(dut, flash, ram))

    dut.ena.value = 1
    dut.ui_in.value = 0x15
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    core = dut.user_project.core
    arb = dut.user_project.arb
    qspi = dut.user_project.qspi

    for n in (5, 50, 150, 300, 600, 1200):
        await ClockCycles(dut.clk, n)
        dut._log.info(
            "fbusy=%s fpc=%s npc=%s valid_d=%s instr_d=%s if_ack=%s | "
            "grant=%s m_req=%s m_ack=%s | q.state=%s q.nbits=%s sck_cs=%s | "
            "valid_e=%s valid_m=%s mstall=%s halted=%s pc_d=%s",
            peek(core.fbusy), peek(core.fpc), peek(core.npc),
            peek(core.valid_d), peek(core.instr_d), peek(core.if_ack),
            peek(arb.grant), peek(arb.m_req), peek(arb.m_ack),
            peek(qspi.state), peek(qspi.nbits), peek(dut.uio_out),
            peek(core.valid_e), peek(core.valid_m), peek(core.mstall),
            peek(core.halted), peek(core.pc_d),
        )

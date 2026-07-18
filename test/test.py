# SPDX-FileCopyrightText: © 2026 Joonatan Alanampa
# SPDX-License-Identifier: Apache-2.0
#
# TinyRV32 smoke test: the CPU executes in place from a behavioral SPI flash
# model, reads/writes a behavioral SPI PSRAM model, drives the LED MMIO,
# reads GPIO, and halts on ecall. Pin-level: the models speak SPI mode 0 on
# the QSPI Pmod pins (uio), exactly like the real chip will.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

# uio bit positions (QSPI Pmod)
CS0 = 0   # flash, active low
SD0 = 1   # MOSI (from chip)
SD1 = 2   # MISO (to chip)
SCK = 3
CS1 = 6   # PSRAM, active low

# ---------------------------------------------------------------- mini assembler

def r_type(f7, rs2, rs1, f3, rd, op):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op

def i_type(imm, rs1, f3, rd, op):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op

def s_type(imm, rs2, rs1, f3):
    return (((imm >> 5) & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((imm & 0x1F) << 7) | 0x23

def b_type(imm, rs2, rs1, f3):
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) | (rs2 << 20) | \
           (rs1 << 15) | (f3 << 12) | (((imm >> 1) & 0xF) << 8) | (((imm >> 11) & 1) << 7) | 0x63

def lui(rd, imm20):     return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | 0x37
def addi(rd, rs1, imm): return i_type(imm, rs1, 0, rd, 0x13)
def andi(rd, rs1, imm): return i_type(imm, rs1, 7, rd, 0x13)
def srli(rd, rs1, sh):  return i_type(sh, rs1, 5, rd, 0x13)
def lw(rd, off, rs1):   return i_type(off, rs1, 2, rd, 0x03)
def lb(rd, off, rs1):   return i_type(off, rs1, 0, rd, 0x03)
def sw(rs2, off, rs1):  return s_type(off, rs2, rs1, 2)
def sb(rs2, off, rs1):  return s_type(off, rs2, rs1, 0)
def bne(rs1, rs2, off): return b_type(off, rs2, rs1, 1)
ECALL = 0x00000073

# ---------------------------------------------------------------- SPI models

class SpiMem:
    """Behavioral SPI slave (mode 0): 03h read, 02h write, 24-bit address."""

    def __init__(self, size, writable):
        self.mem = bytearray(size)
        self.writable = writable
        self.deselect()

    def deselect(self):
        self.sh = 0
        self.nbits = 0
        self.cmd = None
        self.addr = 0
        self.miso = 1

    def on_rise(self, mosi):          # master drives, slave samples
        if self.cmd == 0x03:
            return
        self.sh = ((self.sh << 1) | mosi) & 0xFFFFFFFF
        self.nbits += 1
        if self.nbits == 32 and self.cmd is None:
            self.cmd = self.sh >> 24
            self.addr = self.sh & 0xFFFFFF
            self.sh = 0
            self.nbits = 0
            if self.cmd == 0x03:
                self.bit_idx = 8      # first on_fall loads the first byte
        elif self.cmd == 0x02 and self.nbits == 8:
            if self.writable:
                self.mem[self.addr % len(self.mem)] = self.sh & 0xFF
            self.addr += 1
            self.sh = 0
            self.nbits = 0

    def on_fall(self):                # slave shifts out next read bit
        if self.cmd != 0x03:
            return
        if self.bit_idx == 8:
            self.cur = self.mem[self.addr % len(self.mem)]
            self.addr += 1
            self.bit_idx = 0
        self.miso = (self.cur >> (7 - self.bit_idx)) & 1
        self.bit_idx += 1


async def spi_bus(dut, flash, ram):
    """Samples the pad bus on the falling clk edge — halfway between DUT
    edges, so rising-edge NBA updates have settled and our MISO write lands
    well before the DUT's next sample. SCK toggles at clk/2 so every SCK
    edge is seen exactly once."""
    prev_sck = 0
    while True:
        await FallingEdge(dut.clk)
        v = dut.uio_out.value
        if not v.is_resolvable:      # X during reset
            continue
        out = int(v)
        sck = (out >> SCK) & 1
        mosi = (out >> SD0) & 1
        sel = None
        if not (out >> CS0) & 1:
            sel = flash
        elif not (out >> CS1) & 1:
            sel = ram
        for dev in (flash, ram):
            if dev is not sel:
                dev.deselect()
        if sel is not None:
            if sck and not prev_sck:
                sel.on_rise(mosi)
            elif prev_sck and not sck:
                sel.on_fall()
            dut.uio_in.value = (sel.miso & 1) << SD1
        prev_sck = sck


# ---------------------------------------------------------------- the test

MMIO_HI = 0x10        # lui value: 0x0001_0000
RAM_HI = 0x1000       # lui value: 0x0100_0000 (PSRAM)


def smoke_program():
    x0, x5, x6, x7, x8, x9, x11, x12, x13, x14 = 0, 5, 6, 7, 8, 9, 11, 12, 13, 14
    return [
        lui(x5, MMIO_HI),         # x5 = MMIO base
        lui(x7, RAM_HI),          # x7 = PSRAM base
        addi(x6, x0, 0x2A),       # x6 = 0x2A
        sw(x6, 0, x5),            # LED <= 0x2A
        addi(x8, x0, -3),         # x8 = 0xFFFF_FFFD
        sw(x8, 4, x7),            # PSRAM[4] = FD FF FF FF
        lw(x9, 4, x7),            # x9 = 0xFFFFFFFD
        sw(x9, 0, x5),            # LED <= 0xFD (visible: 0x3D)
        sb(x6, 6, x7),            # PSRAM byte +6 = 0x2A
        lw(x11, 4, x7),           # x11 = 0x2AFFFFFD (bytes FD FF FF 2A... little endian: FD FF 2A FF -> 0xFF2AFFFD)
        srli(x12, x11, 16),       # x12 = 0x0000FF2A
        andi(x12, x12, 0xFF),     # x12 = 0x2A
        sw(x12, 0, x5),           # LED <= 0x2A
        addi(x13, x0, 3),         # countdown loop: 3 iterations
        addi(x13, x13, -1),       # <- loop target (pc = 0x38)
        bne(x13, x0, -4),         # taken twice, then falls through
        sw(x13, 0, x5),           # LED <= 0: proves the loop actually ran
        lw(x14, 8, x5),           # x14 = GPIO in
        sw(x14, 0, x5),           # LED <= GPIO
        ECALL,                    # halt
    ]


@cocotb.test()
async def test_smoke(dut):
    clock = Clock(dut.clk, 40, unit="ns")  # 25 MHz
    cocotb.start_soon(clock.start())

    flash = SpiMem(1 << 16, writable=False)
    ram = SpiMem(1 << 16, writable=True)

    for i, insn in enumerate(smoke_program()):
        flash.mem[4 * i:4 * i + 4] = insn.to_bytes(4, "little")

    cocotb.start_soon(spi_bus(dut, flash, ram))

    gpio_val = 0x15
    dut.ena.value = 1
    dut.ui_in.value = gpio_val
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    led_seq = []
    halted = False
    for _ in range(30000):
        await RisingEdge(dut.clk)
        uo = int(dut.uo_out.value)
        led = uo >> 2
        if not led_seq or led != led_seq[-1]:
            led_seq.append(led)
        if (uo >> 1) & 1:
            halted = True
            break

    assert halted, f"CPU never halted; LED history: {[hex(v) for v in led_seq]}"

    # LED starts at 0, then the program's writes (6-bit visible slice);
    # the 0x00 after 0x2A is the loop counter — catches broken taken-branches
    expected = [0x00, 0x2A, 0xFD & 0x3F, 0x2A, 0x00, gpio_val & 0x3F]
    assert led_seq == expected, \
        f"LED sequence {[hex(v) for v in led_seq]} != {[hex(v) for v in expected]}"

    # PSRAM contents after the run: word at +4 with byte +6 patched
    assert ram.mem[4:8] == bytes([0xFD, 0xFF, 0x2A, 0xFF]), ram.mem[0:12].hex()

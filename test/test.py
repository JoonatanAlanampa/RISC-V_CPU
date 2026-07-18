# SPDX-FileCopyrightText: © 2026 Joonatan Alanampa
# SPDX-License-Identifier: Apache-2.0
#
# TinyRV32 smoke test + the shared pin-level SPI/QSPI memory models.
# The CPU executes in place from a behavioral flash model, reads/writes a
# behavioral PSRAM model, flips itself into QUAD mode via the QSPI_CFG
# MMIO register mid-program, drives the LED MMIO, reads GPIO, and halts.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

# uio bit positions (QSPI Pmod): SD0..SD3 on uio[1,2,4,5]
CS0 = 0   # flash, active low
SD_BITS = (1, 2, 4, 5)
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

# ---------------------------------------------------------------- SPI/QSPI models

class SpiMem:
    """Behavioral SPI/QSPI slave, mode 0.

    Serial: 03h read, 02h write (24-bit address).
    Quad:   6Bh fast-read-quad-output (serial cmd+addr, 8 dummies, quad data)
            EBh quad read (serial cmd, quad addr, 6 waits, quad data)
            38h quad write (serial cmd, quad addr, quad data)
    """

    def __init__(self, size, writable):
        self.mem = bytearray(size)
        self.writable = writable
        self.deselect()

    def deselect(self):
        self.phase = "cmd"
        self.sh = 0
        self.n = 0
        self.cmd = None
        self.addr = 0
        self.dummy_left = 0
        self.nib_idx = 0
        self.cur = 0
        self.out_mask = 0     # which SD bits we drive
        self.out_val = 0

    def _begin_read(self, quad):
        self.phase = "rd_q" if quad else "rd_s"
        self.nib_idx = 2      # forces a fresh byte load on first on_fall
        self.bit_idx = 8

    def on_rise(self, io):
        bit = io & 1          # serial traffic is on SD0
        if self.phase == "cmd":
            self.sh = ((self.sh << 1) | bit) & 0xFF
            self.n += 1
            if self.n == 8:
                self.cmd = self.sh
                self.sh = 0
                self.n = 0
                if self.cmd in (0x03, 0x02, 0x6B):
                    self.phase = "addr_s"
                elif self.cmd in (0xEB, 0x38):
                    self.phase = "addr_q"
                else:
                    self.phase = "ignore"
        elif self.phase == "addr_s":
            self.sh = ((self.sh << 1) | bit) & 0xFFFFFF
            self.n += 1
            if self.n == 24:
                self.addr = self.sh
                self.sh = 0
                self.n = 0
                if self.cmd == 0x03:
                    self._begin_read(False)
                elif self.cmd == 0x02:
                    self.phase = "wr_s"
                else:                       # 6Bh: 8 dummy clocks first
                    self.phase = "dummy"
                    self.dummy_left = 8
        elif self.phase == "addr_q":
            self.sh = ((self.sh << 4) | io) & 0xFFFFFF
            self.n += 1
            if self.n == 6:
                self.addr = self.sh
                self.sh = 0
                self.n = 0
                if self.cmd == 0xEB:
                    self.phase = "dummy"
                    self.dummy_left = 6
                else:                       # 38h quad write
                    self.phase = "wr_q"
        elif self.phase == "dummy":
            self.dummy_left -= 1
            if self.dummy_left == 0:
                self._begin_read(True)
        elif self.phase == "wr_s":
            self.sh = ((self.sh << 1) | bit) & 0xFF
            self.n += 1
            if self.n == 8:
                if self.writable:
                    self.mem[self.addr % len(self.mem)] = self.sh
                self.addr += 1
                self.sh = 0
                self.n = 0
        elif self.phase == "wr_q":
            self.sh = ((self.sh << 4) | io) & 0xFF
            self.n += 1
            if self.n == 2:
                if self.writable:
                    self.mem[self.addr % len(self.mem)] = self.sh
                self.addr += 1
                self.sh = 0
                self.n = 0

    def on_fall(self):
        if self.phase == "rd_s":
            if self.bit_idx == 8:
                self.cur = self.mem[self.addr % len(self.mem)]
                self.addr += 1
                self.bit_idx = 0
            self.out_mask = 0b0010          # SD1 (MISO)
            self.out_val = (((self.cur >> (7 - self.bit_idx)) & 1) << 1)
            self.bit_idx += 1
        elif self.phase == "rd_q":
            if self.nib_idx == 2:
                self.cur = self.mem[self.addr % len(self.mem)]
                self.addr += 1
                self.nib_idx = 0
            nib = (self.cur >> 4) & 0xF if self.nib_idx == 0 else self.cur & 0xF
            self.out_mask = 0b1111
            self.out_val = nib
            self.nib_idx += 1
        else:
            self.out_mask = 0
            self.out_val = 0


async def spi_bus(dut, flash, ram):
    """Pin-level bus glue, sampled on the falling clk edge (rising-edge NBA
    values settled; our writes land before the DUT's next sample). SCK
    toggles at clk/2 so every SCK edge is seen exactly once."""
    prev_sck = 0
    while True:
        await FallingEdge(dut.clk)
        v = dut.uio_out.value
        oe = dut.uio_oe.value
        if not (v.is_resolvable and oe.is_resolvable):
            continue
        out = int(v)
        oem = int(oe)
        sck = (out >> SCK) & 1
        # master nibble: driven pins as driven, released pins pulled up
        io = 0
        for i, b in enumerate(SD_BITS):
            io |= (((out >> b) & 1) if (oem >> b) & 1 else 1) << i
        sel = None
        if not (out >> CS0) & 1:
            sel = flash
        elif not (out >> CS1) & 1:
            sel = ram
        for dev in (flash, ram):
            if dev is not sel:
                dev.deselect()
        uin = 0
        for i, b in enumerate(SD_BITS):     # idle bus: pull-ups
            uin |= 1 << b
        if sel is not None:
            if sck and not prev_sck:
                sel.on_rise(io)
            elif prev_sck and not sck:
                sel.on_fall()
            uin = 0
            for i, b in enumerate(SD_BITS):
                bit = (sel.out_val >> i) & 1 if (sel.out_mask >> i) & 1 else 1
                uin |= bit << b
        dut.uio_in.value = uin
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
        lw(x11, 4, x7),           # x11 = 0xFF2AFFFD
        srli(x12, x11, 16),       # x12 = 0x0000FF2A
        andi(x12, x12, 0xFF),     # x12 = 0x2A
        sw(x12, 0, x5),           # LED <= 0x2A
        addi(x13, x0, 3),         # countdown loop: 3 iterations
        addi(x13, x13, -1),       # <- loop target (pc = 0x38)
        bne(x13, x0, -4),         # taken twice, then falls through
        sw(x13, 0, x5),           # LED <= 0: proves the loop actually ran
        addi(x6, x0, 3),          # QSPI_CFG <= 3: flash + PSRAM go QUAD;
        sw(x6, 12, x5),           # every fetch and PSRAM access below is 4-bit
        lw(x14, 12, x5),          # read the cfg back
        sw(x14, 0, x5),           # LED <= 3
        sw(x8, 8, x7),            # PSRAM quad write (38h)
        lw(x9, 8, x7),            # PSRAM quad read (EBh)
        sw(x9, 0, x5),            # LED <= 0xFD again (visible 0x3D)
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

    # LED starts at 0; the 0x00 after the loop proves taken branches work,
    # the 0x03 is the QSPI_CFG readback, everything after it ran in QUAD
    expected = [0x00, 0x2A, 0xFD & 0x3F, 0x2A, 0x00, 0x03, 0xFD & 0x3F,
                gpio_val & 0x3F]
    assert led_seq == expected, \
        f"LED sequence {[hex(v) for v in led_seq]} != {[hex(v) for v in expected]}"

    # PSRAM contents: word at +4 with byte +6 patched (serial mode), and
    # the quad-written word at +8
    assert ram.mem[4:8] == bytes([0xFD, 0xFF, 0x2A, 0xFF]), ram.mem[0:12].hex()
    assert ram.mem[8:12] == bytes([0xFD, 0xFF, 0xFF, 0xFF]), ram.mem[0:12].hex()

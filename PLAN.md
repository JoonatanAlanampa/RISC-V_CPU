# TinyRV32: ASIC port of the CPU project (chip #2)

Port of the proven RV32I core from https://github.com/JoonatanAlanampa/CPU
(`rv32/hdl/`, vendored in `core/`) to a TinyTapeout Sky130 tile. The core
passes all 40 official rv32ui riscv-tests and runs GCC-compiled C on a
ULX3S FPGA (5-stage pipeline, Fmax 32+ MHz on ECP5 -6).

## Why this is not a copy-paste

A TinyTapeout tile has **no RAM**. The FPGA build uses 61 block RAMs:
64 KB imem + 64 KB dmem, the 4 KB cache, and video/audio buffers. None of
that exists on the ASIC. Everything else (datapath, control, forwarding,
UART) is portable SystemVerilog.

## Target architecture

```
             +--------------------------------------+
             |  tt_um_joonatanalanampa_rv32         |
             |                                      |
  uio[7:0] <-+ qspi_ctrl <- arbiter <- fetch port   |
 (QSPI Pmod) |                ^        (cpu_pipe)   |
             |                +------- data port    |
             |                                      |
  uo[0]    <-+ uart_txd     (MMIO 0x10004)          |
  uo[7:1]  <-+ LED/status   (MMIO 0x10000)          |
  ui[7:0]  --+ GPIO in      (MMIO 0x10008)          |
             +--------------------------------------+
```

- **Memory**: TinyTapeout **QSPI Pmod** (standard pinout: CS0=uio[0],
  SD0..SD3=uio[1,2,4,5], SCK=uio[3], CS1=uio[6], CS2=uio[7]) — W25Q128
  flash for code (XIP) + 2x 8 MB PSRAM for data/stack.
- **Memory map**: flash at 0x0000_0000 (code), PSRAM at 0x0100_0000
  (data/stack), MMIO unchanged from FPGA build (0x10000 LED, 0x10004 UART,
  0x10008 GPIO in).
- **Core**: `cpu_pipe.sv` (5-stage). Both fetch and data already speak (or
  will be converted to) the same req/ack handshake used by the FPGA
  cache/SDRAM path, so the QSPI controller drops in behind an arbiter.
- **Strip for ASIC**: imem/dmem BRAMs, video (vid_*), audio, SNES pad —
  removed or compiled out. UART TX and LED stay.

## Port tasks (in order)

1. [x] `src/qspi_ctrl.sv` — req/ack QSPI master (SPI 1-bit mode, 03h/02h,
       SCK=clk/2; ~132 clk per word). Quad mode still open.
2. [x] `mem_arbiter` (in qspi_ctrl.sv) — 2:1, data priority, grant held
       until ack.
3. [x] `src/rv32_core.sv` — ASIC build of cpu_pipe: fetch FSM over req/ack
       (non-blocking: bubbles while in flight, fdrop on taken branch),
       all non-MMIO data through the old SDRAM handshake, video/audio/pad
       stripped, GPIO-in added at 0x10008.
4. [x] cocotb tb with behavioral SPI flash+PSRAM models: smoke test plus
       **ALL 40 official rv32ui riscv-tests PASS through the QSPI XIP path**
       (test/riscv_suite.py; build_riscv_tests.py relinks .data to PSRAM).
       Found+fixed a real pipeline bug the FPGA build couldn't hit: a
       redirect landing on the same edge a fetch starts must suppress the
       fetch, or taken branches lose their target (rv32_core.sv fetch FSM).
5. [x] Hardening GREEN — final config **4x2 tiles @ 65% density, RV32E +
       burst-2 fetch** (2026-07-18, runs 29646705058 + 29647036676):
       signoff clean, gate-level test passed, TT precheck passed, viewer
       live at joonatanalanampa.github.io/RISC-V_CPU. History: 2x2 = 145%
       util; 4x2 @ 75% with 32 regs = 390k+ routing violations (regfile
       muxing saturates lower metals); RV32E halved the regfile -> routes
       clean at 8 tiles. 6x2 @ 55% with 32 regs also worked (fallback).
6. [x] Performance, superseding the line-buffer idea:
       - Burst-2 fetch (instruction pair per transaction): +22%.
       - **Quad-SPI v2** (2026-07-18): QSPI_CFG MMIO at 0x1000C, resets to
         0 = serial boot (cannot fail), software opts into quad. Flash 6Bh
         quad-output read; PSRAM EBh/38h quad read/write. Full riscv suite
         passes in BOTH modes; quad+burst = 2.2x vs v1 serial. hello.c
         enables quad after boot. Pmod pull-up assumption on SD2/SD3
         documented in qspi_ctrl.sv.
7. [x] `docs/info.md` datasheet + `bringup/bringup.py` MicroPython script
       (flash program/verify via bit-banged SPI + run + UART listen;
       marked VERIFY-ON-HARDWARE for SDK pin names).
8. [ ] Shrink experiments (in progress): can the design fit 3x2 (6 tiles)?
       Levers: latch-based regfile, single-cycle core, iterative shifter.
9. [ ] Submit on app.tinytapeout.com before ~2026-09-07.

## Performance expectation (honest)

XIP over SPI flash at ~clk/2 with no cache means multi-cycle fetches:
CPI will be dominated by memory, ~10-20x slower than the FPGA build.
That is fine — the goal is a *working CPU on our own silicon*; the line
buffer (task 6) and quad mode claw most of it back later.

## Status

- 2026-07-18: repo scaffolded from ttsky-verilog-template; proven core
  vendored into `core/` (alu, branch, control, cpu, cpu_pipe, immgen,
  regfile, uart_tx, cache); `src/project.v` is a placeholder stub so the
  template CI stays green. Next: task 1 (qspi_ctrl).

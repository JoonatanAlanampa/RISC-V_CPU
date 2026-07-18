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
4. [~] cocotb tb with behavioral SPI flash+PSRAM models (test/test.py):
       smoke test passes — XIP boot, PSRAM word/byte load/store, branch
       loop, GPIO read, LED MMIO, ecall halt. ~120 clk/instr as expected.
       Still open: full riscv-tests suite through the QSPI models.
5. [ ] First hardening run -> read area/utilization from the GDS action.
       Decide tiles (expect 2x2..4x2) and whether the 32x32 flop register
       file fits; fallbacks: RV32E (16 regs) or the single-cycle core.
6. [ ] Optional once working: shrink `cache.sv` to a flop-based line buffer
       (e.g. 2x16B) to hide QSPI latency; measure CPI in sim first.
7. [ ] `docs/info.md` datasheet + MicroPython bring-up script.

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

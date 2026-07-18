## How it works

TinyRV32 is a **RISC-V CPU** (RV32E: the 16-register embedded profile of
RV32I) with a classic **5-stage pipeline** — fetch, decode, execute,
memory, writeback — with full forwarding, load-use interlock, and
predict-not-taken branches. It began life as an FPGA design and passes
all 40 official `rv32ui` riscv-tests, here re-verified pin-level through
the ASIC memory subsystem.

The tile has no RAM, so the chip executes **in place from external QSPI
Pmod memory** (SPI mode): code and constants stream from W25Q128 flash,
data and stack live in APS6404 PSRAM. A fetch FSM and a 2:1 arbiter
funnel instruction and data traffic into one SPI memory controller
(commands 03h/02h, SCK = clk/2); the pipeline freezes on data accesses
and takes bubbles on fetches, so ~130 clocks per instruction at the
memory wall — a deliberately honest v1 (quad mode and burst fetch are
the planned upgrades).

Memory map:

| Range | What |
|---|---|
| 0x0000_0000 + | flash: code + rodata, execute in place |
| 0x0001_0000 | MMIO: +0 LED (w), +4 UART tx/busy (w/r), +8 GPIO in (r) |
| 0x0100_0000 + | PSRAM: .data, .bss, stack |

Software builds with plain GCC (`-march=rv32e -mabi=ilp32e`); the flash
image is self-contained — crt0 copies .data to PSRAM and zeroes .bss.
`sw/hello.c` prints "Hello from my own CPU! fib(10)=55" over the UART,
computed recursively with the stack in PSRAM. ECALL halts the core and
raises the HALTED pin.

## How to test

Attach the QSPI Pmod (flash + 2x PSRAM) to the bidirectional Pmod,
program `sw/hello.bin` into the flash, select a 25 MHz clock, release
reset, and listen on uo[0] at 115200 8N1:

```
Hello from my own CPU!
fib(10)=55
```

The LEDs (uo[7:2]) end at 0b110111 (55) and HALTED (uo[1]) goes high.
Any RV32E program under 64 KB of code works the same way: build with
`sw/build.py`, flash, reset.

## External hardware

- **TinyTapeout QSPI Pmod** (W25Q128 flash + 2x APS6404 PSRAM) on the
  uio Pmod — required.
- USB-serial adapter on uo[0] for the UART (the demo board's RP2040 can
  also read it), LEDs on uo[7:2] optional.

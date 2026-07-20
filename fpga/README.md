# TinyRV32 on the Cartridge Pmod (ULX3S harness)

Runs the **unchanged** `tt_um_joonatanalanampa_rv32` module on the ULX3S 85F
with its QSPI Pmod pins routed to the
[Cartridge Pmod](https://github.com/JoonatanAlanampa/cartridge_pmod) in the
J1 header (pins 1-12) — the same position and pin mapping as the cartridge's
own bring-up bitstream. This is the pre-silicon rehearsal of "TinyRV32
executes a program from the cartridge".

**1-PSRAM note**: no fallback needed — TinyRV32 as implemented already uses
exactly one PSRAM (CS1; CS2/uio[7] is held high, `link.ld` maps 8 MB RAM,
`crt0` puts the stack at its top). The "2x PSRAM" phrase in early PLAN.md
prose never made it into the design. On the cartridge, the held-high uio[7]
drives the audio input with DC — silent and harmless.

## Build / sim / flash

```powershell
python fpga\test\run.py            # sim: boots from flash model, PSRAM
                                   # round-trip, halts — both orientations
powershell -File fpga\synth.ps1    # -> fpga\build\rv32_cartridge.bit
openFPGALoader -b ulx3s fpga\build\rv32_cartridge.bit
```

2860 LUTs, Fmax ~47 MHz (needs 25).

## Bench procedure (after the cartridge passes its own bring-up)

1. Run the cartridge bring-up bitstream first; note the reported mapping.
2. **SW1** on the ULX3S selects the orientation: MAP A -> SW1 off,
   MAP B -> SW1 on. (SW1 is read live; no rebuild needed.)
3. The core XIP-boots from cartridge flash at 0x0. A **blank flash reads
   FFh** — the core fetches garbage and spins; that is expected until a
   program is written. Flashing a program image into the cartridge W25Q128
   is the next work item (options: bring-up-gateware extension with a UART
   flash-writer, or the TT demo board RP2040 route from the backlog).
4. With a program flashed: uo[0] UART appears on the USB serial port
   (115200), LED7 = halted, LED5:0 = the program's LED MMIO writes,
   buttons/switches feed GPIO-in MMIO (`ui = {sw[3:2], 2'b00, btn[4:1]}`).

## Full-stack rehearsal (`test/run_hello.py`)

The strongest pre-hardware proof: the **real `sw/hello.bin`** (the exact
bytes `flash_cartridge.py` writes) XIP-boots through the cartridge pin
permutation, switches to QUAD mid-program (`QSPI_CFG = 3`), recurses
fib(10) with its stack in the PSRAM model, and prints
`Hello from my own CPU!` / `fib(10)=55` on the UART — verified in both
plug orientations against the proven serial+quad `SpiMem` models wired at
the header-net level.

Sim lesson worth keeping: wire-level glue must be **X-tolerant per
signal**, never skip-on-X. `main()`'s prologue pushes a never-written
callee-saved register — real silicon stores junk harmlessly, but in sim
the X reaches MOSI; dropping those edges loses SCK/CS events and desyncs
the models forever. CS resolves toward deselected (cartridge pull-ups),
SCK holds, X data bits become defined garbage.

## Files

- `ulx3s_top.sv` — pin permutation + orientation mux around the tt_um core
  (mapping A: `gp[n] = uio[3-n]`, `gn[n] = uio[7-n]`; B swaps rows)
- `ulx3s.lpf` — same J1/LED/UART sites as the cartridge bring-up harness
- `test/` — cocotb: `run.py` = smoke (SV serial models, LED=42 round-trip);
  `run_hello.py` = the full-stack hello.bin rehearsal (python quad models
  on the header nets, `tb_hello.sv`)

# TinyRV32 silicon bring-up

`bringup.py` is the script to run the day the TTSKY26c chips arrive
(~mid-2027). It programs `sw/hello.bin` into the QSPI Pmod flash, boots the
CPU from it, decodes the UART, and prints a PASS/FAIL table.

`test_bringup_host.py` runs that **unmodified** script against a virtual demo
board on CPython, in CI, today — so it does not sit unverified for a year.

## Using it on the bench

Copy `bringup.py` and `sw/hello.bin` onto the demo board's filesystem
(`mpremote cp`, Thonny, whatever), seat the **QSPI Pmod on the bidirectional
header**, then:

```python
>>> import bringup as bu
>>> bu.main()          # program + boot + 12 checks
>>> bu.run()           # boot whatever is already in flash, print the UART
>>> bu.sweep_fmax()    # how fast does this die really run?
```

Expected on a healthy chip:

```
Hello from my own CPU!
fib(10)=55

12/12 checks passed
ALL PASS — the CPU boots from flash and runs C. That is the chip.
```

The checks run in dependency order, so **the first FAIL is the one to chase**;
everything after it fails as a consequence.

| # | Check | What a failure means |
|---|---|---|
| 1 | `bus drive` | RP2040 cannot drive uio — Pmod unseated, or wrong header |
| 2 | `flash id` | no `EF 40 18` — flash absent or miswired |
| 3 | `flash QE bit` | see the hazard below |
| 4 | `psram id`, `psram read/write` | stack memory is dead; `hello.c` cannot run |
| 5 | `flash program+verify` | image did not stick — do not boot garbage |
| 6 | `bus released` | RP2040 still driving uio; it would fight the ASIC |
| 7 | `uart banner`, `uart fib(10)`, `uart framing` | the CPU itself |
| 8 | `halted`, `leds` | ECALL and the MMIO LED register |

## The one hazard worth knowing about

`sw/hello.c` writes `QSPI_CFG = 3` as its **first statement** — so the very
next instruction fetch is a `6Bh` fast-read-quad-output. If the flash's **QE
bit** (status register 2, bit 1) is clear, that read goes into a device that is
not in quad mode and the CPU hangs immediately: no UART output, no LEDs, no
HALTED, nothing to see.

`src/qspi_ctrl.sv` and `docs/info.md` both say the QE bit is "factory-set on
the QSPI Pmod's W25Q128JV". **That has never been verified on hardware.** So
the script reads SR2, reports it, and sets it if clear (`main(set_qe=False)`
to check without writing). If the part refuses the write, rebuild `hello.c`
with `QSPI_CFG = 2` (PSRAM quad only, flash stays serial) — the chip always
boots in plain 1-bit SPI, which is the mode that cannot fail.

## Why the script slows the clock down

`uo[0]` is the UART TX pin. Two things stop us from just reading it at 115200:

* the RP2040 GPIO that `uo[0]` lands on **moves between demo-board revisions**
  (`UO_OUT0` is 30 on one map, 33 on another), and is not guaranteed to be
  UART-capable at all;
* MicroPython cannot bit-bang a 115200 receiver — 8.7 µs per bit.

So the script probes for a hardware `machine.UART` on that pin. If it gets one,
it runs the chip at the full 25 MHz. If it does not, it **clocks the chip at
~1.04 MHz instead**, which drops the baud rate to 4800 (≈208 µs/bit) — easily
sampled in software. `UART_DIV` is fixed in the RTL, so baud tracks the project
clock exactly, and the CPU does not care: only wall-clock time changes.

The host test asserts that bit-banging 115200 **fails**, so nobody can quietly
delete the slow-clock path and still see green.

## Firmware facts, verified not remembered

Checked against `tt-micropython-firmware` **v2.0.0**, commit
`f34d9f0da5b0245de8bbd78e1cd3b3c4170408b9` (microcotb `81f2498`) — the same
pin CI uses. The previous version of this script was written from recollection
and got most of the following wrong.

* **`RPMode.ASIC_ON_BOARD` does not exist.** `ttboard/mode.py` defines only
  `SAFE`, `ASIC_RP_CONTROL`, `ASIC_MANUAL_INPUTS` (plus `STANDALONE` in the
  development enum).
* **`ASIC_RP_CONTROL` does not hand you the uio bank.**
  `Pins.begin_asiconboard()` flips only the `ui_in*` pins to outputs; the bidir
  pins stay inputs. To bit-bang the Pmod you must set `tt.uio_oe_pico`
  yourself — **and clear it again before enabling the ASIC**, or both ends
  drive the same eight pins.
* **The bidir pins are `tt.pins.uio0` … `uio7`.** The docstrings in
  `demoboard.py` and `pins/pins.py` say `pins.uio_in5`; that name does not
  exist — `GPIOMap` defines `uio0`..`uio7`, and `Pins` has no `__getattr__` to
  paper over it. The firmware's own example is stale.
* **`StandardPin` is not `machine.Pin`.** Direction is `p.mode = Pin.OUT`
  (a property that calls `raw_pin.init`), and the pin is *callable*: `p()`
  reads, `p(1)` writes. There is no `.init(...)` / `.value()` pair.
* **`tt.ui_in` / `uo_out` / `uio_in` / `uio_out` / `uio_oe_pico` are microcotb
  IO ports, not ints.** Read with `int(port)`, write with `port.value = x`
  (`tt.uio_in = x` also works — `DemoBoard.__setattr__` forwards it).
  `port[i]` is a sampled `Logic` **bit**, not a pin. Raw `machine.Pin` objects
  live at `tt.pins.uo_out<N>.raw_pin`.
* **`clock_project_PWM()` retunes the RP2040 system clock** and silently
  settles for a nearby frequency — it only logs a warning. It *returns the PWM
  object*, so `pwm.freq()` is the frequency actually achieved. UART framing
  breaks at ~3% clock error, so baud is derived from that return value and
  never from the request. (CORDIC-1 has no such handle and recovers its clock
  from the `uo[0]` heartbeat instead; this chip has no heartbeat but does have
  `pwm.freq()`.)
* **Hardware SPI is not an option** for this Pmod: the uio bank sits on a GPIO
  run whose SPI-capable functions do not line up with this pinout's
  SCK/MOSI/MISO, and the GPIO numbers move between board revisions. Hence the
  bit-banged `Spi` class (2 port writes per bit).

## What the host test actually models

Not mocks — models, because a mock would have passed the broken script:

* **The uio bank at bit level.** The script writes whole uio bytes; the model
  watches CS and SCK edges inside those writes and drives a W25Q128 and an
  APS6404 state machine from them. Clock phasing, MSB-first ordering and read
  alignment are all exercised for real. *This is how the missing-eighth-edge
  bug in `Spi.xfer` was found* — the TX loop left SCK high, so the first read
  bit got no rising edge of its own and every response came back shifted.
* **The UART as a waveform.** `uo[0]`'s level is computed from virtual time,
  baud and the message, so the software receiver has to find start bits, sample
  mid-bit and validate stop bits.
* **Time costs**: a port access is 40 µs, a raw pin read 6 µs, a `ticks_us()`
  call 2 µs. Zero these out and every timing conclusion above becomes fiction.

Faults that must each be caught by the *right* check: `no_pmod`, `no_flash`,
`qe_stubborn`, `dead_psram`, `bad_program`, `cpu_dead`, `no_halt`, `bad_leds`
— plus a clear-but-settable QE bit that the script must **repair** rather than
fail on, a firmware without `uio_oe_pico` (the `.mode` fallback), and a PWM
that misses the requested clock by 7%.

It also found a real race: the receiver stops as soon as it has matched
`fib(10)=55`, but the program still has a newline to push and an `ECALL` to
execute. Sampling HALTED right there reports a working chip as broken, so
`check_halted` polls with a timeout instead.

```
python bringup/test_bringup_host.py     # stand-in ports
TT_FIRMWARE_SRC=ttfw/microcotb/src python bringup/test_bringup_host.py
```

A set-but-unusable `TT_FIRMWARE_SRC` is a hard error, never a silent fallback
to the stand-in — a green run must not claim firmware coverage it lacks.

## Still unverified until there is silicon

* the QE bit's factory state (the whole reason check 3 exists);
* whether `uo[0]` lands on a UART-capable pin on the actual demo board;
* the real Fmax — `sweep_fmax()` measures it, signoff says clean at 25 MHz;
* PSRAM `9Fh` behaviour: the model answers `0D 5D`, and the check is written
  to report what it got rather than merely assert.

# Build the official rv32ui riscv-tests for the TinyRV32 ASIC memory map.
#   python build_riscv_tests.py
# Reuses the CPU project's test environment (ecall + a0 pass/fail protocol)
# but relinks .data into PSRAM (link_asic.ld) and splits each ELF into
# <name>.text.bin (flash) + <name>.data.bin (PSRAM preload).
#
# Requires the CPU repo checkout (env header + vendored riscv-tests) and the
# xpack riscv-none-elf-gcc toolchain; override locations via CPU_REPO / XPACK.

import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
CPU_REPO = Path(os.environ.get("CPU_REPO", HOME / "Documents" / "CPU"))
XPACK = Path(os.environ.get(
    "XPACK", HOME / "opt" / "xpack-riscv-none-elf-gcc-15.2.0-1" / "bin"))

GCC = XPACK / "riscv-none-elf-gcc.exe"
OBJCOPY = XPACK / "riscv-none-elf-objcopy.exe"
ISA = CPU_REPO / "rv32" / "third_party" / "riscv-tests" / "isa"
ENV = CPU_REPO / "rv32" / "tests" / "env"
TEST_DIR = Path(__file__).parent
OUT = TEST_DIR / "riscv_bins"

SKIP = {"fence_i", "ma_data"}  # same as the FPGA runner: ROM imem / no misalign


def run(cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    OUT.mkdir(exist_ok=True)
    built = 0
    for src in sorted((ISA / "rv32ui").glob("*.S")):
        name = src.stem
        if name in SKIP:
            continue
        elf = OUT / f"{name}.elf"
        run([GCC, "-march=rv32i", "-mabi=ilp32", "-nostdlib", "-nostartfiles",
             "-static", "-I", ENV, "-I", ISA / "macros" / "scalar",
             "-T", TEST_DIR / "link_asic.ld", "-o", elf, src])
        run([OBJCOPY, "-O", "binary", "--only-section=.text",
             elf, OUT / f"{name}.text.bin"])
        run([OBJCOPY, "-O", "binary", "--only-section=.data",
             elf, OUT / f"{name}.data.bin"])
        elf.unlink()
        built += 1
    print(f"built {built} tests -> {OUT}")


if __name__ == "__main__":
    main()

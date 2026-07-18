# Build C programs for the TinyRV32 ASIC.
#   python build.py [hello]
# Produces <name>.bin — a self-contained flash image (text + rodata +
# .data load image); crt0 relocates .data and zeroes .bss at boot.
#
# RV32E ABI is mandatory: the core has 16 registers (NREGS=16), and only
# -march=rv32e -mabi=ilp32e stops GCC from allocating x16..x31.

import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
XPACK = Path(os.environ.get(
    "XPACK", HOME / "opt" / "xpack-riscv-none-elf-gcc-15.2.0-1" / "bin"))
SW = Path(__file__).parent


def build(name):
    elf = SW / f"{name}.elf"
    subprocess.run([str(XPACK / "riscv-none-elf-gcc.exe"),
                    "-march=rv32e", "-mabi=ilp32e", "-O2",
                    "-nostdlib", "-nostartfiles", "-static",
                    "-T", str(SW / "link.ld"),
                    str(SW / "crt0.S"), str(SW / f"{name}.c"),
                    "-lgcc", "-o", str(elf)], check=True)
    subprocess.run([str(XPACK / "riscv-none-elf-objcopy.exe"),
                    "-O", "binary", str(elf), str(SW / f"{name}.bin")],
                   check=True)
    size = (SW / f"{name}.bin").stat().st_size
    print(f"{name}.bin: {size} bytes")


if __name__ == "__main__":
    for name in (sys.argv[1:] or ["hello"]):
        build(name)

# Windows-friendly alternative to the Makefile (no `make` required):
#   python run.py
# Runs the same RTL simulation via cocotb's Python runner.

import os
from pathlib import Path

from cocotb_tools.runner import get_runner

TEST_DIR = Path(__file__).parent
SRC_DIR = TEST_DIR.parent / "src"

SOURCES = [
    SRC_DIR / "project.sv",
    SRC_DIR / "rv32_core.sv",
    SRC_DIR / "qspi_ctrl.sv",
    SRC_DIR / "control.sv",
    SRC_DIR / "immgen.sv",
    SRC_DIR / "alu.sv",
    SRC_DIR / "branch.sv",
    SRC_DIR / "regfile.sv",
    SRC_DIR / "uart_tx.sv",
    TEST_DIR / "tb.v",
]


def main():
    runner = get_runner("icarus")
    runner.build(
        sources=SOURCES,
        hdl_toplevel="tb",
        build_dir=TEST_DIR / "sim_build" / "rtl",
        build_args=["-g2012", f"-I{SRC_DIR}"],
        timescale=("1ns", "1ps"),
    )
    runner.test(
        hdl_toplevel="tb",
        test_module=os.environ.get("TEST_MODULE", "test"),
        test_dir=TEST_DIR,
    )


if __name__ == "__main__":
    main()

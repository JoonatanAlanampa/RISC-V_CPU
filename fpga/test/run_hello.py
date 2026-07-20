# run_hello.py — the full-stack cartridge-boot rehearsal, both orientations.
#   python run_hello.py
from pathlib import Path

from cocotb_tools.runner import get_runner

TEST_DIR = Path(__file__).parent
ROOT = TEST_DIR.parent.parent
SRC = ROOT / "src"

SOURCES = [
    SRC / "project.sv", SRC / "rv32_core.sv", SRC / "qspi_ctrl.sv",
    SRC / "control.sv", SRC / "immgen.sv", SRC / "alu.sv",
    SRC / "branch.sv", SRC / "regfile.sv", SRC / "uart_tx.sv",
    ROOT / "fpga" / "ulx3s_top.sv",
    TEST_DIR / "tb_hello.sv",
]


def main():
    runner = get_runner("icarus")
    runner.build(
        sources=SOURCES,
        hdl_toplevel="tb_hello",
        build_dir=TEST_DIR / "sim_build_hello",
        build_args=["-g2012"],
        timescale=("1ns", "1ps"),
    )
    for mapb in (0, 1):
        runner.test(
            hdl_toplevel="tb_hello",
            test_module="test_hello_cart",
            test_dir=TEST_DIR,
            plusargs=[f"+MAPB={mapb}"],
            results_xml=f"results_hello_map{'ba'[mapb == 0]}.xml",
        )


if __name__ == "__main__":
    main()

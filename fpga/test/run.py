# run.py — simulate the cartridge harness in both plug orientations.
#   python run.py
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
    TEST_DIR / "tb_cart.sv",
]


def main():
    runner = get_runner("icarus")
    runner.build(
        sources=SOURCES,
        hdl_toplevel="tb",
        build_dir=TEST_DIR / "sim_build",
        build_args=["-g2012"],
        timescale=("1ns", "1ps"),
    )
    for mapb in (0, 1):
        runner.test(
            hdl_toplevel="tb",
            test_module="test_cart",
            test_dir=TEST_DIR,
            plusargs=[f"+MAPB={mapb}"],
            results_xml=f"results_map{'ba'[mapb == 0]}.xml",
        )


if __name__ == "__main__":
    main()

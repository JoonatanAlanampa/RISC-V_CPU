# test_cart.py — TinyRV32 boots from the cartridge flash model, writes LED
# MMIO, round-trips a word through the PSRAM, and halts.
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer


@cocotb.test()
async def boot_from_cartridge(dut):
    cocotb.start_soon(Clock(dut.clk, 40, "ns").start())

    for _ in range(1000):            # up to 10 ms (POR alone is 2.6 ms)
        await Timer(10, "us")
        led = int(dut.led.value)
        if led & 0x80:               # LED7 = halted (ecall reached)
            break
    else:
        raise TimeoutError(f"core never halted, led=0x{int(dut.led.value):02x}")

    led = int(dut.led.value)
    assert led & 0x3F == 42, f"LED MMIO should hold 42 (PSRAM round-trip), led=0x{led:02x}"
    assert (led >> 6) & 1 == 0

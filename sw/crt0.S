# crt0.S — C runtime zero for the TinyRV32 ASIC memory map.
# Stack at the top of the 8 MB PSRAM; .data initializers are stored in
# flash (LMA) and copied to PSRAM (VMA) here, .bss zeroed — so the flash
# image alone fully boots the chip. RV32E: only x0..x15 used.
    .section .text.init
    .globl _start
_start:
    li   sp, 0x01800000        # top of 8 MB PSRAM, grows down
    la   a0, __data_load       # copy .data image: flash -> PSRAM
    la   a1, __data_start
    la   a2, __data_end
1:  bgeu a1, a2, 2f
    lw   t0, 0(a0)
    sw   t0, 0(a1)
    addi a0, a0, 4
    addi a1, a1, 4
    j    1b
2:  la   a0, __bss_start       # zero .bss — C assumes it
    la   a1, __bss_end
3:  bgeu a0, a1, 4f
    sw   zero, 0(a0)
    addi a0, a0, 4
    j    3b
4:  call main
    ecall                      # a0 = main's return value; core halts

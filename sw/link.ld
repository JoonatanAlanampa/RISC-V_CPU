/* link.ld — TinyRV32 ASIC memory map for C programs.
 * Code + rodata execute/read in place from flash (keep them under the
 * 64 KB MMIO carve-out at 0x10000). Writable data and the stack live in
 * PSRAM; .data's load image sits in flash and crt0 copies it across. */
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY
{
  FLASH (rx)  : ORIGIN = 0x00000000, LENGTH = 64K
  RAM   (rwx) : ORIGIN = 0x01000000, LENGTH = 8M
}

SECTIONS
{
  .text : { *(.text.init) *(.text*) } > FLASH
  .rodata : { *(.rodata*) *(.srodata*) } > FLASH

  .data : {
    . = ALIGN(4);
    __data_start = .;
    *(.data*) *(.sdata*)
    . = ALIGN(4);
    __data_end = .;
  } > RAM AT> FLASH
  __data_load = LOADADDR(.data);

  .bss : {
    . = ALIGN(4);
    __bss_start = .;
    *(.bss*) *(.sbss*) *(COMMON)
    . = ALIGN(4);
    __bss_end = .;
  } > RAM
}

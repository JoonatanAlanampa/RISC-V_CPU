/*
 * TinyRV32 — RV32I CPU with QSPI XIP memory, UART TX, LEDs and GPIO in.
 *
 * Pinout (TinyTapeout QSPI Pmod standard on uio):
 *   uio[0] CS0 flash (out)    uio[4] SD2 (unused in SPI mode)
 *   uio[1] SD0/MOSI (out)     uio[5] SD3 (unused in SPI mode)
 *   uio[2] SD1/MISO (in)      uio[6] CS1 PSRAM (out)
 *   uio[3] SCK (out)          uio[7] CS2 (out, held high)
 *
 *   uo[0] UART TX (115200 8N1 @ 25 MHz)   uo[1] halted (ecall)
 *   uo[7:2] LED[5:0] (MMIO 0x10000)       ui[7:0] GPIO in (MMIO 0x10008)
 *
 * Copyright (c) 2026 Joonatan Alanampa
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_joonatanalanampa_rv32 (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  wire rst = ~rst_n;

  wire        halted;
  wire [7:0]  led;
  wire        uart_txd;

  wire        if_req, if_ack;
  wire [22:0] if_addr;
  wire        d_req, d_we, d_ack;
  wire [22:0] d_addr;
  wire [31:0] d_wdata;
  wire [3:0]  d_be;

  wire        m_req, m_we, m_ack;
  wire [22:0] m_addr;
  wire [31:0] m_wdata, m_rdata;
  wire [3:0]  m_be;

  wire sck, mosi, miso, cs_flash_n, cs_ram_n;

  rv32_core #(.UART_DIV(217)) core (
      .clk(clk), .rst(rst),
      .halted(halted), .led(led), .uart_txd(uart_txd), .gpio_in(ui_in),
      .if_req(if_req), .if_addr(if_addr), .if_ack(if_ack), .if_rdata(m_rdata),
      .d_req(d_req), .d_we(d_we), .d_addr(d_addr), .d_wdata(d_wdata),
      .d_be(d_be), .d_ack(d_ack), .d_rdata(m_rdata)
  );

  mem_arbiter arb (
      .clk(clk), .rst(rst),
      .f_req(if_req), .f_addr(if_addr), .f_ack(if_ack),
      .d_req(d_req), .d_we(d_we), .d_addr(d_addr), .d_wdata(d_wdata),
      .d_be(d_be), .d_ack(d_ack),
      .m_req(m_req), .m_we(m_we), .m_addr(m_addr), .m_wdata(m_wdata),
      .m_be(m_be), .m_ack(m_ack)
  );

  qspi_ctrl qspi (
      .clk(clk), .rst(rst),
      .req(m_req), .we(m_we), .addr(m_addr), .wdata(m_wdata), .be(m_be),
      .ack(m_ack), .rdata(m_rdata),
      .sck(sck), .mosi(mosi), .miso(miso),
      .cs_flash_n(cs_flash_n), .cs_ram_n(cs_ram_n)
  );

  assign miso = uio_in[2];

  assign uio_out = {1'b1, cs_ram_n, 2'b00, sck, 1'b0, mosi, cs_flash_n};
  assign uio_oe  = 8'b1100_1011;

  assign uo_out = {led[5:0], halted, uart_txd};

  wire _unused = &{ena, uio_in[7:3], uio_in[1:0], led[7:6], 1'b0};

endmodule

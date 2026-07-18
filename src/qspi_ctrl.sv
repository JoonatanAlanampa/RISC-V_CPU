// qspi_ctrl.sv — external memory controller for the TinyTapeout QSPI Pmod
// (W25Q128 flash + APS6404 PSRAM), plus the 2:1 fetch/data arbiter.
//
// v1 talks plain 1-bit SPI mode 0 (commands 03h read / 02h write), SCK =
// clk/2. Quad mode is a later upgrade — see PLAN.md.
//
// Request port protocol (same req/ack handshake as the CPU's SDRAM bus):
// master holds req until the 1-cycle ack; rdata is valid during ack.
// Word address bit 22 selects the device: 0 = flash (CS0), 1 = PSRAM (CS1).
// Reads are always 32-bit. Writes send only the contiguous byte run marked
// by `be` (SB/SH/SW patterns). Writes to flash are acknowledged no-ops.
//
// Copyright (c) 2026 Joonatan Alanampa
// SPDX-License-Identifier: Apache-2.0

`default_nettype none

module qspi_ctrl (
    input  logic        clk,
    input  logic        rst,

    input  logic        req,
    input  logic        we,
    input  logic [22:0] addr,    // word address; bit 22: 0=flash, 1=PSRAM
    input  logic [31:0] wdata,   // bytes pre-shifted into lane position
    input  logic [3:0]  be,
    output logic        ack,
    output logic [31:0] rdata,

    output logic        sck,
    output logic        mosi,    // SD0
    input  logic        miso,    // SD1
    output logic        cs_flash_n,
    output logic        cs_ram_n
);

  localparam [2:0] S_IDLE = 3'd0, S_CMDA = 3'd1, S_WR = 3'd2, S_RD = 3'd3,
                   S_FIN  = 3'd4;

  logic [2:0]  state;
  logic [31:0] osh;      // output shift register (cmd+addr, then write data)
  logic [31:0] ish;      // input shift register
  logic [5:0]  nbits;
  logic [31:0] wsh_q;
  logic [5:0]  wbits_q;

  wire dev_ram = addr[22];
  wire [1:0] first = be[0] ? 2'd0 : be[1] ? 2'd1 : be[2] ? 2'd2 : 2'd3;
  wire [23:0] baddr = {addr[21:0], we ? first : 2'b00};

  // write bytes, packed first-sent at the top (memory order, MSB-first bits)
  logic [31:0] wpack;
  logic [5:0]  wbits;
  always_comb
    case (be)
      4'b1111: begin wpack = {wdata[7:0], wdata[15:8], wdata[23:16], wdata[31:24]}; wbits = 6'd32; end
      4'b0011: begin wpack = {wdata[7:0],  wdata[15:8],  16'h0}; wbits = 6'd16; end
      4'b1100: begin wpack = {wdata[23:16], wdata[31:24], 16'h0}; wbits = 6'd16; end
      4'b0001: begin wpack = {wdata[7:0],   24'h0}; wbits = 6'd8; end
      4'b0010: begin wpack = {wdata[15:8],  24'h0}; wbits = 6'd8; end
      4'b0100: begin wpack = {wdata[23:16], 24'h0}; wbits = 6'd8; end
      default: begin wpack = {wdata[31:24], 24'h0}; wbits = 6'd8; end
    endcase

  always_ff @(posedge clk)
    if (rst) begin
      state      <= S_IDLE;
      sck        <= 1'b0;
      mosi       <= 1'b0;
      cs_flash_n <= 1'b1;
      cs_ram_n   <= 1'b1;
      ack        <= 1'b0;
      rdata      <= 32'd0;
      osh        <= 32'd0;
      ish        <= 32'd0;
      nbits      <= 6'd0;
      wsh_q      <= 32'd0;
      wbits_q    <= 6'd0;
    end else begin
      ack <= 1'b0;

      case (state)
        S_IDLE:
          if (req && !ack) begin
            if (we && !dev_ram) begin
              ack <= 1'b1;                      // flash is read-only: no-op
            end else begin
              osh        <= {we ? 8'h02 : 8'h03, baddr};
              nbits      <= 6'd32;
              wsh_q      <= wpack;
              wbits_q    <= wbits;
              cs_flash_n <= dev_ram;
              cs_ram_n   <= ~dev_ram;
              sck        <= 1'b0;
              mosi       <= 1'b0;               // both commands start with 0
              state      <= S_CMDA;
            end
          end

        S_CMDA:
          if (!sck) sck <= 1'b1;                // rising edge: slave samples
          else begin                            // falling edge: next bit out
            sck   <= 1'b0;
            osh   <= {osh[30:0], 1'b0};
            mosi  <= osh[30];
            nbits <= nbits - 6'd1;
            if (nbits == 6'd1) begin
              if (we) begin
                osh   <= wsh_q;
                mosi  <= wsh_q[31];
                nbits <= wbits_q;
                state <= S_WR;
              end else begin
                nbits <= 6'd32;
                state <= S_RD;
              end
            end
          end

        S_WR:
          if (!sck) sck <= 1'b1;
          else begin
            sck   <= 1'b0;
            osh   <= {osh[30:0], 1'b0};
            mosi  <= osh[30];
            nbits <= nbits - 6'd1;
            if (nbits == 6'd1) state <= S_FIN;
          end

        S_RD:
          if (!sck) begin                       // rising edge: sample MISO
            sck   <= 1'b1;
            ish   <= {ish[30:0], miso};
            nbits <= nbits - 6'd1;
            if (nbits == 6'd1) state <= S_FIN;
          end else
            sck <= 1'b0;

        default: begin                          // S_FIN: deselect + ack
          sck        <= 1'b0;
          cs_flash_n <= 1'b1;
          cs_ram_n   <= 1'b1;
          // first byte received is the lowest byte in memory (little-endian)
          rdata      <= {ish[7:0], ish[15:8], ish[23:16], ish[31:24]};
          ack        <= 1'b1;
          state      <= S_IDLE;
        end
      endcase
    end

endmodule

// 2:1 arbiter: data port has priority (rarer, and the pipeline is frozen on
// it); grant is held until the controller acks.
module mem_arbiter (
    input  logic        clk,
    input  logic        rst,

    // instruction fetch port
    input  logic        f_req,
    input  logic [22:0] f_addr,
    output logic        f_ack,

    // data port
    input  logic        d_req,
    input  logic        d_we,
    input  logic [22:0] d_addr,
    input  logic [31:0] d_wdata,
    input  logic [3:0]  d_be,
    output logic        d_ack,

    // downstream (qspi_ctrl)
    output logic        m_req,
    output logic        m_we,
    output logic [22:0] m_addr,
    output logic [31:0] m_wdata,
    output logic [3:0]  m_be,
    input  logic        m_ack
);

  localparam [1:0] G_NONE = 2'd0, G_FETCH = 2'd1, G_DATA = 2'd2;

  logic [1:0] grant;

  always_ff @(posedge clk)
    if (rst)                  grant <= G_NONE;
    else if (grant == G_NONE) grant <= d_req ? G_DATA : f_req ? G_FETCH : G_NONE;
    else if (m_ack)           grant <= G_NONE;

  assign m_req   = (grant == G_DATA) ? d_req : (grant == G_FETCH) ? f_req : 1'b0;
  assign m_we    = (grant == G_DATA) ? d_we : 1'b0;
  assign m_addr  = (grant == G_DATA) ? d_addr : f_addr;
  assign m_wdata = d_wdata;
  assign m_be    = (grant == G_DATA) ? d_be : 4'b1111;
  assign f_ack   = (grant == G_FETCH) && m_ack;
  assign d_ack   = (grant == G_DATA) && m_ack;

endmodule

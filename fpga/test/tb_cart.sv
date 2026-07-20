// tb_cart.sv — ulx3s_top (TinyRV32 + cartridge pin permutation) against
// behavioral flash/PSRAM models wired like the Cartridge Pmod in J1.
// Plusarg +MAPB=1 mates the cartridge flipped AND sets SW1, mirroring the
// real procedure (set SW1 to the bring-up-reported orientation).
//
// Flash boots this RV32E program (XIP, serial SPI, cmd 03h):
//   lui x5,0x10; addi x6,x0,42; sw x6,0(x5)   ; LED <= 42
//   lui x7,0x1000; sw x6,0(x7); lw x8,0(x7)   ; PSRAM store + load
//   sw x8,0(x5); ecall                        ; LED <= loaded, halt
`timescale 1ns/1ps
`default_nettype none

module cart_flash_model (
    input  wire cs_n, sck, si,
    output wire so_oe, so_val
);
    logic [7:0]  cmd;
    logic [3:0]  bitcnt;
    logic [31:0] bytecnt;
    logic [23:0] addr;
    logic [7:0]  shin, shout;
    logic        out_en;
    logic [7:0]  mem [0:255];
    initial begin
        for (int i = 0; i < 256; i++) mem[i] = 8'hFF;
        {mem[3], mem[2], mem[1], mem[0]}   = 32'h000102B7; // lui  x5,0x10
        {mem[7], mem[6], mem[5], mem[4]}   = 32'h02A00313; // addi x6,x0,42
        {mem[11],mem[10],mem[9], mem[8]}   = 32'h0062A023; // sw x6,0(x5)
        {mem[15],mem[14],mem[13],mem[12]}  = 32'h010003B7; // lui  x7,0x1000
        {mem[19],mem[18],mem[17],mem[16]}  = 32'h0063A023; // sw x6,0(x7)
        {mem[23],mem[22],mem[21],mem[20]}  = 32'h0003A403; // lw x8,0(x7)
        {mem[27],mem[26],mem[25],mem[24]}  = 32'h0082A023; // sw x8,0(x5)
        {mem[31],mem[30],mem[29],mem[28]}  = 32'h00000073; // ecall
    end

    assign so_oe  = !cs_n && out_en;
    assign so_val = shout[7];

    always @(negedge cs_n) begin
        bitcnt = 0; bytecnt = 0; cmd = 0; out_en = 0;
    end
    always @(posedge sck) if (!cs_n) begin
        shin = {shin[6:0], si};
        bitcnt = bitcnt + 1;
        if (bitcnt == 8) begin
            bitcnt = 0; bytecnt = bytecnt + 1;
            if (bytecnt == 1) cmd = shin;
            else if (cmd == 8'h03 && bytecnt <= 4) addr = {addr[15:0], shin};
        end
    end
    always @(negedge sck) if (!cs_n) begin
        if (bitcnt == 0) begin
            if (cmd == 8'h03 && bytecnt >= 4) begin
                out_en = 1;
                shout  = mem[addr[7:0]];
                addr   = addr + 1;
            end else out_en = 0;
        end else if (out_en)
            shout = {shout[6:0], 1'b0};
    end
endmodule

module cart_psram_model (
    input  wire cs_n, sck, si,
    output wire so_oe, so_val
);
    logic [7:0]  cmd;
    logic [3:0]  bitcnt;
    logic [31:0] bytecnt;
    logic [23:0] addr;
    logic [7:0]  shin, shout;
    logic        out_en;
    logic [7:0]  mem [0:(1<<17)-1];
    realtime     cs_fall;

    assign so_oe  = !cs_n && out_en;
    assign so_val = shout[7];

    always @(negedge cs_n) begin
        bitcnt = 0; bytecnt = 0; cmd = 0; out_en = 0;
        cs_fall = $realtime;
    end
    always @(posedge cs_n)
        if ($realtime - cs_fall > 8000.0)
            $fatal(1, "psram: tCEM violated (%.0f ns)", $realtime - cs_fall);

    always @(posedge sck) if (!cs_n) begin
        shin = {shin[6:0], si};
        bitcnt = bitcnt + 1;
        if (bitcnt == 8) begin
            bitcnt = 0; bytecnt = bytecnt + 1;
            if (bytecnt == 1) cmd = shin;
            else if ((cmd == 8'h03 || cmd == 8'h02) && bytecnt <= 4)
                addr = {addr[15:0], shin};
            else if (cmd == 8'h02 && bytecnt > 4) begin
                mem[addr[16:0]] = shin;
                addr = addr + 1;
            end
        end
    end
    always @(negedge sck) if (!cs_n) begin
        if (bitcnt == 0) begin
            if (cmd == 8'h03 && bytecnt >= 4) begin
                out_en = 1;
                shout  = mem[addr[16:0]];
                addr   = addr + 1;
            end else out_en = 0;
        end else if (out_en)
            shout = {shout[6:0], 1'b0};
    end
endmodule

module tb;
    logic clk = 0;
    always #20 clk = ~clk;

    integer mapb = 0;
    initial begin
        if (!$value$plusargs("MAPB=%d", mapb)) mapb = 0;
    end

    logic [6:0] btn = 7'b0000001;
    wire  [3:0] sw = {3'b000, mapb[0]};   // SW1 = orientation, as on the bench
    wire  [7:0] led;
    wire        ftdi_rxd, wifi_gpio0;
    tri1  [3:0] gp, gn;

    ulx3s_top dut (
        .clk_25mhz(clk), .btn(btn), .sw(sw), .led(led),
        .ftdi_rxd(ftdi_rxd),
        .pmod_gp(gp), .pmod_gn(gn),
        .wifi_gpio0(wifi_gpio0)
    );

    // cartridge side: pins by Pmod number, orientation per mapb
    // (col n carries {pin 4-n | pin 10-n}; row A on gp when mapb=0)
    wire l_sck  = mapb ? gn[0] : gp[0];   // pin 4  = uio3
    wire l_mosi = mapb ? gn[2] : gp[2];   // pin 2  = uio1  (SD0)
    wire l_fcs  = mapb ? gn[3] : gp[3];   // pin 1  = uio0  (CS0)
    wire l_rcs  = mapb ? gp[1] : gn[1];   // pin 9  = uio6  (CS1)

    wire f_oe, f_val, p_oe, p_val;
    // SD1 (MISO, pin 3 = uio2) — chips drive it, tristate onto the line
    assign gp[1] = (mapb == 0 && f_oe) ? f_val :
                   (mapb == 0 && p_oe) ? p_val : 1'bz;
    assign gn[1] = (mapb != 0 && f_oe) ? f_val :
                   (mapb != 0 && p_oe) ? p_val : 1'bz;

    cart_flash_model u_flash (.cs_n(l_fcs), .sck(l_sck), .si(l_mosi),
                              .so_oe(f_oe), .so_val(f_val));
    cart_psram_model u_psram (.cs_n(l_rcs), .sck(l_sck), .si(l_mosi),
                              .so_oe(p_oe), .so_val(p_val));
endmodule

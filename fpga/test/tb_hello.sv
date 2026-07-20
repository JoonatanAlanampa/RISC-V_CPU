// tb_hello.sv — full-stack rehearsal harness: ulx3s_top (TinyRV32 +
// cartridge pin permutation) with WIRE-LEVEL hooks for the python SpiMem
// quad models. Unlike tb_cart.sv's SV models (serial-only), this exposes
// the four SD lanes so the proven test/test.py SpiMem (03/02 serial +
// 6B/EB/38 quad) can sit on the header nets exactly where the cartridge
// chips will — hello.c switches QSPI_CFG to quad mid-program.
//
// Lane wiring below is the CARTRIDGE side (fixed by the Pmod pinout);
// +MAPB=1 mates it flipped and sets SW1, as on the bench.
`timescale 1ns/1ps
`default_nettype none

module tb_hello;
    logic clk = 0;
    always #20 clk = ~clk;

    integer mapb = 0;
    initial begin
        if (!$value$plusargs("MAPB=%d", mapb)) mapb = 0;
    end

    logic [6:0] btn = 7'b0000001;
    wire  [3:0] sw = {3'b000, mapb[0]};
    wire  [7:0] led;
    wire        ftdi_rxd, wifi_gpio0;
    tri1  [3:0] gp, gn;

    ulx3s_top dut (
        .clk_25mhz(clk), .btn(btn), .sw(sw), .led(led),
        .ftdi_rxd(ftdi_rxd),
        .pmod_gp(gp), .pmod_gn(gn),
        .wifi_gpio0(wifi_gpio0)
    );

    // ---- cartridge-side view (Pmod pin -> header col/row, per mapb) ----
    // SCK=pin4, SD1=pin3, SD0=pin2, CS0=pin1 (row 1-6);
    // AUD=pin10, CS1=pin9, SD3=pin8, SD2=pin7 (row 7-12)
    wire m_sck = mapb ? gn[0] : gp[0];
    wire m_cs0 = mapb ? gn[3] : gp[3];
    wire m_cs1 = mapb ? gp[1] : gn[1];
    wire [3:0] m_sd = {
        mapb ? gp[2] : gn[2],    // SD3
        mapb ? gp[3] : gn[3],    // SD2 -- note: rowB col3; rowA col3 is CS0
        mapb ? gn[1] : gp[1],    // SD1
        mapb ? gn[2] : gp[2]     // SD0
    };

    // model drive-back (cocotb writes whole vectors; z when oe=0)
    logic [3:0] mdl_o  = '0;
    logic [3:0] mdl_oe = '0;
    assign gp[2] = (!mapb && mdl_oe[0]) ? mdl_o[0] : 1'bz;  // SD0
    assign gn[2] = ( mapb && mdl_oe[0]) ? mdl_o[0] : 1'bz;
    assign gp[1] = (!mapb && mdl_oe[1]) ? mdl_o[1] : 1'bz;  // SD1
    assign gn[1] = ( mapb && mdl_oe[1]) ? mdl_o[1] : 1'bz;
    assign gn[3] = (!mapb && mdl_oe[2]) ? mdl_o[2] : 1'bz;  // SD2
    assign gp[3] = ( mapb && mdl_oe[2]) ? mdl_o[2] : 1'bz;
    assign gn[2] = (!mapb && mdl_oe[3]) ? mdl_o[3] : 1'bz;  // SD3
    assign gp[2] = ( mapb && mdl_oe[3]) ? mdl_o[3] : 1'bz;

    initial begin
        if ($test$plusargs("VCD")) begin
            $dumpfile("hello.vcd");
            $dumpvars(1, tb_hello);      // top-level nets only: small file
        end
    end
endmodule

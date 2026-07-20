// ulx3s_top.sv — run the UNCHANGED TinyRV32 tt_um module on the ULX3S 85F
// against the Cartridge Pmod (github.com/JoonatanAlanampa/cartridge_pmod)
// plugged into the J1 header, pins 1-12 — the same position the cartridge
// bring-up bitstream uses.
//
// Pin algebra (cartridge = standard TT QSPI Pmod pinout):
//   Pmod pins 1-4 = uio[0..3], pins 7-10 = uio[4..7]
//   J1 header col n (GPn/GNn, n=0..3) mates Pmod cols {pin 4-n | pin 10-n}
//   => mapping A: gp[n] <-> uio[3-n], gn[n] <-> uio[7-n]; mapping B: rows swap.
// SW1 selects the mapping at runtime: OFF = A, ON = B — set it to whatever
// the bring-up bitstream reported (MAP A -> SW1 off, MAP B -> SW1 on).
//
// uo[0] UART -> FTDI 115200 8N1 (the ASIC's UART_DIV=217 assumes 25 MHz: ok)
// uo[1] halted -> LED7; uo[7:2] = LED[5:0]; ui = {sw[3:2], 2'b00, btn[4:1]}.
// BTN0 (PWR) = reset. TinyRV32 boots by XIP-fetching from the cartridge
// flash at 0x0 — flash a program image first (blank flash = FFh = all-ones
// instructions; the core will fetch garbage and likely just spin).
`default_nettype none

module ulx3s_top (
    input  logic       clk_25mhz,
    input  logic [6:0] btn,
    input  logic [3:0] sw,
    output logic [7:0] led,
    output logic       ftdi_rxd,
    inout  wire  [3:0] pmod_gp,
    inout  wire  [3:0] pmod_gn,
    output logic       wifi_gpio0
);
    assign wifi_gpio0 = 1'b1;

    logic [15:0] por = '0;
    always_ff @(posedge clk_25mhz) if (!(&por)) por <= por + 16'd1;
    wire rst_n = btn[0] && (&por);

    wire [7:0] uo_out, uio_out, uio_oe;
    logic [7:0] uio_in;
    wire [7:0] ui_in = {sw[3:2], 2'b00, btn[4:1]};

    tt_um_joonatanalanampa_rv32 core (
        .ui_in(ui_in), .uo_out(uo_out),
        .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe),
        .ena(1'b1), .clk(clk_25mhz), .rst_n(rst_n)
    );

    // header <-> uio permutation, orientation-selectable via SW1
    wire map_b = sw[0];
    generate for (genvar n = 0; n < 4; n++) begin : g_pins
        // mapping A: gp[n]=uio[3-n], gn[n]=uio[7-n]; B: swapped
        wire [2:0] gpi = map_b ? 3'(7 - n) : 3'(3 - n);
        wire [2:0] gni = map_b ? 3'(3 - n) : 3'(7 - n);
        assign pmod_gp[n] = uio_oe[gpi] ? uio_out[gpi] : 1'bz;
        assign pmod_gn[n] = uio_oe[gni] ? uio_out[gni] : 1'bz;
    end endgenerate

    // inverse permutation: uio[k] sits on header column 3-(k mod 4); the
    // row is gp for k<4 in mapping A, flipped by map_b
    generate for (genvar k = 0; k < 8; k++) begin : g_uin
        assign uio_in[k] = ((k < 4) ^ map_b) ? pmod_gp[3 - (k & 3)]
                                             : pmod_gn[3 - (k & 3)];
    end endgenerate

    assign ftdi_rxd = uo_out[0];
    assign led = {uo_out[1], 1'b0, uo_out[7:2]};   // LED7=halted, LED5:0=MMIO LED
endmodule

// regfile.sv — RISC-V register file, x0 hardwired to zero (read mux, not
// storage). NREGS=16 gives RV32E: half the flops and read-mux area, the
// big lever for fitting a TinyTapeout tile budget. Software contract: with
// NREGS=16, code must never touch x16..x31 (they alias x0..x15). The full
// official rv32ui suite honours this — verified by grep, enforced by the
// suite passing.
//
// SHRINK EXPERIMENT (LATCH_RF): storage cells are transparent D-latches
// instead of edge-triggered flip-flops. On sky130 hd a dlxtp latch is
// ~57% the area of an edfxtp enabled flop, so the 512-bit array (the single
// largest block on the chip, ~37% of area) shrinks by ~5-6k um^2.
//
// Correctness rests on how the CORE uses this file (rv32_core.sv:144-152):
// it is treated as PURE STORAGE with an EXTERNAL WB->ID bypass — "the write
// landing this edge isn't visible to the read". So the file only has to make
// a prior-cycle write readable next cycle; the same-cycle write/read case is
// resolved outside, by the wb_hit bypass muxes. Each register is written
// through an ACTIVE-LOW clocked latch: transparent while clk is low, so it
// captures the (WB-flop-stable) write data and closes at the rising edge —
// exactly the instant an edge-triggered file would have captured. Because a
// real clock gates every latch, STA/CTS still see a clock relationship (a
// purely data-gated latch would not). Any in-cycle read of the register being
// written is overridden by the core's bypass, so early transparency is benign.
//
// HARDEN-BRANCH ENABLE: the `shrink-latch-rf` branch hardens the LATCH variant,
// so LATCH_RF is forced on here. The ifdef is local to this file; on other
// branches the variant is selected by the build's -DLATCH_RF instead.
`define LATCH_RF
`ifdef LATCH_RF
module regfile #(
    parameter NREGS = 32
) (
    input  logic        clk,
    input  logic        we,
    input  logic [4:0]  waddr,
    input  logic [31:0] wdata,
    input  logic [4:0]  raddr1,
    input  logic [4:0]  raddr2,
    output logic [31:0] rdata1,
    output logic [31:0] rdata2
);
    localparam AW = (NREGS == 16) ? 4 : 5;

    logic [31:0] regs [NREGS];

    // Per-register active-low write window. we/waddr/wdata are WB-stage flop
    // outputs (stable for the whole cycle), so the only transition inside the
    // transparent (clk-low) phase is none — no write races. x0 gets a latch
    // too but is never read (read mux forces 0), so it is harmless.
    logic [NREGS-1:0] wsel;
    always_comb begin
        wsel = '0;
        if (we & ~clk)
            wsel[waddr[AW-1:0]] = 1'b1;
    end

    genvar i;
    generate
        for (i = 0; i < NREGS; i = i + 1) begin : rf
            // always_latch (not always @*) so the linter reads this as an
            // INTENTIONAL latch — Verilator/LibreLane RUN_LINTER treats an
            // inferred latch from a combinational block as a fatal %Error.
            always_latch if (wsel[i]) regs[i] = wdata;
        end
    endgenerate

    assign rdata1 = (raddr1 == 5'd0) ? 32'd0 : regs[raddr1[AW-1:0]];
    assign rdata2 = (raddr2 == 5'd0) ? 32'd0 : regs[raddr2[AW-1:0]];
endmodule
`else
module regfile #(
    parameter NREGS = 32
) (
    input  logic        clk,
    input  logic        we,
    input  logic [4:0]  waddr,
    input  logic [31:0] wdata,
    input  logic [4:0]  raddr1,
    input  logic [4:0]  raddr2,
    output logic [31:0] rdata1,
    output logic [31:0] rdata2
);
    localparam AW = (NREGS == 16) ? 4 : 5;

    logic [31:0] regs [NREGS];

    always_ff @(posedge clk)
        if (we)
            regs[waddr[AW-1:0]] <= wdata;

    assign rdata1 = (raddr1 == 5'd0) ? 32'd0 : regs[raddr1[AW-1:0]];
    assign rdata2 = (raddr2 == 5'd0) ? 32'd0 : regs[raddr2[AW-1:0]];
endmodule
`endif

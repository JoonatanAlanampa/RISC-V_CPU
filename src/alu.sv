// alu.sv — RV32I ALU, 10 operations.
//
// op = {funct7[5], funct3} lifted straight from the instruction — RV32I's
// version of the tiny ISA trick where opcode bits WERE the ALU select.
// instr[30] (funct7[5]) splits ADD/SUB and SRL/SRA; for I-type ALU ops that
// bit belongs to the immediate (except shifts), so alu_ctrl masks it — see
// PLAN.md "ALU-op encoding note".
//
// SHRINK EXPERIMENT (SHARED_SHIFT): the plain version writes SLL, SRL and SRA
// as three separate variable shifts (a<<b, a>>b, a>>>b), which yosys maps to
// three independent 32-bit barrel shifters. The shared version folds all three
// onto ONE right barrel shifter using the reverse-shift-reverse identity
// (a<<n == reverse(reverse(a)>>n)) with a sign-fill bit for SRA. Same results,
// ~one shifter's worth of area saved.
module alu (
    input  logic [3:0]  op,      // {funct7[5], funct3}
    input  logic [31:0] a, b,
    output logic [31:0] y
);
`ifdef SHARED_SHIFT
    // ---- single shared shifter ----
    function automatic [31:0] rev(input logic [31:0] x);
        integer k;
        for (k = 0; k < 32; k = k + 1) rev[k] = x[31 - k];
    endfunction

    wire        is_sll  = (op == 4'b0001);
    wire        is_sra  = (op == 4'b1101);
    wire [4:0]  sh      = b[4:0];
    wire [31:0] sh_in   = is_sll ? rev(a) : a;          // left shift enters reversed
    wire        fill    = is_sra ? a[31] : 1'b0;        // SRA sign-extends, else 0
    wire signed [32:0] ext = {fill, sh_in};             // 33-bit arithmetic source
    wire [31:0] sh_raw  = ext >>> sh;                    // one barrel shifter
    wire [31:0] shifted = is_sll ? rev(sh_raw) : sh_raw; // undo the reversal for SLL

    always_comb
        case (op)
            4'b0000: y = a + b;                                // ADD
            4'b1000: y = a - b;                                // SUB
            4'b0001: y = shifted;                              // SLL
            4'b0010: y = {31'd0, $signed(a) < $signed(b)};     // SLT
            4'b0011: y = {31'd0, a < b};                       // SLTU
            4'b0100: y = a ^ b;                                // XOR
            4'b0101: y = shifted;                              // SRL
            4'b1101: y = shifted;                              // SRA
            4'b0110: y = a | b;                                // OR
            4'b0111: y = a & b;                                // AND
            default: y = 32'd0;
        endcase
`else
    always_comb
        case (op)
            4'b0000: y = a + b;                                // ADD
            4'b1000: y = a - b;                                // SUB
            4'b0001: y = a << b[4:0];                          // SLL
            4'b0010: y = {31'd0, $signed(a) < $signed(b)};     // SLT
            4'b0011: y = {31'd0, a < b};                       // SLTU
            4'b0100: y = a ^ b;                                // XOR
            4'b0101: y = a >> b[4:0];                          // SRL
            4'b1101: y = $signed(a) >>> b[4:0];                // SRA
            4'b0110: y = a | b;                                // OR
            4'b0111: y = a & b;                                // AND
            default: y = 32'd0;
        endcase
`endif
endmodule

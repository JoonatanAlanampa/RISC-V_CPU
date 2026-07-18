// rv32_core.sv — ASIC build of the RV32I 5-stage pipeline (cpu_pipe.sv from
// the CPU project, vendored in core/). Same D/E/M/W stages, forwarding
// network, hazards, and MMIO; two changes for a RAM-less TinyTapeout tile:
//
//  1. Fetch: the registered-BRAM imem is replaced by a req/ack fetch FSM.
//     Fetch is non-blocking: while an instruction is in flight, D sees
//     valid_d=0 bubbles, so the back of the pipeline keeps draining. A
//     taken branch during an in-flight fetch marks it dropped (fdrop) and
//     refetches from the target.
//  2. Data: the internal dmem BRAM is gone; every non-MMIO access goes out
//     the data port (same handshake the FPGA build used for SDRAM — the
//     whole pipeline freezes on mstall until ack). Video/audio/pad MMIO
//     are stripped; LED, UART and a GPIO-in port remain.
//
// Memory map: flash 0x0000_0000+ (code+rodata), PSRAM 0x0100_0000+
// (data/stack), MMIO carve-out at 0x0001_0000: +0 LED (w), +4 UART
// (w data / r busy), +8 GPIO in (r).
//
// Copyright (c) 2026 Joonatan Alanampa
// SPDX-License-Identifier: Apache-2.0

`default_nettype none

module rv32_core #(
    parameter UART_DIV = 217,
    parameter NREGS    = 32     // 16 = RV32E-style: halves the regfile
) (
    input  logic        clk,
    input  logic        rst,
    output logic        halted,
    output logic [7:0]  led,
    output logic        uart_txd,
    input  logic [7:0]  gpio_in,
    output logic [1:0]  qspi_cfg,   // MMIO 0x1000C; resets 0 = 1-bit SPI

    // instruction fetch port (req held until 1-cycle ack); each fetch
    // returns an instruction PAIR: if_rdata = @addr, if_rdata2 = @addr+4
    output logic        if_req,
    output logic [22:0] if_addr,
    input  logic        if_ack,
    input  logic [31:0] if_rdata,
    input  logic [31:0] if_rdata2,

    // data port (same protocol)
    output logic        d_req,
    output logic        d_we,
    output logic [22:0] d_addr,
    output logic [31:0] d_wdata,
    output logic [3:0]  d_be,
    input  logic        d_ack,
    input  logic [31:0] d_rdata
);
    // ================= F: fetch FSM =================
    logic [31:0] instr_d, pc_d;          // IF/ID
    logic        valid_d;

    logic        stall, flush_ex;        // defined in D/E below
    logic        mstall;                 // data-port wait, defined in M below
    logic [31:0] target_ex;

    logic        fbusy, fdrop;
    logic [31:0] fpc;                    // address of the in-flight fetch
    logic [31:0] npc;                    // next address to fetch
    logic [31:0] fbuf;                   // skid: second word of the pair
    logic        fbuf_v;

    assign if_req  = fbusy;
    assign if_addr = fpc[24:2];

    wire advance = !halted && !mstall;
    wire consume = advance && valid_d && !stall && !flush_ex;

    always_ff @(posedge clk)
        if (rst) begin
            fbusy <= 1'b0; fdrop <= 1'b0;
            fpc   <= 32'd0; npc <= 32'd0;
            fbuf  <= 32'd0; fbuf_v <= 1'b0;
            valid_d <= 1'b0; pc_d <= 32'd0; instr_d <= 32'd0;
        end else begin
            if (consume) begin
                if (fbuf_v) begin       // promote the pair's second word
                    instr_d <= fbuf;
                    pc_d    <= pc_d + 32'd4;
                    fbuf_v  <= 1'b0;    // valid_d stays 1: no bubble
                end else
                    valid_d <= 1'b0;
            end

            if (if_ack) begin
                // a fetch is only in flight while head+skid are empty, so
                // delivery never collides with consume/promote
                fbusy <= 1'b0;
                fdrop <= 1'b0;
                if (!fdrop) begin
                    instr_d <= if_rdata;
                    pc_d    <= fpc;
                    valid_d <= 1'b1;
                    fbuf    <= if_rdata2;
                    fbuf_v  <= 1'b1;
                    npc     <= fpc + 32'd8;
                end
            end else if (!fbusy && !valid_d && !fbuf_v && !halted && !flush_ex) begin
                // !flush_ex: a redirect lands this same edge and rewrites
                // npc — starting now would fetch the stale wrong-path npc
                // with no fdrop mark (redirect sees the stale fbusy=0), and
                // its delivery would then clobber npc with wrong-path+8,
                // losing the branch target entirely.
                fbusy <= 1'b1;
                fpc   <= npc;
            end

            // redirect last: overrides a same-cycle delivery/promotion
            if (advance && flush_ex) begin
                valid_d <= 1'b0;
                fbuf_v  <= 1'b0;
                npc     <= target_ex;
                if (fbusy && !if_ack) fdrop <= 1'b1;
            end
        end

    // ================= D: decode + regfile read =================
    wire [4:0] rs1_d = instr_d[19:15];
    wire [4:0] rs2_d = instr_d[24:20];
    wire [4:0] rd_d  = instr_d[11:7];

    logic       c_reg_write, c_alu_b_src, c_mem_write, c_is_branch, c_is_jump, c_halt;
    logic [2:0] c_imm_sel;
    logic [1:0] c_alu_a_src, c_wb_src;
    logic [3:0] c_alu_op;
    control ctl (.opcode(instr_d[6:0]), .funct3(instr_d[14:12]),
                 .funct7b5(instr_d[30]),
                 .reg_write(c_reg_write), .imm_sel(c_imm_sel),
                 .alu_a_src(c_alu_a_src), .alu_b_src(c_alu_b_src),
                 .alu_op(c_alu_op), .mem_write(c_mem_write), .wb_src(c_wb_src),
                 .is_branch(c_is_branch), .is_jump(c_is_jump), .halt(c_halt));

    logic [31:0] imm_d;
    immgen ig (.instr(instr_d), .sel(c_imm_sel), .imm(imm_d));

    // regfile: written in W, read in D
    logic        reg_write_w, valid_w;
    logic [4:0]  rd_w;
    logic [31:0] wb_w, rf_r1, rf_r2;
    regfile #(.NREGS(NREGS)) rf (.clk(clk), .we(reg_write_w && valid_w && !halted),
                .waddr(rd_w), .wdata(wb_w),
                .raddr1(rs1_d), .raddr2(rs2_d), .rdata1(rf_r1), .rdata2(rf_r2));

    // WB -> ID bypass: the write landing this edge isn't visible to the read
    wire wb_hit1 = reg_write_w && valid_w && rd_w != 5'd0 && rd_w == rs1_d;
    wire wb_hit2 = reg_write_w && valid_w && rd_w != 5'd0 && rd_w == rs2_d;
    wire [31:0] r1_d = wb_hit1 ? wb_w : rf_r1;
    wire [31:0] r2_d = wb_hit2 ? wb_w : rf_r2;

    // ---- ID/EX ----
    logic        valid_e, reg_write_e, alu_b_src_e, mem_write_e;
    logic        is_branch_e, is_jump_e, halt_e;
    logic [1:0]  alu_a_src_e, wb_src_e;
    logic [3:0]  alu_op_e;
    logic [2:0]  funct3_e;
    logic [4:0]  rs1_e, rs2_e, rd_e;
    logic [31:0] pc_e, r1_e, r2_e, imm_e;

    // load-use: instruction in EX is a load whose rd the ID instruction reads
    wire is_load_e = valid_e && wb_src_e == 2'd1;
    assign stall = valid_d && is_load_e && rd_e != 5'd0
                && (rd_e == rs1_d || rd_e == rs2_d) && !flush_ex;

    always_ff @(posedge clk)
        if (rst) valid_e <= 1'b0;
        else if (!halted && !mstall) begin
            if (flush_ex || stall || !valid_d) valid_e <= 1'b0;   // bubble
            else begin
                valid_e     <= valid_d;
                reg_write_e <= c_reg_write; alu_b_src_e <= c_alu_b_src;
                mem_write_e <= c_mem_write; is_branch_e <= c_is_branch;
                is_jump_e   <= c_is_jump;   halt_e      <= c_halt;
                alu_a_src_e <= c_alu_a_src; wb_src_e    <= c_wb_src;
                alu_op_e    <= c_alu_op;    funct3_e    <= instr_d[14:12];
                rs1_e <= rs1_d; rs2_e <= rs2_d; rd_e <= rd_d;
                pc_e  <= pc_d;  r1_e  <= r1_d;  r2_e <= r2_d; imm_e <= imm_d;
            end
        end

    // ================= E: forward, execute, resolve =================
    logic        valid_m, reg_write_m, mem_write_m;
    logic [1:0]  wb_src_m;
    logic [4:0]  rd_m;
    logic [31:0] value_m;

    wire m_fwd1 = valid_m && reg_write_m && rd_m != 5'd0 && rd_m == rs1_e;
    wire m_fwd2 = valid_m && reg_write_m && rd_m != 5'd0 && rd_m == rs2_e;
    wire w_fwd1 = valid_w && reg_write_w && rd_w != 5'd0 && rd_w == rs1_e;
    wire w_fwd2 = valid_w && reg_write_w && rd_w != 5'd0 && rd_w == rs2_e;
    wire [31:0] fwd1 = m_fwd1 ? value_m : w_fwd1 ? wb_w : r1_e;
    wire [31:0] fwd2 = m_fwd2 ? value_m : w_fwd2 ? wb_w : r2_e;

    logic [31:0] alu_a, alu_y;
    always_comb
        case (alu_a_src_e)
            2'd1:    alu_a = pc_e;
            2'd2:    alu_a = 32'd0;
            default: alu_a = fwd1;
        endcase
    wire [31:0] alu_b = alu_b_src_e ? imm_e : fwd2;
    alu ex (.op(alu_op_e), .a(alu_a), .b(alu_b), .y(alu_y));

    logic br_taken;
    branch_cmp bc (.funct3(funct3_e), .a(fwd1), .b(fwd2), .taken(br_taken));

    wire take_ex  = valid_e && (is_jump_e || (is_branch_e && br_taken));
    assign flush_ex  = take_ex || (valid_e && halt_e);
    assign target_ex = (valid_e && halt_e) ? pc_e            // spin on the ecall
                                           : {alu_y[31:1], 1'b0};

    // ---- EX/MEM ----
    logic [2:0]  funct3_m;
    logic [31:0] st_m;
    logic        halt_m;
    always_ff @(posedge clk)
        if (rst) valid_m <= 1'b0;
        else if (!halted && !mstall) begin
            valid_m     <= valid_e;
            reg_write_m <= reg_write_e;
            mem_write_m <= mem_write_e;
            wb_src_m    <= wb_src_e;
            funct3_m    <= funct3_e;
            rd_m        <= rd_e;
            st_m        <= fwd2;                          // store data, forwarded
            halt_m      <= valid_e && halt_e;
            value_m     <= (wb_src_e == 2'd2) ? pc_e + 32'd4   // JAL/JALR link
                                              : alu_y;
        end

    // ================= M: memory + MMIO =================
    wire is_load_m = valid_m && wb_src_m == 2'd1;
    wire [31:0] addr_m   = value_m;
    wire [1:0]  off_m    = addr_m[1:0];
    wire [31:0] st_data  = st_m << (8 * off_m);
    logic [3:0] be_m;
    always_comb
        case (funct3_m[1:0])
            2'b00:   be_m = 4'b0001 << off_m;
            2'b01:   be_m = 4'b0011 << off_m;
            default: be_m = 4'b1111;
        endcase

    // MMIO carve-out at 0x0001_0000 (inside the flash window: keep
    // code+rodata below 64 KB — see PLAN.md); everything else is external
    wire io_m = addr_m[16] && !addr_m[24];

    // external transactions freeze the entire pipeline until ack
    wire d_active = valid_m && !io_m && (mem_write_m || is_load_m) && !halted;
    assign mstall = d_active && !d_ack;
    // drop req the moment ack arrives (see cpu_pipe.sv for the war story)
    assign d_req   = d_active && !d_ack;
    assign d_we    = mem_write_m;
    assign d_addr  = addr_m[24:2];
    assign d_wdata = st_data;
    assign d_be    = be_m;

    // I/O sub-decode: +0 LED, +4 UART, +8 GPIO in, +C QSPI_CFG
    wire io_gpio_m = addr_m[3];
    wire io_uart_m = addr_m[2];
    always_ff @(posedge clk)
        if (rst)                                              led <= 8'd0;
        else if (mem_write_m && valid_m && io_m && !io_gpio_m
                 && !io_uart_m && !halted)                    led <= st_data[7:0];

    // QSPI_CFG: bit0 = flash quad read, bit1 = PSRAM quad rd/wr.
    // Resets to 0 (plain SPI) so the chip always boots; software opts in.
    always_ff @(posedge clk)
        if (rst)                                              qspi_cfg <= 2'b00;
        else if (mem_write_m && valid_m && io_m && io_gpio_m
                 && io_uart_m && !halted)                     qspi_cfg <= st_data[1:0];

    logic uart_busy;
    uart_tx #(.DIV(UART_DIV)) u0
        (.clk(clk), .rst(rst),
         .wr(mem_write_m && valid_m && io_m && !io_gpio_m && io_uart_m
             && !halted),
         .data(st_data[7:0]), .tx(uart_txd), .busy(uart_busy));

    // loads: external word through the same byte-extension path as before
    wire [31:0] mem_word = d_rdata;
    wire [31:0] ld_shift = mem_word >> (8 * off_m);
    logic [31:0] ld_ext;
    always_comb
        case (funct3_m)
            3'b000:  ld_ext = {{24{ld_shift[7]}},  ld_shift[7:0]};
            3'b001:  ld_ext = {{16{ld_shift[15]}}, ld_shift[15:0]};
            3'b100:  ld_ext = {24'd0, ld_shift[7:0]};
            3'b101:  ld_ext = {16'd0, ld_shift[15:0]};
            default: ld_ext = mem_word;
        endcase

    // ---- MEM/WB ----
    logic halt_w;
    always_ff @(posedge clk)
        if (rst) begin
            valid_w <= 1'b0; halt_w <= 1'b0;
        end else if (!halted && !mstall) begin
            valid_w     <= valid_m;
            reg_write_w <= reg_write_m;
            rd_w        <= rd_m;
            halt_w      <= halt_m;
            wb_w        <= is_load_m
                         ? (io_m ? (io_gpio_m ? (io_uart_m ? {30'd0, qspi_cfg}
                                                           : {24'd0, gpio_in})
                                              : {31'd0, uart_busy})
                                 : ld_ext)
                         : value_m;
        end

    // ================= W: writeback + halt =================
    always_ff @(posedge clk)
        if (rst)                       halted <= 1'b0;
        else if (valid_w && halt_w)    halted <= 1'b1;
endmodule

# synth.ps1 - build the TinyRV32-on-cartridge bitstream for the ULX3S 85F.
#   powershell -File fpga\synth.ps1
# Output: fpga\build\rv32_cartridge.bit
# Flash:  openFPGALoader -b ulx3s fpga\build\rv32_cartridge.bit
$ErrorActionPreference = "Stop"
$oss = "$env:USERPROFILE\opt\oss-cad-suite"
$env:PATH = "$oss\bin;$oss\lib;" + $env:PATH
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force fpga\build | Out-Null

yosys -q -p "read_verilog -sv src/project.sv src/rv32_core.sv src/qspi_ctrl.sv src/control.sv src/immgen.sv src/alu.sv src/branch.sv src/regfile.sv src/uart_tx.sv fpga/ulx3s_top.sv; synth_ecp5 -top ulx3s_top -json fpga/build/rv32.json"
if ($LASTEXITCODE -ne 0) { throw "yosys failed" }

nextpnr-ecp5 --85k --package CABGA381 --json fpga/build/rv32.json `
    --lpf fpga/ulx3s.lpf --textcfg fpga/build/rv32.config
if ($LASTEXITCODE -ne 0) { throw "nextpnr failed" }

ecppack fpga/build/rv32.config fpga/build/rv32_cartridge.bit
if ($LASTEXITCODE -ne 0) { throw "ecppack failed" }

Write-Output "OK: fpga\build\rv32_cartridge.bit"

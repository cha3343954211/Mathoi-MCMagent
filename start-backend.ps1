# 一键启动后端（PowerShell）
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot\backend

if (-not (Test-Path .\.venv)) {
    Write-Host "[*] 创建虚拟环境..." -ForegroundColor Cyan
    python -m venv .venv
}

Write-Host "[*] 激活虚拟环境..." -ForegroundColor Cyan
. .\.venv\Scripts\Activate.ps1

if (-not (Test-Path .\.env)) {
    Write-Host "[*] 复制 .env.example -> .env，请编辑后再启动" -ForegroundColor Yellow
    Copy-Item .env.example .env
    Write-Host "    编辑 backend\.env 填入模型 API Key 后重新运行此脚本" -ForegroundColor Yellow
    exit 0
}

Write-Host "[*] 安装依赖..." -ForegroundColor Cyan
pip install -e . --quiet

Write-Host "[*] 注册 ipykernel..." -ForegroundColor Cyan
python -m ipykernel install --user --name python3 --display-name "Python 3 (mathoi)" 2>$null

Write-Host "[*] 启动后端 http://localhost:8000" -ForegroundColor Green
python -m app.main

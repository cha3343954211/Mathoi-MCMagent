$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot\frontend

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Write-Host "[*] 启用 corepack + pnpm" -ForegroundColor Cyan
    corepack enable
    corepack prepare pnpm@9 --activate
}

if (-not (Test-Path .\node_modules)) {
    Write-Host "[*] 安装依赖..." -ForegroundColor Cyan
    pnpm install
}

Write-Host "[*] 启动前端 http://localhost:5173" -ForegroundColor Green
pnpm dev

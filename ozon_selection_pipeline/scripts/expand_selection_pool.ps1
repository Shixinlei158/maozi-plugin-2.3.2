$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "开始扩充选品库..." -ForegroundColor Green

python -m ozon_pipeline.cli expand-seed-pool-network --max-depth -1 --max-sellers 0

Write-Host "选品库扩充完成！" -ForegroundColor Green

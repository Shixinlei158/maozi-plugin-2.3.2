$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$LoopCount = 0
$BatchSize = 500

Write-Host "循环扩充模式启动，每批 $BatchSize 条，自动重启防内存泄漏" -ForegroundColor Green

while ($true) {
    $LoopCount++
    Write-Host "`n=== 第 $LoopCount 轮开始 ===" -ForegroundColor Cyan
    
    python -m ozon_pipeline.cli expand-seed-pool-network --process-limit $BatchSize --max-depth -1 --max-sellers 0
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "异常退出，15秒后重试..." -ForegroundColor Red
        Start-Sleep -Seconds 15
    } else {
        Write-Host "批次完成，5秒后继续..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
}

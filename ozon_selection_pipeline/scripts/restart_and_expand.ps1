param(
    [int]$SeedSkuWorkers = 4,
    [int]$SellerSkuWorkers = 3,
    [int]$MaxRounds = 0,
    [int]$CdpWaitSeconds = 90,
    [int]$SellerBacklogSeedThreshold = 1000,
    [string]$BootstrapUrl = "https://ozon.maozierp.com/#/selection/top-list"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$RuntimeDir = Join-Path $ProjectRoot "runtime"
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$TranscriptPath = Join-Path $RuntimeDir "restart-and-expand-$RunStamp.log"
Start-Transcript -Path $TranscriptPath -Append | Out-Null

function Test-CdpReady {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:9223/json/version" -TimeoutSec 5 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Get-PipelineState {
    $script = @'
import json
from ozon_pipeline.cli import due_seed_pool_items
from ozon_pipeline import db

_, due_items = due_seed_pool_items(query_key=None, source_type="top_list", process_limit=0, retry_failed_now=True)

def scalar(sql):
    row = db.fetch_one(sql)
    return int((row or {}).get("c") or 0)

state = {
    "due_rows": len(due_items),
    "pending_rows": scalar("select count(*) c from seed_pool_skus where source_type='top_list' and last_process_status is null"),
    "failed_rows": scalar("select count(*) c from seed_pool_skus where source_type='top_list' and last_process_status='failed'"),
    "deferred_rows": scalar("select count(*) c from seed_pool_skus where source_type='top_list' and last_process_status='deferred'"),
    "rejected_rows": scalar("select count(*) c from seed_pool_skus where source_type='top_list' and last_process_status='rejected'"),
    "expanded_rows": scalar("select count(*) c from seed_pool_skus where source_type='top_list' and last_process_status='expanded'"),
    "seller_shops": scalar("select count(*) c from seller_shops"),
    "seller_due_rows": scalar("select count(*) c from seller_shops where home_url is not null and home_url <> '' and (last_collected_at is null or next_collect_after is null or next_collect_after <= current_timestamp)"),
    "seller_offers": scalar("select count(*) c from seller_offers"),
    "sku_universe": scalar("select count(*) c from sku_universe"),
}

print(json.dumps(state, ensure_ascii=False))
'@
    $raw = ($script | python - | Out-String).Trim()
    if (-not $raw) {
        throw "failed to query pipeline state"
    }
    return $raw | ConvertFrom-Json
}

try {
    Write-Host "项目目录: $ProjectRoot" -ForegroundColor Cyan
    Write-Host "日志文件: $TranscriptPath" -ForegroundColor Cyan
    Write-Host "目标并发: seed=$SeedSkuWorkers seller=$SellerSkuWorkers" -ForegroundColor Cyan
    Write-Host "调度阈值: seller_backlog >= $SellerBacklogSeedThreshold 时优先卖家页" -ForegroundColor Cyan

    if (-not (Test-CdpReady)) {
        Write-Host "9223 CDP 未就绪，准备启动带插件浏览器..." -ForegroundColor Yellow
        Start-Process python -ArgumentList "-m", "ozon_pipeline.cli", "launch-real-chrome", "--url", $BootstrapUrl -WorkingDirectory $ProjectRoot
        $ready = $false
        for ($i = 0; $i -lt $CdpWaitSeconds; $i++) {
            Start-Sleep -Seconds 1
            if (Test-CdpReady) {
                $ready = $true
                break
            }
        }
        if (-not $ready) {
            throw "CDP 浏览器在 $CdpWaitSeconds 秒内没有启动成功，请检查浏览器窗口是否被拦截或手动关闭。"
        }
    }
    else {
        Write-Host "复用现有 9223 CDP 浏览器。" -ForegroundColor Green
    }

    $round = 1
    $stallRounds = 0
    $state = Get-PipelineState

    while ($true) {
        if ($state.due_rows -le 0 -and $state.seller_due_rows -le 0) {
            Write-Host "没有待处理的种子行，也没有待处理的卖家 backlog，循环结束。" -ForegroundColor Green
            break
        }
        if ($MaxRounds -gt 0 -and $round -gt $MaxRounds) {
            Write-Host "达到最大轮次 $MaxRounds，停止自动循环。" -ForegroundColor Yellow
            break
        }

        Write-Host ""
        Write-Host ("=" * 80) -ForegroundColor DarkGray
        $runMode = "seller_backlog"
        if ($state.seller_due_rows -lt $SellerBacklogSeedThreshold -and $state.due_rows -gt 0) {
            $runMode = "seed_pool"
        }

        Write-Host ("第 {0} 轮开始 | mode={1} due={2} pending={3} failed={4} deferred={5} rejected={6} expanded={7} seller_due={8} sellers={9} offers={10} universe={11}" -f `
            $round, $runMode, $state.due_rows, $state.pending_rows, $state.failed_rows, $state.deferred_rows, $state.rejected_rows, $state.expanded_rows, $state.seller_due_rows, $state.seller_shops, $state.seller_offers, $state.sku_universe) -ForegroundColor Green

        if ($runMode -eq "seed_pool") {
            python -m ozon_pipeline.cli --verbose expand-seed-pool-network --max-depth -1 --max-sellers 0 --retry-failed-now --seed-sku-workers $SeedSkuWorkers --seller-sku-workers $SellerSkuWorkers
        }
        else {
            python -m ozon_pipeline.cli --verbose expand-seller-backlog --process-limit 100 --max-depth -1 --max-sellers 0 --seller-sku-workers $SellerSkuWorkers
        }
        if ($LASTEXITCODE -ne 0) {
            throw "$runMode 异常退出，退出码: $LASTEXITCODE"
        }

        $nextState = Get-PipelineState
        $madeProgress = (
            ($nextState.due_rows -lt $state.due_rows) -or
            ($nextState.seller_due_rows -lt $state.seller_due_rows) -or
            ($nextState.seller_shops -gt $state.seller_shops) -or
            ($nextState.seller_offers -gt $state.seller_offers) -or
            ($nextState.expanded_rows -gt $state.expanded_rows) -or
            ($nextState.sku_universe -gt $state.sku_universe)
        )

        Write-Host ("第 {0} 轮结束 | mode={1} due={2} pending={3} failed={4} deferred={5} rejected={6} expanded={7} seller_due={8} sellers={9} offers={10} universe={11}" -f `
            $round, $runMode, $nextState.due_rows, $nextState.pending_rows, $nextState.failed_rows, $nextState.deferred_rows, $nextState.rejected_rows, $nextState.expanded_rows, $nextState.seller_due_rows, $nextState.seller_shops, $nextState.seller_offers, $nextState.sku_universe) -ForegroundColor Cyan

        if ($nextState.due_rows -le 0 -and $nextState.seller_due_rows -le 0) {
            Write-Host "种子池和卖家 backlog 都已跑空，停止循环。" -ForegroundColor Green
            break
        }

        if (-not $madeProgress) {
            $stallRounds += 1
            Write-Warning "本轮没有观测到新增卖家、新跟卖、Universe 增量或 due 缩减。"
            if ($stallRounds -ge 2) {
                Write-Warning "连续两轮无进展，自动停止，避免空转。"
                break
            }
        }
        else {
            $stallRounds = 0
        }

        $state = $nextState
        $round += 1
    }

    Write-Host "运行完成。日志保存在: $TranscriptPath" -ForegroundColor Green
}
finally {
    try {
        Stop-Transcript | Out-Null
    }
    catch {
    }
}

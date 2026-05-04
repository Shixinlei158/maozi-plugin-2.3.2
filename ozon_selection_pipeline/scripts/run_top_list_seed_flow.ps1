$ErrorActionPreference = "Stop"

param(
    [int]$PageFrom = 1,
    [int]$PageTo = 100,
    [int]$PageSize = 50,
    [int]$ProcessLimit = 0,
    [int]$MaxDepth = 1,
    [int]$MaxSellers = 20,
    [int]$SkuLimit = 0,
    [int]$RefreshHours = 24,
    [switch]$ForceRefresh,
    [switch]$SkipProcess
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$CreateDateTo = Get-Date -Format "yyyy-MM-dd"
$Arguments = @(
    "-u",
    "-m", "ozon_pipeline.cli",
    "crawl-top-list-network",
    "--main-type", "hot",
    "--sales-min", "1",
    "--sales-max", "65",
    "--avg-price-min", "200",
    "--avg-price-max", "10000",
    "--sales-schema", "FBS",
    "--create-date-from", "2025-04-01",
    "--create-date-to", $CreateDateTo,
    "--sort-by", "sold_sum",
    "--sort-order", "desc",
    "--page-from", $PageFrom,
    "--page-to", $PageTo,
    "--page-size", $PageSize,
    "--process-limit", $ProcessLimit,
    "--max-depth", $MaxDepth,
    "--max-sellers", $MaxSellers,
    "--sku-limit", $SkuLimit,
    "--refresh-hours", $RefreshHours
)

if ($ForceRefresh) {
    $Arguments += "--force-refresh"
}

if ($SkipProcess) {
    $Arguments += "--skip-process"
}

python @Arguments

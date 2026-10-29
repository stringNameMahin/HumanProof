# Build the automated side of the dataset with every bot profile, then optionally train and rescore.
# Start the demo first (scripts\start_demo.ps1). Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\seed_demo.ps1 [-Runs 4] [-Train]
param(
    [int]$Runs = 4,
    [switch]$Train
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
Push-Location $root
try {
    $signInRuns = [math]::Max(1, [int]($Runs / 2))
    Write-Output "Raw DevTools Protocol profiles on the interaction lab ($Runs runs each)..."
    node scripts/bot_fleet.mjs --runs $Runs --transport mixed
    Write-Output "Raw DevTools Protocol profiles on the protected sign-in ($signInRuns runs each)..."
    node scripts/bot_fleet.mjs --runs $signInRuns --transport mixed --page sign-in
    Write-Output "Selenium, Playwright, and API-level profiles ($Runs runs each)..."
    & $python scripts/bot_fleet.py --runs $Runs --transport mixed
    if ($Train) {
        & $python backend\manage.py train_model
        if ($LASTEXITCODE -ne 0) { throw 'Training needs more labelled human sessions; see the message above.' }
        & $python backend\manage.py rescore
    }
} finally {
    Pop-Location
}

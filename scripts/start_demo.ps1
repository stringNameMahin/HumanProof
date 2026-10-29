# Start the HumanProof demo: Django API, TLS edge, and Streamlit dashboard, each in its own window.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 [-NoEdge] [-NoDashboard]
param(
    [switch]$NoEdge,
    [switch]$NoDashboard
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'Create the environment first: python -m venv .venv; .venv\Scripts\python.exe -m pip install -r requirements.txt'
}
Push-Location $root
try {
    & $python backend\manage.py migrate --noinput | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
    & $python backend\manage.py init_demo
    # Raised limits so the bot fleet can create many local sessions. These are local-demo values.
    $env:HUMANPROOF_SESSION_RATE = '2000/hour'
    $env:HUMANPROOF_INGEST_RATE = '2000/min'
    Start-Process -FilePath $python -ArgumentList 'backend\manage.py', 'runserver', '127.0.0.1:8000' `
        -WorkingDirectory $root -WindowStyle Minimized
    if (-not $NoEdge) {
        Start-Process -FilePath $python -ArgumentList 'edge\tls_edge.py' -WorkingDirectory $root -WindowStyle Minimized
    }
    if (-not $NoDashboard) {
        Start-Process -FilePath $python -ArgumentList '-m', 'streamlit', 'run', 'dashboard\app.py', '--server.address',
            '127.0.0.1', '--server.port', '8501', '--server.headless', 'true', '--browser.gatherUsageStats', 'false' `
            -WorkingDirectory $root -WindowStyle Minimized
    }
    $deadline = (Get-Date).AddSeconds(30)
    $ready = $false
    while (-not $ready -and (Get-Date) -lt $deadline) {
        try {
            $ready = (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health/' -TimeoutSec 2).StatusCode -eq 200
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) { throw 'The API did not start. Check the minimized Django window for errors.' }
    Write-Output ''
    Write-Output 'HumanProof is running:'
    Write-Output '  Interaction lab      http://127.0.0.1:8000/'
    Write-Output '  Protected sign-in    http://127.0.0.1:8000/login/'
    if (-not $NoEdge) { Write-Output '  Through the TLS edge https://127.0.0.1:8443/  (self-signed: accept the warning once)' }
    if (-not $NoDashboard) { Write-Output '  Analyst dashboard    http://127.0.0.1:8501/' }
    Write-Output ''
    Write-Output 'Stop everything with: powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1'
} finally {
    Pop-Location
}

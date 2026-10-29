$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
Push-Location $projectRoot
try {
    & $pythonPath backend/manage.py check
    if ($LASTEXITCODE -ne 0) { throw 'Django system checks failed.' }
    & $pythonPath backend/manage.py makemigrations --check --dry-run
    if ($LASTEXITCODE -ne 0) { throw 'Models and migrations differ.' }
    & $pythonPath backend/manage.py test tracker --verbosity 1
    if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
    & $pythonPath -m unittest ml.tests
    if ($LASTEXITCODE -ne 0) { throw 'Feature and model tests failed.' }
    & $pythonPath -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard, script, and edge tests failed.' }
    node --test frontend/tests/*.test.js
    if ($LASTEXITCODE -ne 0) { throw 'Collector tests failed.' }
    foreach ($script in @('scripts/bot_fleet.mjs', 'scripts/browser_demo.mjs', 'scripts/screenshots.mjs', 'scripts/lib/cdp.mjs')) {
        node --check $script
        if ($LASTEXITCODE -ne 0) { throw "Syntax check failed for $script." }
    }
    Write-Output 'HumanProof checks passed.'
} finally {
    Pop-Location
}

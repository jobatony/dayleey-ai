# Launches Dayleey AI locally in Baileys prototype mode (3 terminals: Web, django-q2 Worker, Node Bridge).
# Usage: .\scripts\start_baileys_local.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Host "Missing .env file. Copy .env.example to .env and configure WA_TRANSPORT=baileys + BAILEYS_ALLOWLIST first." -ForegroundColor Red
    exit 1
}

Write-Host "Ensuring migrations & django-q2 schedules are up to date..." -ForegroundColor Cyan
& (Join-Path $Root ".venv\Scripts\python.exe") (Join-Path $Root "manage.py") migrate
& (Join-Path $Root ".venv\Scripts\python.exe") (Join-Path $Root "manage.py") setup_schedules

Write-Host "Starting Django Web (127.0.0.1:8000), django-q2 Worker, and Baileys Node Bridge (127.0.0.1:3001)..." -ForegroundColor Green

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Root'; `$env:DJANGO_Q_SYNC='False'; .\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000"
)

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Root'; `$env:DJANGO_Q_SYNC='False'; .\.venv\Scripts\python.exe manage.py run_tick --catchup; .\.venv\Scripts\python.exe manage.py qcluster"
)

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$Root\bridge'; node index.js"
)

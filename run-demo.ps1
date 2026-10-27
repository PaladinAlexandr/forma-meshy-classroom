param([int]$Port = 8010)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
$studioPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $studioPython -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$env:DJANGO_DEBUG = '1'
$env:MESHY_MODE = 'demo'
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
& $studioPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Migration failed.' }
& $studioPython manage.py seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Demo seed failed.' }
$studioQueue = Start-Process -FilePath $studioPython -ArgumentList 'manage.py','runqueue' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
Write-Host "Open http://127.0.0.1:$Port and choose a demo role. Ctrl+C stops the server."
try {
    & $studioPython manage.py runserver "127.0.0.1:$Port" --noreload
} finally {
    if (-not $studioQueue.HasExited) { Stop-Process -Id $studioQueue.Id }
}

param(
    [ValidateSet('hybrid', 'demo', 'live')]
    [string]$Mode = 'hybrid',
    [int]$Port = 5001
)
$ErrorActionPreference = 'Stop'
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Error 'Create .venv and install requirements-dashboard.txt first. See README.md.'
}
Set-Location -LiteralPath $PSScriptRoot
$env:FACTORY_MODE = $Mode
$env:FACTORY_WEB_PORT = "$Port"
$env:FACTORY_WEB_HOST = '127.0.0.1'
Write-Host "Factory dashboard: http://127.0.0.1:$Port"
Write-Host "Mode: $Mode. Press Ctrl+C to stop."
& $pythonExe (Join-Path $PSScriptRoot 'dashboard.py')

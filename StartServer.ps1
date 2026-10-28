param([switch]$Background)
$ErrorActionPreference = 'Stop'
$reviewRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$reviewConfigPath = Join-Path $reviewRoot 'ServerConfig.json'
if (-not (Test-Path -LiteralPath $reviewConfigPath)) {
    if ($Background) { throw 'Run ConfigureServer.ps1 before enabling startup.' }
    & (Join-Path $reviewRoot 'ConfigureServer.ps1')
}
$reviewPython = Join-Path $reviewRoot 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $reviewPython)) { $reviewPython = Join-Path $reviewRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $reviewPython)) { throw 'Extract the portable release or create .venv and install requirements.txt. See README.md.' }
$reviewArgs = @((Join-Path $reviewRoot 'app\app.py'), '--server', '--data', (Join-Path $reviewRoot 'ServerData'), '--server-config', $reviewConfigPath)
if (-not $Background) { $reviewArgs += '--open' }
& $reviewPython @reviewArgs
if ($LASTEXITCODE -ne 0) { throw ('Server exited with code ' + $LASTEXITCODE) }

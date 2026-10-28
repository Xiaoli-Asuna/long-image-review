$ErrorActionPreference = 'Stop'
$reviewRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$reviewPython = Join-Path $reviewRoot 'runtime\pythonw.exe'
$reviewScript = Join-Path $reviewRoot 'app\app.py'
if (-not (Test-Path -LiteralPath $reviewPython)) { $reviewPython = Join-Path $reviewRoot '.venv\Scripts\pythonw.exe' }
if (-not (Test-Path -LiteralPath $reviewPython)) { throw 'Extract the portable release or create .venv and install requirements.txt. See README.md.' }
if (-not (Test-Path -LiteralPath $reviewScript)) { throw 'Application files are missing. Extract the complete ZIP first.' }
Start-Process -FilePath $reviewPython -ArgumentList @(('"' + $reviewScript + '"'), '--open') -WorkingDirectory $reviewRoot -WindowStyle Hidden | Out-Null

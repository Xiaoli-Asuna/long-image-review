@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -Command "$reviewUser = Read-Host 'Account name to reset'; if ($reviewUser -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,39}$') { throw 'Invalid account name' }; $reviewPython = '.\runtime\python.exe'; if (-not (Test-Path -LiteralPath $reviewPython)) { $reviewPython = '.\.venv\Scripts\python.exe' }; & $reviewPython '.\app\app.py' --server --data '.\ServerData' --server-config '.\ServerConfig.json' --reset-user $reviewUser"
pause

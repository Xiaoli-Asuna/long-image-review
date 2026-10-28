$ErrorActionPreference = 'Stop'
$reviewRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path -LiteralPath (Join-Path $reviewRoot 'ServerConfig.json'))) { throw 'Configure the server first.' }
$reviewUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$reviewHash = [System.Security.Cryptography.SHA256]::Create()
$reviewSuffix = ([BitConverter]::ToString($reviewHash.ComputeHash([Text.Encoding]::UTF8.GetBytes($reviewRoot)))).Replace('-', '').Substring(0, 10)
$reviewName = 'ReviewOffice-' + $reviewSuffix
$reviewAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $reviewRoot 'StartServer.ps1') + '" -Background') -WorkingDirectory $reviewRoot
$reviewTrigger = New-ScheduledTaskTrigger -AtLogOn -User $reviewUser
$reviewPrincipal = New-ScheduledTaskPrincipal -UserId $reviewUser -LogonType Interactive -RunLevel Limited
$reviewSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName $reviewName -Action $reviewAction -Trigger $reviewTrigger -Principal $reviewPrincipal -Settings $reviewSettings -Description 'Office review app. Starts after this Windows user logs in; API key is protected for this user.' -Force | Out-Null
Write-Host ('Installed logon task: ' + $reviewName)
Write-Host 'This starts after Windows login, not before login. Disconnect Remote Desktop instead of signing out.'

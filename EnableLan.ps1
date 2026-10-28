$ErrorActionPreference = 'Stop'
$reviewRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName PresentationFramework
try {
    $reviewPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $reviewPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $reviewElevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $MyInvocation.MyCommand.Path + '"'))
        exit $reviewElevated.ExitCode
    }
    $reviewConfig = Get-Content -Raw -LiteralPath (Join-Path $reviewRoot 'ServerConfig.json') | ConvertFrom-Json
    $reviewPort = [int]$reviewConfig.port
    if ($reviewPort -lt 1024 -or $reviewPort -gt 65535) { throw 'Invalid server port.' }
    $reviewPythonPath = Join-Path $reviewRoot 'runtime\python.exe'
    if (-not (Test-Path -LiteralPath $reviewPythonPath)) { $reviewPythonPath = Join-Path $reviewRoot '.venv\Scripts\python.exe' }
    $reviewPython = (Resolve-Path -LiteralPath $reviewPythonPath).Path
    $reviewHasher = [System.Security.Cryptography.SHA256]::Create()
    $reviewSuffix = ([BitConverter]::ToString($reviewHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($reviewRoot)))).Replace('-', '').Substring(0, 10)
    $reviewRule = 'ReviewOffice-' + $reviewSuffix + '-' + $reviewPort
    if (-not (Get-NetFirewallRule -Name $reviewRule -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name $reviewRule -DisplayName ('Review Office ' + $reviewPort) -Direction Inbound -Action Allow -Protocol TCP -LocalPort $reviewPort -Program $reviewPython -Profile Private,Domain -RemoteAddress LocalSubnet | Out-Null
    }
    [System.Windows.MessageBox]::Show('Office access enabled for Private / Domain networks and LocalSubnet only. No public network rule was added.', 'Review Office') | Out-Null
} catch {
    [System.Windows.MessageBox]::Show($_.Exception.Message, 'Review Office - setup failed') | Out-Null
    exit 1
}

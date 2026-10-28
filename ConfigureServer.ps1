$ErrorActionPreference = 'Stop'
$reviewRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$reviewAddresses = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' -and $_.AddressState -eq 'Preferred' })
if ($reviewAddresses.Count -eq 0) { throw 'No office IPv4 found. Connect to the office network first.' }
Write-Host 'Select the office network adapter (not VPN or virtual adapters):'
for ($reviewIndex = 0; $reviewIndex -lt $reviewAddresses.Count; $reviewIndex++) {
    Write-Host (('{0}. {1}  {2}' -f ($reviewIndex + 1), $reviewAddresses[$reviewIndex].IPAddress, $reviewAddresses[$reviewIndex].InterfaceAlias))
}
$reviewChoice = Read-Host 'Adapter number'
$reviewNumber = 0
if (-not [int]::TryParse($reviewChoice, [ref]$reviewNumber) -or $reviewNumber -lt 1 -or $reviewNumber -gt $reviewAddresses.Count) { throw 'Invalid adapter number.' }
$reviewAddress = $reviewAddresses[$reviewNumber - 1].IPAddress
$reviewConfigPath = Join-Path $reviewRoot 'ServerConfig.json'
if (Test-Path -LiteralPath $reviewConfigPath) {
    $reviewOld = Get-Content -Raw -LiteralPath $reviewConfigPath | ConvertFrom-Json
    if ($reviewOld.tls_cert -or $reviewOld.tls_key) { throw 'HTTPS is configured. Edit ServerConfig.json manually to preserve certificate settings.' }
    Copy-Item -LiteralPath $reviewConfigPath -Destination ($reviewConfigPath + '.' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.bak')
}
$reviewConfig = @{ bind_host = '0.0.0.0'; port = 8765; allowed_hosts = @(($reviewAddress + ':8765'), '127.0.0.1:8765', 'localhost:8765') }
$reviewConfig | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reviewConfigPath -Encoding UTF8
Write-Host ('Office URL: http://' + $reviewAddress + ':8765/')
Write-Host 'Keep this IP stable using a router DHCP reservation. No firewall changes have been made.'

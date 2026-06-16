# Run once as Administrator. Sets up venv, builds frontend, adds firewall rules.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot         # dashboard/

foreach ($exe in 'python', 'node', 'npm') {
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: '$exe' not found in PATH. Install it and re-run."
    exit 1
  }
}

python -m venv "$root\.venv"
& "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$root\.venv\Scripts\python.exe" -m pip install -r "$root\backend\requirements.txt"

Push-Location "$root\frontend"
npm ci
npm run build
Pop-Location

function Ensure-Rule($name, $proto, $port) {
  if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol $proto -LocalPort $port -Action Allow | Out-Null
    Write-Host "added firewall rule: $name"
  } else {
    Write-Host "firewall rule exists: $name"
  }
}
Ensure-Rule 'XRFD diag (UDP 50999)' UDP 50999
Ensure-Rule 'XRFD dashboard (TCP 10000)' TCP 10000

Write-Host "Setup complete. Start with: setup\run.ps1"

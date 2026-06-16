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

& "$PSScriptRoot\firewall.ps1"

Write-Host "Setup complete. Start with: setup\run.ps1"

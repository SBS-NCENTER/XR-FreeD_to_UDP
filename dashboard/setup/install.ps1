# Run once as Administrator. Installs backend deps via uv, builds frontend, adds firewall rules.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot         # dashboard/

# uv manages Python 3.14 itself (.python-version). Install it with:
#   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
#   (or: winget install astral-sh.uv)
foreach ($exe in 'uv', 'node', 'npm') {
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: '$exe' not found in PATH. Install it and re-run."
    exit 1
  }
}

Push-Location $root
uv sync
Pop-Location

Push-Location "$root\frontend"
npm ci
npm run build
Pop-Location

& "$PSScriptRoot\firewall.ps1"

Write-Host "Setup complete. Start with: setup\run.ps1"

# Adds the inbound firewall rules the dashboard needs (idempotent). Run as Administrator.
#
# WHY: the diag broadcast (UDP 50999) and other PCs' browser access (TCP 10000) are
# unsolicited INBOUND traffic. On a Public network profile Windows blocks inbound by
# default, per receiving program. A PORT-based allow rule is program-agnostic, so it
# works for any backend (python/waitress, the old PowerShell, etc.) and does not need
# to be recreated when the backend changes.
$ErrorActionPreference = 'Stop'

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
Write-Host "Firewall rules ensured."

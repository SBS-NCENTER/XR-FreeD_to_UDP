#!/usr/bin/env bash
# dashboard/setup/firewall.sh
# Inbound rules the dashboard needs (idempotent). Linux port of firewall.ps1.
# Acts ONLY if ufw is installed AND active; on a default server with no firewall,
# inbound is already open and this is a harmless no-op. Port-based = program-agnostic.
#   UDP 50999 = diag broadcast (device -> PCs)  ;  TCP 10000 = dashboard browser access
set -euo pipefail

if ! command -v ufw >/dev/null 2>&1; then
  echo "ufw not installed — skipping (inbound likely already open)."
  exit 0
fi
if ! ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "ufw inactive — skipping (inbound already open)."
  exit 0
fi
sudo ufw allow 50999/udp comment 'XRFD diag'
sudo ufw allow 10000/tcp comment 'XRFD dashboard'
echo "Firewall rules ensured (ufw)."

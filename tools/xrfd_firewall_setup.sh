#!/usr/bin/env bash
# XRFD firewall helper (macOS / Linux) - allows inbound UDP 50999 (diag
# broadcast) and TCP 10000 (dashboard access from other machines).
#
# macOS: the Application Firewall is app-based, not port-based, and is OFF
#   by default. If it is ON, just click "Allow" on the popup the first time
#   python3 listens - nothing else to do. (This script only prints guidance.)
# Linux: applies rules if ufw or firewalld is active; otherwise most distros
#   have no host firewall and nothing is needed.
set -u

case "$(uname -s)" in
Darwin)
  fw_state=$(/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null)
  echo "macOS Application Firewall: ${fw_state:-unknown}"
  echo " - If disabled: nothing to do."
  echo " - If enabled: run the dashboard once and click 'Allow' on the popup,"
  echo "   or pre-allow python3 (admin):"
  echo "     sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add \"\$(command -v python3)\""
  echo "     sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp \"\$(command -v python3)\""
  ;;
Linux)
  if command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
    sudo ufw allow 50999/udp comment "XRFD diag"
    sudo ufw allow 10000/tcp comment "XRFD dashboard"
    echo "ufw rules added (UDP 50999, TCP 10000)."
  elif command -v firewall-cmd >/dev/null 2>&1 && sudo firewall-cmd --state >/dev/null 2>&1; then
    sudo firewall-cmd --permanent --add-port=50999/udp
    sudo firewall-cmd --permanent --add-port=10000/tcp
    sudo firewall-cmd --reload
    echo "firewalld rules added (UDP 50999, TCP 10000)."
  else
    echo "No active host firewall detected (ufw/firewalld) - nothing to do."
  fi
  ;;
*)
  echo "Unsupported OS: $(uname -s) - configure UDP 50999 inbound + TCP 10000 inbound manually."
  ;;
esac

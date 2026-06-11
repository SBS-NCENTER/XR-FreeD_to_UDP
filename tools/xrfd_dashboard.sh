#!/usr/bin/env bash
# XRFD Dashboard launcher (macOS / Linux).
# Usage: ./xrfd_dashboard.sh [--port 10000]
# macOS: if the Application Firewall is ON, allow python3 on the first-run
#        popup. Linux: see xrfd_firewall_setup.sh for ufw/firewalld rules.
cd "$(dirname "$0")" || exit 1
exec python3 ./xrfd_dashboard.py "$@"

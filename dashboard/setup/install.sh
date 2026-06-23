#!/usr/bin/env bash
# dashboard/setup/install.sh
# Run once. Sets up venv, installs deps, builds frontend, (optionally) adds firewall rules.
# Linux port of install.ps1.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"                            # -> dashboard/

for exe in python3 node npm; do
  command -v "$exe" >/dev/null || { echo "ERROR: '$exe' not found in PATH. Install it and re-run."; exit 1; }
done

python3 -m venv "$root/.venv"
"$root/.venv/bin/python" -m pip install --upgrade pip
"$root/.venv/bin/python" -m pip install -r "$root/backend/requirements.txt"

( cd "$root/frontend" && npm ci && npm run build )

# Firewall: optional, no-op unless ufw is active (see firewall.sh).
"$here/firewall.sh" || true

echo "Setup complete. Start with: dashboard/setup/run.sh  (or: systemctl enable --now xr-freed-to-udp)"

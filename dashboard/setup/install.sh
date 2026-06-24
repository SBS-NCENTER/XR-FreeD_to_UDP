#!/usr/bin/env bash
# dashboard/setup/install.sh
# Run once. Installs backend deps via uv (which also provisions Python 3.14 per
# .python-version), builds the Svelte frontend, (optionally) adds firewall rules.
# Linux port of install.ps1.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"                            # -> dashboard/

command -v uv >/dev/null || {
  echo "ERROR: 'uv' not found in PATH. Install it first:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
}
for exe in node npm; do
  command -v "$exe" >/dev/null || { echo "ERROR: '$exe' not found in PATH (needed for the frontend build). Install it and re-run."; exit 1; }
done

# Backend: uv sync provisions Python 3.14 (.python-version), installs deps, and
# editable-installs 'backend' so 'python -m backend.app' resolves.
( cd "$root" && uv sync )

# Frontend: built once; the runtime needs only Python.
( cd "$root/frontend" && npm ci && npm run build )

# Firewall: optional, no-op unless ufw is active (see firewall.sh).
"$here/firewall.sh" || true

echo "Setup complete. Start with: dashboard/setup/run.sh  (or: systemctl enable --now xr-freed-to-udp)"

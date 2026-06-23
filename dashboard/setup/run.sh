#!/usr/bin/env bash
# dashboard/setup/run.sh
# Starts the dashboard service (waitress). Invoked by server-manager via server.toml,
# by the systemd unit, or directly. Linux port of run.ps1 — SSOT for launching the backend.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> dashboard/
cd "$root"                                                 # so 'backend' is importable by -m
# -u (unbuffered) so startup URLs + logs stream live into the manager's log view
exec "$root/.venv/bin/python" -u -m backend.app

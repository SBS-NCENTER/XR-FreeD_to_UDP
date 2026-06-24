# XRFD Dashboard (Python + Svelte)

Central web dashboard for the XR-FreeD device. A Python (Flask + waitress) backend
runs a UDP bridge thread that receives the device's diag broadcast (UDP 50999) and
relays control commands (UDP 50998); it serves a Svelte/Vite frontend with live
updates over SSE.

Runs as one always-on service on a PC that is **on the device's LAN segment**
(broadcasts do not cross routers/subnets). Other PCs/tablets browse to it over the LAN.

Primary target is **Linux** (`server-manager` supervises it via `server.toml`); the
**Windows** scripts are kept as a fallback.

## Install (once)

### Linux (primary)
```
cd dashboard
bash setup/install.sh
```
Installs backend deps via `uv` (which also provisions Python 3.14 per `.python-version`),
builds the frontend, and — only if `ufw` is active — adds inbound rules for UDP 50999 / TCP 10000.

Prerequisites:
- `uv` — `curl -LsSf https://astral.sh/uv/install.sh | sh` (manages Python 3.14 itself)
- Node.js — **build time only** (e.g. via fnm/nvm); the runtime needs only Python

### Windows (fallback, as Administrator)
```
cd dashboard
powershell -ExecutionPolicy Bypass -File setup\install.ps1
```
Administrator is required for the firewall rules — without them the diag broadcast is
blocked on the Public profile and the dashboard shows no device. (On Linux a default
server has no firewall, so the rule is usually unnecessary — `firewall.sh` is a no-op then.)

Prerequisites: `uv` and Node.js on PATH (uv manages Python 3.14 itself):
- `uv` — `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` (or `winget install astral-sh.uv`)

## Run

### Linux
```
bash dashboard/setup/run.sh
```
Or as a managed service (autostart + restart-on-failure):
```
# edit WorkingDirectory in setup/xr-freed-to-udp.service to the deploy path, then:
sudo cp setup/xr-freed-to-udp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now xr-freed-to-udp
```

### Windows (fallback)
```
cd dashboard
powershell -ExecutionPolicy Bypass -File setup\run.ps1
```

Then open `http://localhost:10000` (this PC) or `http://<this-pc-ip>:10000` (other PCs).
The service conforms to the workspace `server.toml` contract, so `server-manager` can
supervise start/stop/restart and boot autostart.

## Develop

```
# backend tests
cd dashboard && uv run pytest backend/tests -v
# frontend dev server (proxies /api and /events to the running backend on :10000)
cd dashboard/frontend && npm run dev
```

## Endpoints

- `GET /` — Svelte dashboard
- `GET /api/status` — current state snapshot (JSON)
- `GET /events` — SSE stream of state snapshots
- `POST /api/cmd` `{ "cmd": "target 1 on" }` — relay a control command, returns the reply
- `GET /health` — `{status, pid, uptime_seconds, device}` (server.toml contract)

## Device control (CLI)

From any PC on the device LAN:
```
tools/xrfd_ctl.sh "target 1 on"      # Linux/macOS — auto-discovers the device via diag broadcast
tools/xrfd_ctl.sh "status"
```
The Windows `target1_on.bat` / `status.bat` / `send.bat` are thin double-click wrappers
over the same UDP control; `xrfd_ctl.sh` covers them all.

## Legacy

`tools/xrfd_dashboard.ps1` (single-file PowerShell dashboard) remains as a lightweight
per-PC fallback. This Python/Svelte service is the primary dashboard.

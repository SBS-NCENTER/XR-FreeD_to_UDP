# XRFD Dashboard (Python + Svelte)

Central web dashboard for the XR-FreeD device. A Python (Flask + waitress) backend
runs a UDP bridge thread that receives the device's diag broadcast (UDP 50999) and
relays control commands (UDP 50998); it serves a Svelte/Vite frontend with live
updates over SSE.

Runs as one always-on service on a PC that is **on the device's LAN segment**
(broadcasts do not cross routers/subnets). Other PCs/tablets browse to it over the LAN.

## Install (once, as Administrator)

```
cd dashboard
powershell -ExecutionPolicy Bypass -File setup\install.ps1
```

This creates a Python venv, installs backend deps, builds the frontend, and adds the
required inbound firewall rules (UDP 50999 diag, TCP 10000 dashboard). Administrator is
required for the firewall rules — without them the diag broadcast is blocked on the
Public network profile and the dashboard shows no device.

Prerequisites: Python 3.x and Node.js on PATH (Node is needed only at build time).

## Run

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
cd dashboard && .venv\Scripts\python -m pytest backend/tests -v
# frontend dev server (proxies /api and /events to the running backend on :10000)
cd dashboard\frontend && npm run dev
```

## Endpoints

- `GET /` — Svelte dashboard
- `GET /api/status` — current state snapshot (JSON)
- `GET /events` — SSE stream of state snapshots
- `POST /api/cmd` `{ "cmd": "target 1 on" }` — relay a control command, returns the reply
- `GET /health` — `{status, pid, uptime_seconds, device}` (server.toml contract)

## Legacy

`tools/xrfd_dashboard.ps1` (single-file PowerShell dashboard) remains as a lightweight
per-PC fallback. This Python/Svelte service is the primary dashboard.

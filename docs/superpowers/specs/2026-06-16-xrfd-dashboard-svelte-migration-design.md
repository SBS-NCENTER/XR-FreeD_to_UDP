# XRFD Dashboard — Python + Svelte/Vite Migration Design

- **Date:** 2026-06-16
- **Status:** Approved design, pending implementation plan
- **Repo:** SBS-NCENTER/XR-FreeD_to_UDP (lives in-repo under `dashboard/`)

## 1. Background & Motivation

The current dashboard (`tools/xrfd_dashboard.ps1`) is a single PowerShell script that
runs a raw `TcpListener` HTTP server, receives the device's UDP 50999 diag broadcast,
relays UDP 50998 control commands, and serves an embedded HTML/JS page.

It works, but two recurring pain points motivate a rewrite:

1. **Firewall recurrence.** Windows Firewall filters inbound UDP by *receiving program +
   network profile*. On the Public profile (default-block) with no allow rule, the diag
   broadcast never reaches `powershell.exe` (5.1), so the dashboard shows no data and
   *appears* dead. A per-program *block* rule on `powershell.exe` even overrides a
   port-based allow. (The server itself is stable — observed 2h+ uptime; the symptom is
   missing data, not a crash.)
2. **Stack consistency / maintainability.** Other apps in this workspace use Python
   (schedule-dashboard = Flask) and modern frontends. Embedded HTML-in-PowerShell is hard
   to extend.

**Important truth carried into the design:** changing the language does *not* by itself
fix the firewall — `python.exe` is filtered the same way, and diag is inherently an
unsolicited inbound broadcast that *requires* an inbound allow rule. The migration helps
because (a) a fresh program is free of the sticky `powershell.exe` block, and (b) the
firewall rule is **baked into the installer** idempotently, so it stops recurring.

## 2. Goals / Non-Goals

**Goals**
- Replace the dashboard with a **Python (Flask) backend + Svelte/Vite frontend**.
- Run as **one central always-on service** on this Windows PC (10.10.204.47), which is on
  the device LAN (10.10.204.0/24); other PCs/tablets browse to it over the LAN.
- Port the current UI (Monokai/Solarized themes, status chips, target cards, event log)
  with **minor improvements** (FreeD-rate sparkline chart, responsive layout).
- Bake **idempotent firewall setup** into the installer so the firewall problem ends.
- Conform to the workspace **server.toml standard contract** (port, `/health`, pidfile) so
  `server-manager` supervises start/stop/restart.

**Non-Goals (YAGNI)**
- No per-PC local deployment model (central only).
- No FastAPI/WebSocket (Flask + SSE is sufficient).
- No full E2E frontend test suite or formal CI pipeline in v1.
- No full UI redesign (port + minor improvements only).
- The legacy `tools/*.ps1` ops scripts remain as lightweight fallback tooling.

## 3. Architecture Overview

A central Python process runs two cooperating parts in one service:
- a **UDP bridge** (background daemon thread): receives diag (50999), sends control (50998);
- a **Flask web app** (served by **waitress**): serves the built Svelte app, REST, and SSE.

```
Arduino --UDP 50999 diag broadcast (5s)--> [UDP bridge thread] --> State --push--> SSE --> browsers
browser --POST /api/cmd--> Flask --> [UDP bridge] --UDP 50998--> Arduino --reply--> browser
```

Network constraint: the host **must** be on the device's LAN segment (broadcasts do not
cross routers/subnets). Confirmed: 10.10.204.47 (this PC) receives 10.10.204.123's diag.

## 4. Project Structure

```
XR-FreeD_to_UDP/
  dashboard/
    backend/
      app.py            # Flask: static serving + REST + SSE
      udp_bridge.py     # UDP listener thread + control sender + targets refresh
      protocol.py       # diag/targets parsing + command builders (ported regexes)
      state.py          # thread-safe shared state + SSE subscriber queues
      config.py         # ports, paths, timeouts
      requirements.txt  # flask, waitress
      tests/            # pytest: protocol, state, bridge, api
    frontend/           # Svelte + Vite  ->  dist/
      src/
        App.svelte
        stores.js       # svelte stores fed by SSE
        lib/
          api.js            # EventSource(SSE) + postCmd()
          StatusBar.svelte  # chips: uptime, fps, dhcp, rtr, frames, conflict
          TargetCard.svelte # per-target on/off/edit
          EventLog.svelte
          ThemeToggle.svelte# Monokai / Solarized, localStorage
          RateChart.svelte  # NEW: FreeD-rate sparkline
      vite.config.js
      package.json
    setup/
      install.ps1       # idempotent: venv + pip + npm ci + npm build + firewall rules
      run.ps1           # activate venv, run waitress, write pidfile (called by server-manager)
    server.toml         # standard contract: port 10000, /health, data/xrfd.pid
    data/               # pidfile (gitignored)
```

## 5. Components (single responsibility)

### backend/protocol.py (pure functions — primary unit-test surface)
- `parse_diag(line) -> dict | None` — ports regex from ps1 line 94:
  `XRFD up=(\d+) (?:ms=(\d+) )?ip=(\S+) rx=(\d+) dhcp=(\d+)/(\d+) rtr=(\w)`, plus per-target
  matches (ps1 line 136) ` t(\d)=(off|[ABC]),?(\d+)?,?(\d+)?,?(\d+)?` and the ` CONFLICT` flag.
- `parse_targets(reply) -> list` — ports ps1 line 84: `^t(\d) (on|off) (ip):(port)`.
- Command builders for `target N on|off`, `target N set ip port`, `reboot`, `status`, `targets`.

### backend/state.py (thread-safe)
- Holds: `device_ip`, `last_seen`, `up`, `rx`, `fps`, `prev_rx/prev_ms/prev_time`,
  `dhcp_ok/fail`, `rtr`, `conflict`, `diag_state`, `targets`, `log` (deque maxlen 80),
  `ignored_ips`, `prev_fail`.
- `update_from_diag(parsed, src_ip)` — fps calc (device-ms based, with PC-clock fallback),
  reboot detection (up & rx both reset, not millis wrap), second-device ignore (>12s rule).
- `snapshot() -> dict` — matches the current `/api/status` JSON schema (frontend-compatible),
  with `age_sec` clamped (no Int32-style overflow; `live = device_ip != "" and age <= 12`).
- SSE: maintains a set of subscriber `queue.Queue`; `publish()` pushes a snapshot to each.
- All mutation/read guarded by a `threading.Lock`.

### backend/udp_bridge.py
- `UdpBridge` daemon thread: bind UDP 50999 (`INADDR_ANY`, `SO_REUSEADDR`), recv loop,
  `protocol.parse_diag` -> `state.update_from_diag` -> `state.publish()`.
- Per-packet `try/except`: a malformed packet logs and continues; fatal socket error ->
  backoff + re-bind. Never exits on data.
- `send_command(cmd, timeout=3s) -> str | error` via UDP 50998 (no device / no reply ->
  structured error, not exception).
- Refresh targets every 30s while a device is present (ports ps1 main-loop behavior).

### backend/app.py (Flask, served by waitress)
- `GET /` + static assets -> Svelte `frontend/dist/`.
- `GET /api/status` -> `state.snapshot()` (initial paint / polling fallback).
- `GET /events` -> SSE `text/event-stream`; registers a subscriber queue, streams snapshots
  on each diag update; cleans up on disconnect.
- `POST /api/cmd` body `{cmd}` -> `bridge.send_command`; returns reply text/JSON.
  (Replaces the old `GET /api/cmd?c=`.)
- `GET /health` -> `{status:"ok", pid, uptime_seconds, device}` (local request gets pid/
  uptime/device; conforms to server.toml contract).

### frontend (Svelte + Vite)
- `api.js`: `EventSource('/events')` updates a writable store; `postCmd(cmd)` does POST.
- Components mirror current UI + `RateChart` (fps history sparkline). Theme toggle persists
  to localStorage. Vite builds to `dist/`, Flask serves it.

## 6. Data Flow

1. Arduino diag (5s, UDP 50999) -> bridge recv/parse -> state update -> SSE push -> all
   browsers update live.
2. Browser load -> Svelte app -> `GET /api/status` immediate paint -> `EventSource('/events')`
   for live stream.
3. Target ON click -> `POST /api/cmd {cmd:"target 1 on"}` -> bridge -> UDP 50998 -> device
   reply -> returned to browser; bridge refreshes targets -> next SSE push reflects change.

## 7. Error Handling & Resilience

- UDP recv loop: per-packet try/except (never dies on bad data); socket re-bind w/ backoff.
- `send_command`: timeout / no-device -> structured error to UI, not a crash.
- State: `threading.Lock`; no-device case clamps age and reports `live=false` ("no signal"),
  explicitly covering the prior PS-5.1 Int32-overflow regression.
- Web server: **waitress** (pure-Python WSGI, Windows-friendly) instead of the Flask dev
  server.
- Supervision: server.toml + pidfile -> `server-manager` does start/stop/restart and
  boot-time autostart (no separate Windows service wrapper).

## 8. Firewall (ends the recurrence)

`setup/install.ps1` (run once, admin), **idempotent** — skip if a matching rule exists:
- Inbound **UDP 50999 Allow**, port-based, all programs, all profiles (the sticky
  `powershell.exe` *block* is program-scoped, so a `python`/`waitress` backend is unaffected).
- Inbound **TCP 10000 Allow** for other PCs'/tablets' browsers.

Diag is an unsolicited inbound broadcast, so an inbound allow rule is unavoidable in any
language; baking it into the installer is what stops the problem from recurring.

## 9. Deployment / Setup

- Prereqs on host: **Python 3.x** and **Node.js** (Node needed at *build time only*;
  runtime needs only Python). `install.ps1` checks for both and aborts with guidance if
  missing.
- `install.ps1` (once, admin): create venv -> `pip install flask waitress` ->
  `frontend/` `npm ci && npm run build` -> create firewall rules.
- `run.ps1`: activate venv -> `python -m backend.app` (waitress) -> write `data/xrfd.pid`.
  Invoked by `server-manager` via `server.toml`.
- Port 10000 retained for continuity.
- Build output `frontend/dist/` is gitignored; built by `install.ps1` on the host.

## 10. Testing Strategy

1. **protocol.py (pytest, primary):** real captured diag lines; `ms=`-less legacy form;
   per-target A/B/C/off with ok/fail/skip; targets reply; CONFLICT; malformed/partial lines
   safely ignored.
2. **state.py (pytest):** fps calc (device-ms + PC-clock fallback); reboot detection vs
   millis wrap; **no-device clamp (overflow regression guard)**; log deque cap (80);
   second-device ignore; `snapshot()` schema matches frontend expectations.
3. **udp_bridge.py (integration):** loopback fake diag -> state update + SSE queue push;
   `send_command` timeout -> structured error.
4. **API (Flask test client):** `/api/status`, `/health` (contract fields), `/api/cmd`
   (bridge mocked), `/events` (SSE headers + first event).
5. **Frontend (vitest, optional, light):** render StatusBar/TargetCard from a status object.
6. **Manual verification gate** against real device 10.10.204.123: live updates, target
   on/off/edit, reboot, multi-browser, cross-PC access after firewall setup.

CI: out of scope for v1 (single host) — local `pytest` green is the merge bar; GitHub
Actions can be added later.

## 11. Open Items (resolve during implementation)

- Confirm Python 3.x and Node.js presence on the host (install.ps1 handles/aborts).
- Decide SSE reconnect/backoff parameters on the client (EventSource auto-reconnects;
  tune retry interval).

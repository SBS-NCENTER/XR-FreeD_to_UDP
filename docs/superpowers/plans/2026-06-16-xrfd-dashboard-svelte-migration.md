# XRFD Dashboard (Python + Svelte/Vite) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the PowerShell dashboard with a central Flask (waitress) + SSE backend and a Svelte/Vite frontend, served on this Windows PC over the device LAN, with firewall setup baked into the installer.

**Architecture:** A Python process runs a UDP-bridge daemon thread (receives diag on 50999, sends control on 50998) that updates a thread-safe `State` and pushes snapshots to SSE subscribers. Flask (served by waitress) serves the built Svelte app, `/api/status`, `/api/cmd`, `/health`, and `/events`. Pure parsing/state logic is isolated for unit testing.

**Tech Stack:** Python 3.x, Flask, waitress, pytest (backend); Svelte + Vite, vitest (frontend); PowerShell setup scripts (venv/npm build/firewall).

**Reference spec:** `docs/superpowers/specs/2026-06-16-xrfd-dashboard-svelte-migration-design.md`
**Source of truth for protocol logic:** the existing `tools/xrfd_dashboard.ps1` (regexes, fps calc, reboot detection).

**JSON contract** (snapshot consumed by the frontend — keep these exact keys):
```json
{
  "deviceIp": "10.10.204.123", "ageSec": 2, "live": true,
  "up": 102800, "rx": 6166207, "fps": 60.0,
  "dhcpOk": 0, "dhcpFail": 0, "rtr": "Y", "conflict": false,
  "targets": [{"n":0,"on":true,"ip":"10.10.204.184","port":50001,"state":"A","ok":6166207,"fail":0,"skip":0}],
  "log": [{"t":"19:06:34","k":"info","m":"device found: 10.10.204.123"}]
}
```

---

## File Structure

```
dashboard/
  backend/
    __init__.py
    config.py          # ports, paths, timeouts
    protocol.py        # parse_diag, parse_targets, build_command (pure)
    state.py           # State (thread-safe) + SSE subscriber queues
    udp_bridge.py      # UdpBridge thread + send_command
    app.py             # Flask app factory + routes + waitress __main__
    requirements.txt
    tests/
      __init__.py
      conftest.py
      test_protocol.py
      test_state.py
      test_bridge.py
      test_app.py
  frontend/
    package.json, vite.config.js, index.html
    src/main.js, src/App.svelte, src/stores.js
    src/lib/api.js, StatusBar.svelte, TargetCard.svelte, EventLog.svelte,
            ThemeToggle.svelte, RateChart.svelte
    src/lib/__tests__/render.test.js
  setup/
    install.ps1, run.ps1
  server.toml
  .gitignore
data/                  # pidfile (gitignored)
```

---

## Task 1: Backend scaffolding + config

**Files:**
- Create: `dashboard/backend/__init__.py` (empty)
- Create: `dashboard/backend/tests/__init__.py` (empty)
- Create: `dashboard/backend/config.py`
- Create: `dashboard/backend/requirements.txt`
- Create: `dashboard/.gitignore`

- [ ] **Step 1: Create requirements.txt**

```
flask>=3.0
waitress>=3.0
pytest>=8.0
```

- [ ] **Step 2: Create config.py**

```python
"""Central configuration for the XRFD dashboard backend."""
from pathlib import Path

WEB_PORT = 10000          # HTTP/dashboard port (kept for continuity)
CTRL_PORT = 50998         # UDP: control commands to the device
DIAG_PORT = 50999         # UDP: device diag broadcast (received)

CMD_TIMEOUT_S = 3.0       # wait for a control reply
TARGETS_REFRESH_S = 30.0  # re-query 'targets' while a device is present
DEVICE_LIVE_S = 12        # device considered live if seen within this window
LOG_MAXLEN = 80           # event-log ring size

REPO_ROOT = Path(__file__).resolve().parents[2]   # .../XR-FreeD_to_UDP
DATA_DIR = REPO_ROOT / "data"
PIDFILE = DATA_DIR / "xrfd.pid"
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
```

- [ ] **Step 3: Create dashboard/.gitignore**

```
data/
*.pid
__pycache__/
*.pyc
.venv/
frontend/node_modules/
frontend/dist/
```

- [ ] **Step 4: Create empty __init__.py files**

`dashboard/backend/__init__.py` and `dashboard/backend/tests/__init__.py` — both empty.

- [ ] **Step 5: Verify imports work**

Run: `cd dashboard/backend && python -c "import config; print(config.WEB_PORT)"`
Expected: `10000`

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/__init__.py dashboard/backend/tests/__init__.py dashboard/backend/config.py dashboard/backend/requirements.txt dashboard/.gitignore
git commit -m "scaffold: dashboard backend config + deps"
```

---

## Task 2: protocol.parse_diag

**Files:**
- Create: `dashboard/backend/protocol.py`
- Test: `dashboard/backend/tests/test_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_protocol.py
from backend import protocol

REAL = ("XRFD up=336055 ms=336055050 ip=10.10.204.123 rx=20157893 dhcp=0/0 rtr=Y "
        "t0=A,20157893,0,0 t1=A,20157893,0,0 t2=off t3=off")

def test_parse_diag_basic_fields():
    d = protocol.parse_diag(REAL)
    assert d["up"] == 336055
    assert d["ms"] == 336055050
    assert d["ip"] == "10.10.204.123"
    assert d["rx"] == 20157893
    assert d["dhcp_ok"] == 0 and d["dhcp_fail"] == 0
    assert d["rtr"] == "Y"
    assert d["conflict"] is False

def test_parse_diag_targets():
    d = protocol.parse_diag(REAL)
    assert d["targets"]["0"] == {"state": "A", "ok": 20157893, "fail": 0, "skip": 0}
    assert d["targets"]["2"] == {"state": "off", "ok": 0, "fail": 0, "skip": 0}

def test_parse_diag_legacy_no_ms():
    d = protocol.parse_diag("XRFD up=10 ip=1.2.3.4 rx=5 dhcp=1/2 rtr=N")
    assert d["ms"] is None and d["rtr"] == "N" and d["dhcp_fail"] == 2

def test_parse_diag_conflict():
    assert protocol.parse_diag(REAL + " CONFLICT")["conflict"] is True

def test_parse_diag_garbage_returns_none():
    assert protocol.parse_diag("hello world") is None
    assert protocol.parse_diag("XRFD up=") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_protocol.py -v`
Expected: FAIL — `AttributeError: module 'backend.protocol' has no attribute 'parse_diag'` (or ImportError)

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/backend/protocol.py
"""Pure parsing/command helpers ported from tools/xrfd_dashboard.ps1."""
import re

_DIAG = re.compile(
    r"XRFD up=(\d+) (?:ms=(\d+) )?ip=(\S+) rx=(\d+) dhcp=(\d+)/(\d+) rtr=(\w)")
_TARGET = re.compile(r" t(\d)=(off|[ABC]),?(\d+)?,?(\d+)?,?(\d+)?")
_TARGETS_LINE = re.compile(r"^t(\d) (on|off) (\d+\.\d+\.\d+\.\d+):(\d+)")


def parse_diag(line):
    """Parse one diag broadcast line. Returns dict or None if it doesn't match."""
    m = _DIAG.search(line)
    if not m:
        return None
    targets = {}
    for t in _TARGET.finditer(line):
        n, state, ok, fail, skip = t.group(1), t.group(2), t.group(3), t.group(4), t.group(5)
        targets[n] = {
            "state": state,
            "ok": int(ok) if ok else 0,
            "fail": int(fail) if fail else 0,
            "skip": int(skip) if skip else 0,
        }
    return {
        "up": int(m.group(1)),
        "ms": int(m.group(2)) if m.group(2) else None,
        "ip": m.group(3),
        "rx": int(m.group(4)),
        "dhcp_ok": int(m.group(5)),
        "dhcp_fail": int(m.group(6)),
        "rtr": m.group(7),
        "conflict": " CONFLICT" in line,
        "targets": targets,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/test_protocol.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/protocol.py dashboard/backend/tests/test_protocol.py
git commit -m "feat: protocol.parse_diag (ported from ps1)"
```

---

## Task 3: protocol.parse_targets + build_command

**Files:**
- Modify: `dashboard/backend/protocol.py`
- Test: `dashboard/backend/tests/test_protocol.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_parse_targets():
    reply = ("t0 on 10.10.204.184:50001\n"
             "t1 off 10.10.204.175:50001\n"
             "garbage line\n")
    out = protocol.parse_targets(reply)
    assert out == [
        {"n": 0, "on": True,  "ip": "10.10.204.184", "port": 50001},
        {"n": 1, "on": False, "ip": "10.10.204.175", "port": 50001},
    ]

def test_build_command():
    assert protocol.build_command("target", 1, "on") == "target 1 on"
    assert protocol.build_command("target", 2, "set", "1.2.3.4", 50001) == "target 2 set 1.2.3.4 50001"
    assert protocol.build_command("reboot") == "reboot"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_protocol.py::test_parse_targets -v`
Expected: FAIL — `has no attribute 'parse_targets'`

- [ ] **Step 3: Write minimal implementation (append to protocol.py)**

```python
def parse_targets(reply):
    """Parse a 'targets' command reply into a list of target dicts."""
    out = []
    for line in reply.split("\n"):
        m = _TARGETS_LINE.match(line)
        if m:
            out.append({
                "n": int(m.group(1)),
                "on": m.group(2) == "on",
                "ip": m.group(3),
                "port": int(m.group(4)),
            })
    return out


def build_command(*parts):
    """Join command parts into the wire string, e.g. ('target',1,'on') -> 'target 1 on'."""
    return " ".join(str(p) for p in parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/test_protocol.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/protocol.py dashboard/backend/tests/test_protocol.py
git commit -m "feat: protocol.parse_targets + build_command"
```

---

## Task 4: state.State — diag update, fps, reboot, snapshot

**Files:**
- Create: `dashboard/backend/state.py`
- Test: `dashboard/backend/tests/test_state.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_state.py
from backend.state import State
from backend import protocol

DEV = "10.10.204.123"

def feed(st, line, src=DEV):
    st.update_from_diag(protocol.parse_diag(line), src)

def test_first_packet_sets_device_and_logs():
    st = State()
    feed(st, "XRFD up=10 ms=10000 ip=%s rx=5 dhcp=0/0 rtr=Y" % DEV)
    snap = st.snapshot()
    assert snap["deviceIp"] == DEV
    assert snap["live"] is True
    assert snap["up"] == 10 and snap["rx"] == 5 and snap["rtr"] == "Y"
    assert any(e["m"].startswith("device found") for e in snap["log"])

def test_no_device_snapshot_is_safe():
    # Regression guard for the PS5.1 Int32 overflow: no device -> huge age, must not crash
    snap = State().snapshot()
    assert snap["deviceIp"] == "" and snap["live"] is False
    assert isinstance(snap["ageSec"], int) and snap["ageSec"] >= 0

def test_fps_from_device_ms():
    st = State()
    feed(st, "XRFD up=1 ms=1000 ip=%s rx=0 dhcp=0/0 rtr=Y" % DEV)
    feed(st, "XRFD up=2 ms=2000 ip=%s rx=60 dhcp=0/0 rtr=Y" % DEV)
    assert st.snapshot()["fps"] == 60.0   # 60 frames over 1000 ms

def test_reboot_detection_resets_fps_baseline():
    st = State()
    feed(st, "XRFD up=100 ms=100000 ip=%s rx=6000 dhcp=0/0 rtr=Y" % DEV)
    feed(st, "XRFD up=2 ms=2000 ip=%s rx=10 dhcp=0/0 rtr=Y" % DEV)  # up & rx both reset
    assert any("REBOOT" in e["m"] for e in st.snapshot()["log"])

def test_log_capped_at_maxlen():
    st = State()
    for i in range(200):
        st.add_log("info", "msg %d" % i)
    assert len(st._log) <= 80
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.state'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/backend/state.py
"""Thread-safe shared dashboard state (ported from tools/xrfd_dashboard.ps1)."""
import threading
import time
from collections import deque
from datetime import datetime

from . import config


class State:
    def __init__(self):
        self._lock = threading.RLock()
        self.device_ip = ""
        self.last_seen = 0.0          # monotonic-ish wall time (time.time())
        self.up = 0
        self.rx = 0
        self.fps = 0.0
        self.dhcp_ok = 0
        self.dhcp_fail = 0
        self.rtr = "?"
        self.conflict = False
        self.diag_state = {}          # "0" -> {state,ok,fail,skip}
        self.targets = []             # [{n,on,ip,port}]
        self._log = deque(maxlen=config.LOG_MAXLEN)
        self._prev_rx = -1
        self._prev_ms = -1
        self._prev_time = None
        self._ignored_ips = set()

    def add_log(self, kind, msg):
        with self._lock:
            self._log.appendleft({"t": datetime.now().strftime("%H:%M:%S"),
                                  "k": kind, "m": msg})

    def update_from_diag(self, parsed, src_ip):
        if parsed is None:
            return
        now = time.time()
        with self._lock:
            # ignore a second XRFD device while the current one is live
            if (self.device_ip and src_ip != self.device_ip
                    and (now - self.last_seen) <= config.DEVICE_LIVE_S):
                if src_ip not in self._ignored_ips:
                    self._ignored_ips.add(src_ip)
                    self.add_log("warn", "ignoring second XRFD device at %s (active: %s)"
                                 % (src_ip, self.device_ip))
                return

            up, rx, ms = parsed["up"], parsed["rx"], parsed["ms"]
            # real reboot: up AND rx both reset (millis wrap resets up only)
            if self.device_ip and up < self.up and rx < self.rx:
                self.add_log("warn", "DEVICE REBOOTED (uptime reset: %ds -> %ds)" % (self.up, up))
                self._prev_rx = -1
                self._prev_ms = -1
            if not self.device_ip:
                self.add_log("info", "device found: %s" % src_ip)

            self.device_ip = src_ip
            self.last_seen = now
            self.up, self.rx = up, rx
            self.dhcp_ok, self.dhcp_fail = parsed["dhcp_ok"], parsed["dhcp_fail"]
            self.rtr = parsed["rtr"]
            if parsed["conflict"] and not self.conflict:
                self.add_log("warn", "IP CONFLICT detected on device LAN!")
            self.conflict = parsed["conflict"]

            # fps from device ms when available, else PC-clock fallback
            if ms is not None and self._prev_ms >= 0 and ms > self._prev_ms and rx >= self._prev_rx:
                dms = ms - self._prev_ms
                if dms >= 1000:
                    self.fps = round((rx - self._prev_rx) * 1000.0 / dms, 2)
            elif ms is None and self._prev_rx >= 0 and rx > self._prev_rx and self._prev_time:
                dt = now - self._prev_time
                if dt > 0.5:
                    self.fps = round((rx - self._prev_rx) / dt, 2)
            self._prev_rx, self._prev_ms, self._prev_time = rx, (ms if ms is not None else -1), now

            self.diag_state = parsed["targets"]

    def snapshot(self):
        with self._lock:
            if self.last_seen:
                age = int(min(999999, time.time() - self.last_seen))
            else:
                age = 999999
            tl = []
            for t in self.targets:
                d = self.diag_state.get(str(t["n"]))
                entry = {"n": t["n"], "on": t["on"], "ip": t["ip"], "port": t["port"],
                         "state": "off", "ok": 0, "fail": 0, "skip": 0}
                if d:
                    entry.update(state=d["state"], ok=d["ok"], fail=d["fail"], skip=d["skip"])
                tl.append(entry)
            return {
                "deviceIp": self.device_ip,
                "ageSec": age,
                "live": bool(self.device_ip) and age <= config.DEVICE_LIVE_S,
                "up": self.up, "rx": self.rx, "fps": self.fps,
                "dhcpOk": self.dhcp_ok, "dhcpFail": self.dhcp_fail,
                "rtr": self.rtr, "conflict": self.conflict,
                "targets": tl,
                "log": list(self._log)[:40],
            }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/test_state.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/state.py dashboard/backend/tests/test_state.py
git commit -m "feat: thread-safe State with diag update, fps, reboot, snapshot"
```

---

## Task 5: state — SSE publish/subscribe

**Files:**
- Modify: `dashboard/backend/state.py`
- Test: `dashboard/backend/tests/test_state.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
import queue

def test_subscribe_receives_published_snapshot():
    st = State()
    q = st.subscribe()
    st.publish()
    item = q.get(timeout=1)
    assert item["deviceIp"] == ""      # a snapshot dict
    st.unsubscribe(q)
    assert q not in st._subscribers

def test_update_from_diag_publishes_to_subscribers():
    st = State()
    q = st.subscribe()
    st.update_from_diag(protocol.parse_diag(
        "XRFD up=1 ms=1000 ip=%s rx=0 dhcp=0/0 rtr=Y" % DEV), DEV)
    st.publish()
    assert q.get(timeout=1)["deviceIp"] == DEV
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_state.py::test_subscribe_receives_published_snapshot -v`
Expected: FAIL — `'State' object has no attribute 'subscribe'`

- [ ] **Step 3: Write minimal implementation**

Add to `State.__init__`:
```python
        self._subscribers = set()
```
Add methods to `State`:
```python
    def subscribe(self):
        import queue
        q = queue.Queue(maxsize=10)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)

    def publish(self):
        snap = self.snapshot()
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(snap)
            except Exception:
                pass   # slow/full consumer: drop this update, never block the bridge
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/test_state.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/state.py dashboard/backend/tests/test_state.py
git commit -m "feat: State SSE publish/subscribe queues"
```

---

## Task 6: udp_bridge — listener thread (loopback integration)

**Files:**
- Create: `dashboard/backend/udp_bridge.py`
- Test: `dashboard/backend/tests/test_bridge.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_bridge.py
import socket
import time
from backend.state import State
from backend.udp_bridge import UdpBridge
from backend import config

DEV_LINE = b"XRFD up=1 ms=1000 ip=127.0.0.1 rx=0 dhcp=0/0 rtr=Y"

def test_bridge_receives_and_updates_state(monkeypatch):
    # use a high random diag port to avoid clashing with a real device/listener
    monkeypatch.setattr(config, "DIAG_PORT", 55999)
    st = State()
    bridge = UdpBridge(st)
    bridge.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _ in range(20):
            s.sendto(DEV_LINE, ("127.0.0.1", 55999))
            if st.snapshot()["deviceIp"]:
                break
            time.sleep(0.1)
        assert st.snapshot()["deviceIp"] == "127.0.0.1"
    finally:
        bridge.stop()

def test_bridge_survives_garbage_packet(monkeypatch):
    monkeypatch.setattr(config, "DIAG_PORT", 55998)
    st = State()
    bridge = UdpBridge(st)
    bridge.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"\xff\xff not xrfd", ("127.0.0.1", 55998))   # must not crash thread
        time.sleep(0.3)
        s.sendto(DEV_LINE, ("127.0.0.1", 55998))
        for _ in range(20):
            if st.snapshot()["deviceIp"]:
                break
            time.sleep(0.1)
        assert st.snapshot()["deviceIp"] == "127.0.0.1"
        assert bridge.is_alive()
    finally:
        bridge.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.udp_bridge'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/backend/udp_bridge.py
"""UDP bridge: receive diag (DIAG_PORT), send control (CTRL_PORT)."""
import socket
import threading
import time

from . import config, protocol


class UdpBridge(threading.Thread):
    def __init__(self, state):
        super().__init__(daemon=True, name="udp-bridge")
        self.state = state
        self._stop = threading.Event()
        self._last_targets_refresh = 0.0

    def _bind(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", config.DIAG_PORT))
        s.settimeout(0.5)
        return s

    def run(self):
        sock = self._bind()
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                self._maybe_refresh_targets()
                continue
            except OSError:
                time.sleep(1.0)                 # transient socket error: back off, re-bind
                try:
                    sock.close()
                finally:
                    sock = self._bind()
                continue
            try:
                line = data.decode("ascii", "replace")
                parsed = protocol.parse_diag(line)
                if parsed:
                    self.state.update_from_diag(parsed, addr[0])
                    self.state.publish()
            except Exception:
                pass                            # never let one packet kill the loop
        sock.close()

    def _maybe_refresh_targets(self):
        if not self.state.device_ip:
            return
        if time.time() - self._last_targets_refresh < config.TARGETS_REFRESH_S:
            return
        self._last_targets_refresh = time.time()
        reply = self.send_command("targets")
        if reply:
            tl = protocol.parse_targets(reply)
            if tl:
                with self.state._lock:
                    self.state.targets = tl
                self.state.publish()

    def send_command(self, cmd, timeout=None):
        """Send a control command to the device and return its reply, or None."""
        ip = self.state.device_ip
        if not ip:
            return None
        timeout = config.CMD_TIMEOUT_S if timeout is None else timeout
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(cmd.encode("ascii"), (ip, config.CTRL_PORT))
            data, _ = s.recvfrom(8192)
            return data.decode("ascii", "replace")
        except (socket.timeout, OSError):
            return None
        finally:
            s.close()

    def stop(self):
        self._stop.set()
        self.join(timeout=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/test_bridge.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/udp_bridge.py dashboard/backend/tests/test_bridge.py
git commit -m "feat: UdpBridge listener thread + send_command + targets refresh"
```

---

## Task 7: send_command + logging on control (integration)

**Files:**
- Modify: `dashboard/backend/udp_bridge.py`
- Test: `dashboard/backend/tests/test_bridge.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_send_command_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "CTRL_PORT", 55001)
    # fake device: echoes "ok:<cmd>"
    dev = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dev.bind(("127.0.0.1", 55001))
    dev.settimeout(2)
    st = State()
    st.device_ip = "127.0.0.1"
    bridge = UdpBridge(st)

    import threading
    def responder():
        data, addr = dev.recvfrom(4096)
        dev.sendto(b"ok:" + data, addr)
    threading.Thread(target=responder, daemon=True).start()
    try:
        reply = bridge.send_command("status")
        assert reply == "ok:status"
    finally:
        dev.close()

def test_send_command_no_device_returns_none():
    st = State()           # device_ip == ""
    assert UdpBridge(st).send_command("status") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_bridge.py::test_send_command_roundtrip -v`
Expected: FAIL if `CTRL_PORT` monkeypatch isn't honored — confirm send_command reads `config.CTRL_PORT` at call time (it does in Task 6 code). If both new tests pass immediately, that is acceptable (behavior already implemented); proceed to commit.

- [ ] **Step 3: Implementation**

No new code needed — `send_command` from Task 6 already reads `config.CTRL_PORT` dynamically and returns `None` when `device_ip` is empty. (This task locks the behavior with tests.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/test_bridge.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/tests/test_bridge.py
git commit -m "test: lock send_command roundtrip + no-device behavior"
```

---

## Task 8: app.py — Flask factory, /api/status, /health

**Files:**
- Create: `dashboard/backend/app.py`
- Test: `dashboard/backend/tests/test_app.py`
- Create: `dashboard/backend/tests/conftest.py`

- [ ] **Step 1: Write conftest + failing test**

```python
# dashboard/backend/tests/conftest.py
import pytest
from backend.app import create_app
from backend.state import State

class FakeBridge:
    def __init__(self, reply="ok"):
        self.reply = reply
        self.sent = []
    def send_command(self, cmd, timeout=None):
        self.sent.append(cmd)
        return self.reply

@pytest.fixture
def app_and_state():
    st = State()
    bridge = FakeBridge()
    app = create_app(st, bridge)
    app.testing = True
    return app, st, bridge
```

```python
# dashboard/backend/tests/test_app.py
def test_status_returns_snapshot(app_and_state):
    app, st, _ = app_and_state
    c = app.test_client()
    r = c.get("/api/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["deviceIp"] == "" and body["live"] is False
    assert set(["up", "rx", "fps", "targets", "log"]).issubset(body)

def test_health_contract_fields(app_and_state):
    app, _, _ = app_and_state
    body = app.test_client().get("/health").get_json()
    assert body["status"] == "ok"
    assert "pid" in body and "uptime_seconds" in body and "device" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_app.py -v`
Expected: FAIL — `No module named 'backend.app'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/backend/app.py
"""Flask app factory + waitress entrypoint for the XRFD dashboard."""
import os
import time

from flask import Flask, jsonify, request, send_from_directory, Response

from . import config


def create_app(state, bridge):
    app = Flask(__name__, static_folder=None)
    start_time = time.time()

    @app.get("/api/status")
    def api_status():
        return jsonify(state.snapshot())

    @app.get("/health")
    def health():
        is_local = request.remote_addr in ("127.0.0.1", "::1")
        obj = {"status": "ok"}
        if is_local:
            obj["pid"] = os.getpid()
            obj["uptime_seconds"] = round(time.time() - start_time, 1)
            obj["device"] = state.device_ip
        return jsonify(obj)

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/test_app.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app.py dashboard/backend/tests/test_app.py dashboard/backend/tests/conftest.py
git commit -m "feat: Flask app factory with /api/status and /health"
```

---

## Task 9: app.py — POST /api/cmd

**Files:**
- Modify: `dashboard/backend/app.py`
- Test: `dashboard/backend/tests/test_app.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_cmd_calls_bridge_and_returns_reply(app_and_state):
    app, _, bridge = app_and_state
    bridge.reply = "ok:target 1 on"
    r = app.test_client().post("/api/cmd", json={"cmd": "target 1 on"})
    assert r.status_code == 200
    assert r.get_json()["reply"] == "ok:target 1 on"
    assert bridge.sent == ["target 1 on"]

def test_cmd_no_reply_reports_error(app_and_state):
    app, _, bridge = app_and_state
    bridge.reply = None
    body = app.test_client().post("/api/cmd", json={"cmd": "status"}).get_json()
    assert body["reply"] is None and body["ok"] is False

def test_cmd_missing_field_is_400(app_and_state):
    app, _, _ = app_and_state
    assert app.test_client().post("/api/cmd", json={}).status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_app.py::test_cmd_calls_bridge_and_returns_reply -v`
Expected: FAIL — 404 (route not defined)

- [ ] **Step 3: Write minimal implementation (add inside create_app, before `return app`)**

```python
    @app.post("/api/cmd")
    def api_cmd():
        data = request.get_json(silent=True) or {}
        cmd = data.get("cmd")
        if not cmd:
            return jsonify({"error": "missing 'cmd'"}), 400
        reply = bridge.send_command(cmd)
        state.add_log("cmd", "%s  ->  %s" % (cmd, reply if reply else "(no reply)"))
        if cmd.startswith("target "):
            bridge._maybe_refresh_targets()
        return jsonify({"ok": reply is not None, "reply": reply})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/test_app.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app.py dashboard/backend/tests/test_app.py
git commit -m "feat: POST /api/cmd relays control to the device"
```

---

## Task 10: app.py — /events SSE stream

**Files:**
- Modify: `dashboard/backend/app.py`
- Test: `dashboard/backend/tests/test_app.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_events_streams_initial_snapshot(app_and_state):
    app, st, _ = app_and_state
    c = app.test_client()
    r = c.get("/events", headers={"Accept": "text/event-stream"}, buffered=False)
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/event-stream")
    # read the first SSE event off the stream
    it = r.response
    chunk = next(iter(it))
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    assert text.startswith("data: ")
    assert '"deviceIp"' in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_app.py::test_events_streams_initial_snapshot -v`
Expected: FAIL — 404

- [ ] **Step 3: Write minimal implementation**

Add `import json` and `import queue` at top of app.py. Add inside create_app:
```python
    @app.get("/events")
    def events():
        def stream():
            q = state.subscribe()
            try:
                # immediate snapshot so a fresh client paints at once
                yield "data: %s\n\n" % json.dumps(state.snapshot())
                while True:
                    try:
                        snap = q.get(timeout=15)
                        yield "data: %s\n\n" % json.dumps(snap)
                    except queue.Empty:
                        yield ": keepalive\n\n"   # comment frame keeps proxies open
            finally:
                state.unsubscribe(q)
        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/test_app.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app.py dashboard/backend/tests/test_app.py
git commit -m "feat: /events SSE stream of state snapshots"
```

---

## Task 11: app.py — static serving, pidfile, waitress entrypoint

**Files:**
- Modify: `dashboard/backend/app.py`
- Test: `dashboard/backend/tests/test_app.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_index_served_when_dist_missing_returns_helpful_503(app_and_state, monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "FRONTEND_DIST", config.FRONTEND_DIST / "nonexistent")
    r = app_and_state[0].test_client().get("/")
    assert r.status_code == 503
    assert b"npm run build" in r.data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest backend/tests/test_app.py::test_index_served_when_dist_missing_returns_helpful_503 -v`
Expected: FAIL — 404

- [ ] **Step 3: Write minimal implementation**

Add inside create_app (static routes):
```python
    @app.get("/")
    def index():
        if not (config.FRONTEND_DIST / "index.html").exists():
            return ("frontend not built — run: cd dashboard/frontend && npm run build", 503)
        return send_from_directory(config.FRONTEND_DIST, "index.html")

    @app.get("/<path:path>")
    def assets(path):
        if (config.FRONTEND_DIST / path).exists():
            return send_from_directory(config.FRONTEND_DIST, path)
        return ("not found", 404)
```

Add the waitress entrypoint at the bottom of app.py:
```python
def _write_pidfile():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.PIDFILE.write_text(
        '{"pid": %d, "command": "python -m backend.app", "started_at": "%s"}'
        % (os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S")))


def main():
    from .state import State
    from .udp_bridge import UdpBridge
    from waitress import serve
    state = State()
    bridge = UdpBridge(state)
    bridge.start()
    state.add_log("info", "dashboard started")
    _write_pidfile()
    app = create_app(state, bridge)
    print("XRFD dashboard on http://0.0.0.0:%d (waitress)" % config.WEB_PORT)
    try:
        serve(app, host="0.0.0.0", port=config.WEB_PORT, threads=8)
    finally:
        bridge.stop()
        config.PIDFILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest backend/tests/ -v`
Expected: PASS (entire backend suite)

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app.py dashboard/backend/tests/test_app.py
git commit -m "feat: static serving, pidfile, waitress entrypoint"
```

---

## Task 12: Frontend scaffold (Vite + Svelte) + api/stores

**Files:**
- Create: `dashboard/frontend/package.json`, `vite.config.js`, `index.html`, `src/main.js`, `src/stores.js`, `src/lib/api.js`, `src/App.svelte`

- [ ] **Step 1: Scaffold Vite+Svelte**

Run: `cd dashboard/frontend && npm create vite@latest . -- --template svelte` then `npm install`
(If the dir must be empty, scaffold in a temp dir and move files in.)

- [ ] **Step 2: Add vite proxy for dev (vite.config.js)**

```javascript
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: { proxy: { '/api': 'http://localhost:10000', '/events': { target: 'http://localhost:10000', ws: false } } },
})
```

- [ ] **Step 3: Create src/stores.js**

```javascript
import { writable } from 'svelte/store'
export const status = writable({
  deviceIp: '', ageSec: 999999, live: false, up: 0, rx: 0, fps: 0,
  dhcpOk: 0, dhcpFail: 0, rtr: '?', conflict: false, targets: [], log: [],
})
export const fpsHistory = writable([])   // for RateChart
```

- [ ] **Step 4: Create src/lib/api.js**

```javascript
import { status, fpsHistory } from '../stores.js'

export function connect() {
  fetch('/api/status').then(r => r.json()).then(apply).catch(() => {})
  const es = new EventSource('/events')
  es.onmessage = (e) => apply(JSON.parse(e.data))
  return es
}
function apply(s) {
  status.set(s)
  fpsHistory.update(h => [...h.slice(-59), s.fps || 0])
}
export async function postCmd(cmd) {
  const r = await fetch('/api/cmd', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd }),
  })
  return (await r.json()).reply
}
```

- [ ] **Step 5: Verify dev server boots**

Run: `cd dashboard/frontend && npm run build`
Expected: a `dist/` directory is produced with `index.html`.

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/package.json dashboard/frontend/package-lock.json dashboard/frontend/vite.config.js dashboard/frontend/index.html dashboard/frontend/src
git commit -m "scaffold: Svelte/Vite frontend with api + stores"
```

---

## Task 13: Frontend components (StatusBar, TargetCard, EventLog, ThemeToggle)

**Files:**
- Create: `dashboard/frontend/src/lib/StatusBar.svelte`, `TargetCard.svelte`, `EventLog.svelte`, `ThemeToggle.svelte`
- Reference for markup/CSS/labels: `tools/xrfd_dashboard.ps1` lines 250-366 (existing HTML/JS).

- [ ] **Step 1: StatusBar.svelte** (chips: uptime, fps, dhcp, rtr, frames, conflict)

```svelte
<script>
  export let s
  function fmtUp(x){const d=Math.floor(x/86400),h=Math.floor(x%86400/3600),m=Math.floor(x%3600/60);
    return (d?d+'d ':'')+(h?h+'h ':'')+m+'m '+(x%60)+'s'}
</script>
<div class="bar">
  <div class="chip">Uptime<b>{fmtUp(s.up)}</b></div>
  <div class="chip">FreeD rate<b>{(s.fps||0).toFixed(2)} fps</b></div>
  <div class="chip" class:warn={s.dhcpFail>0}>DHCP renew<b>{s.dhcpOk} / {s.dhcpFail} fail</b></div>
  <div class="chip" class:bad={s.rtr!=='Y'}>RTR patch<b>{s.rtr==='Y'?'OK (80ms)':'FAILED'}</b></div>
  <div class="chip">Frames<b>{(s.rx||0).toLocaleString()}</b></div>
  {#if s.conflict}<div class="chip bad">IP conflict<b>DETECTED</b></div>{/if}
</div>
```

- [ ] **Step 2: TargetCard.svelte** (per-target on/off/edit)

```svelte
<script>
  import { postCmd } from './api.js'
  export let t
  const refresh = () => {}
  function toggle(){ postCmd('target '+t.n+' '+(t.on?'off':'on')) }
  function edit(){
    const ip=prompt('Target '+t.n+' IP:',t.ip); if(ip===null)return
    const port=prompt('Target '+t.n+' port:',t.port); if(port===null)return
    postCmd('target '+t.n+' set '+ip.trim()+' '+port.trim()).then(r=>alert(r))
  }
  $: badge = t.on ? (t.state==='B'?'BACKOFF':(t.state==='C'?'NO SOCKET':'ON')) : 'OFF'
  $: cls = t.on ? ((t.state==='B'||t.state==='C')?'backoff':'on') : 'off'
</script>
<div class="card" class:dim={!t.on}>
  <div class="hd"><span class="nm">Target {t.n}</span><span class="badge {cls}">{badge}</span></div>
  <div class="addr">{t.ip} : {t.port}</div>
  <div class="cnt">ok {t.ok.toLocaleString()} &nbsp; fail <span class:bad={t.fail>0}>{t.fail}</span> &nbsp; skip {t.skip}</div>
  <div class="btns">
    <button class={t.on?'b-off':'b-on'} on:click={toggle}>{t.on?'Turn OFF':'Turn ON'}</button>
    <button class="b-edit" on:click={edit}>Edit IP/Port</button>
  </div>
</div>
```

- [ ] **Step 3: EventLog.svelte**

```svelte
<script>export let log = []</script>
<div class="log">
  {#each log as e}<div class={e.k}>[{e.t}] {e.m}</div>{/each}
</div>
```

- [ ] **Step 4: ThemeToggle.svelte** (Monokai/Solarized, localStorage)

```svelte
<script>
  import { onMount } from 'svelte'
  let theme = 'dark'
  onMount(() => { theme = localStorage.getItem('xrfd_theme') || 'dark'; document.body.className = theme })
  function toggle(){ theme = theme==='dark'?'light':'dark'; document.body.className = theme; localStorage.setItem('xrfd_theme', theme) }
</script>
<button class="btn-theme" on:click={toggle} title="Monokai / Solarized">&#9789;</button>
```

- [ ] **Step 5: Build to verify no syntax errors**

Run: `cd dashboard/frontend && npm run build`
Expected: build succeeds (components compile).

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/src/lib
git commit -m "feat: dashboard Svelte components (StatusBar/TargetCard/EventLog/ThemeToggle)"
```

---

## Task 14: RateChart + App.svelte wiring + theme CSS

**Files:**
- Create: `dashboard/frontend/src/lib/RateChart.svelte`
- Modify: `dashboard/frontend/src/App.svelte`, `dashboard/frontend/src/app.css` (theme variables ported from ps1 lines 259-301)

- [ ] **Step 1: RateChart.svelte** (fps sparkline — the "minor improvement")

```svelte
<script>
  export let history = []
  $: max = Math.max(60, ...history)
  $: pts = history.map((v,i)=>`${(i/Math.max(1,history.length-1))*100},${100-(v/max)*100}`).join(' ')
</script>
<svg class="spark" viewBox="0 0 100 100" preserveAspectRatio="none">
  <polyline points={pts} fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>
</svg>
```

- [ ] **Step 2: App.svelte — wire stores + components**

```svelte
<script>
  import { onMount, onDestroy } from 'svelte'
  import { status, fpsHistory } from './stores.js'
  import { connect, postCmd } from './lib/api.js'
  import StatusBar from './lib/StatusBar.svelte'
  import TargetCard from './lib/TargetCard.svelte'
  import EventLog from './lib/EventLog.svelte'
  import ThemeToggle from './lib/ThemeToggle.svelte'
  import RateChart from './lib/RateChart.svelte'
  let es
  onMount(() => { es = connect() })
  onDestroy(() => es && es.close())
  function reboot(){ if(confirm('Reboot the device? FreeD output stops briefly.')) postCmd('reboot').then(r=>alert(r)) }
</script>

<h1>
  <span class="dot" class:live={$status.live} class:dead={!$status.live}></span>
  XRFD Dashboard
  <span class="dev">device: {$status.deviceIp || '-'}{$status.live ? '' : `  (no signal ${$status.ageSec}s)`}</span>
  <ThemeToggle/>
</h1>
<StatusBar s={$status}/>
<RateChart history={$fpsHistory}/>
<div class="grid">
  {#each $status.targets as t}<TargetCard {t}/>{:else}<div class="card">No target info yet...</div>{/each}
</div>
<div class="foot"><h2>Event log</h2><button class="b-reboot" on:click={reboot}>Reboot device</button></div>
<EventLog log={$status.log}/>
```

- [ ] **Step 3: app.css — port theme variables**

Copy the `body.dark{...}` and `body.light{...}` custom-property blocks and the component CSS (`.bar`, `.chip`, `.card`, `.badge`, `.log`, `button`, `.dot`, etc.) from `tools/xrfd_dashboard.ps1` lines 259-301 into `src/app.css`. Add: `.spark{width:100%;height:48px;background:var(--panel);border-radius:10px;border:1px solid var(--border);margin-bottom:12px}`. Import `./app.css` in `src/main.js`.

- [ ] **Step 4: Build + eyeball**

Run: `cd dashboard/frontend && npm run build`
Expected: `dist/index.html` + assets produced, no errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src
git commit -m "feat: RateChart + App wiring + ported theme CSS"
```

---

## Task 15: setup scripts + server.toml

**Files:**
- Create: `dashboard/setup/install.ps1`, `dashboard/setup/run.ps1`, `dashboard/server.toml`

- [ ] **Step 1: install.ps1 (idempotent)**

```powershell
# Run once as Administrator. Sets up venv, builds frontend, adds firewall rules.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot         # dashboard/
foreach ($exe in 'python','node','npm') {
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { Write-Host "ERROR: '$exe' not found in PATH."; exit 1 }
}
python -m venv "$root\.venv"
& "$root\.venv\Scripts\python.exe" -m pip install -r "$root\backend\requirements.txt"
Push-Location "$root\frontend"; npm ci; npm run build; Pop-Location
function Ensure-Rule($name,$proto,$port){
  if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol $proto -LocalPort $port -Action Allow | Out-Null
    Write-Host "added firewall rule: $name"
  } else { Write-Host "firewall rule exists: $name" }
}
Ensure-Rule 'XRFD diag (UDP 50999)' UDP 50999
Ensure-Rule 'XRFD dashboard (TCP 10000)' TCP 10000
Write-Host "Setup complete."
```

- [ ] **Step 2: run.ps1 (used by server-manager)**

```powershell
$root = Split-Path -Parent $PSScriptRoot
& "$root\.venv\Scripts\python.exe" -m backend.app
```
Note: run with working directory = `dashboard/` so `backend` is importable. server.toml sets cwd.

- [ ] **Step 3: server.toml (standard contract)**

```toml
[[service]]
name = "xrfd-dashboard"
description = "XR-FreeD UDP->web dashboard (Flask/waitress + Svelte)"
cwd = "dashboard"
command = "powershell -NoProfile -ExecutionPolicy Bypass -File setup/run.ps1"
port = 10000
health = "/health"
pidfile = "data/xrfd.pid"
```

- [ ] **Step 4: Verify install on host**

Run: `cd dashboard && powershell -ExecutionPolicy Bypass -File setup/install.ps1` (as admin)
Expected: venv created, `frontend/dist` built, two firewall rules ensured.

- [ ] **Step 5: Commit**

```bash
git add dashboard/setup dashboard/server.toml
git commit -m "feat: install.ps1 (venv+build+firewall), run.ps1, server.toml contract"
```

---

## Task 16: End-to-end verification against the real device

**Files:** none (verification + docs)

- [ ] **Step 1: Run full backend test suite**

Run: `cd dashboard && .venv\Scripts\python.exe -m pytest backend/tests -v`
Expected: all green.

- [ ] **Step 2: Launch the service**

Run: `cd dashboard && powershell -File setup/run.ps1`
Expected console: `XRFD dashboard on http://0.0.0.0:10000 (waitress)`

- [ ] **Step 3: Verify live data (device 10.10.204.123 broadcasting)**

Run: `curl http://127.0.0.1:10000/api/status`
Expected: `deviceIp` = `10.10.204.123`, `live` = true, `targets` populated within ~6s.

- [ ] **Step 4: Verify in browser**

Open `http://127.0.0.1:10000` and (from another LAN PC) `http://10.10.204.47:10000`. Confirm: live chips update, RateChart moves, target ON/OFF/Edit works, Reboot prompts, theme toggle persists, event log scrolls.

- [ ] **Step 5: Verify SSE liveness**

Run: `curl -N http://127.0.0.1:10000/events` — expect repeated `data: {...}` frames ~every diag.

- [ ] **Step 6: Update README + retire note**

Add a `dashboard/README.md` describing install/run, and a one-line note in `tools/` README that the PowerShell dashboard is now a fallback. Commit.

```bash
git add dashboard/README.md tools
git commit -m "docs: dashboard README + mark ps1 dashboard as fallback"
```

---

## Self-Review Notes

- **Spec coverage:** central Flask+SSE (T8-11), UDP bridge thread (T6-7), protocol/state units (T2-5), Svelte/Vite UI port + RateChart (T12-14), firewall-in-installer + server.toml (T15), tests (T2-11,16), resilience (T6 garbage-packet test, T4 no-device clamp). All spec sections mapped.
- **No placeholders:** every code step contains runnable code; CSS port (T14.3) references exact ps1 line ranges.
- **Type consistency:** `State.snapshot()` keys (deviceIp/ageSec/live/...) match the frontend store defaults (T12) and StatusBar/TargetCard usage (T13); `bridge.send_command(cmd)` signature consistent across T6/T7/T9; `/api/cmd` returns `{ok, reply}` consumed by `postCmd` (T12) reading `.reply`.

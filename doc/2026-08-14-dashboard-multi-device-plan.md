# Dashboard Multi-Device Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대시보드가 LAN에서 발견한 XRFD 장치를 모두 목록으로 유지하고, 드롭다운으로 골라 모니터링·제어할 수 있게 한다. 아울러 Event Log 타임스탬프에 년-월-일을 표시한다.

**Architecture:** `State`에 발견 장치 레지스트리(`{ip: last_seen}`)를 추가하고, 상세 상태(uptime/rx/fps/targets)는 **선택된 장치 한 대만** 추적한다. 두 번째 장치를 조용히 버리던 기존 동작(`_ignored_ips`)을 레지스트리 등록으로 대체한다. 장치 전환 시 상세 카운터를 리셋해 이전 장치의 수치가 새 장치 것으로 오인되지 않게 하고, 제어 명령과 `targets` 주기 조회는 항상 선택된 장치로만 나간다.

**Tech Stack:** Python 3.14 (Flask + waitress, uv), pytest, Svelte 5 + Vite

## Global Constraints

- **`snapshot()`의 기존 키를 제거하거나 이름을 바꾸지 않는다.** `deviceIp`, `ageSec`, `live`, `up`, `rx`, `fps`, `dhcpOk`, `dhcpFail`, `rtr`, `conflict`, `targets`, `log`은 프론트엔드(`frontend/src/stores.js`, `lib/StatusBar.svelte`, `lib/RateChart.svelte`, `lib/TargetCard.svelte`, `lib/EventLog.svelte`)와 기존 pytest가 의존한다. 추가만 한다.
- **`State.device_ip` 필드명을 유지한다.** `backend/app.py:32`(`/health`), `backend/udp_bridge.py:54,69`가 참조한다. 의미가 "발견된 장치"에서 "선택된 장치"로 바뀌므로 주석으로 명시한다.
- **`State`의 모든 공개 메서드는 `self._lock`(RLock) 안에서 공유 상태를 만진다.** UDP bridge 스레드와 Flask 워커 스레드가 동시에 접근한다.
- **`publish()`는 `_lock`을 잡은 채 큐에 넣지 않는다** (`backend/state.py:118-124`의 기존 패턴 유지 — 스냅샷을 먼저 만들고 락 밖에서 배포).
- **진단 broadcast 포맷과 `protocol.py` 파서를 바꾸지 않는다.** 이 plan은 firmware를 건드리지 않는다.
- **기존 pytest가 전부 통과해야 한다.** Task 0을 마친 뒤의 기준선은 **30 passed**이며, 로컬 macOS와 배포 타깃 Linux 양쪽에서 같은 숫자가 나와야 한다. 이 plan은 테스트를 추가만 하고 기존 테스트를 삭제하지 않는다. 단 `test_first_packet_sets_device_and_logs`처럼 동작이 확장되는 테스트는 assertion 추가가 허용된다.
- **테스트는 `cd dashboard && uv run pytest backend/tests -q`로 로컬에서 돌린다.** 배포 서버(ts5-server)에서 테스트를 실행하지 않는다 — `uv run`이 운영 venv를 재동기화한다.
- 코드와 주석은 영어로 쓴다 (`dashboard/backend/`의 기존 관례).

## File Structure

| 파일 | 상태 | 책임 |
|---|---|---|
| `dashboard/backend/state.py` | [modify] | 로그 타임스탬프, 장치 레지스트리, 선택/전환 로직 |
| `dashboard/backend/app.py` | [modify] | `POST /api/device` 선택 엔드포인트 |
| `dashboard/backend/udp_bridge.py` | [modify] | 선택 장치 전환 시 targets 즉시 재조회 |
| `dashboard/backend/tests/test_state.py` | [modify] | 타임스탬프·레지스트리·전환 테스트 |
| `dashboard/backend/tests/test_app.py` | [modify] | `/api/device` 엔드포인트 테스트 |
| `dashboard/backend/tests/test_bridge.py` | [modify] | 장치 전환 시 targets 재조회 테스트 |
| `dashboard/frontend/src/stores.js` | [modify] | `devices` 기본값 추가 |
| `dashboard/frontend/src/lib/api.js` | [modify] | `selectDevice()` + 전환 시 차트 히스토리 리셋 |
| `dashboard/frontend/src/App.svelte` | [modify] | 헤더의 장치 표시를 드롭다운으로 |

`lib/EventLog.svelte`는 백엔드가 만든 문자열(`e.t`)을 그대로 출력하므로 **변경하지 않는다**.

**의존 순서:** Task 0 → Task 1 → Task 2 → Task 3 → Task 4 → Task 5. Task 0과 Task 1은 서로 독립적이며 먼저 배포해도 된다.

---

### Task 0: baseline 테스트를 플랫폼 중립으로 고친다

**Files:**
- Modify: `dashboard/backend/tests/test_bridge.py` (`test_bind_sets_reuseport`)

**Interfaces:**
- Consumes: 없음
- Produces: 없음 (테스트 단언만 고친다)

**배경:** `test_bind_sets_reuseport`가 `getsockopt(SOL_SOCKET, SO_REUSEPORT) == 1`을 단언하는데, 이는 Linux 전용 가정이다. macOS(BSD 계열)는 옵션 상수값 자체(`0x0200` = 512)를 돌려주므로 로컬 개발 머신에서 실패한다 — 실측으로 확인했다 (macOS 29 passed + 1 failed, ts5-server Linux 30 passed). `setsockopt`의 계약은 "0이 아니면 켜짐"이므로 `!= 0`이 원래 정확한 단언이고, 양쪽 플랫폼에서 모두 통과한다. 이 task를 건너뛰면 이후 모든 task에서 실패 1건이 상시 노이즈로 남아 회귀를 구분하기 어려워진다.

- [ ] **Step 1: 현재 실패를 재현한다**

Run: `cd dashboard && uv run pytest backend/tests/test_bridge.py::test_bind_sets_reuseport -v`
Expected (macOS): FAIL — `AssertionError: assert 512 == 1`
Expected (Linux): PASS — 이 경우 Step 2를 그대로 적용하고 Step 3에서 통과를 확인하면 된다 (변경은 양쪽에서 안전하다).

- [ ] **Step 2: 단언을 플랫폼 중립으로 바꾼다**

`dashboard/backend/tests/test_bridge.py`에서 다음 줄을 찾는다:

```python
        assert s.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) == 1
```

다음으로 교체:

```python
        # setsockopt's contract is "nonzero means on" — Linux reports 1 while
        # macOS/BSD reports the option constant itself (0x0200), so a `== 1`
        # assertion is a Linux-only assumption.
        assert s.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) != 0
```

- [ ] **Step 3: 전체 스위트가 통과하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests -q`
Expected: `30 passed` (실패 0건)

- [ ] **Step 4: Commit**

변경 파일: `dashboard/backend/tests/test_bridge.py`

커밋 메시지:

```
test: assert SO_REUSEPORT is nonzero rather than exactly 1

getsockopt은 Linux에서 1을, macOS/BSD에서는 옵션 상수값(0x0200)을
돌려준다. `== 1`은 Linux 전용 가정이라 로컬 개발 머신에서만 실패했다.
setsockopt의 계약은 "0이 아니면 켜짐"이므로 != 0이 정확한 단언이다.
```

---

### Task 1: Event Log 타임스탬프에 날짜 추가

**Files:**
- Modify: `dashboard/backend/state.py:31-34`
- Test: `dashboard/backend/tests/test_state.py`

**Interfaces:**
- Produces: 로그 항목의 `t` 필드 포맷이 `"%H:%M:%S"`에서 `"%Y-%m-%d %H:%M:%S"`로 바뀐다. 소비자는 `lib/EventLog.svelte:4`가 유일하며 문자열을 그대로 출력하므로 프론트 변경은 필요 없다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`dashboard/backend/tests/test_state.py` 맨 위 import에 `datetime`을 추가한다:

```python
from datetime import datetime

from backend.state import State
from backend import protocol
```

파일 끝에 테스트를 추가한다:

```python
def test_log_timestamp_includes_date():
    # Event log spans days on a long-running device; time-only stamps made it
    # impossible to tell which day an entry belongs to.
    st = State()
    st.add_log("info", "hello")
    stamp = st.snapshot()["log"][0]["t"]
    datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")   # raises ValueError on mismatch
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests/test_state.py::test_log_timestamp_includes_date -v`
Expected: FAIL — `ValueError: time data '10:34:21' does not match format '%Y-%m-%d %H:%M:%S'`

- [ ] **Step 3: 포맷을 바꾼다**

`dashboard/backend/state.py:33`을 찾는다:

```python
            self._log.appendleft({"t": datetime.now().strftime("%H:%M:%S"),
                                  "k": kind, "m": msg})
```

다음으로 교체:

```python
            self._log.appendleft({"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                  "k": kind, "m": msg})
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests -q`
Expected: `31 passed` (Task 0 이후 기준선 30 + 신규 1)

- [ ] **Step 5: Commit**

변경 파일: `dashboard/backend/state.py`, `dashboard/backend/tests/test_state.py`

커밋 메시지:

```
feat: show full date in dashboard event log timestamps

장비가 며칠씩 연속 가동되면 시:분:초만으로는 로그 항목이 어느 날짜인지
알 수 없었다. YYYY-MM-DD HH:MM:SS로 변경.
```

---

### Task 2: 발견 장치 레지스트리와 선택 전환

**Files:**
- Modify: `dashboard/backend/state.py` (`__init__` `:10-29`, `update_from_diag` `:36-48`, `snapshot` `:85-107`)
- Test: `dashboard/backend/tests/test_state.py`

**Interfaces:**
- Produces: `State._devices` — `{ip: last_seen_walltime}` dict.
- Produces: `State.select_device(ip) -> bool` — 선택 전환. 한 번도 본 적 없는 IP면 `False`, 성공(또는 이미 선택된 IP)이면 `True`. Task 3의 `POST /api/device`가 호출한다.
- Produces: `snapshot()["devices"]` — `[{"ip": str, "ageSec": int, "live": bool}]`, IP 문자열 오름차순 정렬. Task 5의 프론트가 소비한다.
- Changed: `State.device_ip`의 의미가 "발견된 장치"에서 **"선택된(모니터링 중인) 장치"**로 바뀐다. 필드명과 `snapshot()["deviceIp"]` 키는 그대로다.
- Removed: `State._ignored_ips` — 레지스트리가 대체한다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`dashboard/backend/tests/test_state.py` 끝에 추가한다:

```python
DEV2 = "10.10.204.124"


def test_second_device_is_registered_not_discarded():
    # Replacing a converter means both units are briefly live. The old build
    # silently dropped the second one; it must show up as selectable instead.
    st = State()
    feed(st, "XRFD up=10 ms=10000 ip=%s rx=5 dhcp=0/0 rtr=Y" % DEV)
    feed(st, "XRFD up=3 ms=3000 ip=%s rx=1 dhcp=0/0 rtr=Y" % DEV2, src=DEV2)
    snap = st.snapshot()
    assert snap["deviceIp"] == DEV                       # selection unchanged
    assert {d["ip"] for d in snap["devices"]} == {DEV, DEV2}
    assert snap["up"] == 10 and snap["rx"] == 5          # detail still the selected one


def test_unselected_device_does_not_overwrite_detail():
    st = State()
    feed(st, "XRFD up=100 ms=100000 ip=%s rx=6000 dhcp=0/0 rtr=Y" % DEV)
    feed(st, "XRFD up=1 ms=1000 ip=%s rx=0 dhcp=0/0 rtr=N" % DEV2, src=DEV2)
    snap = st.snapshot()
    assert snap["up"] == 100 and snap["rx"] == 6000 and snap["rtr"] == "Y"
    # a second device must not trip the reboot heuristic on the selected one
    assert not any("REBOOT" in e["m"] for e in snap["log"])


def test_select_device_switches_and_resets_detail():
    st = State()
    feed(st, "XRFD up=100 ms=100000 ip=%s rx=6000 dhcp=3/1 rtr=Y" % DEV)
    feed(st, "XRFD up=7 ms=7000 ip=%s rx=42 dhcp=0/0 rtr=N" % DEV2, src=DEV2)
    assert st.select_device(DEV2) is True
    snap = st.snapshot()
    assert snap["deviceIp"] == DEV2
    # stale counters from the previous device must not linger
    assert snap["up"] == 0 and snap["rx"] == 0 and snap["fps"] == 0
    assert snap["dhcpOk"] == 0 and snap["dhcpFail"] == 0 and snap["rtr"] == "?"
    assert snap["targets"] == []
    assert snap["live"] is False          # nothing heard from it *since* the switch


def test_select_device_then_next_diag_populates():
    st = State()
    feed(st, "XRFD up=100 ms=100000 ip=%s rx=6000 dhcp=0/0 rtr=Y" % DEV)
    feed(st, "XRFD up=7 ms=7000 ip=%s rx=42 dhcp=0/0 rtr=N" % DEV2, src=DEV2)
    st.select_device(DEV2)
    feed(st, "XRFD up=8 ms=8000 ip=%s rx=100 dhcp=0/0 rtr=N" % DEV2, src=DEV2)
    snap = st.snapshot()
    assert snap["up"] == 8 and snap["rx"] == 100 and snap["live"] is True


def test_select_unknown_device_rejected():
    st = State()
    feed(st, "XRFD up=10 ms=10000 ip=%s rx=5 dhcp=0/0 rtr=Y" % DEV)
    assert st.select_device("10.10.204.199") is False
    assert st.snapshot()["deviceIp"] == DEV


def test_device_found_logged_once_per_ip():
    st = State()
    for _ in range(3):
        feed(st, "XRFD up=10 ms=10000 ip=%s rx=5 dhcp=0/0 rtr=Y" % DEV2, src=DEV2)
    found = [e for e in st.snapshot()["log"] if e["m"].startswith("device found")]
    assert len(found) == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests/test_state.py -q`
Expected: 6개 신규 테스트가 실패한다. 첫 실패는 `KeyError: 'devices'` 또는 `AttributeError: 'State' object has no attribute 'select_device'`.

- [ ] **Step 3: __init__에 레지스트리를 추가한다**

`dashboard/backend/state.py:12-13`을 찾는다:

```python
        self.device_ip = ""
        self.last_seen = 0.0          # wall time (time.time()) of last diag
```

다음으로 교체:

```python
        self.device_ip = ""           # currently *selected* device (detail below is its)
        self.last_seen = 0.0          # wall time (time.time()) of last diag
        self._devices = {}            # ip -> wall time of that device's last diag
```

그리고 `dashboard/backend/state.py:27`의 다음 줄을 삭제한다:

```python
        self._ignored_ips = set()
```

- [ ] **Step 4: update_from_diag를 레지스트리 기반으로 바꾼다**

`dashboard/backend/state.py:40-48`을 찾는다:

```python
        with self._lock:
            # ignore a second XRFD device while the current one is live
            if (self.device_ip and src_ip != self.device_ip
                    and (now - self.last_seen) <= config.DEVICE_LIVE_S):
                if src_ip not in self._ignored_ips:
                    self._ignored_ips.add(src_ip)
                    self.add_log("warn", "ignoring second XRFD device at %s (active: %s)"
                                 % (src_ip, self.device_ip))
                return
```

다음으로 교체:

```python
        with self._lock:
            # Every device that broadcasts stays in the registry so the UI can
            # offer it; only the selected one drives the detail fields below.
            if src_ip not in self._devices:
                self.add_log("info", "device found: %s" % src_ip)
            self._devices[src_ip] = now

            if not self.device_ip:
                self.device_ip = src_ip
            elif src_ip != self.device_ip:
                return
```

**주의:** 아래에 이어지는 기존 블록(`dashboard/backend/state.py:56-57`)에 첫 발견 로그가 중복으로 남아 있다:

```python
            if not self.device_ip:
                self.add_log("info", "device found: %s" % src_ip)
```

이 두 줄을 삭제한다 — 위에서 이미 로그했고, 이 시점에는 `device_ip`가 항상 채워져 있어 조건이 죽은 코드가 된다.

- [ ] **Step 5: select_device()를 추가한다**

`update_from_diag` 정의가 끝나는 곳(`snapshot` 정의 바로 앞)에 추가한다:

```python
    def select_device(self, ip):
        """Point the dashboard at a different discovered device.

        Returns False if that IP has never been heard from. Detail counters are
        cleared so the previous device's uptime/rx/fps can't be misread as the
        new one's, and so the reboot heuristic doesn't fire on the switch.
        """
        with self._lock:
            if ip not in self._devices:
                return False
            if ip == self.device_ip:
                return True
            self.device_ip = ip
            self.last_seen = 0.0
            self.up = 0
            self.rx = 0
            self.fps = 0.0
            self.dhcp_ok = 0
            self.dhcp_fail = 0
            self.rtr = "?"
            self.conflict = False
            self.diag_state = {}
            self.targets = []
            self._prev_rx = -1
            self._prev_ms = -1
            self._prev_time = None
            self.add_log("info", "switched to %s" % ip)
            return True
```

- [ ] **Step 6: snapshot()에 devices를 추가한다**

`dashboard/backend/state.py:86-90`의 age 계산부를 찾는다:

```python
        with self._lock:
            if self.last_seen:
                age = int(min(999999, time.time() - self.last_seen))
            else:
                age = 999999
```

다음으로 교체 (`now`를 한 번만 읽어 목록과 age가 같은 시점을 쓰게 한다):

```python
        with self._lock:
            now = time.time()
            if self.last_seen:
                age = int(min(999999, now - self.last_seen))
            else:
                age = 999999
            devices = [{"ip": ip,
                        "ageSec": int(min(999999, now - seen)),
                        "live": (now - seen) <= config.DEVICE_LIVE_S}
                       for ip, seen in sorted(self._devices.items())]
```

그리고 반환 dict(`dashboard/backend/state.py:96` 부근)의 `"deviceIp": self.device_ip,` 바로 아래에 한 줄 추가한다:

```python
                "deviceIp": self.device_ip,
                "devices": devices,
```

- [ ] **Step 7: 테스트가 통과하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests -q`
Expected: `37 passed` (31 + 신규 6)

- [ ] **Step 8: _ignored_ips 잔여 참조가 없는지 확인한다**

Run: `cd dashboard && grep -rn "_ignored_ips" backend/`
Expected: 출력 없음

- [ ] **Step 9: Commit**

변경 파일: `dashboard/backend/state.py`, `dashboard/backend/tests/test_state.py`

커밋 메시지:

```
feat: track every discovered XRFD device, make selection switchable

기존에는 두 번째 장치를 _ignored_ips에 넣고 버려서, 장비 교체 중 새
장비를 대시보드로 볼 수 없었다. 이제 발견된 장치를 모두 레지스트리에
남기고 select_device()로 모니터링 대상을 전환한다.

전환 시 상세 카운터를 리셋한다 — 이전 장치의 uptime/rx가 남으면 새 장치
수치로 오인되고 reboot 오탐도 난다.
```

---

### Task 3: 장치 선택 API

**Files:**
- Modify: `dashboard/backend/app.py` (`create_app` 내부, `/api/cmd` 정의 뒤)
- Test: `dashboard/backend/tests/test_app.py`

**Interfaces:**
- Consumes: `State.select_device(ip) -> bool`, `snapshot()["devices"]` (Task 2)
- Produces: `POST /api/device` — 요청 `{"ip": "10.10.204.124"}`, 성공 `200 {"ok": true, "selected": ip}`, `ip` 누락 시 `400 {"error": ...}`, 미발견 IP는 `404 {"error": ...}`. Task 5의 `selectDevice()`가 호출한다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`dashboard/backend/tests/test_app.py` 끝에 추가한다:

```python
def test_select_device_switches_selection(app_and_state):
    app, st, _ = app_and_state
    from backend import protocol
    st.update_from_diag(protocol.parse_diag(
        "XRFD up=1 ms=1000 ip=10.10.204.123 rx=0 dhcp=0/0 rtr=Y"), "10.10.204.123")
    st.update_from_diag(protocol.parse_diag(
        "XRFD up=1 ms=1000 ip=10.10.204.124 rx=0 dhcp=0/0 rtr=Y"), "10.10.204.124")
    c = app.test_client()
    r = c.post("/api/device", json={"ip": "10.10.204.124"})
    assert r.status_code == 200
    assert r.get_json()["selected"] == "10.10.204.124"
    assert c.get("/api/status").get_json()["deviceIp"] == "10.10.204.124"


def test_select_device_rejects_unknown(app_and_state):
    app, st, _ = app_and_state
    from backend import protocol
    st.update_from_diag(protocol.parse_diag(
        "XRFD up=1 ms=1000 ip=10.10.204.123 rx=0 dhcp=0/0 rtr=Y"), "10.10.204.123")
    r = app.test_client().post("/api/device", json={"ip": "10.10.204.199"})
    assert r.status_code == 404
    assert app.test_client().get("/api/status").get_json()["deviceIp"] == "10.10.204.123"


def test_select_device_requires_ip(app_and_state):
    app, _, _ = app_and_state
    r = app.test_client().post("/api/device", json={})
    assert r.status_code == 400


def test_status_lists_discovered_devices(app_and_state):
    app, st, _ = app_and_state
    from backend import protocol
    st.update_from_diag(protocol.parse_diag(
        "XRFD up=1 ms=1000 ip=10.10.204.123 rx=0 dhcp=0/0 rtr=Y"), "10.10.204.123")
    devices = app.test_client().get("/api/status").get_json()["devices"]
    assert [d["ip"] for d in devices] == ["10.10.204.123"]
    assert devices[0]["live"] is True
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests/test_app.py -q`
Expected: 선택 관련 3개가 `404` (라우트 없음)로 실패. `test_status_lists_discovered_devices`는 Task 2 덕에 이미 통과한다.

- [ ] **Step 3: 엔드포인트를 추가한다**

`dashboard/backend/app.py`의 `/api/cmd` 정의가 끝나는 곳, `@app.get("/events")` 바로 앞에 추가한다:

```python
    @app.post("/api/device")
    def api_device():
        data = request.get_json(silent=True) or {}
        ip = data.get("ip")
        if not ip:
            return jsonify({"error": "missing 'ip'"}), 400
        if not state.select_device(ip):
            return jsonify({"error": "unknown device: %s" % ip}), 404
        state.publish()          # push the switch to every open SSE client
        return jsonify({"ok": True, "selected": ip})
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests -q`
Expected: `41 passed` (37 + 신규 4)

- [ ] **Step 5: Commit**

변경 파일: `dashboard/backend/app.py`, `dashboard/backend/tests/test_app.py`

커밋 메시지:

```
feat: add POST /api/device to switch monitored device

발견된 장치 중 하나를 선택하는 엔드포인트. 전환 후 publish()로 열려 있는
모든 SSE 클라이언트에 즉시 반영한다.
```

---

### Task 4: 장치 전환 시 targets 즉시 재조회

**Files:**
- Modify: `dashboard/backend/udp_bridge.py` (`__init__` `:10-14`, `_maybe_refresh_targets` `:53-64`)
- Test: `dashboard/backend/tests/test_bridge.py`

**Interfaces:**
- Consumes: `State.device_ip` (Task 2에서 의미가 "선택된 장치"로 바뀜)
- Produces: `UdpBridge._targets_for_ip` — 마지막으로 targets를 조회한 장치 IP. 선택이 바뀌면 `TARGETS_REFRESH_S` 대기를 건너뛴다.

**왜 필요한가:** `select_device()`가 `state.targets`를 비우는데, bridge의 `_last_targets_refresh` 타이머는 그대로라 최대 `TARGETS_REFRESH_S`(30초) 동안 target 카드가 빈 채로 남는다. 또한 타이머가 만료되지 않았으면 **이전 장치에게 물어본 결과가 새 장치 것으로 표시될** 여지도 있다.

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`dashboard/backend/tests/test_bridge.py` 끝에 추가한다:

```python
def test_targets_refetched_immediately_after_device_switch(monkeypatch):
    from backend import protocol
    from backend.state import State
    from backend.udp_bridge import UdpBridge

    st = State()
    st.update_from_diag(protocol.parse_diag(
        "XRFD up=1 ms=1000 ip=10.10.204.123 rx=0 dhcp=0/0 rtr=Y"), "10.10.204.123")
    st.update_from_diag(protocol.parse_diag(
        "XRFD up=1 ms=1000 ip=10.10.204.124 rx=0 dhcp=0/0 rtr=Y"), "10.10.204.124")

    br = UdpBridge(st)
    calls = []
    monkeypatch.setattr(br, "send_command",
                        lambda cmd, timeout=None: calls.append((cmd, st.device_ip))
                        or "t0 on 10.10.204.184:50001")

    br._maybe_refresh_targets()                 # first poll for .123
    assert calls == [("targets", "10.10.204.123")]

    br._maybe_refresh_targets()                 # within the refresh window: no-op
    assert len(calls) == 1

    st.select_device("10.10.204.124")
    br._maybe_refresh_targets()                 # switch must bypass the timer
    assert calls[-1] == ("targets", "10.10.204.124")
    assert len(calls) == 2
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests/test_bridge.py::test_targets_refetched_immediately_after_device_switch -v`
Expected: FAIL — 마지막 assertion에서 `len(calls) == 1` (전환 후에도 타이머에 막혀 조회하지 않는다)

- [ ] **Step 3: bridge에 추적 필드를 추가한다**

`dashboard/backend/udp_bridge.py:14`를 찾는다:

```python
        self._last_targets_refresh = 0.0
```

다음으로 교체:

```python
        self._last_targets_refresh = 0.0
        self._targets_for_ip = ""      # which device the cached targets belong to
```

- [ ] **Step 4: _maybe_refresh_targets에 전환 감지를 넣는다**

`dashboard/backend/udp_bridge.py:53-58`을 찾는다:

```python
    def _maybe_refresh_targets(self):
        if not self.state.device_ip:
            return
        if time.time() - self._last_targets_refresh < config.TARGETS_REFRESH_S:
            return
        self._last_targets_refresh = time.time()
```

다음으로 교체:

```python
    def _maybe_refresh_targets(self):
        ip = self.state.device_ip
        if not ip:
            return
        # A switch invalidates the cached target list — refetch now instead of
        # showing an empty card (or the previous device's targets) for up to
        # TARGETS_REFRESH_S.
        if ip != self._targets_for_ip:
            self._targets_for_ip = ip
            self._last_targets_refresh = 0.0
        if time.time() - self._last_targets_refresh < config.TARGETS_REFRESH_S:
            return
        self._last_targets_refresh = time.time()
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests -q`
Expected: `42 passed` (41 + 신규 1)

- [ ] **Step 6: 제어 명령이 선택 장치로만 나가는지 확인한다**

Run: `cd dashboard && grep -n "device_ip" backend/udp_bridge.py`
Expected: `_maybe_refresh_targets`의 `ip = self.state.device_ip` 1곳과 `send_command`의 `ip = self.state.device_ip` 1곳 = 총 2곳. `send_command`가 항상 선택된 장치로만 보내므로 별도 변경은 필요 없다.

- [ ] **Step 7: Commit**

변경 파일: `dashboard/backend/udp_bridge.py`, `dashboard/backend/tests/test_bridge.py`

커밋 메시지:

```
fix: refetch targets immediately when the selected device changes

select_device()가 state.targets를 비우는데 bridge의 refresh 타이머는
그대로라, 전환 후 최대 30초 동안 target 카드가 비거나 이전 장치의
목록이 새 장치 것처럼 남았다.
```

---

### Task 5: 프론트엔드 드롭다운

**Files:**
- Modify: `dashboard/frontend/src/stores.js`
- Modify: `dashboard/frontend/src/lib/api.js`
- Modify: `dashboard/frontend/src/App.svelte`

**Interfaces:**
- Consumes: `snapshot()["devices"]` (Task 2), `POST /api/device` (Task 3)
- Produces: `selectDevice(ip)` (from `lib/api.js`) — 전환 요청 후 `fpsHistory`를 비운다.

- [ ] **Step 1: store 기본값에 devices를 추가한다**

`dashboard/frontend/src/stores.js`의 `status` 초기값을 찾는다:

```js
export const status = writable({
  deviceIp: '', ageSec: 999999, live: false, up: 0, rx: 0, fps: 0,
  dhcpOk: 0, dhcpFail: 0, rtr: '?', conflict: false, targets: [], log: [],
})
```

다음으로 교체:

```js
export const status = writable({
  deviceIp: '', devices: [], ageSec: 999999, live: false, up: 0, rx: 0, fps: 0,
  dhcpOk: 0, dhcpFail: 0, rtr: '?', conflict: false, targets: [], log: [],
})
```

- [ ] **Step 2: api.js에 selectDevice를 추가한다**

`dashboard/frontend/src/lib/api.js`의 `postCmd` 정의 뒤에 추가한다:

```js
export async function selectDevice(ip) {
  const r = await fetch('/api/device', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ip }),
  })
  // The chart plots the *selected* device's fps; carrying the old series over
  // would draw a cliff that looks like a fault on the new device.
  if (r.ok) fpsHistory.set([])
  return r.ok
}
```

- [ ] **Step 3: App.svelte에서 selectDevice를 import한다**

`dashboard/frontend/src/App.svelte:4`를 찾는다:

```svelte
  import { connect, postCmd } from './lib/api.js'
```

다음으로 교체:

```svelte
  import { connect, postCmd, selectDevice } from './lib/api.js'
```

- [ ] **Step 4: 헤더의 장치 표시를 드롭다운으로 바꾼다**

`dashboard/frontend/src/App.svelte`의 다음 줄을 찾는다:

```svelte
  <span class="dev">device: {$status.deviceIp || '-'}{$status.live ? '' : `  (no signal ${$status.ageSec}s)`}</span>
```

다음으로 교체 (장치가 2대 이상일 때만 드롭다운을 보여준다 — 1대뿐일 때 고를 것 없는 select는 잡음이다):

```svelte
  <span class="dev">
    device:
    {#if $status.devices.length > 1}
      <select value={$status.deviceIp}
              on:change={(e) => selectDevice(e.currentTarget.value)}>
        {#each $status.devices as d (d.ip)}
          <option value={d.ip}>{d.ip}{d.live ? '' : ` — no signal ${d.ageSec}s`}</option>
        {/each}
      </select>
    {:else}
      {$status.deviceIp || '-'}
    {/if}
    {$status.live ? '' : `  (no signal ${$status.ageSec}s)`}
  </span>
```

- [ ] **Step 5: 빌드가 통과하는지 확인한다**

Run: `cd dashboard/frontend && npm run build`
Expected: 에러 없이 `dist/`가 생성된다. Svelte 컴파일 경고가 나오면 읽고 판단한다 — `value` 바인딩 관련 경고가 뜨면 `<select>`에 `bind:value` 대신 위처럼 `value` + `on:change`를 쓰는 형태가 맞는지 확인한다.

- [ ] **Step 6: 백엔드 테스트가 여전히 통과하는지 확인한다**

Run: `cd dashboard && uv run pytest backend/tests -q`
Expected: `42 passed` (Task 5는 프론트엔드만 바꾸므로 개수가 늘지 않는다)

- [ ] **Step 7: 로컬에서 실제로 띄워 확인한다**

터미널 1:
```bash
cd dashboard && uv run python -m backend.app
```

터미널 2 — 가짜 장치 두 대의 진단 broadcast를 흉내 낸다 (loopback으로 보내 브라우저에서 드롭다운이 뜨는지 본다):
```bash
python3 -c "
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for i in range(20):
    for ip in ('127.0.0.1',):
        s.sendto(b'XRFD up=%d ms=%d ip=10.10.204.123 rx=%d dhcp=0/0 rtr=Y t0=A,%d,0,0'
                 % (i, i*1000, i*60, i*60), ('127.0.0.1', 50999))
    time.sleep(1)
"
```

브라우저에서 `http://localhost:10000` 확인 항목:
- Event log 타임스탬프에 `2026-08-14 ...` 형태로 날짜가 보인다
- 장치가 1대일 땐 헤더에 IP가 텍스트로만 보인다

**주의:** 위 스크립트는 같은 소스 IP(`127.0.0.1`)로만 보내므로 장치는 1대로 잡힌다. 드롭다운 자체는 실장비 2대 또는 서로 다른 소스 IP가 필요하니, 최종 확인은 아래 "실환경 검증"에서 한다.

- [ ] **Step 8: Commit**

변경 파일: `dashboard/frontend/src/stores.js`, `dashboard/frontend/src/lib/api.js`, `dashboard/frontend/src/App.svelte`

커밋 메시지:

```
feat: device dropdown in dashboard header

장치가 2대 이상 발견되면 헤더에서 골라 전환할 수 있다. 전환 시
fps 차트 히스토리를 비운다 — 이전 장치의 곡선이 이어지면 새 장비에
장애가 난 것처럼 보인다.
```

---

## 배포 (전 task 완료 후, 운영자 수행)

대시보드는 ts5-server의 `/opt/xr-freed-to-udp`에 systemd 유닛(`xr-freed-to-udp.service`)으로 배포돼 있다. `docs/XR-FreeD_to_UDP/STATUS.md` 참고.

Claude는 이 구간에서 read-only 확인만 하고, 아래 mutation 명령은 운영자가 직접 실행한다.

```bash
ssh ts5-t                                    # 배포 서버 접속
cd /opt/xr-freed-to-udp                      # 배포 경로
git pull origin main                         # 변경 가져오기
bash dashboard/setup/install.sh              # uv sync + npm run build (프론트 재빌드 필수)
sudo systemctl restart xr-freed-to-udp       # 서비스 재시작
systemctl status xr-freed-to-udp --no-pager  # active(running) 확인
curl -s localhost:10000/health               # {"status":"ok"} 확인
```

`install.sh`를 건너뛰면 프론트엔드 `dist/`가 예전 번들 그대로라 드롭다운이 나타나지 않는다.

## 실환경 검증

- [ ] 브라우저에서 `http://10.10.204.229:10000` 접속 — Event log에 `YYYY-MM-DD HH:MM:SS` 표시 확인
- [ ] 장치가 `10.10.204.123` 1대뿐인 동안에는 헤더가 텍스트로만 보이는지 확인 (드롭다운 없음)
- [ ] 새 장비를 같은 LAN에 연결 → 헤더에 드롭다운이 나타나고 두 IP가 모두 목록에 뜨는지 확인
- [ ] 드롭다운으로 새 장비 선택 → uptime/rx/fps가 0에서 다시 채워지고, target 카드가 30초를 기다리지 않고 곧바로 새 장비의 target으로 갱신되는지 확인
- [ ] Event log에 `switched to <ip>` 항목이 남는지 확인
- [ ] 새 장비를 선택한 상태에서 target 토글이 **새 장비에** 적용되는지 확인 (기존 장비가 아니라)

## 알려진 한계 (의도된 것)

- **선택되지 않은 장치의 상세 상태는 추적하지 않는다.** 드롭다운에는 IP와 마지막 수신 경과(`ageSec`)만 뜨고, fps나 target 상태는 선택해야 보인다. 두 장비를 나란히 비교하려면 전환해 가며 봐야 한다 — 상시 다중 모니터링이 필요해지면 `State`를 장치별 dict로 확장하는 별도 작업이 필요하다.
- **장치는 목록에서 자동으로 사라지지 않는다.** 교체 후 옛 장비를 꺼도 `ageSec`이 계속 늘어난 채 드롭다운에 남는다 (재부팅 중일 수 있어 의도적으로 유지). 대시보드를 재시작하면 목록이 비워진다.
- **전환 직후 짧게 `live: false`로 보인다.** 다음 진단 broadcast(최대 5초)가 올 때까지는 새 장치로부터 들은 것이 없기 때문이다.

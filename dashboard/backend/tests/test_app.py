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


def test_events_streams_initial_snapshot(app_and_state):
    app, st, _ = app_and_state
    c = app.test_client()
    r = c.get("/events", headers={"Accept": "text/event-stream"}, buffered=False)
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/event-stream")
    chunk = next(iter(r.response))
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    assert text.startswith("data: ")
    assert '"deviceIp"' in text


def test_index_served_when_dist_missing_returns_helpful_503(app_and_state, monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "FRONTEND_DIST", config.FRONTEND_DIST / "nonexistent")
    r = app_and_state[0].test_client().get("/")
    assert r.status_code == 503
    assert b"npm run build" in r.data


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

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

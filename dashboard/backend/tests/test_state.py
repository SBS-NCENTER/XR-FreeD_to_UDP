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

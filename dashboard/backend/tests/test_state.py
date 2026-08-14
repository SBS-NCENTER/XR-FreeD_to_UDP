from datetime import datetime

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


def test_log_timestamp_includes_date():
    # Event log spans days on a long-running device; time-only stamps made it
    # impossible to tell which day an entry belongs to.
    st = State()
    st.add_log("info", "hello")
    stamp = st.snapshot()["log"][0]["t"]
    datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")   # raises ValueError on mismatch


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

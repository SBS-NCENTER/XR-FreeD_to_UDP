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

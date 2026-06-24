import socket
import time

from backend.state import State
from backend.udp_bridge import UdpBridge
from backend import config

DEV_LINE = b"XRFD up=1 ms=1000 ip=127.0.0.1 rx=0 dhcp=0/0 rtr=Y"


def test_bridge_receives_and_updates_state(monkeypatch):
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


def test_send_command_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "CTRL_PORT", 55001)
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


def test_bind_sets_reuseport(monkeypatch):
    """DIAG socket must set SO_REUSEPORT so the CLI tools (xrfd_ctl/xrfd_monitor)
    can coexist on the diag port while the dashboard is running."""
    if not hasattr(socket, "SO_REUSEPORT"):
        import pytest
        pytest.skip("SO_REUSEPORT not available on this platform")
    monkeypatch.setattr(config, "DIAG_PORT", 55996)
    s = UdpBridge(State())._bind()
    try:
        assert s.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) == 1
    finally:
        s.close()

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

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

    def _maybe_refresh_targets(self):
        pass


@pytest.fixture
def app_and_state():
    st = State()
    bridge = FakeBridge()
    app = create_app(st, bridge)
    app.testing = True
    return app, st, bridge

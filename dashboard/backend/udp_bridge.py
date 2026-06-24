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
        if hasattr(socket, "SO_REUSEPORT"):   # coexist with xrfd_ctl/xrfd_monitor on DIAG_PORT
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
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

"""Thread-safe shared dashboard state (ported from tools/xrfd_dashboard.ps1)."""
import threading
import time
from collections import deque
from datetime import datetime

from . import config


class State:
    def __init__(self):
        self._lock = threading.RLock()
        self.device_ip = ""
        self.last_seen = 0.0          # wall time (time.time()) of last diag
        self.up = 0
        self.rx = 0
        self.fps = 0.0
        self.dhcp_ok = 0
        self.dhcp_fail = 0
        self.rtr = "?"
        self.conflict = False
        self.diag_state = {}          # "0" -> {state,ok,fail,skip}
        self.targets = []             # [{n,on,ip,port}]
        self._log = deque(maxlen=config.LOG_MAXLEN)
        self._prev_rx = -1
        self._prev_ms = -1
        self._prev_time = None
        self._ignored_ips = set()
        self._subscribers = set()

    def add_log(self, kind, msg):
        with self._lock:
            self._log.appendleft({"t": datetime.now().strftime("%H:%M:%S"),
                                  "k": kind, "m": msg})

    def update_from_diag(self, parsed, src_ip):
        if parsed is None:
            return
        now = time.time()
        with self._lock:
            # ignore a second XRFD device while the current one is live
            if (self.device_ip and src_ip != self.device_ip
                    and (now - self.last_seen) <= config.DEVICE_LIVE_S):
                if src_ip not in self._ignored_ips:
                    self._ignored_ips.add(src_ip)
                    self.add_log("warn", "ignoring second XRFD device at %s (active: %s)"
                                 % (src_ip, self.device_ip))
                return

            up, rx, ms = parsed["up"], parsed["rx"], parsed["ms"]
            # real reboot: up AND rx both reset (millis wrap resets up only)
            if self.device_ip and up < self.up and rx < self.rx:
                self.add_log("warn", "DEVICE REBOOTED (uptime reset: %ds -> %ds)" % (self.up, up))
                self._prev_rx = -1
                self._prev_ms = -1
            if not self.device_ip:
                self.add_log("info", "device found: %s" % src_ip)

            self.device_ip = src_ip
            self.last_seen = now
            self.up, self.rx = up, rx
            self.dhcp_ok, self.dhcp_fail = parsed["dhcp_ok"], parsed["dhcp_fail"]
            self.rtr = parsed["rtr"]
            if parsed["conflict"] and not self.conflict:
                self.add_log("warn", "IP CONFLICT detected on device LAN!")
            self.conflict = parsed["conflict"]

            # fps from device ms when available, else PC-clock fallback
            if ms is not None and self._prev_ms >= 0 and ms > self._prev_ms and rx >= self._prev_rx:
                dms = ms - self._prev_ms
                if dms >= 1000:
                    self.fps = round((rx - self._prev_rx) * 1000.0 / dms, 2)
            elif ms is None and self._prev_rx >= 0 and rx > self._prev_rx and self._prev_time:
                dt = now - self._prev_time
                if dt > 0.5:
                    self.fps = round((rx - self._prev_rx) / dt, 2)
            self._prev_rx = rx
            self._prev_ms = ms if ms is not None else -1
            self._prev_time = now

            self.diag_state = parsed["targets"]

    def snapshot(self):
        with self._lock:
            if self.last_seen:
                age = int(min(999999, time.time() - self.last_seen))
            else:
                age = 999999
            tl = []
            for t in self.targets:
                d = self.diag_state.get(str(t["n"]))
                entry = {"n": t["n"], "on": t["on"], "ip": t["ip"], "port": t["port"],
                         "state": "off", "ok": 0, "fail": 0, "skip": 0}
                if d:
                    entry.update(state=d["state"], ok=d["ok"], fail=d["fail"], skip=d["skip"])
                tl.append(entry)
            return {
                "deviceIp": self.device_ip,
                "ageSec": age,
                "live": bool(self.device_ip) and age <= config.DEVICE_LIVE_S,
                "up": self.up, "rx": self.rx, "fps": self.fps,
                "dhcpOk": self.dhcp_ok, "dhcpFail": self.dhcp_fail,
                "rtr": self.rtr, "conflict": self.conflict,
                "targets": tl,
                "log": list(self._log)[:40],
            }

    def subscribe(self):
        import queue
        q = queue.Queue(maxsize=10)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)

    def publish(self):
        snap = self.snapshot()
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(snap)
            except Exception:
                pass   # slow/full consumer: drop this update, never block the bridge

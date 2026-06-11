#!/usr/bin/env bash
# XRFD diagnostics monitor (macOS / Linux) - prints the device's 5s status
# broadcast (UDP 50999) with timestamps. Ctrl+C to stop.
# (bash cannot bind/receive UDP datagrams natively, so the socket part is
#  a python3 stdlib one-liner - preinstalled on macOS and virtually all Linux)
exec python3 - "$@" <<'PYEOF'
import socket, sys, time
port = int(sys.argv[1]) if len(sys.argv) > 1 else 50999
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
if hasattr(socket, "SO_REUSEPORT"):
    try: s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except OSError: pass
s.bind(("", port))
print(f"Listening for XRFD diagnostics on UDP {port} ... (Ctrl+C to stop)")
try:
    while True:
        data, addr = s.recvfrom(2048)
        line = data.decode("ascii", "replace")
        print(f"[{time.strftime('%H:%M:%S')}] [{addr[0]}] {line}", flush=True)
except KeyboardInterrupt:
    pass
PYEOF

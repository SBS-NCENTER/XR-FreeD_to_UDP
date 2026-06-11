#!/usr/bin/env bash
# XRFD remote control (macOS / Linux).
# Usage: ./xrfd_ctl.sh "target 1 off" [device-ip]
#        ./xrfd_ctl.sh "status"
# Without device-ip the device is auto-discovered from its diag broadcast
# (UDP 50999, max 12s wait - needs no firewall rule on most setups; see
#  xrfd_firewall_setup.sh if discovery times out).
if [ -z "$1" ]; then
  echo "Usage: $0 \"<command>\" [device-ip]"
  echo "Commands: status | targets | target <0-3> on|off|ip <a.b.c.d>|port <n>|set <ip> <port> | reboot"
  exit 1
fi
exec python3 - "$1" "$2" <<'PYEOF'
import socket, sys
cmd = sys.argv[1]
ip = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ""
if not ip:
    print("Discovering device via diag broadcast on UDP 50999 (max 12s)...")
    d = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    d.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try: d.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError: pass
    d.bind(("", 50999))
    d.settimeout(12)
    try:
        _, addr = d.recvfrom(2048)
        ip = addr[0]
        print(f"Found device: {ip}")
    except OSError:
        print("ERROR: no diag broadcast received - pass the device IP explicitly.")
        sys.exit(1)
    finally:
        d.close()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
s.sendto(cmd.encode(), (ip, 50998))
try:
    data, _ = s.recvfrom(2048)
    print(data.decode("ascii", "replace"))
except OSError:
    print(f"ERROR: no reply from {ip}:50998 within 3s")
    sys.exit(1)
PYEOF

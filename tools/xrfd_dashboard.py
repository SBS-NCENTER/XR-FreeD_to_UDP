#!/usr/bin/env python3
"""XRFD Dashboard - cross-platform web GUI bridge (macOS / Linux, stdlib only).

Same protocol and UI as tools/xrfd_dashboard.ps1 (Windows version):
  - caches the device's diag broadcast (UDP 50999)  -> live status
  - relays control commands to the device (UDP 50998)
  - serves the dashboard at http://<this-host>:10000

Run:  ./xrfd_dashboard.sh   (or: python3 xrfd_dashboard.py [--port 10000])

NOTE: the embedded HTML must be kept in sync with xrfd_dashboard.ps1.
"""

import argparse
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

CTRL_PORT = 50998
DIAG_PORT = 50999

S = {
    "deviceIp": "", "lastSeen": 0.0,
    "up": 0, "rx": 0, "dhcpOk": 0, "dhcpFail": 0, "rtr": "?",
    "fps": 0.0, "prevRx": -1, "prevMs": -1,
    "diag": {},      # per-target state from broadcast: {"0": {state, ok, fail, skip}}
    "targets": [],   # config from 'targets' command: [{n, on, ip, port}]
    "log": [],
    "prevFail": {}, "lastTargetsRefresh": 0.0, "ignoredIps": set(),
    "conflict": False,
}
LOCK = threading.Lock()

DIAG_RE = re.compile(r"XRFD up=(\d+) (?:ms=(\d+) )?ip=(\S+) rx=(\d+) dhcp=(\d+)/(\d+) rtr=(\w)")
TGT_RE = re.compile(r" t(\d)=(off|[ABC])(?:,(\d+),(\d+),(\d+))?")
LIST_RE = re.compile(r"^t(\d) (on|off) (\d+\.\d+\.\d+\.\d+):(\d+)")


def add_log(kind, msg):
    with LOCK:
        S["log"].insert(0, {"t": time.strftime("%H:%M:%S"), "k": kind, "m": msg})
        del S["log"][80:]


def send_ctrl(cmd, timeout=3.0):
    with LOCK:
        ip = S["deviceIp"]
    if not ip:
        return None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(cmd.encode("ascii", "replace"), (ip, CTRL_PORT))
        data, _ = sock.recvfrom(2048)
        return data.decode("ascii", "replace")
    except OSError:
        return None
    finally:
        sock.close()


def refresh_targets():
    with LOCK:  # stamp the attempt - device silence must not cause retry storms
        S["lastTargetsRefresh"] = time.time()
    reply = send_ctrl("targets", timeout=2.0)
    if reply is None:
        return
    found = []
    for line in reply.split("\n"):
        m = LIST_RE.match(line.strip())
        if m:
            found.append({"n": int(m.group(1)), "on": m.group(2) == "on",
                          "ip": m.group(3), "port": int(m.group(4))})
    if found:
        with LOCK:
            S["targets"] = found
            S["lastTargetsRefresh"] = time.time()


def process_diag(line, src_ip):
    m = DIAG_RE.search(line)
    if not m:
        return
    up, rx = int(m.group(1)), int(m.group(4))
    ms = int(m.group(2)) if m.group(2) else -1
    now = time.time()
    with LOCK:
        # ignore a second XRFD device while the current one is live -
        # last-broadcaster-wins would misroute commands and corrupt fps
        if S["deviceIp"] and src_ip != S["deviceIp"] and now - S["lastSeen"] <= 12:
            if src_ip not in S["ignoredIps"]:
                S["ignoredIps"].add(src_ip)
                S["log"].insert(0, {"t": time.strftime("%H:%M:%S"), "k": "warn",
                                    "m": f"ignoring second XRFD device at {src_ip} (active: {S['deviceIp']})"})
            return
        # true reboot resets BOTH up and rx; a millis() wrap (49.7 days)
        # only resets up - don't false-alarm on it
        if S["deviceIp"] and up < S["up"] and rx < S["rx"]:
            S["log"].insert(0, {"t": time.strftime("%H:%M:%S"), "k": "warn",
                                "m": f"DEVICE REBOOTED (uptime reset: {S['up']}s -> {up}s)"})
            del S["log"][80:]
            S["prevRx"] = S["prevMs"] = -1
        if not S["deviceIp"]:
            S["log"].insert(0, {"t": time.strftime("%H:%M:%S"), "k": "info",
                                "m": f"device found: {src_ip}"})
        S["deviceIp"], S["lastSeen"] = src_ip, now
        S["up"], S["rx"] = up, rx
        S["dhcpOk"], S["dhcpFail"] = int(m.group(5)), int(m.group(6))
        S["rtr"] = m.group(7)
        cf = " CONFLICT" in line
        if cf and not S["conflict"]:
            S["log"].insert(0, {"t": time.strftime("%H:%M:%S"), "k": "warn",
                                "m": "IP CONFLICT detected on device LAN!"})
        S["conflict"] = cf
        # fps from the device's own clock (ms field) - immune to receive jitter
        if ms >= 0 and S["prevMs"] >= 0 and ms > S["prevMs"] and rx >= S["prevRx"]:
            dms = ms - S["prevMs"]
            if dms >= 1000:
                S["fps"] = round((rx - S["prevRx"]) * 1000.0 / dms, 2)
        S["prevRx"], S["prevMs"] = rx, ms

        S["diag"] = {}
        for t in TGT_RE.finditer(line):
            i, st = t.group(1), t.group(2)
            ok = int(t.group(3)) if t.group(3) else 0
            fail = int(t.group(4)) if t.group(4) else 0
            skip = int(t.group(5)) if t.group(5) else 0
            S["diag"][i] = {"state": st, "ok": ok, "fail": fail, "skip": skip}
            if i in S["prevFail"] and fail > S["prevFail"][i]:
                S["log"].insert(0, {"t": time.strftime("%H:%M:%S"), "k": "warn",
                                    "m": f"target {i} sendFail increased -> {fail}"})
                del S["log"][80:]
            S["prevFail"][i] = fail


def diag_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):  # macOS/BSD: coexist with monitor script
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(("", DIAG_PORT))
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            process_diag(data.decode("ascii", "replace"), addr[0])
        except OSError:
            time.sleep(0.5)


def targets_loop():
    while True:
        with LOCK:
            stale = S["deviceIp"] and (time.time() - S["lastTargetsRefresh"] > 30)
        if stale:
            refresh_targets()
        time.sleep(2)


def status_json():
    with LOCK:
        age = int(time.time() - S["lastSeen"]) if S["lastSeen"] else 9999
        targets = []
        for t in S["targets"]:
            d = S["diag"].get(str(t["n"]), {})
            targets.append({
                "n": t["n"], "on": t["on"], "ip": t["ip"], "port": t["port"],
                "state": d.get("state", "off"), "ok": d.get("ok", 0),
                "fail": d.get("fail", 0), "skip": d.get("skip", 0),
            })
        return json.dumps({
            "deviceIp": S["deviceIp"], "ageSec": age,
            "live": bool(S["deviceIp"]) and age <= 12,
            "up": S["up"], "rx": S["rx"], "fps": S["fps"],
            "dhcpOk": S["dhcpOk"], "dhcpFail": S["dhcpFail"], "rtr": S["rtr"],
            "conflict": S["conflict"],
            "targets": targets, "log": S["log"][:40],
        })


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status, ctype, body):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._reply(200, "text/html", HTML)
        elif self.path == "/api/status":
            self._reply(200, "application/json", status_json())
        elif self.path.startswith("/api/cmd?c="):
            cmd = unquote(self.path[len("/api/cmd?c="):])
            reply = send_ctrl(cmd)
            if reply is None:
                reply = "(no reply from device)"
            add_log("cmd", f"{cmd}  ->  {reply}")
            if cmd.startswith("target "):
                refresh_targets()
            self._reply(200, "text/plain", reply)
        else:
            self._reply(404, "text/plain", "not found")

    def log_message(self, fmt, *args):  # silence per-request console spam
        pass


HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XRFD Dashboard</title>
<style>
/* themes: dark = Monokai, light = Solarized */
*{box-sizing:border-box;margin:0;padding:0}
body.dark{--bg:#272822;--fg:#f8f8f2;--head:#f8f8f2;--panel:#3e3d32;--border:#49483e;--muted:#a59f85;--accent:#66d9ef;--good:#a6e22e;--goodtext:#1e1f1c;--bad:#f92672;--badtext:#fff;--warn:#fd971f;--warntext:#1e1f1c;--violet:#ae81ff;--log:#1e1f1c;--logline:#3e3d32;--btn:#49483e;--addr:#e6db74}
body.light{--bg:#fdf6e3;--fg:#657b83;--head:#586e75;--panel:#eee8d5;--border:#d3cbb7;--muted:#93a1a1;--accent:#268bd2;--good:#859900;--goodtext:#fdf6e3;--bad:#dc322f;--badtext:#fdf6e3;--warn:#cb4b16;--warntext:#fdf6e3;--violet:#6c71c4;--log:#f5eed8;--logline:#e0d9c3;--btn:#d3cbb7;--addr:#b58900}
body{font-family:'Segoe UI','Courier New',monospace;padding:14px;max-width:920px;margin:0 auto;background:var(--bg);color:var(--fg);transition:background .3s,color .3s}
h1{font-size:18px;color:var(--accent);display:flex;align-items:center;gap:10px;margin-bottom:10px}
.dot{width:12px;height:12px;border-radius:50%;background:var(--muted);display:inline-block}
.dot.live{background:var(--good);box-shadow:0 0 8px var(--good)}
.dot.dead{background:var(--bad);box-shadow:0 0 8px var(--bad)}
.dev{color:var(--muted);font-size:13px;font-weight:400;margin-left:auto}
.btn-theme{width:36px;height:30px;border:none;border-radius:8px;background:var(--btn);color:var(--fg);font-size:16px;cursor:pointer}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.chip{background:var(--panel);border-radius:8px;padding:8px 14px;font-size:13px;border:1px solid var(--border)}
.chip b{color:var(--accent);font-size:16px;display:block}
.chip.warn b{color:var(--warn)}
.chip.bad b{color:var(--bad)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
@media(max-width:640px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border-radius:12px;padding:14px;border:1px solid var(--border)}
.card.dim{opacity:0.55}
.card .hd{display:flex;align-items:center;margin-bottom:8px}
.card .nm{font-weight:700;font-size:15px;color:var(--head)}
.badge{margin-left:auto;font-size:11px;font-weight:700;border-radius:6px;padding:3px 10px}
.badge.on{background:var(--good);color:var(--goodtext)}
.badge.off{background:var(--muted);color:var(--bg)}
.badge.backoff{background:var(--warn);color:var(--warntext)}
.addr{font-size:14px;color:var(--addr);margin-bottom:6px}
.cnt{font-size:12px;color:var(--muted);margin-bottom:10px}
.cnt .bad{color:var(--bad);font-weight:700}
.btns{display:flex;gap:8px}
button{border:none;border-radius:8px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer}
.b-on{background:var(--good);color:var(--goodtext)}
.b-off{background:var(--bad);color:var(--badtext)}
.b-edit{background:var(--btn);color:var(--fg)}
.b-reboot{background:var(--violet);color:#fff}
button:hover{opacity:.85}
.foot{display:flex;align-items:center;margin-bottom:8px}
.foot h2{font-size:14px;color:var(--accent)}
.foot .b-reboot{margin-left:auto}
.log{background:var(--log);border-radius:10px;padding:10px;font-size:12px;font-family:'Courier New',monospace;max-height:220px;overflow-y:auto;border:1px solid var(--border)}
.log div{padding:2px 0;border-bottom:1px solid var(--logline);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log .warn{color:var(--warn)}
.log .cmd{color:var(--accent)}
</style>
</head>
<body class="dark">
<h1><span class="dot" id="dot"></span>XRFD Dashboard<span class="dev" id="dev">device: -</span><button class="btn-theme" onclick="toggleTheme()" title="Monokai / Solarized">&#9789;</button></h1>
<div class="bar">
<div class="chip">Uptime<b id="up">-</b></div>
<div class="chip">FreeD rate<b id="fps">-</b></div>
<div class="chip" id="dhcpChip">DHCP renew<b id="dhcp">-</b></div>
<div class="chip" id="rtrChip">RTR patch<b id="rtr">-</b></div>
<div class="chip">Frames<b id="rx">-</b></div>
<div class="chip bad" id="cfChip" style="display:none">IP conflict<b>DETECTED</b></div>
</div>
<div class="grid" id="grid"></div>
<div class="foot"><h2>Event log</h2><button class="b-reboot" onclick="doReboot()">Reboot device</button></div>
<div class="log" id="log"></div>
<script>
var theme=localStorage.getItem('xrfd_theme')||'dark';
document.body.className=theme;
function toggleTheme(){theme=(theme==='dark')?'light':'dark';
 document.body.className=theme;localStorage.setItem('xrfd_theme',theme);}
function fmtUp(s){var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
 return (d?d+'d ':'')+(h?h+'h ':'')+m+'m '+(s%60)+'s';}
function esc(t){var e=document.createElement('span');e.textContent=t;return e.innerHTML;}
function cmd(c,cb){fetch('/api/cmd?c='+encodeURIComponent(c)).then(function(r){return r.text();})
 .then(function(t){if(cb)cb(t);refresh();});}
function toggleT(n,on){cmd('target '+n+' '+(on?'on':'off'));}
function editT(n,ip,port){
 var i=prompt('Target '+n+' IP address:',ip);if(i===null)return;
 var p=prompt('Target '+n+' port:',port);if(p===null)return;
 cmd('target '+n+' set '+i.trim()+' '+p.trim(),function(t){alert(t);});}
function doReboot(){if(confirm('Reboot the device? FreeD output stops for a few seconds.'))
 cmd('reboot',function(t){alert(t);});}
function refresh(){
 fetch('/api/status').then(function(r){return r.json();}).then(function(s){
  var dot=document.getElementById('dot');
  dot.className='dot '+(s.live?'live':'dead');
  document.getElementById('dev').textContent='device: '+(s.deviceIp||'-')+(s.live?'':'  (no signal '+s.ageSec+'s)');
  document.getElementById('up').textContent=fmtUp(s.up);
  document.getElementById('fps').textContent=(s.fps||0).toFixed(2)+' fps';
  document.getElementById('rx').textContent=s.rx.toLocaleString();
  document.getElementById('dhcp').textContent=s.dhcpOk+' / '+s.dhcpFail+' fail';
  document.getElementById('dhcpChip').className='chip'+(s.dhcpFail>0?' warn':'');
  document.getElementById('rtr').textContent=(s.rtr==='Y'?'OK (80ms)':'FAILED');
  document.getElementById('rtrChip').className='chip'+(s.rtr==='Y'?'':' bad');
  document.getElementById('cfChip').style.display=s.conflict?'':'none';
  var g='';
  (s.targets||[]).forEach(function(t){
   var st=t.on?((t.state==='B'||t.state==='C')?'backoff':'on'):'off';
   var lbl=t.on?(t.state==='B'?'BACKOFF':(t.state==='C'?'NO SOCKET':'ON')):'OFF';
   g+='<div class="card'+(t.on?'':' dim')+'"><div class="hd"><span class="nm">Target '+t.n+'</span>'
    +'<span class="badge '+st+'">'+lbl+'</span></div>'
    +'<div class="addr">'+esc(t.ip)+' : '+t.port+'</div>'
    +'<div class="cnt">ok '+t.ok.toLocaleString()+' &nbsp; fail <span'+(t.fail>0?' class="bad"':'')+'>'+t.fail+'</span> &nbsp; skip '+t.skip+'</div>'
    +'<div class="btns"><button class="'+(t.on?'b-off':'b-on')+'" onclick="toggleT('+t.n+','+(!t.on)+')">'+(t.on?'Turn OFF':'Turn ON')+'</button>'
    +'<button class="b-edit" onclick="editT('+t.n+',\\''+esc(t.ip)+'\\','+t.port+')">Edit IP/Port</button></div></div>';
  });
  document.getElementById('grid').innerHTML=g||'<div class="card">No target info yet...</div>';
  var lg='';
  (s.log||[]).forEach(function(e){lg+='<div class="'+e.k+'">['+e.t+'] '+esc(e.m)+'</div>';});
  document.getElementById('log').innerHTML=lg;
 }).catch(function(){document.getElementById('dot').className='dot dead';});
}
refresh();setInterval(refresh,2000);
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="XRFD Dashboard (web GUI bridge)")
    ap.add_argument("--port", type=int, default=10000)
    args = ap.parse_args()

    threading.Thread(target=diag_loop, daemon=True).start()
    threading.Thread(target=targets_loop, daemon=True).start()
    add_log("info", "dashboard started")

    try:
        srv = ThreadingHTTPServer(("", args.port), Handler)
    except OSError as e:
        print(f"ERROR: TCP port {args.port} already in use - another dashboard running?")
        print(f"       Close it, or relaunch with: --port <other>  ({e})")
        raise SystemExit(1)
    print(f"XRFD Dashboard running:")
    print(f"  local:  http://localhost:{args.port}")
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
        if not lan_ip.startswith("127."):
            print(f"  LAN:    http://{lan_ip}:{args.port}")
    except OSError:
        pass
    print(f"Waiting for device diag broadcast on UDP {DIAG_PORT} ... (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

"""Flask app factory + waitress entrypoint for the XRFD dashboard."""
import json
import os
import queue
import socket
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from . import config, lifecycle
from . import __version__


def create_app(state, bridge):
    app = Flask(__name__, static_folder=None)
    start_time = time.time()

    @app.get("/api/status")
    def api_status():
        return jsonify(state.snapshot())

    @app.get("/health")
    def health():
        is_local = request.remote_addr in ("127.0.0.1", "::1")
        obj = {"status": "ok"}
        if is_local:
            obj["pid"] = os.getpid()
            obj["uptime_seconds"] = round(time.time() - start_time, 1)
            obj["device"] = state.device_ip
        return jsonify(obj)

    @app.post("/api/cmd")
    def api_cmd():
        data = request.get_json(silent=True) or {}
        cmd = data.get("cmd")
        if not cmd:
            return jsonify({"error": "missing 'cmd'"}), 400
        reply = bridge.send_command(cmd)
        state.add_log("cmd", "%s  ->  %s" % (cmd, reply if reply else "(no reply)"))
        if cmd.startswith("target "):
            bridge._maybe_refresh_targets()
        return jsonify({"ok": reply is not None, "reply": reply})

    @app.get("/events")
    def events():
        def stream():
            q = state.subscribe()
            try:
                # immediate snapshot so a fresh client paints at once
                yield "data: %s\n\n" % json.dumps(state.snapshot())
                while True:
                    try:
                        snap = q.get(timeout=15)
                        yield "data: %s\n\n" % json.dumps(snap)
                    except queue.Empty:
                        yield ": keepalive\n\n"   # comment frame keeps proxies open
            finally:
                state.unsubscribe(q)
        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/")
    def index():
        if not (config.FRONTEND_DIST / "index.html").exists():
            return ("frontend not built — run: cd dashboard/frontend && npm run build", 503)
        return send_from_directory(config.FRONTEND_DIST, "index.html")

    @app.get("/<path:path>")
    def assets(path):
        if (config.FRONTEND_DIST / path).exists():
            return send_from_directory(config.FRONTEND_DIST, path)
        return ("not found", 404)

    return app


def _write_pidfile():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.PIDFILE.write_text(
        '{"pid": %d, "command": "python -m backend.app", "started_at": "%s"}'
        % (os.getpid(), time.strftime("%Y-%m-%dT%H:%M:%S")))


def _lan_ips():
    """Best-effort list of this host's non-loopback IPv4 addresses."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def main():
    from .state import State
    from .udp_bridge import UdpBridge
    from waitress import serve
    state = State()
    bridge = UdpBridge(state)
    bridge.start()
    state.add_log("info", "dashboard started")
    _write_pidfile()
    _log_dir = config.REPO_ROOT / "log"
    lifecycle.append(_log_dir, "xr-freed-to-udp", "STARTED",
                     "version=%s pid=%d" % (__version__, os.getpid()))
    app = create_app(state, bridge)
    port = config.WEB_PORT
    # 0.0.0.0 = bind on all interfaces; browse via one of these reachable URLs:
    print("XRFD dashboard running (waitress, listening on 0.0.0.0:%d):" % port, flush=True)
    print("  local:   http://localhost:%d" % port, flush=True)
    for ip in _lan_ips():
        print("  LAN:     http://%s:%d" % (ip, port), flush=True)
    try:
        serve(app, host="0.0.0.0", port=port, threads=8)
    finally:
        bridge.stop()
        config.PIDFILE.unlink(missing_ok=True)
        lifecycle.append(_log_dir, "xr-freed-to-udp", "STOPPED",
                         "pid=%d" % os.getpid())


if __name__ == "__main__":
    main()

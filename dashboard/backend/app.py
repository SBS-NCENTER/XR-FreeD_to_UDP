"""Flask app factory + waitress entrypoint for the XRFD dashboard."""
import os
import time

from flask import Flask, jsonify, request

from . import config


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

    return app

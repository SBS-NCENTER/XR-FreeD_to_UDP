from backend.access_log import AccessLog


def _run(environ, capsys):
    def inner_app(env, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    body = b"".join(AccessLog(inner_app)(environ, lambda *a, **k: None))
    assert body == b"ok"
    return capsys.readouterr().out.strip()


def test_logs_ip_method_path_status(capsys):
    env = {"REMOTE_ADDR": "10.10.204.211", "REQUEST_METHOD": "GET", "PATH_INFO": "/api/status"}
    assert _run(env, capsys) == '10.10.204.211 "GET /api/status" 200'


def test_status_code_only(capsys):
    env = {"REMOTE_ADDR": "127.0.0.1", "REQUEST_METHOD": "GET", "PATH_INFO": "/health"}
    assert _run(env, capsys) == '127.0.0.1 "GET /health" 200'


def test_missing_remote_addr_falls_back_to_dash(capsys):
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/"}
    assert _run(env, capsys) == '- "GET /" 200'


def test_non_200_status_logs_code_only(capsys):
    def inner_app(env, start_response):
        start_response("404 NOT FOUND", [("Content-Type", "text/plain")])
        return [b"nope"]
    env = {"REMOTE_ADDR": "10.10.204.211", "REQUEST_METHOD": "GET", "PATH_INFO": "/missing"}
    b"".join(AccessLog(inner_app)(env, lambda *a, **k: None))
    assert capsys.readouterr().out.strip() == '10.10.204.211 "GET /missing" 404'

"""요청당 access log 1줄을 stdout으로 emit하는 WSGI 미들웨어.

NCCP 로깅 규약: `<ip> "<method> <path>" <status>` (journald가 ts·unit 부착).
클라이언트 IP를 줄에 포함 → server-manager의 loopback health-poll 필터가 식별.
waitress는 access log가 내장이 아니라 이 미들웨어로 보충한다.
"""


class AccessLog:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        ip = environ.get("REMOTE_ADDR", "-")
        method = environ.get("REQUEST_METHOD", "-")
        path = environ.get("PATH_INFO", "-")

        def _sr(status, headers, exc_info=None):
            print(f'{ip} "{method} {path}" {status.split(" ", 1)[0]}', flush=True)
            return start_response(status, headers, exc_info)

        return self.app(environ, _sr)

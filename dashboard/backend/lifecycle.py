"""라이프사이클 로그 기록 (server-manager와 공유하는 파일 규약).

형식:  <ts> | <service> | <EVENT> | <detail>
경로:  <log_dir>/<YYYY-MM-DD>.txt   (append-only, best-effort)
"""
import datetime
from pathlib import Path


def append(log_dir, service: str, event: str, detail: str) -> None:
    try:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now().astimezone()
        ts = now.strftime("%Y-%m-%d %H:%M:%S %z")
        fname = now.strftime("%Y-%m-%d") + ".txt"
        with (log_dir / fname).open("a", encoding="utf-8") as f:
            f.write(f"{ts} | {service} | {event} | {detail}\n")
    except Exception:
        pass

"""Pure parsing/command helpers ported from tools/xrfd_dashboard.ps1."""
import re

_DIAG = re.compile(
    r"XRFD up=(\d+) (?:ms=(\d+) )?ip=(\S+) rx=(\d+) dhcp=(\d+)/(\d+) rtr=(\w)")
_TARGET = re.compile(r" t(\d)=(off|[ABC]),?(\d+)?,?(\d+)?,?(\d+)?")
_TARGETS_LINE = re.compile(r"^t(\d) (on|off) (\d+\.\d+\.\d+\.\d+):(\d+)")


def parse_diag(line):
    """Parse one diag broadcast line. Returns dict or None if it doesn't match."""
    m = _DIAG.search(line)
    if not m:
        return None
    targets = {}
    for t in _TARGET.finditer(line):
        n, state, ok, fail, skip = t.group(1), t.group(2), t.group(3), t.group(4), t.group(5)
        targets[n] = {
            "state": state,
            "ok": int(ok) if ok else 0,
            "fail": int(fail) if fail else 0,
            "skip": int(skip) if skip else 0,
        }
    return {
        "up": int(m.group(1)),
        "ms": int(m.group(2)) if m.group(2) else None,
        "ip": m.group(3),
        "rx": int(m.group(4)),
        "dhcp_ok": int(m.group(5)),
        "dhcp_fail": int(m.group(6)),
        "rtr": m.group(7),
        "conflict": " CONFLICT" in line,
        "targets": targets,
    }


def parse_targets(reply):
    """Parse a 'targets' command reply into a list of target dicts."""
    out = []
    for line in reply.split("\n"):
        m = _TARGETS_LINE.match(line)
        if m:
            out.append({
                "n": int(m.group(1)),
                "on": m.group(2) == "on",
                "ip": m.group(3),
                "port": int(m.group(4)),
            })
    return out


def build_command(*parts):
    """Join command parts into the wire string, e.g. ('target',1,'on') -> 'target 1 on'."""
    return " ".join(str(p) for p in parts)

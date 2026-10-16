"""The plumbing every hook entry shares: read the payload, decide, say nothing.

Everything here is written so that a failure is silence. A hook that cannot read
its payload, cannot find its notepad, cannot write its log or raises anything at
all lets the session carry on -- the cost of a wrong refusal is a session's work,
and the cost of a missed one is a defect this host has already survived.

Only the standard library is imported, because the entries are spawned with
site-packages disabled.
"""

import datetime
import json
import os
import sys

#: The directory every session record lives under: the notepads, the lane
#: roster, the hook events and the grants. `HARNESS_SESSION_ROOT` names it, and a
#: host that sets nothing keeps them in `~/draft`.
ROOT = os.environ.get("HARNESS_SESSION_ROOT") or os.path.expanduser("~/draft")
#: The file whose presence in `ROOT` says the records are really there.
MARKER = ".harness-session-root"
#: This session's own records -- its notepads and reports -- when a host keeps
#: each worktree's in a folder of its own under the root (`HARNESS_RECORDS_DIR`,
#: `<root>/seat`, `<root>/lane-a`, ...); a host that sets nothing keeps them at
#: the root.
RECORDS = os.environ.get("HARNESS_RECORDS_DIR") or ROOT
NOTEPADS = RECORDS + "/notepads"
EVENTS = ROOT + "/hook-events"
ROSTER = os.environ.get("LANE_ROSTER", ROOT + "/lane-watch.roster")
GRANTS = os.environ.get("HARNESS_SUBAGENT_GRANTS", ROOT + "/grants/subagents")

OFF = "off"
REPORT = "report"
DENY = "deny"


def payload():
    """The hook payload, or an empty mapping if anything about it is wrong."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def project_dir():
    """The checkout whose hooks are running, as the harness names it."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def root_present():
    """Whether the session root is really there, not merely a path.

    The root counts only when it is a directory holding its marker file. An
    empty directory left where the records used to be reads as a session that
    owes nothing, which is the one wrong answer that matters here, so the
    question asked is about the marker and not about the path. A root on a
    plain directory with its marker is present: whether a disk is mounted
    under it is not the question.
    """
    try:
        return os.path.isfile(os.path.join(ROOT, MARKER))
    except Exception:
        return False


def session_name(data):
    """The session's name, or its id when it was never given one.

    The name lives beside the transcript rather than in the payload: it is the
    `customTitle` of `custom-title.json` in the directory named by dropping the
    transcript's extension -- **not a record inside the transcript itself**. A
    path that is missing or unreadable falls back to the session id.

    **That fallback is the handle a test has.** A session with neither name nor
    id is anonymous and gets no notepad, which is correct; but a probe that
    supplies a transcript path with no such file beside it also lands on the
    fallback, and the hook then exits silently for a reason that looks exactly
    like the rule not matching. Drive a probe by the id, or put the file where
    this reads it.
    """
    sid = data.get("session_id") or ""
    path = data.get("transcript_path") or ""
    if path:
        try:
            base = os.path.splitext(path)[0]
            with open(os.path.join(base, "custom-title.json")) as handle:
                title = json.load(handle).get("customTitle")
            if isinstance(title, str) and title.strip():
                return title.strip()
        except Exception:
            pass
    return sid


def notepad_text(name):
    """The notepad's text, or None when there is no readable one.

    None is not an empty notepad: an empty notepad is a session that has
    written one, and None is a session that has none at all, which is the
    question `notepad-absent` asks.
    """
    if not name or not root_present():
        return None
    try:
        with open(os.path.join(NOTEPADS, name + ".md")) as handle:
            return handle.read()
    except Exception:
        return None


def other_records():
    """The other sessions' records folders under the root, as absolute paths.

    Empty when this host separates nothing (`RECORDS` is the root itself) or
    the root is absent: the rule that reads this is then silent. A folder
    counts when it holds a `notepads` directory, which is what every session's
    folder holds and nothing else under the root does.
    """
    if RECORDS == ROOT or not root_present():
        return []
    own = os.path.realpath(RECORDS)
    out = []
    try:
        for name in sorted(os.listdir(ROOT)):
            path = os.path.join(ROOT, name)
            if os.path.isdir(os.path.join(path, "notepads")) and os.path.realpath(path) != own:
                out.append(path)
    except Exception:
        return []
    return out


def grants_text():
    """The subagent grant file's text, "" when there is none, None when unreadable.

    An absent file is a state the rule can decide from -- nobody holds a grant --
    so it reads as empty. An absent session root or a file that will not open is
    not, and reads as None, which the rule takes as could-not-tell.
    """
    if not root_present():
        return None
    if not os.path.exists(GRANTS):
        return ""
    try:
        with open(GRANTS) as handle:
            return handle.read()
    except Exception:
        return None


def roster_text():
    """The lane-watch roster's text, or None when it cannot be read.

    Read through the same `LANE_ROSTER` override `scripts/watch-lanes.py`
    honors, so a check can drive this on a roster of its own without
    touching the one every lane's watcher polls. None covers an absent
    session root and a missing or unreadable file alike -- both leave the
    rule with nothing to decide from.
    """
    if not root_present():
        return None
    try:
        with open(ROSTER) as handle:
            return handle.read()
    except Exception:
        return None


def modes(root):
    """Every rule's mode, with anything unreadable treated as off."""
    try:
        with open(os.path.join(root, ".claude", "hooks", "modes.json")) as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def mode_of(root, rule):
    value = modes(root).get(rule)
    return value if value in (OFF, REPORT, DENY) else OFF


def report(rule, name, detail):
    """Append one event line. A failure to write is swallowed on purpose."""
    if not root_present():
        return
    try:
        os.makedirs(EVENTS, exist_ok=True)
        day = datetime.datetime.now().strftime("%Y-%m-%d")
        row = {
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
            "rule": rule,
            "session": name,
            "detail": detail,
            # Which checkout's hooks wrote this row: each worktree runs its
            # own copy of the rules and its own modes.json.
            "root": project_dir(),
        }
        with open(os.path.join(EVENTS, day + ".jsonl"), "a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def shell():
    """The shell the Bash tool will run this command in.

    The spelling rules describe zsh and are silent under anything else, so an
    unset or unreadable value makes them silent rather than wrong.
    """
    return os.environ.get("SHELL", "")


def context(event, text):
    """Hand the model a line of context and exit without objecting."""
    try:
        json.dump(
            {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}},
            sys.stdout,
        )
        sys.stdout.write("\n")
    except Exception:
        pass
    sys.exit(0)


def deny(reason):
    """Refuse the call, naming what was matched and what to do about it."""
    try:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
    except Exception:
        pass
    sys.exit(0)


def allow():
    sys.exit(0)

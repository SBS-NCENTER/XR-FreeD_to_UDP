"""What the session is told when a prompt arrives.

A rule here cannot object: every path out of this file exits 0, and its whole
output is one line of context per rule with something to say. One rule speaks
here, `notepad-absent`.

**One `context` call carries every line.** `entry.context` exits the process,
so a second rule with something to say would otherwise be silent for exactly
the reason the first had something to say too.
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import entry  # noqa: E402
import rules  # noqa: E402

ABSENT = "notepad-absent"


def notepad_absent_line(data, root):
    """Whether a session the roster names has kept no notepad at all."""
    mode = entry.mode_of(root, ABSENT)
    if mode == entry.OFF:
        return None

    name = entry.session_name(data)
    sid = data.get("session_id") or ""
    roster = entry.roster_text()
    exists = entry.notepad_text(name) is not None
    speak = rules.notepad_absent(roster, sid, exists, entry.root_present())

    if not speak:
        return None
    if mode == entry.REPORT:
        entry.report(ABSENT, name, {"event": "UserPromptSubmit"})
        return None
    return (
        "No notepad for this session: %s/%s.md does not exist. Create it, "
        "one line per obligation; it is what this session resumes from "
        "after it compacts." % (entry.NOTEPADS, name)
    )


#: Applied in order, and each one's line joins the others. Adding a rule here
#: is adding one entry.
RULES = (notepad_absent_line,)


def main():
    data = entry.payload()
    root = entry.project_dir()
    lines = []
    for rule in RULES:
        try:
            line = rule(data, root)
        except Exception:
            # A rule that raises is a rule with nothing to say. It cannot take
            # the others down with it, and it never objects.
            continue
        if line:
            lines.append(line)
    if not lines:
        entry.allow()
    entry.context("UserPromptSubmit", "\n".join(lines))


try:
    main()
except Exception:
    sys.exit(0)

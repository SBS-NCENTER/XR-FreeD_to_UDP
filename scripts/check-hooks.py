#!/usr/bin/env python3
"""Run every hook case, and check that rules and cases account for each other.

  scripts/check-hooks.py [--hooks DIR]

**What this reaches, and what it does not.** Every case runs against the pure
functions a rule is made of, with its inputs injected -- which is the only level
at which a case can say "the roster could not be read", a state a real session
root reaches only by accident. **Each entry script is then driven end to end** on
a real payload over stdin, with a real session root, roster and grant file, once
per mode, so the plumbing between the harness and those functions is exercised
rather than assumed. What is not reached here is the harness itself: whether
Claude Code calls these scripts at all is not a question this can answer.

Exit 0 when every case holds and every rule and case file account for each
other, 1 when any does not, and 2 when the check cannot run -- a missing hooks
directory, or a case file that will not parse.

**Forced branches**: a flipped expectation exits 1 naming the case; a rule in
the mode file with no case file exits 1; a case file naming a rule the mode file
does not exits 1; a renamed hooks directory exits 2.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

CANNOT_RUN = 2
FAILED = 1
OK = 0

#: The file whose presence makes a session root count as present. The seat
#: creates it in each host's root, so its name is a contract this pins.
MARKER = ".harness-session-root"


def load_cases(hooks):
    """Every case file, keyed by the rule it names."""
    out = {}
    case_dir = hooks / "cases"
    if not case_dir.is_dir():
        return out
    for path in sorted(case_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except Exception as exc:
            raise SystemExit("check-hooks: %s will not parse: %s" % (path.name, exc))
        rule = doc.get("rule")
        if not isinstance(rule, str):
            raise SystemExit("check-hooks: %s names no rule" % path.name)
        out.setdefault(rule, []).append((path.name, doc))
    return out


def is_component(files):
    """A component is tested but carries no mode: it decides nothing on its own.

    The lexer is the one there is. Keeping it out of the mode file rather than
    giving it a mode nobody can promote is what stops a reader believing it can
    be switched off.
    """
    return all(doc.get("kind") == "component" for _, doc in files)


def run_absent_case(rules, case):
    """Whether `notepad-absent` should speak, the way the entry decides it."""
    return rules.notepad_absent(
        case.get("roster"), case.get("session_id"),
        case.get("notepad_exists"), case.get("root_present"),
    )


def run_lexer_case(zshwords, case):
    """Parse one case, comparing only the fields the case states.

    A case written before the record carried a field still passes, and a case
    that names the field tests it. The alternative -- rewriting every case each
    time the record grows -- turns the author's work into the implementer's.
    """
    try:
        parsed = zshwords.lex(case["command"])
    except zshwords.ParseError:
        return {"simple_commands": None, "expansions": None}
    got = [exp.as_dict() for exp in parsed.expansions]
    want = case.get("expansions") or []
    if len(want) == len(got):
        trimmed = [{k: g[k] for k in w if k in g} for w, g in zip(want, got)]
    else:
        trimmed = got
    return {"simple_commands": parsed.simple_commands, "expansions": trimmed}


#: Which entry runs which rule. Entry scripts are the files the harness is told
#: to run; everything else in the hooks directory is a module one of them
#: imports. A rule is reachable only through the entry the harness was told to
#: run, so this is what makes "off" mean something a reader can check rather
#: than a word in a file.
RUN_BY = {
    "notepad-absent": ("user_prompt_submit.py",),
    "zsh-colon-modifier": ("pre_tool_use.py",),
    "zsh-pipestatus": ("pre_tool_use.py",),
    "zsh-unsplit-variable": ("pre_tool_use.py",),
    "git-checkout": ("pre_tool_use.py",),
    "git-push-door": ("pre_tool_use.py",),
    "agent-grant": ("pre_tool_use.py",),
    "records-of-another": ("pre_tool_use.py",),
}


def check_registration(repo, hooks):
    """Every entry is registered, and everything registered exists.

    A built hook that nothing registers delivers nothing, and its silence is
    indistinguishable from a hook that ran and had nothing to say. A registration
    naming a file that is not there is the same failure pointing the other way.
    """
    findings = []
    settings = repo / ".claude" / "settings.json"
    if not settings.exists():
        return ["no .claude/settings.json, so no hook reaches any session"], set()
    try:
        doc = json.loads(settings.read_text())
    except Exception as exc:
        return ["settings.json will not parse: %s" % exc], set()

    registered = set()
    for event, entries in (doc.get("hooks") or {}).items():
        for entry in entries or []:
            for hook in entry.get("hooks") or []:
                args = hook.get("args") or []
                for arg in args:
                    if not isinstance(arg, str) or not arg.endswith(".py"):
                        continue
                    resolved = arg.replace("${CLAUDE_PROJECT_DIR}", str(repo))
                    name = os.path.basename(resolved)
                    registered.add(name)
                    if not os.path.exists(resolved):
                        findings.append(
                            "%s registers %s, which is not there" % (event, arg)
                        )

    return findings, registered


def check_modes_against_registration(modes, registered):
    """A rule that is not `off` must be reachable, and `off` says it is not yet.

    An entry that exists and is not registered is not a finding on its own --
    that is a hook built and waiting for its review, which is a real state this
    rollout passes through. **What is a finding is a rule claiming a mode it
    cannot act in**: a rule at `report` whose entry nothing runs writes no event,
    and its empty log reads exactly like a rule that never matched. That silence
    is the failure this whole gate exists to end, so it is caught here rather
    than discovered when somebody reads an empty log and calls a rule quiet.
    """
    findings = []
    for rule, mode in sorted(modes.items()):
        if mode == "off":
            continue
        for entry_name in RUN_BY.get(rule, ()):
            if entry_name not in registered:
                findings.append(
                    "%s is at %s but %s is not registered, so it cannot act"
                    % (rule, mode, entry_name)
                )
        if rule not in RUN_BY:
            findings.append("%s is at %s and no entry is declared to run it" % (rule, mode))
    return findings


def run_rule_case(zshwords, rules, rule, case):
    """Decide one case the way the entry will decide it.

    The grant rule reads no command, so it is answered first; every other rule
    is answered from a parse, and a parse that raises is allow, which is the
    property the whole hook set rests on.
    """
    if rule == "records-of-another":
        finding = rules.records_of_another(case.get("command"), case.get("own"), case.get("others"))
        return "deny" if finding else "allow"
    if rule == "agent-grant":
        finding = rules.agent_grant(
            case.get("tool"), case.get("roster"), case.get("session_id"),
            case.get("grants"), case.get("session_name"),
        )
        return "deny" if finding else "allow"

    command, shell = case.get("command"), case.get("shell")
    try:
        parse = zshwords.lex(command)
    except zshwords.ParseError:
        return "allow"

    by_rule = {
        "zsh-colon-modifier": lambda: rules.zsh_colon_modifier(parse, shell),
        "zsh-pipestatus": lambda: rules.zsh_pipestatus(parse, shell),
        "zsh-unsplit-variable": lambda: rules.zsh_unsplit_variable(parse, shell, command),
        "git-checkout": lambda: rules.git_checkout(parse, zshwords.git_subcommand),
        "git-push-door": lambda: rules.git_push_door(parse, zshwords.git_subcommand),
    }
    return "deny" if by_rule[rule]() else "allow"


#: Rules answered by `run_rule_case`, as opposed to the notepad's, whose inputs
#: are of another shape entirely.
COMMAND_RULES = (
    "zsh-colon-modifier", "zsh-pipestatus", "zsh-unsplit-variable",
    "git-checkout", "git-push-door", "agent-grant", "records-of-another",
)


def drive_entry(hooks, script, payload, modes, root, extra_env=None):
    """Run one entry as the harness runs it and return (exit, stdout)."""
    (root / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "hooks" / "modes.json").write_text(json.dumps(modes))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    if extra_env:
        env.update(extra_env)
    done = subprocess.run(
        [sys.executable, "-S", str(hooks / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hooks", default=None)
    args = ap.parse_args()

    repo = pathlib.Path(__file__).resolve().parent.parent
    hooks = pathlib.Path(args.hooks) if args.hooks else repo / ".claude" / "hooks"
    if not hooks.is_dir():
        print("check-hooks: no hooks directory at %s" % hooks, file=sys.stderr)
        return CANNOT_RUN

    sys.path.insert(0, str(hooks))
    try:
        import rules
        import zshwords
    except Exception as exc:
        print("check-hooks: cannot import the rules: %s" % exc, file=sys.stderr)
        return CANNOT_RUN

    try:
        modes = json.loads((hooks / "modes.json").read_text())
    except Exception as exc:
        print("check-hooks: modes.json will not parse: %s" % exc, file=sys.stderr)
        return CANNOT_RUN

    cases = load_cases(hooks)
    findings, registered = check_registration(repo, hooks)
    findings = list(findings) + check_modes_against_registration(modes, registered)

    # Rules and cases must account for each other in both directions. A rule with
    # no cases is untested; a case file for no rule tests something that has been
    # removed and will pass for ever.
    for rule in sorted(modes):
        if rule not in cases:
            findings.append("rule %s has no case file" % rule)
    for rule in sorted(cases):
        if rule not in modes and not is_component(cases[rule]):
            findings.append("case file names %s, which modes.json does not" % rule)

    checked = 0
    for rule, files in sorted(cases.items()):
        for filename, doc in files:
            for case in doc.get("cases", []):
                name = case.get("name", "<unnamed>")
                want = case.get("expect")
                if rule == "lexer":
                    got = run_lexer_case(zshwords, case)
                    want = {
                        "simple_commands": case.get("simple_commands"),
                        "expansions": case.get("expansions"),
                    }
                elif rule in COMMAND_RULES:
                    got = run_rule_case(zshwords, rules, rule, case)
                elif rule == "notepad-absent":
                    got = run_absent_case(rules, case)
                else:
                    findings.append("%s: no runner for rule %s" % (filename, rule))
                    continue
                checked += 1
                if got != want:
                    findings.append(
                        "%s: %s expected %r, got %r" % (filename, name, want, got)
                    )

    # The entries themselves, so the plumbing is exercised rather than
    # assumed: a real payload on stdin, a real session root, roster and grant
    # file. The session is `s`, named `x` by the title file beside its
    # transcript.
    drove = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "root"
        root.mkdir()
        sess = pathlib.Path(tmp) / "sess"
        sess.mkdir()
        (sess / "custom-title.json").write_text(json.dumps({"customTitle": "x"}))
        base = {
            "session_id": "s",
            "transcript_path": str(pathlib.Path(tmp) / "sess.jsonl"),
            "cwd": str(root),
        }
        # Every entry below reads its records from a stand-in session root,
        # named the way a host names its own: `HARNESS_SESSION_ROOT`, and the
        # marker file that says the records are really there.
        records = pathlib.Path(tmp) / "records"
        notepads = records / "notepads"
        notepads.mkdir(parents=True)
        (records / MARKER).write_text("")
        os.environ["HARNESS_SESSION_ROOT"] = str(records)
        roster_path = pathlib.Path(tmp) / "roster"
        roster_path.write_text("s lane-name\n")
        roster_env = {"LANE_ROSTER": str(roster_path)}
        push = dict(base, hook_event_name="PreToolUse", tool_name="Bash",
                    tool_input={"command": "git push origin main"})
        prompt = dict(base, hook_event_name="UserPromptSubmit", prompt="hi")

        def drive(label, script, payload, modes, expect_output, extra_env=None):
            nonlocal drove
            code, out, err = drive_entry(hooks, script, payload, modes, root, extra_env)
            drove += 1
            if code != 0:
                findings.append("%s exited %d: %s" % (label, code, err))
            elif bool(out) != expect_output:
                findings.append("%s %s" % (
                    label, "said nothing" if expect_output else "spoke: %s" % out))
            return out

        # Each entry, in deny mode and off: off is silence, whatever matched.
        for mode, expect_output in (("deny", True), ("off", False)):
            drive("pre_tool_use.py (git-push-door, %s)" % mode, "pre_tool_use.py",
                  push, {"git-push-door": mode}, expect_output)
            drive("user_prompt_submit.py (notepad-absent, %s)" % mode,
                  "user_prompt_submit.py", prompt, {"notepad-absent": mode},
                  expect_output, roster_env)

        # notepad-absent, through the real LANE_ROSTER override: a lane's row
        # and the seat's row are each told, a session the roster does not name
        # is not, and a notepad that exists silences it.
        for label, row, expect_output in (
            ("a lane's row", "s lane-name\n", True),
            ("the seat's row", "s 10.04-ochest01 seat\n", True),
            ("no row for the session", "other lane-name\n", False),
        ):
            roster_path.write_text(row)
            drive("notepad-absent, %s," % label, "user_prompt_submit.py", prompt,
                  {"notepad-absent": "deny"}, expect_output, roster_env)
        roster_path.write_text("s lane-name\n")
        (notepads / "x.md").write_text("- [ ] a thing\n")
        drive("notepad-absent, with the notepad there,", "user_prompt_submit.py",
              prompt, {"notepad-absent": "deny"}, False, roster_env)
        (notepads / "x.md").unlink()

        # The line names the notepad under the root the variable gave.
        out = drive("notepad-absent, naming its path,", "user_prompt_submit.py", prompt,
                    {"notepad-absent": "deny"}, True, roster_env)
        if str(notepads / "x.md") not in out:
            findings.append(
                "the absent-notepad line does not name the notepad under the "
                "session root: %s" % out
            )

        # Report mode says nothing to the session and writes one event under the
        # root's hook-events, and only when the rule would have spoken.
        events = records / "hook-events"
        (notepads / "x.md").write_text("")
        drive("notepad-absent in report mode, with the notepad there,",
              "user_prompt_submit.py", prompt, {"notepad-absent": "report"}, False,
              roster_env)
        if list(events.glob("*.jsonl")):
            findings.append("a report-mode event was written for a rule that would "
                            "have said nothing")
        (notepads / "x.md").unlink()
        drive("notepad-absent in report mode", "user_prompt_submit.py", prompt,
              {"notepad-absent": "report"}, False, roster_env)
        if not list(events.glob("*.jsonl")):
            findings.append(
                "a report-mode event was not written under the session root's "
                "hook-events"
            )

        # agent-grant, through the roster and grant-file overrides.
        grants_path = pathlib.Path(tmp) / "grants"
        for label, roster_line, grants, expect_output in (
            ("a lane with no grant file", "s x\n", None, True),
            ("a lane with a grant", "s x\n", "x\n", False),
            ("the seat", "s x seat\n", "", False),
        ):
            roster_path.write_text(roster_line)
            if grants is None:
                if grants_path.exists():
                    grants_path.unlink()
            else:
                grants_path.write_text(grants)
            drive("agent-grant (%s)" % label, "pre_tool_use.py",
                  dict(base, hook_event_name="PreToolUse", tool_name="Agent",
                       tool_input={"prompt": "p"}),
                  {"agent-grant": "deny"}, expect_output,
                  dict(roster_env, HARNESS_SUBAGENT_GRANTS=str(grants_path)))

        # A host that keeps each worktree's records in its own folder: the
        # notepad line names the folder HARNESS_RECORDS_DIR gave, and a command
        # naming another session's folder is refused in deny mode, not in off.
        own = records / "lane-a"
        (own / "notepads").mkdir(parents=True)
        (records / "seat" / "notepads").mkdir(parents=True)
        out = drive("notepad-absent, naming the records folder,", "user_prompt_submit.py",
                    prompt, {"notepad-absent": "deny"}, True,
                    dict(roster_env, HARNESS_RECORDS_DIR=str(own)))
        if str(own / "notepads" / "x.md") not in out:
            findings.append("the absent-notepad line does not name the notepad under "
                            "HARNESS_RECORDS_DIR: %s" % out)
        peek = dict(base, hook_event_name="PreToolUse", tool_name="Bash",
                    tool_input={"command": "cat %s/seat/notepads/10.16-seat01.md" % records})
        for mode, expect_output in (("deny", True), ("off", False)):
            drive("pre_tool_use.py (records-of-another, %s)" % mode, "pre_tool_use.py",
                  peek, {"records-of-another": mode}, expect_output,
                  {"HARNESS_RECORDS_DIR": str(own)})
        drive("records-of-another with no records folder set", "pre_tool_use.py",
              peek, {"records-of-another": "deny"}, False)

        # The marker is what makes the root present, not the directory: without
        # it the same records read as absent, and every rule that reads them is
        # silent rather than deciding from records that may not be there.
        roster_path.write_text("s x\n")
        if grants_path.exists():
            grants_path.unlink()
        (records / MARKER).unlink()
        drive("notepad-absent with the session root's marker absent",
              "user_prompt_submit.py", prompt, {"notepad-absent": "deny"}, False,
              roster_env)
        drive("agent-grant with the session root's marker absent", "pre_tool_use.py",
              dict(base, hook_event_name="PreToolUse", tool_name="Agent",
                   tool_input={"prompt": "p"}),
              {"agent-grant": "deny"}, False,
              dict(roster_env, HARNESS_SUBAGENT_GRANTS=str(grants_path)))
        (records / MARKER).write_text("")

        # The roster and the grant file, each read from its own place under
        # the session root with no override of either.
        overrides = {k: os.environ.pop(k) for k in ("LANE_ROSTER", "HARNESS_SUBAGENT_GRANTS")
                     if k in os.environ}
        try:
            (records / "lane-watch.roster").write_text("s x\n")
            (records / "grants").mkdir()
            for label, grants, expect_output in (
                ("no grant file under the root", None, True),
                ("a grant under the root", "x\n", False),
            ):
                if grants is not None:
                    (records / "grants" / "subagents").write_text(grants)
                drive("agent-grant read from the session root, %s," % label,
                      "pre_tool_use.py",
                      dict(base, hook_event_name="PreToolUse", tool_name="Agent",
                           tool_input={"prompt": "p"}),
                      {"agent-grant": "deny"}, expect_output)
        finally:
            os.environ.update(overrides)

    if findings:
        for line in findings:
            print("check-hooks: " + line, file=sys.stderr)
        print("check-hooks: %d finding(s) over %d case(s)" % (len(findings), checked),
              file=sys.stderr)
        return FAILED

    print("check-hooks: %d case(s) over %d rule(s) hold; %d entry run(s) driven "
          "end to end." % (checked, len(cases), drove))
    return OK


if __name__ == "__main__":
    sys.exit(main())

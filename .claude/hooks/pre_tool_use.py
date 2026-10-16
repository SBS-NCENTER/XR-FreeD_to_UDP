"""The PreToolUse entry: gather what a rule needs, apply its mode, exit 0.

Every path out of this file exits 0 unless a rule in deny mode matched. An
exception anywhere, a payload that will not parse, a roster or grant file that
cannot be read, a session root without its marker -- each of them is silence and
the call proceeds.
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import entry  # noqa: E402
import rules  # noqa: E402
import zshwords  # noqa: E402


def _apply(root, finding, name, detail):
    """Deny, log, or say nothing, according to the rule's mode."""
    if finding is None:
        return
    mode = entry.mode_of(root, finding.rule)
    if mode == entry.DENY:
        entry.deny(finding.reason)
    if mode == entry.REPORT:
        entry.report(finding.rule, name, dict(detail, matched=finding.matched))


def bash_rules(data, root):
    """The spelling and command-shape rules, over one parse of one command.

    The command is parsed once and every rule reads that parse, so a Bash call
    pays for one parse rather than five. A parse that will not resolve raises,
    and nothing downstream of it speaks.
    """
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return

    modes = entry.modes(root)
    # The door runs last on purpose: a push that is also malformed should hear
    # what is wrong with it before it hears where to run it.
    order = ("zsh-colon-modifier", "zsh-pipestatus", "zsh-unsplit-variable",
             "git-checkout", "git-push-door")
    if all(modes.get(rule, entry.OFF) == entry.OFF for rule in order):
        return

    try:
        parse = zshwords.lex(command)
    except zshwords.ParseError:
        return

    shell = entry.shell()
    name = entry.session_name(data)
    head = command.strip().splitlines()[0][:120]
    by_rule = {
        "zsh-colon-modifier": lambda: rules.zsh_colon_modifier(parse, shell),
        "zsh-pipestatus": lambda: rules.zsh_pipestatus(parse, shell),
        "zsh-unsplit-variable": lambda: rules.zsh_unsplit_variable(parse, shell, command),
        "git-checkout": lambda: rules.git_checkout(parse, zshwords.git_subcommand),
        "git-push-door": lambda: rules.git_push_door(parse, zshwords.git_subcommand),
    }
    for rule in order:
        if modes.get(rule, entry.OFF) == entry.OFF:
            continue
        _apply(root, by_rule[rule](), name, {"head": head})


def grant_rule(data, root):
    """A subagent launched without the operator's grant."""
    if entry.mode_of(root, "agent-grant") == entry.OFF:
        return
    tool = data.get("tool_name")
    if tool not in rules.AGENT_TOOLS:
        return
    name = entry.session_name(data)
    finding = rules.agent_grant(
        tool, entry.roster_text(), data.get("session_id"), entry.grants_text(), name,
        entry.GRANTS,
    )
    _apply(root, finding, name, {"tool": tool})


def records_rule(data, root):
    """A Bash command naming another session's records folder."""
    if entry.mode_of(root, "records-of-another") == entry.OFF:
        return
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return
    own = entry.RECORDS if entry.RECORDS != entry.ROOT else None
    if entry.is_seat(data):
        return
    finding = rules.records_of_another(command, own, entry.other_records())
    _apply(root, finding, entry.session_name(data), {"head": command.strip().splitlines()[0][:120]})


#: Rules are applied in order, and the first to deny ends the process. Adding a
#: rule here is adding one line.
RULES = (bash_rules, records_rule, grant_rule)


def main():
    data = entry.payload()
    root = entry.project_dir()
    for rule in RULES:
        try:
            rule(data, root)
        except Exception:
            # A rule that raises is a rule that did not match. It cannot take
            # the others down with it, and it never blocks.
            continue
    entry.allow()


try:
    main()
except Exception:
    sys.exit(0)

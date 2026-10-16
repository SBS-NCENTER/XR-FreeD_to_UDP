"""The rules, as pure functions over what the entry gathered for them.

Each takes what its rule reads and returns a `Finding` or None. **None reads a
file, the environment or a subprocess**, so every branch is reachable from a
case, and a rule that raises is a rule that did not match rather than one that
took the session down with it.

A finding's reason is written for a model reading it mid-command: it names what
was matched, why that is wrong on this host, and the exact rewrite. It carries no
citation, because a pointer to a document is a lookup that will not happen.
"""

import os
import re

#: Letters zsh applies after an unbraced `$name:`, found by trying all 52 on the
#: shell that runs here. `g` is the odd one: it is a prefix and needs `s` or `&`
#: after it in the same word.
MODIFIERS = set("acehlqrstuAPQ")

#: Contexts where the shell is still parsing, so an unquoted `&` ends the word
#: rather than joining it.
PARSING = ("unquoted", "cmdsub")

CHECKOUT_REWRITE = (
    "checkout -b X [start]            switch -c X [start]\n"
    "checkout --detach R              switch --detach R\n"
    "checkout <branch>                switch <branch>\n"
    "checkout [--] <paths>            restore <paths>\n"
    "checkout --ours|--theirs <paths> restore --ours|--theirs <paths>\n"
    "checkout <rev> -- <paths>        restore --source=<rev> <paths>\n"
    "checkout -m|--merge <paths>      restore --merge <paths>"
)


class Finding:
    __slots__ = ("rule", "reason", "matched")

    def __init__(self, rule, reason, matched=""):
        self.rule = rule
        self.reason = reason
        self.matched = matched


def _zsh(shell):
    """Whether the shell that will run this is zsh.

    The three spelling rules describe zsh and nothing else, so under any other
    shell they are silent rather than wrong.
    """
    return bool(shell) and os.path.basename(shell) == "zsh"


# -- zsh-colon-modifier -----------------------------------------------------


def zsh_colon_modifier(parse, shell):
    """An unbraced name followed by a colon and a modifier letter."""
    if not _zsh(shell):
        return None
    for exp in parse.expansions:
        if exp.braced:
            continue
        after = exp.next3
        if len(after) < 2 or after[0] != ":":
            continue
        letter = after[1]
        if letter in MODIFIERS:
            return Finding("zsh-colon-modifier", _colon_reason(exp), "$%s:%s" % (exp.name, letter))
        if letter == "g" and len(after) > 2 and after[2] in "s&":
            # `:g` is a prefix, and an unquoted `&` is the background operator
            # rather than the second half of the modifier.
            if after[2] == "&" and exp.context in PARSING:
                continue
            return Finding("zsh-colon-modifier", _colon_reason(exp), "$%s:g%s" % (exp.name, after[2]))
    return None


def _colon_reason(exp):
    where = {
        "heredoc": " The body of a heredoc whose delimiter is unquoted is expanded, so quoting the delimiter also fixes it.",
        "dquote": " Double quotes do not protect it.",
    }.get(exp.context, "")
    return (
        "zsh reads $%s%s as a modifier, so the text after the colon loses its "
        "first letters. Brace the name: ${%s}%s, or ${%s%s} if the modifier was "
        "meant.%s" % (exp.name, exp.next3, exp.name, exp.next3, exp.name,
                      exp.next3[:2], where)
    )


# -- zsh-pipestatus ---------------------------------------------------------


def zsh_pipestatus(parse, shell):
    """The bash array zsh does not have."""
    if not _zsh(shell):
        return None
    for exp in parse.expansions:
        if exp.name == "PIPESTATUS":
            return Finding(
                "zsh-pipestatus",
                "zsh has no PIPESTATUS, so this is an empty string and the exit "
                "code is lost. Read $? without the pipe -- "
                'cmd > log 2>&1; echo "exit=$?"; tail -n 20 log -- or '
                "${pipestatus[1]} immediately after the pipeline.",
                "$" + exp.name,
            )
    return None


# -- zsh-unsplit-variable ---------------------------------------------------

_ARRAY_ASSIGN = re.compile(r"(?:^|[\s;&|(])([A-Za-z_]\w*)=\(")
_BARE_NAME = re.compile(r"^\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))$")


def zsh_unsplit_variable(parse, shell, command):
    """A list that is one unquoted scalar, where splitting was the point."""
    if not _zsh(shell):
        return None
    arrays = set(m.group(1) for m in _ARRAY_ASSIGN.finditer(command))

    for words in parse.commands:
        texts = [w.text for w in words]
        if len(words) == 4 and texts[0] == "for" and texts[2] == "in":
            candidate, quoted = words[3], words[3].quoted
        elif len(texts) == 2 and texts[0] == "set" and texts[1] == "--":
            continue
        elif len(texts) == 3 and texts[0] == "set" and texts[1] == "--":
            candidate, quoted = words[2], words[2].quoted
        else:
            continue
        if quoted:
            continue
        match = _BARE_NAME.match(candidate.text)
        if match is None:
            continue
        name = match.group(1) or match.group(2)
        if name in arrays:
            # An unquoted array does expand element by element, so this loop is
            # correct and denying it would cost a working command.
            continue
        return Finding(
            "zsh-unsplit-variable",
            "zsh does not split an unquoted scalar, so $%s is one argument -- "
            "one iteration. Write ${=%s} to split on whitespace, ${(f)%s} on "
            'lines, or "$%s" if one was meant.' % (name, name, name, name),
            candidate.text,
        )
    return None


# -- git-checkout -----------------------------------------------------------


def git_checkout(parse, subcommand_of):
    """A checkout with an operand, which always has an exact equivalent."""
    for words in parse.commands:
        texts = [w.text for w in words]
        if texts[:1] != ["git"]:
            continue
        sub = subcommand_of(texts)
        if sub != "checkout":
            continue
        index = texts.index("checkout")
        if len(texts) <= index + 1:
            # A checkout with no operand changes nothing and there is no row to
            # name a replacement from, so there is nothing to say.
            continue
        return Finding(
            "git-checkout",
            "checkout covers several unrelated jobs and a wrong keystroke "
            "silently performs another one. Use the command that does one "
            "thing:\n" + CHECKOUT_REWRITE,
            " ".join(texts[:index + 2]),
        )
    return None


# -- git-push-door ----------------------------------------------------------


def git_push_door(parse, subcommand_of):
    """Every push goes through the publish script, which reads the server.

    **This sees the tool call and nothing else**, so the script's own push -- a
    child process the script starts -- never passes through here. That is the
    whole reason a door at this level is affordable.

    The failure it answers is the one no spelling rule reaches: a hash verified
    at one moment and a different HEAD pushed a minute later. No rule about the
    command's shape can see that, because both commands are well formed. It is
    also what keeps a force or a deletion off `main` and `dev`: the server
    protects neither branch, and the script refuses both.
    """
    for words in parse.commands:
        texts = [w.text for w in words]
        if texts[:1] != ["git"] or subcommand_of(texts) != "push":
            continue
        return Finding(
            "git-push-door",
            "publishing goes through scripts/publish.sh, which reads the hash "
            "and the server in the same command and asks the server afterwards. "
            "Run it instead: scripts/publish.sh <hash> <dest>; a rewritten "
            "branch is re-published with scripts/publish.sh --rebased <hash> "
            "<branch>. If the script refuses or is wrong, report that rather "
            "than pushing by hand.",
            " ".join(texts[:3]),
        )
    return None


# -- the roster -------------------------------------------------------------

#: The role token of the seat's roster row. A row is
#: `<session-uuid> <name> [seat] [window=<tokens>]`, and the seat is the row
#: whose third field is exactly this; a lane's row has no role.
SEAT_ROLE = "seat"


def roster_row(roster, session_id):
    """The fields of the roster row whose first field is `session_id`, or None.

    A comment or blank line is skipped, and a match is only ever a row's own
    first field: the id written anywhere else on a line names no row.
    """
    if roster is None or not session_id:
        return None
    for line in roster.splitlines():
        parts = line.strip().split()
        if not parts or parts[0].startswith("#"):
            continue
        if parts[0] == session_id:
            return parts
    return None


# -- notepad-absent ---------------------------------------------------------


def notepad_absent(roster, session_id, notepad_exists, root_present):
    """Whether a session the roster names has kept no notepad at all.

    True only when the session root is present, `roster` has a row for this
    session -- the seat's or a lane's -- and no notepad exists for it. Silent
    whenever it cannot tell: the root absent, the roster unreadable (`roster`
    is None), or the session anonymous (`session_id` empty).
    """
    if not root_present or notepad_exists:
        return False
    return roster_row(roster, session_id) is not None


# -- agent-grant ------------------------------------------------------------

#: The tool names a subagent is launched through, old and new.
AGENT_TOOLS = ("Agent", "Task")


def agent_grant(tool_name, roster, session_id, grants, session_name,
                grants_path="grants/subagents"):
    """A subagent launched by a session that is not the seat and holds no grant.

    Only the operator grants subagents, and a relay is not a grant. The seat is
    the session whose roster row carries the role `seat` as its third field; a
    grant is a line of the grant file whose first field is the session's name,
    written by the seat when the operator grants in the seat's own prompt.
    Comment and blank lines are skipped.

    Silent whenever it cannot tell: the roster or the grant file unreadable
    (None), or the session anonymous. An absent grant file is passed as "" and
    means nobody holds one. `grants_path` is only named in the refusal.
    """
    if tool_name not in AGENT_TOOLS:
        return None
    if roster is None or grants is None or not session_id:
        return None
    row = roster_row(roster, session_id)
    if row is not None and len(row) > 2 and row[2] == SEAT_ROLE:
        return None
    name = (session_name or "").strip()
    for line in grants.splitlines():
        parts = line.strip().split()
        if parts and not parts[0].startswith("#") and name and parts[0] == name:
            return None
    return Finding(
        "agent-grant",
        "Subagents are granted by the operator, per session, and %s holds no "
        "grant: its name is not a line of %s. A relay is not a grant. Do the "
        "work in this session, or ask the seat to put the question to the "
        "operator." % (name or "this session", grants_path),
        tool_name,
    )

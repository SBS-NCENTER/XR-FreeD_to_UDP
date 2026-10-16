"""Read a shell command the way the shell that runs it does, and no further.

What a caller gets is a list of simple commands -- each a list of words with
quotes removed and expansions left as their source text -- and a list of every
parameter expansion the shell would perform, each with the context it sits in.

**A parse this will not commit to raises, and every caller reads that as allow.**
An unterminated quote or command substitution is the whole of that set: the text
means something the reader cannot determine, and guessing is the one thing a
guard must not do.

Nothing here runs a command or touches a file.
"""

MODIFIER_CONTEXTS = ("unquoted", "dquote", "cmdsub", "heredoc")

#: Stripped from the front of a command. `command` and `exec` take no options of
#: their own here; the wrappers below do, and each says how many words it eats.
KEYWORDS = {
    "if", "then", "else", "elif", "fi", "while", "until", "do", "done",
    "case", "esac", "function", "time", "!", "coproc",
}

#: A wrapper stands in front of the command that matters. The value is a
#: callable that consumes the wrapper's own options and returns the index where
#: the real command starts.
WRAPPERS = {"sudo", "env", "timeout", "nice", "nohup", "command", "exec", "stdbuf", "ionice"}

#: Options of those wrappers that take a separate argument.
WRAPPER_OPTS_WITH_ARG = {
    "sudo": {"-u", "-g", "-C", "-p", "-U", "-r", "-t", "--user", "--group"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e"},
    "ionice": {"-c", "-n", "-p"},
}

#: git's own options that take a separate argument, so its subcommand can be
#: found without mistaking an option's value for it.
GIT_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


class ParseError(Exception):
    """The text will not parse, so no rule may speak about it."""


class Word:
    """One word of a simple command.

    `text` has quotes removed and backslash escapes resolved, and keeps every
    expansion as its source spelling -- a rule reads what the author wrote, not
    what the shell would produce, because the shell has not run yet.
    """

    __slots__ = ("text", "raw", "quoted", "pos")

    def __init__(self, text, raw, quoted, pos):
        self.text = text
        self.raw = raw
        self.quoted = quoted
        self.pos = pos

    def __repr__(self):
        return "Word(%r)" % self.text


class Expansion:
    """One parameter expansion the shell would perform.

    `next3` is the three characters following the name, which is where a zsh
    modifier appears. `braced` separates `${H:t}` from `$H:t`, which differ in
    nothing else and which one rule must tell apart. `flags` is whatever stood
    between the brace and the name, so a caller can see that splitting was asked
    for rather than merely assumed.
    """

    __slots__ = ("name", "next3", "context", "braced", "flags", "pos")

    def __init__(self, name, next3, context, braced, flags, pos):
        self.name = name
        self.next3 = next3
        self.context = context
        self.braced = braced
        self.flags = flags
        self.pos = pos

    def as_dict(self):
        return {
            "name": self.name,
            "next": self.next3,
            "context": self.context,
            "braced": self.braced,
            "flags": self.flags,
            "pos": self.pos,
        }


def _is_name_start(ch):
    return ch.isalpha() or ch == "_"


def _is_name_char(ch):
    return ch.isalnum() or ch == "_"


class _Scanner:
    """Scans one region of the command, appending to shared collections.

    Positions stay absolute throughout, because the three characters after a
    name are read from the original text -- a nested region scanned on its own
    string would report them from the wrong place.
    """

    def __init__(self, text, start, end, context, expansions, jobs):
        self.text = text
        self.i = start
        self.end = end
        self.context = context
        self.expansions = expansions
        self.jobs = jobs
        self.tokens = []
        self.pending_heredocs = []

    # -- small readers ----------------------------------------------------

    def _peek(self, offset=0):
        j = self.i + offset
        return self.text[j] if j < self.end else ""

    def _record(self, name, name_end, context, braced, flags, pos):
        self.expansions.append(
            Expansion(name, self.text[name_end:name_end + 3], context, braced, flags, pos)
        )

    def _matching(self, open_at, opener, closer):
        """Index of the closer that balances `opener`, honouring quotes.

        A closing character inside quotes does not close, which is why this
        cannot be a count of characters.
        """
        depth = 0
        j = open_at
        while j < self.end:
            ch = self.text[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "'":
                j = self.text.find("'", j + 1)
                if j == -1 or j >= self.end:
                    raise ParseError("unterminated single quote")
                j += 1
                continue
            if ch == '"':
                j = self._skip_dquote(j)
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return j
            j += 1
        raise ParseError("unterminated %s" % opener)

    def _skip_dquote(self, j):
        """Past a double-quoted run that begins at `j`."""
        j += 1
        while j < self.end:
            ch = self.text[j]
            if ch == "\\":
                j += 2
                continue
            if ch == '"':
                return j + 1
            j += 1
        raise ParseError("unterminated double quote")

    # -- expansions -------------------------------------------------------

    def _read_expansion(self, context, into):
        """Consume one `$...` at self.i, appending its source text to `into`.

        Returns True when something was consumed. Arithmetic and command
        substitution are consumed here too, because all three begin the same way
        and only the next character tells them apart.
        """
        start = self.i
        nxt = self._peek(1)

        if nxt == "(" and self._peek(2) == "(":
            # Arithmetic. Not a substitution: nothing in it is a command.
            close = self.text.find("))", self.i + 3)
            if close == -1 or close >= self.end:
                raise ParseError("unterminated arithmetic expansion")
            into.append(self.text[start:close + 2])
            self.i = close + 2
            return True

        if nxt == "(":
            close = self._matching(self.i + 1, "(", ")")
            self.jobs.append((self.i + 2, close, "cmdsub"))
            into.append(self.text[start:close + 1])
            self.i = close + 1
            return True

        if nxt == "{":
            close = self._matching(self.i + 1, "{", "}")
            body_start = self.i + 2
            flags, name_start = self._read_flags(body_start, close)
            j = name_start
            while j < close and _is_name_char(self.text[j]):
                j += 1
            name = self.text[name_start:j]
            if name:
                self._record(name, j, context, True, flags, start)
            into.append(self.text[start:close + 1])
            self.i = close + 1
            return True

        if _is_name_start(nxt):
            j = self.i + 1
            while j < self.end and _is_name_char(self.text[j]):
                j += 1
            self._record(self.text[self.i + 1:j], j, context, False, "", start)
            into.append(self.text[start:j])
            self.i = j
            return True

        if nxt.isdigit():
            j = self.i + 1
            while j < self.end and self.text[j].isdigit():
                j += 1
            name = self.text[self.i + 1:j]
            # $0 is reported like any other positional: the shell applies a
            # modifier to it exactly as it does to $1, and `cd $0:h` is the
            # defect that says so.
            self._record(name, j, context, False, "", start)
            into.append(self.text[start:j])
            self.i = j
            return True

        # A punctuation parameter -- $?, $#, $@, $*, $$, $!, $- -- falls through
        # to the caller, which appends the dollar and moves on. The text comes
        # out identical either way, so there is no branch here pretending to
        # decide something.
        return False

    def _read_flags(self, j, close):
        """Whatever stands between `${` and the name: `#`, `=`, or `(f)`."""
        flags = []
        while j < close:
            ch = self.text[j]
            if ch in "#=^~+":
                flags.append(ch)
                j += 1
                continue
            if ch == "(":
                shut = self.text.find(")", j)
                if shut == -1 or shut >= close:
                    break
                flags.append(self.text[j:shut + 1])
                j = shut + 1
                continue
            break
        return "".join(flags), j

    # -- words ------------------------------------------------------------

    def _read_ansi_c(self, parts):
        """`$'...'`, where backslash escapes are resolved and `$` is literal."""
        self.i += 2
        buf = []
        while self.i < self.end:
            ch = self.text[self.i]
            if ch == "\\":
                nxt = self._peek(1)
                buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                self.i += 2
                continue
            if ch == "'":
                self.i += 1
                parts.append("".join(buf))
                return
            buf.append(ch)
            self.i += 1
        raise ParseError("unterminated $' quote")

    def _read_dquote(self, parts):
        self.i += 1
        while self.i < self.end:
            ch = self.text[self.i]
            if ch == '"':
                self.i += 1
                return
            if ch == "\\":
                nxt = self._peek(1)
                if nxt in '$`"\\':
                    parts.append(nxt)
                    self.i += 2
                    continue
                if nxt == "\n":
                    self.i += 2
                    continue
                parts.append(ch)
                self.i += 1
                continue
            if ch == "$":
                # Inside double quotes the context is dquote even when the quotes
                # sit inside a command substitution: the label is the innermost
                # construct, not the outermost.
                if self._read_expansion("dquote", parts):
                    continue
                parts.append(ch)
                self.i += 1
                continue
            if ch == "`":
                self._read_backtick(parts)
                continue
            parts.append(ch)
            self.i += 1
        raise ParseError("unterminated double quote")

    def _read_backtick(self, parts):
        start = self.i
        j = self.i + 1
        while j < self.end:
            if self.text[j] == "\\":
                j += 2
                continue
            if self.text[j] == "`":
                self.jobs.append((start + 1, j, "cmdsub"))
                parts.append(self.text[start:j + 1])
                self.i = j + 1
                return
            j += 1
        raise ParseError("unterminated backtick")

    def _read_word(self):
        start = self.i
        parts = []
        quoted = False
        while self.i < self.end:
            ch = self.text[self.i]
            if ch in " \t\n":
                break
            if ch in ";&|<>()":
                break
            if ch == "\\":
                nxt = self._peek(1)
                if nxt == "\n":
                    self.i += 2
                    continue
                parts.append(nxt)
                self.i += 2
                continue
            if ch == "'":
                close = self.text.find("'", self.i + 1)
                if close == -1 or close >= self.end:
                    raise ParseError("unterminated single quote")
                parts.append(self.text[self.i + 1:close])
                quoted = True
                self.i = close + 1
                continue
            if ch == '"':
                quoted = True
                self._read_dquote(parts)
                continue
            if ch == "`":
                self._read_backtick(parts)
                continue
            if ch == "$":
                if self._peek(1) == "'":
                    quoted = True
                    self._read_ansi_c(parts)
                    continue
                if self._read_expansion(self.context, parts):
                    continue
                parts.append(ch)
                self.i += 1
                continue
            parts.append(ch)
            self.i += 1
        return Word("".join(parts), self.text[start:self.i], quoted, start)

    # -- redirections and heredocs ----------------------------------------

    def _at_word_start(self):
        if self.i == 0:
            return True
        prev = self.text[self.i - 1]
        return prev in " \t\n;&|()"

    def _read_delimiter(self):
        """A heredoc's delimiter, and whether any part of it was quoted.

        Quoting any part makes the whole body inert, which is why this reports
        one flag for the word rather than one per piece.
        """
        while self.i < self.end and self.text[self.i] in " \t":
            self.i += 1
        parts = []
        quoted = False
        while self.i < self.end:
            ch = self.text[self.i]
            if ch in " \t\n;&|<>()":
                break
            if ch == "\\":
                quoted = True
                parts.append(self._peek(1))
                self.i += 2
                continue
            if ch in "'\"":
                quoted = True
                close = self.text.find(ch, self.i + 1)
                if close == -1 or close >= self.end:
                    raise ParseError("unterminated heredoc delimiter")
                parts.append(self.text[self.i + 1:close])
                self.i = close + 1
                continue
            parts.append(ch)
            self.i += 1
        return "".join(parts), quoted

    def _try_redirection(self):
        """Consume a redirection and its target, which never join an argv."""
        j = self.i
        if not self._at_word_start() and not self.text[self.i] in "<>":
            pass
        while j < self.end and self.text[j].isdigit():
            j += 1
        three, two, one = self.text[j:j + 3], self.text[j:j + 2], self.text[j:j + 1]
        if three in ("<<<",):
            op, k = three, j + 3
        elif three == "<<-":
            op, k = three, j + 3
        elif two in ("<<", ">>", ">&", "<&", ">|"):
            op, k = two, j + 2
        elif two == "&>" and self.i == j:
            op, k = two, j + 2
        elif one in ("<", ">"):
            op, k = one, j + 1
        else:
            return False

        self.i = k
        if op in ("<<", "<<-"):
            delim, quoted = self._read_delimiter()
            self.pending_heredocs.append((delim, quoted, op == "<<-"))
            return True

        while self.i < self.end and self.text[self.i] in " \t":
            self.i += 1
        # The target is read rather than skipped: it is not part of the argv, but
        # an expansion inside it is still one the shell performs.
        if self.i < self.end and self.text[self.i] not in "\n;&|()":
            self._read_word()
        return True

    def _consume_heredocs(self):
        for delim, quoted, strip in self.pending_heredocs:
            body_start = self.i
            body_end = self.end
            after = self.end
            j = self.i
            while j <= self.end:
                nl = self.text.find("\n", j)
                line_end = self.end if nl == -1 or nl > self.end else nl
                line = self.text[j:line_end]
                if (line.lstrip("\t") if strip else line) == delim:
                    body_end = j
                    after = line_end + 1 if line_end < self.end else self.end
                    break
                if line_end >= self.end:
                    # No terminator. The shell reads the body to the end of the
                    # input and runs it, so this is a body and not an error.
                    body_end = self.end
                    after = self.end
                    break
                j = line_end + 1
            if not quoted:
                self._scan_heredoc_body(body_start, body_end)
            self.i = after
        self.pending_heredocs = []

    def _scan_heredoc_body(self, start, end):
        """Record what the shell expands in a live heredoc body.

        Quotes in the body are literal, so they are not skipped; a command
        substitution is not, and its command is listed like any other.
        """
        saved_end = self.end
        self.end = end
        j = start
        while j < end:
            ch = self.text[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "$":
                self.i = j
                if self._read_expansion("heredoc", []):
                    j = self.i
                    continue
                j += 1
                continue
            if ch == "`":
                self.i = j
                self._read_backtick([])
                j = self.i
                continue
            j += 1
        self.end = saved_end

    # -- the loop ---------------------------------------------------------

    def scan(self):
        while self.i < self.end:
            ch = self.text[self.i]
            if ch in " \t":
                self.i += 1
                continue
            if ch == "\\" and self._peek(1) == "\n":
                self.i += 2
                continue
            if ch == "\n":
                self.tokens.append(("op", "\n"))
                self.i += 1
                self._consume_heredocs()
                continue
            if ch == "#" and self._at_word_start():
                nl = self.text.find("\n", self.i)
                self.i = self.end if nl == -1 or nl > self.end else nl
                continue
            if self._try_redirection():
                continue
            two = self.text[self.i:self.i + 2]
            if two in ("&&", "||", ";;", "|&"):
                self.tokens.append(("op", two))
                self.i += 2
                continue
            if ch in ";&|()":
                self.tokens.append(("op", ch))
                self.i += 1
                continue
            if ch in "{}" and (self._peek(1) in " \t\n;&|()" or self.i + 1 >= self.end):
                # A brace standing alone groups commands. `{a,b}` is a word, and
                # the difference is exactly whether something follows it.
                self.tokens.append(("op", ch))
                self.i += 1
                continue
            before = self.i
            word = self._read_word()
            if self.i == before:
                self.i += 1
                continue
            self.tokens.append(("word", word))
        if self.pending_heredocs:
            self._consume_heredocs()
        return self.tokens


def _is_assignment(word):
    """`NAME=...` written unquoted at the front of a command."""
    raw = word.raw
    j = 0
    while j < len(raw) and (raw[j].isalnum() or raw[j] == "_"):
        j += 1
    return j > 0 and not raw[0].isdigit() and j < len(raw) and raw[j] == "="


def _skip_wrapper(words, i):
    name = words[i].text
    opts = WRAPPER_OPTS_WITH_ARG.get(name, set())
    j = i + 1
    while j < len(words):
        text = words[j].text
        if text.startswith("-") and len(text) > 1:
            j += 2 if text in opts else 1
            continue
        break
    if name == "timeout" and j < len(words):
        # timeout's first positional is the duration, not the command.
        j += 1
    return j


def _strip(words):
    """Drop what stands in front of the command that matters."""
    i = 0
    while i < len(words):
        word = words[i]
        if not word.quoted and word.text in KEYWORDS:
            i += 1
            continue
        if not word.quoted and word.text in ("for", "select"):
            # A loop header is itself a simple command: it is where a list is
            # written, and a list is what one rule reads.
            return words[i:]
        if _is_assignment(word):
            i += 1
            continue
        if not word.quoted and word.text in WRAPPERS:
            after = _skip_wrapper(words, i)
            if after == i:
                break
            i = after
            continue
        break
    return words[i:]


def _build(tokens):
    """Group tokens into simple commands, each with the position it starts at."""
    groups = []
    current = []
    for kind, value in tokens:
        if kind == "op":
            if current:
                groups.append(current)
                current = []
            continue
        current.append(value)
    if current:
        groups.append(current)

    out = []
    for group in groups:
        words = _strip(group)
        if words:
            out.append((group[0].pos, words))
    return out


def git_subcommand(words):
    """git's subcommand, found past its global options, or None.

    The options stay in the argv: a rule that wants to quote the command back
    needs what was written, and only the subcommand is being located here.
    """
    if not words or words[0] != "git":
        return None
    i = 1
    while i < len(words):
        text = words[i]
        if text.startswith("-"):
            if "=" in text:
                i += 1
                continue
            i += 2 if text in GIT_OPTS_WITH_ARG else 1
            continue
        return text
    return None


class Parse:
    """What a rule is given: the commands, their words, and every expansion."""

    __slots__ = ("commands", "expansions")

    def __init__(self, commands, expansions):
        self.commands = commands
        self.expansions = expansions

    @property
    def simple_commands(self):
        return [[word.text for word in words] for words in self.commands]

    def as_dict(self):
        return {
            "simple_commands": self.simple_commands,
            "expansions": [e.as_dict() for e in self.expansions],
        }


def lex(command):
    """Parse one command string, or raise ParseError.

    Commands are returned in the source order of their first word, so a command
    substitution's contents follow the command that contains it.
    """
    expansions = []
    jobs = []
    found = []

    scanner = _Scanner(command, 0, len(command), "unquoted", expansions, jobs)
    found.extend(_build(scanner.scan()))

    done = 0
    while done < len(jobs):
        start, end, context = jobs[done]
        done += 1
        inner = _Scanner(command, start, end, context, expansions, jobs)
        found.extend(_build(inner.scan()))

    found.sort(key=lambda pair: pair[0])
    expansions.sort(key=lambda exp: exp.pos)
    return Parse([words for _, words in found], expansions)

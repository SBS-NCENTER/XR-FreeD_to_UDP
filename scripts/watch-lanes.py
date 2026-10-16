#!/usr/bin/env python3
"""Emit one line per state transition for every session in the roster.

A roster row is `<session-uuid> <name> [seat] [window=<tokens>]`: the seat's
row carries the role `seat` as its third field, and a lane's row carries no
role. `window=` names the model window the row's lines scale against, the
default 1 000 000 when absent.

Context in use is the sum of input_tokens, cache_read_input_tokens and
cache_creation_input_tokens on the last assistant message of a session's
transcript, over its window, unless a compaction follows that message: then
it is what the compaction left. A session at its line is COMPACT: 70 % for the
seat's row and 50 % for a lane's. WARN is the notice, five points below the
line.

Silence is not success, so quiet is a reported state too: a session that
ended, crashed or was closed writes nothing, and a watcher that only knows the
happy path cannot tell that from still working.  QUIET says go and look; it is
not a verdict that the session is dead.

A session parked on a permission prompt or another dialog is neither thinking
nor finished, and it looks like the former: its token count is static and its
turn is open. Claude Code writes that fact to the session's own record as
`status: "waiting"` with a `waitingFor` (`permission prompt`, `input needed`,
`dialog open`, `sandbox request`), so BLOCKED is read from the record and not
guessed from the transcript. It matters more than QUIET because the seat's
usual remedy does not reach it: a cross-session message queues behind the
dialog and is read only once the dialog is answered.

Every emitted line is a transition, so a persistent monitor stays quiet once a
state has been reported and speaks again only when it changes.

It is configured by environment only and takes one argument, `--self-test`.
Any other argument is refused before the lock is taken: a watcher started by a
mistyped flag on a quiet host would take transitions nobody is reading, and a
transition taken is gone.
"""
import fcntl, glob, json, os, sys, time

# The project directory Claude Code derives from a checkout's path, the path
# with every `/` as `-`. LANE_PROJECT names the primary's; otherwise the
# current directory's is used, and every other project directory is searched
# after it.
PROJ   = os.environ.get("LANE_PROJECT") or os.path.join(
    os.path.expanduser("~/.claude/projects"), os.getcwd().replace("/", "-"))
WINDOW = 1_000_000
# The session root every record lives under, ~/draft when none is named.
RECORDS = os.environ.get("HARNESS_SESSION_ROOT") or os.path.expanduser("~/draft")
ROSTER = os.environ.get("LANE_ROSTER", os.path.join(RECORDS, "lane-watch.roster"))
# The roster declares a role. Whether a session is running is derived, from
# the index Claude Code writes per process.
SESSIONS = os.environ.get("LANE_SESSIONS",
                          os.path.expanduser("~/.claude/sessions"))
STATE  = os.environ.get("LANE_STATE",  os.path.join(RECORDS, "lane-watch.state"))
# A lock file beside STATE rather than a flock on STATE itself, so this
# guarantee is independent of save_state()'s own tmp-then-rename dance --
# neither has to reason about the other's open file descriptors.
LOCK   = STATE + ".lock"
# The role token of the seat's row, and each role's line in percent of its
# window. A session finishes what it is on, brings its notepad up to date and
# compacts at the line; nothing is prepared before it.
SEAT   = "seat"
LINES  = {SEAT: 70, "lane": 50}
# The notice comes this many points below the line.
NOTICE = 5
STALE  = int(os.environ.get("LANE_STALE", 2700))
# A dialog that is up for a few seconds is a session working normally, and
# every permission prompt passes through `waiting`. Reported only once the
# record has said so for this long, or a sweep landing inside a prompt would
# be a line.
BLOCKED_AFTER = int(os.environ.get("LANE_BLOCKED", 120))
PERIOD = int(os.environ.get("LANE_PERIOD", 60))
# A transition-only watcher is silent when nothing changes AND silent when
# it has died, and the reader cannot tell those apart. The heartbeat is the
# only line that says the watcher itself is running; it is one line an hour
# so that it stays readable rather than becoming the noise it exists to
# break.
BEAT   = int(os.environ.get("LANE_HEARTBEAT", 3600))
ONCE   = os.environ.get("LANE_ONCE") == "1"


def roster():
    """(session id, name, role, window) per non-comment roster line.

    The role is `seat` when the row's third field is exactly that, and `lane`
    otherwise. The window is the row's `window=<tokens>`, `WINDOW` when it
    carries none.
    """
    lanes = []
    with open(ROSTER) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            sid = parts[0]
            name = parts[1] if len(parts) > 1 else sid
            role = SEAT if parts[2:3] == [SEAT] else "lane"
            window = next((int(r.split("=", 1)[1]) for r in parts[2:]
                           if r.startswith("window=")), WINDOW)
            lanes.append((sid, name, role, window))
    return lanes


def transcript(sid):
    """Path to a session's transcript, in whichever project directory holds it.

    A project directory is derived from the session's cwd, so a session
    opened in a worktree writes under that worktree's directory and not the
    primary's; a row looked up under the primary alone would read GONE for a
    session that is working. So the primary's directory is tried first and
    every other project directory after it.

    Searching cannot resolve to the wrong session: a session id is unique
    across the whole store. The direct path is also what is returned when
    nothing is found, so an id with no transcript still classifies GONE.
    """
    direct = os.path.join(PROJ, sid + ".jsonl")
    if os.path.exists(direct):
        return direct
    found = glob.glob(os.path.join(os.path.dirname(PROJ), "*", sid + ".jsonl"))
    return found[0] if found else direct


def context_tokens(path):
    """Sum the usage on the last assistant message that REPORTS any. None if unreadable.

    A record whose three fields are all zero is not a reading of zero; it is a
    message that made no API call, and one of them arriving last would read a
    working session as empty, and only while it is last, so the error
    flickers rather than persisting.

    Taking the last NON-ZERO record trades a possible false HIGH, after a
    context clear, for a guaranteed false ZERO on a healthy session.  That is
    the right direction: a false high announces a line early and is loud, a
    false zero misses it and is silent.

    A transcript with no usage records at all returns 0, which is what a
    session that has made no call yet reads.

    A compaction voids every reading before it. Its boundary record carries
    the tokens it left as `postTokens` when Claude Code wrote them, and an
    idle compaction writes none, so it reads 0 until the next call. Kept, the
    reading before it would hold a compacted session at its old line for as
    long as it stays idle.
    """
    last = 0
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if '"compact_boundary"' in line:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("subtype") == "compact_boundary":
                        last = (d.get("compactMetadata") or {}).get("postTokens") or 0
                    continue
                if '"usage"' not in line:
                    continue
                try:
                    u = (json.loads(line).get("message") or {}).get("usage")
                except Exception:
                    continue
                if not u:
                    continue
                total = (u.get("input_tokens", 0)
                         + u.get("cache_read_input_tokens", 0)
                         + u.get("cache_creation_input_tokens", 0))
                if total:
                    last = total
    except OSError:
        return None
    return last


def live_sessions():
    """{session id: {pid, name, status, waitingFor, statusUpdatedAt, tmux}} for
    every session running now.

    Claude Code writes ~/.claude/sessions/<pid>.json carrying sessionId,
    name, tmux pane, status and procStart. `status` is busy, idle, shell or
    waiting; `waitingFor` names the dialog when it is waiting, and
    `statusUpdatedAt` is the millisecond epoch at which the status last moved. A row is kept only when
    /proc/<pid> exists AND its start time matches procStart, so a recycled
    pid cannot present itself as the session that used to hold it. The
    socket file alone is not proof: it outlives the process.

    Failure is silent and empty on purpose -- this derivation adds a
    signal and must never remove one, so a watcher that cannot read the
    index still reports every line it did before.
    """
    out = {}
    try:
        names = os.listdir(SESSIONS)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS, fn)) as fh:
                d = json.load(fh)
            pid = int(d["pid"])
            with open(f"/proc/{pid}/stat") as fh:
                started = int(fh.read().rsplit(")", 1)[1].split()[19])
            # Both sides are coerced because the index writes procStart as a
            # STRING and /proc yields an int. Compared as they arrive, every
            # row is skipped and this function returns empty -- which looks
            # exactly like "no sessions are running" and adds no signal.
            want = d.get("procStart")
            want = None if want is None else int(want)
        except Exception:
            continue
        if want is not None and want != started:
            continue
        sid = d.get("sessionId")
        if sid:
            out[sid] = {"pid": pid, "name": d.get("name"),
                        "status": d.get("status"),
                        "waitingFor": d.get("waitingFor"),
                        "statusUpdatedAt": d.get("statusUpdatedAt"),
                        "tmux": d.get("tmux")}
    return out


def classify(path, role="lane", live=None, window=WINDOW):
    """(state, ctx, age) -- state is one of GONE, BLOCKED, COMPACT, WARN, QUIET, OK.

    BLOCKED is decided by the session's own record and outranks every line
    below: a session parked on a dialog cannot act on a COMPACT or answer a
    message, so the dialog is the line worth reading. Its `age` is the seconds
    the record has said `waiting`, not the transcript's silence.

    The role's line and its notice are percentages of the row's own window,
    so a row with a smaller window crosses the same percentage rather than
    the same token count.
    """
    if not os.path.exists(path):
        return "GONE", None, None
    ctx = context_tokens(path)
    age = int(time.time() - os.stat(path).st_mtime)
    if ctx is None:
        return "GONE", None, age
    if (live or {}).get("status") == "waiting":
        since = live.get("statusUpdatedAt")
        waited = age if not since else int(time.time() - since / 1000)
        if waited >= BLOCKED_AFTER:
            return "BLOCKED", ctx, waited
    line = LINES.get(role, LINES["lane"])
    if ctx >= window * line / 100:
        return "COMPACT", ctx, age
    if age > STALE:
        return "QUIET", ctx, age
    if ctx >= window * (line - NOTICE) / 100:
        return "WARN", ctx, age
    return "OK", ctx, age


def load_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh)
    os.replace(tmp, STATE)   # atomic: a torn write would read as "never seen"


def describe(name, state, ctx, age, prev, role="lane", window=WINDOW, live=None):
    pct = "?" if ctx is None else f"{ctx / window * 100:.1f}%"
    tok = "?" if ctx is None else f"{ctx} tok"
    if state == "BLOCKED":
        what = (live or {}).get("waitingFor") or "dialog"
        # `tmux` reads session:window.pane, and the pane is what capture-pane takes.
        pane = ((live or {}).get("tmux") or "").rsplit(".", 1)[-1]
        look = f"tmux capture-pane -p -t {pane}" if pane.startswith("%") \
            else "its tmux pane"
        return (f"BLOCKED {name} has waited {age}s on a {what} (at {pct}) -- "
                f"its own messages queue behind that dialog, so ask nothing "
                f"of it. Read the pane first: {look}. Answer it only if every "
                f"option is a refusal; a prompt that offers to grant "
                f"anything goes to the operator")
    if state == "OK" and prev == "BLOCKED":
        return (f"OK {name} is no longer waiting on a dialog, at {pct} "
                f"({tok})")
    if state == "COMPACT":
        who = ("the operator enters it" if role == SEAT
               else "the seat sends it to the lane's pane")
        return (f"COMPACT {name} is at {pct} ({tok}) -- at its "
                f"{LINES.get(role, LINES['lane'])} % line. It FINISHES what it "
                f"is on and brings its notepad up to date; then /compact at "
                f"that boundary, and {who}")
    if state == "WARN":
        return (f"WARN {name} is at {pct} ({tok}) -- five points short of its line. Notice "
                f"only: nothing is prepared until the line, and a boundary "
                f"between tasks that comes first is the better place to compact")
    if state == "QUIET":
        return (f"QUIET {name} has written nothing for {age}s (at {pct}) -- "
                f"check whether it stopped: read its last message before its tree")
    if state == "GONE":
        return (f"GONE {name} has no transcript at the rostered path -- "
                f"identify the session again before trusting this watch")
    # Not "resumed": OK-after-not-OK is also what a roster row changing role
    # prints, because the line moved rather than the session, and what a
    # transcript that resolves for the first time prints.
    return (f"OK {name} now reads OK at {pct} ({tok}), was {prev} -- the session "
            f"may have moved, or the roster row may have")


def heartbeat():
    """One line: the watcher is alive, and where every rostered session stands."""
    named, quiet, unreadable = [], 0, 0
    live = live_sessions()
    for sid, name, role, window in roster():
        state, ctx, age = classify(transcript(sid), role, live.get(sid), window)
        if ctx is None:
            unreadable += 1
            continue
        if state == "QUIET":
            quiet += 1
        named.append(f"{name} {state} {ctx / window * 100:.1f}% "
                     f"silent {age // 60}m")
    when = time.strftime("%H:%M")
    alive = "watcher alive" if not quiet else f"watcher alive, {quiet} QUIET"
    tail = f" | UNREADABLE {unreadable}" if unreadable else ""
    return f"HEARTBEAT {when} {alive} -- " + ", ".join(named) + tail


def sweep():
    st = load_state()
    live = live_sessions()
    changed = False
    for sid, name, role, window in roster():
        path = transcript(sid)
        state, ctx, age = classify(path, role, live.get(sid), window)
        prev = st.get(sid)
        if state == prev:
            continue
        # OK is only worth a line when it follows something that was not OK.
        if state != "OK" or prev not in (None, "OK"):
            print(describe(name, state, ctx, age, prev, role, window,
                           live.get(sid)),
                  flush=True)
        st[sid] = state
        changed = True
    if changed:
        save_state(st)


def acquire_lock():
    """An exclusive advisory lock on LOCK, held for this process's whole
    life and released only when it ends -- fcntl's own guarantee, since the
    kernel drops the lock when the last descriptor referencing it closes,
    which a crash does as surely as a clean exit.

    Refuses loudly rather than starting a second watcher against the shared
    state file: two watchers racing sweep() and save_state() do not
    duplicate a transition, they consume it once between them, possibly
    into the one that is about to close. The returned handle must be kept
    referenced for the life of the process -- closing it, even by letting it
    be garbage-collected, releases the lock early.
    """
    fh = open(LOCK, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.seek(0)
        holder = fh.read().strip() or "unknown"
        fh.close()
        print(f"REFUSED another watcher already holds {LOCK} (pid {holder})",
              flush=True)
        sys.exit(1)
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def self_test():
    """Drive the argument refusal on a child, against a throwaway state path,
    and the two lines through one sweep over a roster and transcripts of its
    own.

    The child is this script, so what is tested is the refusal a session meets,
    not the shape of a parser. Each case also checks that no lock file appeared,
    which is what separates a refusal from a watcher that started and then died.
    """
    import contextlib, io, subprocess, tempfile
    global PROJ, ROSTER, SESSIONS, STATE
    failures = 0
    total = 0
    with tempfile.TemporaryDirectory() as tmp:
        roster_path = os.path.join(tmp, "roster")
        open(roster_path, "w").close()
        env = dict(os.environ, LANE_STATE=os.path.join(tmp, "state"),
                   LANE_ROSTER=roster_path, LANE_ONCE="1")
        lock = os.path.join(tmp, "state.lock")
        cases = (
            ("an unknown flag is refused, naming it", ["--selftest"], 2, "--selftest", False),
            ("a bare word is refused, naming it", ["once"], 2, "once", False),
            ("a known flag beside an unknown one is refused", ["--self-test", "-x"], 2, "-x", False),
            ("no argument still runs one sweep", [], 0, "", True),
        )
        for what, args, code, named, locks in cases:
            if os.path.exists(lock):
                os.remove(lock)
            r = subprocess.run([sys.executable, os.path.abspath(__file__), *args],
                               env=env, capture_output=True, text=True, timeout=60)
            ok = (r.returncode == code and named in r.stderr
                  and os.path.exists(lock) == locks)
            print(f"{'ok  ' if ok else 'FAIL'} {what} (exit {r.returncode})")
            failures += not ok
            total += 1
        # With no LANE_* path set, the roster and the state are read and kept
        # under the session root the variable names.
        records = os.path.join(tmp, "records")
        os.makedirs(records)
        open(os.path.join(records, "lane-watch.roster"), "w").close()
        env = {k: v for k, v in os.environ.items() if k not in ("LANE_ROSTER", "LANE_STATE")}
        env.update(HARNESS_SESSION_ROOT=records, LANE_ONCE="1")
        r = subprocess.run([sys.executable, os.path.abspath(__file__)],
                           env=env, capture_output=True, text=True, timeout=60)
        ok = r.returncode == 0 and os.path.exists(os.path.join(records, "lane-watch.state.lock"))
        print(f"{'ok  ' if ok else 'FAIL'} the roster and the state live under HARNESS_SESSION_ROOT "
              f"(exit {r.returncode})")
        failures += not ok
        total += 1

        # The two lines, through roster(), classify(), describe() and one
        # sweep: each row's transcript holds one usage record of `tokens`, and
        # the state is what the row's own line makes of it. An OK on first
        # sight prints nothing, so only the other states are looked for in
        # the output.
        projects = os.path.join(tmp, "projects")
        os.makedirs(os.path.join(projects, "primary"))
        os.makedirs(os.path.join(projects, "worktree"))
        os.makedirs(os.path.join(tmp, "sessions"))
        rows = (
            # (what, row after the id, tokens, state)
            ("the seat below its notice", "seat-a seat", 640_000, "OK"),
            ("the seat at its notice, 65 %", "seat-b seat", 650_000, "WARN"),
            ("the seat at its line, 70 %", "seat-c seat", 700_000, "COMPACT"),
            ("the seat past a lane's line", "seat-d seat", 550_000, "OK"),
            ("a lane below its notice", "lane-a", 440_000, "OK"),
            ("a lane at its notice, 45 %", "lane-b", 450_000, "WARN"),
            ("a lane at its line, 50 %", "lane-c", 500_000, "COMPACT"),
            ("the seat on a smaller window, at 70 % of it", "seat-e seat window=200000", 140_000, "COMPACT"),
            ("the seat on a smaller window, at 65 % of it", "seat-f seat window=200000", 130_000, "WARN"),
            ("a lane on a smaller window, at 50 % of it", "lane-d window=200000", 100_000, "COMPACT"),
            ("a row whose seat is not its third field is a lane's", "lane-e window=1000000 seat", 550_000, "COMPACT"),
            ("a handoff= token is no role", "lane-f handoff=70", 550_000, "COMPACT"),
        )
        with open(roster_path, "w") as fh:
            fh.write("# <session-uuid> <name> [seat] [window=<tokens>]\n\n")
            for i, (_what, row, tokens, _state) in enumerate(rows):
                sid = f"sid-{i}"
                fh.write(f"{sid}  {row}\n")
                # Every other transcript sits in a worktree's project directory,
                # so the search past the primary's is exercised too.
                where = "primary" if i % 2 else "worktree"
                with open(os.path.join(projects, where, sid + ".jsonl"), "w") as tf:
                    usage = {"input_tokens": 0, "cache_read_input_tokens": tokens,
                             "cache_creation_input_tokens": 0}
                    tf.write(json.dumps({"message": {"usage": usage}}) + "\n")
                    # A zero record arriving last is no reading of zero.
                    tf.write(json.dumps({"message": {"usage": {"input_tokens": 0}}}) + "\n")
        PROJ = os.path.join(projects, "primary")
        ROSTER = roster_path
        SESSIONS = os.path.join(tmp, "sessions")
        STATE = os.path.join(tmp, "sweep-state")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sweep()
        printed = {}
        for line in out.getvalue().splitlines():
            parts = line.split()
            if len(parts) > 1:
                printed[parts[1]] = (parts[0], line)
        parsed = {sid: (name, role, window) for sid, name, role, window in roster()}
        for i, (what, row, tokens, want) in enumerate(rows):
            name, role, window = parsed[f"sid-{i}"]
            got = classify(transcript(f"sid-{i}"), role, None, window)[0]
            shown = printed.get(name, ("OK", ""))[0]
            ok = got == want and shown == want
            print(f"{'ok  ' if ok else 'FAIL'} {what}: {want} (classified {got}, printed {shown})")
            failures += not ok
            total += 1
        # The COMPACT line says who enters /compact: the operator for the
        # seat, the seat for a lane.
        seat_line = printed.get("seat-c", ("", ""))[1]
        lane_line = printed.get("lane-c", ("", ""))[1]
        ok = ("70 % line" in seat_line and "the operator enters it" in seat_line
              and "50 % line" in lane_line and "the seat sends it" in lane_line)
        print(f"{'ok  ' if ok else 'FAIL'} the COMPACT line names the row's line and who enters /compact")
        failures += not ok
        total += 1
        # A compaction voids the reading before it: what it left when the
        # boundary says, 0 when it does not, and the next call after it.
        def boundary(post):
            meta = {"trigger": "auto", "preTokens": 480_000}
            if post is not None:
                meta["postTokens"] = post
            return {"type": "system", "subtype": "compact_boundary",
                    "content": "Conversation compacted", "compactMetadata": meta}
        def call(tokens):
            return {"message": {"role": "assistant", "usage": {
                "input_tokens": 0, "cache_read_input_tokens": tokens,
                "cache_creation_input_tokens": 0}}}
        cases = (
            ("a compaction that names what it left reads that", [call(480_000), boundary(7_000)], 7_000),
            ("a compaction that names nothing reads 0", [call(480_000), boundary(None)], 0),
            ("the call after a compaction reads its own usage", [call(480_000), boundary(7_000), call(60_000)], 60_000),
            ("a message that mentions compact_boundary is no compaction",
             [call(480_000), {"message": {"role": "user", "content": "grep compact_boundary"}}], 480_000),
        )
        for i, (what, records, want) in enumerate(cases):
            path = os.path.join(tmp, f"compacted-{i}.jsonl")
            with open(path, "w") as tf:
                for r in records:
                    tf.write(json.dumps(r) + "\n")
            got = context_tokens(path)
            ok = got == want
            print(f"{'ok  ' if ok else 'FAIL'} {what}: {want} (read {got})")
            failures += not ok
            total += 1
        # The sweep recorded what it printed, so a second sweep is silent.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sweep()
        ok = out.getvalue() == ""
        print(f"{'ok  ' if ok else 'FAIL'} a second sweep with nothing changed prints nothing")
        failures += not ok
        total += 1
    print(f"watch-lanes self-test: {total - failures} of {total} hold")
    return 1 if failures else 0


def refuse_arguments(argv):
    """Exit 2 naming the arguments this script does not take.

    `--self-test` counts as known only on its own, so it is not named here, and
    a line that carries it beside anything else is still refused.
    """
    if not argv:
        return
    unknown = [a for a in argv if a != "--self-test"] or argv
    print(f"watch-lanes: refused argument(s) {' '.join(map(repr, unknown))}; the "
          "watcher takes only --self-test, alone, and is configured by LANE_* "
          "environment variables", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    refuse_arguments(sys.argv[1:])
    _lock = acquire_lock()
    # The first beat is due one full interval in, not at start-up: a watcher
    # that announces itself the moment it is armed says nothing the arming did
    # not already say.
    last_beat = time.time()
    while True:
        try:
            sweep()
            if not ONCE and time.time() - last_beat >= BEAT:
                print(heartbeat(), flush=True)
                last_beat = time.time()
        except Exception as exc:                      # a bad poll must not end the watch
            print(f"WATCH ERROR {exc!r}", flush=True)
        if ONCE:
            sys.exit(0)
        time.sleep(PERIOD)

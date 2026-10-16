#!/usr/bin/env python3
"""Fail on a file under scripts/ that carries a shebang and is not executable.

  scripts/check-script-exec-bits.py [--root DIR] [--self-test]

**Why this is a check and not a `chmod`.** A shebang is a claim that the file
runs by its own path, and without the execute bit the shell refuses it with 126
before the file is read. That is the shell's refusal and not a verdict of the
check inside, so a lane that runs a check directly and reports the exit code
reports a number that says nothing about the tree. Every gate here runs these
scripts as `python3 scripts/<name>`, which works whether or not the bit is set,
so nothing notices a script that was added without it.

**What it reads.** The mode git records in the index (`git ls-files -s`), not the
file system: git tracks the execute bit as a mode and nothing else, so the index
is what every other clone receives. A file counts when its first two bytes are
`#!` and its recorded mode is `100644`.

**What it does not reach, stated so the reach is not read as more.** A symlink
or a submodule entry has no execute bit to check. A script with the bit and no
shebang is not flagged: it is a different contradiction and none exists today.
Every tracked file under `scripts/` takes the one rule: nothing there is kept
for reading only. An archive that disclaims being run would be a named exemption
with its reason beside it, added here as a decision, never a match a pattern
makes without anyone looking; none exists today.

Exit 0 when every such file is executable, 1 when any is not, and 2 when the
check cannot run -- not a git checkout, or `git` failing.
"""

import argparse
import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

OK = 0
FAILED = 1
CANNOT_RUN = 2

REGULAR = b"100644"
EXECUTABLE = b"100755"

class CannotRun(Exception):
    """The check could not read the index; distinct from a finding."""


def tracked(root):
    """Every tracked entry under scripts/, as (mode, path) with bytes modes."""
    done = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s", "-z", "--", "scripts"],
        capture_output=True,
    )
    if done.returncode != 0:
        raise CannotRun(
            "git ls-files failed in %s: %s"
            % (root, done.stderr.decode("utf-8", "replace").strip())
        )
    entries = []
    for record in done.stdout.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        entries.append((meta.split()[0], path.decode("utf-8", "surrogateescape")))
    return entries


def shebang_line(root, path):
    """The first line if it starts with `#!`, else None."""
    with open(os.path.join(str(root), path), "rb") as handle:
        first = handle.readline(200)
    if not first.startswith(b"#!"):
        return None
    return first.decode("utf-8", "replace").rstrip()


def violations(root):
    """(path, first line) for each file with a shebang and no execute bit."""
    found = []
    for mode, path in tracked(root):
        if mode != REGULAR:
            continue
        line = shebang_line(root, path)
        if line is not None:
            found.append((path, line))
    return found


def scanned(root):
    """How many regular files were read, for the line that says it looked."""
    return sum(1 for mode, _ in tracked(root) if mode in (REGULAR, EXECUTABLE))


def run(root):
    try:
        found = violations(root)
        total = scanned(root)
    except CannotRun as why:
        print("check-script-exec-bits: cannot run: %s" % why, file=sys.stderr)
        return CANNOT_RUN
    if not found:
        print(
            "check-script-exec-bits: ok -- %d file(s) under scripts/ read, none carries a "
            "shebang without the execute bit" % total
        )
        return OK
    print(
        "check-script-exec-bits: %d file(s) under scripts/ carry a shebang and are not "
        "executable:" % len(found)
    )
    for path, line in found:
        print("  %s  (mode 100644)  %s" % (path, line))
    print(
        "Run by its own path, each is refused with 126 before the file is read. "
        "Fix: chmod +x <path>, then git add it."
    )
    return FAILED


# ---------------------------------------------------------------------------
# --self-test
# ---------------------------------------------------------------------------


def _write(root, relative, text, executable):
    target = pathlib.Path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    target.chmod(0o755 if executable else 0o644)


def _scratch(files):
    """A scratch git checkout holding `files`, as {relative: (text, executable)}."""
    scratch = tempfile.mkdtemp(prefix="check-script-exec-bits-")
    subprocess.run(["git", "-C", scratch, "init", "-q"], check=True)
    for relative, (text, executable) in files.items():
        _write(scratch, relative, text, executable)
    subprocess.run(["git", "-C", scratch, "add", "-A"], check=True)
    return scratch


def self_test():
    """Every branch forced, in a scratch checkout the real tree never sees.

    A check that has only ever printed `ok` has not been shown to be able to
    say anything else, so each way to be red is built and asserted, and the
    exit code of the whole run is asserted after them.
    """
    shebang = "#!/usr/bin/env python3\nprint(1)\n"
    bare = "print(1)\n"
    scratch = _scratch(
        {
            "scripts/ok.py": (shebang, True),
            "scripts/missing.py": (shebang, False),
            "scripts/plain.py": (bare, False),
            "scripts/bare-but-executable.py": (bare, True),
            "scripts/sub/nested-missing.sh": ("#!/usr/bin/env bash\n", False),
            "scripts/deep/kept/for-reading.py": (shebang, False),
            "tools/outside.py": (shebang, False),
        }
    )
    failures = []

    def quiet(root):
        """`run`'s exit code with its own lines swallowed, so the only verdict
        this mode prints is the self-test's."""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run(root)

    def expect(label, got, want):
        if got != want:
            failures.append("%s: got %r, wanted %r" % (label, got, want))

    got = sorted(path for path, _ in violations(scratch))
    expect(
        "which files are red",
        got,
        [
            "scripts/deep/kept/for-reading.py",
            "scripts/missing.py",
            "scripts/sub/nested-missing.sh",
        ],
    )
    expect("a red tree exits 1", quiet(scratch), FAILED)

    for path in got:
        os.chmod(os.path.join(scratch, path), 0o755)
        subprocess.run(["git", "-C", scratch, "add", path], check=True)
    expect("what is left red after fixing them", violations(scratch), [])
    expect("a fixed tree exits 0", quiet(scratch), OK)

    elsewhere = tempfile.mkdtemp(prefix="check-script-exec-bits-nogit-")
    expect("a directory that is no checkout exits 2", quiet(elsewhere), CANNOT_RUN)

    for scratch_dir in (scratch, elsewhere):
        shutil.rmtree(scratch_dir, ignore_errors=True)

    if failures:
        for failure in failures:
            print("check-script-exec-bits: self-test FAILED -- %s" % failure, file=sys.stderr)
        return FAILED
    print("check-script-exec-bits: self-test ok -- red and unrunnable branches both forced")
    return OK


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="the checkout to read (default: this one)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    root = args.root
    if root is None:
        here = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
        )
        if here.returncode != 0:
            print("check-script-exec-bits: cannot run: not inside a git checkout", file=sys.stderr)
            return CANNOT_RUN
        root = here.stdout.strip()
    return run(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

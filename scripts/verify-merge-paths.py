#!/usr/bin/env python3
# The path- and line-level check run by 09.11-ochest02 on every integration
# merge of 2026-09-11, verbatim from the ce488ff run, with one change: the
# literal hash of the merged side (836954e) is sys.argv[1] instead.
#
# Run it AFTER `git merge --no-ff --no-commit <theirs>`, so the index holds
# the merge result and HEAD is our side. It reads the index (`--cached`) for
# single-side paths and the working file for paths both sides changed.
#
# What it proves, and what it does not:
#   - every path changed on one side only equals that side, byte for byte
#   - on a path both sides changed, every non-blank line either side ADDED is
#     present in the result, and no non-blank line either side REMOVED is
#     still there (unless the same side also re-added it)
#   - it is a multiset check on line content: it proves presence, not
#     placement, and a removed line that legitimately occurs elsewhere in the
#     file reads as "still present". Read the counts, then read the hunks.
#
# **Exit 1 when anything is off, 0 when nothing is.** The seat that wrote this
# ran it by hand and read the output, so it never called exit at all -- and a
# sequence that chains it would have read a reported defect as a success. That
# is the failure class this whole issue exists for, and driving its failing
# branch before adopting it is what surfaced it. The exit code is the only
# channel a caller reads without a human in front of it.
#
# `--self-test` builds throwaway repositories and drives the three shapes below
# against a child of this script; it touches nothing in the caller's tree.
import subprocess, collections, os, sys, tempfile


def merged_lines(path):
    """The merge result's lines for a path both sides changed.

    A path both sides deleted is absent from the working tree and has no merged
    content to lose, so it reads as empty rather than raising. A path one side
    deleted while the other changed it reads as empty too, and the changing
    side's added lines then count as missing, which is the right verdict.
    """
    try:
        with open(path) as fh:
            return collections.Counter(fh.read().split("\n"))
    except FileNotFoundError:
        return collections.Counter()


def self_test():
    def run(cwd, *a):
        subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)

    def repo(tmp, ours, theirs):
        """A repo whose base holds a.txt and b.txt, with one commit per side."""
        run(tmp, "git", "init", "-q", "-b", "main")
        run(tmp, "git", "config", "user.email", "t@t")
        run(tmp, "git", "config", "user.name", "t")
        for f in ("a.txt", "b.txt"):
            with open(os.path.join(tmp, f), "w") as fh:
                fh.write(f"{f} one\n{f} two\n")
        run(tmp, "git", "add", "-A")
        run(tmp, "git", "commit", "-qm", "base")
        run(tmp, "git", "branch", "theirs")
        for branch, edit in (("main", ours), ("theirs", theirs)):
            run(tmp, "git", "switch", "-q", branch)
            edit(tmp)
            run(tmp, "git", "add", "-A")
            run(tmp, "git", "commit", "-qm", branch)
        run(tmp, "git", "switch", "-q", "main")

    def delete_a(tmp):
        os.remove(os.path.join(tmp, "a.txt"))

    def append_a(tmp):
        with open(os.path.join(tmp, "a.txt"), "a") as fh:
            fh.write("a.txt three\n")

    def edit_b(tmp):
        with open(os.path.join(tmp, "b.txt"), "a") as fh:
            fh.write("b.txt three\n")

    def both(first, second):
        return lambda tmp: (first(tmp), second(tmp))

    cases = (
        # what, ours, theirs, resolve a deleted modify/delete conflict, exit
        ("a path both sides deleted reports, not raises",
         delete_a, both(delete_a, edit_b), False, 0),
        ("a change dropped by deleting the path is a problem",
         delete_a, append_a, True, 1),
        ("a clean merge of one-side changes passes",
         edit_b, append_a, False, 0),
    )
    bad = 0
    for what, ours, theirs, resolve_deleted, want in cases:
        with tempfile.TemporaryDirectory() as tmp:
            repo(tmp, ours, theirs)
            subprocess.run(["git", "merge", "--no-ff", "--no-commit", "theirs"],
                           cwd=tmp, capture_output=True, text=True)
            if resolve_deleted:
                run(tmp, "git", "rm", "-q", "a.txt")
            r = subprocess.run([sys.executable, os.path.abspath(__file__), "theirs"],
                               cwd=tmp, capture_output=True, text=True)
            ok = r.returncode == want and "Traceback" not in r.stderr
            bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {what} (exit {r.returncode})")
    print(f"verify-merge-paths self-test: {len(cases) - bad} of {len(cases)} hold")
    return 1 if bad else 0


if sys.argv[1:] == ["--self-test"]:
    sys.exit(self_test())

THEIRS = sys.argv[1]
g = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True).stdout
BASE = g("merge-base", "HEAD", THEIRS).strip()
# **The side labels are derived, never written down.** They were literal strings
# from the run this was copied out of -- HEAD read "main side" and the argument
# read "memory lane" -- and the skill's own step 2 puts the LANE on HEAD and main
# in the argument, so both named the other side. The verdict was right and the
# label sent the reader to the wrong branch; the seat who found it still
# mis-stated which side was which while correctly identifying the content. A
# label that is the ref cannot disagree with the ref.
HERE = g("rev-parse", "--abbrev-ref", "HEAD").strip() or "HEAD"
OURS_LABEL, THEIRS_LABEL = f"HEAD {HERE}", f"merged {THEIRS}"
ours = set(g("diff","--name-only",BASE,"HEAD").split()); theirs = set(g("diff","--name-only",BASE,THEIRS).split())
both = sorted(ours & theirs)
print(f"  merge base {BASE[:7]}; {OURS_LABEL} {len(ours)} path(s), {THEIRS_LABEL} {len(theirs)}, both: {both}")
bad = [p for p in sorted(theirs-ours) if g("diff","--cached","--name-only",THEIRS,"--",p).strip()] + [p for p in sorted(ours-theirs) if g("diff","--cached","--name-only","HEAD","--",p).strip()]
print("  single-side paths differing from their side:", bad if bad else "none")
problems = len(bad)
for F in both:
    merged = merged_lines(F)
    for a, b, label in ((BASE, "HEAD", OURS_LABEL), (BASE, THEIRS, THEIRS_LABEL)):
        d = g("diff","-U0",a,b,"--",F).split("\n")
        add = [l[1:] for l in d if l.startswith("+") and not l.startswith("+++")]
        rem = [l[1:] for l in d if l.startswith("-") and not l.startswith("---")]
        miss = [l for l in add if l.strip() and merged[l] == 0]
        kept = [l for l in rem if l.strip() and merged[l] > 0 and l not in add]
        print(f"  {F} [{label}] +{len(add)} -{len(rem)}: added missing {len(miss)}, removed still present {len(kept)}")
        problems += len(miss) + len(kept)

# A count rather than a boolean, because the caller's next question is always
# how much: one missing line is a resolution to redo, forty is a merge to throw
# away.
print("  verify-merge-paths: %d problem(s)" % problems)
sys.exit(1 if problems else 0)

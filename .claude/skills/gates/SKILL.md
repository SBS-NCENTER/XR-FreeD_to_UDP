---
name: gates
description: Use when reading the check of a pull request or of a push to main or production, before saying that a check passes, or when the check is run by hand to diagnose it.
---

# gates — reading the check

The check is the project's `scripts/check.sh`, which a GitHub-hosted runner
starts for every pull request and every push to `main` or `production`
(`.github/workflows/ci.yml`). No session runs it as a step of its work
(`roles/lane.md`); this is how its result is read. It writes no gate
record, so the parts below about a record under `<root>/gates/<hash>/`
describe the source project's runner and not this one: `scripts/publish.sh`
says on one line that no record exists when it moves `production`.

## Where the result is

```sh
gh pr checks <number> --watch          # wait on a pull request's check
gh run view <run id> --log-failed      # the verdict and the end of each red check's log
gh run list --branch main --limit 3    # the check of each push to main
```

For a pull request the commit checked is the branch merged into its base,
`main`, or `production` for a hotfix's second pull request, and the change
measured is that merge against the base. The record is on the host that ran
it, under `<root>/gates/<hash>/`: `<root>` is what `scripts/gates-root.sh`
prints, `~/draft` while it holds its marker file `.harness-session-root`,
and `<hash>` is the commit the runner checked. Each gate's output is
`<gate>.log` there; `progress.txt` takes each red with its log path the
moment its gate exits, so a fix can start while the run finishes;
`summary.tsv` holds the change size, the scope and the verdict, written
whatever the result. `scripts/publish.sh` reads that record before it moves
`production`.

## The verdict lines

A clean run prints one green line and the logs path; a failing run prints
only what is red.

```
green <hash> (<N> gates, <tests line>)     every gate that ran passed; <N> counts them
green <hash> (no gate run: its tree passed in the check of <hash> on <parent>)
                                           a push to main whose tree a pull request's check passed
  logs <root>/gates/<hash>

red <gate> exit=N <logs>/<gate>.log        one line per gate that exited non-zero,
                                           every one the run found
  MOVED                                    HEAD or the tree moved under the run: a failure
  BASE                                     the diff came out empty, green or not
```

## Reading it

- **Read the log a red line names**, not the verdict alone: the verdict says
  whether anything failed, not what.
- **A green line is about the gates that ran.** Say which gates ran, never
  only that the check passed; a scoped green names its packages.
- **Before calling a wall-clock red a finding about the tree**, read the
  `load` and `host` rows of `summary.tsv`: such a test reads the host as much
  as the tree.
- **A red is fixed by a commit on top and a push**, which starts the whole
  check again, never the red gates alone, since the fix can turn another gate
  red.

## Running it by hand

Only to diagnose a check, never as the way a change is verified: a lane
builds and tests through the project's build door while it works, and the
pull request's check is the verdict.

```sh
scripts/run-gates.sh <hash>                 # <hash> is HEAD of the worktree, the tree clean
scripts/run-gates.sh <hash> --since <ref>   # what counts as the change
scripts/run-gates.sh --self-test            # the script's own machinery on a stub gate set
```

- **It refuses unless `HEAD` is the named hash and the tree is clean**, and
  reads both again at the end: commit first, then gate the commit.
- **A result belongs to one hash.** Amending a gated commit's message makes a
  new hash and voids the result even when the tree is byte-identical.

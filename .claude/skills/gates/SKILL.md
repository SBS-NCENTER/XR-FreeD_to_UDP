---
name: gates
description: Use when reading the check of a pull request or of a push to main or production, before saying that a check passes, or when the check is run by hand to diagnose it.
---

# gates — reading the check

The check is the project's `scripts/check.sh`, which a GitHub-hosted runner
starts for every pull request and every push to `main` or `production`
(`.github/workflows/ci.yml`, from chun-harness `templates/ci.yml`). No
session runs it as a step of its work (`roles/lane.md`); this is how its
result is read. The source project ran its own runner,
`origin/scripts/run-gates.sh`, which wrote a record per hash under
`<root>/gates/<hash>/` (`scripts/gates-root.sh` names the root);
`scripts/publish.sh` still looks for that record and says on one line that
none exists.

## Where the result is

```sh
gh pr checks <number> --watch          # wait on a pull request's check
gh run view <run id> --log-failed      # the verdict and the end of each red step's log
gh run list --branch main --limit 3    # the check of each push to main
```

For a pull request the commit checked is the branch merged into its base,
`main`, or `production` for a hotfix's second pull request, as the base
stands at that moment. The record is the run on GitHub: its log holds each
step's output. The job's name is `check`, which `scripts/land-prs.sh` waits
for.

## The verdict

`scripts/check.sh` prints one line per step as it runs (`shellcheck: N
scripts`, a formatter's diff, a test runner's summary) and ends with `green`;
a red step's own output is the last thing printed. Its exit code is the
verdict: a run that printed success and exited non-zero is red.

## Reading it

- **Read the log a red step names**, not the verdict alone: the verdict says
  whether anything failed, not what.
- **A green line is about the steps that ran.** Say which steps ran, never
  only that the check passed; a check cut to part of the tree names the part.
- **Before calling a wall-clock red a finding about the tree**, read the
  runner's own lines: a slow step on a shared runner is the runner's as much
  as the tree's.
- **A red is fixed by a commit on top and a push**, which starts the whole
  check again, never the red step alone, since the fix can turn another step
  red.

## Running it by hand

Only to diagnose a check, never as the way a change is verified: a lane
builds and tests through the project's build door while it works, and the
pull request's check is the verdict.

```sh
git add -A && scripts/check.sh; echo "exit=$?"   # stage first; read the exit directly
```

- **The scripts it lints are the tracked ones**, so a new script is staged
  before it runs; a verdict read through a pipe is the pipe's exit, not the
  check's.
- **A result belongs to one hash.** Amending a checked commit's message makes
  a new hash and voids the result even when the tree is byte-identical.

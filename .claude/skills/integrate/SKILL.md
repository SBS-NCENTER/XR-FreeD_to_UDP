---
name: integrate
description: The seat's. Use when landing a pull request into main, publishing the seat's own change, releasing main to production, or taking a hotfix through main and production. Gives each sequence's commands in order.
---

# integrate — the sequences

The rules these carry out are `roles/seat.md`, under Integrating and
Branches; the branch model is `doc/workflow.md`. Each sequence below is the
order of the commands.

## A pull request into `main`

```
1  read         gh pr diff <number> whole at its head (gh pr view <number> --json headRefOid), and gh pr checks <number>: a green check
2  land         scripts/land-prs.sh --co-author '<Name> <email>' <number>@<head> ...: each in turn, main merged into the branch when main moved, the check on that head watched, then the merge commit pinned to it
3  read main    gh run list --branch main --limit 3: the check of the push each merge made
```

Step 1 reads the check by the `gates` skill. Step 2 runs in the
background; its `LANDED`, `SKIPPED` and `STOPPED` lines are the report, and
its header says what skips one and what stops the train. The
`Co-Authored-By` line is the seat's own model's, the one its commits end
with.

## The seat's own change

```
1  at main      on the seat's branch in the seat's worktree: git fetch -q origin && git merge --ff-only origin/main
2  edit, test   the changed file's own test and nothing else
3  commit       on the seat's branch, never while HEAD is main or production
4  publish      scripts/publish.sh HEAD main: main fast-forwards to the commit
5  read main    the check of that push, at the next turn taken anyway
```

## A release: `main` to `production`

On the operator's word for a deploy:

```
1  choose       main's tip, whose check is green: gh run list --branch main --limit 3; and production holds no commit of its own beside its merges: git rev-list --no-merges origin/main..origin/production prints nothing, else a hotfix's second merge is owed first
2  version      steps 1 and 3 of the seat's own change: the version raised in the manifest and the lockfile's own entries, by an edit; committed with no confirmation of its own, the version named in the stage list posted for the deploy, [skip ci] in the message
3  main         scripts/publish.sh HEAD main
4  merge        git fetch -q origin && git switch -c release/<version> origin/production && git merge --no-ff origin/main -m "release: <version>" -m "$(git log --first-parent --format='- %s' origin/production..origin/main)"
5  production   scripts/publish.sh HEAD production: a merge whose first parent is production's tip; then git switch <the seat's branch> && git branch -D release/<version>
6  ff-primary   scripts/ff-primary.sh
7  deploy       the deploy stages on the release merge, all on the word to deploy; the last stage tags it once the operator confirms its message
```

## A hotfix: upstream first, then `production`

```
1  branch       hotfix/N-<slug> from the release tag, by the lane: git fetch -q origin && git switch -c hotfix/N-<slug> <tag>; the fix and a version commit raising the patch number, with no [skip ci]
2  into main    gh pr create (base main, the default), with Closes #N; on a green check the seat merges it by hand, never by land-prs.sh, which would rebase it onto main: gh pr merge <number> --merge --match-head-commit <head> --subject "<title> (PR #<number>)" --body "<the branch's commits' subjects, one line each, and the Co-Authored-By line>"
3  into production  the same branch, a second pull request: gh pr create --base production; on its green check the seat merges it the same way, with --subject "<title> (PR #<number>)"
4  deploy, tag  the stages on the merged commit, all on the word to deploy; the last stage tags it with the patch number raised, once the operator confirms its message; then scripts/ff-primary.sh
5  close        the pull request into main closed the issue at step 2; check it: gh issue view N
```

The fix reaches `main` before it reaches `production`, GitLab Flow's
upstream-first rule, so the next release carries it by construction and no
back-merge is owed. Skipping step 3 is caught at the next release's step 1
only when `production` gained a commit of its own; a fix merged into `main`
alone simply ships with the next release.

## When a branch conflicts with `main`

GitHub refuses the merge and says so on the pull request. The lane merges
`origin/main` into its own branch, in its own worktree, and pushes. Before
the merge is committed it is verified by content:

```sh
git merge --no-ff --no-commit origin/main
scripts/verify-merge-paths.py <theirs>
```

`verify-merge-paths.py` proves presence, not placement, and exits non-zero on
a problem: read its counts, then read the hunks. Two checks it does not make:

- **No source file may differ from the side that changed it.**
  `git diff --cached --name-only <the-side-that-changed-code> -- '<source glob>'`
  prints nothing, or the merge touched code it had no business touching.
- **Marker phrases are copied from the document, never from a commit
  message**, and each must occur exactly once (`grep -cF`).

## The primary follows `production`

```sh
scripts/ff-primary.sh          # or: scripts/ff-primary.sh <hash>
scripts/ff-primary.sh --self-test
```

It moves at a release, not at a merge. It refuses while the primary's status
shows anything at all, untracked files included: a file left there is
somebody's, so find the owner and commit it, and never fast-forward over it.

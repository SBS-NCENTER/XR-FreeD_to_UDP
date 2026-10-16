---
name: publish
description: Use when about to push a commit to the remote — a branch, a rebased branch, main, production at a release, a version tag, or the deletion of an abandoned branch. Runs scripts/publish.sh, which asks the server afterwards whether it holds what was intended.
---

# publish — one commit to one branch, confirmed by the server

Every push goes through `scripts/publish.sh`, whose header lists what it
guarantees and what it refuses.

## The forms

```sh
# a branch, from the worktree whose HEAD it is: HEAD must be clean
scripts/publish.sh HEAD <branch>

# a hash someone reported, checked against the worktree that holds it
scripts/publish.sh --worktree <that worktree> <hash> <branch>

# a branch whose pushed history was rewritten: lease pinned to the server's value
scripts/publish.sh --rebased HEAD <branch>

# the seat's own commit onto main, and at a release a merge onto production:
# must fast-forward, never forced
scripts/publish.sh HEAD main
scripts/publish.sh <hash> production

# a remote branch that was abandoned; a merged one GitHub deletes itself, and
# a squash-merged one is accepted by its tree
scripts/publish.sh --delete <branch>

# a version tag on production, which the deploy's tag stage runs itself
scripts/publish.sh --tag <vMAJOR.MINOR.PATCH>
```

**Read its last line.** `published` or `deleted` means the server was asked
afterwards and holds what was intended. Anything else starts with `REFUSED:` and
says why; exit 1 is a refusal, exit 2 means it could not run.

## What a refusal means, and what to do

| refusal says | it means | do |
| --- | --- | --- |
| HEAD moved, or the wrong hash was named | the worktree's `HEAD` is not the hash | re-read the hash from the worktree and publish that, or ask its owner |
| uncommitted changes | the tree differs from the commit | commit or restore first; never publish around it |
| not an ancestor | the server holds commits this hash does not | fetch and look; if the seat updated the branch, `git merge origin/<branch>` and publish again; if you rebased it and it is neither `main` nor `production`, `--rebased` |
| holds commits … does not apply | a rebased branch would lose server commits | stop and find where those commits went |
| cannot run: git cherry … failed | the content check itself did not run (exit 2) | report it with the printed error; do not retry blind |
| moved after it was read | someone pushed in between | read the server again and decide; do not retry blind |
| reported success but the server holds … | the push did not land | report it with the printed server value |
| does not carry by content | the branch holds commits `main` lacks by patch, and no commit of `main` holds its tree | read the named commits; if they landed rewritten, report it rather than deleting by hand |
| production / main is never force-pushed / deleted | the operation is not allowed | there is no flag for this |

## If the script itself is broken

A hook refuses a raw `git push` and points here. **If `scripts/publish.sh` is
what fails — its `--self-test` no longer passes, or it refuses a push it should
allow — report that to the seat, which puts it to the operator, and stop.**
The operator's switch is
`disableAllHooks` in `.claude/settings.local.json`. It is theirs to flip, not a
session's, and a raw push around the door is exactly the push the door exists to
stop.

```sh
scripts/publish.sh --self-test
```

Every case runs against a throwaway bare repository, each `PASS` or `FAIL`,
exit 0 only when all pass. It touches no real remote.

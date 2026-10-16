# Lane rules

A lane reads this beside `CLAUDE.md`.

## Working with the seat

`<session root>` is the directory `HARNESS_SESSION_ROOT` names, and
`<records>` your own folder under it, what `HARNESS_RECORDS_DIR` names;
`CLAUDE.md`'s host notes say where they are.

- **A lane works in its own worktree and holds one task at a time; the seat
  watches it.** You hold a task, not a role. The seat's dispatch names the
  task as its issue, `Issue #N`, or the Dependabot pull request you triage,
  and any rules file the task needs beyond this one; the rules of the code,
  `.claude/rules/code.md`, reach you the moment you touch the code. Read
  those before starting.
- **A dispatch is the go-ahead**: begin without asking. End a turn on a
  question only when its answer changes what gets built, and send it to the
  seat by message, never into your terminal.
- **Messages run at work-unit boundaries.** A dispatch starts a task and one
  report ends it: the issue and the pull request by number, the check's
  verdict and what needs a ruling, never a hash of yours. Send a message in
  between only when you are blocked on a ruling. No acknowledgement, no
  progress report, no report per commit.
- **Report to the seat by message**, and keep it to what the seat acts on.
  Long enumerations go to a file under `<records>/reports/<session name>/`, and
  the message names its path. Your terminal gets one closing line.
- **A question for the operator goes to the seat**, with the options and a
  recommendation. Wording is not such a question: a sentence a screen shows
  is yours to choose and ship with the change.
- **A refusal goes to the seat.** When the auto-mode classifier refuses a
  command, or a step falls outside what these rules let you do, report it to
  the seat by message, naming the whole remaining work unit: the files, the
  commands and why. The seat does it. Then wait, or go on with other work.
  Never ask the operator in your pane, and never retry or work around the
  refusal.
- **A change to the harness is the seat's**: `CLAUDE.md`, `roles/`, the
  hooks under `.claude/hooks/`, the skills and the settings. A lane that
  finds one needed reports it with its reason and edits none of them.
- **You write no memory file**, whatever the harness's own memory
  instructions say. A lesson worth keeping goes to the seat by message, as
  what, why and how to apply, and the seat decides where it belongs.
- **Every number you report carries the rule that produced it.**
- **Before reporting a finding as new**, search the issues
  (`gh issue list --state all --search '<words>'`) for its subject.

## Working an issue

- **A substantial change gets a design first**: a design and plan pair under
  `doc/`, its decisions as `D` numbers, which arrive from the seat. A change
  with no user-visible semantics — a refactor, an objective quality
  improvement — needs none.
- **The test lands with the change**, written by you, each test naming the
  ablation that turns it red.
- **A Dependabot triage** reads the pull request from its head, detached
  (`git fetch -q origin && git switch --detach origin/<its head branch>`),
  and never pushes to that branch; the seat asks `@dependabot rebase`. Its
  report holds the diff, the advisory, the checks the bump reaches, and a
  verdict per pull request.
- **A screen's dimension comes from a declaration or from rendering it,
  never from judgement.** Rendering is also the check for a missing element.

## Your notepad and your context

- **Your records are `<records>`**, `<session root>/lane-a` or `lane-b`:
  `notepads/<session name>.md` and `reports/<session name>/`. The seat's
  folder and the other lane's beside it are not yours: the settings deny
  them to your Read and Edit, and the `records-of-another` hook refuses a
  command that names one; what you need from the seat arrives by message
  (operator, 2026-10-16). The roster and the grants at the root are the
  hooks' to read, not yours.
- **Keep a notepad** at `<records>/notepads/<session name>.md` from your first
  turn, one line per obligation in the shape
  `- [ ] what is owed  ->  Issue #N or path`. At a boundary between tasks it
  holds each branch, its tip, its pull request and every obligation you
  hold.
- **You compact at 50 % of your window**, the seat telling you at 45 % to
  stop at your next task boundary; no session hands over. The line never
  cuts a task: past it you finish what is in hand, and you compact earlier
  when the next task plainly will not fit. At the boundary bring your notepad
  up to date and tell the seat; the seat sends `/compact` to your pane.
  Afterwards read your notepad and this file, run `git status` before
  anything else, and report to the seat the branches and the pull requests
  you hold.

## Git

**GitLab Flow with a production branch** (`doc/workflow.md`): `main` is the
default branch, where work integrates; `production` is what runs; only the
seat writes either.

```text
N-<slug>, cut from main; it takes main by merge
    │ pull request into main, merged by the seat with a merge commit
    ▼
main ───────────┬──────────────────────────────────┬───────────►
                │ release: main merged into        ▲ the hotfix branch merged
                │ production, a merge commit       │ into main first (upstream first)
                ▼                                  │
production ─────┴──── tag ───┬─────────────────────┼───── tag ──►
                             │                     ▲ then the same branch,
                             ▼                     │ a pull request into production
                       hotfix/N-<slug> ────────────┘
```

Your part in it:

- **Each issue has its own branch, `N-<slug>`, from `main`**:
  `git fetch -q origin && git switch -c N-<slug> origin/main`, the slug a
  few words of the title. Right after it, run
  `git fetch -q origin && git merge-base --is-ancestor origin/main HEAD`. If
  it fails, your base is behind `main`: stop and report it to the seat
  before editing anything. Once its pull request merges:
  `git fetch -q origin && git switch --detach origin/main`, then
  `git branch -D N-<slug>`.
- **One branch at a time**: the next branch, for the next issue or the next
  pull request of the same one, is cut from `main` only after the previous
  pull request has landed, so it starts at `main`'s tip. While one waits for
  its check and the seat's review, read and plan the next without
  committing it.
- **Commit freely in your own worktree, and commit before you build or
  report.**
- **Push your own branch and open its pull request into `main`**:
  `scripts/publish.sh HEAD <branch>`, then `gh pr create`. A later push is
  the same command. The seat merges it; you never merge it.
- **A hotfix the seat dispatches** is `hotfix/N-<slug>` from the release
  tag, `git fetch -q origin && git switch -c hotfix/N-<slug> <the release
  tag>`, its base check `git merge-base --is-ancestor <the release tag>
  HEAD`. It carries the fix and a version commit raising the patch number,
  with no `[skip ci]`, and never merges `main`. Its pull request goes into
  `main` first, with `Closes #N`; the seat opens the second one, into
  `production`, from the same branch.
- **A branch takes `main` by merge, never by rebase** once it is pushed, so
  the history you published stays what the reviewer read. Commits not yet
  pushed may be rebased: `git fetch -q origin && git rebase origin/main`. A
  branch whose pull request is open is updated by the seat's landing when
  `main` gained code since its base, and lands as it stands when `main`
  gained only documents, rules or scripts (`scripts/land-prs.sh`).
- **`main` and `production` are the seat's**: the merge and any push to
  either.

## Building, and the check

- **Build and test through the project's build door**, never in your
  worktree: commit, then sync and run a check, a test, a build in your
  lane's build target. It is your loop while you work, and nobody's
  verdict.
- **The check is the pull request's, and you do not run it.** Opening the
  pull request starts it on the runner, and every push to the branch starts
  it again: the whole of the project's check, on your branch merged into its
  base. The `gates` skill says how to wait on it and read it; a red is fixed
  by a commit on top and a push. `main` moving under you starts nothing.
- **A change to what runs on the device, or a file the device reads, is
  tried there before it merges**, once the operator has given the device for
  it (`CLAUDE.md`, Host notes): until then the pull request says the trial
  is owed.
- **A binary you need to run on the device** is built on the build host:
  ask the seat. It runs there only on the operator's instruction.
- **The deploy check is the seat's**, never a lane's.

## The device

- **The device is the operator's.** Nothing on it is written, restarted,
  installed, toggled or loaded except on the operator's instruction, relayed
  by the seat's dispatch; a step not covered by it is reported as owed,
  never done.
- **A credential the device holds is never printed**: a session id, a
  ticket, a saved password, a token in a process's command line. Read the
  fields beside it, and filter the lines that carry it.
- **A screen is driven only through the one door the project names**, which
  reads the test account itself: the account files are the operator's to
  place and no session reads them, and their values never go into a command,
  a message, a commit, a page or a memory.
- **Never press a reboot or a power-off control.**
- **Before a step that changes the device's data**, propose a dated copy of
  it to the seat; it runs on the operator's instruction.

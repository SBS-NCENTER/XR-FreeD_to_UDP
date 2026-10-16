# CLAUDE.md

The rules every session here follows, subagents included. One orchestrator
seat and two lanes work here, each in its own worktree, and no session hands
over. Beside this file a session reads:

| Session | Also reads |
| --- | --- |
| the orchestrator seat | `roles/seat.md` |
| a lane | `roles/lane.md` |

## Where things live

- **`.claude/rules/code.md`** — the rules of the code: for the reader, one
  source of truth, units of change, errors, tests, the language's own. Claude
  Code loads it when a session touches the paths it names, so it needs no
  reading step. (`templates/code.md` of chun-harness is the model; a project
  keeps its languages and adds its own section.)
- **`roles/seat.md`, `roles/lane.md`** — what only that role does, read by
  the role at its start; never under `.claude/rules/`, which loads into every
  session.
- **`ARCHITECTURE.md`** — the codemap: the firmware, the dashboard's
  backend and frontend, the tools, the hosts each runs on, and what may
  depend on what.
  A map of the present: a change rewrites the cell it touches and never
  appends to it.
- **GitHub Issues** — `gh issue list` is the open work.
- **`doc/YYYY-MM-DD-<subject>-design.md` · `-plan.md` · `-measurement.md`** —
  decisions as `D` numbers, a plan's tasks with no code, and a measurement
  dated when taken, with its conditions (chun-harness
  `doc/documents-and-citations.md`).
  The design in force is `doc/2026-06-16-xrfd-dashboard-svelte-migration-design.md`;
  the three `-plan.md` files are the records of how the dashboard, the
  multi-device view and the device identity were built.
- **What was done** is `main`'s history and the issues and pull requests;
  nothing else records it.
- **The firmware's `README.md` design notes and `MANUAL.md`** state what
  the device does on the wire and on the serial console; the dashboard's
  `dashboard/README.md` the same for the bridge. A document cites a function
  by `path::name`.

## Non-negotiables

Enforced by the check (`scripts/check.sh`, run by `.github/workflows/ci.yml`
for every pull request and every push to `main` or `production`). Do not
route around them.

1. **`shellcheck` passes with no warning and `shfmt -i 2 -ci` changes nothing on every script**, in the check every change takes before it merges. No session runs the check to verify a change, and a hand run only diagnoses one (the `gates` skill).
2. **A shellcheck directive is placed at the narrowest scope, with its reason written beside it**, never file-wide.
3. **A behaviour of a script is pinned by a test** that the check runs, written with the change that adds the behaviour.
4. **`ruff format` changes nothing and `ruff check` passes** in the check every change takes before it merges, on the environment `uv.lock` pins. No session runs the check to verify a change, and a hand run only diagnoses one (the `gates` skill).
5. **A rule is relaxed on the one line, with its reason beside it** (`# noqa: <code>`), never for the whole tree.
6. **A behaviour is pinned by a test `pytest` runs**, written with the change that adds the behaviour.
7. **`npm ci` and `npm run build` are clean** in the check every change takes before it merges.
8. **A change to the page is tried in a browser before it merges**, and the pull request says so.
9. **`package-lock.json` moves only with a dependency change** that names its reason.

## Git

- **Run `git status` before anything that writes a working tree**, and say
  what it showed. **Use the command that does one thing**: `git restore
  --staged` to unstage, `git revert` to undo a pushed commit, `git branch -f`
  to move a pointer, `git reset --hard` only when the tree should move. Never
  `git checkout`.
- **Every push goes through `scripts/publish.sh`** (the `publish` skill).
  Never force-push a branch another worktree holds.
- **A commit message ends with `Co-Authored-By`**, the model's own line,
  never a session link.

## Evidence

- **A claim is evidenced before it is made**, by a command run and its
  output read, and **checked against the tree before it is acted on**,
  whoever made it, a memory included: a file, a flag or a state a memory
  names is read in the tree first. When two trees disagree, find which one
  is behind.

## Documents and citations

- **An obligation that outlives the task at hand is a GitHub issue**, an
  unanswered `Q` and the unpaid debt of a `D` included: one unit of work
  each, a tightly-coupled follow-up its sub-issue.
- **The issue holds the state, the design holds the substance.** A decision
  is a `D` number in the design that owns its subject, filed when found as
  its own commit, and every other mention points at it. A `Q` closes with the
  pull request that closes its issue and edits the paragraph that posed it.
  **A design holds only what is in force, and nothing of the path to it**: a
  changed decision is rewritten in place to the fact as it now stands, with
  no amendment note and no earlier text kept; the earlier version, the
  question behind a change and the turns on the way are the commit's and the
  issue's. A decision that no longer holds keeps its number and one line
  naming what replaced it, so a citation still resolves.
- **A finding is written once**: what is wrong, where and what closes it in
  the issue, a few hundred characters; the evidence in the pull request and
  its commits; a decision alone in a `D` number. An issue or a pull request
  follows its template under `.github/`.
- **Name a symbol, never a line number.** A citation is
  `path/to/file::symbol`: the full path when it leaves the file, the
  basename inside it, never a bare module path.
- **A `D` number cited between documents carries its document**:
  `` `D13` of `doc/2026-09-02-<subject>-design.md` ``. Inside a plan, its own
  design's `D` numbers are bare.
- **A GitHub issue is written `Issue #N` and a pull request `PR #N`**, in a
  document, a memory, a notepad, a commit and a title; a bare `#N` is written
  only after a closing keyword; and in an issue's body a document is a link to
  it on `main`.
- **Every dated entry carries a local time**, read in the command that writes
  it: `TZ='Asia/Seoul' date '+%Y-%m-%d %H:%M KST'`.
- **Code carries no citation out**: a comment states the fact, and the
  document cites the code.

## Language

- **Never quote the operator verbatim, anywhere.** State the decision in your
  own words: a verbatim relay hands the reading to a session that heard less.
  Literal content — a config value, a log line, a file's text — is copied
  exactly. A phrase in quotes that nobody said is not a quotation.
- **A table's cells are short.** When a cell would hold more than one
  sentence, write the rows as items instead: a heading per row, then one
  labelled line per column.

## Keep the workflow light

- **Take out before adding.** A red is fixed at its cause. Before a rule, a
  test or a gate is added, find the one it replaces, and remove what no
  longer earns its place, such as a check that has caught nothing in hundreds
  of runs and guards no silent harm.
- **A change is checked by what it can affect, and by nothing else**: a
  rule, a hook, a script or a document by its own test, and a gate that runs
  for a change it cannot reach is narrowed.
- **A finding met while working an issue is fixed inside that issue**, and so
  is a new ruling on work of the same flow whose pull request has not merged:
  its issue is edited, never joined by a new one. A new issue is filed only
  for work too large to carry in the task at hand, never to defer a fix the
  change could hold. A small fix found outside it is a line in the open
  batch issue, which may land as one pull request or several.

## Subagents

- **Subagents only where the operator granted them to that session**, the
  seat's standing grant in `roles/seat.md`; a relayed grant is none. An agent
  changes nothing in the repository and returns a proposal with its
  evidence.

## Host notes

- `chunbay-x86`: the sessions' host. The worktrees are
  `~/workspaces/SBS/XR-FreeD_to_UDP` (the primary, on `production`),
  `XR-FreeD_to_UDP-seat`, `XR-FreeD_to_UDP-lane-a` and `XR-FreeD_to_UDP-lane-b` beside it; the
  session root is `~/workspaces/SBS/draft/XR-FreeD_to_UDP` and the memory store
  `~/obsidian-vault/SBS/.claude/memory/XR-FreeD_to_UDP/`, both named in each
  worktree's `.claude/settings.local.json` (chun-harness
  `templates/settings.local.example.json`).
- **Each worktree builds where its part builds**: `pio test -e native` and
  `pio run` in the worktree (`uv tool install platformio` put `pio` on this
  box), `uv run pytest -q` under `dashboard/`, `npm run build` under
  `dashboard/frontend/`; the check on the pull request is the verdict.
- **The boards** are Arduino UNO R4 WiFi units in the studio, on the
  devices' LAN, each reading a camera's FreeD stream over RS-422 and sending
  UDP. **They are on air.** A build goes to a board only by the operator's
  own hand: `pio run -t upload` is never run by a session, which builds,
  tests and, once the operator reports the upload, verifies read-only over
  the device's HTTP API and `ping`. `pio device list` and other reads are a
  session's.
- `ts5-server` runs the dashboard at `/opt/xr-freed-to-udp`, as `ncenter`,
  as the server-manager service `xr-freed-to-udp` (`server.toml`: port
  10000, `/health`); server-manager's update runs
  `dashboard/setup/install.sh` as the build step. **It is on air**: reading it over ssh is fine; nothing there is pulled,
  built, restarted, installed or toggled except on the operator's
  instruction, which is carried out as given. How it is reached is
  `~/obsidian-vault/SBS/.claude/hosts.md`.
- **The deploy of the dashboard is server-manager's update** (`u`) on ts5,
  on the operator's word; the deploy stages are not written, and the
  `deploy` skill is the shape they take when they are. The Windows scripts
  under `tools/` and `dashboard/setup/*.ps1` are the fallback and are tried
  on a Windows PC by the operator.
- The MAC address convention of the UNO R4 WiFi boards (derived from the
  RA UID, a prefix per device family) is the design's; the archived
  registry note that measured it is
  `~/obsidian-vault/SBS/_archive-2026-10-15/INDEX.md`.
- `.claude/`'s `skills/`, `hooks/`, `rules/` and `settings.json` are tracked,
  `.gitignore` the source. A hook or a rule promoted on `main` reaches a
  session when its worktree's checkout next takes `main`.
- **The memory store is outside git**, in the vault so it follows the operator
  between machines: every worktree's `autoMemoryDirectory` names it, so every
  session reads the one current store, and the seat alone writes it.
  `ARCHIVE.md` there holds memories kept but not indexed: read it before
  concluding none exists. The store's rules are `roles/seat.md`'s.
- **Every session record lives under the session root**, a report under
  `reports/<session name>/`; the root counts only while it holds
  `.harness-session-root` (chun-harness `doc/sessions.md`).

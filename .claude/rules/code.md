---
paths:
  - "src/**"
  - "include/**"
  - "test/**"
  - "dashboard/**"
  - "tools/**"
  - "**/*.sh"
  - "platformio.ini"
---
# The rules of the code

Claude Code loads this file into a session when it works with a file the
`paths:` above name, so a lane meets these rules the moment it touches code
and a seat that only merges never pays for them. The rules that bind every session are `CLAUDE.md`'s;
the role files are `roles/`. Each rule here earned its place by a failure it
prevents, and one that stops earning it is taken out.

## 1. For the reader

- **Simple to read over simple to write.** Code a reader cannot follow in
  one pass is rewritten, not commented; "clever" is an accusation.
- **A name says what the thing is or does**, fully, and no longer than that.
  The language's casing rules decide the form.
- **A comment says why**, never what: a "what" that needs a comment is code
  to simplify. A regular expression or an algorithm the reader may not know
  is the exception. A file, and a function that is not both short and
  obvious, opens with a header stating purpose, inputs, outputs and failure.
- **A unit does one thing and fits one screen**: a function past about sixty
  lines, or a condition past two levels of nesting, is split. A variable is
  declared where it is used.
- **Match the file, then the repository**: consistency outranks taste, and
  the formatter's output is the style. Where no rule decides, pick one and
  stop.

## 2. One source of truth

- **A fact lives in one place**: a path, a port, a version, a unit name is
  written once and read from there. A list that could be derived is derived,
  never kept by hand.
- **Code carries no citation out**: a comment states the fact, and the
  document cites the code.
- **A dependency is costed from the tree** before it is added, and added
  only for what is used now.

## 3. Units of change

- **One change does one thing**, with its tests in the same commit: about a
  hundred lines is usual, a thousand is refused, and a refactor never rides
  with a behaviour change. When in doubt, smaller.
- **Solve the problem that exists.** No generality, configuration or
  flexibility for a case nobody has; the next case brings its own change.
- **Take out before adding**: a fix lands at its cause, and what a change
  makes unused leaves with it. Dead code it finds is reported, not deleted.

## 4. Errors and results

- **Every result is checked**: a return value, an exit code, a `Result`. One
  that may be ignored is ignored where that is said, never silently.
- **A failure is loud and placed**: the message names what was being done
  and what was found, goes to stderr or into the error type, and never
  falls back to a weaker default on its own.
- **Input from outside the program is validated at the boundary**: a number
  is numeric, a name is on its allowlist, a path is not a root that must not
  be touched, and nothing read from a file or a person is executed.

## 5. Tests

- **A behaviour is pinned by a test that fails when the code breaks**, and
  the test names the ablation that turns it red. Its assertions are simple;
  a test is code and is held to these rules.
- **A test injects what it touches**: paths, hosts, commands and time come
  in as parameters, so the check runs on a runner with no device.
- **A test that needs a host runs on the operator's word** and is not part
  of the check.

## 6. What the check enforces

The formatter, the linter and the tests run on every pull request and every
push to `main` and `production` (`scripts/check.sh`); a session never runs
them as the verdict, and a human review spends itself on design, names and
why, not on style. A finding is relaxed on the one item it fires on, with
its reason beside it, never file-wide.

## 7. Bash

- Bash only, for a small utility or a wrapper; a script past about a hundred
  lines, or whose control flow is not straight, is a program in another
  language.
- `#!/usr/bin/env bash` and `set -euo pipefail`; a loop that must outlive a
  failing command drops `-e` on purpose and says so at the top.
- `[[ … ]]` for tests, `(( … ))` for arithmetic, `$(…)` for substitution;
  never `eval`, backticks, `let` or `expr`.
- **Quote every expansion**: `"${var}"`, `"$@"`, `"${array[@]}"`; a list is
  an array, never a space-joined string.
- `local` for every function variable, declared and assigned on separate
  lines when the value is a command substitution: `local x=$(cmd)` hides
  the exit code.
- Never pipe into `while`: a pipe is a subshell and its variables die with
  it; use process substitution or `readarray`.
- Errors go to stderr through one helper that adds what was being done; a
  script with functions ends with `main "$@"`; constants are `readonly` and
  declared at the top.
- Prefer builtins and parameter expansion to `sed`, `awk` and `echo |` for
  one value.
- `shellcheck --severity=warning` and `shfmt -i 2 -ci -d` are clean (the
  check); a directive sits on the one line it excuses, with its reason.

## 8. Python

- `uv` owns the environment: `pyproject.toml` names the dependencies, the
  `dev` group holds `pytest` and `ruff`, `uv.lock` is committed and the
  check installs from it with `--frozen`. `requires-python` is the floor.
- `ruff format` is the style and `ruff check` is clean (the check); the
  rule set is `[tool.ruff.lint] select` in `pyproject.toml`, with
  `extend-exclude = ["scripts", ".claude"]` for the harness scripts and hooks,
  which are not the project's Python; a rule is relaxed on the one line with `# noqa: <code>`
  and its reason beside it, never for the whole tree.
- A package lives under `src/<name>/` and its tests under `tests/`, run by
  `pytest` with `pythonpath = ["src"]`; a test touches no network, clock or
  host it did not inject (`conftest.py` holds the fixtures).
- A function's signature carries type hints; `except` names the exception
  it expects, never bare; a path is a `pathlib.Path`; a subprocess is run
  with an argument list, never a shell string; a secret comes from the
  environment or a file the deployment owns, never the tree.

## 9. Web (Svelte, Vite)

- `package-lock.json` is committed and the check installs with `npm ci`;
  `npm run build` is clean, and `npm run check` where the project has it.
- The page is served by the project's own server and reads nothing from
  another box in the browser: the server does, and the page shows.

## 10. This repository

- **The firmware's hot path is the FreeD frame**: nothing on the serial
  console runs in the production build (`DEBUG_SERIAL_MONITOR` is the
  debug environment's), and a change that adds work between a frame's
  arrival and its UDP send says what it costs.
- `lib_deps` pins the Ethernet library exactly (`2.0.2`): `main.cpp` reaches
  into its internals (`<utility/w5100.h>`); a bump re-verifies those paths
  before it lands.
- A board's MAC is derived from its RA UID with the device family's prefix
  (`include/mac_from_uid.h`, tested natively); a hard-coded MAC is a
  defect, not a default.
- Pure logic lives in `include/` and is tested with `pio test -e native`;
  what needs the board is tried on a board by the operator.
- The dashboard's backend reaches the device over UDP from one thread
  (`udp_bridge.py`) and serves the page with SSE; a route never touches the
  socket directly. Python: `dashboard/pyproject.toml` pins the ruff rule
  set; the harness Python is excluded there.
- `dashboard/setup/run.sh` is the one entry point for systemd, server-manager
  and a hand start; `install.sh` is `uv sync` plus the frontend build, and
  is what server-manager's update calls.

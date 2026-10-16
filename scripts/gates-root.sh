#!/usr/bin/env bash
#
# Print the root the gate records live under, `<root>/gates/<hash>/`.
#
#   scripts/gates-root.sh
#   scripts/gates-root.sh --self-test
#
# The first of these that holds:
#
#   $HARNESS_GATES_ROOT                              when it is set and not empty
#   the session root                              while it holds its marker
#   ${XDG_STATE_HOME:-$HOME/.local/state}/harness    otherwise
#
# The session root is $HARNESS_SESSION_ROOT, or ~/draft when that is unset or
# empty, the directory every session record lives under; the gate records
# sit beside them, in its gates/. It counts only while it holds the file
# .harness-session-root, so an empty directory left at its path is never taken
# for it. A host with no session root, the Pi and ts5-server's backup
# runners, keeps its records in the state directory.
#
# Nothing is created here: the writer, scripts/run-gates.sh, makes the
# directory for its first record, resolves this once and exports it as
# HARNESS_GATES_ROOT, so everything it starts names the same place. A reader
# run alone asks this itself, and one that finds no record under the answer
# says so and goes on.
#
# Exit 0 with the root on stdout; 2 on an argument it does not know.

set -euo pipefail

resolve() {
  local records=${HARNESS_SESSION_ROOT:-$HOME/draft}
  if [ -n "${HARNESS_GATES_ROOT:-}" ]; then
    printf '%s\n' "$HARNESS_GATES_ROOT"
  elif [ -f "$records/.harness-session-root" ]; then
    printf '%s\n' "$records"
  else
    printf '%s\n' "${XDG_STATE_HOME:-$HOME/.local/state}/harness"
  fi
}

self_test() {
  local tmp self failures=0
  self=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/gates-root-self-test.XXXXXX")
  # expanded now: at EXIT this function's locals are gone, and set -u would fail
  # tmp is this function's local: expand it now, since the trap fires after the
  # function has returned.
  # shellcheck disable=SC2064
  trap "rm -rf '${tmp}'" EXIT
  mkdir -p "$tmp/records" "$tmp/bare" "$tmp/draft-home/draft"
  : >"$tmp/records/.harness-session-root"
  : >"$tmp/draft-home/draft/.harness-session-root"
  check() { # check <name> <want> <env assignments...>
    local name=$1 want=$2 got rc=0
    shift 2
    got=$(env -u HARNESS_GATES_ROOT -u HARNESS_SESSION_ROOT -u XDG_STATE_HOME "$@" bash "$self" 2>&1) || rc=$?
    if [ "$rc" -eq 0 ] && [ "$got" = "$want" ]; then
      printf 'PASS  %s\n' "$name"
    else
      printf 'FAIL  %s: exit %s, printed %s, wanted %s\n' "$name" "$rc" "$got" "$want"
      failures=$((failures + 1))
    fi
  }
  check "HARNESS_GATES_ROOT wins over a session root" "$tmp/override" \
    HARNESS_GATES_ROOT="$tmp/override" HARNESS_SESSION_ROOT="$tmp/records" HOME="$tmp/home"
  check "a session root holding its marker is the root" "$tmp/records" \
    HARNESS_SESSION_ROOT="$tmp/records" HOME="$tmp/home"
  check "an empty HARNESS_GATES_ROOT is unset" "$tmp/records" \
    HARNESS_GATES_ROOT= HARNESS_SESSION_ROOT="$tmp/records" HOME="$tmp/home"
  check "with no HARNESS_SESSION_ROOT the session root is ~/draft" "$tmp/draft-home/draft" \
    HOME="$tmp/draft-home"
  check "an empty HARNESS_SESSION_ROOT is unset" "$tmp/draft-home/draft" \
    HARNESS_SESSION_ROOT= HOME="$tmp/draft-home"
  check "a directory without the marker falls back to the state directory" "$tmp/state/harness" \
    HARNESS_SESSION_ROOT="$tmp/bare" XDG_STATE_HOME="$tmp/state" HOME="$tmp/home"
  check "an absent session root falls back the same way" "$tmp/state/harness" \
    HARNESS_SESSION_ROOT="$tmp/absent" XDG_STATE_HOME="$tmp/state" HOME="$tmp/home"
  check "with no XDG_STATE_HOME the fallback is under HOME" "$tmp/home/.local/state/harness" \
    HARNESS_SESSION_ROOT="$tmp/bare" HOME="$tmp/home"
  if [ ! -e "$tmp/state" ] && [ ! -e "$tmp/home" ] && [ ! -e "$tmp/absent" ]; then
    printf 'PASS  resolving creates nothing\n'
  else
    printf 'FAIL  resolving created %s\n' "$(ls -d "$tmp/state" "$tmp/home" "$tmp/absent" 2>/dev/null | tr '\n' ' ')"
    failures=$((failures + 1))
  fi
  if [ "$failures" -eq 0 ]; then
    printf 'gates-root self-test: all pass\n'
    return 0
  fi
  printf 'gates-root self-test: %s failure(s)\n' "$failures"
  return 1
}

case "${1-}" in
  --self-test) self_test ;;
  "") resolve ;;
  *)
    printf 'gates-root: unknown argument %s\n' "$1" >&2
    exit 2
    ;;
esac

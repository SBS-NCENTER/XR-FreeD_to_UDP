#!/usr/bin/env bash
#
# Install this repository's git hooks, and say when the installed copy has
# drifted from the tracked one.
#
#   git-hooks/*        the tracked copies, which review can see
#   $(git rev-parse --git-path hooks)/   where git actually looks
#
# ONE COMMAND, AND "CHECK FOR DRIFT" IS "RUN IT AGAIN"
#
# It is idempotent and it is its own check. Each hook reports exactly one of:
#
#   INSTALLED   it was not there, and now it is
#   CURRENT     it is there and identical to the tracked copy
#   DRIFT       it is there and differs -- the diff is printed, nothing is
#               overwritten, and the exit status is 1
#
# `--force` resolves a drift by making the installed copy match the tracked
# one. It is deliberate rather than the default: the installed copy is
# sometimes the newer of the two -- that is exactly how this issue arose, the
# hook having been edited in place hours after it was installed -- and
# overwriting silently would destroy the only copy of that edit.
#
# WHY THIS IS NOT A GATE, WHICH IS A REAL COST
#
# The gate set is environment-independent by construction: every gate
# reads files in this repository and answers the same on any machine. This one
# reads `.git/`, which is **not** in the repository and differs per clone, so as
# a gate it would fail on every fresh checkout that had not run it -- correct
# behaviour for a guard, wrong behaviour for a gate, and a red that means
# "you have not set up your machine" trains people to ignore reds.
#
# **So nothing runs this automatically, and a drift can therefore stand until
# somebody runs it.** That is the cost of the choice, and it is written here
# rather than left for a reader to discover: a description that overstates its
# own coverage is worse than an honest gap.
#
# WHY NOT `core.hooksPath`
#
# It is repository-local config shared by every worktree of this checkout, so
# setting it changes hook resolution under whatever else is running in the
# other worktree, mid-work. `.git/hooks/` already resolves through the common
# git dir and already covers both worktrees -- `git rev-parse --git-path hooks`
# from either one answers the same directory -- so there is nothing to buy.

set -euo pipefail

force=0
case "${1:-}" in
  --force) force=1 ;;
  "") ;;
  *)
    printf 'usage: %s [--force]\n' "$0" >&2
    exit 2
    ;;
esac

repo_root=$(git rev-parse --show-toplevel)
tracked_dir="${repo_root}/git-hooks"

# `--git-path` rather than a literal `.git/hooks`: in a worktree `.git` is a
# FILE naming another directory, so the literal path does not exist there.
hooks_dir=$(git rev-parse --git-path hooks)

if [ ! -d "$tracked_dir" ]; then
  printf 'no tracked hooks at %s\n' "$tracked_dir" >&2
  exit 2
fi

mkdir -p "$hooks_dir"

drifted=0
installed=0
current=0

for tracked in "$tracked_dir"/*; do
  [ -f "$tracked" ] || continue
  name=$(basename "$tracked")
  target="${hooks_dir}/${name}"

  if [ ! -e "$target" ]; then
    cp -- "$tracked" "$target"
    chmod +x -- "$target"
    printf 'INSTALLED  %s\n' "$name"
    installed=$((installed + 1))
    continue
  fi

  if cmp -s -- "$tracked" "$target"; then
    printf 'CURRENT    %s\n' "$name"
    current=$((current + 1))
    continue
  fi

  if [ "$force" -eq 1 ]; then
    cp -- "$tracked" "$target"
    chmod +x -- "$target"
    printf 'INSTALLED  %s (was drifted, --force)\n' "$name"
    installed=$((installed + 1))
    continue
  fi

  printf 'DRIFT      %s\n' "$name"
  printf '  tracked:   %s\n' "$tracked"
  printf '  installed: %s\n' "$target"
  printf '  --- installed +++ tracked ---\n'
  diff -u -- "$target" "$tracked" || true
  printf '  re-run with --force to make the installed copy match the tracked one,\n'
  printf '  or copy the installed one into %s if IT is the newer.\n' "$tracked_dir"
  drifted=$((drifted + 1))
done

printf 'install-git-hooks: %d installed, %d current, %d drifted (hooks at %s)\n' \
  "$installed" "$current" "$drifted" "$hooks_dir"

[ "$drifted" -eq 0 ]

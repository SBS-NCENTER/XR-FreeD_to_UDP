#!/usr/bin/env bash
#
# Compact a lane at its task boundary and start it again on the same
# session, or only start it again. The seat runs it (the `lanes` skill).
#
#   scripts/lane-cycle.sh <session uuid>              /compact, /exit, claude --resume
#   scripts/lane-cycle.sh --restart <session uuid>    /exit, claude --resume
#
# The pane and the worktree come from the session's record under
# ~/.claude/sessions/, and the transcript from ~/.claude/projects/. The input
# line is cleared before anything is typed. An idle prompt can show a shaded
# suggestion that is not typed text, so the line counts as `/compact` only
# when it reads exactly that and is not drawn dim, as a suggestion is; the
# colour a typed command takes follows the theme, Light by day and Dark
# otherwise, and is not read. The compaction is done when the transcript gains
# a compact_boundary, and the restart when a live record names the session
# again.
#
# Exit 0 once the session runs again in its pane; 2 when the session is not
# found, is busy or is not running claude; 4 when the input line is not
# `/compact`; 5, 6 and 7 on a timeout waiting for the compaction, the exit
# and the restart.

set -u

restart_only=0
if [ "${1:-}" = --restart ]; then
  restart_only=1
  shift
fi
if [ $# -ne 1 ]; then
  sed -n '6,7p' "$0" >&2
  exit 2
fi
sid=$1

log() { printf '%s %s\n' "$(TZ='Asia/Seoul' date '+%H:%M:%S KST')" "$*"; }

# The live record of the session: its pid, pane and worktree.
record() {
  local f
  for f in "$HOME"/.claude/sessions/*.json; do
    jq -er --arg s "$sid" 'select(.sessionId == $s) | "\(.pid) \(.tmux | sub(".*[.]"; "")) \(.cwd)"' "$f" 2>/dev/null
  done | while read -r p rest; do
    if [ -d "/proc/$p" ]; then echo "$p $rest"; fi
  done | head -n 1
}

read -r pid pane wt <<<"$(record)"
if [ -z "${pid:-}" ] || [ -z "${pane:-}" ]; then
  log "NO LIVE SESSION $sid"
  exit 2
fi
cap() { tmux capture-pane -p -t "$pane"; }
cur() { tmux display-message -p -t "$pane" '#{pane_current_command}'; }
if cap | grep -q 'esc to interrupt'; then
  log "BUSY $pane"
  exit 2
fi
if [ "$(cur)" != claude ]; then
  log "NOT CLAUDE in $pane: $(cur)"
  exit 2
fi
log "session $sid: pid $pid, pane $pane, worktree $wt"

if [ "$restart_only" = 0 ]; then
  transcript=$(ls "$HOME"/.claude/projects/*/"$sid".jsonl 2>/dev/null | head -n 1)
  if [ -z "$transcript" ]; then
    log "NO TRANSCRIPT for $sid"
    exit 2
  fi
  tmux send-keys -t "$pane" Escape
  sleep 0.5
  tmux send-keys -t "$pane" C-u
  sleep 0.3
  before=$(grep -c compact_boundary "$transcript")
  tmux send-keys -t "$pane" -l '/compact'
  sleep 1.5
  typed=$(cap | grep -m1 '^❯' | sed 's/^❯//; s/\xc2\xa0/ /g; s/^ *//; s/ *$//')
  if [ "$typed" != '/compact' ] || tmux capture-pane -p -e -t "$pane" | grep -a -m1 '❯' | grep -q $'\e\[2m'; then
    log "INPUT LINE IS NOT /compact: [$typed]"
    cap | grep -v '^$' | tail -n 6
    exit 4
  fi
  tmux send-keys -t "$pane" Enter
  log "compact sent (boundaries before: $before)"
  n=0
  while [ "$(grep -c compact_boundary "$transcript")" -le "$before" ]; do
    sleep 5
    n=$((n + 5))
    if [ "$n" -ge 900 ]; then
      log 'TIMEOUT waiting for the compact boundary'
      cap | grep -v '^$' | tail -n 8
      exit 5
    fi
  done
  log "compacted after ${n}s"
  n=0
  while cap | grep -q 'esc to interrupt'; do
    sleep 2
    n=$((n + 2))
    [ "$n" -ge 120 ] && break
  done
  sleep 3
fi

tmux send-keys -t "$pane" Escape
sleep 0.5
tmux send-keys -t "$pane" C-u
sleep 0.3
tmux send-keys -t "$pane" -l '/exit'
sleep 1.5
tmux send-keys -t "$pane" Enter
n=0
while [ "$(cur)" = claude ]; do
  sleep 2
  n=$((n + 2))
  if [ "$n" -ge 120 ]; then
    log 'TIMEOUT waiting for /exit'
    exit 6
  fi
done
log "exited (pane runs: $(cur))"
sleep 1

tmux send-keys -t "$pane" -l "cd $wt && claude --resume $sid"
sleep 0.5
tmux send-keys -t "$pane" Enter
n=0
while :; do
  read -r new _ <<<"$(record)"
  if [ -n "${new:-}" ] && [ "$new" != "$pid" ]; then break; fi
  sleep 3
  n=$((n + 3))
  if [ "$n" -ge 240 ]; then
    log 'TIMEOUT waiting for the restart'
    cap | grep -v '^$' | tail -n 8
    exit 7
  fi
done
sleep 5
log "running again: pid $new"
cap | grep -v '^$' | tail -n 5

---
name: lanes
description: The seat's. Use when compacting a lane, restarting one on new hooks, launching a lane in an empty worktree, starting or reading the lane watch, or editing the roster. Gives the commands and the order.
---

# lanes — the seat's commands for the lanes' sessions

The rules are `roles/seat.md`, under Sessions: the compaction lines, when a
lane is compacted, and who may launch one. The layout of the sessions is
`doc/sessions.md`.

## Compact a lane, or restart it

At the lane's task boundary, once its notepad is up to date:

```sh
scripts/lane-cycle.sh <session uuid>              # /compact, /exit, claude --resume
scripts/lane-cycle.sh --restart <session uuid>    # /exit, claude --resume: new hooks, no compaction
```

The uuid is the lane's roster row. The script finds the pane and the
worktree from the session's record, refuses a busy pane, and types
`/compact` only when the input line reads exactly that; an idle prompt can
show a shaded suggestion that is not typed text. Its header lists its exit
codes. Afterwards the lane reports the branches and the pull requests it
holds, and the seat checks that against the tree.

## The watch

```sh
pgrep -a -f '^python3 -u .*[w]atch-lanes'     # none may run already
LANE_STALE=900 python3 -u scripts/watch-lanes.py >> ~/draft/watch/lane-watch.log
```

Run detached, in its own tmux window, from the seat's worktree, which tracks
`main`; never under a `Monitor`. The seat reads the log at the top of turns
it takes anyway. The states are `COMPACT`, `WARN`, `QUIET`, `BLOCKED` and
`GONE`; `QUIET` is a lane silent for `LANE_STALE` seconds. `LANE_PROJECT`
names the primary checkout's project directory under `~/.claude/projects/`
when the watch is not started from a checkout of the repository.

## The roster

`~/draft/lane-watch.roster`, one row per live session:

```
<uuid>  <session name>  [seat]
```

The uuid is the `sessionId` in `~/.claude/sessions/<pid>.json`, never read
from the name; the seat's own row carries `seat`, which the `agent-grant`
hook reads. A row goes when its session ends.

## Launch a lane

When no session is live in a lane's worktree, and the operator granted it in
their own prompt:

1. The tmux session is the operator's, its lanes' window `lanes`, a lane's
   worktree the one `CLAUDE.md`'s host notes name for it. With no window:
   `tmux new-window -d -P -F '#{pane_id}' -t <session>: -n lanes -c <worktree>`;
   with one, the new pane beside the last:
   `tmux split-window -h -d -P -F '#{pane_id}' -t <session>:lanes -c <worktree>`.
2. `tmux send-keys -t <pane> 'claude' Enter`, with no flag: the settings
   give the mode and the model.
3. Once `tmux capture-pane -p -t <pane>` shows the prompt and its status
   line, type `/rename <MM.DD>-lane-a<n>` (or `-lane-b<n>`), read the input
   line, then `Enter`. `<n>` restarts at 1 each date: count that day's names
   under `~/.claude/sessions/` and, for sessions that have ended, the
   `"customTitle":"<MM.DD>-lane-` lines in the worktree's transcripts under
   `~/.claude/projects/`.
4. Confirm the name in the session's record under `~/.claude/sessions/`,
   sending the rename again while it has none.
5. Read the uuid from the record and add its row to the roster.

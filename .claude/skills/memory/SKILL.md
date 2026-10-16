---
name: memory
description: The seat's. Use when writing, folding, archiving or removing a memory in the store under the session root, or when a lane sends a lesson. Gives the shape of a memory and its index row, and the checks and the backup every edit takes.
---

# memory — editing the store

The rules are `roles/seat.md`, under The memory store: the seat is the one
writer, a memory holds no work in progress, and it holds only what the
repository cannot give. What a memory is for is `doc/memory-store.md`.

## Writing one

- **A lesson amends the memory that already fires where it fires** before
  it earns a file of its own.
- **It is cut to its lesson**, and names what would falsify it, as
  `**Falsified by** <artefact or command>`, or says `**Held by attention.**`
  when nothing could contradict it.
- **One index row per memory** in `MEMORY.md`: a title, the link, and a hook
  that says enough to decide whether to open the file.
- **An archived memory's row moves to `ARCHIVE.md`** and its file stays. A
  removed memory leaves its name on `RETIRED.txt` when its file goes, and no
  forwarding stub.
- **The index is cut at session start** past the harness's limit, the first
  200 lines or 25 000 UTF-16 code units with the leading comment removed, and
  the rows a cut drops are the newest; `scripts/memory-index-load.py` prints
  the margin.

## After every edit

From the seat's worktree's root, since the check's checkout holds no store:

```sh
scripts/check-memory-links.sh
python3 scripts/check-memory-citations.py --check
python3 scripts/memory-index-load.py
```

A floor that moved is re-derived in a commit of
`scripts/check-memory-citations.py`. The same day, the store goes to its
backup, the one copy outside this host:

```sh
rsync -a --delete ~/draft/memory/ <backup host>:<archive path>/memory/
```

The count is derived, never written down:

```sh
find ~/draft/memory -maxdepth 1 -name '*.md' -not -name 'MEMORY.md' -not -name 'ARCHIVE.md' | wc -l
```

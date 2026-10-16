#!/usr/bin/env bash
#
# A citation of a memory has to resolve to a memory.
#
# The store is $HARNESS_MEMORY_DIR, else memory/ under the session root
# ($HARNESS_SESSION_ROOT, else ~/draft), outside git. Three passes, each with its
# own pattern and its own counter:
#
#   inside the store             [[name]]                  wiki-link
#   every tracked file           .claude/memory/<name>.md  file path
#   the store's MEMORY.md        (<name>.md)               index link target
#
# The file-path form is where the store was tracked, and the dated records
# that cite a memory still spell it so; it resolves against the store as it
# is now, or against RETIRED.txt there. **An index target is not a row**: one
# row may link several memories, so the pass counts distinct targets.
#
# WHAT IT DOES NOT CHECK
#
#   - **Whether the link is apt.** It resolves or it does not; whether the
#     memory it names is the one the sentence needed is a judgement no script
#     makes.
#   - **Whether a memory that should be cited is cited.** Absence of a link is
#     invisible here.
#   - **An untracked file in the worktree.** The file-path pass reads
#     `git ls-files`.
#
# **A placeholder in this header is written `<name>`, not a real-looking
# path**: this file is tracked, so the file-path pass reads this header too,
# and a real-looking path here would be checked as a citation.
#
# WHY A LINK BREAKS
#
# A link resolves by **filename**, and a memory's `name:` may differ from its
# filename stem. The commonest broken link is the target's own declared
# name, so a refusal says which file declares the name and what to write
# instead: told only "no", a writer goes looking for a mistake they did not
# make.
#
# NAMED EXCEPTIONS: PROSE ABOUT THE SYNTAX, NOT A CITATION
#
# A sentence about the link syntax cannot be written without the pattern, so
# such a sentence's name goes in EXCEPTIONS with its reason rather than being
# matched away: a rule such as "only a name with an underscore is a citation"
# would stop checking exactly the misspellings this exists to catch. None is
# needed today.

set -u

root=$(git rev-parse --show-toplevel) || exit 2
cd "$root" || exit 2

records=${HARNESS_SESSION_ROOT:-$HOME/draft}
readonly MEM=${HARNESS_MEMORY_DIR:-$records/memory}
readonly INDEX_FILE="$MEM/MEMORY.md"

if [ ! -d "$MEM" ]; then
  echo "check-memory-links: no store at $MEM. It is \$HARNESS_MEMORY_DIR, else" >&2
  echo "  memory/ under \$HARNESS_SESSION_ROOT or ~/draft." >&2
  exit 2
fi

# A store holding no memory yet (its index and archive only) has nothing to
# resolve, and the zero guard below would read that as a broken pattern.
if ! find "$MEM" -maxdepth 1 -name '*.md' ! -name MEMORY.md ! -name ARCHIVE.md | grep -q .; then
  echo "check-memory-links: the store at $MEM holds no memory yet; nothing to resolve"
  exit 0
fi

# Prose about the syntax, not citations.
readonly -A EXCEPTIONS=()

# RETIRED NAMES, AND WHY THE FILE-PATH PASS READS THEM AND THE OTHER TWO DO NOT
#
# A memory that is removed leaves its name behind in dated records: an issue
# file, a design, a plan, a progress page. Those are history, and a record of
# what a session read on the day it wrote is not corrected because the store
# was cleaned since. So a removed memory's name goes on `$RETIRED_FILE`, one per
# line, and a file-path citation of a name on it resolves as history rather
# than as a broken pointer.
#
# **The list cannot hide a live file**: a name on it whose file exists is a red,
# because that name is both retired and present and one of the two is wrong.
# **The store's own links never consult it**: a `[[name]]` inside a memory and a
# row of the index are read by every session today, so each must name a file
# that is there.
readonly RETIRED_FILE="$MEM/RETIRED.txt"
declare -A RETIRED=()
if [ -f "$RETIRED_FILE" ]; then
  while IFS= read -r line; do
    case "$line" in '' | '#'*) continue ;; esac
    if ! printf '%s\n' "$line" | grep -qx '[A-Za-z0-9_.-]\+'; then
      echo "check-memory-links: $RETIRED_FILE holds a line that is not a name: $line" >&2
      exit 1
    fi
    RETIRED[$line]=1
  done <"$RETIRED_FILE"
fi

failed=0
retired_cited=0
for name in "${!RETIRED[@]}"; do
  if [ -f "$MEM/$name.md" ]; then
    failed=1
    echo "  RETIRED AND PRESENT $name -- $RETIRED_FILE names it and $MEM/$name.md exists."
    echo "         Take the name off the list, or remove the file."
  fi
done
# **Three counters, because the three passes count three different things and
# one summed number would misdescribe all of them.** The wiki pass sees one
# entry per (file, link) pair -- a name cited from four memories is four -- while
# the other two see distinct names.
wiki_pairs=0
path_names=0
index_links=0

# ---- inside the store: [[name]] ------------------------------------------
while IFS=: read -r file name; do
  [ -n "$name" ] || continue
  if [ -n "${EXCEPTIONS[$name]-}" ]; then
    continue
  fi
  wiki_pairs=$((wiki_pairs + 1))
  if [ ! -f "$MEM/$name.md" ]; then
    failed=1
    echo "  BROKEN [[$name]] in $file -- no $MEM/$name.md"
    # The commonest broken link is the target's own declared `name:`, so
    # the refusal names the file that declares it.
    declarer=$(grep -l "^name: *${name} *\$" "$MEM"/*.md 2>/dev/null | head -1)
    if [ -n "$declarer" ]; then
      echo "         NOTE: $(basename -- "$declarer") declares \`name: $name\`."
      echo "         Links resolve by FILENAME here, so write" \
        "[[$(basename -- "$declarer" .md)]]."
    fi
  fi
done < <(grep -ho '\[\[[^]]*\]\]' "$MEM"/*.md --with-filename 2>/dev/null |
  sed 's/\[\[//; s/\]\]//' | sort -u)

# ---- every tracked file: .claude/memory/<name>.md ------------------------
while IFS= read -r name; do
  [ -n "$name" ] || continue
  path_names=$((path_names + 1))
  if [ ! -f "$MEM/$name.md" ]; then
    if [ -n "${RETIRED[$name]-}" ]; then
      retired_cited=$((retired_cited + 1))
      continue
    fi
    failed=1
    echo "  BROKEN path citation $MEM/$name.md"
  fi
done < <(git ls-files |
  xargs -r grep -ho '\.claude/memory/[A-Za-z0-9_.-]\+\.md' 2>/dev/null |
  sed 's|\.claude/memory/||; s|\.md$||' | sort -u)

# `MEMORY.md`'s own index links carry a bare filename rather than a path.
while IFS= read -r name; do
  [ -n "$name" ] || continue
  index_links=$((index_links + 1))
  if [ ! -f "$MEM/$name.md" ]; then
    failed=1
    echo "  BROKEN index link target ($name.md) in $INDEX_FILE"
  fi
done < <(grep -ho '](\([A-Za-z0-9_.-]\+\)\.md)' "$INDEX_FILE" 2>/dev/null |
  sed 's|](||; s|\.md)$||' | sort -u)

# **A degenerate pass is the likeliest way this check goes wrong.** An extraction
# that stopped matching would compare nothing against anything and report
# success forever, so all three counts are asserted non-empty and all three are
# printed -- each pass has its own pattern and one can go blind alone.
if [ "$wiki_pairs" -eq 0 ] || [ "$path_names" -eq 0 ] || [ "$index_links" -eq 0 ]; then
  echo "check-memory-links: extracted $wiki_pairs wiki pair(s), $path_names file-path" >&2
  echo "  name(s) and $index_links index link target(s). A zero in any of the three" >&2
  echo "  means the script's patterns have stopped matching the files' shape, not that" >&2
  echo "  they are clean -- and each pass has its own pattern, so one can fail alone." >&2
  exit 1
fi

if [ "$failed" -ne 0 ]; then
  exit 1
fi

echo "check-memory-links: $wiki_pairs (file, link) pair(s) inside the store," \
  "$path_names name(s) cited by path from tracked files, and $index_links index link" \
  "target(s) all resolve, $retired_cited of the path names as retired (${#RETIRED[@]} on" \
  "$RETIRED_FILE); ${#EXCEPTIONS[@]} named exception(s)."

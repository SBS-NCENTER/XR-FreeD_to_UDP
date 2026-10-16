#!/usr/bin/env python3
"""Predict how much of the memory store's MEMORY.md reaches a session, and why.

Prints the index's size in the unit the harness counts, the margin to its cap,
and -- when the index is over -- which row is the last one a session receives
and the warning the harness should print, so the prediction can be checked
against a real session's context.

    scripts/memory-index-load.py              the store's index
    scripts/memory-index-load.py --file F     any file, read as if it were the index

The store is `$HARNESS_MEMORY_DIR`, else `memory/` under the session root,
`$HARNESS_SESSION_ROOT` or `~/draft`.

**The limits are the harness's, not this repository's.** Claude Code 2.1.268
loads the index through a function that trims the text, counts newlines + 1 as
lines and the JavaScript string length as its "byteCount", and truncates when
either exceeds its constant: 200 lines, 25 000. A JavaScript string length is a
count of UTF-16 code units, so **the name says bytes and the value is not
bytes**. The cut keeps the first 200 lines if over, then, if still over 25 000,
cuts at the last newline at or before index 25 000.

**The leading `<!-- ... -->` comment is not counted, because it never arrives.**
That is observed rather than read: a session's context carries the index from
its first row, and the harness's warning counts rows, not the file's lines.
Where the stripping happens was not traced, so only a LEADING comment is
removed here, and any other comment is reported rather than assumed away.

**A harness update can change any of this without notice.** The warning the
harness prints names its own figures, so a session that sees one can compare it
with this script's prediction; a mismatch means the constants moved.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

STORE = pathlib.Path(os.environ.get("HARNESS_MEMORY_DIR") or os.path.join(
    os.environ.get("HARNESS_SESSION_ROOT") or os.path.expanduser("~/draft"), "memory"))
INDEX = STORE / "MEMORY.md"

LINE_CAP = 200
UNIT_CAP = 25_000

LEADING_COMMENT = re.compile(r"\A\s*<!--.*?-->", re.S)
ANY_COMMENT = re.compile(r"<!--.*?-->", re.S)
INDEX_ROW = re.compile(r"^- \[")


def units(text: str) -> int:
    """UTF-16 code units: what a JavaScript string's `length` counts."""
    return len(text.encode("utf-16-le")) // 2


def kib(n: int) -> str:
    """The harness's own size formatting: one decimal place, a trailing `.0`
    dropped. Printed so the prediction can be matched against its warning
    character for character."""
    return f"{n / 1024:.1f}".removesuffix(".0") + "KB"


def title(row: str) -> str:
    match = re.match(r"- \[(.*?)\]\(", row)
    return match.group(1) if match else row[:60]


def read_index(file: pathlib.Path | None) -> str:
    if file is not None:
        return file.read_text()
    if not INDEX.is_file():
        sys.exit(f"no index at {INDEX}: the store is $HARNESS_MEMORY_DIR, else "
                 "memory/ under $HARNESS_SESSION_ROOT or ~/draft")
    return INDEX.read_text()


def cut(lines: list[str]) -> int | None:
    """The harness's truncation, step for step, in UTF-16 code units.

    Returns how many whole lines arrive, or None when nothing is cut. Zero
    means the harness found no newline inside the cap and cut mid-line.
    """
    total = sum(units(line) for line in lines) + len(lines) - 1
    if len(lines) <= LINE_CAP and total <= UNIT_CAP:
        return None
    kept = lines[:LINE_CAP]
    if sum(units(line) for line in kept) + len(kept) - 1 <= UNIT_CAP:
        return len(kept)
    # The last newline at or before code-unit index UNIT_CAP. Walked per line
    # rather than found by indexing the string, because a Python index counts
    # code points and an astral character is one of those and two code units.
    arriving = 0
    offset = 0
    for count, line in enumerate(kept[:-1], start=1):
        offset += units(line)
        if offset > UNIT_CAP:
            break
        if offset > 0:
            arriving = count
        offset += 1
    return arriving


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=pathlib.Path,
                        help="read any file as if it were the index")
    args = parser.parse_args()

    raw = read_index(args.file)
    comment = LEADING_COMMENT.match(raw)
    body = (raw[comment.end():] if comment else raw).strip()
    stray = len(ANY_COMMENT.findall(body))
    lines = body.split("\n")
    rows = [line for line in lines if INDEX_ROW.match(line)]

    where = f"read from {args.file}" if args.file else f"in {STORE}"
    print(f"MEMORY.md {where}")
    print(f"  file                    {len(raw.encode()):>7,} bytes")
    print(f"  leading comment         {len(comment.group(0).encode()) if comment else 0:>7,} bytes"
          f"   never arrives, so never counted")
    if stray:
        print(f"  OTHER comments in body  {stray:>7}"
              f"   counted here; whether the harness strips them is untested")
    print(f"  body                    {units(body):>7,} UTF-16 units   "
          f"{len(body.encode()):,} bytes   {len(lines)} lines   {len(rows)} rows")
    print("    rule: the file less its leading <!-- --> comment, trimmed; "
          "a row is a line opening '- ['")

    print(f"\n  cap                     {UNIT_CAP:>7,} UTF-16 units   {LINE_CAP} lines")
    print(f"  margin                  {UNIT_CAP - units(body):>+7,} units"
          f"   {LINE_CAP - len(lines):+} lines")
    print(f"  byte figure beside it   {UNIT_CAP - len(body.encode()):>+7,}"
          f"   informative only: the harness does not count bytes today")

    kept = cut(lines)
    if kept is None:
        print("\n  loads WHOLE; the harness prints no warning")
        print(f"  last row                {title(rows[-1]) if rows else '(none)'}")
        return 0

    print(f"\n  TRUNCATED: {len(lines) - kept} of {len(lines)} lines lost")
    print(f"  last row that arrives   {title(lines[kept - 1]) if kept else '(mid-line cut)'}")
    if kept < len(lines):
        print(f"  first row lost          {title(lines[kept])}")
    over_lines = len(lines) > LINE_CAP
    over_units = units(body) > UNIT_CAP
    if over_units and not over_lines:
        size = f"{kib(units(body))} (limit: {kib(UNIT_CAP)})"
    elif over_lines and not over_units:
        size = f"{len(lines)} lines (limit: {LINE_CAP})"
    else:
        size = f"{len(lines)} lines and {kib(units(body))}"
    detail = (f"{len(lines) - kept} of {len(lines)} lines were cut off, "
              f"starting at line {kept + 1}") if kept else "a mid-line cut"
    print(f"  the warning should read MEMORY.md is {size} ... {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

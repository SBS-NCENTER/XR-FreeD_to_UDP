#!/usr/bin/env python3
"""Make scripts/memory-index-load.py fire and fall quiet on indexes built to do each.

Run from the repository root. Exit status 1 when any case disagrees.

**The two cases that carry the weight are the unit ones.** A predictor counting
Unicode code points agrees with one counting UTF-16 code units on every index
this repository has ever committed, because none of them holds a character above
U+FFFF; and one counting UTF-8 bytes agrees with both on an ASCII index. So each
wrong unit gets an index on which its answer differs from the harness's, and a
predictor that has drifted to either fails here rather than in a session.

Every other case runs a probe that must fire beside a control that must not, in
that order, so a predictor that reports everything as cut is caught before it is
trusted to report anything as whole.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

PREDICTOR = pathlib.Path("scripts/memory-index-load.py")
EMOJI = "\U0001F4DD"


def row(n: int, width: int, filler: str = "x") -> str:
    """One index row of exactly `width` characters, built from `filler`."""
    head = f"- [Row {n}](feedback_row_{n}.md) — "
    return head + filler * (width - len(head))


def body(rows: list[str]) -> str:
    return "\n".join(rows) + "\n"


def run(text: str, scratch: pathlib.Path, name: str) -> str:
    path = scratch / f"{name}.md"
    path.write_text(text)
    # The store is the scratch directory, so no case can read the real one.
    out = subprocess.run(
        [sys.executable, str(PREDICTOR), "--file", str(path)],
        capture_output=True, text=True,
        env=dict(os.environ, HARNESS_MEMORY_DIR=str(scratch)),
    )
    if out.returncode != 0:
        return f"EXIT {out.returncode}: {out.stderr.strip()}"
    return out.stdout


def main() -> int:
    if not PREDICTOR.is_file():
        print(f"{PREDICTOR} not found -- run from the repository root", file=sys.stderr)
        return 2

    # 100 rows of 240 characters: 24 099 units, comfortably whole.
    under = [row(n, 240) for n in range(1, 101)]
    cases = [
        ("under both caps", body(under),
         ["loads WHOLE"]),
        ("four more rows cross the unit cap", body(under + [row(n, 240) for n in range(101, 105)]),
         ["TRUNCATED: 1 of 104 lines lost",
          "1 of 104 lines were cut off, starting at line 104"]),
        ("a 5 000-character leading comment is not counted",
         "<!-- " + "c" * 5000 + " -->\n" + body(under),
         ["loads WHOLE"]),
        ("the same 5 000 characters as a row are",
         body(under + [row(101, 5000, "c")]),
         ["TRUNCATED: 1 of 101 lines lost"]),
        ("201 short rows hit the line cap and not the unit cap",
         body([row(n, 40) for n in range(1, 202)]),
         ["201 lines (limit: 200)", "1 of 201 lines were cut off, starting at line 201"]),
        # 103 rows of 240 code points, two of them astral in each: 24 822 code
        # points, which would load whole, and 25 028 units, which does not.
        ("astral characters: units cut where code points would not",
         body([row(n, 238) + EMOJI * 2 for n in range(1, 104)]),
         ["TRUNCATED", "25,028 UTF-16 units"]),
        # 100 rows of 240 units, 21 em dashes in each counting the row's own:
        # 24 099 units and 28 299 bytes, which a byte count would cut.
        ("multibyte characters: bytes over, units under, loads whole",
         body([row(n, 220) + "—" * 20 for n in range(1, 101)]),
         ["loads WHOLE", "24,099 UTF-16 units", "28,299 bytes"]),
    ]

    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        scratch = pathlib.Path(tmp)
        for number, (label, text, expected) in enumerate(cases, start=1):
            output = run(text, scratch, f"case-{number}")
            missing = [needle for needle in expected if needle not in output]
            print(f"  {'pass' if not missing else 'FAIL'}  {label}")
            for needle in missing:
                print(f"          expected: {needle}")
            if missing:
                failed += 1
                print("          got:")
                for line in output.splitlines():
                    print(f"            {line}")
    print(f"\n{len(cases) - failed} of {len(cases)} pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

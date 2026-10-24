"""Edit release notes in place, keeping everything else (stdlib only: no uv needed).

    python3 -m tools.release.notes NOTES LINES > BODY              (release.yml, #38)
    python3 -m tools.release.notes --rollback-note NOTE NOTES > BODY   (rollback.yml, #39)

LINES holds `Image: …` and `Tested with Engine …`. A line in NOTES starting the same way is
replaced in place; one that is missing is appended after a blank line, so a re-run rewrites only
these two lines. A rollback note replaces every earlier `Rolled back on …` line and goes last.
"""

import sys
from pathlib import Path

PREFIXES = ("Image: ", "Tested with Engine ")
ROLLBACK_PREFIX = "Rolled back on "


def merge(notes: str, lines: list[str]) -> str:
    out = notes.rstrip("\n").split("\n") if notes.strip() else []
    missing: list[str] = []
    for line in lines:
        prefix = next(p for p in PREFIXES if line.startswith(p))
        found = [i for i, existing in enumerate(out) if existing.startswith(prefix)]
        if found:
            out[found[0]] = line
            out = [x for i, x in enumerate(out) if i not in found[1:]]
        else:
            missing.append(line)
    if missing:
        while out and not out[-1].strip():
            out.pop()
        out = [*out, "", *missing] if out else missing
    return "\n".join(out) + "\n"


def with_rollback_note(notes: str, note: str) -> str:
    """``notes`` without any earlier rollback line, then a blank line and ``note``."""
    out = [line for line in notes.split("\n") if not line.startswith(ROLLBACK_PREFIX)]
    while out and not out[-1].strip():
        out.pop()
    return "\n".join([*out, "", note] if out else [note]) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--rollback-note":
        if not argv[2].startswith(ROLLBACK_PREFIX):
            print(f"error: the note must start with {ROLLBACK_PREFIX!r}", file=sys.stderr)
            return 2
        sys.stdout.write(with_rollback_note(Path(argv[3]).read_text(encoding="utf-8"), argv[2]))
        return 0
    notes = Path(argv[1]).read_text(encoding="utf-8")
    lines = [ln for ln in Path(argv[2]).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) != 2 or not all(ln.startswith(p) for ln, p in zip(lines, PREFIXES, strict=True)):
        print("error: LINES must be the Image: and Tested with Engine lines", file=sys.stderr)
        return 2
    sys.stdout.write(merge(notes, lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

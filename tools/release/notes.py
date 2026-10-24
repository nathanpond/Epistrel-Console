"""Put the release's two identity lines into its notes (release.yml, #38).

    python3 -m tools.release.notes NOTES LINES > BODY      (stdlib only: no uv needed)

LINES holds `Image: …` and `Tested with Engine …`. A line in NOTES starting the same way is
replaced in place; one that is missing is appended after a blank line. Everything else in NOTES is
kept, so a re-run rewrites only these two lines.
"""

import sys
from pathlib import Path

PREFIXES = ("Image: ", "Tested with Engine ")


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


def main(argv: list[str]) -> int:
    notes = Path(argv[1]).read_text(encoding="utf-8")
    lines = [ln for ln in Path(argv[2]).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) != 2 or not all(ln.startswith(p) for ln, p in zip(lines, PREFIXES, strict=True)):
        print("error: LINES must be the Image: and Tested with Engine lines", file=sys.stderr)
        return 2
    sys.stdout.write(merge(notes, lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

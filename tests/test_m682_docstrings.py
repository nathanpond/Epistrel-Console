"""#293 (#682's Console half): Engine M7 declares ``?viewpoint=`` on the States and
scheduled-event reads and the 404 on ``POST /stories``, so no source still calls them
undeclared. The guard that the recorded schema declares them is #291's
``tests/engine/test_contract.py::test_the_three_viewpoint_reads_are_declared``."""

import re
from pathlib import Path

from tests.helpers import REPO

WINDOW = 2  # lines either side of a "#682"


def stale_lines(root: Path) -> list[str]:
    """Each ``root/**/*.py`` line saying "undeclared" (any case) on, or within
    :data:`WINDOW` lines of, a line naming "#682", as ``<path under root>:<line>``."""
    found: set[str] = set()
    for source in sorted(root.rglob("*.py")):
        lines = source.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "#682" not in line:
                continue
            for near in range(max(index - WINDOW, 0), min(index + WINDOW + 1, len(lines))):
                if re.search("undeclared", lines[near], re.IGNORECASE):
                    found.add(f"{source.relative_to(root)}:{near + 1}")
    return sorted(found)


def test_no_source_calls_the_viewpoint_reads_undeclared() -> None:
    assert stale_lines(REPO / "console") == []


def test_the_scan_finds_a_stale_line(tmp_path: Path) -> None:
    """Complement: the scan isn't vacuous (a line two away from "#682" counts, three doesn't)."""
    (tmp_path / "stale.py").write_text(
        '"""``?viewpoint=`` is\nUndeclared here\n(Engine bug #682)."""\n', encoding="utf-8"
    )
    (tmp_path / "far.py").write_text('"""undeclared\n\n\n\nsee #682."""\n', encoding="utf-8")
    (tmp_path / "fine.py").write_text('"""Declared since Engine M7 (#682)."""\n', encoding="utf-8")
    assert stale_lines(tmp_path) == ["stale.py:2"]

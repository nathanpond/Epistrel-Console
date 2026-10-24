"""tools.release.notes: the release's Image/Tested-with lines are appended once, then replaced."""

from tools.release.notes import merge, with_rollback_note

LINES = ["Image: `img:0.1.1@sha256:aa`", "Tested with Engine 0.1.1 (`eng@sha256:bb`)"]
NEW = ["Image: `img:0.1.1@sha256:cc`", "Tested with Engine 0.1.1 (`eng@sha256:dd`)"]
NOTES = "## What's Changed\n* feat: thing by @x in #1\n\n**Full Changelog**: v0.1.0...v0.1.1\n"


def test_appended_after_a_blank_line() -> None:
    assert merge(NOTES, LINES) == NOTES + "\n" + "\n".join(LINES) + "\n"


def test_rerun_replaces_only_the_two_lines() -> None:
    first = merge(NOTES, LINES)
    second = merge(first, NEW)
    assert second == NOTES + "\n" + "\n".join(NEW) + "\n"
    assert "sha256:aa" not in second
    assert second.count("Image: ") == 1


def test_trailing_blank_lines_do_not_pile_up() -> None:
    assert merge(NOTES + "\n\n\n", LINES) == merge(NOTES, LINES)


def test_empty_notes() -> None:
    assert merge("", LINES) == "\n".join(LINES) + "\n"


def test_one_missing_line_is_appended() -> None:
    body = NOTES + "\nImage: `old`\n"
    out = merge(body, LINES)
    assert out.count("Image: ") == 1
    assert out.endswith(LINES[1] + "\n")
    assert "Image: `img:0.1.1@sha256:aa`" in out


ROLLED = "Rolled back on 2026-10-24; use v0.1.0."


def test_rollback_note_is_appended() -> None:
    assert with_rollback_note(NOTES, ROLLED) == NOTES + "\n" + ROLLED + "\n"


def test_rollback_note_is_replaced_not_duplicated() -> None:
    once = with_rollback_note(NOTES, "Rolled back on 2026-10-01; use v0.0.9.")
    twice = with_rollback_note(once + "\n\n", ROLLED)
    assert twice.count("Rolled back on ") == 1
    assert twice.endswith("\n" + ROLLED + "\n")
    assert twice.startswith(NOTES.rstrip("\n"))


def test_rollback_note_on_empty_notes() -> None:
    assert with_rollback_note("", ROLLED) == ROLLED + "\n"

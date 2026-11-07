"""E23's shared renderers (#294): ``console/raw.py``."""

import pytest

from console.raw import compact_json, more_json, number_text, plural


def test_plural() -> None:
    assert plural(1, "conflict") == "1 conflict"
    assert plural(2, "conflict") == "2 conflicts"
    assert plural(0, "conflict") == "0 conflicts"
    assert plural(3650, "day") == "3,650 days"
    assert plural(2, "story", "stories") == "2 stories"
    assert plural(1, "story", "stories") == "1 story"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.910, "0.91"),
        (0.025, "0.025"),
        (2.0, "2"),
        (1234.5, "1,234.5"),
        (1200, "1,200"),
        (0, "0"),
        (True, "—"),
        (False, "—"),
        ("12", "—"),
        (None, "—"),
        (float("nan"), "—"),
    ],
)
def test_number_text(value: object, text: str) -> None:
    assert number_text(value) == text


def test_compact_json() -> None:
    assert compact_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
    cut = compact_json({"b": 1, "a": "é"}, limit=5)
    assert cut == '{"a":…'
    assert cut.endswith("…")
    assert compact_json("x", limit=5) == '"x"'  # short enough: never cut


def test_more_json_is_never_cut() -> None:
    value = {"text": "y" * 500}
    shown = more_json(value)
    assert "y" * 500 in shown
    assert "…" not in shown
    assert more_json({"b": 1, "a": "é"}) == '{\n  "a": "é",\n  "b": 1\n}'

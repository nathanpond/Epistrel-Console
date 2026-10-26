"""The shared M4 helpers #140 introduced (#135 conventions 2, 3, 5 and 11): the short Engine id,
the history turn link, the Retrieval link (absent without the Retrieval tab), and the count
filter; and #141's position text (convention 4)."""

import pytest
from django.test import override_settings

from console.inspect import history_turn_url, position_text, retrieval_url, short_id
from console.templatetags.console_format import count
from console.templatetags.inspect_tags import engine_id

STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"


def test_short_id_and_engine_id() -> None:
    assert short_id(STORY) == "…4P7R0T2V"
    assert short_id("ABC") == "ABC"
    assert engine_id(STORY) == f'<code title="{STORY}">…4P7R0T2V</code>'
    assert engine_id(None) == "—"
    assert engine_id('<"x">') == '<code title="&lt;&quot;x&quot;&gt;">&lt;&quot;x&quot;&gt;</code>'


def test_history_turn_url() -> None:
    assert history_turn_url(STORY, 12) == f"/stories/{STORY}/history/?before_turn=13#turn-12-0"


def test_retrieval_url() -> None:
    assert retrieval_url(STORY, {"kind": "semantic", "tier": "hot"}) == (
        f"/stories/{STORY}/inspect/retrieval/?kind=semantic&tier=hot"
    )
    assert retrieval_url(STORY, {}) == f"/stories/{STORY}/inspect/retrieval/"


def test_retrieval_url_is_none_without_the_retrieval_tab() -> None:
    with override_settings(ROOT_URLCONF="tests.inspect.urls_without_retrieval"):
        assert retrieval_url(STORY, {"kind": "semantic"}) is None


@pytest.mark.parametrize(
    ("turn", "beat", "variant", "shown"),
    [
        (12, 3, None, "turn 12 · beat 3"),
        (12, 3, 2, "turn 12 · v2 · beat 3"),
        (12, None, None, "turn 12"),
        (None, 3, None, "beat 3"),
        (None, None, None, None),
    ],
)
def test_position_text(turn: int | None, beat: int | None, variant: int | None, shown: str) -> None:
    assert position_text(turn, beat, variant) == shown


@pytest.mark.parametrize(
    ("value", "shown"), [(0, "0"), (1234567, "1,234,567"), (-5, "-5"), (True, "—"), ("3", "—")]
)
def test_count(value: object, shown: str) -> None:
    assert count(value) == shown

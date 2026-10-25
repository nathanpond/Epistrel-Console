"""The shared M4 helpers #140 introduced (#135 conventions 2, 3, 5 and 11): the short Engine id,
the history turn link, the Retrieval link (absent while #141 isn't registered), and the count
filter."""

import pytest
from django.test import override_settings

from console.inspect import history_turn_url, retrieval_url, short_id
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


def test_retrieval_url_is_none_until_retrieval_is_registered() -> None:
    assert retrieval_url(STORY, {"kind": "semantic"}) is None
    with override_settings(ROOT_URLCONF="tests.inspect.urls"):
        assert retrieval_url(STORY, {"kind": "semantic", "tier": "hot"}) == (
            f"/stories/{STORY}/inspect/retrieval/?kind=semantic&tier=hot"
        )
        assert retrieval_url(STORY, {}) == f"/stories/{STORY}/inspect/retrieval/"


@pytest.mark.parametrize(
    ("value", "shown"), [(0, "0"), (1234567, "1,234,567"), (-5, "-5"), (True, "—"), ("3", "—")]
)
def test_count(value: object, shown: str) -> None:
    assert count(value) == shown

"""#53: a swap to a chain whose ``requires_check`` is true warns, on its confirm page, that the
Engine re-checks those turns first and pauses the story; a chain without it does not. The flag
travels in the link (``&check=1``), so the confirm GET still makes no Engine call."""

import pytest
from django.test import Client

from tests.fakes.engine import FakeEngine
from tests.history.helpers import answer
from tests.timeline.helpers import (
    PLAY,
    STORY,
    answer_play,
    chain,
    flash,
    html,
    path,
    posted,
    recheck_json,
    timeline_story,
    url,
)

pytestmark = pytest.mark.django_db

WARNING = (
    "These turns were checked under older guardrails; the Engine re-checks them first and "
    "pauses the story until it finishes."
)


def test_recheck_warning_when_the_chain_requires_a_check(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = html(operator_client, url("swap", chain="3:2", check="1"))
    assert WARNING in page
    assert engine.requests == []


def test_no_recheck_warning_otherwise(operator_client: Client, engine: FakeEngine) -> None:
    page = html(operator_client, url("swap", chain="3:2"))
    assert WARNING not in page
    assert "Make chain turn 3 · variant 2 the active timeline" in page
    assert engine.requests == []


def test_play_view_links_carry_the_check_flag(operator_client: Client, engine: FakeEngine) -> None:
    story = timeline_story(
        variant_chains=[
            chain(1, 1, "active", tip_turn=2),
            chain(2, 2, "dormant"),
            chain(3, 2, "dormant", requires_check=True),
        ]
    )
    answer_play(engine, story=story)
    page = html(operator_client, PLAY)
    assert "chain=3%3A2&amp;check=1" in page
    assert "chain=2%3A2&amp;check" not in page
    assert "chain=2%3A2" in page


def test_history_variants_links_carry_the_check_flag(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)  # list_variants: chain 3:2 dormant and requires_check
    page = html(operator_client, f"/stories/{STORY}/history/")
    assert "&amp;chain=3%3A2&amp;check=1" in page


def test_a_started_recheck_says_so(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("swap"), 202, json=recheck_json())
    response = operator_client.post(
        url("swap", chain="3:2", check="1"), posted(chain="3:2", check="1")
    )
    assert response.status_code == 302  # #81: the check page follows the re-check
    assert (
        response["Location"]
        == f"/stories/{STORY}/rating/checks/01JB2Z8Y5Q3W6K9M1N4P7R0JOB/?op=swap"
    )
    (line,) = flash(response)
    assert line.startswith("Held for a guardrail re-check")

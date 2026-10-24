"""An archived story (#48): the form is disabled, and a forced turn shows the Engine's refusal."""

from typing import Any

import pytest
from django.test import Client

from tests.fakes.engine import FakeEngine
from tests.play.helpers import (
    RESPOND,
    STORY,
    answer_page,
    error_json,
    post_turn,
    snapshot,
    story_json,
)

pytestmark = pytest.mark.django_db


def archived() -> dict[str, Any]:
    return story_json(status="archived", archived_at="2026-10-24T22:00:00Z")


def test_an_archived_story_disables_the_form(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine, story=archived())
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "Archived — unarchive to play" in html
    assert "<fieldset disabled>" in html
    assert f"/stories/{STORY}/unarchive/" in html


def test_a_forced_turn_shows_the_engines_refusal(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", RESPOND, 409, json=error_json("story_archived", "the story is archived"))
    html = post_turn(operator_client, snapshot=snapshot(story=archived()))
    assert "Engine error 409 story_archived" in html
    assert "the story is archived" in html
    assert "<fieldset disabled>" in html  # the re-rendered form stays disabled

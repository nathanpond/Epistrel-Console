"""Regenerate's directed answer (#292): a 202 ``DirectedPending`` (the reply held in a rewrite
session for review) reaches the session page when ``stories:rewrite`` resolves, else #43's
panel with the held-for-review sentence. Nothing sends a directive before #301, which replaces
this."""

from typing import Any

from django.test import Client, override_settings

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.timeline.helpers import STORY, flash, path, regenerate_posted, url

SESSION = "01JM7RW0000000000000000004"
SENTENCE = "The Engine held this reply for review in a rewrite session"


def directed() -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("m7/directed_pending")
    body["rewrite_session"]["story_id"] = STORY
    return body


def test_a_directed_answer_reaches_the_session_or_the_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", path("regenerate"), 202, json=directed())
    # The real urlconf before #297: ``stories:rewrite`` is unregistered, so the panel shows.
    response = operator_client.post(url("regenerate"), regenerate_posted())
    assert response.status_code == 200
    page = response.content.decode()
    assert SENTENCE in page
    assert f"<code>{SESSION}</code>" in page
    assert f'href="/stories/{STORY}/"' in page
    assert flash(response) == []
    row = EngineOperation.objects.get(kind="regenerate")
    assert (row.http_status, row.outcome) == (202, EngineOperation.Outcome.OK)  # ``ended``
    # E24's linked case: the shared M7 stub urlconf (``stories:rewrite``).
    with override_settings(ROOT_URLCONF="tests.urls_with_m7_pages"):
        linked = operator_client.post(url("regenerate"), regenerate_posted())
    assert linked.status_code == 302
    assert linked["Location"] == f"/stories/{STORY}/rewrites/{SESSION}/"

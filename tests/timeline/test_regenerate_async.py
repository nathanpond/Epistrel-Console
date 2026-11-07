"""#53: regenerate and #48's waiting/busy handling. The Engine never queues a regenerate (its
recorded schema declares no queued 202 and no ``Prefer``; M7's 202 is a directed regenerate's
``DirectedPendingBody``; another running turn is 423 ``story_busy``),
so the Console sends no ``Prefer: respond-async`` and shows #48's busy message instead of a
waiting card."""

import json

import pytest
from django.test import Client

from console.engine.schema import RECORDED_PATH
from tests.fakes.engine import FakeEngine
from tests.timeline.helpers import STORY, calls, error_json, path, regenerate_posted, url

pytestmark = pytest.mark.django_db


def test_the_engine_declares_no_queued_regenerate() -> None:
    """M7 (#291) declares a 202, but it is a directed regenerate's ``DirectedPendingBody`` (a
    rewrite session to answer), not a queued turn: still no ``Prefer``."""
    doc = json.loads(RECORDED_PATH.read_text(encoding="utf-8"))
    operation = doc["paths"]["/stories/{story_id}/regenerate"]["post"]
    accepted = operation["responses"].get("202", {}).get("content", {})
    assert accepted["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DirectedPendingBody"
    }
    assert "Prefer" not in {p["name"] for p in operation["parameters"]}


def test_a_running_turn_shows_the_busy_message_not_a_waiting_card(
    operator_client: Client, engine: FakeEngine
) -> None:
    busy = error_json("story_busy", "a turn is running", reason="in_flight", operation_id="01OPX")
    engine.respond("POST", path("regenerate"), 423, json=busy, headers={"Retry-After": "3"})
    page = operator_client.post(url("regenerate"), regenerate_posted()).content.decode()
    (request,) = calls(engine, "POST", f"/stories/{STORY}/regenerate")
    assert "Prefer" not in request.headers
    assert "Story busy — another turn is running" in page
    assert "<code>01OPX</code>" in page
    assert "3 s" in page  # Retry-After
    assert 'class="waiting-card"' not in page

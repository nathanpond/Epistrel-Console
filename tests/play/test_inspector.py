"""The inspector for earlier turns (#48): a turn played from this Console shows its stored
report (#44's log); any other turn says why there is none."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import HTMX, QUEUED_OP, STORY, TOBIN, TURN_OP, answer_page, respond_json

pytestmark = pytest.mark.django_db


def log_row(**fields: Any) -> EngineOperation:
    values: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "story_id": STORY,
        "kind": "respond",
        "http_method": "POST",
        "path": f"/stories/{STORY}/respond",
        "request": {"body": {"output_viewpoint": f"character:{TOBIN}"}, "headers": {}},
        "http_status": 200,
        "engine_operation_id": TURN_OP,
        "timeline_revision": 2,
        "config_revision": 0,
        "idempotency_key": "01JB2Z8Y5Q3W6K9M1N4P7R0KY9",
        "outcome": "ok",
        "response": respond_json(),
    }
    values.update(fields)
    return EngineOperation.objects.create(**values)


def inspect(client: Client, operation: str, **extra: str) -> str:
    url = reverse("stories:inspector", args=[STORY])
    response = client.get(url, {"operation": operation, **extra}, headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


def test_a_turn_played_here_shows_its_stored_report(operator_client: Client) -> None:
    row = log_row()
    html = inspect(operator_client, TURN_OP)
    assert "Turn 1 (variant 1)" in html
    assert "<h3>Turn report</h3>" in html
    assert "<td>generation</td>" in html
    assert "<dt>Timeline revision</dt><dd>2</dd>" in html
    assert "<code>01JB2Z8Y5Q3W6K9M1N4P7R0KY9</code>" in html
    assert reverse("operations:detail", args=[row.pk]) in html


def test_a_turn_not_in_the_log_has_no_report(operator_client: Client) -> None:
    html = inspect(operator_client, "01JB2Z8Y5Q3W6K9M1N4P7R0ZZZ")
    assert "Report not available — this turn was not played from this Console" in html


def test_an_ingested_turn_says_so(operator_client: Client) -> None:
    html = inspect(operator_client, "01JB2Z8Y5Q3W6K9M1N4P7R0ZZZ", origin="ingested")
    assert "No report — this turn was ingested, not generated" in html


def test_a_still_queued_row_has_no_report_yet(operator_client: Client) -> None:
    log_row(
        engine_operation_id=QUEUED_OP,
        http_status=202,
        outcome="queued",
        response={"operation_id": QUEUED_OP, "position": 1},
    )
    assert "Report not available" in inspect(operator_client, QUEUED_OP)


def test_a_rejected_input_shows_its_violations(operator_client: Client) -> None:
    log_row(
        http_status=422,
        outcome="error",
        error_code="guardrail_violation",
        engine_operation_id=TURN_OP,
        response={
            "error": {
                **engine_fixture("respond_guardrail_violation")["error"],
                "details": {
                    **engine_fixture("respond_guardrail_violation")["error"]["details"],
                    "model_calls": [],
                    "elapsed_ms": 10,
                },
            }
        },
    )
    html = inspect(operator_client, TURN_OP)
    assert "Input rejected by a guardrail" in html
    assert "<td><code>minors_no_sexual_content</code></td>" in html


def test_first_load_shows_the_latest_report(operator_client: Client, engine: FakeEngine) -> None:
    log_row(created_at=timezone.now() - timedelta(minutes=5))
    log_row(kind="archive_story", engine_operation_id="", response={"status": "archived"})
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    inspector = html.split('<div id="inspector">')[1]
    assert "Turn 1 (variant 1)" in inspector
    assert "<h3>Turn report</h3>" in inspector


def test_first_load_with_no_turn_asks_to_select_one(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "Select a turn to see its report." in html


def test_transcript_items_select_their_turn(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    inspector_url = reverse("stories:inspector", args=[STORY])
    assert f'hx-get="{inspector_url}?operation={TURN_OP}&amp;origin=generated"' in html
    assert f'href="/stories/{STORY}/?operation={TURN_OP}&amp;origin=generated"' in html


def test_the_no_js_link_renders_the_page_with_that_report(
    operator_client: Client, engine: FakeEngine
) -> None:
    log_row()
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/", {"operation": TURN_OP}).content.decode()
    assert "<h3>Turn report</h3>" in html.split('<div id="inspector">')[1]

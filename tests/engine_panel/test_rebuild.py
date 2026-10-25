"""Rebuild a story's projections (#50): the select, the confirm page, the result."""

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.http import HttpRequest
from django.test import Client

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import play as play_views
from tests.engine_panel.helpers import HELD_STORY, REBUILD, STORY, calls, error
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import answer_page
from tests.stories.helpers import summary

KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KEY"
CONFIRM = f"/engine/rebuild/{STORY}/"
PATH = f"/admin/stories/{STORY}/rebuild"


def _post(client: Client, **extra: str) -> Any:
    data = {"idempotency_key": KEY, "name": "Harbour", **extra}
    return client.post(CONFIRM, data)


def test_the_select_lists_stories_and_disables_held_ones(
    operator_client: Client, engine: FakeEngine
) -> None:
    items = [
        summary(STORY, "Harbour", "active"),
        summary("01JB2Z8Y5Q3W6K9M1N4P7R0T2X", "Old pier", "archived"),
        summary(HELD_STORY, None, "blocked_by_erasure"),
    ]
    engine.respond("GET", "/stories", 200, json={"items": items})

    html = operator_client.get("/engine/").content.decode()

    assert f'<option value="{STORY}">Harbour</option>' in html
    # Archived stories can be rebuilt (maintenance is allowed there).
    assert '<option value="01JB2Z8Y5Q3W6K9M1N4P7R0T2X">Old pier (archived)</option>' in html
    assert f'<option value="{HELD_STORY}" disabled>(held) …{HELD_STORY[-6:]}</option>' in html


def test_the_select_goes_to_the_confirm_page_with_the_name(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories", 200, json={"items": [summary(STORY, "Harbour", "active")]})
    html = operator_client.get("/engine/").content.decode()
    names = re.search(r'name="story_names" value="([^"]+)"', html)
    assert names is not None
    engine.reset()

    response = operator_client.get(
        "/engine/rebuild/", {"story": STORY, "story_names": names.group(1)}
    )

    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert location.path == CONFIRM
    assert parse_qs(location.query) == {"name": ["Harbour"], "next": ["/engine/"]}
    assert engine.requests == []


def test_a_failed_story_read_degrades_only_the_select(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories", 503, json=error("database_unavailable"))
    html = operator_client.get("/engine/").content.decode()
    assert "Engine error 503 database_unavailable" in html
    assert "<h2>Health</h2>" in html


def test_the_confirm_get_makes_no_engine_call(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(CONFIRM, {"name": "Harbour"}).content.decode()

    assert (
        "Rebuild <em>Harbour</em>&#x27;s projections from its event log. Turns are refused "
        "while it runs."
    ) in html
    assert ">Rebuild projections</button>" in html
    assert engine.requests == []


def test_a_synchronous_rebuild_shows_the_counts(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", REBUILD, 200, json=engine_fixture("rebuild_story"))

    response = _post(operator_client, next="/engine/")

    sent = calls(engine, "POST", PATH)
    assert len(sent) == 1
    assert sent[0].headers["Idempotency-Key"] == KEY
    row = EngineOperation.objects.get()
    # Recorded by #44, under the client method's name, with the story.
    assert (row.kind, row.outcome, row.story_id) == ("rebuild_story", "ok", STORY)
    assert row.engine_operation_id == "01JB2Z8Y5Q3W6K9M1N4P7R0OP1"
    assert response.status_code == 302
    assert response["Location"] == f"/engine/rebuild/{row.pk}/?name=Harbour"

    html = operator_client.get(response["Location"]).content.decode()
    assert "Rebuild projections: Harbour" in html
    assert "<tr><td><code>transcript_messages</code></td><td>12</td></tr>" in html
    # Engine order.
    assert html.index("transcript_messages") < html.index("story_heads") < html.index("scene_state")
    assert "<code>01JB2Z8Y5Q3W6K9M1N4P7R0OP1</code>" in html
    assert "<dt>Timeline revision</dt><dd>7</dd>" in html
    assert "Elapsed (Console)" in html
    assert "Rebuilt Harbour&#x27;s projections — operation 01JB2Z8Y5Q3W6K9M1N4P7R0OP1" in html
    assert '<a href="/engine/">Back to Engine</a>' in html


def test_the_play_view_link_returns_to_the_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", REBUILD, 200, json=engine_fixture("rebuild_story"))
    response = _post(operator_client, next=f"/stories/{STORY}/")
    html = operator_client.get(response["Location"]).content.decode()
    assert f'<a href="/stories/{STORY}/">Back to story</a>' in html


def test_a_large_rebuild_states_the_job_honestly(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", REBUILD, 202, json=engine_fixture("rebuild_story_queued"))

    response = _post(operator_client)

    row = EngineOperation.objects.get()
    assert (row.outcome, row.http_status) == ("queued", 202)
    message = (
        "Rebuild running as job 01JB2Z8Y5Q3W6K9M1N4P7R0JOB — the Engine has no job status "
        "endpoint for rebuilds; check the Engine logs"
    )
    html = operator_client.get(response["Location"]).content.decode()
    assert message in html
    assert "rebuild-counts" not in html
    # #44's detail page says the same.
    detail = operator_client.get(f"/operations/{row.pk}/").content.decode()
    assert message in detail


def test_a_busy_story_shows_the_busy_heading(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        REBUILD,
        423,
        json=error("story_busy", "a turn is running", reason="turn_running", operation_id="X"),
        headers={"Retry-After": "2"},
    )

    response = _post(operator_client)

    html = response.content.decode()
    assert response.status_code == 200
    assert "Story busy — a turn is running; try again" in html
    assert "Engine error 423 story_busy" in html
    assert "turn_running" in html
    assert "Retry-After: 2 s" in html
    assert EngineOperation.objects.get().error_code == "story_busy"


def test_other_refusals_render_the_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    # (A 503 erasure_pending is the held message since #87: tests/test_history_erasure.py.)
    engine.respond("POST", REBUILD, 503, json=error("database_unavailable", "down"))
    html = _post(operator_client).content.decode()
    assert "Engine error 503 database_unavailable" in html
    assert "Story busy" not in html


@pytest.mark.parametrize("method", ["put", "delete"])
def test_other_methods_are_refused(
    operator_client: Client, engine: FakeEngine, method: str
) -> None:
    assert getattr(operator_client, method)(CONFIRM).status_code == 405


def test_the_play_view_offers_rebuild(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = engine.transport
        return client

    monkeypatch.setattr(play_views, "engine_client_for", client_with_fake)
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "Rebuild projections</a>" in html
    assert f'href="/engine/rebuild/{STORY}/?name=' in html

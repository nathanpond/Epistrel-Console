"""/stories/ (#45): every story, archived included, with a status filter."""

from typing import Any

import pytest
from django.test import Client

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.stories.helpers import calls, summary

pytestmark = pytest.mark.django_db

ACTIVE = "01JB2Z8Y5Q3W6K9M1N4P7R0A01"
ARCHIVED = "01JB2Z8Y5Q3W6K9M1N4P7R0A02"
PAUSED = "01JB2Z8Y5Q3W6K9M1N4P7R0A03"


def serve(engine: FakeEngine, *items: dict[str, Any]) -> None:
    engine.respond("GET", "/stories", 200, json={"items": list(items)})
    engine.respond("GET", "/stories/{story_id}", 200, json=engine_fixture("get_story"))


def row_html(html: str, story_id: str) -> str:
    start = html.index(f"<code>{story_id}</code>")
    return html[html.rfind("<tr>", 0, start) : html.index("</tr>", start)]


def test_archived_stories_are_listed_by_default(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine, summary(ACTIVE, "Harbour", "active"), summary(ARCHIVED, "Old pier", "archived"))
    html = operator_client.get("/stories/").content.decode()
    assert "Harbour" in html
    assert "Old pier" in html
    (listing,) = calls(engine, "GET", "/stories")
    assert "status" not in listing.url.params  # "all" sends no status


def test_row_shows_id_status_revision_rating_cast_and_play_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine, summary(ACTIVE, "Harbour", "active", timeline_revision=4))
    row = row_html(operator_client.get("/stories/").content.decode(), ACTIVE)
    assert f'<a href="/stories/{ACTIVE}/">Harbour</a>' in row
    assert '<td class="status">active</td>' in row
    assert "<td>4</td>" in row
    assert "<td>Teen</td>" in row
    assert "<td>2</td>" in row  # the cast, from GET /stories/{id}
    assert len(calls(engine, "GET", f"/stories/{ACTIVE}")) == 1


@pytest.mark.parametrize("status", ["active", "archived", "paused"])
def test_status_filter_is_passed_through(
    operator_client: Client, engine: FakeEngine, status: str
) -> None:
    serve(engine)
    html = operator_client.get(f"/stories/?status={status}").content.decode()
    (listing,) = calls(engine, "GET", "/stories")
    assert listing.url.params["status"] == status
    assert f'<option value="{status}" selected>' in html
    assert f"No {status} stories." in html


def test_unknown_status_falls_back_to_all(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = operator_client.get("/stories/?status=blocked_by_erasure").content.decode()
    (listing,) = calls(engine, "GET", "/stories")
    assert "status" not in listing.url.params
    assert '<option value="" selected>All</option>' in html


def test_empty_list_says_no_stories_yet(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = operator_client.get("/stories/").content.decode()
    assert "No stories yet" in html
    assert "<table" not in html


def test_held_story_renders_a_dash_and_its_status(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories", 200, json=engine_fixture("list_stories"))
    engine.respond("GET", "/stories/{story_id}", 200, json=engine_fixture("get_story"))
    response = operator_client.get("/stories/")
    assert response.status_code == 200
    held = row_html(response.content.decode(), "01JB2Z8Y5Q3W6K9M1N4P7R0T2W")
    assert '<a href="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2W/">—</a>' in held
    assert '<td class="status">blocked_by_erasure</td>' in held
    assert "Archive" not in held  # neither active nor archived nor paused
    assert "Delete" in held


def test_paused_row_disables_archive(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, summary(PAUSED, "Busy", "paused"))
    row = row_html(operator_client.get("/stories/").content.decode(), PAUSED)
    assert '<button type="button" disabled>Archive</button>' in row
    assert "Paused while a check runs" in row
    assert f"/stories/{PAUSED}/archive/" not in row


def test_active_row_archive_is_enabled(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, summary(ACTIVE, "Harbour", "active"))
    row = row_html(operator_client.get("/stories/?status=active").content.decode(), ACTIVE)
    assert (
        f'href="/stories/{ACTIVE}/archive/?name=Harbour&amp;next=%2Fstories%2F%3Fstatus%3Dactive"'
        in row
    )
    assert "disabled" not in row
    assert "Paused while a check runs" not in row


def test_archived_row_offers_unarchive_and_every_row_delete(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine, summary(ARCHIVED, "Old pier", "archived"))
    row = row_html(operator_client.get("/stories/").content.decode(), ARCHIVED)
    assert f"/stories/{ARCHIVED}/unarchive/" in row
    assert f"/stories/{ARCHIVED}/archive/" not in row
    assert f"/stories/{ARCHIVED}/delete/" in row


def test_a_failed_cast_read_degrades_that_row_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories", 200, json={"items": [summary(ACTIVE, "Harbour", "active")]})
    engine.respond(
        "GET",
        "/stories/{story_id}",
        503,
        json={"error": {"code": "erasure_pending", "message": "held", "details": {}}},
    )
    response = operator_client.get("/stories/")
    html = response.content.decode()
    assert response.status_code == 200
    assert "Harbour" in html
    assert "<td>—</td>" in row_html(html, ACTIVE)
    assert 'role="alert"' not in html


def test_list_failure_renders_the_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        "/stories",
        503,
        json={"error": {"code": "unavailable", "message": "db", "details": {}}},
    )
    html = operator_client.get("/stories/").content.decode()
    assert "Engine error 503 unavailable" in html

"""/stories/ (#45): every story, archived included, with a status filter."""

from typing import Any

import httpx
import pytest
from django.test import Client

from console.forms.stories import IMPORTING_ROW_NOTE, STATUS_LABELS
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.stories.helpers import calls, summary

pytestmark = pytest.mark.django_db

ACTIVE = "01JB2Z8Y5Q3W6K9M1N4P7R0A01"
ARCHIVED = "01JB2Z8Y5Q3W6K9M1N4P7R0A02"
PAUSED = "01JB2Z8Y5Q3W6K9M1N4P7R0A03"
IMPORTING = "01JB2Z8Y5Q3W6K9M1N4P7R0A04"
HELD = "01JB2Z8Y5Q3W6K9M1N4P7R0A05"


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
    assert '<td class="status"><span title="active">Active</span></td>' in row
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
    html = operator_client.get("/stories/?status=bogus").content.decode()
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
    # #87: a held story's null name reads "(held — erasure pending)", its status is worded.
    assert '<a href="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2W/">(held — erasure pending)</a>' in held
    assert '<td class="status">Held (erasure pending)</td>' in held
    assert "Archive" not in held  # neither active nor archived nor paused
    assert "Delete" in held


def test_paused_row_disables_archive(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, summary(PAUSED, "Busy", "paused"))
    row = row_html(operator_client.get("/stories/").content.decode(), PAUSED)
    assert '<button type="button" disabled>Archive</button>' in row
    assert "Paused while the check runs" in row
    assert f"/stories/{PAUSED}/archive/" not in row


def test_active_row_archive_is_enabled(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, summary(ACTIVE, "Harbour", "active"))
    row = row_html(operator_client.get("/stories/?status=active").content.decode(), ACTIVE)
    assert (
        f'href="/stories/{ACTIVE}/archive/?name=Harbour&amp;next=%2Fstories%2F%3Fstatus%3Dactive"'
        in row
    )
    assert "disabled" not in row
    assert "Paused while the check runs" not in row


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


def test_the_plain_list_sends_no_profile_filter(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#238: only ``?profile=`` sends ``engine_profile``; a bare list's query is empty."""
    serve(engine, summary(ACTIVE, "Harbour", "active"))
    html = operator_client.get("/stories/").content.decode()
    (listing,) = calls(engine, "GET", "/stories")
    assert listing.url.query == b""
    assert "Profile version" not in html


# --- Importing stories (#293, FR-STORE-7) --------------------------------------------------------


def test_an_importing_story_is_listed_as_importing(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(
        engine,
        summary(IMPORTING, "Imported", "importing"),
        summary(ACTIVE, "Harbour", "active"),
        summary(HELD, "Kept", "blocked_by_erasure"),
    )
    html = operator_client.get("/stories/").content.decode()
    row = row_html(html, IMPORTING)
    assert '<td class="status"><span title="importing">Importing</span></td>' in row
    assert '<button type="button" disabled>Archive</button>' in row
    assert '<button type="button" disabled>Delete</button>' in row
    assert f'<span class="note">{IMPORTING_ROW_NOTE}</span>' in row
    assert IMPORTING_ROW_NOTE == "Importing — available once the import ends"
    for absent in ("/archive/", "/delete/", "/unarchive/", "history-link"):
        assert absent not in row, absent
    assert f'<a href="/stories/{IMPORTING}/">Imported</a>' in row  # the name link stays
    # Its cast isn't read (409 story_importing): only the active story's read is made.
    assert calls(engine, "GET", f"/stories/{IMPORTING}") == []
    assert "<td>—</td>" in row
    # Complement: an active story keeps both links, a held one keeps Delete alone (#87).
    active = row_html(html, ACTIVE)
    assert f"/stories/{ACTIVE}/archive/" in active
    assert f"/stories/{ACTIVE}/delete/" in active
    assert "disabled" not in active
    held = row_html(html, HELD)
    assert f"/stories/{HELD}/delete/" in held
    assert "Archive" not in held
    assert IMPORTING_ROW_NOTE not in held


def test_the_filter_offers_importing(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = operator_client.get("/stories/?status=importing").content.decode()
    (listing,) = calls(engine, "GET", "/stories")
    assert listing.url.params["status"] == "importing"
    assert '<option value="importing" selected>Importing</option>' in html
    assert html.index('value="blocked_by_erasure"') < html.index('value="importing"')
    assert "No importing stories." in html
    # Complement: an unknown status still sends none.
    serve(engine)
    operator_client.get("/stories/?status=bogus")
    assert "status" not in calls(engine, "GET", "/stories")[-1].url.params


def test_status_labels_are_the_filter_labels() -> None:
    assert STATUS_LABELS == {
        "active": "Active",
        "archived": "Archived",
        "paused": "Paused",
        "blocked_by_erasure": "Held",
        "importing": "Importing",
    }


def test_an_unknown_status_is_humanised_with_its_raw_value(
    operator_client: Client, engine: FakeEngine
) -> None:
    """A status a later Engine adds (an open enum, E15) is C3-humanised. The fake's schema
    validation can't carry it, so the listing answers through ``side_effect``."""
    serve(engine)
    body = {"items": [summary(ACTIVE, "Harbour", "later_status")]}
    engine.side_effect("GET", "/stories", lambda _request: httpx.Response(200, json=body))
    row = row_html(operator_client.get("/stories/").content.decode(), ACTIVE)
    assert '<td class="status"><span title="later_status">Later status</span></td>' in row

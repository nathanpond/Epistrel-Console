"""Schedule a canon event between turns (#253): the page, the gate, the form, the body, the result
and report pages, and refusals.

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``: Mara the player, Alice, Bob; revision 12).
``m6/scheduled_event_written.json`` is #232's created event (revision 13); this story adds
``m6/scheduled_event_created_at_time.json`` ("the breakfast", an ``"HH:MM"`` event placed at its
next occurrence, 1890, with Alice and a place). Refusal bodies are built inline (D16)."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

import httpx
import pytest
from django.http import HttpRequest, QueryDict
from django.test import Client, override_settings
from django.utils.html import escape

from console.cast import Character
from console.engine.client import EngineClient
from console.engine.models import Story
from console.engine_access import engine_client_for
from console.forms.scheduled_events import (
    CAST_UNCHECKED,
    NAME_REQUIRED,
    NAME_TOO_LONG,
    OWNER_INVALID,
    PLACE_INVALID,
    TOO_MANY_CHARACTERS,
    ScheduledEventForm,
)
from console.forms.world import PLANNED_INVALID
from console.models import EngineOperation
from console.paging import CAST_UNAVAILABLE
from console.views import inspect as views
from console.wording import UNKNOWN_CHARACTER
from console.world import PLACE_UNKNOWN
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
BOB = "01JM6CH0000000000000000003"
PLACE = "01JM6EN0000000000000000001"
KEY = "01JM6QP00000000000000000K1"
EVENTS = "/stories/{story_id}/scheduled-events"
EVENTS_PATH = f"/stories/{STORY}/scheduled-events"
SECTION_URL = f"/stories/{STORY}/inspect/world/scheduled-events/"
URL = f"/stories/{STORY}/inspect/world/scheduled-events/new/"
WRITTEN_URL = f"/stories/{STORY}/inspect/world/scheduled-events/written/"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
WRITES = ("PUT", "POST", "PATCH", "DELETE")
CLOCK_OFF_REASON = "Needs <code>clock.level</code> at <code>prompt_only</code> or above"
SHELL_PATHS = [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
UNANCHORED = (
    "The clock's anchor doesn't fix the time of day, so \"07:30\" can't be placed — "
    "enter minutes from the story's start"
)


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m6/{name}"))
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v3())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond("GET", EVENTS, 200, json=fixture("scheduled_events"))
    fake.respond("GET", "/stories/{story_id}/clock", 200, json=fixture("clock"))
    fake.respond("POST", EVENTS, 201, json=fixture("scheduled_event_written"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def creates(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "POST" and r.url.path == EVENTS_PATH]


def writes(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method in WRITES]


def sent(engine: FakeEngine) -> dict[str, Any]:
    (create,) = creates(engine)
    body: dict[str, Any] = json.loads(create.content)
    return body


def get(client: Client, url: str = URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def post(client: Client, **fields: Any) -> str:
    data: dict[str, Any] = {"idempotency_key": KEY, "expected_revision": "12"}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    response = client.post(URL, data)
    assert response.status_code == 200
    return response.content.decode()


def field(html: str, name: str) -> str:
    found = re.search(
        rf'<div class="field field-{name}">(.*?)</div>\s*(?:<div class="field|<p><button)',
        html,
        re.S,
    )
    assert found is not None, name
    return found.group(1)


def hidden(html: str, name: str) -> str | None:
    found = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)"', html)
    return found.group(1) if found is not None else None


def refusal(html: str) -> str:
    found = re.search(r'<p class="errorlist event-new-refusal" role="alert">(.*?)</p>', html, re.S)
    assert found is not None
    return found.group(1)


def non_field(html: str) -> list[str]:
    found = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    if found is None:
        return []
    return [unescape(e) for e in re.findall(r'<p class="errorlist">(.*?)</p>', found.group(1))]


def form_html(html: str) -> str:
    assert 'class="event-new-form"' in html
    return html.split('class="event-new-form"')[1].split("</form>")[0]


def submit(html: str) -> str:
    found = re.search(r'<button type="submit" class="event-new-submit"[^>]*>', html)
    assert found is not None
    return found.group(0)


def checkbox_values(html: str, name: str) -> list[str]:
    return re.findall(rf'<input type="checkbox" name="{name}" value="([^"]*)"', html)


# --- the page and the gate ---------------------------------------------------------------------


def test_new_link_on_section(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, SECTION_URL)
    link = f'<a class="events-new-link" href="{URL}">New scheduled event</a>'
    assert link in html
    # Above the table.
    assert html.index(link) < html.index('<table class="scheduled-events">')
    # In every state of the section: an events read failure too.
    engine.fail("GET", EVENTS, httpx.ConnectError("down"))
    assert link in get(operator_client, SECTION_URL)


def test_new_link_greyed_when_clock_off(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = get(operator_client, SECTION_URL)
    found = re.search(
        r'<span class="disabled events-new-link"[^>]*>New scheduled event</span>', html
    )
    assert found is not None
    assert 'aria-disabled="true"' in found.group(0)
    assert "clock.level" in found.group(0)
    assert f'href="{URL}"' not in html
    assert CLOCK_OFF_REASON in html.split("New scheduled event</span>")[1]
    form = get(operator_client)
    assert "disabled" in submit(form)
    assert "clock.level" in submit(form)
    assert CLOCK_OFF_REASON in form
    assert "disabled" not in field(form, "proposition")


def test_get_makes_only_the_shell_reads(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert "New scheduled event</h3>" in html
    assert "<title>Inspect · New scheduled event · " in html
    assert (
        f'<a class="active" aria-current="page" href="{SECTION_URL}">Scheduled events</a>' in html
    )
    assert hidden(html, "expected_revision") == "12"
    key = hidden(html, "idempotency_key")
    assert key is not None
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", key)
    assert hidden(get(operator_client), "idempotency_key") != key  # fresh per render
    form = form_html(html)
    for attr in ("min=", "max=", "maxlength"):
        assert attr not in form
    order = [m for m in re.findall(r'<div class="field field-([a-z_]+)">', form)]
    assert order == ["proposition", "planned", "owner", "participants", "shared_owners", "place"]
    assert "Event" in field(html, "proposition")
    assert escape(
        "Minutes from the story's start (e.g. 1140), or a time of day (HH:MM): its next occurrence"
    ) in field(html, "planned")
    owner = field(html, "owner")
    assert re.findall(r'<option value="([^"]*)"', owner) == ["", "world", "author"]
    assert "Engine default (World)" in owner
    assert checkbox_values(html, "participants") == [MARA, ALICE, BOB]
    assert checkbox_values(html, "shared_owners") == [MARA, ALICE, BOB]
    assert "Mara (player)" in field(html, "participants")
    assert "Shared with" in field(html, "shared_owners")
    assert "They may change the event as its owner may" in field(html, "shared_owners")
    assert "Place id" in field(html, "place")
    assert ">Schedule</button>" in html
    assert "disabled" not in submit(html)
    assert not writes(engine)


def test_gated_post_makes_no_write(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = post(operator_client, proposition="the dinner", planned="1140")
    assert CLOCK_OFF_REASON in refusal(html)
    assert 'value="the dinner"' in field(html, "proposition")
    assert hidden(html, "idempotency_key") == KEY
    # Complement: zero POST /stories/{id}/scheduled-events.
    assert creates(engine) == []


def test_cast_unavailable_hides_lists(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", STORY_PATH, httpx.ConnectError("down"))
    html = get(operator_client)
    assert CAST_UNAVAILABLE in html
    assert 'type="checkbox"' not in html
    assert "field-participants" not in html
    assert "field-shared_owners" not in html
    assert hidden(html, "expected_revision") is None
    # A minimal event still posts, without expected_revision.
    operator_client.post(
        URL, {"idempotency_key": KEY, "proposition": "the dinner", "planned": "1140"}
    )
    (create,) = creates(engine)
    assert json.loads(create.content) == {"proposition": "the dinner", "planned": 1140}
    assert "X-Epistrel-Expected-Revision" not in create.headers


def test_cast_unavailable_refuses_posted_characters(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("GET", STORY_PATH, httpx.ConnectError("down"))
    html = post(operator_client, proposition="the dinner", planned="1140", participants=[ALICE])
    assert CAST_UNCHECKED in non_field(html)
    assert CAST_UNAVAILABLE in html
    assert creates(engine) == []


# --- local validation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "where", "message"),
    [
        ({"proposition": ""}, "proposition", NAME_REQUIRED),
        ({"proposition": "   "}, "proposition", NAME_REQUIRED),
        ({"proposition": "x" * 201}, "proposition", NAME_TOO_LONG),
        ({"planned": ""}, "planned", PLANNED_INVALID),
        ({"planned": "24:00"}, "planned", PLANNED_INVALID),
        ({"planned": "7:30"}, "planned", PLANNED_INVALID),
        ({"planned": "-5"}, "planned", PLANNED_INVALID),
        ({"place": "not-an-id"}, "place", PLACE_INVALID),
        ({"owner": "alice"}, "owner", OWNER_INVALID),
        ({"participants": ["01JM6CH0000000000000000009"]}, "participants", UNKNOWN_CHARACTER),
        ({"shared_owners": ["nobody"]}, "shared_owners", UNKNOWN_CHARACTER),
    ],
)
def test_local_validation_zero_calls(
    operator_client: Client,
    engine: FakeEngine,
    fields: dict[str, Any],
    where: str,
    message: str,
) -> None:
    data = {"proposition": "the dinner", "planned": "1140", **fields}
    html = post(operator_client, **data)
    assert escape(message) in field(html, where)
    assert creates(engine) == []
    assert "Scheduled:" not in html


def test_name_of_200_after_trimming_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, proposition=f"  {'é' * 198} x  ", planned="1140")
    assert sent(engine)["proposition"] == f"{'é' * 198} x"


def big_cast(n: int) -> list[Character]:
    story = fixture("story")
    template = story["characters"][1]
    chars = [
        {**template, "id": f"01JM6CH00000000000000001{i:02d}", "name": f"C{i}"} for i in range(n)
    ]
    parsed = Story.from_json({**story, "characters": chars})
    assert parsed is not None
    return list(parsed.cast)


def test_too_many_participants() -> None:
    cast = big_cast(21)
    ids = [c.id for c in cast]
    query = QueryDict(mutable=True)
    query["proposition"] = "p"
    query["planned"] = "1"
    query.setlist("participants", ids)
    query.setlist("shared_owners", ids)
    form = ScheduledEventForm(query, cast=cast, initial_revision=12)
    assert not form.is_valid()
    assert form.errors["participants"] == [TOO_MANY_CHARACTERS]
    assert form.errors["shared_owners"] == [TOO_MANY_CHARACTERS]
    # Duplicates are dropped before the count.
    query.setlist("participants", ids[:20] + ids[:5])
    query.setlist("shared_owners", [])
    form = ScheduledEventForm(query, cast=cast, initial_revision=12)
    assert form.is_valid(), form.errors
    assert form.body()["participants"] == ids[:20]


# --- the body ----------------------------------------------------------------------------------


def test_minimal_body(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, proposition=" the dinner ", planned="1140", owner="", place="  ")
    body = sent(engine)
    assert body == {"proposition": "the dinner", "planned": 1140}
    # Complement: no optional keys, never null or [].
    for key in ("owner", "participants", "place", "shared_owners"):
        assert key not in body


def test_full_body(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", EVENTS, 201, json=fixture("scheduled_event_created_at_time"))
    html = get(operator_client)
    key = hidden(html, "idempotency_key")
    post(
        operator_client,
        proposition="the breakfast",
        planned="07:30",
        owner="author",
        participants=[BOB, ALICE, BOB],
        shared_owners=[ALICE, MARA],
        place=PLACE.lower(),
        idempotency_key=key,
        expected_revision=hidden(html, "expected_revision"),
    )
    body = sent(engine)
    assert body == {
        "proposition": "the breakfast",
        "planned": "07:30",
        "owner": "author",
        "participants": [ALICE, BOB],
        "place": PLACE,
        "shared_owners": [MARA, ALICE],
    }
    assert isinstance(body["planned"], str)
    (create,) = creates(engine)
    assert create.headers["X-Epistrel-Expected-Revision"] == "12"
    assert create.headers["Idempotency-Key"] == key


def test_explicit_world_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, proposition="the dinner", planned="1140", owner="world")
    assert sent(engine)["owner"] == "world"


# --- the result and the report -----------------------------------------------------------------


def lines(html: str) -> list[str]:
    found = re.search(r'<ul class="event-written-lines">(.*?)</ul>', html, re.S)
    assert found is not None
    return [
        unescape(re.sub(r"<[^>]+>", "", item)).strip()
        for item in re.findall(r"<li[^>]*>(.*?)</li>", found.group(1), re.S)
    ]


def assert_result(html: str, row: EngineOperation) -> None:
    event = fixture("scheduled_event_written")["event"]
    name = event["name"]
    assert f"Scheduled: {escape(name)}</h3>" in html
    assert f"<title>Inspect · Scheduled: {escape(name)} · " in html
    table = html.split('<table class="scheduled-events">')[1].split("</table>")[0]
    assert len(re.findall(r"<tr id=", table)) == 1
    assert f'<tr id="ev-{event["event_id"]}" class="canon">' in table
    assert ">Canon</span>" in table
    assert ">Pending</span>" in table
    assert ">+04h 00m</span>" in table
    assert "Mara (player), Alice" in table
    assert "<th>Actions</th>" not in table
    assert lines(html) == [
        f"Operation: {fixture('scheduled_event_written')['operation_id']}",
        ("Story revision now 13"),
    ]
    # Complement: no "Slot:" line.
    assert "Slot:" not in html
    assert f'<a class="operation-link" href="/operations/{row.pk}/">' in html
    back = f'<a class="events-back" href="{SECTION_URL}#ev-{event["event_id"]}">'
    assert f"{back}Back to Scheduled events</a>" in html
    assert f'<a class="schedule-another" href="{URL}">Schedule another</a>' in html
    assert f'href="{WRITTEN_URL}?report={row.pk}"' in html
    assert (
        f'<a class="active" aria-current="page" href="{SECTION_URL}">Scheduled events</a>' in html
    )


def test_result_page(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, proposition="the dinner", planned="1140")
    row = EngineOperation.objects.get()
    assert row.kind == "create_scheduled_event"
    assert_result(html, row)
    assert "Already written" not in html


def test_result_page_at_time(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", EVENTS, 201, json=fixture("scheduled_event_created_at_time"))
    html = post(operator_client, proposition="the breakfast", planned="07:30")
    assert "Scheduled: the breakfast</h3>" in html
    assert ">+31h 30m</span>" in html
    row = html.split('<tr id="ev-01JM6EV0000000000000000002" class="canon">')[1].split("</tr>")[0]
    assert '<td class="owner">World</td>' in row
    assert '<td class="participants">Alice</td>' in row
    assert f'<a href="/stories/{STORY}/inspect/entities/{PLACE}/">' in row


def test_report_page(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, proposition="the dinner", planned="1140")
    row = EngineOperation.objects.get()
    engine.requests.clear()
    html = get(operator_client, f"{WRITTEN_URL}?report={row.pk}")
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert_result(html, row)


@pytest.mark.parametrize(
    "case", ["story", "kind", "error", "unparsed", "nonint", "blank", "absent", "missing"]
)
def test_report_page_complement(operator_client: Client, engine: FakeEngine, case: str) -> None:
    post(operator_client, proposition="the dinner", planned="1140")
    row = EngineOperation.objects.get()
    report: object = row.pk
    if case == "story":
        row.story_id = "01JM6ST0000000000000000002"
    elif case == "kind":
        row.kind = "advance_clock"
    elif case == "error":
        row.outcome = EngineOperation.Outcome.ERROR
    elif case == "unparsed":
        row.response = {"event": "x"}
    elif case == "nonint":
        report = "x"
    elif case == "blank":
        report = ""
    elif case == "missing":
        report = row.pk + 1
    row.save()
    engine.requests.clear()
    url = WRITTEN_URL if case == "absent" else f"{WRITTEN_URL}?report={report}"
    html = get(operator_client, url)
    assert NOT_FOUND in html
    assert f'<a href="{SECTION_URL}">Back to Scheduled events</a>' in html
    assert "event-written-lines" not in html
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert not writes(engine)


def test_replayed_row(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        EVENTS,
        201,
        json=fixture("scheduled_event_written"),
        headers={"Idempotent-Replay": "true"},
    )
    html = post(operator_client, proposition="the dinner", planned="1140")
    assert '<p class="notice replayed">Already written' in html


# --- refusals ----------------------------------------------------------------------------------


def refuse(
    engine: FakeEngine,
    status: int,
    code: str,
    message: str = "refused",
    headers: dict[str, str] | None = None,
    **details: Any,
) -> None:
    engine.respond(
        "POST", EVENTS, status, json=error_json(code, message, **details), headers=headers
    )


def refused_page(client: Client, **fields: Any) -> str:
    data: dict[str, Any] = {
        "proposition": "the dinner",
        "planned": "07:30",
        "owner": "world",
        "participants": [ALICE],
        "shared_owners": [BOB],
        "place": PLACE,
        "expected_revision": "9",
    }
    data.update(fields)
    html = post(client, **data)
    assert 'class="event-new-form"' in html
    assert 'value="the dinner"' in field(html, "proposition")
    assert f'value="{data["planned"]}"' in field(html, "planned")
    assert '<option value="world" selected>' in field(html, "owner")
    assert re.search(rf'value="{ALICE}"[^>]*checked', field(html, "participants"))
    assert re.search(rf'value="{BOB}"[^>]*checked', field(html, "shared_owners"))
    assert f'value="{PLACE}"' in field(html, "place")
    # D8: a fresh key and the POST's shell revision.
    assert hidden(html, "idempotency_key") != KEY
    assert hidden(html, "expected_revision") == "12"
    # Complement: none renders the result page.
    assert "Scheduled:" not in html
    assert "event-written-lines" not in html
    return html


def test_clock_unanchored_beside_planned(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "clock_unanchored", to="07:30")
    html = refused_page(operator_client)
    assert escape(UNANCHORED) in field(html, "planned")
    assert "event-new-refusal" not in html
    assert len(creates(engine)) == 1


def test_unknown_participant_beside_participants(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(
        engine,
        422,
        "validation_error",
        field="body.participants",
        reason="unknown_character",
        character_id=ALICE,
    )
    html = refused_page(operator_client)
    assert escape(UNKNOWN_CHARACTER) in field(html, "participants")
    assert escape(UNKNOWN_CHARACTER) not in field(html, "shared_owners")


def test_unknown_place_beside_place(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="body.place", reason="unknown_place")
    html = refused_page(operator_client)
    assert escape(PLACE_UNKNOWN) in field(html, "place")


def test_unknown_shared_owner_beside_shared_owners(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(
        engine,
        422,
        "viewpoint_unresolved",
        field="body.shared_owners",
        reason="unknown_character",
    )
    html = refused_page(operator_client)
    assert escape(UNKNOWN_CHARACTER) in field(html, "shared_owners")
    assert escape(UNKNOWN_CHARACTER) not in field(html, "participants")


def test_module_off_is_a_form_refusal(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "module_off", module="clock")
    html = refused_page(operator_client)
    assert "<code>clock.level</code>" in refusal(html)
    assert f'<a href="/stories/{STORY}/config/">Config</a>' in refusal(html)
    # D8: the submit stays live (no re-grey) and no extra read.
    assert "disabled" not in submit(html)
    assert [r.url.path for r in engine.requests] == [*SHELL_PATHS, EVENTS_PATH]


def test_story_busy_retry_after(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 423, "story_busy", headers={"Retry-After": "4"}, reason="in_flight")
    html = refused_page(operator_client)
    assert refusal(html) == "Story busy — another turn is running · retry after 4 s"


def test_revision_conflict(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "revision_conflict", current_revision=14)
    engine.respond("GET", STORY_PATH, 200, json={**fixture("story"), "timeline_revision": 12})
    html = refused_page(operator_client)
    assert "changed" in refusal(html)
    (create,) = creates(engine)
    assert create.headers["X-Epistrel-Expected-Revision"] == "9"


def test_archived(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "story_archived")
    html = refused_page(operator_client)
    assert refusal(html) == "This story is archived — unarchive it to schedule an event."


def test_request_validation_beside_field(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        422,
        "validation_error",
        "request validation failed",
        errors=[{"field": "body.proposition", "message": "String should have at most 200"}],
    )
    html = refused_page(operator_client)
    assert "String should have at most 200" in field(html, "proposition")


def test_request_validation_indexed_list_path(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        422,
        "validation_error",
        "request validation failed",
        errors=[{"field": "body.participants[0]", "message": "String should match pattern"}],
    )
    html = refused_page(operator_client)
    assert "String should match pattern" in field(html, "participants")


def test_other_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "idempotency_conflict")
    html = refused_page(operator_client)
    assert "engine-error" in html.split('class="event-new-form"')[0]


def test_invalid_form_and_transport_error_keep_key_and_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post(operator_client, proposition="the dinner", planned="x", expected_revision="9")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "9"
    assert creates(engine) == []
    engine.fail("POST", EVENTS, httpx.ConnectError("down"))
    html = post(operator_client, proposition="the dinner", planned="1140", expected_revision="9")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "9"
    assert "engine-error" in html.split('class="event-new-form"')[0]
    assert 'value="1140"' in field(html, "planned")
    assert "Scheduled:" not in html

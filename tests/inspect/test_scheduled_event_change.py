"""Reschedule or cancel a scheduled event between turns (#262): the row actions, the Reschedule
form and its PATCH, the cancel confirm page and its DELETE, and refusals.

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``: Mara the player, Alice, Bob; revision 12). The omniscient
list is ``m6/scheduled_events.json`` plus "the dinner" (``…EV…05``, pending at 1140, shared with
Bob). This story adds ``m6/scheduled_event_rescheduled.json`` (the dinner pending at 1200, shared
with nobody, revision 13) and ``m6/scheduled_event_cancelled.json`` (the dinner cancelled, Bob
kept). Refusal bodies are built inline (D16)."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.scheduled_events import SHARED_UNCHECKED, TOO_MANY_CHARACTERS
from console.forms.world import PLANNED_INVALID
from console.inspect import GATE_UNREAD
from console.models import EngineOperation
from console.paging import CAST_UNAVAILABLE
from console.scheduled_events import NO_CHANGES, NOT_LISTED, EventSnapshot
from console.views import inspect as views
from console.views import inspect_scheduled_events
from console.wording import UNKNOWN_CHARACTER
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
BOB = "01JM6CH0000000000000000003"
OUTSIDER = "01JM6CH0000000000000000009"
DINNER = "01JM6EV0000000000000000005"
MEETING = "01JM6EV0000000000000000001"
CARDS = "01JM6EV0000000000000000003"
KEY = "01JM6QP00000000000000000K1"
EVENTS = "/stories/{story_id}/scheduled-events"
EVENT = "/stories/{story_id}/scheduled-events/{event_id}"
EVENTS_PATH = f"/stories/{STORY}/scheduled-events"
EVENT_PATH = f"{EVENTS_PATH}/{DINNER}"
CONFIG_PATH = f"/stories/{STORY}/effective-config"
SHELL_PATHS = [CONFIG_PATH, f"/stories/{STORY}"]
SECTION_URL = f"/stories/{STORY}/inspect/world/scheduled-events/"
URL = f"{SECTION_URL}{DINNER}/reschedule/"
CANCEL_URL = f"{SECTION_URL}{DINNER}/cancel/"
WRITTEN_URL = f"{SECTION_URL}written/"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
WRITES = ("PUT", "POST", "PATCH", "DELETE")
CLOCK_OFF_REASON = "Needs <code>clock.level</code> at <code>prompt_only</code> or above"
SETTLED_CANCELLED = "This event is already cancelled — it can't be changed"
UNANCHORED = (
    "The clock's anchor doesn't fix the time of day, so \"07:30\" can't be placed — "
    "enter minutes from the story's start"
)
RESCHEDULE_HELP = (
    "Blank keeps its time. A new time makes it pending again, and it may stop the clock once more"
)


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m6/{name}"))
    return body


def dinner(**changes: Any) -> dict[str, Any]:
    event = {
        **fixture("scheduled_event_rescheduled")["event"],
        "planned": 1140,
        "shared_owners": [BOB],
    }
    event.update(changes)
    return event


def listing(*extra: dict[str, Any], revision: int = 12) -> dict[str, Any]:
    body = fixture("scheduled_events")
    body["events"].extend(extra or [dinner()])
    body["timeline_revision"] = revision
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    monkeypatch.setattr(inspect_scheduled_events, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v3())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond("GET", EVENTS, 200, json=listing())
    fake.respond("GET", "/stories/{story_id}/clock", 200, json=fixture("clock"))
    fake.respond("PATCH", EVENT, 200, json=fixture("scheduled_event_rescheduled"))
    fake.respond("DELETE", EVENT, 200, json=fixture("scheduled_event_cancelled"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def patches(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "PATCH"]


def deletes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "DELETE"]


def writes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method in WRITES]


def sent(engine: FakeEngine) -> dict[str, Any]:
    (patch,) = patches(engine)
    assert patch.url.path == EVENT_PATH
    body: dict[str, Any] = json.loads(patch.content)
    return body


def get(client: Client, url: str = URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def snapshot(owners: tuple[str, ...] = (BOB,), event_id: str = DINNER) -> str:
    return EventSnapshot(STORY, event_id, "the dinner", owners).signed()


def post(client: Client, *, status: int = 200, **fields: Any) -> Any:
    data: dict[str, Any] = {
        "idempotency_key": KEY,
        "expected_revision": "12",
        "snapshot": snapshot(),
        "shared_owners_shown": "1",
        "shared_owners": [BOB],
        "planned": "",
    }
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    response = client.post(URL, data)
    assert response.status_code == status
    return response.content.decode() if status == 200 else response


def hidden(html: str, name: str) -> str | None:
    found = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)"', html)
    return unescape(found.group(1)) if found is not None else None


def field(html: str, name: str) -> str:
    found = re.search(
        rf'<div class="field field-{name}">(.*?)</div>\s*(?:<div class="field|<p><button)',
        html,
        re.S,
    )
    assert found is not None, name
    return found.group(1)


def checked(html: str) -> list[str]:
    return re.findall(
        r'<input type="checkbox" name="shared_owners" value="([^"]*)"[^>]*checked', html
    )


def checkbox_values(html: str) -> list[str]:
    return re.findall(r'<input type="checkbox" name="shared_owners" value="([^"]*)"', html)


def refusal(html: str) -> str:
    found = re.search(
        r'<p class="errorlist event-reschedule-refusal" role="alert">(.*?)</p>', html, re.S
    )
    assert found is not None
    return found.group(1)


def submit(html: str) -> str:
    found = re.search(r'<button type="submit" class="event-reschedule-submit"[^>]*>', html)
    assert found is not None
    return found.group(0)


def has_form(html: str) -> bool:
    return 'class="event-reschedule-form"' in html


def row_html(html: str, event_id: str) -> str:
    found = re.search(rf'<tr id="ev-{event_id}"[^>]*>(.*?)</tr>', html, re.S)
    assert found is not None, event_id
    return found.group(1)


def actions_cell(html: str, event_id: str) -> str:
    found = re.search(r'<td class="actions">(.*?)</td>', row_html(html, event_id), re.S)
    assert found is not None
    return found.group(1)


# --- the row actions -----------------------------------------------------------------------------


def test_actions_only_on_changeable_canon_rows(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("scheduled_events_statuses")
    engine.respond("GET", EVENTS, 200, json=body)
    html = get(operator_client, SECTION_URL)
    assert "<th>Actions</th>" in html
    for event in body["events"]:
        cell = actions_cell(html, event["event_id"])
        if event["status"] in ("cancelled", "completed"):
            assert cell == "", event["status"]
        else:
            reschedule = f"{SECTION_URL}{event['event_id']}/reschedule/"
            assert f'<a class="event-reschedule" href="{reschedule}">Reschedule</a>' in cell
            assert f'href="{SECTION_URL}{event["event_id"]}/cancel/?' in cell
            assert ">Cancel</a>" in cell


def test_belief_and_character_rows_have_no_actions(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EVENTS, 200, json=fixture("scheduled_events_alice"))
    html = get(operator_client, f"{SECTION_URL}?viewpoint=character:{ALICE}")
    assert actions_cell(html, MEETING) == ""  # a canon row in a character's view
    assert actions_cell(html, "01JM6EV0000000000000000004") == ""  # a belief row
    assert "/reschedule/" not in html
    assert "/cancel/" not in html


def test_actions_greyed_when_clock_off(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = get(operator_client, SECTION_URL)
    cell = actions_cell(html, DINNER)
    assert re.search(r'<span class="disabled event-reschedule" aria-disabled="true"[^>]*>', cell)
    assert re.search(r'<span class="disabled event-cancel" aria-disabled="true"[^>]*>', cell)
    assert f'aria-describedby="gate-ev-{DINNER}"' in cell
    assert CLOCK_OFF_REASON in cell
    assert "href=" not in cell
    assert "/reschedule/" not in html


def test_cancel_link_carries_revision_and_next(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EVENTS, 200, json=listing(revision=17))
    html = get(operator_client, SECTION_URL)
    found = re.search(r'<a class="event-cancel" href="([^"]*)">', actions_cell(html, DINNER))
    assert found is not None
    href = unescape(found.group(1))
    parts = urlsplit(href)
    assert parts.path == CANCEL_URL
    assert parse_qs(parts.query) == {
        "name": ["the dinner"],
        "expected_revision": ["17"],
        "next": [SECTION_URL],
    }
    # An unnamed event carries ``name=`` blank.
    engine.respond("GET", EVENTS, 200, json=listing(dinner(name=None)))
    html = get(operator_client, SECTION_URL)
    found = re.search(r'<a class="event-cancel" href="([^"]*)">', actions_cell(html, DINNER))
    assert found is not None
    assert parse_qs(urlsplit(unescape(found.group(1))).query, keep_blank_values=True)["name"] == [
        ""
    ]


# --- the reschedule page -------------------------------------------------------------------------


def test_get_reads_the_list_once_and_prefills(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EVENTS, 200, json=listing(revision=15))
    html = get(operator_client)
    assert [r.url.path for r in engine.requests] == [*SHELL_PATHS, EVENTS_PATH]
    (read,) = [r for r in engine.requests if r.url.path == EVENTS_PATH]
    assert read.url.params["viewpoint"] == "omniscient"
    assert "Reschedule the dinner</h3>" in html
    assert "<title>Inspect · Reschedule the dinner · " in html
    assert (
        f'<a class="active" aria-current="page" href="{SECTION_URL}">Scheduled events</a>' in html
    )
    # The event's row, no Actions.
    assert f'<tr id="ev-{DINNER}" class="canon">' in html
    assert "<th>Actions</th>" not in html
    assert hidden(html, "expected_revision") == "15"
    key = hidden(html, "idempotency_key")
    assert key is not None
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", key)
    snap = EventSnapshot.unsigned(hidden(html, "snapshot"), STORY, DINNER)
    assert snap == EventSnapshot(STORY, DINNER, "the dinner", (BOB,))
    assert checkbox_values(html) == [MARA, ALICE, BOB]
    assert checked(html) == [BOB]
    assert "Mara (player)" in field(html, "shared_owners")
    assert 'value=""' in field(html, "planned") or 'value="' not in field(html, "planned")
    assert escape(RESCHEDULE_HELP) in field(html, "planned")
    assert ">Reschedule</button>" in html
    assert "disabled" not in submit(html)
    assert "Back to Scheduled events" in html
    assert not writes(engine)


def test_outside_cast_owner_kept(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EVENTS, 200, json=listing(dinner(shared_owners=[BOB, OUTSIDER])))
    html = get(operator_client)
    assert checkbox_values(html) == [MARA, ALICE, BOB, OUTSIDER]
    assert checked(html) == [BOB, OUTSIDER]
    assert "…00000009 (not in the cast)" in field(html, "shared_owners")
    # Kept ticked, it sends nothing; a posted value neither in the cast nor kept is refused.
    response = post(
        operator_client,
        status=302,
        snapshot=snapshot((BOB, OUTSIDER)),
        shared_owners=[OUTSIDER, BOB],
    )
    assert response["Location"] == f"{SECTION_URL}#ev-{DINNER}"
    html = post(operator_client, shared_owners=[BOB, OUTSIDER])
    assert escape(UNKNOWN_CHARACTER) in field(html, "shared_owners")
    assert patches(engine) == []


def test_unlisted_and_settled_events_have_no_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    unlisted = "01JM6EV0000000000000000077"
    html = get(operator_client, f"{SECTION_URL}{unlisted}/reschedule/")
    assert NOT_LISTED in html
    assert not has_form(html)
    html = get(operator_client, f"{SECTION_URL}{CARDS}/reschedule/")
    assert SETTLED_CANCELLED in unescape(html)
    assert not has_form(html)
    engine.respond("GET", EVENTS, 200, json=listing(dinner(status="completed")))
    html = get(operator_client)
    assert "This event is already completed — it can't be changed" in unescape(html)
    assert not has_form(html)
    # A belief's id isn't an event of the story.
    engine.respond("GET", EVENTS, 200, json=listing(dinner(label="belief", status=None)))
    html = get(operator_client)
    assert NOT_LISTED in html
    # Complement: zero PATCH.
    assert patches(engine) == []


def test_get_read_failures(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EVENTS, 404, json=error_json("not_found"))
    html = get(operator_client)
    assert NOT_FOUND in html
    assert not has_form(html)
    engine.fail("GET", EVENTS, httpx.ConnectError("down"))
    html = get(operator_client)
    assert "engine-error" in html
    assert not has_form(html)


def test_get_cast_unavailable(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", STORY_PATH, httpx.ConnectError("down"))
    html = get(operator_client)
    assert CAST_UNAVAILABLE in html
    assert 'type="checkbox"' not in html
    assert hidden(html, "shared_owners_shown") is None
    # Only planned can be sent.
    data = {
        "idempotency_key": KEY,
        "expected_revision": "12",
        "snapshot": snapshot(),
        "planned": "1200",
    }
    operator_client.post(URL, data)
    assert sent(engine) == {"planned": 1200}


def test_gated_get_greys_the_submit(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = get(operator_client)
    assert has_form(html)
    assert f'<tr id="ev-{DINNER}"' in html
    assert "disabled" in submit(html)
    assert CLOCK_OFF_REASON in html


# --- the reschedule POST -------------------------------------------------------------------------


def test_unchanged_form_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    response = post(operator_client, status=302)
    assert response["Location"] == f"{SECTION_URL}#ev-{DINNER}"
    page = operator_client.get(response["Location"]).content.decode()
    assert NO_CHANGES in page
    # Complement: zero PATCH; the shared owners re-ticked in another order send nothing (C9).
    post(
        operator_client,
        status=302,
        snapshot=snapshot((BOB, ALICE)),
        shared_owners=[ALICE, BOB],
    )
    assert patches(engine) == []


def test_planned_only(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, planned="1200")
    assert sent(engine) == {"planned": 1200}


def test_time_of_day(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, planned="07:30")
    assert sent(engine) == {"planned": "07:30"}


def test_shared_owners_only(operator_client: Client, engine: FakeEngine) -> None:
    # Snapshot order kept (Bob, Alice), the new one (Mara) appended in cast order.
    post(operator_client, snapshot=snapshot((BOB, ALICE)), shared_owners=[MARA, ALICE, BOB])
    assert sent(engine) == {"shared_owners": [BOB, ALICE, MARA]}


def test_new_owners_appended_in_cast_order(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, shared_owners=[ALICE, BOB, MARA])
    assert sent(engine) == {"shared_owners": [BOB, MARA, ALICE]}


def test_untick_all_sends_empty_list(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, shared_owners=None, planned="1200")
    assert sent(engine) == {"planned": 1200, "shared_owners": []}


def test_headers(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    key = hidden(html, "idempotency_key")
    post(
        operator_client,
        planned="1200",
        idempotency_key=key,
        expected_revision=hidden(html, "expected_revision"),
        snapshot=hidden(html, "snapshot"),
    )
    (patch,) = patches(engine)
    assert patch.headers["X-Epistrel-Expected-Revision"] == "12"
    assert patch.headers["Idempotency-Key"] == key


@pytest.mark.parametrize("planned", ["24:00", "x", "-5", "7:30"])
def test_invalid_planned_no_call(operator_client: Client, engine: FakeEngine, planned: str) -> None:
    html = post(operator_client, planned=planned, expected_revision="9")
    assert escape(PLANNED_INVALID) in field(html, "planned")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "9"
    assert f'<tr id="ev-{DINNER}"' not in html  # re-renders leave out the row
    assert patches(engine) == []


def test_too_many_shared_owners() -> None:
    from django.http import QueryDict

    from console.engine.models import Story
    from console.forms.scheduled_events import RescheduleForm

    story = fixture("story")
    template = story["characters"][1]
    chars = [
        {**template, "id": f"01JM6CH00000000000000001{i:02d}", "name": f"C{i}"} for i in range(21)
    ]
    parsed = Story.from_json({**story, "characters": chars})
    assert parsed is not None
    ids = [c.id for c in parsed.cast]
    query = QueryDict(mutable=True)
    query["shared_owners_shown"] = "1"
    query.setlist("shared_owners", ids)
    form = RescheduleForm(query, cast=list(parsed.cast), current=[])
    assert not form.is_valid()
    assert form.errors["shared_owners"] == [TOO_MANY_CHARACTERS]


def test_gated_post_makes_no_write(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = post(operator_client, planned="1200", shared_owners=None, expected_revision="9")
    assert has_form(html)
    assert "disabled" in submit(html)
    assert CLOCK_OFF_REASON in html
    assert 'value="1200"' in field(html, "planned")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "9"
    assert f'<tr id="ev-{DINNER}"' not in html
    # Complement: zero PATCH, and no events read either.
    assert patches(engine) == []
    assert [r.url.path for r in engine.requests] == SHELL_PATHS


@pytest.mark.parametrize(
    "bad",
    ["", "garbage", snapshot(event_id=MEETING), "absent"],
    ids=["blank", "bad", "foreign", "absent"],
)
def test_bad_snapshot_rereads_once(operator_client: Client, engine: FakeEngine, bad: str) -> None:
    post(operator_client, planned="1200", snapshot=None if bad == "absent" else bad)
    reads = [r for r in engine.requests if r.method == "GET" and r.url.path == EVENTS_PATH]
    assert len(reads) == 1
    assert reads[0].url.params["viewpoint"] == "omniscient"
    assert sent(engine) == {"planned": 1200}


def test_bad_snapshot_reread_finds_it_settled(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EVENTS, 200, json=listing(dinner(status="cancelled")))
    html = post(operator_client, planned="1200", snapshot="garbage")
    assert SETTLED_CANCELLED in unescape(html)
    assert not has_form(html)
    engine.respond("GET", EVENTS, 200, json=listing(dinner(event_id=MEETING.replace("1", "8"))))
    html = post(operator_client, planned="1200", snapshot="garbage")
    assert NOT_LISTED in html
    assert patches(engine) == []


def test_cast_unavailable_on_post_refuses_changed_owners(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("GET", STORY_PATH, httpx.ConnectError("down"))
    html = post(operator_client, planned="1200", shared_owners=[ALICE])
    assert SHARED_UNCHECKED in unescape(html)
    assert CAST_UNAVAILABLE in html
    assert patches(engine) == []
    # An unchanged set lets a Planned-only PATCH through.
    post(operator_client, planned="1200")
    assert sent(engine) == {"planned": 1200}


def written_lines(html: str) -> list[str]:
    found = re.search(r'<ul class="event-written-lines">(.*?)</ul>', html, re.S)
    assert found is not None
    return [
        unescape(re.sub(r"<[^>]+>", "", item)).strip()
        for item in re.findall(r"<li[^>]*>(.*?)</li>", found.group(1), re.S)
    ]


def test_result_page_rescheduled(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, planned="1200", shared_owners=None)
    row = EngineOperation.objects.get()
    assert row.kind == "reschedule_scheduled_event"
    assert "Rescheduled: the dinner</h3>" in html
    assert "<title>Inspect · Rescheduled: the dinner · " in html
    cells = row_html(html, DINNER)
    assert ">+20h 00m</span>" in cells
    assert ">Pending</span>" in cells
    assert '<td class="shared">—</td>' in cells
    assert written_lines(html) == [
        "Operation: 01JM6QP0000000000000000042",
        "Story revision now 13",
    ]
    back = f'<a class="events-back" href="{SECTION_URL}#ev-{DINNER}">'
    assert f"{back}Back to Scheduled events</a>" in html
    # Again from the #44 row.
    engine.requests.clear()
    again = get(operator_client, f"{WRITTEN_URL}?report={row.pk}")
    assert "Rescheduled: the dinner</h3>" in again
    assert [r.url.path for r in engine.requests] == SHELL_PATHS


# --- reschedule refusals -------------------------------------------------------------------------


def refuse(
    engine: FakeEngine,
    http_status: int,
    code: str,
    message: str = "refused",
    headers: dict[str, str] | None = None,
    **details: Any,
) -> None:
    engine.respond(
        "PATCH", EVENT, http_status, json=error_json(code, message, **details), headers=headers
    )


def refused_page(client: Client, **fields: Any) -> str:
    data: dict[str, Any] = {"planned": "07:30", "shared_owners": [ALICE], "expected_revision": "9"}
    data.update(fields)
    html: str = post(client, **data)
    assert has_form(html)
    assert 'value="07:30"' in field(html, "planned")
    assert checked(html) == [ALICE]
    # D8: a fresh key and the POST's shell revision.
    assert hidden(html, "idempotency_key") != KEY
    assert hidden(html, "expected_revision") == "12"
    assert "Rescheduled:" not in html
    return html


def test_event_settled_removes_form(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        422,
        "validation_error",
        field="event_id",
        reason="event_settled",
        status="cancelled",
    )
    html = post(operator_client, planned="1200")
    assert SETTLED_CANCELLED in unescape(html)
    assert not has_form(html)
    assert len(patches(engine)) == 1


def test_404_not_found(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "not_found")
    html = post(operator_client, planned="1200")
    assert NOT_FOUND in html
    assert not has_form(html)


def test_clock_unanchored_beside_planned(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "clock_unanchored", to="07:30")
    html = refused_page(operator_client)
    assert escape(UNANCHORED) in field(html, "planned")
    assert "event-reschedule-refusal" not in html


def test_unknown_shared_owner(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "viewpoint_unresolved", field="body.shared_owners")
    html = refused_page(operator_client)
    assert escape(UNKNOWN_CHARACTER) in field(html, "shared_owners")


def test_module_off_is_a_form_refusal(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "module_off", module="clock")
    html = refused_page(operator_client)
    assert "<code>clock.level</code>" in refusal(html)
    assert f'<a href="/stories/{STORY}/config/">Config</a>' in refusal(html)
    # D8: no re-grey, no extra read.
    assert "disabled" not in submit(html)
    assert [r.url.path for r in engine.requests] == [*SHELL_PATHS, EVENT_PATH]


def test_revision_conflict(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "revision_conflict", current_revision=14)
    html = refused_page(operator_client)
    assert "changed" in refusal(html)
    (patch,) = patches(engine)
    assert patch.headers["X-Epistrel-Expected-Revision"] == "9"


def test_story_busy_retry_after(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 423, "story_busy", headers={"Retry-After": "4"}, reason="in_flight")
    html = refused_page(operator_client)
    assert refusal(html) == "Story busy — another turn is running · retry after 4 s"


def test_other_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "idempotency_conflict")
    html = refused_page(operator_client)
    assert "engine-error" in html.split('class="event-reschedule-form"')[0]


def test_transport_error_keeps_key_and_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("PATCH", EVENT, httpx.ConnectError("down"))
    html = post(operator_client, planned="1200", expected_revision="9")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "9"
    assert "engine-error" in html.split('class="event-reschedule-form"')[0]
    assert "Rescheduled:" not in html


# --- the cancel confirm page ---------------------------------------------------------------------

QUERY = {"name": "the dinner", "expected_revision": "12", "next": SECTION_URL}


def cancel_get(client: Client, query: dict[str, str] | None = None) -> str:
    response = client.get(f"{CANCEL_URL}?{urlencode(QUERY if query is None else query)}")
    assert response.status_code == 200
    return response.content.decode()


def cancel_post(client: Client, url: str = CANCEL_URL, **fields: Any) -> Any:
    data: dict[str, Any] = {"idempotency_key": KEY, "confirmed": "1", **QUERY}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    return client.post(url, data)


def confirm_error(html: str) -> str:
    found = re.search(r'<p class="errorlist confirm-error">(.*?)</p>', html, re.S)
    assert found is not None
    return found.group(1)


def test_confirm_get_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = cancel_get(operator_client)
    assert engine.requests == []
    assert "Cancel a scheduled event" in html
    assert "Cancel the scheduled event <em>the dinner</em>?" in html
    assert "It becomes cancelled and never stops the clock." in html
    second = (
        "A cancelled event can't be rescheduled; rewinding the story to before this slot undoes it."
    )
    assert escape(second) in html
    assert f"<dt>Event id</dt><dd><code>{DINNER}</code></dd>" in html
    assert f"<dt>Story id</dt><dd><code>{STORY}</code></dd>" in html
    assert hidden(html, "expected_revision") == "12"
    assert hidden(html, "name") == "the dinner"
    assert hidden(html, "confirmed") == "1"
    assert re.search(r'<button type="submit" class="danger">Cancel the event</button>', html)
    assert f'href="{SECTION_URL}"' in html
    # A blank name reads "this event".
    html = cancel_get(operator_client, {"name": "", "expected_revision": "12"})
    assert "Cancel the scheduled event <em>this event</em>?" in html
    assert engine.requests == []


def test_unconfirmed_post_rerenders_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = cancel_post(operator_client, confirmed=None)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Cancel the scheduled event <em>the dinner</em>?" in html
    assert hidden(html, "idempotency_key") == KEY
    # Complement: zero DELETE.
    assert deletes(engine) == []
    assert engine.requests == []


def test_confirmed_post_cancels_once(operator_client: Client, engine: FakeEngine) -> None:
    response = cancel_post(operator_client)
    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("GET", CONFIG_PATH),
        ("DELETE", EVENT_PATH),
    ]
    (delete,) = deletes(engine)
    assert delete.headers["X-Epistrel-Expected-Revision"] == "12"
    assert delete.headers["Idempotency-Key"] == KEY
    row = EngineOperation.objects.get()
    assert row.kind == "cancel_scheduled_event"
    assert response.status_code == 302
    assert response["Location"] == f"{WRITTEN_URL}?report={row.pk}"
    html = get(operator_client, response["Location"])
    assert "Cancelled: the dinner</h3>" in html
    assert ">Cancelled</span>" in row_html(html, DINNER)


def test_cancel_without_operation_row_flashes(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from console import operation_log

    monkeypatch.setattr(operation_log, "record", lambda *a, **k: None)
    response = cancel_post(operator_client)
    assert response.status_code == 302
    assert response["Location"] == SECTION_URL
    page = operator_client.get(SECTION_URL).content.decode()
    assert (
        "Cancelled “the dinner” — operation 01JM6QP0000000000000000043, story revision now 13"
        in unescape(page)
    )


def test_event_id_from_url_not_display(operator_client: Client, engine: FakeEngine) -> None:
    cancel_post(operator_client, name="The harbourmaster's meeting", event_id=MEETING)
    (delete,) = deletes(engine)
    assert delete.url.path == EVENT_PATH


@pytest.mark.parametrize("case", ["lite", "unread"])
def test_gated_confirm_makes_no_write(
    operator_client: Client, engine: FakeEngine, case: str
) -> None:
    if case == "lite":
        engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    else:
        engine.fail("GET", EFFECTIVE, httpx.ConnectError("down"))
    response = cancel_post(operator_client)
    assert response.status_code == 200
    html = response.content.decode()
    if case == "lite":
        assert CLOCK_OFF_REASON in confirm_error(html)
        assert f'<a href="/stories/{STORY}/config/">Config</a>' in confirm_error(html)
    else:
        assert GATE_UNREAD in unescape(re.sub(r"</?code>", "`", confirm_error(html)))
    assert hidden(html, "idempotency_key") == KEY  # nothing was sent
    # Complement: zero DELETE.
    assert deletes(engine) == []


@pytest.mark.parametrize(
    ("status", "code", "details", "headers", "expected"),
    [
        (
            422,
            "validation_error",
            {"field": "event_id", "reason": "event_settled", "status": "cancelled"},
            None,
            SETTLED_CANCELLED,
        ),
        (404, "not_found", {}, None, NOT_FOUND),
        (409, "module_off", {"module": "clock"}, None, "<code>clock.level</code>"),
        (409, "revision_conflict", {"current_revision": 14}, None, "changed"),
        (
            423,
            "story_busy",
            {"reason": "in_flight"},
            {"Retry-After": "4"},
            "Story busy — another turn is running · retry after 4 s",
        ),
    ],
    ids=["event_settled", "404", "module_off", "revision_conflict", "story_busy"],
)
def test_cancel_refusals_on_page(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
    headers: dict[str, str] | None,
    expected: str,
) -> None:
    engine.respond("DELETE", EVENT, status, json=error_json(code, **details), headers=headers)
    response = cancel_post(operator_client)
    # Complement: no redirect, no success flash.
    assert response.status_code == 200
    html = response.content.decode()
    assert expected in unescape(confirm_error(html)) or expected in confirm_error(html)
    assert hidden(html, "idempotency_key") != KEY
    assert hidden(html, "expected_revision") == "12"
    assert "Cancelled “" not in unescape(html)
    if code == "module_off":
        assert f'<a href="/stories/{STORY}/config/">Config</a>' in confirm_error(html)
    assert EngineOperation.objects.filter(outcome=EngineOperation.Outcome.OK).count() == 0


def test_cancel_transport_error_keeps_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("DELETE", EVENT, httpx.ConnectError("down"))
    response = cancel_post(operator_client)
    assert response.status_code == 200
    html = response.content.decode()
    assert "engine-error" in html
    assert hidden(html, "idempotency_key") == KEY


def test_next_offsite_falls_back(operator_client: Client, engine: FakeEngine) -> None:
    html = cancel_get(operator_client, {**QUERY, "next": "https://evil.example/"})
    assert f'<a href="{SECTION_URL}">Cancel</a>' in html
    assert 'name="next" value="https://evil.example/"' not in html
    assert engine.requests == []

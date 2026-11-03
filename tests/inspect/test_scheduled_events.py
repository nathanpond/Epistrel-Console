"""The World tab's Scheduled events section (#244).

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``: the player Mara, Alice and Bob). ``m6/scheduled_events.json``
is the omniscient list, ``m6/scheduled_events_alice.json`` Alice's (her canon event, then her
belief-only "The ferry leaves"), keyed on ``viewpoint`` (C14). This story adds
``m6/scheduled_events_statuses.json``: one canon event per status, plus ``postponed``."""

import copy
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, override_settings
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine.models import ScheduledEvent
from console.engine_access import engine_client_for
from console.forms.inspect import VIEWPOINT_NOT_IN_CAST
from console.inspect import CLOCK, PARTITIONS, SCHEDULED_STOPS, m5_gate, m6_gate
from console.scheduled_events import BELIEF_NEVER_STOPS, event_row
from console.templatetags.inspect_tags import code_spans
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
BOB = "01JM6CH0000000000000000003"
OUTSIDER = "01JM6CH0000000000000000099"
ALICE_VP = f"character:{ALICE}"
BOB_VP = f"character:{BOB}"
URL = f"/stories/{STORY}/inspect/world/scheduled-events/"
EVENTS = "/stories/{story_id}/scheduled-events"
CLOCK_ROUTE = "/stories/{story_id}/clock"
EVENTS_PATH = f"/stories/{STORY}/scheduled-events"
CLOCK_PATH = f"/stories/{STORY}/clock"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
NEVER = "Never — a belief doesn&#x27;t stop the clock"


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
    fake.respond(
        "GET", EVENTS, 200, json=fixture("scheduled_events_alice"), query={"viewpoint": ALICE_VP}
    )
    fake.respond("GET", CLOCK_ROUTE, 200, json=fixture("clock"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def page(client: Client, url: str = URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def events_reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.url.path == EVENTS_PATH]


def clock_reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.url.path == CLOCK_PATH]


def event_id(n: int) -> str:
    return f"01JM6EV{n:019d}"


def row(html: str, eid: str) -> str:
    found = re.search(rf'<tr id="ev-{eid}" class="[a-z]+">(.*?)</tr>', html, re.S)
    assert found is not None, eid
    return found.group(1)


def row_ids(html: str) -> list[str]:
    return re.findall(r'<tr id="ev-([0-9A-Z]+)"', html)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def field_errors(html: str, field: str) -> str:
    found = re.search(rf'<div class="field field-{field}">(.*?)</div>', html, re.S)
    assert found is not None
    return found.group(1)


def statuses(engine: FakeEngine) -> None:
    engine.respond("GET", EVENTS, 200, json=fixture("scheduled_events_statuses"))


# --- reads and the picker ------------------------------------------------------------------------


def test_default_viewpoint_is_omniscient(operator_client: Client, engine: FakeEngine) -> None:
    story = fixture("story")
    story["settings"]["default_output_viewpoint"] = ALICE_VP
    engine.respond("GET", STORY_PATH, 200, json=story)
    html = page(operator_client)
    (read,) = events_reads(engine)
    assert dict(read.url.params) == {"viewpoint": "omniscient"}
    assert '<option value="omniscient" selected>Omniscient</option>' in html
    assert "Mara (player)</option>" in html
    assert '<p class="viewpoint-line">Viewpoint: Omniscient — every canon event</p>' in html
    assert "<h2>Scheduled events</h2>" in html
    assert f'aria-current="page" href="{URL}">Scheduled events' in html


def test_character_viewpoint_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"{URL}?viewpoint={ALICE_VP}")
    (read,) = events_reads(engine)
    assert read.url.params["viewpoint"] == ALICE_VP
    assert (
        "Viewpoint: Alice — only events Alice takes part in, and Alice&#x27;s own belief-only "
        "schedules" in html
    )


def test_read_order(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client)
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        EVENTS_PATH,
        CLOCK_PATH,
    ]


def test_viewpoint_outside_cast_no_read(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"{URL}?viewpoint=character:{OUTSIDER}")
    assert escape(VIEWPOINT_NOT_IN_CAST) in field_errors(html, "viewpoint")
    assert events_reads(engine) == []  # **Complement:** no events read
    assert clock_reads(engine) == []  # and no clock read
    assert "events-count" not in html


def test_blank_viewpoint_is_omniscient(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client, f"{URL}?viewpoint=")
    (read,) = events_reads(engine)
    assert read.url.params["viewpoint"] == "omniscient"


def test_viewpoint_unresolved_on_field(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        EVENTS,
        422,
        json=error_json("viewpoint_unresolved", "no such character"),
        query={"viewpoint": ALICE_VP},
    )
    html = page(operator_client, f"{URL}?viewpoint={ALICE_VP}")
    assert escape(VIEWPOINT_NOT_IN_CAST) in field_errors(html, "viewpoint")
    assert "<table" not in html
    assert clock_reads(engine) == []
    assert 'class="viewpoint-line"' not in html


# --- rows ----------------------------------------------------------------------------------------


def test_rows_render_each_column(operator_client: Client, engine: FakeEngine) -> None:
    statuses(engine)
    html = page(operator_client)
    expected = {
        11: "Pending",
        12: "Possibly due",
        13: "Due",
        14: "Surfaced",
        15: "Completed",
        16: "Missed",
        17: "Cancelled",
        18: "Postponed",
    }
    for n, text in expected.items():
        raw = cell(row(html, event_id(n)), "status")
        assert raw.endswith(f">{text}</span>"), (n, raw)
    assert cell(row(html, event_id(18)), "status") == '<span title="postponed">Postponed</span>'
    pending = row(html, event_id(11))
    assert cell(pending, "event") == "The dawn bell"
    assert cell(pending, "kind") == '<span title="canon">Canon</span>'
    assert cell(pending, "planned") == '<span title="offset 360 min">+06h 00m</span>'
    assert cell(pending, "owner") == "Author"
    assert cell(pending, "shared") == "—"
    assert cell(pending, "participants") == "Alice"
    assert cell(pending, "place") == "—"
    assert cell(pending, "grace") == "30 min"
    assert cell(pending, "stopped") == "—"
    assert cell(pending, "confidence") == "—"
    market = row(html, event_id(12))
    assert cell(market, "owner") == "World"
    assert cell(market, "participants") == "Mara (player), Bob"
    assert cell(market, "grace") == "0 min"
    delivery = row(html, event_id(13))
    assert cell(delivery, "owner") == "Alice"
    assert cell(delivery, "shared") == "Bob"
    place = "01JM6EN0000000000000000002"
    assert cell(delivery, "place") == (
        f'<a href="/stories/{STORY}/inspect/entities/{place}/">'
        f'<code title="{place}">…00000002</code></a>'
    )
    dinner = row(html, event_id(14))
    assert cell(dinner, "stopped") == '<span title="offset 1140 min">at +19h 00m</span>'
    assert cell(row(html, event_id(15)), "event") == '<span class="muted">(unnamed)</span>'
    more = cell(delivery, "more")
    assert f"<dt>Event id</dt><dd><code>{event_id(13)}</code></dd>" in more
    assert f"<dt>Owner</dt><dd><code>{ALICE}</code></dd>" in more
    assert f"<dt>Participants</dt><dd><code>{ALICE}, {BOB}</code></dd>" in more
    assert f"<dt>Shared with</dt><dd><code>{BOB}</code></dd>" in more
    assert "<dt>Shared with</dt><dd>—</dd>" in cell(pending, "more")
    assert '<p class="events-count">8 events · revision 14</p>' in html


def test_unknown_owner_shows_raw_code(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("scheduled_events")
    body["events"][0]["owner"] = "narrator"
    body["events"][1]["owner"] = OUTSIDER
    engine.respond("GET", EVENTS, 200, json=body)
    html = page(operator_client)
    assert cell(row(html, event_id(2)), "owner") == "<code>narrator</code>"
    assert cell(row(html, event_id(1)), "owner") == f"<code>{OUTSIDER}</code>"


def test_column_order(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    head = re.search(r'<table class="scheduled-events">\s*<thead><tr>(.*?)</tr>', html, re.S)
    assert head is not None
    assert re.findall(r"<th>(.*?)</th>", head.group(1)) == [
        "Event",
        "Kind",
        "Status",
        "Planned",
        "Owner",
        "Shared with",
        "Participants",
        "Place",
        "Grace",
        "Stopped the clock",
        "Confidence",
        "Actions",
        "More",
    ]
    cells = re.findall(r'<td class="([a-z]+)">', row(html, event_id(1)))
    assert cells == [
        "event",
        "kind",
        "status",
        "planned",
        "owner",
        "shared",
        "participants",
        "place",
        "grace",
        "stopped",
        "confidence",
        "actions",
        "more",
    ]


def test_actions_hook_renders_a_column() -> None:
    raw = fixture("scheduled_events")["events"][1]
    event = ScheduledEvent.from_json(raw)
    assert event is not None
    built = event_row(event, {}, STORY)
    with override_settings(ROOT_URLCONF="epistrel_console.urls"):
        html = render_to_string(
            "console/inspect/_scheduled_event_row.html",
            {
                "row": built,
                "story_id": STORY,
                "actions": {built.event_id: object()},
                "actions_template": "console/inspect/_unavailable.html",
            },
        )
    assert re.findall(r'<td class="([a-z]+)">', html)[-2:] == ["actions", "more"]


def test_belief_row(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"{URL}?viewpoint={ALICE_VP}")
    assert f'<tr id="ev-{event_id(4)}" class="belief">' in html
    belief = row(html, event_id(4))
    assert cell(belief, "kind") == '<span title="belief">Belief</span>'
    assert cell(belief, "status") == "—"
    assert cell(belief, "owner") == "—"
    assert cell(belief, "participants") == "—"
    assert cell(belief, "grace") == "—"
    assert cell(belief, "confidence") == '<span title="believed">Believed</span>'
    assert cell(belief, "stopped") == NEVER
    more = cell(belief, "more")
    assert f"<dt>Event id</dt><dd><code>{event_id(4)}</code></dd>" in more
    assert "<dt>Owner</dt><dd>—</dd>" in more
    assert "<dt>Participants</dt><dd>—</dd>" in more
    # **Complement:** no belief row ever renders an "at +…" stop
    assert "at +" not in cell(belief, "stopped")
    canon = row(html, event_id(1))
    assert cell(canon, "stopped") == "—"
    assert cell(canon, "confidence") == "—"


def test_belief_never_stops_whatever_the_row_carries() -> None:
    """The rule is on the label, not the value: a belief with ``stopped_clock_at`` 120 still
    reads "Never …" (the Engine never sends this shape, so no fixture carries it)."""
    raw = fixture("scheduled_events_alice")["events"][1]
    raw["stopped_clock_at"] = 120
    event = ScheduledEvent.from_json(raw)
    assert event is not None
    built = event_row(event, {}, STORY)
    assert built.stopped_text == BELIEF_NEVER_STOPS
    with override_settings(ROOT_URLCONF="epistrel_console.urls"):
        html = render_to_string(
            "console/inspect/_scheduled_event_row.html",
            {"row": built, "story_id": STORY, "actions": None},
        )
    assert cell(html, "stopped") == NEVER
    assert "+02h 00m" not in html
    # **Complement:** the same value on a canon row does read as a stop
    raw["label"] = "canon"
    canon = ScheduledEvent.from_json(raw)
    assert canon is not None
    assert event_row(canon, {}, STORY).stopped_text == "at +02h 00m"


def test_unknown_label_follows_canon_rules() -> None:
    raw = fixture("scheduled_events")["events"][0]
    raw["label"] = "rumour"
    event = ScheduledEvent.from_json(raw)
    assert event is not None
    built = event_row(event, {}, STORY)
    assert built.row_class == "other"
    assert built.kind == ("Rumour", "rumour")
    assert built.stopped_text == "at +01h 00m"


def test_engine_order_kept(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"{URL}?viewpoint={ALICE_VP}")
    assert row_ids(html) == [event_id(1), event_id(4)]  # canon first, as sent
    html = page(operator_client)
    assert row_ids(html) == [event_id(2), event_id(1), event_id(3)]


def test_malformed_event_kept_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("scheduled_events_bad_item")
    engine.side_effect("GET", EVENTS, lambda request: httpx.Response(200, json=body))
    html = page(operator_client)
    assert row_ids(html) == [event_id(1)]
    notice = re.search(r'<div class="notice events-unparsed">(.*?)</div>', html, re.S)
    assert notice is not None
    assert "<p>1 event couldn&#x27;t be read</p>" in notice.group(1)
    assert notice.group(1).count('<pre class="raw-item">') == 1
    assert "The morning tide" in notice.group(1)
    assert "The harbourmaster" not in notice.group(1)  # only the rejected entry
    assert '<p class="events-count">1 event · revision 12</p>' in html
    assert "events-none" not in html


def test_every_event_malformed_shows_only_the_notice(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("scheduled_events_bad_item")
    body["events"] = [body["events"][1], body["events"][1]]
    engine.side_effect("GET", EVENTS, lambda request: httpx.Response(200, json=body))
    html = page(operator_client)
    assert "2 events couldn&#x27;t be read" in html
    assert "No scheduled events" not in html
    assert "<table" not in html


# --- header lines --------------------------------------------------------------------------------


def test_now_and_stops_lines(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert (
        '<p class="events-now">Now: <span class="clock" title="offset 130 min">+02h 10m (Saturday'
        in html
    )
    assert '<p class="events-stops">Due events stop the clock</p>' in html
    assert "events-clock-off" not in html
    assert (
        html.index('class="viewpoint-line"') < html.index("events-now") < html.index("events-count")
    )


def test_stops_off_by_profile(operator_client: Client, engine: FakeEngine) -> None:
    effective = structured_v3(clock__scheduled_stops=False)
    engine.respond("GET", EFFECTIVE, 200, json=effective)
    html = page(operator_client)
    closed = m6_gate(SCHEDULED_STOPS, effective["effective"])
    assert closed is not None
    assert (
        f'<p class="events-stops">Due events don&#x27;t stop the clock: '
        f"{code_spans(closed.reason)}</p>" in html
    )
    assert "Stop at due events setting" not in html


def test_stops_off_by_setting(operator_client: Client, engine: FakeEngine) -> None:
    clock = fixture("clock")
    clock["settings"]["stop_at_due_events"] = False
    engine.respond("GET", CLOCK_ROUTE, 200, json=clock)
    html = page(operator_client)
    settings = f"/stories/{STORY}/inspect/world/clock/settings/"
    assert (
        '<p class="events-stops">Due events don&#x27;t stop the clock: the clock&#x27;s '
        f'<a href="{settings}">Stop at due events setting</a> is off</p>' in html
    )


def test_stops_off_by_profile_and_setting_gives_the_reason_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=structured_v3(clock__scheduled_stops=False))
    clock = fixture("clock")
    clock["settings"]["stop_at_due_events"] = False
    engine.respond("GET", CLOCK_ROUTE, 200, json=clock)
    html = page(operator_client)
    assert "Stop at due events setting" not in html
    assert "Due events don&#x27;t stop the clock: Needs" in html


def test_clock_read_failure_keeps_the_list(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK_ROUTE, 503, json=error_json("engine_unavailable", "down"))
    html = page(operator_client)
    assert '<p class="notice events-clock-unread">Couldn&#x27;t read the clock</p>' in html
    assert "events-now" not in html
    assert "events-stops" not in html
    assert row_ids(html) == [event_id(2), event_id(1), event_id(3)]
    assert 'class="engine-error' not in html


def test_clock_off_line_but_list_served(operator_client: Client, engine: FakeEngine) -> None:
    effective = lite_m6()
    engine.respond("GET", EFFECTIVE, 200, json=effective)
    html = page(operator_client)
    assert len(events_reads(engine)) == 1  # ungated (D1)
    closed = m6_gate(CLOCK, effective["effective"])
    assert closed is not None
    assert (
        '<p class="notice events-clock-off">The clock is off under this story&#x27;s '
        f"configuration: {code_spans(closed.reason)} — events are listed, but none comes due</p>"
        in html
    )
    assert "events-stops" not in html  # the off line says it once
    assert "events-now" in html  # the Now line shows whenever the clock read answered
    assert html.index("events-now") < html.index("events-clock-off")
    assert row_ids(html) == [event_id(2), event_id(1), event_id(3)]


def test_clock_off_line_kept_when_clock_read_fails(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    engine.respond("GET", CLOCK_ROUTE, 503, json=error_json("engine_unavailable", "down"))
    html = page(operator_client)
    assert "events-clock-off" in html
    assert "Couldn&#x27;t read the clock" in html


def test_partitions_note_for_character(operator_client: Client, engine: FakeEngine) -> None:
    effective = structured_v3(character_knowledge__partitions=False)
    engine.respond("GET", EFFECTIVE, 200, json=effective)
    html = page(operator_client, f"{URL}?viewpoint={ALICE_VP}")
    closed = m5_gate(PARTITIONS, effective["effective"])
    assert closed is not None
    assert (
        '<p class="notice events-partitions">Belief-only schedules aren&#x27;t listed: '
        f"{code_spans(closed.reason)}</p>" in html
    )
    # **Complement:** no note under omniscient
    assert "events-partitions" not in page(operator_client)


def test_no_partitions_note_when_partitions_on(operator_client: Client, engine: FakeEngine) -> None:
    assert "events-partitions" not in page(operator_client, f"{URL}?viewpoint={ALICE_VP}")


# --- empty states and errors ---------------------------------------------------------------------


def _empty(engine: FakeEngine, viewpoint: str) -> None:
    body = fixture("scheduled_events")
    body["events"] = []
    body["output_viewpoint"] = viewpoint
    engine.respond("GET", EVENTS, 200, json=body, query={"viewpoint": viewpoint})


def test_empty_omniscient(operator_client: Client, engine: FakeEngine) -> None:
    _empty(engine, "omniscient")
    html = page(operator_client)
    assert '<p class="none events-none">No scheduled events</p>' in html
    assert '<p class="events-count">0 events · revision 12</p>' in html
    assert "<table" not in html


def test_empty_character(operator_client: Client, engine: FakeEngine) -> None:
    _empty(engine, BOB_VP)
    html = page(operator_client, f"{URL}?viewpoint={BOB_VP}")
    assert (
        '<p class="none events-none">Bob takes part in no scheduled event and holds no '
        "belief-only schedule</p>" in html
    )


def test_404_text(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EVENTS, 404, json=error_json("not_found", "no story", story_id=STORY))
    html = page(operator_client)
    assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    assert '<form method="get" id="events-viewpoint">' in html
    assert clock_reads(engine) == []
    assert "events-count" not in html


def test_503_panel_keeps_picker(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EVENTS, 503, json=error_json("engine_unavailable", "down"))
    html = page(operator_client)
    form = html.index('<form method="get" id="events-viewpoint">')
    panel = html.index('<div class="engine-error')
    assert form < panel
    assert "<title>Inspect · World · Harbour Ledger</title>" in html
    assert clock_reads(engine) == []
    assert 'class="viewpoint-line"' not in html

"""The World tab's Clock section (#243).

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast. ``m6/clock.json`` is anchored (Saturday morning, hour 5 … 11, at offset 130,
established at turn 3 · beat 2), ``m6/clock_unanchored.json`` unanchored; this story adds
``m6/clock_external.json`` (``bank`` · ``story_minutes``) and ``m6/clock_custom_calendar.json``
(three months, five weekdays, ``day_minutes: 600``)."""

import copy
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.clock import anchor_view, component_cell, settings_rows
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
URL = f"/stories/{STORY}/inspect/world/clock/"
CLOCK = "/stories/{story_id}/clock"
CLOCK_PATH = f"/stories/{STORY}/clock"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
CLOCK_OFF_REASON = (
    "Needs <code>clock.level</code> at <code>prompt_only</code> or above (this story runs "
    "<code>off</code>)"
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
    fake.respond("GET", CLOCK, 200, json=fixture("clock"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def page(client: Client, url: str = URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def clock_reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.url.path == CLOCK_PATH]


def setting(html: str, key: str) -> str:
    """The settings-table row ``key`` (up to the next row, nested tables included)."""
    marker = f'<tr id="setting-{key}">'
    assert marker in html, key
    rest = html.split(marker, 1)[1]
    return rest.split('<tr id="setting-', 1)[0]


def anchor_cell(html: str, key: str) -> str:
    found = re.search(rf'<td class="anchor-{key}">(.*?)</td>', html, re.S)
    assert found is not None, key
    return found.group(1)


def serve_clock(engine: FakeEngine, body: dict[str, Any]) -> None:
    """A clock answer served unvalidated (its anchor is the Engine's raw object)."""
    engine.side_effect("GET", CLOCK, lambda request: httpx.Response(200, json=body))


# --- now, level and mode -------------------------------------------------------------------------


def test_clock_reads_display_verbatim(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    display = fixture("clock")["display"]
    assert f'<span class="clock" title="offset 130 min">{display}</span>' in html.replace(
        "&#x27;", "'"
    )
    assert "Clock: <span" in html
    assert "Offset: 130 min from the story's start" in html
    # Complement: no date or time of day the Engine didn't send (the anchor's own strings).
    times = set(re.findall(r"\b\d{2}:\d{2}\b", html))
    engine_strings = (
        display
        + fixture("clock")["anchor"]["display"]
        + " ".join("".join(band) for band in fixture("clock")["settings"]["bands"])
    )
    assert {t for t in times if t not in engine_strings} == set()
    assert "Sunday" not in html
    assert "2026" not in html
    # Each render: the shell's two reads and one get_clock.
    paths = [r.url.path for r in engine.requests]
    assert paths == [f"/stories/{STORY}/effective-config", f"/stories/{STORY}", CLOCK_PATH]


def test_level_and_flags(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert 'Level: <span title="on">On</span>' in html
    assert "Time-claim verification on · Scheduled-event stops on" in html
    assert 'Mode: <span title="paced">Paced</span>' in html
    assert "Reads <code>" not in html
    assert "The clock is off" not in html


def test_level_humanised_and_a_missing_flag_left_out(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("clock")
    body["level"] = "prompt_only"
    engine.respond("GET", CLOCK, 200, json=body)
    engine.respond(
        "GET",
        EFFECTIVE,
        200,
        json=structured_v3(clock__level="prompt_only", clock__time_claims=None),
    )
    html = page(operator_client)
    assert 'Level: <span title="prompt_only">Prompt only</span>' in html
    assert "Time-claim verification" not in html
    assert "Scheduled-event stops on" in html


def test_change_link(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert (
        f'<a href="/stories/{STORY}/inspect/world/clock/settings/">Change clock settings</a>'
        in html
    )


def test_advance_link_present(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert f'<a class="clock-advance" href="{URL}advance/">Advance the clock</a>' in html


def test_advance_link_greyed_when_clock_off(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = page(operator_client)
    link = re.search(r'<span class="disabled clock-advance"[^>]*>Advance the clock</span>', html)
    assert link is not None
    assert "clock.level" in link.group(0)
    assert f'href="{URL}advance/"' not in html


def test_clock_section_is_current(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert f'<a class="active" aria-current="page" href="{URL}">Clock</a>' in html
    assert "<h2>Clock</h2>" in html


# --- the anchor ----------------------------------------------------------------------------------


def test_anchor_components(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert anchor_cell(html, "weekday") == "saturday"
    assert anchor_cell(html, "band") == "morning"
    assert anchor_cell(html, "hour") == "5 … 11"
    for key in ("year", "month", "day", "minute"):
        assert anchor_cell(html, key) == '<span class="muted">unknown</span>'
    anchor = fixture("clock")["anchor"]
    assert f"As established: {anchor['display']} · at offset +02h 10m" in html
    labels = re.findall(
        r"<th>(\w+)</th>", html.split('class="clock-anchor"')[1].split("</thead>")[0]
    )
    assert labels == ["Year", "Month", "Day", "Weekday", "Band", "Hour", "Minute"]


def test_anchor_more(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    more = html.split('class="anchor-more"')[1].split("</details>")[0]
    assert "revision" in more
    assert "reference" in more
    assert "segments" in more
    assert "&quot;weekday&quot;: &quot;saturday&quot;" in more


def test_anchor_established_at_start(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("clock")
    body["anchor"]["anchored_at"] = None
    engine.respond("GET", CLOCK, 200, json=body)
    assert "Established at the story&#x27;s start" in page(operator_client)
    body["anchor"]["anchored_at"] = {"turn": 3, "variant": 0, "slot_k": 1, "beat": 0}
    engine.respond("GET", CLOCK, 200, json=body)
    html = page(operator_client)
    found = re.search(r'<p class="anchor-established"><a href="([^"]+)">([^<]+)</a>', html)
    assert found is not None
    assert found.group(2) == "Established at turn 3⁺¹ · start"
    from console.inspect import history_turn_url

    assert found.group(1) == history_turn_url(STORY, 3, None)


def test_anchor_established_at_turn_link(operator_client: Client, engine: FakeEngine) -> None:
    from console.inspect import history_turn_url

    html = page(operator_client)
    assert (
        f'<a href="{history_turn_url(STORY, 3, None)}">Established at turn 3 · beat 2</a>' in html
    )


def test_unanchored(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK, 200, json=fixture("clock_unanchored"))
    html = page(operator_client)
    assert "Unanchored — the story hasn't established a date or time of day yet" in html
    assert 'class="clock-anchor"' not in html
    assert "As established:" not in html
    assert "Clock: <span" in html
    assert "+00h 00m" in html


def test_malformed_anchor_shows_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("clock")
    del body["anchor"]["hour"]
    serve_clock(engine, body)
    html = page(operator_client)
    assert "This anchor couldn't be read" in html
    assert "&quot;weekday&quot;: &quot;saturday&quot;" in html
    assert 'class="clock-anchor"' not in html
    # The rest of the page renders.
    assert "Clock: <span" in html
    assert 'id="setting-mode"' in html


def test_component_cells() -> None:
    assert component_cell("hour", "Hour", [22, 1]).text == "22 … 1 (wraps)"
    assert component_cell("weekday", "Weekday", ["saturday", "monday"]).text == (
        "saturday … monday"
    )
    assert component_cell("year", "Year", 2026).text == "2026"
    assert component_cell("x", "X", {"a": 1}).kind == "json"
    assert component_cell("x", "X", True).text == "true"
    assert component_cell("x", "X", None).text == "unknown"


def test_anchor_view_edges() -> None:
    assert anchor_view(None) is None
    raw = anchor_view(["x"])
    assert raw is not None
    assert not raw.readable
    full = {key: None for key in ("year", "month", "day", "weekday", "band", "hour", "minute")}
    view = anchor_view(full)
    assert view is not None
    assert view.readable
    assert view.display == "—"
    assert view.offset == "—"
    assert view.established == "Established at the story's start"


# --- the settings table --------------------------------------------------------------------------


def test_settings_table_order_and_read_only_marks(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    keys = re.findall(r'<tr id="setting-([a-z_]+)">', html)
    assert keys == [
        "mode",
        "pacing_table",
        "max_unprompted_skip_minutes",
        "stop_at_due_events",
        "grace_minutes",
        "time_statement_tolerance_minutes",
        "elapsed_tolerance_factor",
        "time_awareness",
        "bands",
        "calendar",
    ]
    labels = [r.label for r in settings_rows(fixture("clock")["settings"])]
    assert labels == [
        "Mode",
        "Pacing table",
        "Max unprompted skip",
        "Stop at due events",
        "Grace",
        "Time-statement tolerance",
        "Elapsed tolerance factor",
        "Time awareness",
        "Bands",
        "Calendar",
    ]
    assert "1,440 min" in setting(html, "max_unprompted_skip_minutes")
    assert "Yes" in setting(html, "stop_at_due_events")
    for key, text in (
        ("grace_minutes", "30 min"),
        ("time_statement_tolerance_minutes", "15 min"),
        ("elapsed_tolerance_factor", "2.0"),
    ):
        row = setting(html, key)
        assert text in row
        assert "(set at creation)" in row
    assert "(set at creation)" not in setting(html, "mode")
    pacing = setting(html, "pacing_table")
    assert "<th>From words</th><th>To words (exclusive)</th><th>Minutes</th>" in pacing
    assert '<td>51</td><td><span class="muted">no end</span></td><td>2</td>' in pacing
    bands = setting(html, "bands")
    assert "<td>night</td><td>21:00</td><td>24:00</td>" in bands
    assert 'title="approximate">Approximate' in setting(html, "time_awareness")
    assert "Gregorian" in setting(html, "calendar")


def test_skip_zero_note() -> None:
    settings = fixture("clock")["settings"]
    settings["max_unprompted_skip_minutes"] = 0
    row = settings_rows(settings)[2]
    assert row.text == "0 min (0: a render never skips on its own)"


def test_malformed_rows_show_as_json() -> None:
    settings = fixture("clock")["settings"]
    settings["pacing_table"] = [[0, 5], [5, None, 1]]
    settings["bands"] = [["night", "00:00"]]
    rows = settings_rows(settings)
    assert rows[1].rows[0][0].code
    assert rows[1].rows[0][0].text == "[0, 5]"
    assert rows[8].rows[0][0].text == '["night", "00:00"]'


def test_custom_calendar_rows(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK, 200, json=fixture("clock_custom_calendar"))
    html = page(operator_client)
    calendar = setting(html, "calendar")
    assert "Custom" in calendar
    assert "Day minutes: 600" in calendar
    assert "<td>Thaw</td><td>30</td>" in calendar
    assert calendar.index("Thaw") < calendar.index("Bloom") < calendar.index("Frost")
    assert "Weekdays: Oneday, Twoday, Threeday, Fourday, Fiveday" in calendar
    assert "Year days: 120" in calendar
    assert "&quot;day_minutes&quot;: 600" in calendar  # the full object in More
    assert "<td>light</td><td>08:00</td><td>20:00</td>" in setting(html, "bands")


def test_custom_calendar_missing_parts_and_unknown_kind() -> None:
    settings = fixture("clock")["settings"]
    settings["calendar"] = {"kind": "custom"}
    row = settings_rows(settings)[9]
    assert row.lines == ("Day minutes: —", "Weekdays: —", "Year days: —")
    settings["calendar"] = {"kind": "lunar"}
    row = settings_rows(settings)[9]
    assert row.code
    assert row.text == '"lunar"'
    assert row.has_more


def test_external_mode_names_provider(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK, 200, json=fixture("clock_external"))
    html = page(operator_client)
    assert (
        'Mode: <span title="external">External</span> · Reads <code>bank</code> · '
        "<code>story_minutes</code>" in html
    )
    assert 'title="external">External' in setting(html, "mode")


def test_external_missing_name_reads_dash(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("clock_external")
    body["settings"]["external_value_name"] = None
    engine.respond("GET", CLOCK, 200, json=body)
    assert "Reads <code>bank</code> · <code>—</code>" in page(operator_client)


# --- gates and notes -----------------------------------------------------------------------------


def test_clock_off_notice(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    body = fixture("clock_unanchored")
    body["level"] = "off"
    engine.respond("GET", CLOCK, 200, json=body)
    html = page(operator_client)
    assert (
        f"The clock is off under this story&#x27;s configuration: {CLOCK_OFF_REASON} — its "
        "settings can still be changed, but nothing advances it" in html
    )
    assert html.index("The clock is off") < html.index('class="clock-settings"')
    assert 'Level: <span title="off">Off</span>' in html
    assert "Time-claim verification off · Scheduled-event stops off" in html
    # Complement: an ungated section still makes its get_clock read, and no other M6 read.
    assert len(clock_reads(engine)) == 1
    paths = [r.url.path for r in engine.requests]
    assert paths == [f"/stories/{STORY}/effective-config", f"/stories/{STORY}", CLOCK_PATH]


def test_world_landing_reaches_clock_when_states_off(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    response = operator_client.get(f"/stories/{STORY}/inspect/world/")
    assert response.status_code == 302
    assert response["Location"] == URL


def test_stops_note_when_flag_off(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=structured_v3(clock__scheduled_stops=False))
    html = page(operator_client)
    stop = setting(html, "stop_at_due_events")
    assert (
        "Stops also need the scheduled-stops module: Needs the <code>scheduled_stops</code>" in stop
    )
    # Complement: with the flag on there is no note.
    engine.respond("GET", EFFECTIVE, 200, json=structured_v3())
    assert "Stops also need" not in page(operator_client)


# --- errors --------------------------------------------------------------------------------------


def test_404_and_503_stay_in_section(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK, 404, json=error_json("not_found", "no such story"))
    html = page(operator_client)
    assert NOT_FOUND in html
    assert 'aria-label="World sections"' in html
    assert "<h2>Clock</h2>" in html
    assert 'class="clock-settings"' not in html
    engine.respond("GET", CLOCK, 503, json=error_json("engine_unavailable", "down"))
    html = page(operator_client)
    assert NOT_FOUND not in html
    assert "engine_unavailable" in html
    assert 'aria-label="World sections"' in html
    assert "<h2>Clock</h2>" in html

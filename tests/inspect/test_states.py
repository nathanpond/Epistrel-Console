"""The World tab's States section and one State as of a boundary (#242).

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``: the player Mara, Alice and Bob). ``m6/states.json`` holds
``gold`` (story, unit "gold", since turn 3⁺¹), ``trust`` (per character, hidden), ``door_open``
(initial value) and ``bank_balance`` (owner external); ``m6/states_alice.json`` is Alice's read
(no ``trust``), keyed on ``viewpoint`` (C14). ``m6/state_view.json`` is ``gold`` through
``3+1``; this story adds ``m6/states_empty.json`` and ``m6/state_view_trust.json``."""

import copy
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.inspect import VIEWPOINT_NOT_IN_CAST
from console.forms.world import BOUNDARY_INVALID
from console.held import held_text
from console.paging import CAST_UNAVAILABLE
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
URL = f"/stories/{STORY}/inspect/world/states/"
STATE = f"/stories/{STORY}/inspect/world/state/"
STATES = "/stories/{story_id}/states"
ONE_STATE = "/stories/{story_id}/states/{name}"
STATES_PATH = f"/stories/{STORY}/states"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
STATES_OFF = (
    "Needs <code>states.level</code> at <code>prompt_only</code> or above (this story runs "
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
    fake.respond("GET", STATES, 200, json=fixture("states"))
    fake.respond("GET", STATES, 200, json=fixture("states_alice"), query={"viewpoint": ALICE_VP})
    fake.respond("GET", ONE_STATE, 200, json=fixture("state_view"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def page(client: Client, url: str = URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def states_reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.url.path.startswith(STATES_PATH)]


def list_reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.url.path == STATES_PATH]


def one_reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.url.path.startswith(f"{STATES_PATH}/")]


def row(html: str, name: str) -> str:
    found = re.search(rf'<tr id="state-{name}" class="state-row">(.*?)</tr>', html, re.S)
    assert found is not None, name
    return found.group(1)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def viewpoint_options(html: str) -> list[str]:
    found = re.search(r'<select name="viewpoint"[^>]*>(.*?)</select>', html, re.S)
    assert found is not None
    return re.findall(r'<option value="([^"]+)"', found.group(1))


def field_errors(html: str, field: str) -> str:
    found = re.search(rf'<div class="field field-{field}">(.*?)</div>', html, re.S)
    assert found is not None
    return found.group(1)


def answer_raw(engine: FakeEngine, body: dict[str, Any]) -> None:
    """A States list the schema refuses (a malformed entry), served unvalidated."""
    engine.side_effect("GET", STATES, lambda request: httpx.Response(200, json=body))


def answer_trust(engine: FakeEngine, status: int, body: dict[str, Any], **query: str) -> None:
    """Answer ``GET …/states/{name}`` with ``body`` (the fake keys on the query, not the name:
    each test reads one State)."""
    engine.respond("GET", ONE_STATE, status, json=body, query=query or None)


# --- the section --------------------------------------------------------------------------------


def test_reads_in_order_and_header(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        STATES_PATH,
    ]
    assert "Viewpoint: Omniscient · revision 12" in html
    assert "World clock: " in html
    assert '<span class="clock" title="offset 130 min">+02h 10m (Saturday' in html
    assert '<p class="states-count">4 States · <a class="state-declare-link"' in html
    assert "<h2>States</h2>" in html
    assert 'aria-current="page" href="/stories/' + STORY + '/inspect/world/states/">States' in html


def test_rows_render_each_column(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    gold = row(html, "gold")
    assert cell(gold, "value") == "120 gold"
    assert "turn 3⁺¹ · start" in cell(gold, "since")
    assert f'href="{STATE}gold/"' in cell(gold, "name")
    assert '<span title="story">Story</span>' in cell(gold, "scope")
    assert '<span title="player_visible">Player visible</span>' in cell(gold, "visibility")
    door = row(html, "door_open")
    assert cell(door, "value") == "false"
    assert cell(door, "since") == "initial value"
    bank = row(html, "bank_balance")
    assert cell(bank, "owner") == '<span title="external">External</span>'
    trust = row(html, "trust")
    assert '<span title="character">Per character</span>' in cell(trust, "scope")
    values = cell(trust, "value")
    labels = re.findall(r"<dt>(.*?)</dt>", values)
    assert labels == ["Mara (player)", "Alice", "Bob"]
    assert '<dt>Bob</dt><dd><span class="undefined">undefined</span></dd>' in values
    assert "<dt>Alice</dt><dd>0.4</dd>" in values
    more = cell(gold, "more")
    assert "<dt>Type</dt><dd><code>number</code></dd>" in more
    assert "<dt>Unit</dt><dd>gold</dd>" in more
    assert "<dt>Step</dt><dd>0</dd>" in more
    assert "<dt>slot_k</dt><dd>1</dd>" in more
    # door_open: no unit, no position, so no Unit/Step rows
    assert "<dt>Unit</dt>" not in cell(door, "more")
    assert "<dt>Step</dt>" not in cell(door, "more")


def test_since_links_the_turn(operator_client: Client, engine: FakeEngine) -> None:
    from console.positions import knowledge_position_url
    from console.world import five_part

    html = page(operator_client)
    position = five_part({"turn": 3, "variant": 0, "slot_k": 1, "beat": 0, "step": 0})
    url = knowledge_position_url(STORY, position)
    assert cell(row(html, "gold"), "since") == f'<a href="{url}">turn 3⁺¹ · start</a>'
    assert url == knowledge_position_url(
        STORY, five_part({"turn": 3, "variant": 0, "slot_k": 0, "beat": 1, "step": 0})
    )


def test_column_order(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    head = re.search(r'<table class="states">\s*<thead><tr>(.*?)</tr>', html, re.S)
    assert head is not None
    assert re.findall(r"<th>(.*?)</th>", head.group(1)) == [
        "Name",
        "Value",
        "Scope",
        "Visibility",
        "Owner",
        "Since",
        "Actions",  # #251's Set… (convention 14's actions hook)
        "More",
    ]
    cells = re.findall(r'<td class="([a-z]+)">', row(html, "gold"))
    assert cells == ["name", "value", "scope", "visibility", "owner", "since", "actions", "more"]


def test_actions_hook_renders_a_column() -> None:
    from django.template.loader import render_to_string

    from console.engine.models import StoryState
    from console.states import state_row

    state = StoryState.from_json(fixture("states")["states"][0])
    assert state is not None
    built = state_row(state, {}, [], STORY, None)
    with override_settings(ROOT_URLCONF="epistrel_console.urls"):
        html = render_to_string(
            "console/inspect/_state_row.html",
            {"row": built, "story_id": STORY, "actions": "console/inspect/_unavailable.html"},
        )
    assert re.findall(r'<td class="([a-z]+)">', html)[-2:] == ["actions", "more"]


def test_actions_none_renders_no_column() -> None:
    """**Complement** (#276): ``actions`` None leaves the row without an Actions cell."""
    from django.template.loader import render_to_string

    from console.engine.models import StoryState
    from console.states import state_row

    state = StoryState.from_json(fixture("states")["states"][0])
    assert state is not None
    built = state_row(state, {}, [], STORY, None)
    with override_settings(ROOT_URLCONF="epistrel_console.urls"):
        html = render_to_string(
            "console/inspect/_state_row.html",
            {"row": built, "story_id": STORY, "actions": None},
        )
    assert '<td class="actions">' not in html
    assert re.findall(r'<td class="([a-z]+)">', html) == [
        "name",
        "value",
        "scope",
        "visibility",
        "owner",
        "since",
        "more",
    ]


@pytest.mark.parametrize("url", [URL, f"{STATE}gold/"], ids=["states", "state"])
def test_actions_none_hides_the_column_header(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """**Complement** (#276): with no actions hook, neither ``states.html`` nor ``state.html``
    renders an Actions header or cell."""
    from console.views import inspect_states

    monkeypatch.setattr(inspect_states, "ACTIONS_TEMPLATE", None)
    html = page(operator_client, url)
    assert '<table class="states">' in html
    assert "<th>Actions</th>" not in html
    assert '<td class="actions">' not in html
    assert "<th>More</th>" in html


def test_default_viewpoint_is_omniscient_not_the_story_default(
    operator_client: Client, engine: FakeEngine
) -> None:
    story = fixture("story")
    story["settings"]["default_output_viewpoint"] = ALICE_VP
    engine.respond("GET", STORY_PATH, 200, json=story)
    html = page(operator_client)
    (read,) = list_reads(engine)
    assert read.url.params["viewpoint"] == "omniscient"
    assert '<option value="omniscient" selected>Omniscient</option>' in html
    cast = [f"character:{MARA}", ALICE_VP, f"character:{BOB}"]
    assert viewpoint_options(html) == ["omniscient", *cast]
    assert "Mara (player)</option>" in html


def test_character_viewpoint_omits_hidden_states(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, f"{URL}?viewpoint={ALICE_VP}")
    (read,) = states_reads(engine)
    assert read.url.params["viewpoint"] == ALICE_VP
    assert 'id="state-trust"' not in html
    assert "trust" not in html
    assert "Viewpoint: Alice — hidden States are left out · revision 12" in html
    assert '<p class="states-count">3 States · <a class="state-declare-link"' in html
    # the Name link keeps the viewpoint
    assert f'href="{STATE}gold/?viewpoint=character%3A{ALICE}"' in html


def test_omniscient_page_shows_trust(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** the omniscient page does show ``trust``."""
    html = page(operator_client, f"{URL}?viewpoint=omniscient")
    assert 'id="state-trust"' in html
    assert f'href="{STATE}gold/"' in html


def test_hidden_state_named_for_a_character_reads_not_readable(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_trust(
        engine,
        404,
        error_json("not_found", "the story has no State 'trust'", story_id=STORY, state="trust"),
        viewpoint=ALICE_VP,
    )
    html = page(operator_client, f"{STATE}trust/?viewpoint={ALICE_VP}")
    assert (
        "No State “trust” is readable here. It may not exist, be hidden from this viewpoint."
        in html
    )
    assert "<dd>0.4" not in html
    assert "<dd>0.7" not in html
    assert len(states_reads(engine)) == 1
    (read,) = one_reads(engine)
    assert read.url.params["viewpoint"] == ALICE_VP
    assert "<table" not in html
    assert "states-header" not in html


def test_one_state_omniscient_trust(operator_client: Client, engine: FakeEngine) -> None:
    answer_trust(engine, 200, fixture("state_view_trust"))
    html = page(operator_client, f"{STATE}trust/")
    assert "<title>Inspect · trust · Harbour Ledger</title>" in html
    assert '<h3 class="state-name">trust · as the story stands</h3>' in html
    assert "Viewpoint: Omniscient · revision 7" in html
    trust = row(html, "trust")
    assert cell(trust, "name") == "trust"  # unlinked
    assert "<dt>Mara (player)</dt><dd>0.7</dd>" in trust
    (read,) = one_reads(engine)
    assert read.url.path == f"{STATES_PATH}/trust"
    assert "as_of" not in read.url.params
    assert f'<a href="{URL}">Back to States</a>' in html
    assert 'aria-current="page" href="/stories/' + STORY + '/inspect/world/states/">States' in html


def test_viewpoint_outside_cast_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"{URL}?viewpoint=character:{OUTSIDER}")
    assert escape(VIEWPOINT_NOT_IN_CAST) in field_errors(html, "viewpoint")
    assert states_reads(engine) == []
    html = page(operator_client, f"{STATE}gold/?viewpoint=character:{OUTSIDER}")
    assert escape(VIEWPOINT_NOT_IN_CAST) in field_errors(html, "viewpoint")
    assert states_reads(engine) == []


def test_story_read_failed_offers_raw_viewpoint(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("engine_unavailable", "down"))
    html = page(operator_client, f"{URL}?viewpoint={ALICE_VP}")
    assert CAST_UNAVAILABLE in html
    assert html.index(CAST_UNAVAILABLE) < html.index('<form method="get" id="states-viewpoint">')
    assert viewpoint_options(html) == ["omniscient", ALICE_VP]
    (read,) = states_reads(engine)
    assert read.url.params["viewpoint"] == ALICE_VP


def test_viewpoint_unresolved_lands_on_field(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        STATES,
        422,
        json=error_json("viewpoint_unresolved", "no such character"),
        query={"viewpoint": ALICE_VP},
    )
    html = page(operator_client, f"{URL}?viewpoint={ALICE_VP}")
    assert escape(VIEWPOINT_NOT_IN_CAST) in field_errors(html, "viewpoint")
    assert "<table" not in html


def test_other_422_above_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATES, 422, json=error_json("validation_error", "odd request"))
    html = page(operator_client)
    errors = html.index('<div class="errors" role="alert">')
    assert "odd request" in html[errors : html.index('<form method="get"')]


@pytest.mark.parametrize("typed", ["3+1", "3⁺¹"])
def test_as_of_sent_as_given(operator_client: Client, engine: FakeEngine, typed: str) -> None:
    response = operator_client.get(f"{STATE}gold/", {"as_of": typed})
    assert response.status_code == 200
    html = response.content.decode()
    (read,) = one_reads(engine)
    assert "as_of=3%2B1" in read.url.query.decode()
    assert read.url.params["viewpoint"] == "omniscient"
    assert '<h3 class="state-name">gold · through turn 3⁺¹</h3>' in html
    assert "<dt>Unit</dt><dd>gold</dd>" in html
    assert f'<a href="{URL}">Back to States</a>' in html  # never as_of


def test_blank_as_of_sends_none(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** blank As of sends no ``as_of``."""
    page(operator_client, f"{STATE}gold/?as_of=")
    (read,) = one_reads(engine)
    assert "as_of" not in read.url.params


@pytest.mark.parametrize("typed", ["3+", "+1", "3.1", "x"])
def test_bad_boundary_zero_calls(operator_client: Client, engine: FakeEngine, typed: str) -> None:
    response = operator_client.get(f"{STATE}gold/", {"as_of": typed})
    html = response.content.decode()
    assert escape(BOUNDARY_INVALID) in field_errors(html, "as_of")
    assert one_reads(engine) == []


def test_as_of_past_head_beside_field(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        ONE_STATE,
        422,
        json=error_json(
            "validation_error",
            "as_of is a turn of the story, from 0 to its head",
            field="query.as_of",
            head=9,
        ),
        query={"as_of": "12"},
    )
    html = page(operator_client, f"{STATE}gold/?as_of=12")
    assert "The story&#x27;s head is turn 9" in field_errors(html, "as_of")
    assert 'value="12"' in field_errors(html, "as_of")
    assert '<h3 class="state-name">gold · through turn 12</h3>' in html


def test_as_of_refused_without_head_uses_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        ONE_STATE,
        422,
        json=error_json("validation_error", "as_of out of range", field="query.as_of"),
        query={"as_of": "12"},
    )
    html = page(operator_client, f"{STATE}gold/?as_of=12")
    assert "as_of out of range" in field_errors(html, "as_of")


def test_declared_after_boundary_reads_not_readable(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        ONE_STATE,
        404,
        json=error_json("not_found", "no State", story_id=STORY, state="gold"),
        query={"as_of": "1+1"},
    )
    html = page(operator_client, f"{STATE}gold/?as_of=1%2B1")
    assert (
        "No State “gold” is readable here. It may not exist, be hidden from this viewpoint, or "
        "be declared after turn 1⁺¹." in html
    )
    assert 'value="1+1"' in field_errors(html, "as_of")


def test_one_state_404_without_state_is_not_found(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ONE_STATE, 404, json=error_json("not_found", "gone", story_id=STORY))
    html = page(operator_client, f"{STATE}gold/")
    assert NOT_FOUND in html
    assert "is readable here" not in html


@pytest.mark.parametrize("name", ["9gold", "a-b"])
def test_invalid_state_name_is_django_404(
    operator_client: Client, engine: FakeEngine, name: str
) -> None:
    response = operator_client.get(f"{STATE}{name}/")
    assert response.status_code == 404
    assert engine.requests == []


@pytest.mark.parametrize("config", [lite_m6(), structured_v3(states__level="off")])
def test_states_gate_closed_zero_calls(
    operator_client: Client, engine: FakeEngine, config: dict[str, Any]
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=config)
    html = page(operator_client)
    assert f'<p class="notice unavailable">{STATES_OFF}' in html
    one = page(operator_client, f"{STATE}gold/?viewpoint={ALICE_VP}")
    assert f'<p class="notice unavailable">{STATES_OFF}' in one
    assert '<h3 class="state-name">gold</h3>' in one
    assert f'<a href="{URL}">Back to States</a>' in one
    assert "<form" not in one.split('<nav class="subtabs"')[1]
    assert states_reads(engine) == []


def test_states_prompt_only_on_lite_is_served(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6(**{"states.level": "prompt_only"}))
    html = page(operator_client)
    assert len(list_reads(engine)) == 1
    assert 'id="state-gold"' in html
    page(operator_client, f"{STATE}gold/")
    assert len(one_reads(engine)) == 1


def test_module_off_after_gate_is_greyed_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATES, 409, json=error_json("module_off", "off", module="states"))
    html = page(operator_client)
    assert '<div class="panel unavailable">' in html
    assert "The story's configuration changed since this page loaded" in html
    engine.respond("GET", ONE_STATE, 409, json=error_json("module_off", "off", module="states"))
    one = page(operator_client, f"{STATE}gold/")
    assert '<div class="panel unavailable">' in one


def test_empty_texts(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATES, 200, json=fixture("states_empty"))
    html = page(operator_client)
    assert "This story tracks no States yet" in html
    assert "<table" not in html
    empty = fixture("states_empty")
    empty["output_viewpoint"] = ALICE_VP
    engine.respond("GET", STATES, 200, json=empty, query={"viewpoint": ALICE_VP})
    html = page(operator_client, f"{URL}?viewpoint={ALICE_VP}")
    assert "No States are readable from Alice&#x27;s viewpoint" in html


def test_unparsed_states_notice(operator_client: Client, engine: FakeEngine) -> None:
    answer_raw(engine, fixture("states_bad_item"))
    html = page(operator_client)
    assert 'id="state-gold"' in html
    assert 'id="state-door_open"' not in html
    assert "Some States couldn&#x27;t be read" in html
    notice = html.index("Some States couldn")
    assert notice < html.index('<table class="states">')
    raws = re.findall(r'<pre class="raw-item">(.*?)</pre>', html, re.S)
    assert len(raws) == 1
    assert "door_open" in raws[0]
    assert "gold" not in raws[0]
    assert '<p class="states-count">1 State · <a class="state-declare-link"' in html


def test_all_unparsed_shows_notice_only(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("states_bad_item")
    body["states"] = body["states"][1:]
    answer_raw(engine, body)
    html = page(operator_client)
    assert "Some States couldn" in html
    assert "This story tracks no States yet" not in html
    assert "<table" not in html


@pytest.mark.parametrize(
    ("urlconf", "linked"),
    [
        ("tests.inspect.urls_states_no_clock", False),
        ("tests.inspect.urls_states_with_clock", True),
    ],
)
def test_clock_line_links_clock_only_when_registered(
    operator_client: Client, engine: FakeEngine, urlconf: str, linked: bool
) -> None:
    with override_settings(ROOT_URLCONF=urlconf):
        html = page(operator_client)
    line = re.search(r'<p class="world-clock">(.*?)</p>', html, re.S)
    assert line is not None
    clock = f'<a href="/stories/{STORY}/inspect/world/clock/">Clock</a>'
    assert (clock in line.group(1)) is linked
    assert line.group(1).startswith('World clock: <span class="clock"')
    assert ("· " in line.group(1)) is linked


def test_404_story_not_found_text(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATES, 404, json=error_json("not_found", "gone", story_id=STORY))
    html = page(operator_client)
    assert NOT_FOUND in html
    assert '<form method="get" id="states-viewpoint">' in html
    assert "Harbour Ledger" in html


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        STATES,
        503,
        json=error_json("erasure_pending", "held"),
        headers={"Retry-After": "30"},
    )
    response = operator_client.get(URL)
    assert response.status_code == 503
    assert held_text("30") in response.content.decode()


def test_503_panel_keeps_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATES, 503, json=error_json("engine_unavailable", "down"))
    html = page(operator_client)
    form = html.index('<form method="get" id="states-viewpoint">')
    panel = html.index('<div class="engine-error')
    assert form < panel
    assert "states-header" not in html
    assert "World clock" not in html


def test_value_not_fitting_type_is_json(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("states")
    body["states"][0]["value"] = "many"
    engine.respond("GET", STATES, 200, json=body)
    html = page(operator_client)
    assert cell(row(html, "gold"), "value") == "&quot;many&quot;"


def test_mismatched_scope_renders_what_was_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("states")
    body["states"][0]["values"] = {ALICE: 3}  # story-scoped, but a values map
    body["states"][1].pop("values")  # per character, but only a value
    body["states"][1]["value"] = 5
    engine.respond("GET", STATES, 200, json=body)
    html = page(operator_client)
    assert "<dt>Alice</dt><dd>3 gold</dd>" in cell(row(html, "gold"), "value")
    assert cell(row(html, "trust"), "value") == "5"


def test_no_state_write_on_any_get(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** across the section's and the one-State page's GETs, no write."""
    for url in (
        URL,
        f"{URL}?viewpoint={ALICE_VP}",
        f"{STATE}gold/",
        f"{STATE}gold/?as_of=3%2B1",
        f"{STATE}trust/?viewpoint={ALICE_VP}",
    ):
        operator_client.get(url)
    assert engine.requests
    assert {r.method for r in engine.requests} == {"GET"}


def test_world_landing_redirects_to_states(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(f"/stories/{STORY}/inspect/world/?viewpoint=x")
    assert response.status_code == 302
    assert response["Location"] == URL
    assert states_reads(engine) == []

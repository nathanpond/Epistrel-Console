"""Set a State between turns (#251): the Set page, the form, the write, the result and refusals.

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``: the player Mara, Alice and Bob). Each State the page reads
is a ``m6/states.json`` entry wrapped into a ``StateView`` body by :func:`state_view` (revision
12, ``as_of: null``, omniscient). ``m6/state_written.json`` is ``gold`` 120 → 125 at turn 3⁺²
(revision 13); this story adds ``m6/state_written_trust.json`` (Alice's ``trust`` 0.4 → 0.5 at
turn 2⁺¹, revision 8). Refusal bodies are built inline (D16)."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.states import (
    CHOOSE_CHARACTER,
    ENTER_AMOUNT,
    ENTER_VALUE,
    GIVE_REASON,
    NO_VALUE_YET,
    TEXT_TOO_LONG,
)
from console.forms.world import NUMBER_INVALID
from console.held import held_text
from console.models import EngineOperation
from console.paging import CAST_UNAVAILABLE, CHARACTER_INVALID
from console.views import inspect as views
from console.wording import UNKNOWN_CHARACTER
from console.world import READONLY_EXTERNAL_TEXT
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
OUTSIDER = "01JM6CH0000000000000000099"
KEY = "01JM6QP00000000000000000K1"
ONE_STATE = "/stories/{story_id}/states/{name}"
STATES_PATH = f"/stories/{STORY}/states"
STATES_URL = f"/stories/{STORY}/inspect/world/states/"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
WRITES = ("PUT", "POST", "PATCH", "DELETE")


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m6/{name}"))
    return body


def state_view(source: str, /, **changes: Any) -> dict[str, Any]:
    """A ``states.json`` State as a ``StateView`` body (``changes`` laid over the State)."""
    state = next(s for s in fixture("states")["states"] if s["name"] == source)
    state.update(changes)
    return {
        "state": state,
        "as_of": None,
        "timeline_revision": 12,
        "output_viewpoint": "omniscient",
    }


def set_url(name: str = "gold") -> str:
    return f"/stories/{STORY}/inspect/world/state/{name}/set/"


def written_url(name: str = "gold") -> str:
    return f"/stories/{STORY}/inspect/world/state/{name}/set/written/"


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
    fake.respond("GET", "/stories/{story_id}/states", 200, json=fixture("states"))
    fake.respond("GET", ONE_STATE, 200, json=state_view("gold"))
    fake.respond("PUT", ONE_STATE, 200, json=fixture("state_written"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def reads(engine: FakeEngine, name: str = "gold") -> list[Any]:
    path = f"{STATES_PATH}/{name}"
    return [r for r in engine.requests if r.method == "GET" and r.url.path == path]


def puts(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "PUT"]


def writes(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method in WRITES]


def sent(engine: FakeEngine) -> dict[str, Any]:
    (put,) = puts(engine)
    body: dict[str, Any] = json.loads(put.content)
    return body


def get(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def post(client: Client, name: str = "gold", url: str | None = None, **fields: Any) -> str:
    data: dict[str, Any] = {"idempotency_key": KEY, "expected_revision": "12"}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    response = client.post(url or set_url(name), data)
    assert response.status_code == 200
    return response.content.decode()


def field(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def hidden(html: str, name: str) -> str:
    found = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)">', html)
    assert found is not None, name
    return found.group(1)


def refusal(html: str) -> str:
    found = re.search(r'<p class="errorlist state-set-refusal" role="alert">(.*?)</p>', html, re.S)
    assert found is not None
    return found.group(1)


def options(html: str, name: str) -> list[tuple[str, str, bool]]:
    found = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert found is not None
    return [
        (value, unescape(label), "selected" in attrs)
        for value, attrs, label in re.findall(
            r'<option value="([^"]*)"([^>]*)>([^<]*)</option>', found.group(1)
        )
    ]


def unchanged(client: Client) -> None:
    """A refused write changed nothing: the States list still reads the old value."""
    html = get(client, STATES_URL)
    gold = re.search(r'<tr id="state-gold" class="state-row">(.*?)</tr>', html, re.S)
    assert gold is not None
    assert "120 gold" in gold.group(1)


# --- the page ------------------------------------------------------------------------------


def test_page_reads_and_shows_the_state(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, set_url())
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        f"{STATES_PATH}/gold",
    ]
    assert reads(engine)[0].url.params["viewpoint"] == "omniscient"
    assert "Set gold</h3>" in html
    assert "120 gold" in html
    assert "Story revision 12" in html
    assert hidden(html, "expected_revision") == "12"
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", hidden(html, "idempotency_key"))
    assert "Inspect · Set gold · " in html
    assert f'<a class="active" aria-current="page" href="{STATES_URL}">States</a>' in html
    value = field(html, "value")
    assert 'inputmode="decimal"' in value
    assert "min=" not in value
    assert "max" not in value
    assert re.search(r'value="value"[^>]*checked', html) is not None
    assert re.search(r'value="delta"[^>]*checked', html) is None
    assert "Set to uses Value; Change by uses Amount — the other box is ignored." in html
    assert "maxlength" not in html.split('class="state-set-form"')[1].split("</form>")[0]


def test_get_makes_no_write(operator_client: Client, engine: FakeEngine) -> None:
    for name, view in (
        ("gold", state_view("gold")),
        ("trust", state_view("trust")),
        ("door_open", state_view("door_open")),
        ("bank_balance", state_view("bank_balance")),
        ("ledger", state_view("gold", name="ledger", type="ledger")),
    ):
        engine.respond("GET", ONE_STATE, 200, json=view)
        get(operator_client, set_url(name))
    get(operator_client, set_url("world_clock"))
    assert not writes(engine)


@pytest.mark.parametrize("config", ["lite", "states_off"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_gate_closed_zero_calls(
    operator_client: Client, engine: FakeEngine, config: str, method: str
) -> None:
    body = lite_m6() if config == "lite" else structured_v3(states__level="off")
    engine.respond("GET", EFFECTIVE, 200, json=body)
    if method == "get":
        html = get(operator_client, set_url())
    else:
        html = post(operator_client, value="130", change="value", reason="r")
    assert "states.level" in html
    assert 'class="state-set-form"' not in html
    assert "Set gold</h3>" in html
    assert "Back to States" in html
    assert not reads(engine)
    assert not puts(engine)


def test_external_state_offers_no_form_and_refuses_post(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("bank_balance"))
    html = get(operator_client, set_url("bank_balance"))
    assert escape(READONLY_EXTERNAL_TEXT) in html
    assert 'class="state-set-form"' not in html
    html = post(operator_client, "bank_balance", change="value", value="1", reason="forged")
    assert escape(READONLY_EXTERNAL_TEXT) in html
    assert not puts(engine)


def test_unknown_type_offers_no_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("gold", name="ledger", type="ledger"))
    html = get(operator_client, set_url("ledger"))
    assert "The Console can&#x27;t set a State of type <code>ledger</code>" in html
    assert 'class="state-set-form"' not in html
    html = post(operator_client, "ledger", value="1", reason="forged")
    assert "<code>ledger</code>" in html
    assert not puts(engine)


def test_world_clock_names_the_clock_section(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, set_url("world_clock"))
    assert "<code>world_clock</code> is the world clock — use the Clock section" in html
    assert f'<a href="/stories/{STORY}/inspect/world/clock/">Clock</a>' in html
    post(operator_client, "world_clock", value="1", reason="forged")
    assert not reads(engine, "world_clock")
    assert not puts(engine)


def test_read_404_is_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 404, json=error_json("not_found", state="silver"))
    html = get(operator_client, set_url("silver"))
    assert NOT_FOUND in html
    assert 'class="state-set-form"' not in html
    assert f'<a href="{STATES_URL}">Back to States</a>' in html


def test_read_failure_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", ONE_STATE, httpx.ConnectError("down"))
    html = post(operator_client, value="130", change="value", reason="r")
    assert 'class="state-set-form"' not in html
    assert "engine-error" in html
    assert not puts(engine)


# --- the body ------------------------------------------------------------------------------


def test_set_story_number_value(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, set_url())
    key = hidden(html, "idempotency_key")
    post(
        operator_client,
        change="value",
        value="130",
        delta="",
        reason="  found a purse ",
        idempotency_key=key,
        expected_revision=hidden(html, "expected_revision"),
    )
    assert sent(engine) == {"value": 130, "reason": "found a purse"}
    (put,) = puts(engine)
    assert put.headers["X-Epistrel-Expected-Revision"] == "12"
    assert put.headers["Idempotency-Key"] == key


def test_change_by_sends_delta_only(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, change="delta", value="999", delta="-3", reason="spent")
    assert sent(engine) == {"delta": -3, "reason": "spent"}


def test_change_by_zero_and_blank(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, change="delta", value="5", delta="", reason="r")
    assert ENTER_AMOUNT in field(html, "delta")
    assert not puts(engine)
    post(operator_client, change="delta", delta="0", reason="r")
    assert sent(engine) == {"delta": 0, "reason": "r"}


def test_change_missing_is_required(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, value="5", reason="r")
    assert "This field is required." in html
    post(operator_client, change="forged", value="5", reason="r")
    assert not puts(engine)


@pytest.mark.parametrize(
    ("typed", "number"), [("0.5", 0.5), ("+5", 5), ("\u22123", -3), (" 12 ", 12)]
)
def test_number_syntax(
    operator_client: Client, engine: FakeEngine, typed: str, number: float
) -> None:
    post(operator_client, change="value", value=typed, reason="r")
    value = sent(engine)["value"]
    assert value == number
    assert type(value) is type(number)


@pytest.mark.parametrize("typed", ["5e3", "1,000", "NaN", "١٢", "-", ""])
def test_number_syntax_complement(operator_client: Client, engine: FakeEngine, typed: str) -> None:
    html = post(operator_client, change="value", value=typed, reason="r")
    assert escape(NUMBER_INVALID) in field(html, "value")
    html = post(operator_client, change="delta", delta=typed or "x", reason="r")
    assert escape(NUMBER_INVALID) in field(html, "delta")
    assert not puts(engine)


def test_boolean_sends_json_bool(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("door_open"))
    html = get(operator_client, set_url("door_open"))
    assert options(html, "value") == [("true", "true", False), ("false", "false", True)]
    assert "field-change" not in html
    assert "field-delta" not in html
    post(operator_client, "door_open", value="false", change="delta", delta="3", reason="shut")
    assert sent(engine) == {"value": False, "reason": "shut"}


def test_enum_and_text_sent_verbatim(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("gold", name="mood", type="enum"))
    html = post(operator_client, "mood", value="  ")
    assert ENTER_VALUE in field(html, "value")
    post(operator_client, "mood", value="  calm ", reason="r")
    assert sent(engine) == {"value": "calm", "reason": "r"}
    engine.requests.clear()
    engine.respond("GET", ONE_STATE, 200, json=state_view("gold", name="note", type="text"))
    html = get(operator_client, set_url("note"))
    assert "<textarea" in field(html, "value")
    post(operator_client, "note", value="  two\r\nlines", reason="r")
    assert sent(engine) == {"value": "  two\nlines", "reason": "r"}
    engine.requests.clear()
    html = post(operator_client, "note", value="x" * 201, reason="r")
    assert TEXT_TOO_LONG in field(html, "value")
    html = post(operator_client, "note", value="", reason="r")
    assert ENTER_VALUE in field(html, "value")
    assert not puts(engine)


def test_per_character_requires_character_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("trust"))
    html = get(operator_client, set_url("trust"))
    assert options(html, "scope_key") == [
        ("", "Choose a character", True),
        (MARA, "Mara (player)", False),
        (ALICE, "Alice", False),
        ("01JM6CH0000000000000000003", "Bob", False),
    ]
    assert "0.4" in html
    assert '<dl class="state-values">' in html
    html = post(operator_client, "trust", change="value", value="0.5", scope_key="", reason="r")
    assert CHOOSE_CHARACTER in field(html, "scope_key")
    assert not puts(engine)


def test_per_character_sends_scope_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("trust"))
    engine.respond("PUT", ONE_STATE, 200, json=fixture("state_written_trust"))
    post(operator_client, "trust", change="value", value="0.5", scope_key=ALICE, reason="r")
    assert sent(engine) == {"value": 0.5, "scope_key": ALICE, "reason": "r"}


def test_story_scoped_never_sends_scope_key(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, set_url())
    assert "scope_key" not in html
    post(operator_client, change="value", value="1", scope_key=ALICE, reason="r")
    assert "scope_key" not in sent(engine)


def test_url_scope_key_lowercase_selects_cast_member(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("trust"))
    html = get(operator_client, f"{set_url('trust')}?scope_key=%20{ALICE.lower()}%20")
    chosen = [value for value, _, selected in options(html, "scope_key") if selected]
    assert chosen == [ALICE]
    assert "not in the cast" not in html


def test_unknown_url_scope_key_kept_and_sent(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("trust"))
    html = get(operator_client, f"{set_url('trust')}?scope_key={OUTSIDER}")
    assert options(html, "scope_key")[-1] == (OUTSIDER, "…00000099 (not in the cast)", True)
    post(operator_client, "trust", change="value", value="0.5", scope_key=OUTSIDER, reason="r")
    assert sent(engine)["scope_key"] == OUTSIDER


def test_non_ulid_scope_key_kept_and_refused(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("trust"))
    html = get(operator_client, f"{set_url('trust')}?scope_key=nobody")
    assert options(html, "scope_key")[-1] == ("NOBODY", "NOBODY (not in the cast)", True)
    html = post(operator_client, "trust", change="value", value="1", scope_key="nobody", reason="r")
    assert escape(CHARACTER_INVALID) in field(html, "scope_key")
    assert not puts(engine)


def test_story_read_failed_free_text_character(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", STORY_PATH, httpx.ConnectError("down"))
    engine.respond("GET", ONE_STATE, 200, json=state_view("trust"))
    html = get(operator_client, set_url("trust"))
    assert CAST_UNAVAILABLE in html
    assert '<input type="text" name="scope_key"' in field(html, "scope_key")
    html = post(operator_client, "trust", change="value", value="1", scope_key="bob", reason="r")
    assert escape(CHARACTER_INVALID) in field(html, "scope_key")
    assert not puts(engine)
    post(operator_client, "trust", change="value", value="1", scope_key=ALICE.lower(), reason="r")
    assert sent(engine)["scope_key"] == ALICE


@pytest.mark.parametrize(
    ("reason", "error"), [("", GIVE_REASON), ("   ", GIVE_REASON), ("x" * 201, TEXT_TOO_LONG)]
)
def test_reason_required_and_bounded_zero_calls(
    operator_client: Client, engine: FakeEngine, reason: str, error: str
) -> None:
    html = post(operator_client, change="value", value="1", reason=reason)
    assert error in field(html, "reason")
    assert not puts(engine)


def test_invalid_form_keeps_posted_key_and_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post(operator_client, change="value", value="x", reason="r", expected_revision="9")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "9"
    assert 'value="x"' in field(html, "value")


@pytest.mark.parametrize("revision", [None, "x"])
def test_missing_expected_revision_sends_no_header(
    operator_client: Client, engine: FakeEngine, revision: str | None
) -> None:
    data: dict[str, Any] = {"idempotency_key": KEY, "change": "value", "value": "1", "reason": "r"}
    if revision is not None:
        data["expected_revision"] = revision
    operator_client.post(set_url(), data)
    (put,) = puts(engine)
    assert "X-Epistrel-Expected-Revision" not in put.headers


# --- the result page -------------------------------------------------------------------------


def lines(html: str) -> list[str]:
    found = re.search(r'<ul class="state-written-lines">(.*?)</ul>', html, re.S)
    assert found is not None
    return [
        unescape(re.sub(r"<[^>]+>", "", item)).strip()
        for item in re.findall(r"<li[^>]*>(.*?)</li>", found.group(1), re.S)
    ]


def test_result_page_lines(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, change="value", value="125", reason="found a purse")
    row = EngineOperation.objects.get()
    assert row.kind == "set_state"
    assert "Set gold</h3>" in html
    assert lines(html) == [
        "Value: 120 gold → 125 gold",
        "Reason: found a purse",
        "Slot: turn 3⁺² · start",
        "Operation: 01JM6QP0000000000000000011",
        "Story revision now 13",
    ]
    slot = re.search(r'<li class="slot">Slot: <a href="([^"]+)">', html)
    assert slot is not None
    assert "3" in slot.group(1)
    assert f'href="/operations/{row.pk}/"' in html
    assert f'href="/stories/{STORY}/inspect/world/state/gold/?as_of=3%2B2"' in html
    assert "See gold as of this slot" in html
    assert f'<a class="set-again" href="{set_url()}">Set again</a>' in html
    assert f'href="{STATES_URL}">Back to States</a>' in html
    before, more = html.split('<details class="state-written-more">')
    assert "3⁺2" not in before
    assert "3⁺2" in more
    assert "01JM6CG0000000000000000011" in more
    assert "Replayed" not in html


def test_delta_result_reads_changed_by(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, change="delta", delta="5", reason="r")
    assert lines(html)[0] == "Changed by +5 gold (120 gold → 125 gold)"


def test_per_character_result_names_character(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("trust"))
    engine.respond("PUT", ONE_STATE, 200, json=fixture("state_written_trust"))
    html = post(operator_client, "trust", change="value", value="0.5", scope_key=ALICE, reason="r")
    assert lines(html)[:2] == ["Character: Alice", "Value: 0.4 → 0.5"]
    assert f'href="{set_url("trust")}?scope_key={ALICE}">Set again' in html


def test_replayed_row_says_replayed(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "PUT", ONE_STATE, 200, json=fixture("state_written"), headers={"Idempotent-Replay": "true"}
    )
    html = post(operator_client, change="value", value="125", reason="r")
    assert "Replayed (repeated submit)" in html
    row = EngineOperation.objects.get()
    assert "Replayed (repeated submit)" in get(operator_client, f"{written_url()}?report={row.pk}")


def test_report_page_rebuilds_from_row(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, change="delta", delta="5", reason="bought a ship")
    row = EngineOperation.objects.get()
    engine.requests.clear()
    html = get(operator_client, f"{written_url()}?report={row.pk}")
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
    ]
    assert lines(html) == [
        "Changed by +5 (120 → 125)",
        "Reason: bought a ship",
        "Slot: turn 3⁺² · start",
        "Operation: 01JM6QP0000000000000000011",
        "Story revision now 13",
    ]


def test_report_page_not_gated(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, change="value", value="125", reason="r")
    row = EngineOperation.objects.get()
    engine.respond("GET", EFFECTIVE, 200, json=structured_v3(states__level="off"))
    assert "Value: 120 → 125" in get(operator_client, f"{written_url()}?report={row.pk}")


@pytest.mark.parametrize("case", ["kind", "story", "error", "state", "nonint", "missing"])
def test_report_page_complement(operator_client: Client, engine: FakeEngine, case: str) -> None:
    post(operator_client, change="value", value="125", reason="r")
    row = EngineOperation.objects.get()
    report: object = row.pk
    if case == "kind":
        row.kind = "declare_state"
    elif case == "story":
        row.story_id = "01JM6ST0000000000000000002"
    elif case == "error":
        row.outcome = EngineOperation.Outcome.ERROR
    elif case == "nonint":
        report = "abc"
    elif case == "missing":
        report = row.pk + 1
    row.save()
    name = "trust" if case == "state" else "gold"
    html = get(operator_client, f"{written_url(name)}?report={report}")
    assert NOT_FOUND in html
    assert "Back to States" in html
    assert "state-written-lines" not in html


# --- refusals --------------------------------------------------------------------------------


def refuse(
    engine: FakeEngine,
    status: int,
    code: str,
    headers: dict[str, str] | None = None,
    **details: Any,
) -> None:
    engine.respond("PUT", ONE_STATE, status, json=error_json(code, **details), headers=headers)


def refused_page(client: Client, **fields: Any) -> str:
    data = {"change": "delta", "value": "7", "delta": "-150", "reason": "bought a ship"}
    data.update(fields)
    html = post(client, **data)
    assert 'class="state-set-form"' in html
    assert 'value="-150"' in field(html, "delta") or "delta" in fields
    assert 'value="bought a ship"' in field(html, "reason")
    assert hidden(html, "idempotency_key") != KEY
    return html


def test_constraint_violation(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        422,
        "state_constraint_violation",
        constraint="non_negative",
        attempted=-30,
        current=120,
    )
    html = refused_page(operator_client)
    assert refusal(html) == (
        "That breaks the State&#x27;s <code>non_negative</code> constraint "
        "(asked -30 gold, now 120 gold)"
    )
    unchanged(operator_client)


def test_unknown_character_beside_character(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("trust"))
    refuse(engine, 422, "validation_error", field="body.scope_key", reason="unknown_character")
    html = refused_page(operator_client, scope_key=OUTSIDER, delta="0.1")
    assert escape(UNKNOWN_CHARACTER) in field(html, "scope_key")
    unchanged(operator_client)


def test_undefined_delta_no_value_yet(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="delta", reason="undefined")
    html = refused_page(operator_client)
    assert NO_VALUE_YET in field(html, "delta")
    unchanged(operator_client)


@pytest.mark.parametrize(
    ("code", "text"),
    [
        (
            "state_derived",
            "This State is derived from other States — it can&#x27;t be set directly",
        ),
        (
            "state_unpermitted",
            "This State&#x27;s authorization doesn&#x27;t admit the consumer as a source",
        ),
        ("state_readonly_external", escape(READONLY_EXTERNAL_TEXT)),
    ],
)
def test_state_refusals(operator_client: Client, engine: FakeEngine, code: str, text: str) -> None:
    refuse(engine, 422, code, state="gold")
    html = refused_page(operator_client)
    assert refusal(html) == text
    unchanged(operator_client)


def test_reserved_state(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="state", reason="reserved_state")
    html = refused_page(operator_client)
    assert refusal(html) == "<code>world_clock</code> is the world clock — use the Clock section"


def test_story_busy(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 423, "story_busy", headers={"Retry-After": "5"}, reason="in_flight")
    html = refused_page(operator_client)
    assert refusal(html) == "Story busy — another turn is running · retry after 5 s"
    unchanged(operator_client)


def test_module_off_on_the_write(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "module_off", module="states")
    html = refused_page(operator_client)
    assert "states.level" in refusal(html)
    assert f'<a href="/stories/{STORY}/config/">Config</a>' in refusal(html)
    unchanged(operator_client)


def test_story_archived(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "story_archived")
    html = refused_page(operator_client)
    assert refusal(html) == "This story is archived — unarchive it to set a State."


def test_revision_conflict_refreshes_revision(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "revision_conflict")
    engine.respond("GET", ONE_STATE, 200, json={**state_view("gold"), "timeline_revision": 14})
    html = refused_page(operator_client, expected_revision="12")
    assert "changed" in refusal(html)
    assert hidden(html, "expected_revision") == "14"
    (put,) = puts(engine)
    assert put.headers["X-Epistrel-Expected-Revision"] == "12"
    unchanged(operator_client)


def test_idempotency_conflict_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "idempotency_conflict")
    html = refused_page(operator_client)
    assert "engine-error" in html.split('class="state-set-form"')[0]


def test_transport_error_keeps_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("PUT", ONE_STATE, httpx.ConnectError("down"))
    html = post(operator_client, change="value", value="1", reason="r", expected_revision="12")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "12"
    assert "engine-error" in html.split('class="state-set-form"')[0]


def test_write_404_not_found(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "not_found", state="gold")
    html = post(operator_client, change="value", value="1", reason="r")
    assert NOT_FOUND in html
    assert 'class="state-set-form"' not in html


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 503, "erasure_pending", headers={"Retry-After": "30"})
    response = operator_client.post(
        set_url(), {"idempotency_key": KEY, "change": "value", "value": "1", "reason": "r"}
    )
    html = response.content.decode()
    assert held_text("30") in html
    assert 'class="state-set-form"' not in html


# --- links in --------------------------------------------------------------------------------


def actions(html: str, name: str) -> str:
    found = re.search(
        rf'<tr id="state-{name}" class="state-row">.*?<td class="actions">(.*?)</td>', html, re.S
    )
    assert found is not None, name
    return found.group(1).strip()


def test_rows_link_to_set(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, STATES_URL)
    assert actions(html, "gold") == f'<a class="state-set" href="{set_url()}">Set…</a>'
    assert actions(html, "trust") == f'<a class="state-set" href="{set_url("trust")}">Set…</a>'
    assert (
        actions(html, "bank_balance")
        == '<span class="no-set" title="Owned by its provider">—</span>'
    )
    html = get(operator_client, f"/stories/{STORY}/inspect/world/state/gold/?as_of=3%2B1")
    assert f'<a class="state-set" href="{set_url()}">Set…</a>' in html

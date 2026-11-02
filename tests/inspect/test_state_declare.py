"""Declare a State between turns (#261): the page, the gate, the form and its body, the result and
report pages, and refusals.

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``: revision 12). ``m6/state_declared.json`` is #232's
declaration answer (``rope`` at turn 3⁺², revision 13); :func:`declared` renames it where a test
posts another name. Refusal bodies are built inline (D16)."""

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
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.inspect import SUBJECT_INVALID
from console.forms.states import (
    ALLOWED_DUPLICATE,
    ALLOWED_REQUIRED,
    BOOLEAN_INVALID,
    CHOOSE_TYPE,
    ENUM_ONLY,
    EXTERNAL_RULES,
    MIN_ABOVE_MAX,
    MINUTES_INVALID,
    NOT_ALLOWED,
    NUMBER_ONLY,
    PROVIDER_REQUIRED,
    PROVIDER_STORY_OWNED,
    STATE_NAME_INVALID,
)
from console.forms.world import NUMBER_INVALID
from console.held import held_text
from console.models import EngineOperation
from console.views import inspect as views
from console.views import inspect_state_declare as declare_views
from console.views.inspect_state_declare import ENTITY_UNKNOWN
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
ENTITY = "01JM6EN0000000000000000001"
KEY = "01JM6QP00000000000000000K1"
STATES = "/stories/{story_id}/states"
STATES_PATH = f"/stories/{STORY}/states"
STATES_URL = f"/stories/{STORY}/inspect/world/states/"
URL = f"/stories/{STORY}/inspect/world/states/new/"
DECLARED_URL = f"/stories/{STORY}/inspect/world/states/new/declared/"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
SHELL_PATHS = [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
WRITES = ("PUT", "POST", "PATCH", "DELETE")
BOXES = ("rules", "authorization", "transfer")
PROVIDERS_LINK = "Engine \u203a Providers"


def _reverse_except(missing: str) -> Any:
    """``reverse`` as if ``missing`` were unregistered (D19's unlinked rendering)."""

    def patched(viewname: str, *args: Any, **kwargs: Any) -> str:
        if viewname == missing:
            raise NoReverseMatch(missing)
        return reverse(viewname, *args, **kwargs)

    return patched


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m6/{name}"))
    return body


def declared(**definition: Any) -> dict[str, Any]:
    """``state_declared.json`` with ``definition`` laid over its stored definition."""
    body = fixture("state_declared")
    body["definition"].update(definition)
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
    fake.respond("POST", STATES, 201, json=declared(name="gold"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def declarations(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "POST" and r.url.path == STATES_PATH]


def writes(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method in WRITES]


def sent(engine: FakeEngine) -> dict[str, Any]:
    (declaration,) = declarations(engine)
    body: dict[str, Any] = json.loads(declaration.content)
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
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def errors(html: str, name: str) -> list[str]:
    found = re.search(r'<ul class="errorlist">(.*?)</ul>', field(html, name), re.S)
    if found is None:
        return []
    return [unescape(e) for e in re.findall(r"<li>(.*?)</li>", found.group(1), re.S)]


def hidden(html: str, name: str) -> str | None:
    found = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)"', html)
    return found.group(1) if found is not None else None


def refusal(html: str) -> str:
    found = re.search(
        r'<p class="errorlist state-declare-refusal" role="alert">(.*?)</p>', html, re.S
    )
    assert found is not None
    return found.group(1)


def is_form(html: str) -> bool:
    return 'class="state-declare-form"' in html


# --- the page and its link ----------------------------------------------------------------------


def test_states_header_links_declare(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, STATES_URL)
    count = re.search(r'<p class="states-count">(.*?)</p>', html, re.S)
    assert count is not None
    assert f'<a class="state-declare-link" href="{URL}">Declare a State…</a>' in count.group(1)


def test_get_makes_only_the_shell_reads(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert "<title>Inspect · Declare a State · " in html
    assert is_form(html)
    assert hidden(html, "expected_revision") == "12"
    assert re.fullmatch(r"[0-9A-Z]{26}", hidden(html, "idempotency_key") or "")
    assert '<button type="submit" class="state-declare-submit">Declare</button>' in html
    # The sub-navigation marks States current.
    assert 'aria-current="page"' in html.split("States</a>")[0].rsplit("<a", 1)[1]


def test_fieldsets_and_order(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    legends = re.findall(r"<legend>(.*?)</legend>", html)
    assert legends == [
        "Identity",
        "Shown as",
        "Owner",
        "Constraints",
        "Initial value",
        "Effect rules",
    ]
    constraints = html.split("<legend>Constraints</legend>")[1].split("</fieldset>")[0]
    names = re.findall(r'class="field field-([a-z_]+)"', constraints)
    assert names == [
        "constraints_min",
        "constraints_max",
        "constraints_tolerance",
        "constraints_non_negative",
        "constraints_allowed_values",
    ]
    for attr in (" min=", " max=", "maxlength"):
        assert attr not in html.split('class="state-declare-form"')[1]


def test_type_and_choice_labels(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    labels = re.findall(r"<option [^>]*>([^<]*)</option>", field(html, "type"))
    assert labels == ["Choose a type", "Number", "Boolean", "Enum", "Text"]
    assert "Engine default (story)" in field(html, "scope")
    assert "Per character" in field(html, "scope")
    assert "Engine default (player visible)" in field(html, "visibility")
    assert "Engine default (story)" in field(html, "owner")
    assert "Engine default (false)" in field(html, "silent")


def test_expected_revision_empty_without_story(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", STORY_PATH, httpx.ConnectError("down"))
    html = get(operator_client)
    assert is_form(html)
    assert hidden(html, "expected_revision") is None
    html = post(operator_client, name="gold", type="number", expected_revision="")
    assert "Declared gold" in html
    (declaration,) = declarations(engine)
    assert "X-Epistrel-Expected-Revision" not in declaration.headers


def test_provider_help_links_providers(operator_client: Client, engine: FakeEngine) -> None:
    help_text = field(get(operator_client), "provider_id")
    assert "A registered provider&#x27;s name" in help_text
    assert f'<a href="/engine/providers/">{PROVIDERS_LINK}</a>' in help_text


def test_provider_help_plain_without_providers(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D19: while ``engine:providers`` doesn't resolve, the help is plain text."""

    monkeypatch.setattr(declare_views, "reverse", _reverse_except("engine:providers"))
    help_text = field(get(operator_client), "provider_id")
    assert "A registered provider&#x27;s name" in help_text
    assert PROVIDERS_LINK not in help_text
    assert "<a " not in help_text


# --- the gate -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [lite_m6(), structured_v3(states__level="off")],
    ids=["lite_m6", "states_off"],
)
def test_gate_closed_zero_calls(
    operator_client: Client, engine: FakeEngine, config: dict[str, Any]
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=config)
    html = get(operator_client)
    assert "states.level" in html
    assert '<p class="notice unavailable">' in html
    assert not is_form(html)
    html = post(operator_client, name="gold", type="number", unit="coins")
    assert "states.level" in refusal(html)
    assert is_form(html)
    assert 'value="gold"' in field(html, "name")
    assert 'value="coins"' in field(html, "unit")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "12"
    assert '<ul class="errorlist"' not in html
    assert declarations(engine) == []
    assert writes(engine) == []


def test_gate_prompt_only_posts(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6(**{"states.level": "prompt_only"}))
    html = post(operator_client, name="gold", type="number")
    assert "Declared gold" in html
    assert len(declarations(engine)) == 1


# --- the body -----------------------------------------------------------------------------------


def test_minimal_body(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, name="  gold ", type="number")
    assert sent(engine) == {"definition": {"name": "gold", "type": "number"}}
    (declaration,) = declarations(engine)
    assert declaration.headers["X-Epistrel-Expected-Revision"] == "12"
    assert declaration.headers["Idempotency-Key"] == KEY


def test_full_body(operator_client: Client, engine: FakeEngine) -> None:
    post(
        operator_client,
        name="gold",
        type="number",
        scope="character",
        entity_id=f" {ENTITY.lower()} ",
        unit=" coins ",
        aliases="purse\n\n  coin  \nmoney\n",
        visibility="hidden",
        silent="true",
        owner="story",
        constraints_min="0",
        constraints_max="1000",
        constraints_tolerance="0.5",
        constraints_non_negative="true",
        initial_value="12",
        rules='{"derive": {"from": ["silver"]}}',
        authorization='{"sources": ["consumer"]}',
        transfer="{}",
        reservation_expiry_minutes="030",
    )
    body = sent(engine)
    assert list(body) == ["definition"]
    definition = body["definition"]
    assert definition == {
        "name": "gold",
        "type": "number",
        "scope": "character",
        "constraints": {"min": 0, "max": 1000, "non_negative": True, "tolerance": 0.5},
        "initial_value": 12,
        "aliases": ["purse", "coin", "money"],
        "unit": "coins",
        "entity_id": ENTITY,
        "visibility": "hidden",
        "owner": "story",
        "silent": True,
        "rules": {"derive": {"from": ["silver"]}},
        "authorization": {"sources": ["consumer"]},
        "transfer": {},
        "reservation_expiry_minutes": 30,
    }
    assert list(definition) == list(
        [
            "name",
            "type",
            "scope",
            "constraints",
            "initial_value",
            "aliases",
            "unit",
            "entity_id",
            "visibility",
            "owner",
            "silent",
            "rules",
            "authorization",
            "transfer",
            "reservation_expiry_minutes",
        ]
    )


def test_constraints_only_given_members(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, name="gold", type="number", constraints_max="10")
    assert sent(engine)["definition"]["constraints"] == {"max": 10}


def test_engine_default_choices_send_nothing(operator_client: Client, engine: FakeEngine) -> None:
    post(
        operator_client,
        name="gold",
        type="number",
        scope="",
        visibility="",
        owner="",
        silent="",
        constraints_non_negative="",
        rules="",
        authorization="  ",
        transfer="",
        unit="  ",
        aliases="\n \n",
        initial_value="   ",
        reservation_expiry_minutes="",
    )
    assert sent(engine) == {"definition": {"name": "gold", "type": "number"}}


def test_explicit_default_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    post(
        operator_client,
        name="gold",
        type="number",
        scope="story",
        visibility="player_visible",
        owner="story",
        silent="false",
        constraints_non_negative="false",
    )
    assert sent(engine)["definition"] == {
        "name": "gold",
        "type": "number",
        "scope": "story",
        "constraints": {"non_negative": False},
        "visibility": "player_visible",
        "owner": "story",
        "silent": False,
    }


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"type": "number", "initial_value": "3"}, 3),
        ({"type": "number", "initial_value": " -2.5 "}, -2.5),
        ({"type": "boolean", "initial_value": "true"}, True),
        ({"type": "boolean", "initial_value": "false"}, False),
        (
            {"type": "enum", "constraints_allowed_values": "open\nshut", "initial_value": " shut "},
            "shut",
        ),
        ({"type": "text", "initial_value": "  a lamp  "}, "  a lamp  "),
    ],
)
def test_initial_value_typed_by_type(
    operator_client: Client, engine: FakeEngine, fields: dict[str, str], expected: Any
) -> None:
    post(operator_client, name="lamp", **fields)
    value = sent(engine)["definition"]["initial_value"]
    assert value == expected
    assert type(value) is type(expected)


def test_enum_body(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, name="door", type="enum", constraints_allowed_values=" open \nshut\n")
    assert sent(engine)["definition"] == {
        "name": "door",
        "type": "enum",
        "constraints": {"allowed_values": ["open", "shut"]},
    }


def test_never_sends_external_value_name(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        STATES,
        201,
        json=declared(
            name="bank_balance",
            owner="external",
            provider_id="bank",
            external_value_name="bank_balance",
            rules={},
        ),
    )
    post(
        operator_client,
        name="bank_balance",
        type="number",
        owner="external",
        provider_id="bank",
        external_value_name="other",
        authorization='{"sources": ["provider"]}',
        rules="{}",
    )
    definition = sent(engine)["definition"]
    assert "external_value_name" not in definition
    assert definition["owner"] == "external"
    assert definition["provider_id"] == "bank"
    assert definition["rules"] == {}
    assert definition["authorization"] == {"sources": ["provider"]}


# --- local checks: zero POST …/states -----------------------------------------------------------


def refused_locally(client: Client, engine: FakeEngine, **fields: Any) -> str:
    html = post(client, **fields)
    assert is_form(html)
    assert declarations(engine) == []
    # D8: local invalid input keeps the posted key and revision.
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "12"
    return html


@pytest.mark.parametrize("name", ["9lives", "a-b", "a" * 81])
def test_name_pattern(operator_client: Client, engine: FakeEngine, name: str) -> None:
    html = refused_locally(operator_client, engine, name=name, type="number")
    assert errors(html, "name") == [STATE_NAME_INVALID]
    assert escape(name) in field(html, "name")


def test_name_required(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(operator_client, engine, name="  ", type="number")
    assert errors(html, "name") == ["This field is required."]


def test_world_clock_left_to_engine(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        STATES,
        422,
        json=error_json("validation_error", field="name", reason="reserved_state"),
    )
    html = post(operator_client, name="world_clock", type="number")
    assert len(declarations(engine)) == 1
    assert "<code>world_clock</code> is the world clock — use the Clock section" in field(
        html, "name"
    )


def test_type_required(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(operator_client, engine, name="gold", type="")
    assert errors(html, "type") == [CHOOSE_TYPE]


def test_enum_needs_allowed_values(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(operator_client, engine, name="door", type="enum")
    assert errors(html, "constraints_allowed_values") == [ALLOWED_REQUIRED]


def test_allowed_values_duplicate(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(
        operator_client, engine, name="door", type="enum", constraints_allowed_values="a\n a \nb"
    )
    assert errors(html, "constraints_allowed_values") == [ALLOWED_DUPLICATE]


def test_allowed_values_on_non_enum(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(
        operator_client, engine, name="door", type="text", constraints_allowed_values="a\na"
    )
    assert errors(html, "constraints_allowed_values") == [ENUM_ONLY]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("constraints_min", "0"),
        ("constraints_max", "5"),
        ("constraints_tolerance", "0.1"),
        ("constraints_non_negative", "true"),
    ],
)
def test_bounds_on_non_number(
    operator_client: Client, engine: FakeEngine, name: str, value: str
) -> None:
    html = refused_locally(operator_client, engine, **{"name": "lamp", "type": "text", name: value})
    assert errors(html, name) == [NUMBER_ONLY]


def test_non_negative_false_on_non_number_allowed(
    operator_client: Client, engine: FakeEngine
) -> None:
    post(operator_client, name="lamp", type="text", constraints_non_negative="false")
    assert sent(engine)["definition"]["constraints"] == {"non_negative": False}


def test_bounds_number_invalid(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(
        operator_client, engine, name="gold", type="number", constraints_min="1e3"
    )
    assert errors(html, "constraints_min") == [NUMBER_INVALID]


def test_min_above_max(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(
        operator_client,
        engine,
        name="gold",
        type="number",
        constraints_min="5",
        constraints_max="2",
    )
    assert errors(html, "constraints_min") == [MIN_ABOVE_MAX]
    assert errors(html, "constraints_max") == []


def test_min_equal_max_allowed(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, name="gold", type="number", constraints_min="2", constraints_max="2")
    assert sent(engine)["definition"]["constraints"] == {"min": 2, "max": 2}


def test_initial_value_not_allowed(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(
        operator_client,
        engine,
        name="door",
        type="enum",
        constraints_allowed_values="open\nshut",
        initial_value="Open",
    )
    assert errors(html, "initial_value") == [NOT_ALLOWED]


def test_initial_boolean_invalid(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(operator_client, engine, name="lit", type="boolean", initial_value="yes")
    assert errors(html, "initial_value") == [BOOLEAN_INVALID]


def test_initial_number_invalid(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(operator_client, engine, name="gold", type="number", initial_value="x")
    assert errors(html, "initial_value") == [NUMBER_INVALID]


def test_initial_text_too_long(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(
        operator_client, engine, name="note", type="text", initial_value="x" * 201
    )
    assert errors(html, "initial_value") == ["At most 200 characters."]


def test_external_needs_provider(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(operator_client, engine, name="gold", type="number", owner="external")
    assert errors(html, "provider_id") == [PROVIDER_REQUIRED]


@pytest.mark.parametrize("owner", ["", "story"])
def test_provider_needs_external(operator_client: Client, engine: FakeEngine, owner: str) -> None:
    html = refused_locally(
        operator_client, engine, name="gold", type="number", owner=owner, provider_id="bank"
    )
    assert errors(html, "provider_id") == [PROVIDER_STORY_OWNED]


def test_external_with_rules(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(
        operator_client,
        engine,
        name="gold",
        type="number",
        owner="external",
        provider_id="bank",
        rules='{"derive": {}}',
    )
    assert errors(html, "rules") == [EXTERNAL_RULES]


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("text", ['{"a": 1, "a": 2}', "[1, 2]", '{"a": NaN}'])
def test_bad_json_box(operator_client: Client, engine: FakeEngine, box: str, text: str) -> None:
    html = refused_locally(operator_client, engine, **{"name": "gold", "type": "number", box: text})
    (error,) = errors(html, box)
    assert error.startswith("Enter valid JSON") or error == "Enter a JSON object ({…})."


@pytest.mark.parametrize("value", ["-5", "1.5", "\uff15"])
def test_minutes_invalid(operator_client: Client, engine: FakeEngine, value: str) -> None:
    html = refused_locally(
        operator_client, engine, name="gold", type="number", reservation_expiry_minutes=value
    )
    assert errors(html, "reservation_expiry_minutes") == [MINUTES_INVALID]


def test_entity_id_invalid(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_locally(operator_client, engine, name="gold", type="number", entity_id="abc")
    assert errors(html, "entity_id") == [SUBJECT_INVALID]


def test_type_dependent_checks_skipped_without_type(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = refused_locally(
        operator_client,
        engine,
        name="gold",
        type="",
        constraints_min="0",
        initial_value="yes",
        constraints_allowed_values="a",
        owner="external",
    )
    assert errors(html, "type") == [CHOOSE_TYPE]
    assert errors(html, "constraints_min") == []
    assert errors(html, "initial_value") == []
    assert errors(html, "constraints_allowed_values") == []
    assert errors(html, "provider_id") == [PROVIDER_REQUIRED]  # type-independent


# --- the result page ----------------------------------------------------------------------------


def lines(html: str) -> list[str]:
    found = re.search(r'<ul class="state-declared-lines">(.*?)</ul>', html, re.S)
    assert found is not None
    return [
        unescape(re.sub(r"<[^>]+>", "", item)).strip()
        for item in re.findall(r"<li[^>]*>(.*?)</li>", found.group(1), re.S)
    ]


def definition_list(html: str) -> list[tuple[str, str]]:
    found = re.search(r'<dl class="state-definition">(.*?)</dl>', html, re.S)
    assert found is not None
    return [
        (unescape(dt), unescape(re.sub(r"<[^>]+>", "", dd)).strip())
        for dt, dd in re.findall(r"<dt>(.*?)</dt><dd[^>]*>(.*?)</dd>", found.group(1), re.S)
    ]


def test_result_page_lines(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, name="gold", type="number")
    row = EngineOperation.objects.get()
    assert row.kind == "declare_state"
    assert '<h3 class="state-declared-title">Declared gold</h3>' in html
    assert "<title>Inspect · Declared gold · " in html
    assert lines(html) == [
        "Slot: turn 3⁺² · start",
        "Operation: 01JM6QP0000000000000000012",
        "Story revision now 13",
    ]
    assert re.search(r'<li class="slot">Slot: <a href="[^"]+">', html)
    assert f'href="/operations/{row.pk}/"' in html
    assert definition_list(html) == [
        ("Name", "gold"),
        ("Type", "Number"),
        ("Scope", "Story"),
        ("Visibility", "Player visible"),
        ("Owner", "Story"),
        ("Provider id", "—"),
        ("External value name", "—"),
        ("Unit", "coils"),
        ("Aliases", "—"),
        ("Entity id", "—"),
        ("Constraints", '{\n  "non_negative": true\n}'),
        ("Initial value", "3 coils"),
        ("Silent", "false"),
        ("Reservation expiry minutes", "—"),
        ("Rules", "—"),
        ("Authorization", "—"),
        ("Transfer", "—"),
        ("Created by", "Consumer"),
    ]
    links = html.split('<p class="declared-links">')[1].split("</p>")[0]
    see = f'<a class="see-state" href="/stories/{STORY}/inspect/world/state/gold/">See gold</a>'
    assert see in links
    assert (
        f'<a class="set-value" href="/stories/{STORY}/inspect/world/state/gold/set/">'
        "Set its value</a>"
    ) in links
    assert f'<a class="declare-another" href="{URL}">Declare another</a>' in links
    assert f'href="{STATES_URL}">Back to States</a>' in links
    before, more = html.split('<details class="state-declared-more">')
    assert "3⁺2" not in before
    assert "3⁺2" in more
    assert "Replayed" not in html


def test_definition_other_members_and_values(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        STATES,
        201,
        json=declared(
            name="gold",
            aliases=["purse", "coin"],
            entity_id=ENTITY,
            initial_value=None,
            rules={"derive": {"from": ["silver"]}},
            silent=True,
            id="01JM6SD0000000000000000001",
        ),
    )
    rows = dict(definition_list(post(operator_client, name="gold", type="number")))
    assert rows["Aliases"] == "purse, coin"
    assert rows["Entity id"] == ENTITY
    assert rows["Initial value"] == "undefined"
    assert rows["Silent"] == "true"
    assert rows["Rules"] == '{\n  "derive": {\n    "from": [\n      "silver"\n    ]\n  }\n}'
    assert list(rows)[-1] == "Id"
    assert rows["Id"] == "01JM6SD0000000000000000001"


def test_heading_falls_back_to_posted_name(operator_client: Client, engine: FakeEngine) -> None:
    body = declared()
    del body["definition"]["name"]
    engine.respond("POST", STATES, 201, json=body)
    html = post(operator_client, name="gold", type="number")
    assert "Declared gold</h3>" in html
    assert dict(definition_list(html))["Name"] == "—"


def test_external_value_name_line(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        STATES,
        201,
        json=declared(
            name="bank_balance",
            owner="external",
            provider_id="bank",
            external_value_name="bank_balance",
        ),
    )
    html = post(
        operator_client, name="bank_balance", type="number", owner="external", provider_id="bank"
    )
    rows = dict(definition_list(html))
    assert rows["External value name"] == "bank_balance (the provider's value of this name)"
    assert rows["Owner"] == "External"
    assert rows["Provider id"] == "bank"
    # An external State's result page offers no "Set its value".
    assert "Set its value" not in html
    assert "See bank_balance" in html


def test_replayed_renders_the_same_page(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", STATES, 201, json=declared(name="gold"), headers={"Idempotent-Replay": "true"}
    )
    html = post(operator_client, name="gold", type="number")
    assert "Declared gold</h3>" in html
    assert "Replayed" not in html.split('<details class="state-declared-more">')[0]


def test_report_page_rebuilds_from_row(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, name="gold", type="number")
    row = EngineOperation.objects.get()
    engine.requests.clear()
    html = get(operator_client, f"{DECLARED_URL}?report={row.pk}")
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert "Declared gold</h3>" in html
    assert lines(html) == [
        "Slot: turn 3⁺² · start",
        "Operation: 01JM6QP0000000000000000012",
        "Story revision now 13",
    ]
    assert dict(definition_list(html))["Unit"] == "coils"


def test_report_page_not_gated(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, name="gold", type="number")
    row = EngineOperation.objects.get()
    engine.respond("GET", EFFECTIVE, 200, json=structured_v3(states__level="off"))
    assert "Declared gold</h3>" in get(operator_client, f"{DECLARED_URL}?report={row.pk}")


@pytest.mark.parametrize("case", ["kind", "story", "error", "garbage", "missing", "unparsed"])
def test_report_page_complement(operator_client: Client, engine: FakeEngine, case: str) -> None:
    post(operator_client, name="gold", type="number")
    row = EngineOperation.objects.get()
    report: object = row.pk
    if case == "kind":
        row.kind = "set_state"
    elif case == "story":
        row.story_id = "01JM6ST0000000000000000002"
    elif case == "error":
        row.outcome = EngineOperation.Outcome.ERROR
    elif case == "garbage":
        report = "abc"
    elif case == "missing":
        report = row.pk + 1
    elif case == "unparsed":
        row.response = {"definition": "nope"}
    row.save()
    html = get(operator_client, f"{DECLARED_URL}?report={report}")
    assert NOT_FOUND in html
    assert "Back to States" in html
    assert "state-declared-lines" not in html


# --- refusals -----------------------------------------------------------------------------------


def refuse(
    engine: FakeEngine,
    status: int,
    code: str,
    headers: dict[str, str] | None = None,
    message: str = "refused",
    **details: Any,
) -> None:
    engine.respond(
        "POST", STATES, status, json=error_json(code, message, **details), headers=headers
    )


REFUSED_FIELDS = {
    "name": "gold",
    "type": "number",
    "unit": "coins",
    "aliases": "purse\ncoin",
    "constraints_min": "0",
    "constraints_max": "100",
    "initial_value": "5",
    "rules": '{"derive": {}}',
}


def refused_page(client: Client, engine: FakeEngine, **fields: Any) -> str:
    """A POST the Engine refuses: the form again, every input kept, a new key."""
    data = {**REFUSED_FIELDS, **fields}
    html = post(client, **data)
    assert is_form(html)
    assert len(declarations(engine)) == 1
    assert 'value="gold"' in field(html, "name") or "name" in fields
    assert 'value="coins"' in field(html, "unit")
    assert "purse\ncoin</textarea>" in field(html, "aliases")
    assert 'value="100"' in field(html, "constraints_max")
    assert 'value="5"' in field(html, "initial_value")
    assert escape(data["rules"]) + "</textarea>" in field(html, "rules")
    assert 'value="number" selected' in field(html, "type")
    assert hidden(html, "idempotency_key") not in (None, KEY)
    return html


def test_state_name_taken(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "state_name_taken", state="gold")
    html = refused_page(operator_client, engine)
    assert errors(html, "name") == ["A State named “gold” already exists"]


def test_reserved_state(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="name", reason="reserved_state")
    html = refused_page(operator_client, engine)
    assert "<code>world_clock</code> is the world clock — use the Clock section" in field(
        html, "name"
    )


def test_unknown_provider(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="provider_id", reason="unknown_provider")
    html = refused_page(operator_client, engine, owner="external", provider_id="bank", rules="{}")
    assert errors(html, "provider_id") == ["No provider is registered as “bank”"]


def test_unknown_value(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="external_value_name", reason="unknown_value")
    html = refused_page(operator_client, engine, owner="external", provider_id="bank", rules="{}")
    assert errors(html, "name") == [
        "bank owns no value named “gold” — an external State takes its provider's value of "
        "its own name"
    ]


def test_unknown_entity(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="entity_id", reason="unknown_entity")
    html = refused_page(operator_client, engine, entity_id=ENTITY)
    assert errors(html, "entity_id") == [ENTITY_UNKNOWN]


def test_pydantic_path_on_max(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        422,
        "validation_error",
        errors=[{"field": "body.definition.constraints.max", "message": "not a number"}],
    )
    html = refused_page(operator_client, engine)
    assert errors(html, "constraints_max") == ["not a number"]


def test_engine_bounds_on_min(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        422,
        "validation_error",
        message="min is above max",
        field="constraints.min",
        reason="bounds",
    )
    html = refused_page(operator_client, engine)
    assert errors(html, "constraints_min") == ["min is above max"]


def test_box_sub_path_on_box(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        422,
        "validation_error",
        errors=[{"field": "body.definition.rules.derive.from", "message": "field required"}],
    )
    html = refused_page(operator_client, engine)
    assert errors(html, "rules") == ["derive.from: field required"]


def test_state_unpermitted(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "state_unpermitted", state="gold")
    html = refused_page(operator_client, engine)
    assert refusal(html) == (
        "This State&#x27;s authorization doesn&#x27;t admit the consumer as a source"
    )


def test_constraint_violation(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        422,
        "state_constraint_violation",
        constraint="max",
        attempted=500,
        current=None,
    )
    html = refused_page(operator_client, engine)
    assert "<code>max</code>" in refusal(html)


def test_story_busy(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 423, "story_busy", headers={"Retry-After": "5"}, reason="in_flight")
    html = refused_page(operator_client, engine)
    assert refusal(html) == "Story busy — another turn is running · retry after 5 s"


def test_module_off_on_the_write(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "module_off", module="states")
    html = refused_page(operator_client, engine)
    assert "states.level" in refusal(html)
    assert f'<a href="/stories/{STORY}/config/">Config</a>' in refusal(html)


def test_story_archived(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "story_archived")
    html = refused_page(operator_client, engine)
    assert refusal(html) == "This story is archived — unarchive it to declare a State."


def test_revision_conflict(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "revision_conflict")
    engine.respond("GET", STORY_PATH, 200, json={**fixture("story"), "timeline_revision": 14})
    html = refused_page(operator_client, engine, expected_revision="12")
    assert "changed" in refusal(html)
    assert hidden(html, "expected_revision") == "14"
    (declaration,) = declarations(engine)
    assert declaration.headers["X-Epistrel-Expected-Revision"] == "12"


def test_idempotency_conflict_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "idempotency_conflict")
    html = refused_page(operator_client, engine)
    assert "engine-error" in html.split('class="state-declare-form"')[0]


def test_unknown_outcome_keeps_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", STATES, httpx.ConnectError("down"))
    html = post(operator_client, name="gold", type="number", unit="coins")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "12"
    assert 'value="coins"' in field(html, "unit")
    assert "engine-error" in html.split('class="state-declare-form"')[0]


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 503, "erasure_pending", headers={"Retry-After": "30"})
    response = operator_client.post(URL, {"idempotency_key": KEY, "name": "gold", "type": "number"})
    html = response.content.decode()
    assert held_text("30") in html
    assert not is_form(html)


def test_refused_declaration_changes_nothing(operator_client: Client, engine: FakeEngine) -> None:
    before = re.findall(r'<tr id="state-([a-z_]+)"', get(operator_client, STATES_URL))
    refuse(engine, 422, "state_name_taken", state="gold")
    refused_page(operator_client, engine)
    after = re.findall(r'<tr id="state-([a-z_]+)"', get(operator_client, STATES_URL))
    assert after == before
    assert "gold" not in [n for n in after if n not in before]
    assert len(declarations(engine)) == 1

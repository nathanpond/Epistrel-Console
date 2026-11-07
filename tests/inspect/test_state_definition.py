"""Change a State's definition (#265, rebuilt on the definition read by #329):
``inspect:state-definition`` and ``PATCH /stories/{id}/states/{name}/definition``.

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast. The page reads ``gold`` from #292's ``m7/state_definition.json``, with the
inline variant of :func:`definition_body` (number, unit "gold", player visible, created by the
author, constraints ``{"max": 500, "non_negative": true}``, ``config_revision`` 30); a save answers
#265's ``m6/state_definition_written.json`` (``config_revision`` 6). Each test posts back what the
rendered form holds (:func:`form_data`, scraped from the HTML), with its changes. Other variants
and refusal bodies are built inline (D16)."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from html.parser import HTMLParser
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.core import signing
from django.db import connection
from django.http import HttpRequest
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.html import escape

import console.states
from console.engine.client import MUTATION_TIMEOUT_S, EngineClient
from console.engine_access import engine_client_for
from console.forms.states import (
    MINUTES_INVALID,
    MUTABLE_MEMBERS,
    NOTHING_CHANGED,
    PATCH_BOX_HELP,
    RULES_KIND,
    SNAPSHOT_INVALID,
    read_definition_snapshot,
    sign_definition_snapshot,
)
from console.held import held_text
from console.models import EngineOperation
from console.states import UNCHANGED_SINCE_DECLARED, last_changed_text
from console.views import inspect as views
from console.views import inspect_clock, inspect_state_definition
from console.views.inspect_state_definition import UNREADABLE_SAVED
from console.world import ENGINE_NOTHING_CHANGED
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, full_v4, lite_m6, state_view, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
DEFINITION = "/stories/{story_id}/states/{name}/definition"
GOLD_PATH = f"/stories/{STORY}/states/gold"
DEFINITION_PATH = f"{GOLD_PATH}/definition"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
V1_SALT = "console.states.definition.snapshot"
GOLD_CONSTRAINTS = {"max": 500, "non_negative": True}


def url(name: str = "gold") -> str:
    return f"/stories/{STORY}/inspect/world/state/{name}/definition/"


STATE_URL = f"/stories/{STORY}/inspect/world/state/gold/"


def definition_body(*, config_revision: Any = 30, **members: Any) -> dict[str, Any]:
    """#292's ``m7/state_definition.json`` with this suite's ``gold`` (see the module
    docstring), then ``members`` over its ``definition`` (``None`` removes one)."""
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m7/state_definition"))
    body["created_by"] = "author"
    body["config_revision"] = config_revision
    definition = body["definition"]
    definition.update(visibility="player_visible", unit="gold", constraints=dict(GOLD_CONSTRAINTS))
    for member, value in members.items():
        if value is None:
            definition.pop(member, None)
        else:
            definition[member] = value
    return body


def serve(engine: FakeEngine, body: dict[str, Any]) -> None:
    """Serve ``body`` unvalidated (``definition`` is a free-form object; an odd
    ``config_revision`` would fail the schema)."""
    engine.side_effect("GET", DEFINITION, lambda request: httpx.Response(200, json=body))


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v3())
    fake.respond("GET", STORY_PATH, 200, json=engine_fixture("m6/story"))
    fake.respond("GET", DEFINITION, 200, json=definition_body())
    fake.respond("PATCH", DEFINITION, 200, json=engine_fixture("m6/state_definition_written"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


class _FormScraper(HTMLParser):
    """The definition form's successful controls: inputs (a checkbox only when checked), the
    selected option of each select and each textarea's text."""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, str] = {}
        self._in_form = False
        self._select: str | None = None
        self._textarea: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "form" and "state-definition-form" in a.get("class", ""):
            self._in_form = True
        if not self._in_form:
            return
        if tag == "input" and a.get("name"):
            if a.get("type") == "checkbox":
                if "checked" in a:
                    self.data[a["name"]] = a.get("value", "on")
            elif a.get("type") != "submit":
                self.data[a["name"]] = a.get("value", "")
        elif tag == "select":
            self._select = a.get("name")
        elif tag == "option" and self._select is not None:
            self.data.setdefault(self._select, a.get("value", ""))
            if "selected" in a:
                self.data[self._select] = a.get("value", "")
        elif tag == "textarea":
            self._textarea = a.get("name")
            self.data[self._textarea or ""] = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data: str) -> None:
        if self._textarea is not None:
            self.data[self._textarea] += data


def form_data(html: str, **changes: str) -> dict[str, str]:
    scraper = _FormScraper()
    scraper.feed(html)
    data = {k: v.removeprefix("\n") for k, v in scraper.data.items()}
    data.update(changes)
    return data


def get(client: Client, name: str = "gold") -> str:
    response = client.get(url(name))
    assert response.status_code == 200
    return response.content.decode()


def post(client: Client, data: dict[str, str], name: str = "gold") -> Any:
    return client.post(url(name), data)


def page(response: Any) -> str:
    assert response.status_code == 200
    html: str = response.content.decode()
    return html


def patches(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "PATCH"]


def sent(engine: FakeEngine) -> Any:
    (request,) = patches(engine)
    return json.loads(request.content)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def field(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def refusal(html: str) -> str:
    found = re.search(
        r'<p class="errorlist state-definition-refusal" role="alert">(.*?)</p>', html, re.S
    )
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


def change(client: Client, engine: FakeEngine, **changes: str) -> Any:
    """GET the page, then POST its rendered values with ``changes``; the PATCH body."""
    response = post(client, form_data(get(client), **changes))
    assert response.status_code == 302, response.content.decode()[:500]
    return sent(engine)


def definition_reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "GET" and r.url.path == DEFINITION_PATH]


def panel(html: str) -> str:
    found = re.search(r'<section class="stored-definition">(.*?)</section>', html, re.S)
    assert found is not None
    return found.group(1)


def unchanged_from_read(client: Client, engine: FakeEngine, data: dict[str, str]) -> None:
    html = page(post(client, data))
    assert NOTHING_CHANGED in html
    assert 'class="state-definition-form"' in html
    assert not patches(engine)


# --- the read and the stored definition --------------------------------------------------


@pytest.mark.parametrize("config", [structured_v3, full_v4])
def test_reads_the_definition_once(
    operator_client: Client, engine: FakeEngine, config: Any
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=config())
    with CaptureQueriesContext(connection) as queries:
        html = get(operator_client)
        assert len(definition_reads(engine)) == 1
        post(operator_client, form_data(html, unit="coins"))
    assert len(definition_reads(engine)) == 2
    assert all(r.url.params["viewpoint"] == "omniscient" for r in definition_reads(engine))
    assert [(r.method, r.url.path) for r in engine.requests[:3]] == [
        ("GET", f"/stories/{STORY}/effective-config"),
        ("GET", f"/stories/{STORY}"),
        ("GET", DEFINITION_PATH),
    ]
    # Complement: no State read and no local #44 definition query (the save's own #44 write
    # is the only SQL on that table).
    assert not [r for r in engine.requests if r.url.path == GOLD_PATH]
    table = EngineOperation._meta.db_table
    reads = [q["sql"] for q in queries.captured_queries if table in q["sql"]]
    assert not [sql for sql in reads if sql.lstrip().upper().startswith("SELECT")]
    assert not hasattr(console.states, "last_recorded_definition")


def test_stored_definition_panel(operator_client: Client, engine: FakeEngine) -> None:
    shown = panel(get(operator_client))
    assert "<h4>Stored definition</h4>" in shown
    assert '<dd class="member-type">Number</dd>' in shown
    assert '<dd class="member-scope">Story</dd>' in shown
    assert '<dd class="member-owner">Story</dd>' in shown
    assert '<dd class="member-initial_value">120 gold</dd>' in shown
    assert 'class="member-provider_id"' in shown
    assert 'class="member-created_by"' not in shown  # shown once, on the line below
    assert '<p class="definition-created-by">Created by Author</p>' in shown
    assert (
        '<p class="definition-last-changed">Last changed at configuration revision 30</p>'
    ) in shown
    serve(engine, definition_body(config_revision=None))
    assert '<p class="definition-last-changed">Unchanged since it was declared</p>' in panel(
        get(operator_client)
    )


@pytest.mark.parametrize("odd", [True, "7", 1.5])
def test_odd_config_revision(operator_client: Client, engine: FakeEngine, odd: Any) -> None:
    # The typed read refuses it (#43's panel, no form); the page's own helpers read anything
    # but an int as null.
    serve(engine, definition_body(config_revision=odd))
    html = get(operator_client)
    assert "engine-error" in html
    assert 'class="state-definition-form"' not in html
    assert last_changed_text(odd) == UNCHANGED_SINCE_DECLARED
    signed = sign_definition_snapshot("gold", definition_body()["definition"], odd)
    snapshot = read_definition_snapshot(signed, "gold")
    assert snapshot is not None
    assert snapshot["config_revision"] is None


def test_last_changed_thousands(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, definition_body(config_revision=1200))
    assert "Last changed at configuration revision 1,200" in panel(get(operator_client))


def test_missing_created_by_reads_dash(operator_client: Client, engine: FakeEngine) -> None:
    body = definition_body()
    body["created_by"] = ""
    serve(engine, body)
    assert '<p class="definition-created-by">Created by —</p>' in panel(get(operator_client))


def test_no_unreadable_notice_or_last_recorded(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert "definition-unreadable" not in html
    assert "recorded-definition" not in html
    assert "#680" not in html
    assert "state-identity" not in html
    assert "Change gold&#x27;s definition</h3>" in html
    assert "turns checked under the old ones are re-checked" in html
    for gone in (
        "DEFINITION_UNREADABLE",
        "definition_unreadable_html",
        "STATE_DEFINITION_REQUEST",
        "STATE_DEFINITION_REQUEST_LABEL",
        "RecordedDefinition",
        "NO_RECORDED_DEFINITION",
        "RECORDED_HEADING",
        "DEFINITION_KINDS",
        "no_recorded_definition_text",
    ):
        assert not hasattr(console.states, gone), gone
    assert not hasattr(inspect_state_definition, "AUTHORIZATION_REDACTED")


def test_unknown_state_not_found_page(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", DEFINITION, 404, json=error_json("not_found", state="silver"))
    for response in (
        operator_client.get(url("silver")),
        operator_client.post(url("silver"), {"unit": "x", "visibility": "hidden"}),
    ):
        html = page(response)
        assert NOT_FOUND in html
        assert 'class="state-definition-form"' not in html
        assert "stored-definition" not in html
    assert not patches(engine)


def test_read_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", DEFINITION, httpx.ConnectError("down"))
    html = get(operator_client)
    assert "engine-error" in html
    assert 'class="state-definition-form"' not in html


def test_invalid_name_django_404(operator_client: Client, engine: FakeEngine) -> None:
    for bad in ("9gold", "go-ld", "_gold"):
        assert operator_client.get(url(bad)).status_code == 404
    assert not engine.requests


@pytest.mark.parametrize("config", ["lite", "off"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_gate_closed_zero_calls(
    operator_client: Client, engine: FakeEngine, config: str, method: str
) -> None:
    data = form_data(get(operator_client), unit="coins")
    engine.requests.clear()
    body = lite_m6() if config == "lite" else structured_v3(states__level="off")
    engine.respond("GET", EFFECTIVE, 200, json=body)
    if method == "get":
        html = get(operator_client)
        assert 'class="state-definition-form"' not in html
    else:
        html = page(post(operator_client, data))
        assert "states.level" in refusal(html)
        assert f'<a href="/stories/{STORY}/config/">Config</a>' in refusal(html)
        kept = form_data(html)
        assert kept["unit"] == "coins"
        assert kept["constraints"] == data["constraints"]
        assert kept["idempotency_key"] == data["idempotency_key"]
        assert kept["snapshot"] == data["snapshot"]
    assert "states.level" in html
    assert "Change gold&#x27;s definition" in html
    assert not [r for r in engine.requests if r.url.path.startswith(GOLD_PATH)]
    assert not patches(engine)


# --- pre-fill ---------------------------------------------------------------------------------


STORED = {
    "aliases": ["coin", "doubloon"],
    "silent": True,
    "rules": {"kind": "derived", "expr": "a + b"},
    "authorization": {"sources": ["rule"]},
    "transfer": {"to": "Bob", "nested": {"a": 1}},
    "reservation_expiry_minutes": 15,
}


def test_every_member_prefilled(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, definition_body(**STORED))
    data = form_data(get(operator_client))
    assert data["visibility"] == "player_visible"
    assert data["unit"] == "gold"
    assert data["aliases"] == "coin\ndoubloon"
    assert data["silent"] == "true"
    assert data["reservation_expiry_minutes"] == "15"
    assert json.loads(data["constraints"]) == GOLD_CONSTRAINTS
    assert data["constraints"] == json.dumps(GOLD_CONSTRAINTS, indent=2)  # stored order
    for member in ("rules", "authorization", "transfer"):
        assert data[member] == json.dumps(STORED[member], indent=2, ensure_ascii=False)
    # Complement: the restored form sends nothing.
    unchanged_from_read(operator_client, engine, data)


def test_box_help_says_clear_resets(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    help_html = "Edit the stored value; clear the box to reset it to the default (<code>{}</code>)"
    assert PATCH_BOX_HELP == (
        "Edit the stored value; clear the box to reset it to the default (`{}`)"
    )
    for member in ("constraints", "rules", "authorization", "transfer"):
        assert help_html in field(html, member), member
    assert "One per line; clear to remove them all" in field(html, "aliases")
    assert "Clear to remove it" in field(html, "reservation_expiry_minutes")
    assert "Blank: no unit" in field(html, "unit")
    assert "<small" not in field(html, "silent")
    assert "changes Max only" not in html  # CONSTRAINTS_EXAMPLE is gone


def test_defaults_render_blank(operator_client: Client, engine: FakeEngine) -> None:
    serve(
        engine,
        definition_body(
            unit="",
            constraints={},
            rules={},
            authorization=None,
            transfer={"gone": None},
            aliases=[],
            silent=None,
            reservation_expiry_minutes=None,
        ),
    )
    data = form_data(get(operator_client))
    for member in ("unit", "aliases", "constraints", "rules", "authorization", "transfer"):
        assert data[member] == "", member
    assert data["reservation_expiry_minutes"] == ""
    assert data["silent"] == "false"
    unchanged_from_read(operator_client, engine, data)


def test_no_reset_ticks(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    data = form_data(html)
    assert not any(name.endswith("_reset") for name in data)
    assert 'type="checkbox"' not in html
    assert "Either reset it or edit it" not in html
    assert options(html, "silent") == [("true", "Yes", False), ("false", "No", True)]
    assert "Leave as is" not in html


def test_silent_is_required(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client))
    del data["silent"]
    html = page(post(operator_client, data))
    assert "This field is required." in field(html, "silent")
    assert not patches(engine)


def test_unknown_visibility_kept_selected(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, definition_body(visibility="public"))
    html = get(operator_client)
    assert options(html, "visibility") == [
        ("player_visible", "Player visible", False),
        ("hidden", "Hidden", False),
        ("public", "Public", True),
    ]
    unchanged_from_read(operator_client, engine, form_data(html))


@pytest.mark.parametrize(("member", "stored"), [("constraints", [1, 2]), ("rules", "derived")])
def test_non_dict_stored_member_errors_untouched(
    operator_client: Client, engine: FakeEngine, member: str, stored: Any
) -> None:
    serve(engine, definition_body(**{member: stored}))
    data = form_data(get(operator_client))
    assert data[member] == json.dumps(stored, indent=2)
    html = page(post(operator_client, data))
    assert "Enter a JSON object" in field(html, member)
    assert not patches(engine)
    # Clearing the box is the way on: it sends the reset.
    response = post(operator_client, {**data, member: ""})
    assert response.status_code == 302
    assert sent(engine) == {member: None}


def test_odd_stored_scalars_prefill_blank(operator_client: Client, engine: FakeEngine) -> None:
    serve(
        engine,
        definition_body(
            unit=7, silent="yes", reservation_expiry_minutes=True, aliases=["coin", 3, " "]
        ),
    )
    html = get(operator_client)
    data = form_data(html)
    assert data["unit"] == ""
    assert data["silent"] == "false"
    assert data["reservation_expiry_minutes"] == ""
    assert data["aliases"] == "coin"
    # The raw values still show in the Stored definition panel.
    assert '<dd class="member-unit">7</dd>' in panel(html)
    unchanged_from_read(operator_client, engine, data)


@pytest.mark.parametrize("expiry", [-5, 2.5, "15"])
def test_odd_expiry_prefills_blank(
    operator_client: Client, engine: FakeEngine, expiry: Any
) -> None:
    serve(engine, definition_body(reservation_expiry_minutes=expiry))
    data = form_data(get(operator_client))
    assert data["reservation_expiry_minutes"] == ""
    unchanged_from_read(operator_client, engine, data)


def test_stored_null_keys_are_stripped(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, definition_body(constraints={"max": 500, "min": None, "x": {"y": None}}))
    data = form_data(get(operator_client))
    assert json.loads(data["constraints"]) == {"max": 500, "x": {}}
    unchanged_from_read(operator_client, engine, data)


# --- the patch --------------------------------------------------------------------------------


def test_untouched_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    unchanged_from_read(operator_client, engine, form_data(get(operator_client)))


def test_restored_values_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(
        get(operator_client),
        unit="  gold ",
        constraints='{"non_negative": true,   "max": 500.0}',
        rules="{}",
        aliases="\n\n",
        silent="false",
    )
    unchanged_from_read(operator_client, engine, data)


def test_edit_one_key_sends_that_key(operator_client: Client, engine: FakeEngine) -> None:
    body = change(operator_client, engine, constraints='{"max": 600, "non_negative": true}')
    assert body == {"constraints": {"max": 600}}


def test_remove_a_key_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, constraints='{"max": 500}') == {
        "constraints": {"non_negative": None}
    }


def test_typed_null_key_is_a_removal(operator_client: Client, engine: FakeEngine) -> None:
    # A typed null is stripped before the diff: the key is gone, so it is sent as a removal.
    assert change(operator_client, engine, constraints='{"max": null, "non_negative": true}') == {
        "constraints": {"max": None}
    }


def test_add_a_key(operator_client: Client, engine: FakeEngine) -> None:
    body = change(
        operator_client, engine, constraints='{"max": 500, "non_negative": true, "min": 1}'
    )
    assert body == {"constraints": {"min": 1}}


def test_clear_box_resets_member(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, constraints="") == {"constraints": None}
    engine.requests.clear()
    assert change(operator_client, engine, constraints="{}") == {"constraints": None}


def test_fill_an_empty_member(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, transfer='{"to": "Bob"}') == {"transfer": {"to": "Bob"}}


def test_aliases_replaced_whole(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, definition_body(aliases=["coin", "doubloon"]))
    body = change(operator_client, engine, aliases="  coin \n\nducat\ncoin\n")
    assert body == {"aliases": ["coin", "ducat", "coin"]}


def test_clear_aliases_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, definition_body(aliases=["coin", "doubloon"]))
    assert change(operator_client, engine, aliases="") == {"aliases": None}


def test_silent_change(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, silent="true") == {"silent": True}
    serve(engine, definition_body(silent=True))
    engine.requests.clear()
    assert change(operator_client, engine, silent="false") == {"silent": False}


def test_expiry_cleared_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, definition_body(reservation_expiry_minutes=15))
    assert change(operator_client, engine, reservation_expiry_minutes="") == {
        "reservation_expiry_minutes": None
    }


@pytest.mark.parametrize(("typed", "number"), [("007", 7), ("0", 0), ("1440", 1440)])
def test_expiry_digits_sent_as_int(
    operator_client: Client, engine: FakeEngine, typed: str, number: int
) -> None:
    assert change(operator_client, engine, reservation_expiry_minutes=typed) == {
        "reservation_expiry_minutes": number
    }


@pytest.mark.parametrize("typed", ["-1", "1.5", "five", "٣"])
def test_expiry_invalid_zero_calls(operator_client: Client, engine: FakeEngine, typed: str) -> None:
    html = page(
        post(operator_client, form_data(get(operator_client), reservation_expiry_minutes=typed))
    )
    assert escape(MINUTES_INVALID) in field(html, "reservation_expiry_minutes")
    assert not patches(engine)


def test_unit_and_visibility(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, unit="coins") == {"unit": "coins"}
    engine.requests.clear()
    assert change(operator_client, engine, unit="  ") == {"unit": None}
    engine.requests.clear()
    assert change(operator_client, engine, visibility="hidden") == {"visibility": "hidden"}


def test_only_mutable_members_ever_sent(operator_client: Client, engine: FakeEngine) -> None:
    cases: list[dict[str, str]] = [
        {"unit": "coins"},
        {"visibility": "hidden"},
        {"aliases": "coin"},
        {"silent": "true"},
        {"constraints": '{"max": 5}', "rules": '{"kind": "derived"}'},
        {"authorization": '{"sources": ["consumer"]}', "transfer": '{"to": "x"}'},
        {"reservation_expiry_minutes": "3", "constraints": ""},
        # Posted extras named after immutable members are never sent.
        {"unit": "x", "scope": "character", "type": "text", "name": "silver", "owner": "external"},
    ]
    for changes in cases:
        post(operator_client, form_data(get(operator_client), **changes))
    bodies = [json.loads(r.content) for r in patches(engine)]
    assert len(bodies) == len(cases)
    for body in bodies:
        assert body
        assert set(body) <= set(MUTABLE_MEMBERS)
    assert len(MUTABLE_MEMBERS) == 9


@pytest.mark.parametrize(
    ("typed", "error"),
    [
        ("{bad", "Enter valid JSON"),
        ("null", "Enter a JSON object"),
        ("[1]", "Enter a JSON object"),
    ],
)
def test_bad_json_zero_calls(
    operator_client: Client, engine: FakeEngine, typed: str, error: str
) -> None:
    data = form_data(get(operator_client), constraints=typed)
    html = page(post(operator_client, data))
    assert error in field(html, "constraints")
    assert not patches(engine)
    kept = form_data(html)
    assert kept["idempotency_key"] == data["idempotency_key"]  # local invalid: the posted key


def test_no_expected_revision_header(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), unit="coins")
    post(operator_client, data)
    (request,) = patches(engine)
    assert "X-Epistrel-Expected-Revision" not in request.headers
    assert request.headers["Idempotency-Key"] == data["idempotency_key"]
    assert request.extensions["timeout"]["read"] == MUTATION_TIMEOUT_S


def test_patch_diffs_against_the_rendered_snapshot(
    operator_client: Client, engine: FakeEngine
) -> None:
    # Another writer changed Unit after the render: the POST re-reads, but an untouched Unit is
    # never sent (save wins, R2-Q32).
    data = form_data(get(operator_client), silent="true")
    serve(engine, definition_body(unit="coins"))
    post(operator_client, data)
    assert sent(engine) == {"silent": True}


# --- the snapshot ----------------------------------------------------------------------------


def test_v1_snapshot_rejected(operator_client: Client, engine: FakeEngine) -> None:
    v1 = signing.dumps(
        {"name": "gold", "visibility": "player_visible", "unit": "gold"}, salt=V1_SALT
    )
    data = form_data(get(operator_client), unit="coins", snapshot=v1)
    html = page(post(operator_client, data))
    assert escape(SNAPSHOT_INVALID) in html
    assert not patches(engine)


def _signed(payload: Any) -> str:
    return signing.dumps(payload, salt="console.states.definition.snapshot.v2", compress=True)


@pytest.mark.parametrize(
    "snapshot",
    [
        "tampered",
        "",
        sign_definition_snapshot("silver", definition_body()["definition"], 30),
        _signed({"name": "gold", "members": {}, "config_revision": 30, "x": 1}),
        _signed({"name": "gold", "members": {"scope": "story"}, "config_revision": 30}),
        _signed({"name": "gold", "members": {"unit": 7}, "config_revision": 30}),
        _signed({"name": "gold", "members": {"silent": "true"}, "config_revision": 30}),
        _signed({"name": "gold", "members": {"aliases": [1]}, "config_revision": 30}),
        _signed({"name": "gold", "members": {"rules": None}, "config_revision": 30}),
        _signed(
            {"name": "gold", "members": {"reservation_expiry_minutes": True}, "config_revision": 1}
        ),
        _signed({"name": "gold", "members": [], "config_revision": 30}),
        _signed({"name": "gold", "members": {}, "config_revision": True}),
        _signed({"name": "gold", "members": {}, "config_revision": "30"}),
    ],
)
def test_tampered_snapshot_zero_calls(
    operator_client: Client, engine: FakeEngine, snapshot: str
) -> None:
    data = form_data(get(operator_client), unit="coins", constraints="{bad", snapshot=snapshot)
    html = page(post(operator_client, data))
    assert escape(SNAPSHOT_INVALID) in html
    assert "Enter valid JSON" not in html  # the snapshot alone: no field errors
    assert not patches(engine)
    kept = form_data(html)
    assert kept["unit"] == "coins"
    assert kept["idempotency_key"] == data["idempotency_key"]


# --- the outcome and D8's base ---------------------------------------------------------------


def test_success_redirects_with_member_flashes(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(
        get(operator_client),
        constraints='{"max": 600, "non_negative": true}',
        unit="coins",
        reservation_expiry_minutes="30",
        aliases="coin",
    )
    response = post(operator_client, data)
    assert response.status_code == 302
    assert response["Location"] == STATE_URL
    assert flashes(response) == [
        "Unit: saved",
        "Aliases: saved",
        "Constraints: saved",
        "Reservation expiry minutes: saved",
        "Configuration revision now 6",
    ]


def test_nothing_changed_against_rendered_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine, definition_body(config_revision=6))
    response = post(operator_client, form_data(get(operator_client), unit="coins"))
    assert response.status_code == 302
    assert len(patches(engine)) == 1
    assert flashes(response) == [ENGINE_NOTHING_CHANGED]


def test_null_base_shows_saved(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, definition_body(config_revision=None))
    response = post(operator_client, form_data(get(operator_client), unit="coins"))
    assert flashes(response) == ["Unit: saved", "Configuration revision now 6"]


def test_moved_revision_shows_saved(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, definition_body(config_revision=6))
    written = engine_fixture("m6/state_definition_written")
    engine.respond("PATCH", DEFINITION, 200, json={**written, "config_revision": 7})
    response = post(operator_client, form_data(get(operator_client), unit="coins"))
    assert flashes(response) == ["Unit: saved", "Configuration revision now 7"]


def test_base_is_the_rendered_snapshot_not_the_post_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form_data(get(operator_client), unit="coins")  # rendered at 30
    serve(engine, definition_body(config_revision=6))  # the POST's re-read says 6
    response = post(operator_client, data)
    assert flashes(response) == ["Unit: saved", "Configuration revision now 6"]


def test_no_base_revision_name() -> None:
    assert not hasattr(inspect_state_definition, "base_revision")
    assert not hasattr(inspect_clock, "base_revision")


def test_unreadable_200_warns_and_redirects(operator_client: Client, engine: FakeEngine) -> None:
    engine.side_effect("PATCH", DEFINITION, lambda request: httpx.Response(200, json={"x": 1}))
    response = post(operator_client, form_data(get(operator_client), unit="coins"))
    assert response.status_code == 302
    assert response["Location"] == STATE_URL
    assert flashes(response) == [UNREADABLE_SAVED]


# --- refusals --------------------------------------------------------------------------------


def refuse(engine: FakeEngine, status: int, code: str, **details: Any) -> None:
    engine.respond("PATCH", DEFINITION, status, json=error_json(code, **details))


def refused(client: Client, **changes: str) -> tuple[str, dict[str, str]]:
    """POST a change that the fake refuses: the re-rendered page and the posted data. Every
    input and the snapshot kept; a fresh key (an answered refusal)."""
    data = form_data(get(client), unit="coins", constraints='{"max": 100}', **changes)
    html = page(post(client, data))
    kept = form_data(html)
    assert kept["unit"] == "coins"
    assert kept["constraints"] == '{"max": 100}'
    assert kept["snapshot"] == data["snapshot"]
    return html, data


def fresh_key(html: str, data: dict[str, str]) -> bool:
    return form_data(html)["idempotency_key"] != data["idempotency_key"]


def unchanged(client: Client) -> None:
    """A refused write changed nothing: a re-GET shows the old Unit."""
    assert form_data(get(client))["unit"] == "gold"


def test_rules_kind_on_rules(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="rules", reason="rules_kind")
    html, data = refused(operator_client)
    assert escape(RULES_KIND) in field(html, "rules")
    assert "state-definition-refusal" not in html
    assert fresh_key(html, data)
    unchanged(operator_client)


def test_unknown_member_above_form(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="colour", reason="unknown")
    html, data = refused(operator_client)
    assert refusal(html) == "colour isn&#x27;t a State definition member"
    assert fresh_key(html, data)
    unchanged(operator_client)


def test_immutable_field_scope_above_form(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="scope", reason="immutable_field")
    html, data = refused(operator_client)
    assert refusal(html) == "Scope can&#x27;t change once a State is declared"
    assert fresh_key(html, data)
    unchanged(operator_client)


@pytest.mark.parametrize(
    "path",
    [
        "constraints.allowed_values",
        "constraints.min",
        "constraints.max",
        "constraints.non_negative",
        "constraints.tolerance",
        "initial_value",
    ],
)
def test_constraint_shape_on_constraints(
    operator_client: Client, engine: FakeEngine, path: str
) -> None:
    refuse(
        engine,
        422,
        "validation_error",
        message="only an enum State lists allowed values",
        field=path,
        reason="not_enum",
    )
    html, data = refused(operator_client)
    assert "only an enum State lists allowed values" in field(html, "constraints")
    assert "state-definition-refusal" not in html
    assert fresh_key(html, data)
    unchanged(operator_client)


def test_plain_member_shape_on_its_field(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        422,
        "validation_error",
        message="a unit is 1 to 32 characters",
        field="unit",
        reason="length",
    )
    html, _ = refused(operator_client)
    assert "a unit is 1 to 32 characters" in field(html, "unit")


def test_reserved_state(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error", field="name", reason="reserved_state")
    html, data = refused(operator_client)
    assert refusal(html) == "<code>world_clock</code> is the world clock — use the Clock section"
    assert fresh_key(html, data)


def test_constraint_violation_definition_wording(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(
        engine,
        422,
        "state_constraint_violation",
        state="gold",
        constraint="max",
        attempted=125,
        current=125,
    )
    html, data = refused(operator_client)
    assert refusal(html) == "The current value 125 gold breaks the new <code>max</code> constraint"
    assert fresh_key(html, data)
    unchanged(operator_client)


def test_module_off(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "module_off", module="states")
    html, data = refused(operator_client)
    assert "states.level" in refusal(html)
    assert f'<a href="/stories/{STORY}/config/">Config</a>' in refusal(html)
    assert fresh_key(html, data)
    unchanged(operator_client)


def test_story_archived(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "story_archived")
    html, data = refused(operator_client)
    assert refusal(html) == (
        "This story is archived — unarchive it to change a State&#x27;s definition."
    )
    assert fresh_key(html, data)
    unchanged(operator_client)


def test_idempotency_conflict_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "idempotency_conflict")
    html, data = refused(operator_client)
    assert "engine-error" in html.split('class="state-definition-form"')[0]
    assert fresh_key(html, data)
    unchanged(operator_client)


def test_patch_404_not_found_page(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "not_found", state="gold")
    data = form_data(get(operator_client), unit="coins")
    html = page(post(operator_client, data))
    assert NOT_FOUND in html
    assert 'class="state-definition-form"' not in html


def test_transport_error_keeps_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("PATCH", DEFINITION, httpx.ConnectError("down"))
    html, data = refused(operator_client)
    assert not fresh_key(html, data)
    assert "engine-error" in html.split('class="state-definition-form"')[0]


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), unit="coins")
    engine.respond(
        "PATCH",
        DEFINITION,
        503,
        json=error_json("erasure_pending"),
        headers={"Retry-After": "30"},
    )
    html = operator_client.post(url(), data).content.decode()
    assert held_text("30") in html
    assert 'class="state-definition-form"' not in html


# --- links in --------------------------------------------------------------------------------


def test_one_state_page_links_change_definition(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories/{story_id}/states/{name}", 200, json=state_view("gold"))
    html = get_page(operator_client, STATE_URL)
    assert f'<a class="state-definition" href="{url()}">Change definition…</a>' in html


def get_page(client: Client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    return response.content.decode()

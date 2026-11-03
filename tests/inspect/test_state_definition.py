"""Change a State's definition (#265): ``inspect:state-definition`` and ``PATCH
/stories/{id}/states/{name}/definition``.

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast. The page reads ``gold`` (``m6/states.json`` wrapped by
:func:`tests.inspect.helpers.state_view`: number, unit "gold", player visible); a save answers
``m6/state_definition_written.json`` (``config_revision`` 6). Each test posts back what the
rendered form holds (:func:`form_data`, scraped from the HTML), with its changes. Refusal bodies
are built inline (D16)."""

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.engine.client import MUTATION_TIMEOUT_S, EngineClient
from console.engine_access import engine_client_for
from console.forms.states import (
    MINUTES_INVALID,
    MUTABLE_MEMBERS,
    NOTHING_CHANGED,
    RESET_OR_EDIT,
    RULES_KIND,
    SNAPSHOT_INVALID,
    sign_definition_snapshot,
)
from console.held import held_text
from console.models import EngineOperation
from console.states import STATE_DEFINITION_REQUEST
from console.views import inspect as views
from console.views import inspect_state_definition
from console.views.inspect_state_definition import AUTHORIZATION_REDACTED, UNREADABLE_SAVED
from console.world import ENGINE_NOTHING_CHANGED
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, state_view, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
OTHER_STORY = "01JM6ST0000000000000000002"
ONE_STATE = "/stories/{story_id}/states/{name}"
DEFINITION = "/stories/{story_id}/states/{name}/definition"
GOLD_PATH = f"/stories/{STORY}/states/gold"
PATCH_PATH = f"{GOLD_PATH}/definition"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
WRITES = ("PUT", "POST", "PATCH", "DELETE")


def url(name: str = "gold") -> str:
    return f"/stories/{STORY}/inspect/world/state/{name}/definition/"


STATE_URL = f"/stories/{STORY}/inspect/world/state/gold/"


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
    fake.respond("GET", ONE_STATE, 200, json=state_view("gold"))
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


# --- the patch --------------------------------------------------------------------------------


def test_untouched_form_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = page(post(operator_client, form_data(get(operator_client))))
    assert NOTHING_CHANGED in html
    assert 'class="state-definition-form"' in html
    assert not patches(engine)


def test_page_prefills_only_visibility_and_unit(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form_data(get(operator_client))
    assert data["visibility"] == "player_visible"
    assert data["unit"] == "gold"
    for blank in ("constraints", "rules", "authorization", "transfer", "aliases"):
        assert data[blank] == ""
    assert data["reservation_expiry_minutes"] == ""
    assert data["silent"] == ""
    assert not any(name.endswith("_reset") for name in data)


def test_unit_changed_sends_unit_only(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, unit="coins") == {"unit": "coins"}


def test_unit_cleared_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, unit="") == {"unit": None}


def test_whitespace_unit_is_blank(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, unit="   ") == {"unit": None}


def test_unitless_state_blank_unit_sends_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ONE_STATE, 200, json=state_view("door_open"))
    html = page(post(operator_client, form_data(get(operator_client, "door_open")), "door_open"))
    assert NOTHING_CHANGED in html
    assert not patches(engine)


def test_visibility_changed(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, visibility="hidden") == {"visibility": "hidden"}


def test_unknown_read_visibility_kept_selected(operator_client: Client, engine: FakeEngine) -> None:
    # An open enum (D15) the recorded schema doesn't list yet: served unvalidated.
    body = state_view("gold", visibility="secret_ish")
    engine.side_effect("GET", ONE_STATE, lambda request: httpx.Response(200, json=body))
    html = get(operator_client)
    assert options(html, "visibility") == [
        ("player_visible", "Player visible", False),
        ("hidden", "Hidden", False),
        ("secret_ish", "Secret ish", True),
    ]
    assert NOTHING_CHANGED in page(post(operator_client, form_data(html)))
    assert not patches(engine)


def test_constraints_box_sent_as_member_patch(operator_client: Client, engine: FakeEngine) -> None:
    body = change(operator_client, engine, constraints='{"max": 500}')
    assert body == {"constraints": {"max": 500}}
    # Complement: no other member is sent.
    assert set(body) == {"constraints"}


def test_nested_null_sent_as_given(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, constraints='{"max": null}') == {
        "constraints": {"max": None}
    }


def test_empty_object_box_is_blank(operator_client: Client, engine: FakeEngine) -> None:
    html = page(post(operator_client, form_data(get(operator_client), rules="{}")))
    assert NOTHING_CHANGED in html
    assert not patches(engine)


@pytest.mark.parametrize(
    "member",
    ["constraints", "rules", "authorization", "transfer", "aliases", "reservation_expiry_minutes"],
)
def test_reset_sends_null(operator_client: Client, engine: FakeEngine, member: str) -> None:
    assert change(operator_client, engine, **{f"{member}_reset": "on"}) == {member: None}


@pytest.mark.parametrize(
    ("member", "typed"),
    [
        ("constraints", '{"max": 1}'),
        ("rules", '{"kind": "derived"}'),
        ("authorization", '{"sources": ["rule"]}'),
        ("transfer", '{"x": 1}'),
        ("aliases", "coin"),
        ("reservation_expiry_minutes", "5"),
    ],
)
def test_reset_and_edit_zero_calls(
    operator_client: Client, engine: FakeEngine, member: str, typed: str
) -> None:
    data = form_data(get(operator_client), **{member: typed, f"{member}_reset": "on"})
    html = page(post(operator_client, data))
    assert escape(RESET_OR_EDIT) in field(html, member)
    assert not patches(engine)


def test_aliases_replace_whole(operator_client: Client, engine: FakeEngine) -> None:
    body = change(operator_client, engine, aliases="  coin \n\ndoubloon\ncoin\n")
    assert body == {"aliases": ["coin", "doubloon", "coin"]}


def test_silent_leave_as_is_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    body = change(operator_client, engine, silent="", unit="coins")
    assert "silent" not in body


def test_silent_false_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    assert change(operator_client, engine, silent="false") == {"silent": False}
    engine.requests.clear()
    assert change(operator_client, engine, silent="true") == {"silent": True}


def test_silent_labels(operator_client: Client, engine: FakeEngine) -> None:
    assert options(get(operator_client), "silent") == [
        ("", "Leave as is", True),
        ("true", "Yes", False),
        ("false", "No", False),
    ]


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


def test_no_immutable_member_ever_sent(operator_client: Client, engine: FakeEngine) -> None:
    cases: list[dict[str, str]] = [
        {"unit": "coins"},
        {"visibility": "hidden"},
        {"aliases": "coin"},
        {"silent": "true"},
        {"constraints": '{"max": 5}', "rules_reset": "on"},
        {"authorization": '{"sources": ["consumer"]}', "transfer": '{"to": "x"}'},
        {"reservation_expiry_minutes": "3", "aliases_reset": "on"},
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


def test_no_expected_revision_header(operator_client: Client, engine: FakeEngine) -> None:
    change(operator_client, engine, unit="coins")
    (request,) = patches(engine)
    assert "X-Epistrel-Expected-Revision" not in request.headers
    assert request.headers["Idempotency-Key"]


def test_patch_timeout_is_mutation_timeout(operator_client: Client, engine: FakeEngine) -> None:
    change(operator_client, engine, unit="coins")
    (request,) = patches(engine)
    assert request.extensions["timeout"]["read"] == MUTATION_TIMEOUT_S


def test_page_key_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), unit="coins")
    post(operator_client, data)
    (request,) = patches(engine)
    assert request.headers["Idempotency-Key"] == data["idempotency_key"]


@pytest.mark.parametrize(
    "snapshot",
    [
        "tampered",
        "",
        sign_definition_snapshot("silver", "player_visible", "gold"),
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


# --- the gate and the read ------------------------------------------------------------------


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
        assert kept["idempotency_key"] == data["idempotency_key"]
        assert kept["snapshot"] == data["snapshot"]
    assert "states.level" in html
    assert "Change gold&#x27;s definition" in html
    assert not [r for r in engine.requests if r.url.path == GOLD_PATH]
    assert not patches(engine)


def test_reads_shell_then_one_omniscient_state(operator_client: Client, engine: FakeEngine) -> None:
    get(operator_client)
    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("GET", f"/stories/{STORY}/effective-config"),
        ("GET", f"/stories/{STORY}"),
        ("GET", GOLD_PATH),
    ]
    assert engine.requests[-1].url.params["viewpoint"] == "omniscient"


def test_page_shows_identity_and_notice(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert "Change gold&#x27;s definition</h3>" in html
    identity = re.search(r'<dl class="state-identity">(.*?)</dl>', html, re.S)
    assert identity is not None
    assert identity.group(1) == (
        "<dt>Type</dt><dd>Number</dd><dt>Scope</dt><dd>Story</dd><dt>Owner</dt><dd>Story</dd>"
    )
    assert (
        "The Engine serves no read of a State&#x27;s definition, so only Visibility and Unit "
        "start from the State as it stands; leave the others blank to keep them "
        f'(<a href="{STATE_DEFINITION_REQUEST}">Engine request (State definition read, '
        "nathanpond/Epistrel#680)</a>)"
    ) in html
    assert STATE_DEFINITION_REQUEST == "https://github.com/nathanpond/Epistrel/issues/680"
    assert "turns checked under the old ones are re-checked" in html
    assert '<details class="recorded-definition">' in html  # closed


def test_unknown_state_not_found_page(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_STATE, 404, json=error_json("not_found", state="silver"))
    for response in (
        operator_client.get(url("silver")),
        operator_client.post(url("silver"), {"unit": "x", "visibility": "hidden"}),
    ):
        html = page(response)
        assert NOT_FOUND in html
        assert 'class="state-definition-form"' not in html
    assert not patches(engine)


def test_invalid_name_django_404(operator_client: Client, engine: FakeEngine) -> None:
    for bad in ("9gold", "go-ld", "_gold"):
        assert operator_client.get(url(bad)).status_code == 404
    assert not engine.requests


# --- the outcome ----------------------------------------------------------------------------


def test_success_redirects_with_member_flashes(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(
        get(operator_client),
        constraints='{"max": 500}',
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


def test_engine_found_nothing_to_change(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The shell's effective config carries no config_revision at the recorded schema (#270);
    # stand one in equal to the answer's.
    monkeypatch.setattr(inspect_state_definition, "base_revision", lambda ctx: 6)
    response = post(operator_client, form_data(get(operator_client), unit="coins"))
    assert response.status_code == 302
    assert len(patches(engine)) == 1
    assert flashes(response) == [ENGINE_NOTHING_CHANGED]


def test_unknown_base_revision_shows_saved(operator_client: Client, engine: FakeEngine) -> None:
    response = post(operator_client, form_data(get(operator_client), unit="coins"))
    assert flashes(response) == ["Unit: saved", "Configuration revision now 6"]


def test_unreadable_200_warns_and_redirects(operator_client: Client, engine: FakeEngine) -> None:
    engine.side_effect("PATCH", DEFINITION, lambda request: httpx.Response(200, json={"x": 1}))
    response = post(operator_client, form_data(get(operator_client), unit="coins"))
    assert response.status_code == 302
    assert response["Location"] == STATE_URL
    assert flashes(response) == [UNREADABLE_SAVED]


# --- last recorded definition ---------------------------------------------------------------


def record(
    kind: str,
    definition: dict[str, Any],
    when: datetime,
    *,
    story_id: str = STORY,
    outcome: str = "ok",
) -> EngineOperation:
    row: EngineOperation = EngineOperation.objects.create(
        operator_username="op",
        principal="op",
        story_id=story_id,
        kind=kind,
        http_method="POST",
        path="/x",
        outcome=outcome,
        response={"definition": definition, "timeline_revision": 1},
        created_at=when,
    )
    return row


def gold(**changes: Any) -> dict[str, Any]:
    definition: dict[str, Any] = dict(engine_fixture("m6/state_definition_written")["definition"])
    definition.update(changes)
    return definition


def recorded(html: str) -> str:
    found = re.search(r'<details class="recorded-definition">(.*?)</details>', html, re.S)
    assert found is not None
    return found.group(1)


def test_last_recorded_definition_picks_newest_matching_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    record("declare_state", gold(unit="old"), datetime(2026, 10, 1, 9, 0, tzinfo=UTC))
    record("update_state_definition", gold(unit="newest"), datetime(2026, 10, 2, 14, 3, tzinfo=UTC))
    later = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    record("declare_state", gold(name="silver", unit="other state"), later)
    record("declare_state", gold(unit="other story"), later, story_id=OTHER_STORY)
    record("declare_state", gold(unit="refused"), later, outcome="error")
    record("set_state", gold(unit="other kind"), later)
    html = get(operator_client)
    panel = recorded(html)
    assert (
        "Last definition this Console recorded (2026-10-02 14:03 UTC, Update state definition)"
        " — it may be out of date"
    ) in panel
    assert '<dd class="member-unit">newest</dd>' in panel
    for ignored in ("old", "other state", "other story", "refused", "other kind"):
        assert f'<dd class="member-unit">{ignored}</dd>' not in panel
    # Complement: the panel makes no Engine call (the three reads only).
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        GOLD_PATH,
    ]


def test_approved_proposal_row_counts(operator_client: Client, engine: FakeEngine) -> None:
    record("approve_state_proposal", gold(unit="flasks"), datetime(2026, 10, 2, 9, 0, tzinfo=UTC))
    panel = recorded(get(operator_client))
    assert "Approve state proposal" in panel
    assert '<dd class="member-unit">flasks</dd>' in panel


def test_redacted_authorization_says_so(operator_client: Client, engine: FakeEngine) -> None:
    record("declare_state", gold(authorization="***"), datetime(2026, 10, 2, tzinfo=UTC))
    panel = recorded(get(operator_client))
    assert f'<dd class="member-authorization">{escape(AUTHORIZATION_REDACTED)}</dd>' in panel


def test_no_recorded_definition_text(operator_client: Client, engine: FakeEngine) -> None:
    record("declare_state", gold(name="silver"), datetime(2026, 10, 2, tzinfo=UTC))
    panel = recorded(get(operator_client))
    assert "This Console has recorded no definition of gold" in panel
    assert "member-unit" not in panel


def test_successful_change_becomes_the_last_record(
    operator_client: Client, engine: FakeEngine
) -> None:
    change(operator_client, engine, unit="coins")
    panel = recorded(get(operator_client))
    assert "Update state definition" in panel


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
    html = get_page(operator_client, STATE_URL)
    assert f'<a class="state-definition" href="{url()}">Change definition…</a>' in html


def get_page(client: Client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    return response.content.decode()

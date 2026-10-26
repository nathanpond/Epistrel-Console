"""The Predicates tab and approving or rejecting a strengthening proposal (#136).

Runs against the real urlconf on the schema-validating fake Engine. The registry is
``tests/fixtures/inspect/predicates_list.json``: three core predicates (``located_at``,
``holds``, ``contained_in``), an author one (``sworn_to``) and an auto one (``guards``) holding a
two-field pending proposal; one rule without ``via`` and one with; ``located_at`` excludes an id
the registry doesn't hold. The decisions are ``predicate_decision_approved.json`` (two
violations) and ``predicate_decision_rejected.json``."""

import copy
import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine.models import PredicateList
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.predicates import Refs, layer_filter, predicate_names, value_cell
from console.views import inspect as views
from console.views import inspect_predicates
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, lite, structured
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

TAB = f"/stories/{STORY}/inspect/predicates/"
LIST = "/stories/{story_id}/predicates"
LIST_PATH = f"/stories/{STORY}/predicates"
APPROVE = "/stories/{story_id}/predicates/{predicate_id}/approve"
REJECT = "/stories/{story_id}/predicates/{predicate_id}/reject"
OTHER_STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2W"
LOC = "01JB2Z8Y5Q3W6K9M1N4P7R0PR1"
HOLDS = "01JB2Z8Y5Q3W6K9M1N4P7R0PR2"
CONT = "01JB2Z8Y5Q3W6K9M1N4P7R0PR3"
SWORN = "01JB2Z8Y5Q3W6K9M1N4P7R0PR4"
GUARDS = "01JB2Z8Y5Q3W6K9M1N4P7R0PR5"
UNKNOWN = "01JB2Z8Y5Q3W6K9M1N4P7R0PX9"
PROPOSAL = "01JB2Z8Y5Q3W6K9M1N4P7R0PP1"
APPROVE_URL = f"/stories/{STORY}/inspect/predicates/{GUARDS}/approve/"
REJECT_URL = f"/stories/{STORY}/inspect/predicates/{GUARDS}/reject/"
APPROVE_PATH = f"/stories/{STORY}/predicates/{GUARDS}/approve"
REJECT_PATH = f"/stories/{STORY}/predicates/{GUARDS}/reject"
DECISION_URL = f"/stories/{STORY}/inspect/predicates/decision/"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0K01"
NOT_FOUND = "Not found (it may have been deleted or truncated)"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return copy.deepcopy(body)


def registry(**over: Any) -> dict[str, Any]:
    body = fixture("predicates_list")
    body.update(over)
    return body


def with_guards(**over: Any) -> dict[str, Any]:
    """The registry with the auto predicate's fields changed."""
    body = registry()
    body["predicates"][4].update(over)
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    monkeypatch.setattr(inspect_predicates, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured())
    fake.respond("GET", STORY_PATH, 200, json=story_json())
    fake.respond("GET", LIST, 200, json=registry())
    fake.respond("POST", APPROVE, 200, json=fixture("predicate_decision_approved"))
    fake.respond("POST", REJECT, 200, json=fixture("predicate_decision_rejected"))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def tab(client: Client, query: str = "") -> str:
    response = client.get(f"{TAB}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == LIST_PATH]


def posts(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST"]


def row(html: str, predicate_id: str) -> str:
    pattern = rf'<tr id="p-{predicate_id}" class="predicate-row[^"]*">(.*?)</tr>'
    found = re.search(pattern, html, re.S)
    assert found is not None, predicate_id
    return found.group(1)


def row_ids(html: str) -> list[str]:
    return re.findall(r'<tr id="p-([^"]+)" class="predicate-row', html)


def cells(row_html: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip().replace(" ,", ",")
        for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
    ]


def proposal_html(html: str) -> str:
    found = re.search(r'<tr class="proposal-row">(.*?)\n</tr>', html, re.S)
    assert found is not None
    return found.group(1)


def control_query(html: str, label: str) -> dict[str, list[str]]:
    found = re.search(rf'<a class="button" href="([^"]+)">{label}</a>', html)
    assert found is not None, label
    return parse_qs(urlsplit(found.group(1).replace("&amp;", "&")).query)


def hidden_values(html: str, name: str) -> list[str]:
    return re.findall(rf'<input type="hidden" name="{name}" value="([^"]*)">', html)


def rules(html: str) -> str:
    return html.split('<table class="predicate-rules">')[1].split("</table>")[0]


def flashes(response: Any) -> list[tuple[str, str]]:
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def confirm(client: Client, url: str = APPROVE_URL, **data: Any) -> Any:
    sent: dict[str, Any] = {
        "name": "guards",
        "offered": ["cardinality", "qualifiers"],
        "layer": "all",
        "idempotency_key": KEY,
        "confirmed": "1",
    }
    sent.update(data)
    return client.post(url, sent)


def sent_body(request: httpx.Request) -> object:
    return json.loads(request.content) if request.content else None


# --- rendering -----------------------------------------------------------------------------------


def test_one_row_per_predicate_in_the_engines_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client)
    assert row_ids(html) == [LOC, HOLDS, CONT, SWORN, GUARDS]
    assert "<title>Inspect · Predicates · " in html
    assert 'aria-current="page" href="' + TAB + '">Predicates</a>' in html
    assert len(reads(engine)) == 1


def test_a_core_row(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    located = row(html, LOC)
    assert '<span class="badge layer-core">core</span>' in located
    assert "<code>entity</code> → <code>place</code>" in located
    shown = cells(located)
    assert shown[0] == "located_at"
    assert shown[3] == "functional"
    assert shown[4] == "frame"
    assert shown[5] == "frame: string since: time"
    assert "frame: string<br>since: time" in located
    assert shown[6] == "Fluent"
    assert shown[7] == "—"
    assert shown[8] == "—"  # inverse of
    # Predicate ids as names linked to the row on the page; an unknown id as a short id.
    assert f'<a href="#p-{CONT}" title="{CONT}">contained_in</a>' in located
    assert f'<code title="{UNKNOWN}">…4P7R0PX9</code>' in located
    assert f'href="#p-{UNKNOWN}"' not in html
    assert shown[11] == "located_at ⇒ contained_in"
    assert shown[12] == "location"
    assert shown[13] == "engine"
    assert "<details><summary>docs</summary>Where an entity is.</details>" in located


def test_the_other_cells(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    holds = cells(row(html, HOLDS))
    assert holds[3] == "multi (max 3)"
    assert holds[5] == "none declared"  # {}
    assert holds[7] == "set object"
    assert holds[12] == "—"
    assert holds[14] == "—"  # no docs
    sworn = row(html, SWORN)
    assert '<span class="badge layer-author">author</span>' in sworn
    assert cells(sworn)[7] == "symmetric"
    assert f'<a href="#p-{GUARDS}" title="{GUARDS}">guards</a>' in sworn
    guards = row(html, GUARDS)
    assert '<span class="badge layer-auto">auto</span>' in guards
    assert "<code>any</code> → <code>none</code>" in guards
    shown = cells(guards)
    assert shown[3] == "multi"  # max_cardinality null
    assert shown[5] == "undeclared"  # null qualifiers
    assert shown[7] == "weak"
    # The id is in "More" with the rest of the raw row.
    assert f"&quot;id&quot;: &quot;{GUARDS}&quot;" in guards


def test_an_unknown_layer_gets_the_neutral_badge(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 200, json=with_guards(layer="draft"))
    assert '<span class="badge layer-other">draft</span>' in row(tab(operator_client), GUARDS)


def test_the_rules_table(operator_client: Client, engine: FakeEngine) -> None:
    shown = rules(tab(operator_client))
    lines = [cells(r) for r in re.findall(r'<tr class="rule-row">(.*?)</tr>', shown, re.S)]
    assert lines == [
        ["located_at", "⇒ contained_in", "—"],
        ["sworn_to", "⇒ guards", "holds"],
    ]
    assert f'<a href="#p-{HOLDS}" title="{HOLDS}">holds</a>' in shown


def test_no_rules_is_none(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json=registry(rules=[]))
    html = tab(operator_client)
    assert '<h2>Implication rules</h2>\n<p class="rules-none">None</p>' in html


def test_the_header(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    assert "Configuration revision <code>7</code>" in html
    assert f'<a href="{TAB}?layer=all#p-{GUARDS}">1 pending proposal</a>' in html


def test_the_header_counts_and_plurals(operator_client: Client, engine: FakeEngine) -> None:
    body = registry()
    body["predicates"][3]["pending_proposal"] = {
        "id": PROPOSAL,
        "status": "pending",
        "filed_by": "author",
        "changes": {"symmetric": False},
    }
    engine.respond("GET", LIST, 200, json=body)
    assert f'<a href="{TAB}?layer=all#p-{SWORN}">2 pending proposals</a>' in tab(operator_client)
    engine.respond("GET", LIST, 200, json=with_guards(pending_proposal=None))
    html = tab(operator_client)
    assert "· No pending proposals</p>" in html
    assert '<tr class="proposal-row">' not in html


# --- the layer filter ----------------------------------------------------------------------------


def test_the_layer_filter_shows_only_that_layer(
    operator_client: Client, engine: FakeEngine
) -> None:
    unfiltered = tab(operator_client)
    html = tab(operator_client, "?layer=auto")
    assert row_ids(html) == [GUARDS]
    # Complement: the core and author rows are gone; the rules and the header are not.
    for hidden_id in (LOC, HOLDS, CONT, SWORN):
        assert f'id="p-{hidden_id}"' not in html
    assert cells(rules(html)) == cells(rules(unfiltered))
    assert f'<a href="{TAB}?layer=all#p-{GUARDS}">1 pending proposal</a>' in html
    assert '<option value="auto" selected>auto</option>' in html
    # A row the filter hides is linked on the unfiltered tab.
    assert f'<a href="{TAB}?layer=all#p-{HOLDS}" title="{HOLDS}">holds</a>' in rules(html)
    assert len(reads(engine)) == 2  # one per render


@pytest.mark.parametrize("value", ["bogus", "AUTO", "", "all"])
def test_any_other_layer_is_all(operator_client: Client, engine: FakeEngine, value: str) -> None:
    html = tab(operator_client, f"?layer={value}")
    assert row_ids(html) == [LOC, HOLDS, CONT, SWORN, GUARDS]
    assert '<option value="all" selected>all</option>' in html
    assert layer_filter(value) == "all"


# --- the proposal sub-row ------------------------------------------------------------------------


def test_the_proposal_sub_row(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    # It sits right under its predicate's row.
    assert html.index(f'id="p-{GUARDS}"') < html.index('<tr class="proposal-row">')
    shown = proposal_html(html)
    assert f'Proposal <code title="{PROPOSAL}">…4P7R0PP1</code> · filed by engine' in shown
    fields = [cells(r) for r in re.findall(r'<tr class="proposal-field">(.*?)</tr>', shown, re.S)]
    assert fields == [
        ["", "cardinality", "multi", "functional"],
        ["", "qualifiers", "undeclared", "role: string"],
    ]
    assert '<p class="proposal-note">Also sets max cardinality to 1</p>' in shown
    assert ">Approve all</a>" in shown
    assert ">Reject</a>" in shown
    assert '<button type="submit">Approve selected</button>' in shown
    # One checkbox per field, unticked, in the approve-selected form.
    boxes = re.findall(r'<input type="checkbox" name="field" value="([^"]+)" form="([^"]+)"', shown)
    assert boxes == [("cardinality", f"select-{GUARDS}"), ("qualifiers", f"select-{GUARDS}")]
    assert "checked" not in shown


def test_a_field_with_no_attribute_shows_a_dash_as_current(
    operator_client: Client, engine: FakeEngine
) -> None:
    proposal = {
        "id": PROPOSAL,
        "status": "pending",
        "filed_by": "engine",
        "changes": {"exclusions": [SWORN, UNKNOWN], "brand_new": 3},
    }
    engine.respond("GET", LIST, 200, json=with_guards(pending_proposal=proposal))
    shown = proposal_html(tab(operator_client))
    fields = [cells(r) for r in re.findall(r'<tr class="proposal-field">(.*?)</tr>', shown, re.S)]
    assert fields == [
        ["", "exclusions", "—", "sworn_to, …4P7R0PX9"],
        ["", "brand_new", "—", "3"],
    ]
    assert "Also sets max cardinality" not in shown


def test_a_one_field_proposal_has_no_approve_selected(
    operator_client: Client, engine: FakeEngine
) -> None:
    proposal = {
        "id": PROPOSAL,
        "status": "pending",
        "filed_by": "engine",
        "changes": {"cardinality": "functional"},
    }
    engine.respond("GET", LIST, 200, json=with_guards(pending_proposal=proposal))
    shown = proposal_html(tab(operator_client))
    assert "Approve selected" not in shown
    assert 'type="checkbox"' not in shown
    assert ">Approve all</a>" in shown
    assert ">Reject</a>" in shown


@pytest.mark.parametrize("status", ["approved", "rejected", "superseded"])
def test_a_proposal_that_is_not_pending_has_no_sub_row(
    operator_client: Client, engine: FakeEngine, status: str
) -> None:
    proposal = {"id": PROPOSAL, "status": status, "filed_by": "engine", "changes": {"x": 1}}
    engine.respond("GET", LIST, 200, json=with_guards(pending_proposal=proposal))
    html = tab(operator_client)
    assert '<tr class="proposal-row">' not in html
    assert "No pending proposals" in html


def test_each_control_carries_its_own_key(operator_client: Client, engine: FakeEngine) -> None:
    shown = proposal_html(tab(operator_client))
    approve_all = control_query(shown, "Approve all")
    reject = control_query(shown, "Reject")
    assert approve_all["name"] == ["guards"]
    assert approve_all["offered"] == ["cardinality", "qualifiers"]
    assert approve_all["layer"] == ["all"]
    assert reject["offered"] == ["cardinality", "qualifiers"]
    selected_key = hidden_values(shown, "idempotency_key")
    assert hidden_values(shown, "mode") == ["selected"]
    assert hidden_values(shown, "offered") == ["cardinality", "qualifiers"]
    keys = {approve_all["idempotency_key"][0], reject["idempotency_key"][0], *selected_key}
    assert len(keys) == 3
    assert all(re.fullmatch(r"[0-9A-Z]{26}", k) for k in keys)
    assert f'action="{APPROVE_URL}"' in shown


def test_the_controls_keep_the_layer(operator_client: Client, engine: FakeEngine) -> None:
    shown = proposal_html(tab(operator_client, "?layer=auto"))
    assert control_query(shown, "Approve all")["layer"] == ["auto"]
    assert hidden_values(shown, "layer") == ["auto"]


# --- a failed read -------------------------------------------------------------------------------


def test_a_failed_registry_read_replaces_the_tab_content(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 503, json=error_json("database_unavailable"))
    html = tab(operator_client)
    assert "Engine error 503 database_unavailable" in html
    assert 'aria-label="Inspect tabs"' in html  # the tab bar is kept
    # Complement: no partial rows, header or rules.
    assert "predicate-row" not in html
    assert "Configuration revision" not in html
    assert "Implication rules" not in html


def test_a_404_registry_read_is_convention_10s_text(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 404, json=error_json("not_found"))
    html = tab(operator_client)
    assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    assert "predicate-row" not in html


def test_the_tab_is_served_under_lite(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite())
    assert row_ids(tab(operator_client)) == [LOC, HOLDS, CONT, SWORN, GUARDS]


# --- the confirm step ----------------------------------------------------------------------------


def test_the_confirm_get_makes_no_engine_call(operator_client: Client, engine: FakeEngine) -> None:
    shown = proposal_html(tab(operator_client))
    url = re.search(r'<a class="button" href="([^"]+)">Approve all</a>', shown)
    assert url is not None
    before = len(engine.requests)
    response = operator_client.get(url.group(1).replace("&amp;", "&"))
    html = response.content.decode()
    assert len(engine.requests) == before
    assert (
        "Approve the whole proposal for <em>guards</em>? It changes: cardinality, qualifiers. The "
        "story's configuration revision moves by one; existing propositions it would flag are "
        "listed, never changed."
    ) in html.replace("&#x27;", "'")
    assert hidden_values(html, "confirmed") == ["1"]
    assert hidden_values(html, "offered") == ["cardinality", "qualifiers"]
    assert (
        hidden_values(html, "idempotency_key")
        == control_query(shown, "Approve all")["idempotency_key"]
    )
    assert '<button type="submit">Approve</button>' in html
    assert 'type="checkbox"' not in html
    assert f'<a href="{TAB}">Cancel</a>' in html


def test_the_reject_confirm(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(
        f"{REJECT_URL}?name=guards&offered=cardinality&layer=auto&idempotency_key={KEY}"
    ).content.decode()
    assert (
        "Reject the proposal for <em>guards</em>? Nothing changes; the Engine won't file the "
        "same proposal again."
    ) in html.replace("&#x27;", "'")
    assert '<button type="submit">Reject</button>' in html
    assert hidden_values(html, "idempotency_key") == [KEY]
    assert f'<a href="{TAB}?layer=auto">Cancel</a>' in html
    assert posts(engine) == []


def test_the_approve_selected_confirm_renders_the_checkboxes(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(
        f"{APPROVE_URL}?name=guards&offered=cardinality&offered=qualifiers&layer=all"
        f"&mode=selected&field=qualifiers&idempotency_key={KEY}"
    ).content.decode()
    assert "Fields you leave unticked are dropped: the proposal closes." in html
    assert '<input type="checkbox" name="field" value="cardinality"> ' in html
    assert '<input type="checkbox" name="field" value="qualifiers" checked> ' in html
    assert hidden_values(html, "mode") == ["selected"]
    assert posts(engine) == []


def test_a_post_without_confirmed_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = confirm(operator_client, confirmed="")
    assert response.status_code == 200
    html = response.content.decode()
    assert hidden_values(html, "confirmed") == ["1"]
    assert hidden_values(html, "idempotency_key") == [KEY]
    assert posts(engine) == []


# --- the call ------------------------------------------------------------------------------------


def test_approve_all_sends_an_empty_body_with_the_tabs_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = confirm(operator_client)
    assert response.status_code == 200
    (sent,) = posts(engine)
    assert sent.url.path == APPROVE_PATH
    assert sent_body(sent) == {}
    assert sent.headers["Idempotency-Key"] == KEY
    assert "X-Epistrel-Expected-Revision" not in sent.headers


def test_approve_selected_sends_only_the_ticked_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    confirm(operator_client, mode="selected", field=["qualifiers"])
    (sent,) = posts(engine)
    # Complement: the unticked field is absent.
    assert sent_body(sent) == {"changes": ["qualifiers"]}


def test_approve_selected_keeps_the_offered_order_and_drops_a_forged_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    confirm(operator_client, mode="selected", field=["symmetric", "qualifiers", "cardinality"])
    (sent,) = posts(engine)
    assert sent_body(sent) == {"changes": ["cardinality", "qualifiers"]}
    assert "symmetric" not in sent.content.decode()


def test_approve_selected_with_nothing_ticked_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = confirm(operator_client, mode="selected", field=["symmetric"])
    html = response.content.decode()
    assert '<p class="errorlist confirm-error">Choose at least one field to approve</p>' in html
    assert hidden_values(html, "idempotency_key") == [KEY]
    assert hidden_values(html, "confirmed") == ["1"]
    assert posts(engine) == []


def test_reject_sends_no_body(operator_client: Client, engine: FakeEngine) -> None:
    confirm(operator_client, REJECT_URL)
    (sent,) = posts(engine)
    assert sent.url.path == REJECT_PATH
    assert sent.content == b""
    assert sent.headers["Idempotency-Key"] == KEY


def test_each_decision_is_recorded_once(operator_client: Client, engine: FakeEngine) -> None:
    confirm(operator_client)
    confirm(operator_client, REJECT_URL, idempotency_key="01JB2Z8Y5Q3W6K9M1N4P7R0K02")
    rows = list(EngineOperation.objects.order_by("id"))
    assert [(r.kind, r.entity_type, r.entity_id, r.story_id) for r in rows] == [
        ("approve_predicate", "story", STORY, STORY),
        ("reject_predicate", "story", STORY, STORY),
    ]
    assert rows[0].idempotency_key == KEY


# --- the result page -----------------------------------------------------------------------------


def test_the_approval_result(operator_client: Client, engine: FakeEngine) -> None:
    response = confirm(operator_client, layer="auto")
    html = response.content.decode()
    assert "<title>Predicate decision · guards — Epistrel Console</title>" in html
    assert 'Status: <span title="approved">Approved</span>' in html
    assert "Configuration revision 7 → 8" in html
    applied = html.split('<table class="predicate-applied">')[1].split("</table>")[0]
    assert [cells(r) for r in re.findall(r"<tr>(<td.*?)</tr>", applied, re.S)] == [
        ["cardinality", "functional"],
        ["max_cardinality", "1"],
    ]
    # The predicate as it now is, by the tab's own row partial; no proposal sub-row.
    now = row(html, GUARDS)
    assert cells(now)[3] == "functional"
    assert '<tr class="proposal-row">' not in html
    violations = html.split('<table class="predicate-violations">')[1].split("</table>")[0]
    found = re.findall(r'<tr class="violation-row">(.*?)</tr>', violations, re.S)
    assert [cells(r) for r in found] == [
        ["…4P7R0EN1", "cardinality", "…4P7R0FA1, …4P7R0FA2"],
        ["…4P7R0EN2", "cardinality", "…4P7R0FA3, …4P7R0FA4"],
    ]
    assert "Already decided" not in html
    assert f'<a href="{TAB}?layer=auto">← Predicates</a>' in html
    pk = EngineOperation.objects.get().pk
    assert f'<a href="{DECISION_URL}?report={pk}">Link to this result</a>' in html
    assert "&quot;timeline_revision&quot;: 12" in html


def test_no_violations(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("predicate_decision_approved")
    body["violations"] = []
    engine.respond("POST", APPROVE, 200, json=body)
    html = confirm(operator_client).content.decode()
    assert '<p class="no-violations">No existing propositions are flagged</p>' in html


def test_the_rejection_result(operator_client: Client, engine: FakeEngine) -> None:
    html = confirm(operator_client, REJECT_URL).content.decode()
    assert "Rejected; nothing changed" in html
    assert "Configuration revision 7</p>" in html
    # Complement: no "→" revision line, no applied table, no violations.
    assert "→ 7" not in html
    assert "Configuration revision 6" not in html
    assert "predicate-applied" not in html
    assert "Flagged propositions" not in html
    assert cells(row(html, GUARDS))[3] == "multi"


def test_the_result_page_names_only_the_decided_predicate(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("predicate_decision_approved")
    body["predicate"]["inverse_of"] = SWORN
    body["predicate"]["implications"] = [{"when": GUARDS, "then": HOLDS, "via": None}]
    engine.respond("POST", APPROVE, 200, json=body)
    now = row(confirm(operator_client).content.decode(), GUARDS)
    assert f'<a href="{TAB}?layer=all#p-{SWORN}"><code title="{SWORN}">…4P7R0PR4</code></a>' in now
    assert f'<a href="{TAB}?layer=all#p-{GUARDS}" title="{GUARDS}">guards</a>' in now


def test_the_result_page_makes_no_read(operator_client: Client, engine: FakeEngine) -> None:
    confirm(operator_client)
    assert [r.method for r in engine.requests] == ["POST"]


def test_a_replay_says_already_decided(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        APPROVE,
        200,
        json=fixture("predicate_decision_approved"),
        headers={"Idempotent-Replay": "true"},
    )
    html = confirm(operator_client).content.decode()
    assert "Already decided — this is the Engine's recorded answer" in html


def test_the_report_rebuilds_the_page_with_no_engine_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    confirm(operator_client, layer="auto")
    pk = EngineOperation.objects.get().pk
    before = len(engine.requests)
    html = operator_client.get(f"{DECISION_URL}?report={pk}").content.decode()
    assert len(engine.requests) == before
    assert "Configuration revision 7 → 8" in html
    assert "predicate-violations" in html
    assert f'<a href="{TAB}">← Predicates</a>' in html  # unfiltered from ?report=


def test_the_report_of_a_replay_says_already_decided(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        REJECT,
        200,
        json=fixture("predicate_decision_rejected"),
        headers={"Idempotent-Replay": "true"},
    )
    confirm(operator_client, REJECT_URL)
    pk = EngineOperation.objects.get().pk
    html = operator_client.get(f"{DECISION_URL}?report={pk}").content.decode()
    assert "Already decided" in html
    assert "Rejected; nothing changed" in html


def _row(**over: Any) -> EngineOperation:
    values: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "story_id": STORY,
        "kind": "approve_predicate",
        "http_method": "POST",
        "path": APPROVE_PATH,
        "outcome": EngineOperation.Outcome.OK,
        "response": fixture("predicate_decision_approved"),
    }
    values.update(over)
    return EngineOperation.objects.create(**values)


@pytest.mark.parametrize(
    "over",
    [
        {"story_id": OTHER_STORY},
        {"kind": "pin_memory"},
        {"outcome": EngineOperation.Outcome.ERROR, "response": error_json("not_found")},
        {"response": {"predicate": "nope"}},
    ],
)
def test_a_report_that_is_not_a_decision_of_this_story_is_not_found(
    operator_client: Client, engine: FakeEngine, over: dict[str, Any]
) -> None:
    pk = _row(**over).pk
    html = operator_client.get(f"{DECISION_URL}?report={pk}").content.decode()
    assert NOT_FOUND in html
    assert f'<a href="{TAB}">← Predicates</a>' in html
    assert "Configuration revision" not in html
    assert engine.requests == []


@pytest.mark.parametrize("value", ["", "abc", "999999"])
def test_a_missing_or_unparseable_report_is_not_found(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    html = operator_client.get(f"{DECISION_URL}?report={value}").content.decode()
    assert NOT_FOUND in html
    assert engine.requests == []


def test_a_stored_decision_of_this_story_renders(
    operator_client: Client, engine: FakeEngine
) -> None:
    pk = _row().pk
    html = operator_client.get(f"{DECISION_URL}?report={pk}").content.decode()
    assert "Configuration revision 7 → 8" in html


# --- refusals ------------------------------------------------------------------------------------


def test_no_pending_proposal_redirects_with_the_flash(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", APPROVE, 404, json=error_json("not_found", reason="no_pending_proposal"))
    response = confirm(operator_client, layer="auto")
    assert response.status_code == 302
    assert response["Location"] == f"{TAB}?layer=auto"
    assert flashes(response) == [
        ("warning", "That proposal is no longer pending — the registry has been reloaded")
    ]
    # Complement: not the generic error panel.
    assert "Engine error" not in response.content.decode()


def test_another_404_is_the_not_found_text(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", REJECT, 404, json=error_json("not_found"))
    response = confirm(operator_client, REJECT_URL)
    html = response.content.decode()
    assert NOT_FOUND in html
    assert f'<a href="{TAB}">← Predicates</a>' in html
    assert EngineOperation.objects.get().outcome == "error"


@pytest.mark.parametrize("url", [APPROVE_URL, REJECT_URL])
def test_a_422_re_renders_the_confirm_page_with_the_message(
    operator_client: Client, engine: FakeEngine, url: str
) -> None:
    answer = error_json(
        "validation_error",
        "not proposed",
        field="body.changes",
        reason="not_proposed",
        fields=["symmetric", "weak"],
    )
    engine.respond("POST", APPROVE if url == APPROVE_URL else REJECT, 422, json=answer)
    response = confirm(operator_client, url)
    html = response.content.decode()
    assert '<p class="errorlist confirm-error">not proposed Fields: symmetric, weak</p>' in html
    assert hidden_values(html, "confirmed") == ["1"]
    assert hidden_values(html, "idempotency_key") != [KEY]  # a fresh key for the next attempt
    assert len(posts(engine)) == 1


def test_a_422_without_fields_shows_the_message_alone(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", APPROVE, 422, json=error_json("validation_error", "bad id"))
    html = confirm(operator_client).content.decode()
    assert '<p class="errorlist confirm-error">bad id</p>' in html


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "story_archived"),
        (409, "revision_conflict"),
        (409, "idempotency_conflict"),
        (423, "story_busy"),
        (503, "database_unavailable"),
    ],
)
def test_shared_refusals_are_43s_panel(
    operator_client: Client, engine: FakeEngine, status: int, code: str
) -> None:
    engine.respond("POST", APPROVE, status, json=error_json(code))
    html = confirm(operator_client).content.decode()
    assert f"Engine error {status} {code}" in html
    assert EngineOperation.objects.get().error_code == code


def test_erasure_pending_is_the_held_wrapper(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", APPROVE, 503, json=error_json("erasure_pending"))
    response = confirm(operator_client)
    assert response.status_code == 503


# --- the shared helpers --------------------------------------------------------------------------


def test_names_maps_ids_to_names() -> None:
    parsed = PredicateList.from_json(registry())
    assert parsed is not None
    assert parsed.names()[GUARDS] == "guards"
    assert len(parsed.names()) == 5


def test_predicate_names_degrades_to_no_names(engine: FakeEngine, rf: Any, operator: Any) -> None:
    request = rf.get("/")
    request.user = operator
    client = engine_client_for(request)
    client._transport = engine.transport
    assert predicate_names(request, client, STORY)[LOC] == "located_at"
    engine.respond("GET", LIST, 503, json=error_json("database_unavailable"))
    assert predicate_names(request, client, STORY) == {}


def test_a_value_cell_takes_a_single_id_a_list_or_null() -> None:
    refs = Refs(STORY, {LOC: "located_at"})
    assert [r.name for r in value_cell("containment_of", LOC, refs).refs] == ["located_at"]
    assert [r.id for r in value_cell("exclusions", [LOC, UNKNOWN], refs).refs] == [LOC, UNKNOWN]
    assert value_cell("containment_of", None, refs).text == "—"
    assert value_cell("symmetric", True, refs).text == "true"
    assert value_cell("key_qualifiers", ["a", "b"], refs).text == "a, b"
    assert value_cell("qualifiers", {}, refs).text == "none declared"
    assert value_cell("qualifiers", None, refs).text == "undeclared"

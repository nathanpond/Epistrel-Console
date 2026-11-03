"""The World tab's Proposed States (#266): the section, a 202 analysis, approve (a form page, its
result and report pages) and dismiss (a confirmed-mode confirm page).

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``: revision 12). Fixtures: #232's ``state_proposals.json``
(``lantern_oil`` proposed with two evidence beats, ``rope`` dismissed), ``analysis_queued.json``,
``proposal_approved.json`` and ``proposal_dismissed.json``; this story's
``state_proposals_mixed.json`` (``coins`` approved with a ``definition_id`` and a ``null`` span,
``lamp`` proposed for Alice, ``arrows`` with the unknown status ``superseded``) and
``state_proposals_bad_item.json`` (D15: a ``torches`` entry without ``suggested_type``). Refusal
bodies are built inline (D16)."""

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
from console.forms.states import ALLOWED_REQUIRED, STATE_NAME_INVALID
from console.held import held_text
from console.inspect import history_turn_url
from console.models import EngineOperation
from console.state_proposals import (
    ANALYSIS_NOTICE,
    EMPTY_TEXT,
    UNPARSED_TEXT,
)
from console.views import inspect as views
from console.views import inspect_state_proposals as proposal_views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
PENDING = "01JM6PP0000000000000000001"  # lantern_oil, proposed
DISMISSED = "01JM6PP0000000000000000002"  # rope, dismissed
ABSENT = "01JM6PP0000000000000000099"
KEY = "01JM6QP00000000000000000K1"
JOB = "01JM6JB0000000000000000001"

PROPOSALS = "/stories/{story_id}/state-proposals"
ANALYZE = "/stories/{story_id}/state-proposals/analyze"
APPROVE = "/stories/{story_id}/state-proposals/{proposal_id}/approve"
DISMISS = "/stories/{story_id}/state-proposals/{proposal_id}/dismiss"
PROPOSALS_PATH = f"/stories/{STORY}/state-proposals"
ANALYZE_PATH = f"{PROPOSALS_PATH}/analyze"
SHELL_PATHS = [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]

URL = f"/stories/{STORY}/inspect/world/states/proposals/"
ANALYZE_URL = f"{URL}analyze/"
APPROVED_URL = f"{URL}approved/"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
NOT_PENDING = "This proposal is no longer pending — reload the list"
REVISION_HEADER = "X-Epistrel-Expected-Revision"


def approve_url(proposal_id: str = PENDING) -> str:
    return f"{URL}{proposal_id}/approve/"


def dismiss_url(proposal_id: str = PENDING) -> str:
    return f"{URL}{proposal_id}/dismiss/"


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
    monkeypatch.setattr(proposal_views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v3())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond("GET", PROPOSALS, 200, json=fixture("state_proposals"))
    fake.respond("POST", ANALYZE, 202, json=fixture("analysis_queued"))
    fake.respond("POST", APPROVE, 201, json=fixture("proposal_approved"))
    fake.respond("POST", DISMISS, 200, json=fixture("proposal_dismissed"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def calls(engine: FakeEngine, method: str, suffix: str = "") -> list[Any]:
    return [
        r
        for r in engine.requests
        if r.method == method
        and r.url.path.startswith(PROPOSALS_PATH)
        and r.url.path.endswith(suffix)
    ]


def reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "GET" and r.url.path == PROPOSALS_PATH]


def approvals(engine: FakeEngine) -> list[Any]:
    return calls(engine, "POST", "/approve")


def dismissals(engine: FakeEngine) -> list[Any]:
    return calls(engine, "POST", "/dismiss")


def analyses(engine: FakeEngine) -> list[Any]:
    return calls(engine, "POST", "/analyze")


def get(client: Client, url: str = URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def text_of(html: str) -> str:
    """The visible text: tags stripped, entities unescaped, whitespace collapsed."""
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", body))).strip()
    return re.sub(r"\s+([).,?!])", r"\1", text)


def row(html: str, proposal_id: str) -> str:
    found = re.search(rf'<tr id="proposal-{proposal_id}"[^>]*>(.*?)</tr>', html, re.S)
    assert found is not None, proposal_id
    return found.group(1)


def cell(row_html: str, name: str) -> str:
    found = re.search(rf'<td class="{name}">(.*?)</td>', row_html, re.S)
    assert found is not None, name
    return found.group(1)


def row_ids(html: str) -> list[str]:
    return re.findall(r'<tr id="proposal-([0-9A-Z]+)"', html)


def messages_of(html: str) -> list[tuple[str, str]]:
    found = re.search(r'<ul class="messages">(.*?)</ul>', html, re.S)
    if found is None:
        return []
    return [
        (tag, text_of(body))
        for tag, body in re.findall(r'<li class="([^"]*)">(.*?)</li>', found.group(1), re.S)
    ]


def mixed(engine: FakeEngine) -> None:
    engine.respond("GET", PROPOSALS, 200, json=fixture("state_proposals_mixed"))


# --- the section --------------------------------------------------------------------------------


def test_section_reads_and_notice(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert [r.url.path for r in engine.requests] == [*SHELL_PATHS, PROPOSALS_PATH]
    assert escape(ANALYSIS_NOTICE) in html
    form = re.search(r'<form method="post" action="([^"]+)" class="proposals-analyze">', html)
    assert form is not None
    assert form.group(1) == ANALYZE_URL
    assert re.search(r'name="idempotency_key" value="[0-9A-Z]{26}"', html)
    assert '<button type="submit">Run an analysis</button>' in html
    assert '<p class="proposals-count">2 proposals · 1 pending</p>' in html
    assert row_ids(html) == [PENDING, DISMISSED]  # the Engine's order
    assert "<h2>Proposed States</h2>" in html
    assert 'aria-current="page" href="' + URL + '">Proposed States</a>' in html


def test_rows_render_each_column(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    pending = row(html, PENDING)
    assert cell(pending, "name") == "lantern_oil"
    assert text_of(cell(pending, "suggests")) == "Number · Story · unit flasks"
    evidence = cell(pending, "evidence")
    assert '<p class="recurs">Recurs in 2 turns</p>' in evidence
    links = re.findall(r'<li><a href="([^"]+)">(.*?)</a></li>', evidence)
    assert [(unescape(u), unescape(t)) for u, t in links] == [
        (
            history_turn_url(STORY, 1),
            "Turn 1: “Mara tips the last of the lantern oil into the lamp.”",
        ),
        (history_turn_url(STORY, 3), "Turn 3: “Two flasks of lantern oil, Bob says.”"),
    ]
    assert cell(pending, "status") == '<span title="proposed">Proposed</span>'
    assert "2026-10-31 18:01:00 UTC" in cell(pending, "proposed")
    assert cell(pending, "decided") == "—"
    actions = cell(pending, "actions")
    assert f'href="{approve_url()}"' in actions
    assert "Approve…" in actions
    assert "Dismiss…" in actions
    dismiss = re.search(r'class="proposal-dismiss" href="([^"]+)"', actions)
    assert dismiss is not None
    assert unescape(dismiss.group(1)).startswith(f"{dismiss_url()}?name=lantern_oil&next=")
    more = text_of(cell(pending, "more"))
    assert f"Proposal id {PENDING}" in more
    assert "Analysis request 01JM6QP0000000000000000031" in more
    assert "Turn 1 evidence event 01JM6EV0000000000000000091 · span 16\u201332" in more
    assert (
        "Turn 3 evidence event 01JM6EV0000000000000000092 · span — (declared by the beat)" in more
    )
    assert "Declared State" not in more
    # The dismissed one: no actions.
    done = row(html, DISMISSED)
    assert cell(done, "status") == '<span title="dismissed">Dismissed</span>'
    assert cell(done, "actions") == "—"
    assert "2026-10-31 18:04:00 UTC" in cell(done, "decided")
    assert '<p class="recurs">Recurs in 1 turn</p>' in cell(done, "evidence")


def test_approved_row_more_names_declared_state(
    operator_client: Client, engine: FakeEngine
) -> None:
    mixed(engine)
    html = get(operator_client)
    coins = row(html, "01JM6PP0000000000000000011")
    more = text_of(cell(coins, "more"))
    assert "Declared State 01JM6SD0000000000000000012" in more
    assert "span — (declared by the beat)" in more
    assert cell(coins, "actions") == "—"
    assert '<p class="recurs">Recurs in 3 turns</p>' in cell(coins, "evidence")
    assert '<p class="proposals-count">3 proposals · 1 pending</p>' in html


def test_entity_suggestion_uses_cast_label(operator_client: Client, engine: FakeEngine) -> None:
    mixed(engine)
    lamp = row(get(operator_client), "01JM6PP0000000000000000012")
    suggests = cell(lamp, "suggests")
    assert text_of(suggests) == "Text · Story · Alice"
    assert f'<span class="character" title="{ALICE}">Alice</span>' in suggests


def test_entity_suggestion_without_cast_is_short_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    mixed(engine)
    engine.respond("GET", STORY_PATH, 404, json=error_json("not_found"))
    lamp = row(get(operator_client), "01JM6PP0000000000000000012")
    assert "Alice" not in cell(lamp, "suggests")
    assert ALICE[-6:] in cell(lamp, "suggests") or ALICE in cell(lamp, "suggests")


def test_unknown_status_humanised_no_actions(operator_client: Client, engine: FakeEngine) -> None:
    mixed(engine)
    arrows = row(get(operator_client), "01JM6PP0000000000000000013")
    assert cell(arrows, "status") == '<span title="superseded">Superseded</span>'
    assert cell(arrows, "actions") == "—"


def test_evidence_cap_reads_at_least(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("state_proposals")
    one = body["proposals"][0]
    one["evidence"] = [
        {"event_id": f"01JM6EV00000000000000002{i:02d}", "turn": i, "text": f"t{i}", "span": None}
        for i in range(1, 11)
    ]
    engine.respond("GET", PROPOSALS, 200, json=body)
    evidence = cell(row(get(operator_client), PENDING), "evidence")
    assert "Recurs in at least 10 turns (the Engine keeps the earliest 10 quotes)" in unescape(
        evidence
    )


def test_empty_text(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PROPOSALS, 200, json={"proposals": []})
    html = get(operator_client)
    assert f'<p class="none proposals-none">{escape(EMPTY_TEXT)}</p>' in html
    assert "proposals-count" not in html
    assert "<table" not in html.split("proposals-analyze")[1]


def test_unparsed_notice(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("state_proposals_bad_item")
    engine.side_effect("GET", PROPOSALS, lambda request: httpx.Response(200, json=body))
    html = get(operator_client)
    assert row_ids(html) == [PENDING]
    notice = re.search(r'<div class="notice proposals-unparsed">(.*?)</div>', html, re.S)
    assert notice is not None
    assert escape(UNPARSED_TEXT) in notice.group(1)
    assert "torches" in notice.group(1)
    assert "lantern_oil" not in notice.group(1)  # only the rejected entry
    assert '<p class="proposals-count">1 proposal · 1 pending</p>' in html
    assert "proposals-none" not in html


def test_unparsed_only_has_no_empty_text(operator_client: Client, engine: FakeEngine) -> None:
    body = {"proposals": [fixture("state_proposals_bad_item")["proposals"][1]]}
    engine.side_effect("GET", PROPOSALS, lambda request: httpx.Response(200, json=body))
    html = get(operator_client)
    assert "proposals-unparsed" in html
    assert "proposals-none" not in html
    assert "proposals-count" not in html


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
    assert reads(engine) == []
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert "states.level" in html
    assert "proposals-analyze" not in html


def test_module_off_after_gate_is_greyed_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PROPOSALS, 409, json=error_json("module_off", module="states"))
    html = get(operator_client)
    assert len(reads(engine)) == 1
    assert "configuration changed since" in html
    assert "proposals-analyze" not in html


def test_404_is_not_found_line(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PROPOSALS, 404, json=error_json("not_found"))
    html = get(operator_client)
    assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    assert "proposals-analyze" in html


def test_503_panel_keeps_button(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PROPOSALS, 503, json=error_json("unavailable"))
    html = get(operator_client)
    before, _, after = html.partition('class="proposals-analyze"')
    assert "Run an analysis" in after
    assert '<div class="engine-error' in after
    assert '<div class="engine-error' not in before


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        PROPOSALS,
        503,
        json=error_json("erasure_pending"),
        headers={"Retry-After": "30"},
    )
    response = operator_client.get(URL)
    assert held_text("30") in response.content.decode()


# --- run an analysis ----------------------------------------------------------------------------


def analyze(client: Client, **data: str) -> str:
    response = client.post(ANALYZE_URL, {"idempotency_key": KEY, **data}, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [(URL, 302)]
    return response.content.decode()


def test_analyze_posts_once_and_flashes_job(operator_client: Client, engine: FakeEngine) -> None:
    html = analyze(operator_client)
    (call,) = analyses(engine)
    assert call.headers["Idempotency-Key"] == KEY
    assert REVISION_HEADER not in call.headers
    assert call.content == b""
    (message,) = messages_of(html)
    assert message[0] == "safe success"
    assert message[1] == (
        f"Analysis queued (job {JOB}). Proposals it finds appear here when the worker "
        "finishes — reload this page to see them. The Engine reports no progress for the job, "
        "so the Console can't say when it's done."
    )
    assert f"<code>{JOB}</code>" in html
    row = EngineOperation.objects.get(kind="analyze_state_proposals")
    assert row.outcome == EngineOperation.Outcome.QUEUED


def test_replayed_analyze_says_already_queued(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        ANALYZE,
        202,
        json=fixture("analysis_queued"),
        headers={"Idempotent-Replay": "true"},
    )
    html = analyze(operator_client)
    assert messages_of(html) == [("info", f"Already queued (repeated submit) — job {JOB}.")]


@pytest.mark.parametrize("method", ["get", "head", "put"])
def test_analyze_get_is_405(operator_client: Client, engine: FakeEngine, method: str) -> None:
    response = getattr(operator_client, method)(ANALYZE_URL)
    assert response.status_code == 405
    assert engine.requests == []


@pytest.mark.parametrize(
    "config",
    [lite_m6(), structured_v3(states__level="off")],
    ids=["lite_m6", "states_off"],
)
def test_analyze_gate_closed_zero_calls(
    operator_client: Client, engine: FakeEngine, config: dict[str, Any]
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=config)
    html = analyze(operator_client)
    assert analyses(engine) == []
    ((tag, text),) = messages_of(html)
    assert tag == "error"
    assert "states.level" in text


@pytest.mark.parametrize(
    ("status", "code", "details", "expected"),
    [
        (409, "module_off", {"module": "states"}, "states.level"),
        (423, "story_busy", {"reason": "in_flight"}, "Story busy"),
        (409, "story_archived", {}, "archived"),
        (409, "story_paused", {}, "paused"),
    ],
)
def test_analyze_refusals_flash(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
    expected: str,
) -> None:
    engine.respond("POST", ANALYZE, status, json=error_json(code, **details))
    html = analyze(operator_client)
    ((tag, text),) = messages_of(html)
    assert tag == "error"
    assert expected in text
    assert "Config" not in text  # C5's flash rule: the link dropped
    assert len(analyses(engine)) == 1


def test_analyze_unknown_outcome_renders_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", ANALYZE, httpx.ConnectError("down"))
    response = operator_client.post(ANALYZE_URL, {"idempotency_key": KEY})
    assert response.status_code == 200
    html = response.content.decode()
    after = html.partition('class="proposals-analyze"')[2]
    assert '<div class="engine-error' in after
    assert row_ids(html) == [PENDING, DISMISSED]


def test_analyze_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", ANALYZE, 503, json=error_json("erasure_pending"), headers={"Retry-After": "30"}
    )
    response = operator_client.post(ANALYZE_URL, {"idempotency_key": KEY})
    assert held_text("30") in response.content.decode()


def test_never_says_finished(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** no page says an analysis finished or completed."""
    pages = [analyze(operator_client), get(operator_client)]
    engine.respond(
        "POST",
        ANALYZE,
        202,
        json=fixture("analysis_queued"),
        headers={"Idempotent-Replay": "true"},
    )
    pages.append(analyze(operator_client))
    for html in pages:
        text = text_of(html)
        assert not re.search(r"\b(finished|complete|completed)\b", text, re.I), text


# --- approve ------------------------------------------------------------------------------------


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


def is_form(html: str) -> bool:
    return 'class="proposal-approve-form"' in html


def suggested_post(**fields: Any) -> dict[str, Any]:
    """The approve form as rendered untouched (the suggestion), with ``fields`` laid over."""
    data: dict[str, Any] = {
        "idempotency_key": KEY,
        "expected_revision": "12",
        "name": "lantern_oil",
        "type": "number",
        "scope": "story",
        "unit": "flasks",
        "entity_id": "",
    }
    data.update(fields)
    return {k: v for k, v in data.items() if v is not None}


def approve(client: Client, proposal_id: str = PENDING, **fields: Any) -> str:
    response = client.post(approve_url(proposal_id), suggested_post(**fields))
    assert response.status_code == 200
    return response.content.decode()


def sent(engine: FakeEngine) -> dict[str, Any]:
    (call,) = approvals(engine)
    body: dict[str, Any] = json.loads(call.content)
    return body


def refusal(html: str) -> str:
    found = re.search(
        r'<p class="errorlist proposal-approve-refusal" role="alert">(.*?)</p>', html, re.S
    )
    assert found is not None
    return found.group(1)


def test_form_starts_from_suggestion(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, approve_url())
    assert [r.url.path for r in engine.requests] == [*SHELL_PATHS, PROPOSALS_PATH]
    assert "<title>Inspect · Approve “lantern_oil” · " in html
    assert '<h3 class="proposal-approve-title">Approve “lantern_oil”</h3>' in html
    assert is_form(html)
    assert 'value="lantern_oil"' in field(html, "name")
    assert 'value="number" selected' in field(html, "type")
    assert 'value="story" selected' in field(html, "scope")
    assert "Engine default keeps the suggestion" in field(html, "scope")
    assert 'value="flasks"' in field(html, "unit")
    assert "Blank keeps the suggestion" in field(html, "unit")
    assert 'name="owner"' not in html
    assert 'name="provider_id"' not in html
    assert hidden(html, "expected_revision") == "12"
    assert re.fullmatch(r"[0-9A-Z]{26}", hidden(html, "idempotency_key") or "")
    assert '<button type="submit" class="proposal-approve-submit">Approve</button>' in html
    assert "Unchanged fields approve as suggested" in html
    summary = re.search(r'<dl class="proposal-summary">(.*?)</dl>', html, re.S)
    assert summary is not None
    assert "Number · Story · unit flasks" in summary.group(1)
    assert "Recurs in 2 turns" in summary.group(1)
    assert unescape(history_turn_url(STORY, 3)) in unescape(summary.group(1))
    # The sub-navigation marks Proposed States current.
    assert 'aria-current="page" href="' + URL + '">Proposed States</a>' in html


def test_form_keeps_unknown_suggestion_and_names_entity(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("state_proposals_mixed")
    lamp = body["proposals"][1]
    lamp["suggested_type"] = "list"
    lamp["suggested_scope"] = "realm"
    engine.respond("GET", PROPOSALS, 200, json=body)
    html = get(operator_client, approve_url(lamp["id"]))
    assert 'value="list" selected>List</option>' in field(html, "type")
    assert 'value="realm" selected>Realm</option>' in field(html, "scope")
    assert f'value="{ALICE}"' in field(html, "entity_id")
    assert "Suggested: Alice" in field(html, "entity_id")


def test_untouched_approve_sends_empty_body(operator_client: Client, engine: FakeEngine) -> None:
    approve(operator_client)
    (call,) = approvals(engine)
    assert json.loads(call.content) == {}
    assert call.headers[REVISION_HEADER] == "12"
    assert call.headers["Idempotency-Key"] == KEY
    assert call.url.path == f"{PROPOSALS_PATH}/{PENDING}/approve"


def test_changed_name_sends_only_overrides(operator_client: Client, engine: FakeEngine) -> None:
    approve(operator_client, name="lamp_oil")
    assert sent(engine) == {"definition": {"name": "lamp_oil"}}


def test_changed_name_and_min(operator_client: Client, engine: FakeEngine) -> None:
    approve(operator_client, name="lamp_oil", constraints_min="0")
    body = sent(engine)
    assert body == {"definition": {"name": "lamp_oil", "constraints": {"min": 0}}}
    # **Complement:** never an owner or provider.
    assert "owner" not in body["definition"]
    assert "provider_id" not in body["definition"]


def test_owner_and_provider_posted_are_ignored(operator_client: Client, engine: FakeEngine) -> None:
    approve(operator_client, owner="external", provider_id="bank")
    assert sent(engine) == {}


def test_other_members_and_changed_suggestions(operator_client: Client, engine: FakeEngine) -> None:
    approve(
        operator_client,
        scope="character",
        unit="jars",
        visibility="hidden",
        aliases="oil\nfuel",
    )
    assert sent(engine) == {
        "definition": {
            "scope": "character",
            "unit": "jars",
            "visibility": "hidden",
            "aliases": ["oil", "fuel"],
        }
    }


@pytest.mark.parametrize("scope", ["", "story"])
def test_engine_default_scope_is_unchanged(
    operator_client: Client, engine: FakeEngine, scope: str
) -> None:
    approve(operator_client, scope=scope)
    assert sent(engine) == {}


def test_blank_unit_keeps_suggestion(operator_client: Client, engine: FakeEngine) -> None:
    approve(operator_client, unit="", name="lamp_oil")
    body = sent(engine)
    assert "unit" not in body["definition"]
    assert body == {"definition": {"name": "lamp_oil"}}


def test_blank_entity_keeps_suggestion(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("state_proposals_mixed")
    engine.respond("GET", PROPOSALS, 200, json=body)
    lamp = body["proposals"][1]["id"]
    approve(operator_client, lamp, name="lamp", type="text", unit="", entity_id="")
    assert sent(engine) == {}


@pytest.mark.parametrize(
    ("fields", "name", "error"),
    [
        ({"rules": "{not json"}, "rules", None),
        ({"name": "9lives"}, "name", STATE_NAME_INVALID),
        ({"type": "enum"}, "constraints_allowed_values", ALLOWED_REQUIRED),
    ],
    ids=["bad_json_box", "bad_name", "enum_without_allowed"],
)
def test_invalid_overrides_zero_calls(
    operator_client: Client,
    engine: FakeEngine,
    fields: dict[str, Any],
    name: str,
    error: str | None,
) -> None:
    html = approve(operator_client, **fields)
    assert approvals(engine) == []
    assert is_form(html)
    found = errors(html, name)
    assert found
    if error is not None:
        assert found == [error]
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "12"


@pytest.mark.parametrize("proposal_id", [DISMISSED, ABSENT])
def test_not_pending_shows_sentence_no_form(
    operator_client: Client, engine: FakeEngine, proposal_id: str
) -> None:
    for html in (
        get(operator_client, approve_url(proposal_id)),
        approve(operator_client, proposal_id),
    ):
        assert f'<p class="warning proposal-not-pending">{NOT_PENDING}</p>' in html
        assert not is_form(html)
        assert "Back to Proposed States" in html
    assert approvals(engine) == []


def test_unparsed_id_is_not_pending(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("state_proposals_bad_item")
    engine.side_effect("GET", PROPOSALS, lambda request: httpx.Response(200, json=body))
    html = get(operator_client, approve_url("01JM6PP0000000000000000003"))
    assert NOT_PENDING in html
    assert not is_form(html)
    details = re.search(r'<details class="proposal-unparsed">(.*?)</details>', html, re.S)
    assert details is not None
    assert "torches" in details.group(1)


@pytest.mark.parametrize(
    "config",
    [lite_m6(), structured_v3(states__level="off")],
    ids=["lite_m6", "states_off"],
)
def test_approve_gate_closed_zero_calls(
    operator_client: Client, engine: FakeEngine, config: dict[str, Any]
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=config)
    for html in (get(operator_client, approve_url()), approve(operator_client, name="x_y")):
        assert "states.level" in html
        assert not is_form(html)
    assert reads(engine) == []
    assert approvals(engine) == []


def test_approve_read_failure_has_no_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PROPOSALS, 404, json=error_json("not_found"))
    html = approve(operator_client)
    assert NOT_FOUND in html
    assert not is_form(html)
    assert approvals(engine) == []


def result_lines(html: str) -> list[str]:
    found = re.search(r'<ul class="proposal-lines">(.*?)</ul>', html, re.S)
    assert found is not None
    return [unescape(li) for li in re.findall(r"<li>(.*?)</li>", found.group(1))]


def test_result_reuses_declared_page(operator_client: Client, engine: FakeEngine) -> None:
    html = approve(operator_client)
    assert '<h3 class="state-declared-title">Approved “lantern_oil”</h3>' in html
    assert "<title>Inspect · Approved “lantern_oil” · " in html
    assert result_lines(html) == [
        f"Proposal {PENDING} · Approved",
        "Declared as “lantern_oil”",
    ]
    assert '<dl class="state-definition">' in html
    assert ">Slot: <a" in html
    assert "turn 3⁺²" in html
    assert "Story revision now 13" in html
    links = re.search(r'<p class="declared-links">(.*?)</p>', html, re.S)
    assert links is not None
    text = text_of(links.group(1))
    assert text.startswith("See lantern_oil · Set its value · Back to Proposed States")
    assert f'href="/stories/{STORY}/inspect/world/state/lantern_oil/"' in links.group(1)
    assert "Declare another" not in text
    row = EngineOperation.objects.get(kind="approve_state_proposal")
    assert f'href="{APPROVED_URL}?report={row.pk}"' in links.group(1)
    assert 'aria-current="page" href="' + URL + '">Proposed States</a>' in html


def test_result_declared_as_override(operator_client: Client, engine: FakeEngine) -> None:
    answer = fixture("proposal_approved")
    answer["definition"]["name"] = "lamp_oil"
    engine.respond("POST", APPROVE, 201, json=answer)
    html = approve(operator_client, name="lamp_oil")
    assert '<h3 class="state-declared-title">Approved “lantern_oil”</h3>' in html
    assert result_lines(html)[1] == "Declared as “lamp_oil”"
    assert "See lamp_oil" in html


def test_report_page_rebuilds_from_row(operator_client: Client, engine: FakeEngine) -> None:
    approve(operator_client)
    row = EngineOperation.objects.get(kind="approve_state_proposal")
    engine.requests.clear()
    html = get(operator_client, f"{APPROVED_URL}?report={row.pk}")
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert "Approved “lantern_oil”" in html
    assert result_lines(html)[0] == f"Proposal {PENDING} · Approved"


def test_report_page_not_found_complements(operator_client: Client, engine: FakeEngine) -> None:
    approve(operator_client)
    row = EngineOperation.objects.get(kind="approve_state_proposal")
    other = EngineOperation.objects.create(
        kind="declare_state",
        http_method="POST",
        path=f"/stories/{STORY}/states",
        outcome=EngineOperation.Outcome.OK,
        story_id=STORY,
        response=fixture("state_declared"),
    )
    for query in ("", "?report=nope", f"?report={row.pk + 1000}", f"?report={other.pk}"):
        html = get(operator_client, f"{APPROVED_URL}{query}")
        assert NOT_FOUND in html, query
        assert "Back to Proposed States" in html
        assert "<title>Inspect · Approved a proposed State · " in html
    row.story_id = "01JM6ST0000000000000000009"
    row.save()
    assert NOT_FOUND in get(operator_client, f"{APPROVED_URL}?report={row.pk}")


def refuse(engine: FakeEngine, status: int, code: str, **details: Any) -> None:
    engine.respond("POST", APPROVE, status, json=error_json(code, **details))


@pytest.mark.parametrize(
    ("status", "code", "details"),
    [
        (422, "state_name_taken", {"state": "lantern_oil"}),
        (404, "not_found", {"reason": "no_pending_proposal"}),
        (423, "story_busy", {"reason": "in_flight"}),
        (409, "revision_conflict", {}),
        (409, "module_off", {"module": "states"}),
        (409, "idempotency_conflict", {}),
    ],
)
def test_approve_refusals(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
) -> None:
    refuse(engine, status, code, **details)
    if code == "revision_conflict":
        engine.respond("GET", STORY_PATH, 200, json={**fixture("story"), "timeline_revision": 14})
    html = approve(operator_client, name="lamp_oil")
    assert len(approvals(engine)) == 1
    if code == "state_name_taken":
        assert errors(html, "name") == ["A State named “lantern_oil” already exists"]
    elif code == "not_found":
        assert refusal(html) == NOT_PENDING
        assert not is_form(html)
        assert "Back to Proposed States" in html
    elif code == "story_busy":
        assert refusal(html).startswith("Story busy")
    elif code == "revision_conflict":
        assert "changed" in refusal(html)
        assert hidden(html, "expected_revision") == "14"
    elif code == "module_off":
        assert "states.level" in refusal(html)
        assert f'<a href="/stories/{STORY}/config/">Config</a>' in refusal(html)
    else:
        assert '<div class="engine-error' in html.split('class="proposal-approve-form"')[0]
    if is_form(html):
        assert 'value="lamp_oil"' in field(html, "name")
        assert hidden(html, "idempotency_key") not in (None, KEY)  # D8: a fresh key
    # **Complement:** the section still lists the proposal as proposed.
    engine.respond("GET", STORY_PATH, 200, json=fixture("story"))
    section = get(operator_client)
    assert '<span title="proposed">Proposed</span>' in row(section, PENDING)


def test_approve_unknown_outcome_keeps_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", APPROVE, httpx.ConnectError("down"))
    html = approve(operator_client, name="lamp_oil")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "12"
    assert '<div class="engine-error' in html.split('class="proposal-approve-form"')[0]


def test_approve_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", APPROVE, 503, json=error_json("erasure_pending"), headers={"Retry-After": "30"}
    )
    response = operator_client.post(approve_url(), suggested_post())
    assert held_text("30") in response.content.decode()


# --- dismiss ------------------------------------------------------------------------------------


def dismiss_post(client: Client, *, confirmed: bool = True, **data: str) -> Any:
    form = {"idempotency_key": KEY, "name": "lantern_oil", "next": URL, **data}
    if confirmed:
        form["confirmed"] = "1"
    return client.post(dismiss_url(), form)


def test_dismiss_get_makes_no_engine_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(f"{dismiss_url()}?name=lantern_oil&next={URL}")
    assert response.status_code == 200
    assert engine.requests == []
    html = response.content.decode()
    text = text_of(html)
    assert (
        "Dismiss the proposed State lantern_oil? Nothing is tracked, and the analysis never "
        "proposes it again."
    ) in text
    assert re.search(r'<button[^>]*type="submit"[^>]*>Dismiss</button>', html)
    assert 'name="confirmed" value="1"' in html
    assert f'href="{URL}"' in html  # Cancel


def test_dismiss_without_name_names_the_id(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, dismiss_url())
    assert f"Dismiss the proposed State {PENDING}?" in text_of(html)


def test_dismiss_unconfirmed_post_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    response = dismiss_post(operator_client, confirmed=False)
    assert response.status_code == 200
    assert engine.requests == []
    assert f'name="idempotency_key" value="{KEY}"' in response.content.decode()


def test_dismiss_confirmed_posts_once_and_flashes(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = dismiss_post(operator_client)
    assert response.status_code == 302
    assert response["Location"] == URL
    (call,) = dismissals(engine)
    assert call.url.path == f"{PROPOSALS_PATH}/{PENDING}/dismiss"
    assert call.headers["Idempotency-Key"] == KEY
    assert REVISION_HEADER not in call.headers
    assert call.content == b""
    assert [r.url.path for r in engine.requests] == [SHELL_PATHS[0], call.url.path]
    html = get(operator_client)
    assert messages_of(html) == [
        ("success", "Dismissed “lantern_oil” — the analysis won't propose it again")
    ]


@pytest.mark.parametrize(
    "config",
    [lite_m6(), structured_v3(states__level="off")],
    ids=["lite_m6", "states_off"],
)
def test_dismiss_gate_closed_zero_calls(
    operator_client: Client, engine: FakeEngine, config: dict[str, Any]
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=config)
    response = dismiss_post(operator_client)
    assert response.status_code == 200
    assert [r.url.path for r in engine.requests] == [SHELL_PATHS[0]]
    assert dismissals(engine) == []
    html = response.content.decode()
    assert "states.level" in html
    assert f'name="idempotency_key" value="{KEY}"' in html


def test_dismiss_unread_config_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 503, json=error_json("unavailable"))
    response = dismiss_post(operator_client)
    assert response.status_code == 200
    assert dismissals(engine) == []


def test_dismiss_no_pending_warning_flash(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", DISMISS, 404, json=error_json("not_found", reason="no_pending_proposal"))
    response = dismiss_post(operator_client)
    assert response.status_code == 302
    assert response["Location"] == URL
    assert messages_of(get(operator_client)) == [("warning", NOT_PENDING)]


def test_dismiss_other_404_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", DISMISS, 404, json=error_json("not_found"))
    response = dismiss_post(operator_client)
    assert response.status_code == 200
    assert NOT_FOUND in response.content.decode()


@pytest.mark.parametrize(
    ("status", "code", "details", "expected"),
    [
        (409, "story_archived", {}, "archived"),
        (409, "story_paused", {}, "paused"),
        (423, "story_busy", {"reason": "in_flight"}, "Story busy"),
        (409, "module_off", {"module": "states"}, "states.level"),
    ],
)
def test_dismiss_archived_flash(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
    expected: str,
) -> None:
    engine.respond("POST", DISMISS, status, json=error_json(code, **details))
    response = dismiss_post(operator_client)
    assert response.status_code == 302
    assert response["Location"] == URL
    ((tag, text),) = messages_of(get(operator_client))
    assert tag == "error"
    assert expected in text


def test_dismiss_unknown_outcome_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", DISMISS, httpx.ConnectError("down"))
    response = dismiss_post(operator_client)
    assert response.status_code == 200
    html = response.content.decode()
    assert '<div class="engine-error' in html
    assert f'name="idempotency_key" value="{KEY}"' in html


def test_dismiss_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", DISMISS, 503, json=error_json("erasure_pending"), headers={"Retry-After": "30"}
    )
    response = dismiss_post(operator_client)
    assert held_text("30") in response.content.decode()


def test_no_dismiss_path_sends_expected_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** no dismiss path sends ``X-Epistrel-Expected-Revision``."""
    dismiss_post(operator_client, expected_revision="12")
    engine.respond("POST", DISMISS, 404, json=error_json("not_found", reason="no_pending_proposal"))
    dismiss_post(operator_client)
    assert len(dismissals(engine)) == 2
    assert all(REVISION_HEADER not in call.headers for call in dismissals(engine))

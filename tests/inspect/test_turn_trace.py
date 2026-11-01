"""A structured turn's eval hooks and read set (#139): ``console/turn_trace.py``'s read and
lenient parsing, the ``inspect:turn-trace`` fragment in each state, and the lazy block on the
turn inspector (#48) and the #44 detail page.

Runs on the real urlconf against the schema-validating fake Engine. Fixtures are schema-valid
``OperationBody`` answers under ``tests/fixtures/inspect/``: ``operation_turn_structured.json``
(an omniscient structured turn of story ``STORY``, committed 2026-10-24 14:03 UTC),
``operation_turn_lite.json``, ``operation_not_turn.json`` and ``idempotency_conflict.json``.
Shapes the schema refuses (a non-object verdict) are tested by rendering the partial directly."""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, override_settings

from console.engine.client import EngineClient, EngineError
from console.engine.models import EvalHooks, OperationRecord, ReadSet, RepairRung, Verdict
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.turn_trace import (
    STAGES,
    TurnTrace,
    beat_of,
    read_turn_trace,
    rung_label,
    trace_view,
)
from console.views import inspect as inspect_views
from console.views import operations as operation_views
from console.views import play as play_views
from console.views import stories as story_views
from console.views.play import error_inspector, selected_inspector
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.fixtures import engine_fixture
from tests.inspect.helpers import FIXTURES, STORY, STORY_PATH
from tests.inspect.test_explain_turn import history_body, structured_turn
from tests.play.helpers import HTMX, error_json, respond_json, story_json

pytestmark = pytest.mark.django_db

OP = "01JB2Z8Y5Q3W6K9M1N4P7R0T7P"
OTHER_STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2W"
CH1 = "01JB2Z8Y5Q3W6K9M1N4P7R0CH1"
OPERATION = "/operations/{operation_id}"
OPERATION_PATH = f"/operations/{OP}"
SETTINGS = "/admin/settings"
HISTORY = "/stories/{story_id}/history"
VARIANTS = "/stories/{story_id}/variants"
STORY_SETTINGS = "/stories/{story_id}/settings"
URL = f"/stories/{STORY}/inspect/turns/{OP}/trace/"
STRUCTURED_CAPS = ["budgets", "structured", "models", "episodic"]
NOT_FOUND = "Not found (it may have been deleted or truncated)"
NO_HOOKS = "No eval hooks recorded (a Lite turn, or one committed before the Engine recorded them)"


def body(name: str, **over: Any) -> dict[str, Any]:
    found: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    found = copy.deepcopy(found)
    found.update(over)
    return found


def structured_body(**over: Any) -> dict[str, Any]:
    return body("operation_turn_structured", **over)


def record(**over: Any) -> OperationRecord:
    found = OperationRecord.from_json(structured_body(**over))
    assert found is not None
    return found


def trace_of(**over: Any) -> TurnTrace:
    found = record(**over)
    return TurnTrace(found, EvalHooks.parse(found.eval_hooks), ReadSet.parse(found.read_set))


def settings_body(**over: Any) -> dict[str, Any]:
    found: dict[str, Any] = engine_fixture("get_deployment_settings")
    found.update(over)
    return found


# --- the fake Engine ----------------------------------------------------------------------------


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """One fake behind the trace endpoint, the play page and the #44 detail page."""
    fake = FakeEngine()
    answer_relations(fake)
    answer_effective_config(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (play_views, story_views, inspect_views, operation_views):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    fake.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=8))
    fake.respond("GET", VARIANTS, 200, json=engine_fixture("list_variants"))
    fake.respond("GET", STORY_SETTINGS, 200, json=engine_fixture("get_story_settings"))
    fake.respond("GET", HISTORY, 200, json=history_body(structured_turn(7)))
    fake.respond("GET", OPERATION, 200, json=structured_body())
    fake.respond("GET", SETTINGS, 200, json=settings_body(idempotency_result_window=48))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == OPERATION_PATH]


def settings_reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == SETTINGS]


def fragment(client: Client, url: str = URL, **headers: str) -> str:
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    html = response.content.decode()
    assert "verified" not in html.lower()
    return html


def rows(html: str, table: str) -> list[list[str]]:
    """The cells' text of ``<table class="<table>">``'s rows (tags stripped)."""
    found = re.search(rf'<table class="{table}">(.*?)</table>', html, re.S)
    assert found is not None, table
    return [
        [
            re.sub(r"<[^>]+>", "", c).strip()
            for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)
        ]
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", found.group(1), re.S)
    ]


def section(html: str, heading: str) -> str:
    return html.split(f"<h4>{heading}</h4>")[1].split("<h4>")[0]


# --- the read -----------------------------------------------------------------------------------


def test_one_omniscient_read(engine: FakeEngine) -> None:
    client = EngineClient("http://engine.test", "key", transport=engine.transport)
    trace = read_turn_trace(client, OP)
    assert [r.url.params.get("viewpoint") for r in reads(engine)] == ["omniscient"]
    assert trace.eval_hooks is not None
    assert trace.read_set is not None
    assert trace.operation.id == OP


def test_a_turn_produced_for_a_character_shows_its_hooks(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Engine #534: the omniscient read of a ``character:<id>`` turn carries the hooks."""
    engine.respond(
        "GET", OPERATION, 200, json=structured_body(viewpoint=f"character:{CH1}", result=None)
    )
    html = fragment(operator_client)
    assert [dict(r.url.params) for r in reads(engine)] == [{"viewpoint": "omniscient"}]
    assert "trace-verdicts" in html
    assert "trace-latency" in html
    assert "Keys are shown to an omniscient read only" not in html
    assert settings_reads(engine) == []


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "idempotency_conflict"),
        (409, "revision_conflict"),
        (404, "not_found"),
        (503, "erasure_pending"),
    ],
)
def test_other_errors_raise(engine: FakeEngine, status: int, code: str) -> None:
    engine.respond("GET", OPERATION, status, json=error_json(code))
    client = EngineClient("http://engine.test", "key", transport=engine.transport)
    with pytest.raises(EngineError) as raised:
        read_turn_trace(client, OP)
    assert raised.value.code == code
    assert len(reads(engine)) == 1


# --- lenient parsing ----------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, [], "hooks", 3, True])
def test_a_non_object_parses_to_none(raw: object) -> None:
    assert EvalHooks.parse(raw) is None
    assert ReadSet.parse(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        {"verdicts": "x", "repair_rungs": {}, "latency_ms_by_stage": [], "raw_render_event_ids": 4},
        {"verdicts": [1, None, [], {"kind": 3, "escalated": "yes", "evidence_ids": "f"}]},
        {"repair_rungs": [{"beat": True, "rung": 1}, {"beat": 1}, {"beat": 1, "rung": 1.5}]},
        {"latency_ms_by_stage": {"plan": "fast", "render": None, "verify": True}},
    ],
)
def test_malformed_parts_never_raise(raw: dict[str, Any]) -> None:
    hooks = EvalHooks.parse(raw)
    assert hooks is not None
    trace_view(STORY, TurnTrace(record(), hooks, ReadSet.parse({"keys": [1, {}]})))


def test_a_malformed_verdict_field_reads_missing_and_goes_to_more() -> None:
    verdict = Verdict.parse({"kind": 3, "escalated": "yes", "claim_id": "d1.0"}, 1)
    assert verdict is not None
    assert (verdict.kind, verdict.escalated, verdict.claim_id) == (None, None, "d1.0")
    assert verdict.extras == {"kind": 3, "escalated": "yes"}
    assert Verdict.parse("d1.0", 1) is None


def test_a_rung_needs_integer_beat_and_rung() -> None:
    assert RepairRung.parse({"beat": 1, "rung": True}, 1) is None
    assert RepairRung.parse({"beat": "1", "rung": 2}, 1) is None
    rung = RepairRung.parse({"beat": 1, "rung": 2, "accepted": "yes"}, 1)
    assert rung is not None
    assert rung.accepted is None
    assert rung.extras == {"accepted": "yes"}


def test_beat_of_a_claim_id() -> None:
    assert beat_of("d3.1") == "beat 3"
    assert beat_of("d12.0") == "beat 12"
    assert beat_of("01JB2Z8Y5Q3W6K9M1N4P7R0CK1:4") is None
    assert beat_of("d3") is None
    assert beat_of(None) is None


def test_rung_labels() -> None:
    assert rung_label(1) == "rung 1 · narrowing"
    assert rung_label(2) == "rung 2 · substitution"
    assert rung_label(3) == "rung 3 · regeneration"
    assert rung_label(4) == "rung 4 · safe fallback (or dropped)"
    assert rung_label(7) == "rung 7"


def test_latency_in_the_engines_stage_order_then_unknown_stages() -> None:
    hooks = trace_view(STORY, trace_of()).hooks
    assert hooks is not None
    assert hooks.latency is not None
    assert [r.name for r in hooks.latency.rows] == [*STAGES, "warmup"]
    assert hooks.latency.total == 7 + 3100 + 12 + 0 + 25 + 40 + 0 + 2210 + 5 + 18


# --- the partial, rendered from a parsed trace --------------------------------------------------


def render_trace(trace: TurnTrace, state: str = "hooks") -> str:
    html = render_to_string(
        "console/_turn_trace.html",
        {"state": state, "story_id": STORY, "trace": trace_view(STORY, trace)},
    )
    assert "verified" not in html.lower()
    return html


def test_skipped_entries_appear_in_more_not_raised() -> None:
    raw = structured_body()["eval_hooks"]
    raw["verdicts"].insert(1, "not an object")
    raw["repair_rungs"].insert(0, {"beat": 9, "rung": True, "accepted": True})
    raw["mystery"] = {"x": 1}
    trace = trace_of(eval_hooks=raw)
    html = render_trace(trace)
    assert len(rows(html, "trace-verdicts")) == 1 + 4
    assert len(rows(html, "trace-repairs")) == 1 + 3
    more = html.split('<details class="more trace-more">')[1]
    labels = re.findall(r"<h5>([^<]+)</h5>", more)
    assert labels == [
        "Raw render event ids",
        "eval_hooks",
        "verdict 2",
        "verdict 4",
        "rung 1",
    ]
    assert "&quot;not an object&quot;" in more
    assert "&quot;rung&quot;: true" in more
    assert "&quot;memory_id&quot;" in more  # verdict 4's unnamed ``recall``


def test_a_non_object_latency_table_is_none_with_the_raw_value_in_more() -> None:
    raw = structured_body()["eval_hooks"]
    raw["latency_ms_by_stage"] = ["plan", 3]
    raw["model_calls_by_purpose"] = {"generation": 1, "repair": "two"}
    html = render_trace(trace_of(eval_hooks=raw))
    assert section(html, "Latency").strip() == "<p>None</p>"
    assert rows(html, "trace-calls") == [
        ["Purpose", "Calls"],
        ["Generation", "1"],
        ["Total", "1"],
    ]
    more = html.split("<h5>eval_hooks</h5>")[1]
    assert "&quot;latency_ms_by_stage&quot;" in more
    assert "&quot;two&quot;" in more


# --- the endpoint -------------------------------------------------------------------------------


def test_full_hooks_render(operator_client: Client, engine: FakeEngine) -> None:
    html = fragment(operator_client)
    assert len(reads(engine)) == 1
    assert settings_reads(engine) == []
    latency = rows(html, "trace-latency")
    assert latency[0] == ["Stage", "ms"]
    assert [r[0] for r in latency[1:-1]] == [
        "Plan",
        "Render",
        "Normalize",
        "Detection",
        "Verify",
        "Entailment",
        "Repair",
        "Composition",
        "Commit",
        "Warmup",
    ]
    assert latency[2] == ["Render", "3,100"]
    assert latency[3] == ["Normalize", "0"]
    assert latency[-1] == ["Total", "5,417"]
    assert '<tr class="total"><th>Total</th>' in html
    assert '<td title="extraction_backstop">Extraction backstop</td>' in html
    assert rows(html, "trace-calls")[1:] == [
        ["Generation", "1"],
        ["Extraction backstop", "1"],
        ["Total", "2"],
    ]
    assert '<dl class="verdict-legend">' in html


def test_verdict_counts_and_rows(operator_client: Client, engine: FakeEngine) -> None:
    html = fragment(operator_client)
    assert (
        '<p class="verdict-counts">4 claims · 2 decided without a model · 1 escalated</p>' in html
    )
    table = rows(html, "trace-verdicts")
    assert table[0] == [
        "Claim",
        "Beat",
        "Kind",
        "Escalated",
        "Reason",
        "Rule",
        "Evidence",
        "Disposition",
        "Permission",
        "Cause",
    ]
    supported, novel, uncertain, contradicted = table[1:]
    assert supported[:4] == ["d1.0", "beat 1", "supported", "—"]
    assert supported[5] == "canon_match"
    assert supported[6].startswith("2")
    assert supported[7:] == ["Canonized", "permitted · owner character", "—"]
    assert novel[:5] == ["d3.1", "beat 3", "novel", "escalated", "—"]
    assert novel[8:] == ["denied · player authority", "Resolution conflict"]
    assert uncertain[:5] == ["—", "—", "uncertain", "not recorded", "Partial coverage"]
    assert contradicted[:3] == ["…7R0CK1:4", "—", "contradicted"]
    assert "<strong>escalated</strong>" in html
    assert '<span class="muted">not recorded</span>' in html
    assert '<span title="permitted:owner_character">' in html
    assert '<span title="partial_coverage">Partial coverage</span>' in html


def test_repairs(operator_client: Client, engine: FakeEngine) -> None:
    assert rows(fragment(operator_client), "trace-repairs") == [
        ["Beat", "Rung", "Accepted"],
        ["beat 2", "rung 1 · narrowing", "yes"],
        ["beat 3", "rung 4 · safe fallback (or dropped)", "no"],
        ["beat 4", "rung 7", "—"],
    ]


def test_the_read_set_keys(operator_client: Client, engine: FakeEngine) -> None:
    html = fragment(operator_client)
    assert '<p class="read-set-counts">4 keys · 9 retrieved</p>' in html
    table = rows(html, "trace-keys")
    assert table[0] == ["Subject", "Predicate", "Object", "Holder", "Window", "Result hash"]
    star, pair, belief, literal = table[1:]
    assert star == ["any", "…4P7R0PR1", "any", "canon", "[…, …) min", "9f2c1d0e8b7a…"]
    assert pair[0] == "…4P7R0CH1, …4P7R0CH2"
    assert pair[2] == "{&quot;literal&quot;:&quot;blue&quot;,&quot;unit&quot;:null}"
    assert pair[3:5] == ["utterance · …4P7R0CH1", "[3, …) min"]
    assert belief[2:6] == ["…4P7R0EN1", "belief · …4P7R0CH2", "[0, 12) min", "abc"]
    assert literal[2] == "harbour"
    assert "<code>harbour</code>" in html
    digest = structured_body()["read_set"]["keys"][0]["result_hash"]
    assert f'<code title="{digest}">9f2c1d0e8b7a…</code>' in html


def test_a_predicate_links_its_row_on_the_predicates_tab(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = fragment(operator_client)
    predicate = structured_body()["read_set"]["keys"][0]["predicate"]
    link = f'<a href="/stories/{STORY}/inspect/predicates/#p-{predicate}">'
    assert f'{link}<code title="{predicate}">…4P7R0PR1</code></a>' in html


def test_the_raw_render_event_ids_are_in_more(operator_client: Client, engine: FakeEngine) -> None:
    more = fragment(operator_client).split('<details class="more trace-more">')[1]
    ids = structured_body()["eval_hooks"]["raw_render_event_ids"]
    assert re.findall(r"<li><code>([^<]+)</code></li>", more) == ids


def test_keys_empty_and_keys_null_and_read_set_null(
    operator_client: Client, engine: FakeEngine
) -> None:
    read_set = {"keys_count": 0, "retrieved_count": 0, "keys": []}
    engine.respond("GET", OPERATION, 200, json=structured_body(read_set=read_set))
    assert "<p>None</p>" in section(fragment(operator_client), "Read set")
    read_set = {"keys_count": 3, "retrieved_count": 1, "keys": None}
    engine.respond("GET", OPERATION, 200, json=structured_body(read_set=read_set))
    html = fragment(operator_client)
    assert '<p class="keys-withheld">Keys are shown to an omniscient read only</p>' in html
    engine.respond("GET", OPERATION, 200, json=structured_body(read_set=None))
    assert '<p class="no-read-set">No read set recorded</p>' in fragment(operator_client)


def test_a_409_is_an_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 409, json=body("idempotency_conflict"))
    html = fragment(operator_client)
    assert 'data-state="error"' in html
    assert "idempotency_conflict" in html
    assert "engine-error" in html
    # Complement: exactly one operation read and no settings read.
    assert len(reads(engine)) == 1
    assert settings_reads(engine) == []
    engine.respond("GET", OPERATION, 409, json=error_json("revision_conflict"))
    assert 'data-state="error"' in fragment(operator_client)


def test_a_lite_turn(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 200, json=body("operation_turn_lite"))
    html = fragment(operator_client)
    assert NO_HOOKS in html
    assert "trace-verdicts" not in html
    assert '<p class="read-set-counts">0 keys · 0 retrieved</p>' in html


def test_a_turn_not_committed(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 200, json=structured_body(status="in_flight", eval_hooks=None))
    html = fragment(operator_client)
    assert 'No eval hooks: the turn is <span title="in_flight">in flight</span>' in html
    assert "4 keys · 9 retrieved" in html
    engine.respond(
        "GET", OPERATION, 200, json=structured_body(status="failed", eval_hooks=None, read_set=None)
    )
    html = fragment(operator_client)
    assert "No read set recorded" not in html
    assert "Read set" not in html


def test_another_storys_operation_is_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 200, json=structured_body(story_id=OTHER_STORY))
    html = fragment(operator_client)
    assert NOT_FOUND in html
    assert "trace-latency" not in html


def test_not_a_turn(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 200, json=body("operation_not_turn"))
    html = fragment(operator_client)
    assert "Not a turn — no eval hooks or read set" in html
    assert "Read set" not in html


def test_a_404_is_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 404, json=error_json("not_found"))
    assert NOT_FOUND in fragment(operator_client)


def test_a_held_story_renders_the_held_partial(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 503, json=error_json("erasure_pending"))
    for headers in ({}, HTMX):
        html = fragment(operator_client, **headers)
        assert '<p class="warning held-message" role="status">' in html
        assert "<html" not in html


def test_another_error_renders_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 503, json=error_json("database_unavailable"))
    html = fragment(operator_client, **HTMX)
    assert "database_unavailable" in html
    assert "<html" not in html


def test_a_non_ulid_operation_is_a_404(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(f"/stories/{STORY}/inspect/turns/not-an-id/trace/")
    assert response.status_code == 404
    assert reads(engine) == []


# --- the lazy block -----------------------------------------------------------------------------

BLOCK = (
    f'<details class="turn-trace" hx-get="{URL}" hx-trigger="toggle once" '
    'hx-target="find .turn-trace-body"'
)


def log_row(**fields: Any) -> EngineOperation:
    values: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "story_id": STORY,
        "kind": "respond",
        "http_method": "POST",
        "path": f"/stories/{STORY}/respond",
        "request": {"body": {"say": "say", "do": "do"}, "headers": {}},
        "http_status": 200,
        "engine_operation_id": OP,
        "timeline_revision": 8,
        "config_revision": 0,
        "idempotency_key": "01JB2Z8Y5Q3W6K9M1N4P7R0KY7",
        "outcome": "ok",
        "response": respond_json(turn=7, variant=1, capabilities=STRUCTURED_CAPS),
    }
    values.update(fields)
    return EngineOperation.objects.create(**values)


def no_trace_reads(engine: FakeEngine) -> None:
    assert reads(engine) == []
    assert settings_reads(engine) == []


def get(client: Client, url: str, **headers: str) -> str:
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    return response.content.decode()


def test_the_inspector_offers_the_block_without_reading(
    operator_client: Client, engine: FakeEngine
) -> None:
    log_row()
    latest = get(operator_client, f"/stories/{STORY}/")
    assert BLOCK in latest
    selected = get(operator_client, f"/stories/{STORY}/inspector/?operation={OP}", **HTMX)
    assert BLOCK in selected
    assert "Loading eval hooks…" in selected
    assert "Couldn't load eval hooks — reload the page to retry." in selected
    assert 'hx-on:htmx:error="' in selected
    # #176: no answer's <title> (a Django 404/500 page's) replaces the host page's.
    assert 'hx-swap="innerHTML ignoreTitle:true"' in selected
    for status in ("4xx", "5xx"):
        assert f"""hx-status:{status}='swap:"none ignoreTitle:true"'""" in selected
    no_trace_reads(engine)


def test_a_turn_not_played_here_is_offered_too(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, f"/stories/{STORY}/inspector/?operation={OP}", **HTMX)
    assert "Report not available" in html
    assert BLOCK in html
    no_trace_reads(engine)


def test_an_ingested_turn_has_no_block(operator_client: Client, engine: FakeEngine) -> None:
    html = get(
        operator_client, f"/stories/{STORY}/inspector/?operation={OP}&origin=ingested", **HTMX
    )
    assert "ingested" in html
    assert 'class="turn-trace"' not in html
    assert not selected_inspector(STORY, OP, "ingested").trace_shown


def test_an_error_inspector_has_no_block(operator_client: Client, engine: FakeEngine) -> None:
    log_row(http_status=503, error_code="model_unavailable", outcome="error")
    html = get(operator_client, f"/stories/{STORY}/inspector/?operation={OP}", **HTMX)
    assert 'class="turn-trace"' not in html
    exc = EngineError("story_busy", "busy", {}, 423)
    assert not error_inspector(exc, "key").trace_shown


def test_the_detail_page_offers_the_block_without_reading(
    operator_client: Client, engine: FakeEngine
) -> None:
    row = log_row()
    html = get(operator_client, f"/operations/{row.pk}/")
    assert BLOCK in html
    assert html.index("<h2>Response</h2>") < html.index(BLOCK) < html.index("Engine's view")
    no_trace_reads(engine)


@pytest.mark.parametrize(
    "fields",
    [
        {"story_id": ""},
        {"engine_operation_id": ""},
        {"kind": "create_story", "response": {"id": STORY}},
    ],
)
def test_the_detail_page_has_no_block(
    operator_client: Client, engine: FakeEngine, fields: dict[str, Any]
) -> None:
    row = log_row(**fields)
    assert 'class="turn-trace"' not in get(operator_client, f"/operations/{row.pk}/")
    no_trace_reads(engine)


ROOT = Path(__file__).resolve().parents[2]
TRACE_SOURCES = (
    "console/turn_trace.py",
    "console/views/inspect.py",
    "console/history.py",
    "console/views/history.py",
    "templates/console/_turn_trace.html",
)


def test_no_withheld_path_remains() -> None:
    """#237: Engine #534 made the no-viewpoint fallback dead code; none of it is left."""
    found = [
        f"{path.relative_to(ROOT)}: {word}"
        for folder in ("console", "templates")
        for path in (ROOT / folder).rglob("*")
        if path.is_file()
        and path.name != "openapi.json"
        and path.suffix in (".py", ".html", ".json", ".txt", ".css")
        for word in ("withheld_for", "is_withheld", "hooks_unavailable_until", "Epistrel#534")
        if word in path.read_text()
    ]
    assert found == []
    for name in TRACE_SOURCES:
        assert "idempotency_result_window" not in (ROOT / name).read_text(), name
    partial = (ROOT / "templates/console/_turn_trace.html").read_text()
    assert 'state == "withheld"' not in partial
    assert "notice withheld" not in partial

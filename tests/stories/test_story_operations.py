"""#324's story Operations page (``stories:operations-list``,
``console/views/story_operations.py``), against the schema-validating fake Engine over #292's
``m7/story.json`` cast and ``full_v4()``: the reads and filters, the cursor and paging, the
rows and their links, rejected entries, the empty texts and the in-page errors. Nothing here
writes: every case's Engine log holds GETs only."""

import json
import typing
from collections.abc import Iterator
from html import unescape
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils.html import escape

from console.effects import STAGED_LABEL
from console.engine.models import OperationLabel
from console.engine.schema import RECORDED_PATH
from console.forms.story_operations import (
    INTER_TURN_TEXT,
    OPERATION_KINDS,
    OPERATION_LABELS,
    OPERATION_STATUSES,
    TIME_INVALID,
    WINDOW_INVERTED,
    kind_text,
)
from console.inspect import history_turn_url
from console.models import EngineOperation
from console.paging import CURSOR_DROPPED
from console.views import story_operations as views
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json
from tests.rewrites.helpers import (
    CONFIG_READ,
    STORY,
    STORY_READ,
    answer_raw,
    calls,
    engine,
    m7,
    queries,
)

pytestmark = pytest.mark.django_db
__all__ = ["engine"]  # the fixture, re-exported for pytest

URL = f"/stories/{STORY}/operations/"
LIST = f"/stories/{STORY}/operations"
LIST_ROUTE = "/stories/{story_id}/operations"
OP1 = "01JM7QP0000000000000000001"
OP2 = "01JM7QP0000000000000000002"
OP3 = "01JM7QP0000000000000000003"
OP4 = "01JM7QP0000000000000000004"
NEXT = OP4
WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
FILTER_DATA = {
    "label": "staged_turn",
    "kind": "turn",
    "status": "committed",
    "since": "2026-11-01T00:00",
    "until": "2026-11-02T12:30",
}


@pytest.fixture(autouse=True)
def no_writes(engine: FakeEngine) -> Iterator[None]:
    """**Complement** (``test_no_write_on_get``, held by every case): the fake's log holds no
    POST, PUT, PATCH or DELETE."""
    yield
    assert [r for r in engine.requests if r.method in WRITES] == []


def listed(engine: FakeEngine, body: Any | None = None) -> None:
    engine.respond("GET", LIST_ROUTE, 200, json=body if body is not None else m7("operations"))


def empty(next_after: str | None = None) -> dict[str, Any]:
    return {"story_id": STORY, "operations": [], "next_after": next_after}


def page(client: Client, url: str = URL, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status
    return response.content.decode()


def list_reads(engine: FakeEngine) -> list[list[tuple[str, str]]]:
    return queries(engine, "GET", LIST)


def row(html: str, operation_id: str) -> str:
    start = html.index(f'<tr id="op-{operation_id}">')
    return html[start : html.index("</tr>", start)]


def cell(html: str, operation_id: str, name: str) -> str:
    part = row(html, operation_id)
    start = part.index(f'<td class="{name}">') + len(f'<td class="{name}">')
    return part[start : part.index("</td>", start)]


def field(html: str, name: str) -> str:
    start = html.index(f'<span class="field field-{name}">')
    return html[start : html.index("</span>\n", start)]


def above_filters(html: str) -> str:
    start = html.index('<section class="story-operations">')
    return html[start : html.index('<form method="get"', start)]


def below_filters(html: str) -> str:
    return html[html.index("</form>") :]


# --- the choices -------------------------------------------------------------------------------


def _query_enum(name: str) -> list[str]:
    schema = json.loads(RECORDED_PATH.read_text())
    operation = schema["paths"]["/stories/{story_id}/operations"]["get"]
    (param,) = [p for p in operation["parameters"] if p["name"] == name]
    (ref,) = [option["$ref"] for option in param["schema"]["anyOf"] if "$ref" in option]
    enum: list[str] = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]["enum"]
    return enum


def test_the_choices_match_the_recorded_query_enums() -> None:
    assert list(OPERATION_KINDS) == _query_enum("kind")
    assert list(OPERATION_STATUSES) == _query_enum("status")
    assert list(OPERATION_LABELS) == _query_enum("label")
    assert typing.get_args(OperationLabel) == OPERATION_LABELS


def test_the_filter_options(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = page(operator_client)
    label = field(html, "label")
    assert '<option value="" selected>any</option>' in label
    assert f'<option value="staged_turn" title="staged_turn">{STAGED_LABEL}</option>' in label
    assert '<option value="rating_check" title="rating_check">Rating check</option>' in label
    kind = field(html, "kind")
    assert f'<option value="inter_turn" title="inter_turn">{INTER_TURN_TEXT}</option>' in kind
    assert '<option value="rewrite_session" title="rewrite_session">Rewrite session</option>' in (
        kind
    )
    status = field(html, "status")
    assert '<option value="in_flight" title="in_flight">In flight</option>' in status
    for name in ("since", "until"):
        assert 'type="datetime-local"' in field(html, name)
        assert 'step="1"' in field(html, name)
    assert "Since (UTC)" in html
    assert "Until (UTC)" in html
    assert '<button type="submit">Filter</button>' in html
    assert "Clear filters" not in html  # **Complement:** no filter set


# --- reads and filters -------------------------------------------------------------------------


def test_default_read(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = page(operator_client)
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), ("GET", LIST)]
    # Exactly limit=50. **Complement:** no label, kind, status, since, until or after_id.
    assert list_reads(engine) == [[("limit", "50")]]
    assert "<title>Operations · Harbour Ledger</title>" in html
    assert f'<a href="{URL}" aria-current="page">Operations</a>' in html


def test_filters_sent(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = operator_client.get(URL, FILTER_DATA).content.decode()
    assert list_reads(engine) == [
        [
            ("label", "staged_turn"),
            ("kind", "turn"),
            ("status", "committed"),
            ("since", "2026-11-01T00:00:00Z"),
            ("until", "2026-11-02T12:30:00Z"),
            ("limit", "50"),
        ]
    ]
    # The inputs echo the typed bounds; Clear filters goes to the bare page.
    assert 'value="2026-11-01T00:00"' in field(html, "since")
    assert f'<a href="{URL}">Clear filters</a>' in html
    assert "4 operations on this page, matching filters" in html


def test_an_aware_bound_is_converted_to_utc(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    operator_client.get(URL, {"since": "2026-11-01T02:00:00+02:00"})
    assert list_reads(engine) == [[("since", "2026-11-01T00:00:00Z"), ("limit", "50")]]


@pytest.mark.parametrize("name", ["label", "kind", "status"])
def test_bad_choice_zero_calls(operator_client: Client, engine: FakeEngine, name: str) -> None:
    listed(engine)
    html = operator_client.get(URL, {name: "made_up"}).content.decode()
    assert list_reads(engine) == []
    assert "Select a valid choice. made_up is not one of the available choices." in unescape(
        field(html, name)
    )
    assert f'<a href="{URL}">Clear filters</a>' in html
    assert "<table" not in html
    assert views.SCOPE_TEXT in html  # the scope line and cross-link always show
    assert "on this page" not in html  # no call, no count


def test_bad_time_and_inverted_window_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    listed(engine)
    html = operator_client.get(URL, {"since": "yesterday"}).content.decode()
    assert escape(TIME_INVALID) in field(html, "since")
    html = operator_client.get(
        URL, {"since": "2026-11-02T00:00", "until": "2026-11-01T00:00"}
    ).content.decode()
    assert WINDOW_INVERTED in field(html, "until")
    assert WINDOW_INVERTED not in field(html, "since")
    assert list_reads(engine) == []
    # **Complement:** Until equal to Since is allowed.
    operator_client.get(URL, {"since": "2026-11-01T00:00", "until": "2026-11-01T00:00"})
    assert len(list_reads(engine)) == 1


def test_bad_cursor_dropped_not_sent(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = operator_client.get(URL, {"after_id": "nope", "kind": "turn"}).content.decode()
    assert list_reads(engine) == [[("kind", "turn"), ("limit", "50")]]
    assert escape(CURSOR_DROPPED) in above_filters(html)
    # It counts as a first page: no "First page" link.
    assert "First page" not in html


def test_paging_keeps_filters(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = operator_client.get(URL, {"kind": "turn", "label": "", "junk": "x"}).content.decode()
    assert f'<a href="{URL}?kind=turn&amp;after_id={NEXT}">Next page</a>' in html
    assert "First page" not in html
    html = operator_client.get(URL, {"kind": "turn", "after_id": OP2}).content.decode()
    assert list_reads(engine)[-1] == [("kind", "turn"), ("after_id", OP2), ("limit", "50")]
    assert f'<a href="{URL}?kind=turn">First page</a>' in html
    assert f'<a href="{URL}?kind=turn&amp;after_id={NEXT}">Next page</a>' in html
    # The filter form carries no cursor.
    assert 'name="after_id"' not in html


# --- rows --------------------------------------------------------------------------------------


def test_rows_render_each_column(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = page(operator_client)
    assert [h for h in ("op-" + op for op in (OP1, OP2, OP3, OP4)) if h in html] == [
        "op-" + op for op in (OP1, OP2, OP3, OP4)
    ]
    assert html.index(f"op-{OP1}") < html.index(f"op-{OP2}") < html.index(f"op-{OP4}")
    assert cell(html, OP1, "kind") == '<span title="turn">Turn</span>'
    assert cell(html, OP3, "kind") == '<span title="rewrite_session">Rewrite session</span>'
    assert cell(html, OP4, "kind") == '<span title="import">Import</span>'
    assert cell(html, OP1, "label") == "—"
    assert cell(html, OP2, "label") == f'<span title="staged_turn">{STAGED_LABEL}</span>'
    assert cell(html, OP1, "status") == (
        '<span class="badge badge-committed" title="committed">Committed</span>'
    )
    turn_url = escape(history_turn_url(STORY, 3, 1))
    assert cell(html, OP1, "turn") == f'<a href="{turn_url}">turn 3 · v1</a>'
    assert cell(html, OP3, "turn") == "—"
    assert "console:nate" in cell(html, OP1, "principal")
    assert cell(html, OP1, "started") == (
        '<time datetime="2026-11-04T10:00:00Z" title="2026-11-04T10:00:00Z">'
        "2026-11-04 10:00:00 UTC</time>"
    )
    assert "2026-11-04 10:00:09 UTC" in cell(html, OP1, "committed")
    assert views.SCOPE_TEXT + ' · <span class="count">4 operations on this page</span>' in html
    log_link = f'<a class="console-log-link" href="/operations/?story={STORY}">'
    assert log_link + escape(views.LOG_LINK_TEXT) + "</a>" in html


def test_row_texts_for_other_values(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("operations")
    first = body["operations"][0]
    first.update(kind="inter_turn", status="in_flight", turn_variant=0, committed_at=None)
    body["operations"][1].update(label=None, staged=True)  # staged with no label
    body["operations"][2].update(label="rating_check", status="paused_for_x", kind="new_kind")
    body["operations"][3].update(narrative_turn=1200, turn_variant=None)
    answer_raw(engine, "GET", LIST_ROUTE, body)
    html = page(operator_client)
    assert cell(html, OP1, "kind") == f'<span title="inter_turn">{INTER_TURN_TEXT}</span>'
    assert cell(html, OP1, "status") == (
        '<span class="badge badge-in_flight" title="in_flight">In flight</span>'
    )
    assert ">turn 3</a>" in cell(html, OP1, "turn")  # variant 0: no suffix
    assert cell(html, OP1, "committed") == "—"
    assert cell(html, OP2, "label") == STAGED_LABEL
    assert cell(html, OP3, "label") == '<span title="rating_check">Rating check</span>'
    assert cell(html, OP3, "kind") == '<span title="new_kind">New kind</span>'
    assert 'title="paused_for_x">Paused for x</span>' in cell(html, OP3, "status")
    turn_url = escape(history_turn_url(STORY, 1200))
    assert cell(html, OP4, "turn") == f'<a href="{turn_url}">turn 1,200</a>'
    assert kind_text("inter_turn") == INTER_TURN_TEXT


def test_operation_links_engine_page_when_registered(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed(engine)
    html = page(operator_client)
    assert cell(html, OP1, "operation") == (
        f'<a href="/operations/engine/{OP1}/"><code title="{OP1}">…00000001</code></a>'
    )
    # **Complement:** D19's unregistered case is plain <code>.
    monkeypatch.setattr("console.effects.engine_operation_url", lambda _id: None)
    html = page(operator_client)
    assert cell(html, OP1, "operation") == f'<code title="{OP1}">…00000001</code>'


def _log_row(engine_operation_id: str, story_id: str = STORY) -> EngineOperation:
    return EngineOperation.objects.create(
        operator_username="nate",
        principal="console:nate",
        kind="respond",
        http_method="POST",
        path=f"/stories/{story_id}/respond",
        story_id=story_id,
        outcome="ok",
        http_status=200,
        request={"body": {}, "headers": {}},
        engine_operation_id=engine_operation_id,
    )


def test_console_log_link_newest_row(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    older = _log_row(OP1)
    newer = _log_row(OP1)
    _log_row(OP2, story_id="01JM7ST0000000000000000009")  # another story's row: not linked
    assert newer.pk > older.pk
    with CaptureQueriesContext(connection) as captured:
        html = page(operator_client)
    assert cell(html, OP1, "console-log") == f'<a href="/operations/{newer.pk}/">#{newer.pk}</a>'
    for other in (OP2, OP3, OP4):
        assert cell(html, other, "console-log") == "—"
    # **Complement:** one local query for it, and no GET /operations/{id}.
    log_queries = [q for q in captured.captured_queries if "console_engineoperation" in q["sql"]]
    assert len(log_queries) == 1
    assert not [path for _, path in calls(engine) if path.startswith("/operations/")]


def test_no_local_query_without_rows(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine, empty())
    with CaptureQueriesContext(connection) as captured:
        page(operator_client)
    assert not [q for q in captured.captured_queries if "console_engineoperation" in q["sql"]]


def test_unparsed_notice(operator_client: Client, engine: FakeEngine) -> None:
    answer_raw(engine, "GET", LIST_ROUTE, m7("operations_bad_item"))
    html = page(operator_client)
    assert f'<p class="notice rejected-notice">{escape(views.UNPARSED_TEXT)}</p>' in html
    assert html.index(escape(views.UNPARSED_TEXT)) < html.index('<table class="operations">')
    assert f"op-{OP1}" in html
    assert f"op-{OP3}" in html
    rejected = html[html.index('<tr class="rejected">') :]
    assert html.index(f"op-{OP3}") < html.index('<tr class="rejected">')
    assert '<td colspan="9">' + escape(views.OPERATION_REJECTED_TEXT) in rejected
    assert "01JM7QP0000000000000000009" in rejected
    assert "2 operations on this page" in html
    # Every entry rejected: only the notice and the raw entries, no table and no empty text.
    body = m7("operations_bad_item")
    body["operations"] = [body["operations"][1]]
    answer_raw(engine, "GET", LIST_ROUTE, body)
    html = page(operator_client)
    assert escape(views.UNPARSED_TEXT) in html
    assert '<details class="rejected-entry">' in html
    assert "<table" not in html
    for text in (views.NO_OPERATIONS, views.NO_OPERATIONS_MATCH, views.NO_MORE_OPERATIONS):
        assert text not in html
    assert "on this page" not in html


def test_empty_texts(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine, empty())
    html = page(operator_client)
    assert f'<p class="empty">{views.NO_OPERATIONS}</p>' in html
    assert "on this page" not in html
    html = operator_client.get(URL, {"since": "2026-11-01T00:00"}).content.decode()
    assert f'<p class="empty">{views.NO_OPERATIONS_MATCH}</p>' in html
    html = operator_client.get(URL, {"kind": "turn", "status": "failed"}).content.decode()
    assert f'<p class="empty">{views.NO_OPERATIONS_MATCH}</p>' in html
    html = operator_client.get(URL, {"kind": "turn", "after_id": OP2}).content.decode()
    assert f'<p class="empty">{views.NO_MORE_OPERATIONS}</p>' in html
    assert f'<a href="{URL}?kind=turn">First page</a>' in html
    assert views.NO_OPERATIONS_MATCH not in html


# --- errors ------------------------------------------------------------------------------------


def _validation(field_path: str, message: str) -> dict[str, Any]:
    return error_json(
        "validation_error",
        message,
        field=field_path,
        errors=[{"field": field_path, "message": message, "type": "value_error"}],
    )


def test_validation_error_on_since_is_beside_since(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST_ROUTE, 422, json=_validation("query.since", "Since is odd"))
    html = operator_client.get(URL, {"since": "2026-11-01T00:00"}).content.decode()
    assert "Since is odd" in field(html, "since")
    assert "Since is odd" not in above_filters(html)
    assert "<table" not in html


def test_validation_error_loc_list_is_placed(operator_client: Client, engine: FakeEngine) -> None:
    body = error_json(
        "validation_error",
        "bad",
        errors=[{"loc": ["query", "kind"], "msg": "Kind is odd"}, {"loc": ["query", "x"]}],
    )
    engine.respond("GET", LIST_ROUTE, 422, json=body)
    html = operator_client.get(URL, {"kind": "turn"}).content.decode()
    assert "Kind is odd" in field(html, "kind")
    assert 'class="errorlist operations-refusal" role="alert">bad' in above_filters(html)


def test_validation_error_on_after_id_offers_first_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST_ROUTE, 422, json=_validation("query.after_id", "Cursor is odd"))
    html = operator_client.get(URL, {"kind": "turn", "after_id": OP2}).content.decode()
    above = above_filters(html)
    assert "Cursor is odd" in above
    assert f'<a href="{URL}?kind=turn">First page</a>' in above


def test_other_422_is_above_the_filters(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST_ROUTE, 422, json=_validation("query.limit", "Limit is odd"))
    html = page(operator_client)
    assert "Limit is odd" in above_filters(html)
    assert "First page" not in html


def test_404_is_the_frames_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST_ROUTE, 404, json=error_json("not_found", "no story"))
    html = page(operator_client, status=404)
    assert "Not found (it may have been deleted or truncated)" in html
    assert '<form method="get"' not in html


def test_story_importing_is_the_importing_page(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        LIST_ROUTE,
        409,
        json=error_json("story_importing", job_id="01JM7JB0000000000000000007"),
    )
    html = page(operator_client, status=409)
    assert "This story is being imported" in html


def test_erasure_pending_is_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", LIST_ROUTE, 503, json=error_json("erasure_pending"), headers={"Retry-After": "30"}
    )
    html = page(operator_client, status=503)
    assert "Held until the erasure ledger is re-applied" in html


def test_a_500_is_43s_panel_below_the_filters(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond_undeclared("GET", LIST_ROUTE, 500, json=error_json("internal", "boom"))
    html = operator_client.get(URL, {"kind": "turn"}).content.decode()
    assert html.index('<form method="get"') < html.index('class="engine-error')
    assert '<option value="turn" selected title="turn">Turn</option>' in field(html, "kind")
    assert "<table" not in html
    assert "on this page" not in html
    assert escape(views.LOG_LINK_TEXT) in html


def test_no_write_on_get(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    page(operator_client)
    operator_client.get(URL, FILTER_DATA)
    assert {r.method for r in engine.requests} == {"GET"}
    assert EngineOperation.objects.count() == 0


def test_the_path_sits_beside_the_poll_path() -> None:
    """E11: ``operations/`` never shadows ``operations/<str:operation_id>/poll/``."""
    from django.urls import resolve

    assert resolve(URL).view_name == "stories:operations-list"
    assert resolve(f"{URL}{OP1}/poll/").view_name == "stories:poll"

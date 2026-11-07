"""#302's Amendments page (``stories:amendments``) and an amendment's page
(``stories:amendment``), ``console/views/amendments.py``, against the schema-validating fake
Engine over #292's ``m7/story.json`` cast and ``full_v4()``: the reads, the rows, the status
filter, paging, rejected entries, the empty texts, the parts and edges, and the in-page
errors. Nothing here writes: every case's Engine log holds GETs only."""

import json
import logging
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

import httpx
import pytest
from django.test import Client, override_settings
from django.utils.html import escape

from console.amendments import (
    AMENDMENT_REJECTED_TEXT,
    DEPENDENTS_TRUNCATED,
    LATER_PART_TEXT,
    NO_ACTIVE,
    NO_AMENDMENTS,
    NO_DEPENDENTS,
    NO_MORE_AMENDMENTS,
    NO_PARTS,
    NO_RELIES,
    NO_UNDONE,
    UNPARSED_TEXT,
)
from console.engine.models import AmendmentDetail
from console.forms.amendments import STATUS_INVALID
from console.paging import CURSOR_DROPPED
from console.rewrites import OMNISCIENT_LINE, OMNISCIENT_REQUIRED_TEXT
from tests.fakes.engine import FakeEngine
from tests.fixtures import ENGINE
from tests.play.helpers import error_json
from tests.rewrites.helpers import (
    CONFIG_READ,
    M7_PAGES,
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

S = f"/stories/{STORY}/"
URL = f"{S}amendments/"
A1 = "01JM7AM0000000000000000001"
A2 = "01JM7AM0000000000000000002"
A4 = "01JM7AM0000000000000000004"
DETAIL_URL = f"{URL}{A1}/"
LIST = f"/stories/{STORY}/amendments"
GET_ONE = f"{LIST}/{A1}"
LIST_ROUTE = "/stories/{story_id}/amendments"
ONE_ROUTE = "/stories/{story_id}/amendments/{amendment_id}"
PREDICATES = f"/stories/{STORY}/predicates"
PREDICATES_ROUTE = "/stories/{story_id}/predicates"
HOLDS = "01JB2Z8Y5Q3W6K9M1N4P7R0PR2"
NEXT = "01JM7AM0000000000000000007"
WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@pytest.fixture(autouse=True)
def no_writes(engine: FakeEngine) -> Iterator[None]:
    """**Complement** (``test_no_write_on_any_get``'s rule, held by every case): the fake's log
    holds no POST, PUT, PATCH or DELETE."""
    yield
    assert [r for r in engine.requests if r.method in WRITES] == []


def listed(engine: FakeEngine, body: Any | None = None) -> None:
    engine.respond("GET", LIST_ROUTE, 200, json=body if body is not None else m7("amendments"))


def detail(engine: FakeEngine, body: Any | None = None) -> None:
    engine.respond("GET", ONE_ROUTE, 200, json=body if body is not None else m7("amendment"))


def registry(engine: FakeEngine) -> None:
    body = json.loads((ENGINE.parent / "inspect" / "predicates_list.json").read_text())
    engine.respond("GET", PREDICATES_ROUTE, 200, json=body)


def page(client: Client, url: str = URL, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status
    return str(response.content.decode())


def squeezed(html: str) -> str:
    return re.sub(r">\s*\n\s*<", "><", html)


def text_of(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def row(html: str, anchor: str) -> str:
    found = re.search(rf'<tr id="{anchor}">(.*?)</tr>', squeezed(html), re.S)
    assert found, f"no row {anchor}"
    return found.group(1)


def list_reads(engine: FakeEngine) -> int:
    return calls(engine).count(("GET", LIST))


def empty_body(next_cursor: str | None = None) -> dict[str, Any]:
    return {"items": [], "next_cursor": next_cursor}


def above_filter(html: str) -> str:
    return html.split('<form method="get"')[0]


# --- the list: reads ------------------------------------------------------------------------------


def test_list_reads_omniscient_with_limit_50(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    page(operator_client)
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), ("GET", LIST)]
    # **Complement:** no status and no cursor are sent with the filter on All.
    assert queries(engine, "GET", LIST) == [[("viewpoint", "omniscient"), ("limit", "50")]]


def test_the_list_title_and_current_link(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = page(operator_client)
    assert "<title>Amendments · Harbour Ledger</title>" in html
    assert f'<a href="{URL}" aria-current="page">Amendments</a>' in html


def test_rows_render_each_column(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    with override_settings(ROOT_URLCONF=M7_PAGES):
        html = page(operator_client)
    first, undo, reverted = (row(html, f"amendment-{a}") for a in (A1, A4, A2))
    assert re.findall(r'<tr id="amendment-(\w+)">', html) == [A1, A4, A2]  # the Engine's order
    assert 'title="2026-11-04T12:00:00Z"' in first  # When, through engine_time
    assert '<span title="rewrite">Rewrite</span>' in first
    assert 'class="badge status-active" title="active">Active</span>' in first
    assert "<code>console:nate</code>" in first
    assert '<td class="parts">2</td>' in first
    assert '<td class="relations">—</td>' in first
    session = "01JM7RW0000000000000000008"
    assert f'<a href="{S}rewrites/{session}/"><code title="{session}">…00000008</code></a>' in first
    assert f'<a href="{DETAIL_URL}"><code title="{A1}">…00000001</code></a>' in first
    assert '<span title="undo">Undo</span>' in undo
    assert f'Undoes <a href="{URL}{A2}/"><code title="{A2}">' in undo
    assert 'class="badge status-reverted" title="reverted">Undone</span>' in reverted
    assert f'Undone by <a href="{URL}{A4}/"><code title="{A4}">' in reverted
    assert "3 amendments on this page" in html


def test_kind_texts_and_an_unknown_kind() -> None:
    from console.amendments import kind_cell, status_cell

    assert kind_cell("redo").text == "Redo"
    assert kind_cell("directed_resolution").text == "Directed regenerate"
    assert kind_cell("merge_like").text == "Merge like"
    assert kind_cell("merge_like").raw == "merge_like"
    assert status_cell("pending_x").text == "Pending x"


def test_relations_plain_while_unregistered(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D19: ``stories:rewrite`` is registered (#297), so the unregistered case is stood in for
    by ``rewrite_session_url`` and ``amendment_url`` answering ``None``."""
    import console.amendments as amendments

    monkeypatch.setattr(amendments, "rewrite_session_url", lambda *a: None)
    monkeypatch.setattr(amendments, "amendment_url", lambda *a: None)
    monkeypatch.setattr(amendments, "undo_url", lambda *a: None)  # #303's actions, likewise
    monkeypatch.setattr(amendments, "redo_url", lambda *a: None)
    listed(engine)
    html = page(operator_client)
    first, undo = row(html, f"amendment-{A1}"), row(html, f"amendment-{A4}")
    session = "01JM7RW0000000000000000008"
    assert f'<td class="session"><code title="{session}">…00000008</code></td>' in first
    assert f'<td class="amendment"><code title="{A1}">' in first
    assert f'Undoes <code title="{A2}">' in undo
    assert "<a " not in first.split('<td class="relations">')[1]
    assert "<a " not in undo.split('<td class="relations">')[1]


def test_a_null_session_reads_a_dash(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("amendments")
    body["items"][0]["session_id"] = None
    listed(engine, body)
    assert '<td class="session">—</td>' in row(page(operator_client), f"amendment-{A1}")


def test_status_filter_sends_reverted(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine, {"items": [m7("amendments")["items"][2]], "next_cursor": None})
    html = page(operator_client, f"{URL}?status=reverted")
    assert queries(engine, "GET", LIST) == [
        [("viewpoint", "omniscient"), ("status", "reverted"), ("limit", "50")]
    ]
    assert "1 amendment on this page, matching filters" in html
    assert '<option value="reverted" selected>Undone</option>' in html


def test_status_active_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    page(operator_client, f"{URL}?status=active")
    assert ("status", "active") in queries(engine, "GET", LIST)[0]


def test_bad_status_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"{URL}?status=gone")
    assert STATUS_INVALID in html
    assert list_reads(engine) == 0
    assert "on this page" not in html  # no count line without a read


def test_bad_status_and_bad_cursor_show_both_cursor_first(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, f"{URL}?status=gone&cursor=nope")
    assert html.index(CURSOR_DROPPED) < html.index(STATUS_INVALID)
    assert list_reads(engine) == 0


def test_bad_cursor_dropped_not_sent(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = page(operator_client, f"{URL}?cursor=nope")
    assert CURSOR_DROPPED in above_filter(html.split("<h2>Amendments</h2>")[1])
    # **Complement:** "nope" appears in no Engine request.
    assert all("nope" not in str(r.url) for r in engine.requests)
    assert queries(engine, "GET", LIST) == [[("viewpoint", "omniscient"), ("limit", "50")]]
    assert "First page" not in html  # it counts as a first page


def test_paging_links_keep_status(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine, dict(m7("amendments"), next_cursor=NEXT))
    html = page(operator_client, f"{URL}?status=active&cursor={A2}&stray=1")
    assert ("cursor", A2) in queries(engine, "GET", LIST)[0]
    assert f'<a href="{URL}?status=active&amp;cursor={NEXT}">Next page</a>' in html
    assert f'<a href="{URL}?status=active">First page</a>' in html
    filter_form = re.search(r'<form method="get".*?</form>', html, re.S)
    assert filter_form is not None
    assert "cursor" not in filter_form.group(0)  # the filter carries none


def test_no_paging_links_on_a_single_first_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    listed(engine)
    html = page(operator_client)
    assert "Next page" not in html
    assert "First page" not in html


def test_unparsed_notice_keeps_good_rows(operator_client: Client, engine: FakeEngine) -> None:
    answer_raw(engine, "GET", LIST_ROUTE, m7("amendments_bad_item"))
    html = squeezed(page(operator_client))
    table = html.split('<table class="amendments">')
    assert UNPARSED_TEXT in unescape(table[0])
    assert re.findall(r'<tr id="amendment-(\w+)">', html) == [A1, A4]
    rejected = re.findall(r'<tr class="rejected">(.*?)</tr>', html, re.S)
    assert len(rejected) == 1
    assert escape(AMENDMENT_REJECTED_TEXT) in rejected[0]
    assert '<td colspan="8">' in rejected[0]
    assert "01JM7AM0000000000000000009" in rejected[0]  # the raw entry under More
    assert html.index(f'id="amendment-{A4}"') < html.index('class="rejected"')
    assert "2 amendments on this page" in html  # parsed rows only


def test_every_entry_rejected_shows_only_the_notice(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_raw(engine, "GET", LIST_ROUTE, {"items": [{"id": "x"}], "next_cursor": None})
    html = unescape(page(operator_client))
    assert UNPARSED_TEXT in html
    assert '<details class="rejected-entry">' in html
    for text in (NO_AMENDMENTS, NO_ACTIVE, NO_UNDONE, NO_MORE_AMENDMENTS, "on this page"):
        assert text not in html


def test_clear_filters_link_only_while_filtered(
    operator_client: Client, engine: FakeEngine
) -> None:
    listed(engine)
    html = page(operator_client, f"{URL}?status=active")
    assert f'<a href="{URL}">Clear filters</a>' in html
    # **Complement:** absent with Status All.
    assert "Clear filters" not in page(operator_client)
    assert "Clear filters" not in page(operator_client, f"{URL}?status=")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", NO_AMENDMENTS),
        ("?status=active", NO_ACTIVE),
        ("?status=reverted", NO_UNDONE),
        (f"?status=reverted&cursor={A2}", NO_MORE_AMENDMENTS),
    ],
)
def test_empty_texts(
    operator_client: Client, engine: FakeEngine, query: str, expected: str
) -> None:
    listed(engine, empty_body())
    html = page(operator_client, f"{URL}{query}")
    assert f'<p class="empty">{expected}</p>' in html
    assert "<table" not in html
    assert "on this page" not in html
    if "cursor" in query:
        assert f'<a href="{URL}?status=reverted">First page</a>' in html


def test_no_viewpoint_picker(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = page(operator_client, f"{URL}?viewpoint=01JM7CH0000000000000000002")
    assert 'name="viewpoint"' not in html
    assert html.count(OMNISCIENT_LINE) == 1
    assert queries(engine, "GET", LIST)[0][0] == ("viewpoint", "omniscient")


def test_the_actions_column_is_303s(operator_client: Client, engine: FakeEngine) -> None:
    """#302 left ``row_actions`` unset; #303 fills it (its own tests cover the links)."""
    listed(engine)
    html = page(operator_client)
    assert "<th>Actions</th>" in html
    assert html.count('<td class="actions">') == 3


# --- the list: errors -----------------------------------------------------------------------------


def test_list_omniscient_required_is_e2s_text(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    engine.respond("GET", LIST_ROUTE, 403, json=error_json("omniscient_required"))
    with caplog.at_level(logging.WARNING, logger="console.engine_errors"):
        html = page(operator_client)
    assert OMNISCIENT_REQUIRED_TEXT in above_filter(html.split("<h2>Amendments</h2>")[1])
    logged = [r for r in caplog.records if "omniscient_required" in r.getMessage()]
    assert len(logged) == 1
    assert 'class="story-links"' in html  # the frame still renders


def test_list_cursor_validation_error_has_first_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = error_json(
        "validation_error",
        "cursor is not an amendment of this story",
        errors=[{"loc": ["query", "cursor"], "msg": "bad", "type": "value_error"}],
    )
    engine.respond("GET", LIST_ROUTE, 422, json=body)
    html = page(operator_client, f"{URL}?status=active&cursor={A2}")
    above = above_filter(html.split("<h2>Amendments</h2>")[1])
    assert "cursor is not an amendment of this story" in above
    assert f'<a href="{URL}?status=active">First page</a>' in above


def test_list_other_422_is_the_message_without_first_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST_ROUTE, 422, json=error_json("viewpoint_unresolved", "no view"))
    html = page(operator_client)
    assert OMNISCIENT_REQUIRED_TEXT in html
    assert "First page" not in html


def test_list_failure_is_43s_panel_below_the_filter(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST_ROUTE, 503, json=error_json("database_unavailable"))
    html = page(operator_client)
    assert html.index('<form method="get"') < html.index('class="engine-error')
    assert "<table" not in html


def test_list_story_importing_is_the_importing_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        LIST_ROUTE,
        409,
        json=error_json("story_importing", job_id="01JM7JB0000000000000000007"),
    )
    html = page(operator_client, status=409)
    assert "This story is being imported" in html


def test_list_erasure_pending_is_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", LIST_ROUTE, 503, json=error_json("erasure_pending"), headers={"Retry-After": "30"}
    )
    html = page(operator_client, status=503)
    assert "Held until the erasure ledger is re-applied" in html


# --- the detail -----------------------------------------------------------------------------------


def test_the_detail_reads_and_title(operator_client: Client, engine: FakeEngine) -> None:
    detail(engine)
    registry(engine)
    html = page(operator_client, DETAIL_URL)
    assert calls(engine) == [
        ("GET", STORY_READ),
        ("GET", CONFIG_READ),
        ("GET", GET_ONE),
        ("GET", PREDICATES),
    ]
    assert queries(engine, "GET", GET_ONE) == [[("viewpoint", "omniscient")]]
    assert "<title>Amendment …00000001 · Harbour Ledger</title>" in html
    assert f'<a href="{URL}" aria-current="page">Amendments</a>' in html


def test_the_summary(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("amendment")
    body.update(status="reverted", reverted_by=A4)
    detail(engine, body)
    registry(engine)
    html = squeezed(page(operator_client, DETAIL_URL))
    summary = html.split('<dl class="amendment-summary">')[1].split("</dl>")[0]
    assert '<dd class="kind"><span title="rewrite">Rewrite</span></dd>' in summary
    assert 'title="reverted">Undone</span>' in summary
    assert "<code>console:nate</code>" in summary
    assert '<dd class="parts">2</dd>' in summary
    assert f'Undone by <a href="{URL}{A4}/">' in summary
    assert f'<a href="{S}rewrites/01JM7RW0000000000000000008/">' in summary
    after = html.split("</dl>")[1]
    assert after.index(OMNISCIENT_LINE) < after.index(f'<a href="{URL}">Back to Amendments</a>')


def test_parts_render_in_ordinal_order(operator_client: Client, engine: FakeEngine) -> None:
    detail(engine)
    registry(engine)
    html = page(operator_client, DETAIL_URL)
    assert re.findall(r'<tr id="(part-[^"]+)">', html) == ["part-1", "part-2"]
    proposition, record = row(html, "part-1"), row(html, "part-2")
    assert text_of(proposition).startswith("1 Proposition Mara · holds Active")
    assert f'<span title="{HOLDS}">holds</span>' in proposition
    assert "[540, …) min" in proposition
    fact = "01JM7FA0000000000000000001"
    lineage = f"{S}inspect/canon/{fact}/lineage/"
    assert re.search(
        rf'<a href="{re.escape(lineage)}\?subject=01JM7CH0000000000000000001'
        rf'&amp;predicate={HOLDS}">'
        rf'<code title="{fact}">',
        proposition,
    )
    # The Engine's record parts always carry an action and a strategy.
    assert "Transcript messages …00000002 · Replace · Overlay" in text_of(record)
    assert '<td class="story-time">—</td>' in record
    assert '<td class="wrote">—</td>' in record
    assert "Bob copied the ledger." in record  # patch_after, under More


def test_a_parts_order_is_the_engines(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("amendment")
    body["parts"].reverse()
    detail(engine, body)
    registry(engine)
    html = page(operator_client, DETAIL_URL)
    assert re.findall(r'<tr id="(part-[^"]+)">', html) == ["part-2", "part-1"]


def test_registry_read_only_with_a_proposition_part(
    operator_client: Client, engine: FakeEngine
) -> None:
    detail(engine)
    registry(engine)
    page(operator_client, DETAIL_URL)
    assert calls(engine).count(("GET", PREDICATES)) == 1
    # **Complement:** zero for a detail whose parts are all ``record``.
    engine.requests.clear()
    body = m7("amendment")
    body["parts"] = [body["parts"][1]]
    detail(engine, body)
    page(operator_client, DETAIL_URL)
    assert calls(engine).count(("GET", PREDICATES)) == 0


def test_a_failed_registry_read_shows_short_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    detail(engine)
    engine.respond("GET", PREDICATES_ROUTE, 503, json=error_json("database_unavailable"))
    html = page(operator_client, DETAIL_URL)
    assert f'<code title="{HOLDS}">…4P7R0PR2</code>' in row(html, "part-1")


def test_a_state_change_part_and_timing(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("amendment")
    body["parts"][0].update(
        subject_id=None,
        predicate_id=None,
        state_id="01JM7SD0000000000000000001",
        written_fact_id=None,
        written_state_change_id="01JM7SC0000000000000000001",
        timing_uncertain=True,
        valid_from_minutes=60,
        valid_to_minutes=120,
    )
    detail(engine, body)
    html = page(operator_client, DETAIL_URL)
    part = row(html, "part-1")
    assert '<td class="what">— · —</td>' in part
    assert '[60, 120) min <span class="badge timing-uncertain">Timing uncertain</span>' in part
    assert '<td class="wrote"><code title="01JM7SC0000000000000000001">' in part
    assert "01JM7SD0000000000000000001" in part  # state_id under More
    assert calls(engine).count(("GET", PREDICATES)) == 0


def test_a_note_part(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("amendment")
    body["parts"] = [
        dict(
            body["parts"][1],
            kind="lite_note",
            record_table=None,
            record_id=None,
            action=None,
            strategy=None,
        )
    ]
    detail(engine, body)
    part = row(page(operator_client, DETAIL_URL), "part-2")
    assert '<span title="lite_note">Note</span>' in part
    assert '<td class="what">Note</td>' in part


def test_edges_and_dependents(operator_client: Client, engine: FakeEngine) -> None:
    detail(engine)
    registry(engine)
    html = page(operator_client, DETAIL_URL)
    relies = row(html, "relies-0")
    assert '<span title="presupposes">Presupposes</span>' in relies
    assert '<span title="01JM7PT0000000000000000001">#1</span>' in relies
    assert '<span title="fact">Fact</span> <code title="01JM7FA0000000000000000005">' in relies
    dependent = row(html, "dependent-0")
    later = f'<span title="01JM7PT0000000000000000006">{escape(LATER_PART_TEXT)}</span>'
    assert later in dependent
    assert '<span title="fact">Fact</span> <code title="01JM7FA0000000000000000001">' in dependent
    assert DEPENDENTS_TRUNCATED not in html


def test_an_unknown_part_id_and_another_table(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("amendment")
    body["relies_on"][0]["part_id"] = "01JM7PT0000000000000000099"
    body["dependents"][0]["record"] = {"table": "utterances", "id": "01JM7UT0000000000000000001"}
    detail(engine, body)
    registry(engine)
    html = page(operator_client, DETAIL_URL)
    assert '<span title="01JM7PT0000000000000000099">…00000099</span>' in row(html, "relies-0")
    assert (
        '<span title="utterances">Utterances</span> <code title="01JM7UT0000000000000000001">'
        in (row(html, "dependent-0"))
    )


def test_truncated_warning(operator_client: Client, engine: FakeEngine) -> None:
    detail(engine, dict(m7("amendment"), truncated=True, dependents=[]))
    registry(engine)
    html = page(operator_client, DETAIL_URL)
    assert f'<p class="warning">{DEPENDENTS_TRUNCATED}</p>' in html
    assert NO_DEPENDENTS in html  # whatever the list holds


def test_malformed_part_is_a_dash_row(operator_client: Client, engine: FakeEngine) -> None:
    """E15/E23: a part whose ``ordinal`` isn't an int, and edges with no ``edge_type``, are
    dash rows with the raw entry under More; the page still renders. (A part that isn't an
    object is :func:`test_non_object_part_is_a_dash_row`; an edge that isn't one still fails
    ``AmendmentDetail``'s parse; :func:`test_non_object_entries_never_raise` covers the
    helpers.)"""
    body = m7("amendment")
    body["parts"] = [{"ordinal": "two", "kind": "record"}, {"ordinal": True}, body["parts"][1]]
    body["relies_on"] = [{"part_id": "x"}]
    body["dependents"] = [{"record": {"table": "utterances"}}]
    detail(engine, body)
    html = page(operator_client, DETAIL_URL)
    assert re.findall(r'<tr id="(part-[^"]+)">', html) == ["part-bad-0", "part-bad-1", "part-2"]
    bad = row(html, "part-bad-0")
    assert bad.count("<td") == 7
    assert bad.count(">—</td>") == 6
    assert "&quot;two&quot;" in bad  # the raw part under More
    assert "<td>—</td><td>—</td><td>—<details>" in row(html, "relies-bad-0")
    assert "<td>—</td><td>—</td><td>—<details>" in row(html, "dependent-bad-0")
    assert "utterances" in row(html, "dependent-bad-0")


def test_non_object_part_keeps_the_detail() -> None:
    """#347: a part that isn't an object never fails ``AmendmentDetail``'s parse: ``parts``
    keeps the objects and ``unparsed`` the raw list (a non-list ``parts`` reads as [])."""
    body = m7("amendment")
    good = body["parts"][1]
    body["parts"] = ["x", good, 7]
    found = AmendmentDetail.from_json(body)
    assert found is not None
    assert found.parts == [good]
    assert found.unparsed == {"parts": ["x", good, 7]}
    assert found.id == body["id"]
    whole = AmendmentDetail.from_json(m7("amendment"))
    assert whole is not None
    assert whole.unparsed == {}  # nothing lost, nothing kept
    odd = AmendmentDetail.from_json(dict(m7("amendment"), parts="nope"))
    assert odd is not None
    assert odd.parts == []
    assert odd.unparsed == {"parts": "nope"}


def test_non_object_part_is_a_dash_row(operator_client: Client, engine: FakeEngine) -> None:
    """#302 AC 2 (#347): a part that isn't an object is ``<tr id="part-bad-<index>">`` at its
    own index, the raw entry under More, with the summary and the other parts still rendered
    (never #43's panel). The schema types parts as objects, so the body is served unvalidated,
    as an Engine breaking its contract would send it."""
    body = m7("amendment")
    good = body["parts"][1]
    body["parts"] = ["x", good]
    engine.side_effect("GET", ONE_ROUTE, lambda _r: httpx.Response(200, json=body))
    registry(engine)
    html = page(operator_client, DETAIL_URL)
    assert 'class="engine-error' not in html
    assert '<dl class="amendment-summary">' in html
    assert re.findall(r'<tr id="(part-[^"]+)">', html) == ["part-bad-0", f"part-{good['ordinal']}"]
    bad = row(html, "part-bad-0")
    assert bad.count(">—</td>") == 6
    assert "&quot;x&quot;" in bad  # the raw part under More


def test_non_object_entries_never_raise() -> None:
    from console.amendments import dependent_rows, edge_rows, part_rows

    parts = part_rows(["x", None, {"ordinal": 1, "kind": "proposition"}], {}, {}, STORY)
    assert [p.anchor for p in parts] == ["part-bad-0", "part-bad-1", "part-1"]
    assert parts[2].subject is None  # "— · —"
    assert parts[2].predicate is None
    assert [e.anchor for e in edge_rows([7, {"edge_type": "causes"}], ["x"])] == [
        "relies-bad-0",
        "relies-1",
    ]
    rows = dependent_rows([None, {"edge_type": "causes", "record": "x", "target": 3}])
    assert [d.anchor for d in rows] == ["dependent-bad-0", "dependent-1"]
    assert rows[1].record_text == "—"
    assert rows[1].target.kind_text == "—"


def test_story_time_text_rules() -> None:
    from console.amendments import story_time_text

    assert story_time_text(None, None) == "—"
    assert story_time_text(True, 5) == "—"
    assert story_time_text("5", None) == "—"
    assert story_time_text(None, 30) == "[…, 30) min"


def test_lite_empty_edges_texts(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("amendment")
    body.update(relies_on=[], dependents=[], parts=[])
    detail(engine, body)
    html = page(operator_client, DETAIL_URL)
    assert f'<p class="empty">{NO_PARTS}</p>' in html
    assert f'<p class="empty">{NO_RELIES}</p>' in html
    assert f'<p class="empty">{NO_DEPENDENTS}</p>' in html


def test_the_actions_block_holds_only_303s_action(
    operator_client: Client, engine: FakeEngine
) -> None:
    detail(engine)
    registry(engine)
    html = squeezed(page(operator_client, DETAIL_URL))
    # Only #303's action line sits between the summary and the viewpoint line.
    assert re.search(r'</dl><p class="amendment-actions">[^\n]*?</p><p class="viewpoint">', html)


def test_malformed_amendment_id_is_django_404(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client, f"{URL}not-an-id/", status=404)
    assert engine.requests == []


# --- the detail: errors ---------------------------------------------------------------------------


def test_amendment_not_found_in_the_page(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_ROUTE, 404, json=error_json("amendment_not_found"))
    html = page(operator_client, DETAIL_URL)  # 200, the frame kept
    assert '<p class="not-found amendment-not-found">No such amendment for this story</p>' in html
    assert "<table" not in html
    assert f'<a href="{URL}">Back to Amendments</a>' in html
    assert 'class="story-links"' in html


def test_story_not_found_is_the_frames(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories/{story_id}", 404, json=error_json("not_found"))
    html = page(operator_client, DETAIL_URL, status=404)
    assert "Not found (it may have been deleted or truncated)" in html
    assert calls(engine) == [("GET", STORY_READ)]


def test_detail_omniscient_required(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    engine.respond("GET", ONE_ROUTE, 403, json=error_json("omniscient_required"))
    with caplog.at_level(logging.WARNING, logger="console.engine_errors"):
        html = page(operator_client, DETAIL_URL)
    assert OMNISCIENT_REQUIRED_TEXT in html
    assert len([r for r in caplog.records if "omniscient_required" in r.getMessage()]) == 1
    assert "amendment-summary" not in html
    assert f'<a href="{URL}">Back to Amendments</a>' in html


def test_detail_422_is_the_message(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_ROUTE, 422, json=error_json("validation_error", "bad id"))
    html = page(operator_client, DETAIL_URL)
    assert html.index("bad id") < html.index("Back to Amendments")


def test_detail_failure_is_43s_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_ROUTE, 503, json=error_json("database_unavailable"))
    html = page(operator_client, DETAIL_URL)
    assert 'class="engine-error' in html
    assert "amendment-summary" not in html
    assert html.index('class="engine-error') < html.index("Back to Amendments")


def test_detail_bad_response_is_43s_panel(operator_client: Client, engine: FakeEngine) -> None:
    answer_raw(engine, "GET", ONE_ROUTE, {"id": A1})
    html = page(operator_client, DETAIL_URL)
    assert "bad_response" in html
    assert "amendment-summary" not in html


def test_detail_story_importing(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        ONE_ROUTE,
        409,
        json=error_json("story_importing", job_id="01JM7JB0000000000000000007"),
    )
    assert "This story is being imported" in page(operator_client, DETAIL_URL, status=409)


def test_detail_erasure_pending_is_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", ONE_ROUTE, 503, json=error_json("erasure_pending"), headers={"Retry-After": "30"}
    )
    page(operator_client, DETAIL_URL, status=503)


def test_no_write_on_any_get(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** across the list, a filtered and paged list and the detail, the fake's
    log holds no POST, PUT, PATCH or DELETE (the autouse ``no_writes`` holds it for every case)."""
    listed(engine)
    detail(engine)
    registry(engine)
    for url in (URL, f"{URL}?status=reverted", f"{URL}?cursor={A2}", DETAIL_URL):
        page(operator_client, url)
    assert {r.method for r in engine.requests} == {"GET"}

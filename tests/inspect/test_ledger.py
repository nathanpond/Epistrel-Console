"""The Ledger tab (#148): a story's thunks and its promissory ledger.

Runs against the real urlconf on the schema-validating fake Engine. Page 1 is
``tests/fixtures/inspect/ledger_page1.json`` (revision 9, ``next_after_id`` ``TH3``):

- ``TH1``: open, salient, subject slot open in ``located_at(?, …PA1)``, three candidates (Tobin
  0.85, a text-only "the ferryman" 0.4, Mara with no text 0.2), one rejected binding (Tobin,
  conflicting ``FC1`` and ``FC2``), introduced at turn 3 · beat 2, valid time 120; entry ``EN1``
  undischarged;
- ``TH2``: resolved by ``FR1`` (consequential), a negated ``holds(Mara, ?)`` with the literal
  slot open, turn 4 · v2 · beat 1; entries ``EN2`` (discharged by the logged ``QP1``,
  dependence) and ``EN3`` (by the unlogged ``QP2``, null mode);
- ``TH3``: expired, subject open with a known ``40 kg (quantity)`` literal, a text-only
  candidate, ``parent_fact_id`` ``FP1``, a null position and valid time; no entries;
- ``EN4``: owed on the hidden proposition ``PP9``; ``EN5``: on ``TH9``, not on the page.

Page 2 (``ledger_page2.json``, ``next_after_id`` null) holds ``TH4`` with ``EN6``, and a
hidden-fact entry ``EN7`` the Console leaves out off the first page."""

import copy
import json
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine.models import ThunkSketch
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import inspect as views
from console.views import inspect_ledger
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, structured
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

TAB = f"/stories/{STORY}/inspect/ledger/"
LEDGER = "/stories/{story_id}/inspect/ledger"
LEDGER_PATH = f"/stories/{STORY}/inspect/ledger"
PREDICATES = "/stories/{story_id}/predicates"
P = "01JB2Z8Y5Q3W6K9M1N4P7R0"
TH1, TH2, TH3, TH4, TH9 = (P + s for s in ("TH1", "TH2", "TH3", "TH4", "TH9"))
EN1, EN2, EN3, EN4, EN5, EN6, EN7 = (P + f"EN{n}" for n in range(1, 8))
QP1, QP2, QP3 = P + "QP1", P + "QP2", P + "QP3"
MARA, TOBIN = P + "T3A", P + "T3B"
LOC, HOLDS = P + "PR1", P + "PR2"
FR1, FC1, FC2, FP1 = P + "FR1", P + "FC1", P + "FC2", P + "FP1"
NOT_FOUND = "Not found (it may have been deleted or truncated)"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return copy.deepcopy(body)


def page1(**over: Any) -> dict[str, Any]:
    body = fixture("ledger_page1")
    body.update(over)
    return body


def thunk(tid: str, **over: Any) -> dict[str, Any]:
    found: dict[str, Any]
    (found,) = [t for t in fixture("ledger_page1")["thunks"] if t["id"] == tid]
    found.update(over)
    return found


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured())
    fake.respond("GET", STORY_PATH, 200, json=story_json())
    fake.respond("GET", PREDICATES, 200, json=fixture("predicates_list"))
    fake.respond("GET", LEDGER, 200, json=page1())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def tab(client: Client, query: str = "") -> str:
    response = client.get(f"{TAB}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == LEDGER_PATH]


def sent(engine: FakeEngine) -> dict[str, str]:
    (call,) = reads(engine)
    return dict(call.url.params)


def row(html: str, tid: str) -> str:
    found = re.search(rf'<tr id="t-{tid}" class="thunk-row">(.*?)</tr>', html, re.S)
    assert found is not None, tid
    return found.group(1)


def entries_of(html: str, tid: str) -> str:
    found = re.search(rf'<tr class="ledger-entries" data-thunk="{tid}">(.*?)</td></tr>', html, re.S)
    assert found is not None, tid
    return found.group(1)


def entry(html: str, eid: str) -> str:
    found = re.search(rf'<tr id="e-{eid}" class="ledger-entry">(.*?)</tr>', html, re.S)
    assert found is not None, eid
    return found.group(1)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def field_errors(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def non_field(html: str) -> str:
    found = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    return found.group(1) if found else ""


def section(html: str, heading: str) -> str:
    found = re.search(rf"<h2>{heading}</h2>(.*?)</table>", html, re.S)
    return found.group(1) if found else ""


def log_operation(op_id: str) -> EngineOperation:
    return EngineOperation.objects.create(
        operator_username="nate",
        principal="operator",
        kind="respond",
        http_method="POST",
        path=f"/stories/{STORY}/respond",
        story_id=STORY,
        outcome="ok",
        http_status=200,
        request={"body": {}, "headers": {}},
        engine_operation_id=op_id,
    )


# --- the read and its filters --------------------------------------------------------------------


def test_the_default_read_is_omniscient_with_limit_100_and_no_filters(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client)
    assert sent(engine) == {"viewpoint": "omniscient", "limit": "100"}
    assert "<title>Inspect · Ledger · Harbour</title>" in html
    # Convention 2: one registry read; the shell's one story read.
    paths = [r.url.path for r in engine.requests]
    assert paths.count(f"/stories/{STORY}/predicates") == 1
    assert paths.count(f"/stories/{STORY}") == 1
    assert re.findall(r'<tr id="t-([^"]+)" class="thunk-row">', html) == [TH1, TH2, TH3]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?state=open", {"state": "open"}),
        ("?state=resolved", {"state": "resolved"}),
        ("?state=expired", {"state": "expired"}),
        ("?discharged=discharged", {"discharged": "true"}),
        ("?discharged=undischarged", {"discharged": "false"}),
        ("?state=open&discharged=undischarged", {"state": "open", "discharged": "false"}),
        # Complement: "any" (or blank) sends neither parameter.
        ("?state=any&discharged=any", {}),
        ("?state=&discharged=", {}),
    ],
)
def test_filters_map_to_the_engines_parameters(
    operator_client: Client, engine: FakeEngine, query: str, expected: dict[str, str]
) -> None:
    tab(operator_client, query)
    assert sent(engine) == {"viewpoint": "omniscient", "limit": "100", **expected}


@pytest.mark.parametrize("name", ["state", "discharged"])
def test_an_unknown_filter_value_is_a_form_error_with_no_ledger_call(
    operator_client: Client, engine: FakeEngine, name: str
) -> None:
    html = tab(operator_client, f"?{name}=bogus")
    assert reads(engine) == []
    assert "Select a valid choice" in field_errors(html, name)
    assert "ledger-counts" not in html
    assert "Story revision" not in html


# --- paging --------------------------------------------------------------------------------------


def test_next_page_carries_after_id_and_keeps_the_filters(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client, "?state=any&discharged=undischarged")
    assert (
        f'<a class="next-page" href="?state=any&amp;discharged=undischarged&amp;after_id={TH3}">'
        "Next page</a>"
    ) in html
    assert "first-page" not in html
    # The Apply form carries no after_id: a changed filter starts over.
    form = re.search(r'<form method="get"[^>]*class="ledger-form">(.*?)</form>', html, re.S)
    assert form is not None
    assert "after_id" not in form.group(1)


def test_page_two_sends_after_id_and_offers_first_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LEDGER, 200, json=fixture("ledger_page2"))
    html = tab(operator_client, f"?state=open&after_id={TH3}")
    assert sent(engine) == {
        "viewpoint": "omniscient",
        "limit": "100",
        "state": "open",
        "after_id": TH3,
    }
    assert '<a class="first-page" href="?state=open">First page</a>' in html
    assert "next-page" not in html


def test_an_empty_page_reached_by_after_id_says_the_ledger_may_have_changed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LEDGER, 200, json=page1(thunks=[], ledger=[], next_after_id=None))
    html = tab(operator_client, f"?after_id={TH3}")
    assert "No thunks on this page — the ledger may have changed since the last page." in html
    assert '<a class="first-page" href="?">First page</a>' in html


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", "No thunks yet: commitment records a reference the story leaves open."),
        # A discharged filter doesn't narrow thunks.
        ("?discharged=discharged", "No thunks yet: commitment records a reference the story"),
        ("?state=expired", "No thunks match these filters."),
    ],
)
def test_empty_states(
    operator_client: Client, engine: FakeEngine, query: str, expected: str
) -> None:
    engine.respond("GET", LEDGER, 200, json=page1(thunks=[], ledger=[], next_after_id=None))
    html = tab(operator_client, query)
    assert expected in html
    assert '<table class="ledger">' not in html


# --- the thunk rows ------------------------------------------------------------------------------


def test_a_subject_open_thunk_row(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    r = row(html, TH1)
    assert cell(r, "description") == "a stranger is at the harbour"
    assert cell(r, "open-slot") == '<span title="subject">Subject</span>'
    proposition = cell(r, "proposition")
    # The predicate's name from the registry, linked to #136's row; the object a non-cast id.
    assert (
        f'<a href="/stories/{STORY}/inspect/predicates/?layer=all#p-{LOC}" title="{LOC}">'
        "located_at</a>"
    ) in proposition
    assert "negated" not in proposition
    assert f'object=<code title="{P}PA1">…4P7R0PA1</code>' in proposition
    assert "subject=" not in proposition  # the open slot isn't a known argument
    # Candidates by confidence, three decimals; a text-only one without parentheses.
    lines = re.findall(r'<span class="line candidate">(.*?)</span>\n', proposition, re.S)
    assert [text(line) for line in lines] == [
        "the tall man (Tobin) 0.850",
        "the ferryman 0.400",
        "Mara (player) 0.200",
    ]
    assert f'title="{TOBIN}"' in lines[0]
    assert cell(r, "state") == '<span class="badge state-open" title="open">Open</span>'
    assert cell(r, "introduced") == (
        f'<a href="/stories/{STORY}/history/?before_turn=4#turn-3-0">turn 3 · beat 2</a>'
    )
    assert cell(r, "salient") == "yes"
    assert cell(r, "valid-time") == "120 min"
    assert cell(r, "resolved") == "—"
    assert cell(r, "level") == "—"


def test_rejected_bindings_are_an_expandable_list(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client)
    bindings = cell(row(html, TH1), "bindings")
    assert '<details class="rejected-bindings"><summary>1 rejected</summary>' in bindings
    line = text(bindings)
    assert "claim …4P7R0CL1 · beat 2 · bound Tobin · conflicts with …4P7R0FC1, …4P7R0FC2" in line
    assert f'<a href="/stories/{STORY}/inspect/canon/{FC1}/lineage/">' in bindings
    assert f'<a href="/stories/{STORY}/inspect/canon/{FC2}/lineage/">' in bindings
    # Count 0: "—" and no <details>.
    assert cell(row(html, TH2), "bindings") == "—"


def test_a_negated_literal_slot_thunk_resolved_by_a_fact(
    operator_client: Client, engine: FakeEngine
) -> None:
    r = row(tab(operator_client), TH2)
    proposition = cell(r, "proposition")
    assert '>holds</a> <span class="badge negated" title="negated">negated</span>' in proposition
    assert f'subject=<span class="character" title="{MARA}">Mara (player)</span>' in proposition
    assert "literal=" not in proposition  # the open slot
    assert cell(r, "open-slot") == '<span title="literal">Literal</span>'
    assert cell(r, "state") == '<span class="badge state-resolved" title="resolved">Resolved</span>'
    # The lineage link carries subject and predicate: the sketch has both.
    assert cell(r, "resolved") == (
        f'<a href="/stories/{STORY}/inspect/canon/{FR1}/lineage/?subject={MARA}&amp;'
        f'predicate={HOLDS}"><code title="{FR1}">…4P7R0FR1</code></a>'
    )
    assert cell(r, "level") == '<span title="consequential">Consequential</span>'
    assert cell(r, "salient") == "no"
    assert cell(r, "valid-time") == "0 min"
    assert "turn 4 · v2 · beat 1" in cell(r, "introduced")
    assert "#turn-4-2" in cell(r, "introduced")


def test_a_known_quantity_literal_and_a_null_position(
    operator_client: Client, engine: FakeEngine
) -> None:
    r = row(tab(operator_client), TH3)
    proposition = cell(r, "proposition")
    assert "literal=<span>40 kg</span> (quantity)" in proposition
    assert text(proposition).startswith("holds literal=40 kg (quantity) a crate 0.500")
    assert cell(r, "state") == '<span class="badge state-expired" title="expired">Expired</span>'
    assert cell(r, "introduced") == "—"  # no turn: no link
    assert cell(r, "valid-time") == "—"
    # Resolved fact absent; the parent fact in "More", linked to #138.
    assert cell(r, "resolved") == "—"
    assert f'Parent fact: <a href="/stories/{STORY}/inspect/canon/{FP1}/lineage/">' in r
    assert cell(r, "level") == '<span title="incidental">Incidental</span>'


def test_a_resolved_fact_link_is_bare_without_both_subject_and_predicate(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", LEDGER, 200, json=page1(thunks=[thunk(TH1, state="resolved", resolved_fact_id=FR1)])
    )
    resolved = cell(row(tab(operator_client), TH1), "resolved")
    assert f'<a href="/stories/{STORY}/inspect/canon/{FR1}/lineage/">' in resolved


def test_a_boolean_literal_reads_true(operator_client: Client, engine: FakeEngine) -> None:
    sketch = thunk(TH3)["sketch"]
    sketch["literal"] = {"kind": "bool", "value": True}
    engine.respond("GET", LEDGER, 200, json=page1(thunks=[thunk(TH3, sketch=sketch)]))
    assert "literal=<span>true</span> (bool)" in tab(operator_client)


def test_a_literal_without_kind_falls_back_to_the_description(
    operator_client: Client, engine: FakeEngine
) -> None:
    sketch = thunk(TH3)["sketch"]
    sketch["literal"] = {"value": 40}
    engine.respond("GET", LEDGER, 200, json=page1(thunks=[thunk(TH3, sketch=sketch)]))
    r = row(tab(operator_client), TH3)
    assert cell(r, "description") == "someone holds 40"
    assert cell(r, "proposition") == "—"
    assert "&quot;sketch&quot;" in r  # the raw sketch in "More"


def test_more_than_five_candidates_show_five_and_the_rest_in_more(
    operator_client: Client, engine: FakeEngine
) -> None:
    sketch = thunk(TH1)["sketch"]
    sketch["candidates"] = [{"text": f"c{n}", "confidence": n / 10} for n in range(1, 8)]
    engine.respond("GET", LEDGER, 200, json=page1(thunks=[thunk(TH1, sketch=sketch)]))
    r = row(tab(operator_client), TH1)
    shown = re.findall(r'<span class="line candidate">(.*?)</span>', r, re.S)
    assert [text(s) for s in shown] == ["c7 0.700", "c6 0.600", "c5 0.500", "c4 0.400", "c3 0.300"]
    assert '<span class="line muted">+2 more</span>' in r
    assert "&quot;more_candidates&quot;" in r


def test_an_unknown_state_gets_the_neutral_badge(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LEDGER, 200, json=page1(thunks=[thunk(TH1, state="pending_review")]))
    html = tab(operator_client)
    assert cell(row(html, TH1), "state") == (
        '<span class="badge state-other" title="pending_review">Pending review</span>'
    )
    assert "open 0 · resolved 0 · expired 0 · other 1" in text(html)


# --- ledger entries ------------------------------------------------------------------------------


def test_entries_sit_under_their_thunk(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    assert f'id="e-{EN1}"' in entries_of(html, TH1)
    group = entries_of(html, TH2)
    assert re.findall(r'<tr id="e-([^"]+)"', group) == [EN2, EN3]  # the Engine's order
    assert (
        "<thead><tr><th>Entry</th><th>Kind</th><th>Introduced</th><th>Discharged by</th>"
        "<th>Mode</th></tr></thead>"
    ) in group
    assert 'colspan="12"' in html
    assert '<span class="muted no-entries">No ledger entries</span>' in entries_of(html, TH3)


def test_an_undischarged_entry_reads_undischarged_in_bold(
    operator_client: Client, engine: FakeEngine
) -> None:
    e = entry(tab(operator_client), EN1)
    assert cell(e, "discharged-by") == "<strong>undischarged</strong>"
    assert cell(e, "mode") == "—"
    assert '<span title="salient_reference">Salient reference</span>' in e
    assert cell(e, "introduced") == (
        f'<a href="/stories/{STORY}/history/?before_turn=4#turn-3-0">turn 3 · beat 2</a>'
    )


def test_a_discharge_links_its_logged_operation_and_is_plain_code_otherwise(
    operator_client: Client, engine: FakeEngine
) -> None:
    logged = log_operation(QP1)
    html = tab(operator_client)
    e2 = entry(html, EN2)
    assert cell(e2, "discharged-by") == (
        f'<a class="operation-link" href="/operations/{logged.pk}/">'
        f'<code title="{QP1}">…4P7R0QP1</code></a>'
    )
    assert cell(e2, "mode") == '<span title="dependence">Dependence</span>'
    # Complement: an unlogged operation is a plain short id, and no history link is invented.
    e3 = entry(html, EN3)
    assert cell(e3, "discharged-by") == f'<code title="{QP2}">…4P7R0QP2</code>'
    assert cell(e3, "mode") == "—"  # discharged by <op> · —
    assert "/history/" not in cell(e3, "discharged-by")


def test_operation_links_is_called_once_per_render(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def spy(story_id: str, ids: Any) -> dict[str, int]:
        calls.append((story_id, list(ids)))
        return {}

    monkeypatch.setattr(inspect_ledger, "operation_links", spy)
    tab(operator_client)
    assert calls == [(STORY, [QP1, QP2, QP3])]


def test_hidden_fact_entries_render_on_the_first_page_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client)
    hidden = section(html, "Owed on hidden facts")
    assert re.findall(r'<tr id="e-([^"]+)"', hidden) == [EN4]
    assert "<th>Proposition</th>" in hidden
    assert f'<code title="{P}PP9">…4P7R0PP9</code>' in hidden
    # Complement: page 2 has none, even when the answer carries one.
    engine.respond("GET", LEDGER, 200, json=fixture("ledger_page2"))
    page2 = tab(operator_client, f"?after_id={TH3}")
    assert "Owed on hidden facts" not in page2
    assert EN7 not in page2


def test_entries_for_thunks_not_on_the_page_get_their_own_table(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client)
    orphans = section(html, "Entries for thunks not on this page")
    assert re.findall(r'<tr id="e-([^"]+)"', orphans) == [EN5]
    assert f'<code title="{TH9}">…4P7R0TH9</code>' in orphans
    assert '<span title="render">Render</span>' in orphans
    # Absent when every entry's thunk is on the page.
    engine.respond("GET", LEDGER, 200, json=fixture("ledger_page2"))
    assert "Entries for thunks not on this page" not in tab(operator_client, f"?after_id={TH3}")


# --- counts --------------------------------------------------------------------------------------


def test_counts_on_a_page_with_a_next_page(operator_client: Client, engine: FakeEngine) -> None:
    counts = text(re.findall(r'<p class="ledger-counts">(.*?)</p>', tab(operator_client), re.S)[0])
    assert counts == (
        "Story revision 9 · Thunks on this page: open 1 · resolved 1 · expired 1 · "
        "Entries on this page: discharged 3 · undischarged 2"
    )


@pytest.mark.parametrize(
    ("query", "body", "label"),
    [
        ("", {"next_after_id": None}, ""),
        ("?state=open", {"next_after_id": None}, " matching filters"),
        (f"?after_id={TH3}", {"next_after_id": None}, " on this page"),
        ("?discharged=discharged", {}, " on this page, matching filters"),
    ],
)
def test_count_labels(
    operator_client: Client, engine: FakeEngine, query: str, body: dict[str, Any], label: str
) -> None:
    engine.respond("GET", LEDGER, 200, json=page1(**body))
    counts = text(
        re.findall(r'<p class="ledger-counts">(.*?)</p>', tab(operator_client, query), re.S)[0]
    )
    assert f"Thunks{label}: open" in counts
    assert f"Entries{label}: discharged" in counts


# --- errors and degraded reads -------------------------------------------------------------------


def test_module_off_is_the_greyed_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", LEDGER, 409, json=error_json("module_off", "commitment is off", module="commitment")
    )
    html = tab(operator_client)
    assert '<div class="panel unavailable">' in html
    assert "<code>commitment</code>" in html
    assert 'class="engine-error"' not in html


@pytest.mark.parametrize(
    ("field", "name"), [("query.state", "state"), ("query.discharged", "discharged")]
)
def test_a_422_naming_a_filter_lands_beside_it(
    operator_client: Client, engine: FakeEngine, field: str, name: str
) -> None:
    engine.respond(
        "GET", LEDGER, 422, json=error_json("validation_error", "bad value", field=field)
    )
    html = tab(operator_client)
    assert "bad value" in field_errors(html, name)
    assert "Story revision" not in html


def test_a_bad_after_id_lands_above_the_filters_with_first_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", LEDGER, 422, json=error_json("validation_error", "after_id: bad id", field="query")
    )
    html = tab(operator_client, "?state=open&after_id=nope")
    assert sent(engine)["after_id"] == "nope"  # passed through, not validated
    assert "after_id: bad id" in non_field(html)
    assert '<a class="first-page" href="?state=open">First page</a>' in html
    assert "ledger-counts" not in html


def test_viewpoint_unresolved_and_omniscient_required(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LEDGER, 422, json=error_json("viewpoint_unresolved", "who?"))
    assert "This read needs the omniscient viewpoint" in non_field(tab(operator_client))
    engine.respond("GET", LEDGER, 403, json=error_json("omniscient_required", "omniscient only"))
    html = tab(operator_client)
    assert '<p class="omniscient-required">This read needs the omniscient viewpoint</p>' in html


def test_not_found_is_the_not_found_line(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LEDGER, 404, json=error_json("not_found", "gone"))
    html = tab(operator_client)
    assert NOT_FOUND in html
    assert "Story revision" not in html


@pytest.mark.parametrize("code", ["database_unavailable", "ledger_unavailable"])
def test_a_503_is_the_error_panel(operator_client: Client, engine: FakeEngine, code: str) -> None:
    engine.respond("GET", LEDGER, 503, json=error_json(code, "later"))
    html = tab(operator_client)
    assert f"Engine error 503 {code}" in html
    assert "Story revision" not in html
    assert "ledger-counts" not in html


def test_erasure_pending_is_the_held_wrapper(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LEDGER, 503, json=error_json("erasure_pending", "held"))
    response = operator_client.get(TAB)
    assert response.status_code == 503


def test_a_failed_registry_read_degrades_to_predicate_short_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    proposition = cell(row(tab(operator_client), TH1), "proposition")
    assert (
        f'<a href="/stories/{STORY}/inspect/predicates/?layer=all#p-{LOC}">'
        f'<code title="{LOC}">…4P7R0PR1</code></a>'
    ) in proposition


def test_a_failed_story_read_degrades_to_entity_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = tab(operator_client)
    assert f'subject=<code title="{MARA}">…4P7R0T3A</code>' in cell(row(html, TH2), "proposition")
    assert "Cast names unavailable — showing ids" in html


# --- the sketch model ----------------------------------------------------------------------------


def test_thunk_sketch_parses_the_engines_shape() -> None:
    sketch = ThunkSketch.from_json(thunk(TH3)["sketch"])
    assert sketch is not None
    assert sketch.literal is not None
    assert (sketch.literal.kind, sketch.literal.value, sketch.literal.unit) == (
        "quantity",
        40,
        "kg",
    )
    assert sketch.parent_fact_id == FP1
    ((candidate),) = sketch.candidates
    assert (candidate.entity_id, candidate.text, candidate.confidence) == (None, "a crate", 0.5)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"open_slot": "subject"},  # no predicate
        {"predicate_id": LOC},  # no open slot
        {"predicate_id": LOC, "open_slot": "subject", "literal": {"value": 4}},  # no kind
        {"predicate_id": LOC, "open_slot": "subject", "candidates": "x"},
    ],
)
def test_a_malformed_sketch_is_none(raw: object) -> None:
    assert ThunkSketch.from_json(raw) is None


def test_odd_sketch_fields_go_to_extras() -> None:
    sketch = ThunkSketch.from_json(
        {
            "predicate_id": LOC,
            "open_slot": "subject",
            "subject_text": 7,
            "span": "x",
            "candidates": [{"text": "a", "confidence": "high"}, {"text": "b", "confidence": 1}],
            "future": True,
        }
    )
    assert sketch is not None
    assert sketch.subject_text is None
    assert sketch.extras == {"subject_text": 7, "span": "x", "future": True}
    assert [c.text for c in sketch.candidates] == ["b"]
    assert sketch.skipped_candidates == ((1, {"text": "a", "confidence": "high"}),)

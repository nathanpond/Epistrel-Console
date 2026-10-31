"""The Ledger tab (#148): a story's thunks and its promissory ledger.

Runs against the real urlconf on the schema-validating fake Engine. Page 1 is
``tests/fixtures/inspect/ledger_page1.json`` (revision 9, ``next_after_id`` ``TH3``):

- ``TH1``: open, salient, subject slot open in ``located_at(?, …PA1)``, three candidates (Tobin
  0.85, a text-only "the ferryman" 0.4, Mara with no text 0.2), one rejected binding (Tobin,
  conflicting ``FC1`` and ``FC2``), introduced at turn 3 · beat 2, valid time 120; entry ``EN1``
  undischarged;
- ``TH2``: resolved by ``FR1`` (consequential), a negated ``holds(Mara, ?)`` with the literal
  slot open, turn 4 · v2 · beat 1; entries ``EN2`` (discharged by the logged ``QP1``,
  dependence) and ``EN3`` (by the unlogged ``QP2``, null mode); two could-have-known flags
  (#208): Tobin mild, gap 600, then Mara strong, gap 64800;
- ``TH3``: expired, subject open with a known ``40 kg (quantity)`` literal, a text-only
  candidate, ``parent_fact_id`` ``FP1``, a null position and valid time; no entries;
- ``EN4``: owed on the hidden proposition ``PP9``; ``EN5``: on ``TH9``, not on the page;
- hidden facts (#208): ``HF1`` ``located_at(Tobin, …PA1)``, scope ``characters`` known to Mara
  and the non-cast ``CX1``, valid ``[30, …)``, committed turn 3; ``HF2`` negated
  ``holds(Mara, …PA3)``, scope ``engine``, valid ``[…, 90)``, committed turn 0.

Page 2 (``ledger_page2.json``, ``next_after_id`` null) holds ``TH4`` with ``EN6``, a
hidden-fact entry ``EN7`` the Console leaves out off the first page, and ``hidden_facts: []``."""

import copy
import json
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.canon import STRING_SHOWN, literal_view
from console.engine.client import EngineClient
from console.engine.models import ThunkSketch, TypedLiteral
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


def test_a_long_string_literal_shows_its_full_value_as_title(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#174: the cut text, with the full quoted value as its ``title``."""
    long = "x" * (STRING_SHOWN + 5)
    sketch = thunk(TH3)["sketch"]
    sketch["literal"] = {"kind": "str", "value": long}
    engine.respond("GET", LEDGER, 200, json=page1(thunks=[thunk(TH3, sketch=sketch)]))
    shown = literal_view(TypedLiteral(kind="str", value=long))
    assert shown.full == f'"{long}"'
    expected = f'literal=<span title="{escape(shown.full)}">{escape(shown.text)}</span> (str)'
    assert expected in tab(operator_client)


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
    assert 'colspan="13"' in html
    assert 'colspan="12"' not in html
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
        "Entries on this page: discharged 3 · undischarged 2 · "
        "Could-have-known flags on this page: 1"
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


# --- hidden facts (#208) -------------------------------------------------------------------------

HF1, HF2, HF3, CX1, PA1, PA3 = (P + s for s in ("HF1", "HF2", "HF3", "CX1", "PA1", "PA3"))
HIDDEN_HEADING = (
    '<h2 id="hidden-facts">Hidden facts <small class="muted">not affected by the filters</small>'
    "</h2>"
)
ENGINE_ONLY = (
    '<span title="Scoped to the engine: no character\'s partition holds it">'
    "Nobody (engine only)</span>"
)


def hidden_facts(html: str) -> str:
    """The section, from its heading to the next heading or the paging line."""
    found = re.search(r'<h2 id="hidden-facts">(.*?)(?=<h2>|<p class="paging">)', html, re.S)
    assert found is not None
    return found.group(1)


def hidden_row(html: str, fid: str) -> str:
    found = re.search(rf'<tr id="hf-{fid}">(.*?)</tr>', html, re.S)
    assert found is not None, fid
    return found.group(1)


def hidden_fact(fid: str, **over: Any) -> dict[str, Any]:
    found: dict[str, Any]
    (found,) = [f for f in fixture("ledger_page1")["hidden_facts"] if f["fact_id"] == fid]
    found.update(over)
    return found


def unvalidated(engine: FakeEngine, body: dict[str, Any]) -> None:
    """Answer the ledger read with a shape the schema refuses (a malformed item)."""
    engine.side_effect("GET", LEDGER, lambda request: httpx.Response(200, json=body))


def test_hidden_facts_render_on_the_first_page(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    assert HIDDEN_HEADING in html
    # After the thunk table and the flag legend, above "Owed on hidden facts".
    order = [
        html.index('<table class="ledger">'),
        html.index('<p class="muted flag-legend">'),
        html.index(HIDDEN_HEADING),
        html.index("<h2>Owed on hidden facts</h2>"),
    ]
    assert order == sorted(order)
    section_html = hidden_facts(html)
    assert '<table class="hidden-facts">' in section_html
    assert (
        "<thead><tr><th>Fact</th><th>Proposition</th><th>Known to</th><th>Valid</th>"
        "<th>Committed</th></tr></thead>"
    ) in section_html
    assert re.findall(r'<tr id="hf-([^"]+)">', section_html) == [HF1, HF2]
    r1 = hidden_row(html, HF1)
    assert cell(r1, "fact") == (
        f'<a href="/stories/{STORY}/inspect/canon/{HF1}/lineage/?subject={TOBIN}&amp;'
        f'predicate={LOC}"><code title="{HF1}">…4P7R0HF1</code></a>'
    )
    proposition = cell(r1, "proposition")
    assert f'<span class="character" title="{TOBIN}">Tobin</span>' in proposition
    assert ">located_at</a>" in proposition
    assert f'<code title="{PA1}">…4P7R0PA1</code>' in proposition
    assert cell(r1, "valid") == "[30, …) min"
    assert cell(r1, "committed") == (
        f'<a href="/stories/{STORY}/history/?before_turn=4#turn-3-0">turn 3</a>'
    )
    r2 = hidden_row(html, HF2)
    assert '<strong class="polarity">NOT</strong>' in cell(r2, "proposition")
    assert cell(r2, "valid") == "[…, 90) min"
    # Turn 0 is linked too.
    assert cell(r2, "committed") == (
        f'<a href="/stories/{STORY}/history/?before_turn=1#turn-0-0">turn 0</a>'
    )


def test_a_null_committing_turn_reads_a_dash(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", LEDGER, 200, json=page1(hidden_facts=[hidden_fact(HF1, narrative_turn=None)])
    )
    assert cell(hidden_row(tab(operator_client), HF1), "committed") == "—"


def test_hidden_facts_are_not_rendered_on_page_two(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LEDGER, 200, json=fixture("ledger_page2"))
    html = tab(operator_client, f"?after_id={TH3}")
    assert 'id="hidden-facts"' not in html
    assert "hidden-facts-none" not in html
    # Complement: whatever the list holds off the first page, it's ignored.
    engine.respond(
        "GET", LEDGER, 200, json={**fixture("ledger_page2"), "hidden_facts": [hidden_fact(HF1)]}
    )
    html = tab(operator_client, f"?after_id={TH3}")
    assert 'id="hidden-facts"' not in html
    assert f'id="hf-{HF1}"' not in html


def test_engine_scope_reads_nobody(operator_client: Client, engine: FakeEngine) -> None:
    assert cell(hidden_row(tab(operator_client), HF2), "known-to") == ENGINE_ONLY
    # Any characters it carries are ignored.
    engine.respond(
        "GET", LEDGER, 200, json=page1(hidden_facts=[hidden_fact(HF2, characters=[MARA])])
    )
    assert cell(hidden_row(tab(operator_client), HF2), "known-to") == ENGINE_ONLY


def test_known_to_names_cast_and_short_ids_others(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client)
    assert cell(hidden_row(html, HF1), "known-to") == (
        f'<span class="character" title="{MARA}">Mara (player)</span>, '
        f'<code title="{CX1}">…4P7R0CX1</code>'
    )
    engine.respond(
        "GET",
        LEDGER,
        200,
        json=page1(
            hidden_facts=[
                hidden_fact(HF1, characters=[]),
                hidden_fact(HF2, scope="party_only", characters=[TOBIN]),
                hidden_fact(HF1, fact_id=HF3, scope="Engine", characters=[]),
            ]
        ),
    )
    html = tab(operator_client)
    assert cell(hidden_row(html, HF1), "known-to") == '<span class="muted">no character</span>'
    # An unknown scope is humanised and titled with the raw value, then its characters.
    assert cell(hidden_row(html, HF2), "known-to") == (
        f'<span title="party_only">Party only</span>: '
        f'<span class="character" title="{TOBIN}">Tobin</span>'
    )
    # Scope matching is exact and case-sensitive.
    assert cell(hidden_row(html, HF3), "known-to") == '<span title="Engine">Engine</span>'


def test_empty_hidden_facts_reads_none(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LEDGER, 200, json=page1(hidden_facts=[]))
    html = tab(operator_client)
    assert HIDDEN_HEADING in html
    assert '<p class="hidden-facts-none">None</p>' in hidden_facts(html)
    assert '<table class="hidden-facts">' not in html
    # Whatever the thunk table shows.
    engine.respond(
        "GET", LEDGER, 200, json=page1(thunks=[], ledger=[], hidden_facts=[], next_after_id=None)
    )
    html = tab(operator_client)
    assert '<p class="ledger-none">' in html
    assert '<p class="hidden-facts-none">None</p>' in hidden_facts(html)


def test_malformed_hidden_fact_goes_to_the_section_more(
    operator_client: Client, engine: FakeEngine
) -> None:
    bad = {"fact_id": HF3, "scope": "characters"}
    unvalidated(engine, page1(hidden_facts=[hidden_fact(HF1), bad]))
    html = tab(operator_client)
    section_html = hidden_facts(html)
    assert re.findall(r'<tr id="hf-([^"]+)">', section_html) == [HF1]
    unread = re.search(
        r'<details class="hidden-facts-unread"><summary>More</summary>(.*?)</details>',
        section_html,
        re.S,
    )
    assert unread is not None
    assert "<p>Some hidden facts couldn't be read; the Engine's list as sent:</p>" in unread[1]
    assert HF3 in unread[1]
    assert HF1 in unread[1]
    assert "hidden-facts-none" not in section_html


def test_unparseable_proposition_keeps_its_row(operator_client: Client, engine: FakeEngine) -> None:
    unvalidated(engine, page1(hidden_facts=[hidden_fact(HF1, proposition={"bogus": 7})]))
    r = hidden_row(tab(operator_client), HF1)
    proposition = cell(r, "proposition")
    assert proposition.startswith('<details class="raw-proposition"><summary>More</summary>')
    assert "&quot;bogus&quot;: 7" in proposition
    # The other cells render; the fact link is bare without a subject and predicate.
    assert cell(r, "fact") == (
        f'<a href="/stories/{STORY}/inspect/canon/{HF1}/lineage/">'
        f'<code title="{HF1}">…4P7R0HF1</code></a>'
    )
    assert "Mara (player)" in cell(r, "known-to")
    assert cell(r, "valid") == "[30, …) min"
    assert "turn 3" in cell(r, "committed")


def test_hidden_facts_ignore_filters(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client, "?state=open&discharged=undischarged")
    assert re.findall(r'<tr id="hf-([^"]+)">', hidden_facts(html)) == [HF1, HF2]
    assert len(reads(engine)) == 1


def test_a_failed_story_read_degrades_hidden_facts_to_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    r = hidden_row(tab(operator_client), HF1)
    assert cell(r, "known-to") == (
        f'<code title="{MARA}">…4P7R0T3A</code>, <code title="{CX1}">…4P7R0CX1</code>'
    )
    proposition = cell(r, "proposition")
    assert f'<code title="{TOBIN}">…4P7R0T3B</code>' in proposition
    assert f'<code title="{LOC}">…4P7R0PR1</code>' in proposition


def test_failed_ledger_read_has_no_hidden_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LEDGER, 503, json=error_json("ledger_unavailable", "later"))
    html = tab(operator_client)
    assert "Engine error 503 ledger_unavailable" in html
    assert 'id="hidden-facts"' not in html
    engine.respond("GET", LEDGER, 404, json=error_json("not_found", "gone"))
    html = tab(operator_client)
    assert NOT_FOUND in html
    assert 'id="hidden-facts"' not in html


def test_one_ledger_read_per_render(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    assert f'id="hf-{HF1}"' in html
    assert len(reads(engine)) == 1
    # No separate hidden-facts read: only the shell's reads, the registry and the ledger.
    assert {r.url.path for r in engine.requests} == {
        f"/stories/{STORY}",
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}/predicates",
        LEDGER_PATH,
    }


# --- could-have-known flags (#208) ---------------------------------------------------------------


def flag_line(name: str, severity: str, title: str, gap: str, css: str) -> str:
    return (
        f'<span class="flag-line {css}">{name} · <span title="{title}">{severity}</span> · '
        f"gap {gap} min</span>"
    )


TOBIN_REF = f'<span class="character" title="{TOBIN}">Tobin</span>'
MARA_REF = f'<span class="character" title="{MARA}">Mara (player)</span>'


def test_could_have_known_lines_and_titles(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    assert "<th>Level</th><th>Could have known</th><th>Rejected bindings</th>" in html
    assert cell(row(html, TH2), "could-have-known") == (
        flag_line(TOBIN_REF, "Mild", "under one story-day", "600", "flag-mild")
        + "<br>"
        + flag_line(MARA_REF, "Strong", "30 story-days or more", "64,800", "flag-strong")
    )
    flags = [
        {"character_id": MARA, "severity": "moderate", "gap_minutes": 4320},
        {"character_id": CX1, "severity": "very_strong", "gap_minutes": 9},
        {"character_id": MARA, "severity": "Mild", "gap_minutes": 1},
        {"character_id": MARA, "severity": "moderate", "gap_minutes": 4320},
    ]
    # Flags show whatever the thunk's state (TH1 is open).
    engine.respond(
        "GET", LEDGER, 200, json=page1(thunks=[thunk(TH1, could_have_known_flags=flags)])
    )
    lines = cell(row(tab(operator_client), TH1), "could-have-known").split("<br>")
    moderate = flag_line(MARA_REF, "Moderate", "under 30 story-days", "4,320", "flag-moderate")
    assert lines == [
        moderate,
        flag_line(
            f'<code title="{CX1}">…4P7R0CX1</code>',
            "Very strong",
            "very_strong",
            "9",
            "flag-other",
        ),
        # Severity matching is exact and case-sensitive.
        flag_line(MARA_REF, "Mild", "Mild", "1", "flag-other"),
        moderate,  # duplicates kept
    ]


def test_no_flags_reads_a_dash(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client)
    assert cell(row(html, TH1), "could-have-known") == "—"
    assert cell(row(html, TH3), "could-have-known") == "—"
    assert "could_have_known_flags_as_sent" not in html


def test_malformed_flag_goes_to_more(operator_client: Client, engine: FakeEngine) -> None:
    good = {"character_id": TOBIN, "severity": "mild", "gap_minutes": 600}
    bad = {"character_id": MARA, "severity": "strong", "gap_minutes": "lots"}
    sketch = thunk(TH1)["sketch"]
    sketch["could_have_known_flags_as_sent"] = "from the sketch"
    unvalidated(
        engine,
        page1(
            thunks=[
                thunk(TH1, sketch=sketch, could_have_known_flags=[good, bad]),
                thunk(TH3, could_have_known_flags=[bad]),
            ],
            next_after_id=None,
        ),
    )
    html = tab(operator_client)
    r1 = row(html, TH1)
    # The typed flag still renders.
    assert cell(r1, "could-have-known") == flag_line(
        TOBIN_REF, "Mild", "under one story-day", "600", "flag-mild"
    )
    more = re.search(r"<pre>(.*?)</pre>", cell(r1, "more"), re.S)
    assert more is not None
    shown = json.loads(more[1].replace("&quot;", '"'))
    # Set after the sketch's extras: it wins the name clash.
    assert shown["could_have_known_flags_as_sent"] == [good, bad]
    # A thunk whose only flag is unread reads "—" and still counts as flagged.
    assert cell(row(html, TH3), "could-have-known") == "—"
    assert "could_have_known_flags_as_sent" in cell(row(html, TH3), "more")
    counts = text(re.findall(r'<p class="ledger-counts">(.*?)</p>', html, re.S)[0])
    assert counts.endswith("Could-have-known flags: 2")


@pytest.mark.parametrize(
    ("query", "body", "label"),
    [
        ("", {"next_after_id": None}, ""),
        ("?state=open", {"next_after_id": None}, " matching filters"),
        (f"?after_id={TH3}", {"next_after_id": None}, " on this page"),
        ("?discharged=discharged", {}, " on this page, matching filters"),
    ],
)
def test_flag_count_uses_the_counts_label(
    operator_client: Client, engine: FakeEngine, query: str, body: dict[str, Any], label: str
) -> None:
    engine.respond("GET", LEDGER, 200, json=page1(**body))
    html = tab(operator_client, query)
    assert (f'<span class="flag-counts">Could-have-known flags{label}: 1</span></p>') in html
    # Shown when 0.
    engine.respond("GET", LEDGER, 200, json=page1(thunks=[thunk(TH1)], **body))
    html = tab(operator_client, query)
    assert f'<span class="flag-counts">Could-have-known flags{label}: 0</span></p>' in html


def test_the_flag_legend_sits_under_a_thunk_table_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    legend = (
        '<p class="muted flag-legend">Could have known: the resolution bound a character who, by '
        "story-time, could already have known it. A warning only; the resolution stands.</p>"
    )
    assert legend in tab(operator_client)
    engine.respond("GET", LEDGER, 200, json=page1(thunks=[], ledger=[], next_after_id=None))
    assert "flag-legend" not in tab(operator_client)

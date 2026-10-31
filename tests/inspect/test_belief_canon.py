"""Beliefs compared with canon (#191): the rule table over ``canon_mark`` and the Beliefs
section's Canon column on the schema-validating fake Engine.

``beliefs_canon_alice.json`` (revision 7) holds Alice's ``BC01`` (the key is in the study,
testimony), ``BC02`` (the key is in the hall) and ``BC03`` (a ``nested`` row). Canon
(``facts_current_page1.json`` then ``facts_current_page2.json``, keyed on ``cursor``, C14) places
the key in the hall (``FC01``) over ``[0, …)``; ``located_at`` is ``functional`` and ``holds``
``multi`` in ``predicates_list.json``. ``facts_current_race.json`` carries revision 8."""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.belief_canon import (
    AGREES,
    CONTRADICTS,
    NOT_COMPARED,
    NOT_FIRST_ORDER,
    UNSETTLED,
    CanonMark,
    canon_mark,
    canon_marks,
    facts_by_key,
    summary_text,
)
from console.engine.client import EngineClient
from console.engine.models import BeliefItem, FactBody
from console.engine_access import engine_client_for
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import (
    ALICE,
    BOB,
    EFFECTIVE,
    FIXTURES,
    STORY,
    STORY_PATH,
    story_cast,
    structured_v2,
)
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/inspect/knowledge/beliefs/"
BELIEFS = "/stories/{story_id}/inspect/beliefs"
PREDICATES = "/stories/{story_id}/predicates"
PREDICATES_PATH = f"/stories/{STORY}/predicates"
FACTS = "/admin/stories/{story_id}/facts"
FACTS_PATH = f"/admin/stories/{STORY}/facts"
P = "0000000000000000000000"
BC = {n: P + f"BC{n:02d}" for n in range(1, 4)}
FC = {n: P + f"FC{n:02d}" for n in range(1, 10)}
KEY, STUDY, HALL = P + "EN01", P + "EN02", P + "EN04"
LOC = "01JB2Z8Y5Q3W6K9M1N4P7R0PR1"  # functional
HOLDS = "01JB2Z8Y5Q3W6K9M1N4P7R0PR2"  # multi
ABSENT = P + "PZ99"
CARDINALITY = {LOC: "functional", HOLDS: "multi"}
PAST_NOTICE = (
    "Canon is compared only for the current partition (canon is read by record time, not by "
    "turn) — clear &#x27;As of turn&#x27; to compare"
)
REGISTRY_NOTICE = "Some beliefs weren&#x27;t compared: the predicate registry couldn&#x27;t be read"
LABELS = ("Contradicts canon", "Canon agrees", "No canon record settles it")
TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "console" / "inspect"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return copy.deepcopy(body)


# --- the rule ------------------------------------------------------------------------------------


def prop(
    obj: Any = HALL,
    *,
    predicate: str = LOC,
    polarity: str = "affirmed",
    qualifiers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A proposition about the key: ``obj`` an entity id, a literal dict or a member list."""
    body: dict[str, Any] = {
        "subject_id": KEY,
        "predicate_id": predicate,
        "polarity": polarity,
        "qualifiers": qualifiers or {},
        "valid_time": {"kind": "interval", "start": {"inner": 0, "outer": 0}},
    }
    if isinstance(obj, dict):
        body["literal"] = obj
    elif isinstance(obj, list):
        body["object_set"] = obj
    else:
        body["object_id"] = obj
    return body


def belief(
    proposition: Any, start: int | None = 0, end: int | None = None, kind: str = "first_order"
) -> BeliefItem:
    raw = fixture("beliefs_canon_alice")["beliefs"][0]
    raw.update(proposition=proposition, kind=kind, valid_from=start, valid_to=end)
    parsed = BeliefItem.from_json(raw)
    assert parsed is not None
    return parsed


def fact(
    proposition: dict[str, Any], start: int | None = 0, end: int | None = None, n: int = 1
) -> FactBody:
    raw = fixture("facts_current_page1")["facts"][0]
    raw.update(id=FC[n], proposition=proposition, valid_from=start, valid_to=end)
    parsed = FactBody.from_json(raw)
    assert parsed is not None
    return parsed


def verdict(
    b: BeliefItem, facts: list[FactBody], cardinality: dict[str, str] | None = CARDINALITY
) -> str:
    return canon_mark(b, facts_by_key(facts), cardinality).verdict


INT3 = {"kind": "int", "value": 3}
FLOAT3 = {"kind": "float", "value": 3.0}
CASES: list[tuple[str, BeliefItem, list[FactBody], dict[str, str] | None, str]] = [
    ("same key and polarity", belief(prop()), [fact(prop())], CARDINALITY, AGREES),
    (
        "other polarity",
        belief(prop()),
        [fact(prop(polarity="negated"))],
        CARDINALITY,
        CONTRADICTS,
    ),
    (
        "other polarity, non-overlapping",
        belief(prop(), 0, 10),
        [fact(prop(polarity="negated"), 10, 20)],
        CARDINALITY,
        UNSETTLED,
    ),
    ("zero-width belief inside", belief(prop(), 5, 5), [fact(prop(), 0, 10)], CARDINALITY, AGREES),
    (
        "zero-width belief at a fact's start",
        belief(prop(), 0, 0),
        [fact(prop(), 0, 10)],
        CARDINALITY,
        AGREES,
    ),
    (
        "zero-width belief at a fact's end",
        belief(prop(), 10, 10),
        [fact(prop(), 0, 10)],
        CARDINALITY,
        UNSETTLED,
    ),
    ("two equal points", belief(prop(), 5, 5), [fact(prop(), 5, 5)], CARDINALITY, AGREES),
    ("two other points", belief(prop(), 5, 5), [fact(prop(), 6, 6)], CARDINALITY, UNSETTLED),
    (
        "functional, another object",
        belief(prop(STUDY)),
        [fact(prop(HALL))],
        CARDINALITY,
        CONTRADICTS,
    ),
    (
        "multi, another object",
        belief(prop(STUDY, predicate=HOLDS)),
        [fact(prop(HALL, predicate=HOLDS))],
        CARDINALITY,
        UNSETTLED,
    ),
    (
        "functional, negated belief",
        belief(prop(STUDY, polarity="negated")),
        [fact(prop(HALL))],
        CARDINALITY,
        UNSETTLED,
    ),
    (
        "functional, other qualifiers",
        belief(prop(STUDY, qualifiers={"by": "map"})),
        [fact(prop(HALL, qualifiers={"by": "eye"}))],
        CARDINALITY,
        UNSETTLED,
    ),
    ("int 3 vs float 3.0", belief(prop(INT3)), [fact(prop(FLOAT3))], CARDINALITY, AGREES),
    (
        "quantity, another unit",
        belief(prop({"kind": "quantity", "value": 3, "unit": "kg"})),
        [fact(prop({"kind": "quantity", "value": 3, "unit": "lb"}))],
        {LOC: "multi"},
        UNSETTLED,
    ),
    (
        "unknown literal kind, same value and unit",
        belief(prop({"kind": "colour", "value": "red"})),
        [fact(prop({"kind": "colour", "value": "red"}))],
        CARDINALITY,
        AGREES,
    ),
    (
        "unknown literal kind, another value",
        belief(prop({"kind": "colour", "value": "red"})),
        [fact(prop({"kind": "colour", "value": "blue"}))],
        {LOC: "multi"},
        UNSETTLED,
    ),
    (
        "object set in another order",
        belief(prop([STUDY, HALL])),
        [fact(prop([HALL, STUDY]))],
        CARDINALITY,
        AGREES,
    ),
    (
        "list qualifier in another order",
        belief(prop(qualifiers={"via": [STUDY, HALL]})),
        [fact(prop(polarity="negated", qualifiers={"via": [HALL, STUDY]}))],
        CARDINALITY,
        UNSETTLED,
    ),
    (
        "open belief start, closed fact",
        belief(prop(polarity="negated"), None, 5),
        [fact(prop(), 0, 10)],
        CARDINALITY,
        CONTRADICTS,
    ),
    (
        "open fact end, belief after its start",
        belief(prop(), 100, 200),
        [fact(prop(), 0, None)],
        CARDINALITY,
        AGREES,
    ),
    (
        "both open",
        belief(prop(), None, None),
        [fact(prop(), None, None)],
        CARDINALITY,
        AGREES,
    ),
    (
        "missing from the registry: exact-key only",
        belief(prop(STUDY, predicate=ABSENT)),
        [fact(prop(HALL, predicate=ABSENT))],
        CARDINALITY,
        UNSETTLED,
    ),
    (
        "missing from the registry: exact key still agrees",
        belief(prop(predicate=ABSENT)),
        [fact(prop(predicate=ABSENT))],
        CARDINALITY,
        AGREES,
    ),
    (
        "contradicting and agreeing both present",
        belief(prop(HALL)),
        [fact(prop(HALL), n=1), fact(prop(STUDY), n=2)],
        CARDINALITY,
        CONTRADICTS,
    ),
    ("nested row", belief(prop(), kind="nested"), [fact(prop())], CARDINALITY, NOT_COMPARED),
    (
        "unparsed proposition",
        belief({"subject_id": KEY, "predicate_id": LOC, "valid_time": 5}),
        [fact(prop())],
        CARDINALITY,
        NOT_COMPARED,
    ),
    ("another subject", belief(prop()), [], CARDINALITY, UNSETTLED),
]


@pytest.mark.parametrize(
    ("b", "facts", "cardinality", "expected"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_canon_mark_table(
    b: BeliefItem, facts: list[FactBody], cardinality: dict[str, str] | None, expected: str
) -> None:
    assert verdict(b, facts, cardinality) == expected


def test_canon_mark_facts_shown_and_more() -> None:
    """Up to 3 fact ids of the verdict in read order (each once), then how many more."""
    facts = [fact(prop(STUDY), n=n) for n in range(1, 6)]
    mark = canon_mark(belief(prop(HALL)), facts_by_key([*facts, facts[0]]), CARDINALITY)
    assert mark == CanonMark(CONTRADICTS, (FC[1], FC[2], FC[3]), 2)
    agreeing = canon_mark(belief(prop(STUDY)), facts_by_key(facts[:1]), CARDINALITY)
    assert agreeing == CanonMark(AGREES, (FC[1],), 0)


def test_canon_mark_not_compared_reason() -> None:
    mark = canon_mark(belief(prop(), kind="nested"), {}, CARDINALITY)
    assert mark.reason == NOT_FIRST_ORDER
    assert mark.label == "Not compared"
    assert mark.title == "Only first-order beliefs with a readable proposition are compared"


def test_canon_mark_registry_unavailable() -> None:
    """``cardinality`` ``None``: a row needing the functional rule isn't compared; exact-key
    verdicts still stand; a row with no fact is unsettled."""
    other = canon_mark(belief(prop(STUDY)), facts_by_key([fact(prop(HALL))]), None)
    assert other.verdict == NOT_COMPARED
    assert other.label == "Not compared (predicate registry unavailable)"
    assert verdict(belief(prop(HALL)), [fact(prop(HALL))], None) == AGREES
    assert verdict(belief(prop(polarity="negated")), [fact(prop())], None) == CONTRADICTS
    assert verdict(belief(prop()), [], None) == UNSETTLED


def test_canon_marks_by_belief_id() -> None:
    body = fixture("beliefs_canon_alice")
    beliefs = [b for raw in body["beliefs"] if (b := BeliefItem.from_json(raw)) is not None]
    facts = [fact(prop(HALL))]
    marks = canon_marks(beliefs, facts_by_key(facts), CARDINALITY)
    assert {k: m.verdict for k, m in marks.items()} == {
        BC[1]: CONTRADICTS,
        BC[2]: AGREES,
        BC[3]: NOT_COMPARED,
    }


def test_summary_text_grammar() -> None:
    assert summary_text(1, 1) == "1 of 1 belief on this page contradicts canon"
    assert summary_text(2, 5) == "2 of 5 beliefs on this page contradict canon"
    assert summary_text(1, 3) == "1 of 3 beliefs on this page contradicts canon"


# --- the view ------------------------------------------------------------------------------------


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v2())
    fake.respond("GET", STORY_PATH, 200, json=story_cast())
    fake.respond("GET", PREDICATES, 200, json=fixture("predicates_list"))
    fake.respond("GET", BELIEFS, 200, json=fixture("beliefs_canon_alice"))
    fake.respond("GET", FACTS, 200, json=fixture("facts_current_page1"))
    fake.respond("GET", FACTS, 200, json=fixture("facts_current_page2"), query={"cursor": FC[2]})
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def facts_reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == FACTS_PATH]


def row(html: str, belief_id: str) -> str:
    found = re.search(rf'<tr id="b-{belief_id}" class="belief-row">(.*?)</tr>', html, re.S)
    assert found is not None, belief_id
    return found.group(1)


def canon_cell(html: str, belief_id: str) -> str:
    found = re.search(r'<td class="canon">(.*?)</td>', row(html, belief_id), re.S)
    assert found is not None, belief_id
    return found.group(1)


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def notices(html: str) -> list[str]:
    return re.findall(r'<p class="notice canon-notice">(.*?)</p>', html, re.S)


def summary(html: str) -> str | None:
    found = re.search(r'<p class="canon-summary">(.*?)</p>', html, re.S)
    return None if found is None else found.group(1)


def beliefs_of(character: str, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body = fixture("beliefs_canon_alice")
    body["character"] = character
    if rows is not None:
        body["beliefs"] = rows
    return body


def lineage(fact_id: str) -> str:
    return escape(
        f"/stories/{STORY}/inspect/canon/{fact_id}/lineage/?subject={KEY}&predicate={LOC}"
    )


def assert_no_verdicts(html: str) -> None:
    """**Complement:** no row whose comparison wasn't made shows a verdict label."""
    for belief_id in BC.values():
        cell = canon_cell(html, belief_id)
        assert not any(label in cell for label in LABELS), cell


def test_one_canon_read_series(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    paths = [r.url.path for r in engine.requests]
    assert paths.index(PREDICATES_PATH) < paths.index(FACTS_PATH)
    assert paths.count(PREDICATES_PATH) == 1
    calls = facts_reads(engine)
    assert len(calls) == 2
    for call in calls:
        params = dict(call.url.params)
        for name in (
            "subject",
            "predicate",
            "as_of_story_time",
            "as_of_record_time",
            "include_superseded",
        ):
            assert name not in params
        assert params["limit"] == "500"
        assert params["viewpoint"] == "omniscient"
    assert "cursor" not in dict(calls[0].url.params)
    assert dict(calls[1].url.params)["cursor"] == FC[2]
    study = canon_cell(html, BC[1])
    assert '<span class="canon-contradicts" title="Canon holds the other polarity' in study
    assert ">Contradicts canon</span>" in study
    assert '<span class="canon-agrees"' in canon_cell(html, BC[2])
    assert ">Canon agrees</span>" in canon_cell(html, BC[2])
    nested = canon_cell(html, BC[3])
    assert nested == (
        '<span class="muted canon-not-compared" title="Only first-order beliefs with a readable '
        'proposition are compared">Not compared</span>'
    )
    assert notices(html) == []


def test_bob_hall_row_agrees(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BELIEFS, 200, json=beliefs_of(BOB))
    html = page(operator_client, f"?character={BOB}")
    assert ">Canon agrees</span>" in canon_cell(html, BC[2])
    assert f'href="{lineage(FC[1])}"' in canon_cell(html, BC[2])


def test_unsettled_cell(operator_client: Client, engine: FakeEngine) -> None:
    rows = fixture("beliefs_canon_alice")["beliefs"][:1]
    rows[0]["proposition"]["predicate_id"] = HOLDS
    engine.respond("GET", BELIEFS, 200, json=beliefs_of(ALICE, rows))
    html = page(operator_client)
    assert canon_cell(html, BC[1]) == (
        '<span class="muted canon-unsettled" title="No current canon fact with this subject and '
        'predicate agrees or conflicts over an overlapping story-time">No canon record settles '
        "it</span>"
    )
    assert summary(html) is None


def test_as_of_makes_no_facts_call(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?as_of=1")
    assert facts_reads(engine) == []
    for belief_id in BC.values():
        cell = canon_cell(html, belief_id)
        assert ">Not compared for past turns</span>" in cell
        assert 'class="muted canon-not-compared"' in cell
        assert f'title="{PAST_NOTICE}"' in cell
    assert notices(html) == [PAST_NOTICE]
    assert summary(html) is None
    assert_no_verdicts(html)


def test_empty_page_no_facts_call(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BELIEFS, 200, json=beliefs_of(ALICE, []))
    html = page(operator_client)
    assert facts_reads(engine) == []
    assert notices(html) == []
    assert summary(html) is None


def test_all_rows_unparsed_no_facts_call(operator_client: Client, engine: FakeEngine) -> None:
    """No comparable row (here every row is ``nested``; an unparsed proposition counts the same,
    see the rule table): no facts call, each cell "Not compared"."""
    rows = fixture("beliefs_canon_alice")["beliefs"]
    for item in rows:
        item["kind"] = "nested"
    engine.respond("GET", BELIEFS, 200, json=beliefs_of(ALICE, rows))
    html = page(operator_client)
    assert facts_reads(engine) == []
    for belief_id in BC.values():
        assert ">Not compared</span>" in canon_cell(html, belief_id)
    assert_no_verdicts(html)
    assert summary(html) is None


def capped(engine: FakeEngine) -> None:
    for n in range(1, 5):
        body = fixture("facts_current_page1")
        body["facts"] = [body["facts"][0]] if n == 1 else []
        body["next_cursor"] = FC[n + 4]
        query = None if n == 1 else {"cursor": FC[n + 3]}
        if query is None:
            engine.respond("GET", FACTS, 200, json=body)
        else:
            engine.respond("GET", FACTS, 200, json=body, query=query)


def assert_not_compared(html: str, notice: str) -> None:
    for belief_id in BC.values():
        cell = canon_cell(html, belief_id)
        assert ">Not compared</span>" in cell
        assert 'class="muted canon-not-compared"' in cell
    assert f'title="{notice.replace("`", "")}"' in canon_cell(html, BC[1])
    assert_no_verdicts(html)
    assert summary(html) is None
    assert 'class="belief-row"' in html


def test_capped_not_compared(operator_client: Client, engine: FakeEngine) -> None:
    capped(engine)
    html = page(operator_client)
    assert len(facts_reads(engine)) == 4
    notice = "Not compared: this story has more than 2,000 current canon facts"
    assert notices(html) == [notice]
    assert_not_compared(html, notice)


def test_facts_failure_not_compared_beliefs_render(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    engine.respond("GET", FACTS, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client)
    notice = "Not compared: canon couldn&#x27;t be read"
    assert notices(html) == [notice]
    assert_not_compared(html, notice)
    assert 'class="engine-error"' not in html
    assert any("database_unavailable" in r.getMessage() for r in caplog.records)


def test_facts_unreachable_not_compared(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", FACTS, httpx.ConnectError("refused"))
    html = page(operator_client)
    assert notices(html) == ["Not compared: canon couldn&#x27;t be read"]


def test_revision_race_not_compared(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", FACTS, 200, json=fixture("facts_current_race"))
    html = page(operator_client)
    notice = "Not compared: the story changed while this page loaded — reload"
    assert notices(html) == [notice]
    assert_not_compared(html, notice)


def test_revision_race_on_page2(operator_client: Client, engine: FakeEngine) -> None:
    race = fixture("facts_current_page2")
    race["timeline_revision"] = 8
    engine.respond("GET", FACTS, 200, json=race, query={"cursor": FC[2]})
    html = page(operator_client)
    assert notices(html) == ["Not compared: the story changed while this page loaded — reload"]


def test_facts_module_off_not_compared_not_greyed(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    engine.respond("GET", FACTS, 409, json=error_json("module_off", "lite", module="canon"))
    html = page(operator_client)
    notice = "Not compared: this story keeps no canon (`pipeline` is not structured)"
    assert notices(html) == [
        "Not compared: this story keeps no canon (<code>pipeline</code> is not structured)"
    ]
    assert_not_compared(html, notice)
    assert '<div class="panel unavailable">' not in html
    assert not any("module_off" in r.getMessage() for r in caplog.records)


def test_facts_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", FACTS, 503, json=error_json("erasure_pending", "held"))
    response = operator_client.get(URL)
    assert response.status_code == 503
    assert 'class="belief-row"' not in response.content.decode()


def test_registry_failure_partial(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    rows = fixture("beliefs_canon_alice")["beliefs"]
    lone = copy.deepcopy(rows[0])
    lone["belief_id"] = P + "BC04"
    lone["proposition"]["subject_id"] = P + "EN09"  # no canon fact about it
    engine.respond("GET", BELIEFS, 200, json=beliefs_of(ALICE, [*rows, lone]))
    html = page(operator_client)
    assert notices(html) == [REGISTRY_NOTICE]
    study = canon_cell(html, BC[1])
    assert ">Not compared (predicate registry unavailable)</span>" in study
    assert f'title="{REGISTRY_NOTICE}"' in study
    assert ">Canon agrees</span>" in canon_cell(html, BC[2])
    assert ">No canon record settles it</span>" in canon_cell(html, P + "BC04")
    assert summary(html) is None


def test_registry_failure_with_facts_problem(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    engine.respond("GET", FACTS, 200, json=fixture("facts_current_race"))
    html = page(operator_client)
    assert notices(html) == [
        "Not compared: the story changed while this page loaded — reload",
        REGISTRY_NOTICE,
    ]


def test_registry_failure_summary_still_shows(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    rows = fixture("beliefs_canon_alice")["beliefs"][1:2]
    rows[0]["proposition"]["polarity"] = "negated"
    engine.respond("GET", BELIEFS, 200, json=beliefs_of(ALICE, rows))
    html = page(operator_client)
    assert summary(html) == "1 of 1 belief on this page contradicts canon"


def test_notices_order(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?as_of=1")
    first_order = html.index('<p class="notice first-order">')
    assert first_order < html.index('<p class="notice canon-notice">')
    assert html.index('<p class="notice canon-notice">') < html.index('<table class="beliefs">')


def test_lineage_links(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    study = canon_cell(html, BC[1])
    assert f'<a class="canon-fact" href="{lineage(FC[1])}"><code title="{FC[1]}">' in study
    assert "more" not in study


def test_more_link_filters_canon_tab(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("facts_current_page1")
    hall = body["facts"][0]
    body["facts"] = []
    for n in range(1, 6):
        item = copy.deepcopy(hall)
        item["id"] = FC[n]
        body["facts"].append(item)
    body["next_cursor"] = None
    engine.respond("GET", FACTS, 200, json=body)
    html = page(operator_client)
    study = canon_cell(html, BC[1])
    assert study.count('class="canon-fact"') == 3
    more = escape(f"/stories/{STORY}/inspect/canon/?subject={KEY}&predicate={LOC}")
    assert f'<a class="canon-more" href="{more}">+2 more</a>' in study


def test_more_link_off_registry_predicate(operator_client: Client, engine: FakeEngine) -> None:
    """The "+N more" link shows for a predicate the registry doesn't hold too."""
    body = fixture("facts_current_page1")
    hall = body["facts"][0]
    hall["proposition"]["predicate_id"] = ABSENT
    body["facts"] = []
    for n in range(1, 5):
        item = copy.deepcopy(hall)
        item["id"] = FC[n]
        body["facts"].append(item)
    body["next_cursor"] = None
    engine.respond("GET", FACTS, 200, json=body)
    rows = fixture("beliefs_canon_alice")["beliefs"][1:2]
    rows[0]["proposition"]["predicate_id"] = ABSENT
    engine.respond("GET", BELIEFS, 200, json=beliefs_of(ALICE, rows))
    html = page(operator_client)
    more = escape(f"/stories/{STORY}/inspect/canon/?subject={KEY}&predicate={ABSENT}")
    assert f'<a class="canon-more" href="{more}">+1 more</a>' in canon_cell(html, BC[2])


def test_summary_counts_on_this_page(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert summary(html) == "1 of 3 beliefs on this page contradicts canon"
    rows = fixture("beliefs_canon_alice")["beliefs"][:1]
    engine.respond("GET", BELIEFS, 200, json=beliefs_of(ALICE, rows))
    html = page(operator_client)
    assert summary(html) == "1 of 1 belief on this page contradicts canon"
    both = fixture("beliefs_canon_alice")["beliefs"]
    both[1]["proposition"]["object_id"] = STUDY
    engine.respond("GET", BELIEFS, 200, json=beliefs_of(ALICE, both))
    html = page(operator_client)
    assert summary(html) == "2 of 3 beliefs on this page contradict canon"
    assert html.index('<p class="canon-summary">') < html.index('<table class="beliefs">')


def test_canon_column_header(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    head = re.search(r'<table class="beliefs">\s*<thead><tr>(.*?)</tr></thead>', html, re.S)
    assert head is not None
    headers = re.findall(r"<th>(.*?)</th>", head.group(1))
    assert headers[headers.index("About story-time") + 1] == "Canon"


def test_no_verified_in_new_templates() -> None:
    for name in ("_belief_canon.html", "beliefs.html", "_belief_row.html"):
        assert "verified" not in (TEMPLATES / name).read_text().lower()


def test_canon_styles_not_colour() -> None:
    base = (TEMPLATES.parents[1] / "base.html").read_text()
    rules = re.findall(r"\.canon-(?:contradicts|agrees) \{([^}]*)\}", base)
    assert rules == [" font-weight: bold; ", " font-weight: normal; "]
    assert "color" not in "".join(rules)

"""What a retrieval placed (#152): the Retrieval tab's Canon, Profiles, Context and Run facts
sections and the degradation lines, all from the one ``GET /stories/{id}/retrieve`` read.

Runs against the real urlconf on the schema-validating fake Engine with #141's fixture
``tests/fixtures/inspect/retrieval.json``, extended in place: 3 canon facts (``…F01`` an entity
object, ``…F02`` a set, ``…F03`` a negated ``3 kg`` literal), Tobin's profile at turn 4 (a
relationship to the tavern, a placement within Riverton, a brass key), 3 context items in tiers 1
and 3, 2 dropped (``budget`` and the unknown ``spill_over``), ``degradation:
["embedding_pending"]`` and 1 rehydrated id. The retrieval's turn is 5 (``position [6, 0]``)."""

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from console.engine.models import RetrievalBody
from console.retrieval import Names, literal_text, placed, timing_rows
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import STORY
from tests.inspect.test_retrieval import (
    MARA,
    QUESTION,
    RETRIEVE,
    TAVERN,
    TOBIN,
    URL,
    ask,
    engine,
    fixture,
    page,
    retrieve_calls,
    text,
)

__all__ = ["engine"]  # the fake Engine fixture, shared with #141's tests

pytestmark = pytest.mark.django_db

P = "01JB2Z8Y5Q3W6K9M1N4P7R0"
F01, F02, F03 = P + "F01", P + "F02", P + "F03"
PR1, PR2, PR3 = P + "PR1", P + "PR2", P + "PR3"
KEY, TOWN = P + "KY1", P + "TWN"
M01, M03, M04 = P + "M01", P + "M03", P + "M04"
ENTITY = "/stories/{}/inspect/entities/{}/"


def section(html: str, name: str) -> str:
    """A top-level section's markup, up to the next one (or the page's "More"); profiles nest
    #58's own sections, so this cuts at line starts."""
    start = html.index(f'<section class="retrieval-{name}">')
    ends = [
        html.find(m, start + 1)
        for m in ('\n<section class="retrieval-', '\n<details class="more">')
    ]
    return html[start : min(e for e in ends if e != -1)]


def canon_rows(html: str) -> dict[str, str]:
    """Each canon row's markup by its fact id."""
    found = re.findall(r'<tr class="canon-row">(.*?)</tr>', section(html, "canon"), re.S)
    return {first(r'<code title="([^"]+)">', row): row for row in found}


def first(pattern: str, markup: str) -> str:
    """The first group of ``pattern``'s first match in ``markup`` (which must match)."""
    found = re.search(pattern, markup, re.S)
    assert found is not None, pattern
    return found.group(1)


def cell(row: str, name: str) -> str:
    found = re.search(rf'<td class="{name}">(.*?)</td>', row, re.S)
    assert found is not None, name
    return found.group(1)


def query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url.replace("&amp;", "&")).query)


def with_body(engine: FakeEngine, **changes: Any) -> None:
    body = fixture()
    body.update(changes)
    engine.respond("GET", RETRIEVE, 200, json=body)


# --- Canon --------------------------------------------------------------------------------------


def test_canon_rows_in_engine_order_with_each_object_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client)
    rows = canon_rows(html)
    assert list(rows) == [F01, F02, F03]
    assert text(cell(rows[F01], "object")) == "The tavern"
    assert text(cell(rows[F02], "object")) == "Mara (player) Tobin"
    assert cell(rows[F02], "object").count('<span class="chip">') == 2
    assert '<span class="literal" title="quantity">3 kg</span>' in cell(rows[F03], "object")


def test_canon_names_come_from_the_profile_and_link_the_entity_at_the_retrieval_turn(
    operator_client: Client, engine: FakeEngine
) -> None:
    rows = canon_rows(ask(operator_client))
    subject = cell(rows[F03], "subject")  # the brass key: named only by the profile's inventory
    assert ">Brass key</a>" in subject
    href = first(r'href="([^"]+)"', subject)
    assert urlsplit(href).path == ENTITY.format(STORY, KEY)
    assert query(href) == {"as_of": ["5"], "viewpoint": ["omniscient"]}
    tavern = cell(rows[F01], "object")
    assert f'title="{TAVERN}">The tavern</a>' in tavern
    assert "as_of=5&amp;viewpoint=omniscient" in tavern


def test_canon_predicate_named_by_a_profile_relationship_else_a_short_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    rows = canon_rows(ask(operator_client))
    predicates = reverse("inspect:predicates", args=[STORY])
    assert f'<a href="{predicates}?layer=all#p-{PR1}" title="{PR1}">located in</a>' in cell(
        rows[F01], "predicate"
    )
    assert f'<code title="{PR2}">…{PR2[-8:]}</code>' in cell(rows[F02], "predicate")


def test_canon_fact_links_its_lineage_and_polarity_is_humanised(
    operator_client: Client, engine: FakeEngine
) -> None:
    rows = canon_rows(ask(operator_client))
    lineage = reverse("inspect:canon-lineage", args=[STORY, F01])
    assert f'href="{lineage}?subject={TOBIN}&amp;predicate={PR1}"' in cell(rows[F01], "fact-id")
    assert '<span title="negated">Negated</span>' in cell(rows[F03], "polarity")
    assert '<span title="affirmed">Affirmed</span>' in cell(rows[F01], "polarity")


def test_a_fact_with_no_object_renders_a_dash(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture()
    body["canon"][2]["literal"] = None
    engine.respond("GET", RETRIEVE, 200, json=body)
    assert cell(canon_rows(ask(operator_client))[F03], "object") == "—"


def test_no_canon_reads_none(operator_client: Client, engine: FakeEngine) -> None:
    with_body(engine, canon=[])
    canon = section(ask(operator_client), "canon")
    assert '<p class="none">None</p>' in canon
    assert "<table" not in canon


def test_a_name_from_the_cast_when_no_profile_carries_the_entity(
    operator_client: Client, engine: FakeEngine
) -> None:
    with_body(engine, profiles=[])
    rows = canon_rows(ask(operator_client))
    assert f'title="{TOBIN}">Tobin</a>' in cell(rows[F01], "subject")
    assert f'<code title="{KEY}">' in cell(rows[F03], "subject")  # no name anywhere: short id
    assert f'<code title="{PR1}">' in cell(rows[F01], "predicate")


@pytest.mark.parametrize(
    ("value", "shown", "title"),
    [
        ({"kind": "quantity", "value": 3, "unit": "kg"}, "3 kg", "quantity"),
        ({"kind": "string", "value": "red", "unit": None}, "red", "string"),
        ({"kind": "boolean", "value": True}, "true", "boolean"),
        ({"value": 3}, '{"value": 3}', None),
        ([1, "two"], '[1, "two"]', None),
    ],
)
def test_literal_text(value: Any, shown: str, title: str | None) -> None:
    found = literal_text(value)
    assert (found.text, found.title) == (shown, title)


# --- Profiles -----------------------------------------------------------------------------------


def test_the_profile_renders_through_the_entity_profile_partial(
    operator_client: Client, engine: FakeEngine
) -> None:
    profiles = section(ask(operator_client), "profiles")
    assert '<h3>Tobin (<span title="character">Character</span>)</h3>' in profiles
    assert '<table class="profile-relationships">' in profiles
    assert ">located in</a>" in profiles
    assert "Riverton" in profiles
    assert ">Brass key</a>" in profiles
    links = re.findall(r'<a class="entity" href="([^"]+)"', profiles)
    assert links
    assert all(query(href) == {"as_of": ["4"], "viewpoint": ["omniscient"]} for href in links)
    assert '<td class="source-fact">' in profiles  # an omniscient profile shows its source facts


def test_no_profiles_reads_none(operator_client: Client, engine: FakeEngine) -> None:
    with_body(engine, profiles=[])
    profiles = section(ask(operator_client), "profiles")
    assert '<p class="none">None</p>' in profiles
    assert "<table" not in profiles


# --- Context ------------------------------------------------------------------------------------


def tier(html: str, number: int) -> str:
    context = section(html, "context")
    found = re.search(
        rf'<h3 class="context-tier" id="tier-{number}">(.*?)</h3>(.*?)(?=<h3)', context, re.S
    )
    assert found is not None, number
    return found.group(1) + found.group(2)


def test_placed_items_sit_under_their_tier_headings(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client)
    assert tier(html, 1).startswith("1 · Ledger and pins")
    assert f'<code title="{M01}">' in tier(html, 1)
    assert '<span title="pin">Pin</span>' in tier(html, 1)
    assert "None" in tier(html, 2)
    assert "<table" not in tier(html, 2)
    third = tier(html, 3)
    assert third.startswith("3 · Profiles and canon")
    assert third.index(f'<code title="{TOBIN}">') < third.index(f'<code title="{F01}">')
    assert '<td class="number">1,200</td>' in third
    assert tier(html, 4).startswith("4 · Ranked memories")
    assert "Other tier" not in html


def test_context_text_is_in_a_closed_details_cut_at_80(
    operator_client: Client, engine: FakeEngine
) -> None:
    third = tier(ask(operator_client), 3)
    long = fixture()["context"]["items"][1]["text"]
    assert f"<details><summary>{long[:80]}…</summary>" in third
    assert "<details open" not in third
    assert "<summary>Tobin is located in the tavern.</summary>" in third


def test_context_id_links(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client, kind="semantic", tier="hot")
    href = first(r'<a class="context-id" href="([^"]+)">', tier(html, 1))
    assert urlsplit(href).path == URL
    assert query(href) == {"q": [QUESTION], "actor": [MARA], "memory": [M01]}  # no kind/tier
    third = tier(html, 3)
    assert (
        f'<a class="context-id" href="{reverse("inspect:canon-lineage", args=[STORY, F01])}'
        in third
    )
    assert f'<td class="id"><code title="{TOBIN}">' in third  # a profile id stays plain


def test_dropped_items_show_their_reason(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client)
    context = section(html, "context")
    dropped = first(r'<table class="context-dropped">(.*?)</table>', context)
    rows = re.findall(r'<tr class="dropped-item">(.*?)</tr>', dropped, re.S)
    assert len(rows) == 2
    assert '<span title="budget">over the remaining budget</span>' in rows[0]
    assert "<code>spill_over</code>" in rows[1]
    assert "<details>" not in dropped  # dropped items show no text
    placed_ids = set(
        re.findall(r'<tr class="context-item">.*?<code title="([^"]+)">', context, re.S)
    )
    assert placed_ids == {M01, TOBIN, F01}
    assert not placed_ids & {M03, M04}


def test_used_over_budget(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client)
    assert '<p class="context-used">Used 1,840 / 3,000 tokens</p>' in html
    body = fixture()
    body["context"]["used"] = 3100
    engine.respond("GET", RETRIEVE, 200, json=body)
    assert 'Used 3,100 / 3,000 tokens <strong class="over-budget">(over budget)</strong>' in ask(
        operator_client
    )


def test_an_unknown_tier_groups_after_four(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture()
    body["context"]["items"].append(
        {"tier": 7, "kind": "rumour", "item_id": "x1", "text": "Odd.", "tokens": 1}
    )
    engine.respond("GET", RETRIEVE, 200, json=body)
    html = ask(operator_client)
    context = section(html, "context")
    assert context.index("4 · Ranked memories") < context.index("Other tier 7")
    assert '<span title="rumour">Rumour</span>' in context


# --- Run facts and degradation ------------------------------------------------------------------


def test_timings_list_the_stages_then_total(operator_client: Client, engine: FakeEngine) -> None:
    run = section(ask(operator_client), "run")
    stages = re.findall(r'<th scope="row">([^<]+)</th><td class="number">([^<]+)</td>', run)
    assert stages == [
        ("resolve", "1.500"),
        ("occasion", "2.250"),
        ("vector", "4.000"),
        ("lexical", "1.000"),
        ("fuse", "0.500"),
        ("profiles", "1.250"),
        ("assemble", "0.750"),
        ("total", "1,234.568"),
    ]
    assert '<tr class="timing total">' in run


def test_timing_rows_place_unknown_stages_before_total_and_dash_missing_ones() -> None:
    rows = timing_rows({"total": 9.0, "beliefs": 2.0, "resolve": 1.0})
    assert [(r.stage, r.ms) for r in rows] == [
        ("resolve", "1.000"),
        ("occasion", "—"),
        ("vector", "—"),
        ("lexical", "—"),
        ("fuse", "—"),
        ("profiles", "—"),
        ("assemble", "—"),
        ("beliefs", "2.000"),
        ("total", "9.000"),
    ]


def test_embedding_pending_warns_at_the_top_with_the_run_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client)
    line = (
        '<p class="notice warn degradation" title="embedding_pending">'
        f'<a href="{reverse("models:reembed")}">Re-embedding — this story&#x27;s search is '
        "lexical-only until it finishes</a></p>"
    )
    assert line in html
    assert html.index(line) < html.index('<section class="retrieval-resolution">')


def test_other_degradations_are_worded_or_humanised(
    operator_client: Client, engine: FakeEngine
) -> None:
    with_body(engine, degradation=["embedding_unavailable", "cache_cold"])
    html = ask(operator_client)
    assert (
        '<p class="notice warn degradation" title="embedding_unavailable">'
        "Embedding unavailable — vector arm skipped</p>"
    ) in html
    assert '<p class="notice warn degradation" title="cache_cold">Cache cold</p>' in html


def test_run_facts(operator_client: Client, engine: FakeEngine) -> None:
    with_body(engine, skipped=["occasion", "alias", "mystery"], model_calls=0)
    run = section(ask(operator_client), "run")
    assert '<dd class="run-model-calls">0</dd>' in run
    assert (
        '<li><span title="occasion">Occasion</span> — '
        "no time window or event type in the question</li>" in run
    )
    assert '<li><span title="alias">Alias</span> — arm did not run</li>' in run
    assert '<li><span title="mystery">Mystery</span></li>' in run
    assert '<dd class="run-embedding-version"><code>text-embed-3@1</code></dd>' in run
    assert "Rehydrated for this read only; nothing was stored." in run


def test_empty_run_facts(operator_client: Client, engine: FakeEngine) -> None:
    with_body(engine, rehydrated=[], degradation=[], embedding_model_version=None)
    html = ask(operator_client)
    run = section(html, "run")
    assert '<dd class="run-rehydrated">None</dd>' in run
    assert '<dd class="run-degradation">None</dd>' in run
    assert '<dd class="run-skipped">None</dd>' in run
    assert '<dd class="run-embedding-version">—</dd>' in run
    assert "notice warn" not in html


def test_rehydrated_ids_link_back_with_the_same_question(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(
        operator_client,
        {
            "q": QUESTION,
            "as_of": "4",
            "actor": MARA,
            "include_quarantined": "on",
            "kind": "episodic",
            "tier": "hot",
            "memory": "M01",
            "explain": "5",
            "explain_variant": "2",
        },
    )
    run = section(html, "run")
    href = first(r'<a class="rehydrated-id" href="([^"]+)">', run)
    assert urlsplit(href).path == URL
    assert query(href) == {
        "q": [QUESTION],
        "as_of": ["4"],
        "actor": [MARA],
        "include_quarantined": ["on"],
        "explain": ["5"],
        "explain_variant": ["2"],
        "memory": [M03],
    }


# --- what never changes the sections ------------------------------------------------------------


SECTIONS = ("canon", "profiles", "context", "run")


def test_display_filters_never_narrow_the_sections(
    operator_client: Client, engine: FakeEngine
) -> None:
    plain = ask(operator_client)
    filtered = ask(operator_client, kind="reflection", tier="retired", memory="M03")
    for name in ("canon", "profiles"):
        assert section(filtered, name) == section(plain, name)
    for html in (plain, filtered):
        context = section(html, "context")
        assert context.count('<tr class="context-item">') == 3
        assert context.count('<tr class="dropped-item">') == 2
        assert section(html, "run").count('<th scope="row">') == 8


def test_episodic_skipped_renders_none_of_the_sections(
    operator_client: Client, engine: FakeEngine
) -> None:
    with_body(
        engine,
        memories=[],
        episodes=[],
        assessments=[],
        participants=[],
        profiles=[],
        canon=[],
        context={"budget": 3000, "used": 0, "items": [], "dropped": []},
        rehydrated=[],
        degradation=[],
        skipped=["episodic"],
    )
    html = ask(operator_client)
    for name in SECTIONS:
        assert f'<section class="retrieval-{name}">' not in html
    assert '<p class="notice unavailable">' in html


def test_no_section_json_remains_in_more(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client)
    mores = re.findall(r'<details class="more"><summary>More</summary><pre>(.*?)</pre>', html, re.S)
    assert len(mores) == 1
    for name in ("canon", "profiles", "context", "timings", "rehydrated", "degradation"):
        assert f"&quot;{name}&quot;" not in mores[0]


def test_one_retrieve_and_no_other_engine_read(operator_client: Client, engine: FakeEngine) -> None:
    ask(operator_client)
    assert len(retrieve_calls(engine)) == 1
    paths = sorted({r.url.path for r in engine.requests})
    assert paths == sorted(
        {f"/stories/{STORY}", f"/stories/{STORY}/effective-config", f"/stories/{STORY}/retrieve"}
    )


# --- an entry that fails to parse ---------------------------------------------------------------


def test_an_entry_that_fails_to_parse_is_left_out_and_kept_raw() -> None:
    body = fixture()
    body["canon"][1]["polarity"] = 7
    body["context"]["dropped"][0]["tokens"] = "many"
    parsed = RetrievalBody.from_json(body)
    assert parsed is not None
    assert [f.fact_id for f in parsed.canon] == [F01, F03]
    assert [d.item_id for d in parsed.context.dropped] == [M04]
    assert parsed.unparsed["canon"] == body["canon"]
    assert set(parsed.unparsed) == {"canon", "context.dropped"}
    view = placed(STORY, parsed, Names(None), {})
    assert view.canon_more == {"canon": body["canon"]}
    assert set(view.context_more) == {"context.dropped"}
    assert view.profiles_more == {}
    clean = RetrievalBody.from_json(fixture())
    assert clean is not None
    assert clean.unparsed == {}

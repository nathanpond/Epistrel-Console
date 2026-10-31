"""The Knowledge tab's Beliefs section (#190): what one character believes.

Runs against the real urlconf on the schema-validating fake Engine, under #188's Structured v2
configuration and the shared Alice/Bob/Pat cast (``story_cast.json``; Pat is the player).
``beliefs_page1.json`` is Alice's first page (revision 7, ``next_after`` ``PP11``):

- ``BE01``: suspected, no scalar, observation, ``located_at(Bob, …EN03)``, since turn 1 · beat 2;
- ``BE02``: believed 0.7, testimony, since turn 1 · v1 · beat 2 (step 40000), held to turn 3 ·
  beat 4, valid [540, 600);
- ``BE03``: inference, a negated ``holds(…EN01, Alice)``, since the slot turn 2⁺¹ · beat 5;
- ``BE04`` to ``BE09``: discovery, correction, forgetting, revision, author (after the turn),
  resolution (turn 0 · start);
- ``BE10``: no provenance, both valid bounds open; ``BE11``: the unknown ``hearsay``, on a
  predicate absent from the registry;
- identities: ``…EN01 = …EN03`` (canon, ``FA01``) and ``Bob = …EN02`` (belief ``BE01``).

``beliefs_page2.json`` (keyed on ``after=PP11``, C14) holds ``BE12`` and no ``next_after``;
``beliefs_empty.json`` no rows."""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.template import Template
from django.template.loader import render_to_string
from django.test import Client, override_settings
from django.utils.html import escape

from console.belief_canon import UNSETTLED, CanonMark
from console.beliefs import belief_rows
from console.engine.client import EngineClient
from console.engine.models import Beliefs
from console.engine_access import engine_client_for
from console.forms.inspect import AS_OF_INVALID, PROPOSITION_INVALID, TURN_TOO_HIGH
from console.inspect import history_turn_url
from console.paging import CAST_UNAVAILABLE, CHARACTER_INVALID, CURSOR_DROPPED
from console.views import inspect as views
from console.wording import UNKNOWN_CHARACTER
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import (
    ALICE,
    BOB,
    EFFECTIVE,
    FIXTURES,
    PAT,
    STORY,
    STORY_PATH,
    story_cast,
    structured_v2,
)
from tests.inspect.test_knowledge_shell import partitions_off
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/inspect/knowledge/beliefs/"
BELIEFS = "/stories/{story_id}/inspect/beliefs"
BELIEFS_PATH = f"/stories/{STORY}/inspect/beliefs"
PREDICATES = "/stories/{story_id}/predicates"
PREDICATES_PATH = f"/stories/{STORY}/predicates"
FACTS = "/admin/stories/{story_id}/facts"
NO_FACTS: dict[str, Any] = {"facts": [], "next_cursor": None, "timeline_revision": 7}
P = "0000000000000000000000"
BE = {n: P + f"BE{n:02d}" for n in range(1, 13)}
PP11 = P + "PP11"
KEY, STUDY, ROOM, FACT = P + "EN01", P + "EN02", P + "EN03", P + "FA01"
OUTSIDER = P + "ZZ99"
PROPOSITION = P + "PP01"
LOC = "01JB2Z8Y5Q3W6K9M1N4P7R0PR1"
ABSENT = P + "PZ99"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
FIRST_ORDER = "Only first-order beliefs held at suspected or above are listed."
PLAYER_NOTICE = "The player&#x27;s character has no partition — the Engine keeps no beliefs for it"
TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "console" / "inspect"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return copy.deepcopy(body)


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
    fake.respond("GET", BELIEFS, 200, json=fixture("beliefs_page1"))
    fake.respond("GET", BELIEFS, 200, json=fixture("beliefs_page2"), query={"after": PP11})
    fake.respond("GET", FACTS, 200, json=NO_FACTS)  # #191's canon read: nothing to compare
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == BELIEFS_PATH]


def registry_reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == PREDICATES_PATH]


def sent(engine: FakeEngine) -> dict[str, str]:
    (call,) = reads(engine)
    return dict(call.url.params)


def row(html: str, belief_id: str) -> str:
    found = re.search(rf'<tr id="b-{belief_id}" class="belief-row">(.*?)</tr>', html, re.S)
    assert found is not None, belief_id
    return found.group(1)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def field(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def options(html: str) -> list[tuple[str, str]]:
    select = field(html, "character")
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', select)


def selected(html: str) -> str:
    found = re.search(r'<option value="([^"]*)"[^>]*selected', field(html, "character"))
    assert found is not None
    return found.group(1)


def header(html: str) -> str | None:
    found = re.search(r'<h3 class="beliefs-header">(.*?)</h3>', html, re.S)
    return None if found is None else found.group(1)


def answer(engine: FakeEngine, status: int, body: dict[str, Any]) -> None:
    engine.respond("GET", BELIEFS, status, json=body)


def empty_for(character: str = ALICE) -> dict[str, Any]:
    body = fixture("beliefs_empty")
    body["character"] = character
    return body


def link(story_turn: int, variant: int | None = None) -> str:
    return escape(history_turn_url(STORY, story_turn, variant))


# --- the route and its reads ---------------------------------------------------------------------


def test_reads_in_order_and_sub_nav(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        BELIEFS_PATH,
        PREDICATES_PATH,
        f"/admin/stories/{STORY}/facts",  # #191's canon read
    ]
    assert sent(engine) == {"viewpoint": "omniscient", "character": ALICE, "limit": "100"}
    assert f'<a class="active" aria-current="page" href="{URL}">Beliefs</a>' in html
    assert "<h2>Beliefs</h2>" in html


def test_knowledge_landing_redirects_to_beliefs(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.get(f"/stories/{STORY}/inspect/knowledge/")
    assert response.status_code == 302
    assert response["Location"] == URL


def test_greyed_tab_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=partitions_off())
    html = page(operator_client)
    assert '<div class="panel unavailable">' in html
    assert reads(engine) == []
    assert registry_reads(engine) == []


# --- rows ----------------------------------------------------------------------------------------


def test_rows_render_each_column(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    first = row(html, BE[1])
    assert cell(first, "confidence") == '<span title="suspected">Suspected</span>'
    assert cell(first, "how") == '<span title="observation">Observation</span>'
    assert cell(first, "since") == f'<a href="{link(1)}">turn 1 · beat 2</a>'
    assert cell(first, "until") == "now"
    assert cell(first, "about") == "[0, …) min"
    assert "Bob" in cell(first, "proposition")

    testimony = row(html, BE[2])
    assert cell(testimony, "confidence") == '<span title="believed">Believed</span> · 0.700'
    assert cell(testimony, "how") == (
        '<span class="testimony" title="testimony">Testimony — heard from another character</span>'
    )
    assert cell(testimony, "since") == f'<a href="{link(1, 1)}">turn 1 · v1 · beat 2</a>'
    assert cell(testimony, "until") == f'<a href="{link(3)}">turn 3 · beat 4</a>'
    assert cell(testimony, "about") == "[540, 600) min"

    negated = row(html, BE[3])
    assert '<strong class="polarity">NOT</strong>' in cell(negated, "proposition")
    assert cell(negated, "since") == f'<a href="{link(2)}">turn 2⁺¹ · beat 5</a>'
    assert '<strong class="polarity">NOT</strong>' not in cell(first, "proposition")

    for n, word in (
        (4, "Discovery"),
        (5, "Correction"),
        (6, "Forgetting"),
        (7, "Revision"),
        (8, "Author"),
        (9, "Resolution"),
    ):
        assert cell(row(html, BE[n]), "how") == f'<span title="{word.lower()}">{word}</span>'
    assert text(cell(row(html, BE[8]), "since")) == "turn 2 · after the turn"
    assert text(cell(row(html, BE[9]), "since")) == "turn 0 · start"
    assert cell(row(html, BE[10]), "how") == "—"
    assert cell(row(html, BE[10]), "about") == "[…, …) min"
    assert cell(row(html, BE[11]), "how") == '<span title="hearsay">Hearsay</span>'


def more_items(row_html: str) -> list[tuple[str, str]]:
    return [
        (term, text(value))
        for term, value in re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", cell(row_html, "more"), re.S)
    ]


def test_more_lists_the_unnamed_fields(operator_client: Client, engine: FakeEngine) -> None:
    testimony = row(page(operator_client), BE[2])
    assert more_items(testimony) == [
        ("Belief id", BE[2]),
        ("Proposition id", P + "PP02"),
        ("Kind", "First order"),
        ("Held from", "turn 1 · v1 · beat 2"),
        ("Step", "40000"),
        ("Tx from", "2026-10-30 09:20:03 UTC"),
        ("Proposition valid-time", "interval [0, …) min"),
        ("Polarity", "Affirmed"),
    ]
    assert '<time datetime="2026-10-30T09:20:03.118204+00:00"' in cell(testimony, "more")
    assert '<span title="first_order">First order</span>' in cell(testimony, "more")


def test_unparsable_proposition_shows_raw_in_more() -> None:
    """C2: a proposition the Console can't read (the Engine's schema allows none, C15, so this
    is a view-model test) shows "—" in its column and raw in More; the row still renders."""
    raw = fixture("beliefs_page1")
    raw["beliefs"][0]["proposition"] = {"subject_id": KEY, "predicate_id": LOC, "valid_time": 5}
    body = Beliefs.from_json(raw)
    assert body is not None
    (first,) = belief_rows(STORY, body.beliefs[:1])
    assert first.proposition is None
    html = render_to_string("console/inspect/_belief_row.html", {"row": first, "story_id": STORY})
    assert cell(html, "proposition") == "—"
    assert '<pre class="raw-proposition">' in cell(html, "more")
    assert ("Proposition valid-time", "—") in more_items(html)


def test_column_order(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    head = re.search(r'<table class="beliefs">\s*<thead><tr>(.*?)</tr></thead>', html, re.S)
    assert head is not None
    assert re.findall(r"<th>(.*?)</th>", head.group(1)) == [
        "Proposition",
        "Confidence",
        "How",
        "Holds since",
        "Until",
        "About story-time",
        "Canon",  # #191
        "Actions",  # #194's Change…
        "More",
    ]
    assert html.count('<td class="actions">') == html.count('class="belief-row"')


def test_column_hooks_render_canon_and_actions() -> None:
    """#191's ``marks`` adds the Canon cell, #194's ``actions`` the Actions cell, before More."""
    body = Beliefs.from_json(fixture("beliefs_page1"))
    assert body is not None
    marks = {BE[1]: CanonMark(UNSETTLED)}
    (first,) = belief_rows(STORY, body.beliefs[:1], marks)
    context = {"row": first, "marks": marks, "actions": Template("ACT"), "story_id": STORY}
    html = render_to_string("console/inspect/_belief_row.html", context)
    assert "No canon record settles it</span></td>" in html
    assert '<td class="actions">ACT</td>' in html
    assert html.index('class="canon"') < html.index('class="actions"') < html.index('class="more"')
    (unmarked,) = belief_rows(STORY, body.beliefs[1:2], {})
    html = render_to_string(
        "console/inspect/_belief_row.html", {"row": unmarked, "marks": {}, "story_id": STORY}
    )
    assert '<td class="canon">—</td>' in html


def test_registry_failure_shows_short_ids(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client)
    proposition = cell(row(html, BE[1]), "proposition")
    assert f'<code title="{LOC}">…4P7R0PR1</code>' in proposition
    assert "located_at" not in proposition


def test_registry_names_predicates_and_absent_one_is_short(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    assert "located_at" in cell(row(html, BE[1]), "proposition")
    assert f'<code title="{ABSENT}">' in cell(row(html, BE[11]), "proposition")


@pytest.mark.parametrize("body", ["empty", "error"])
def test_registry_read_skipped_on_empty_or_error(
    operator_client: Client, engine: FakeEngine, body: str
) -> None:
    if body == "empty":
        answer(engine, 200, empty_for())
    else:
        answer(engine, 503, error_json("database_unavailable", "down"))
    page(operator_client)
    assert len(reads(engine)) == 1
    assert registry_reads(engine) == []


# --- the character ------------------------------------------------------------------------------


def test_default_character_is_first_non_player(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert sent(engine)["character"] == ALICE
    assert selected(html) == ALICE
    assert header(html) == "What Alice believes · now · revision 7"


def test_player_is_not_offered(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert options(html) == [(ALICE, "Alice"), (BOB, "Bob")]
    assert PAT not in field(html, "character")
    html = page(operator_client, f"?character={BOB}")
    assert PAT not in field(html, "character")
    assert selected(html) == BOB


def test_player_in_url_shows_no_partition_notice(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, 200, empty_for(PAT))
    html = page(operator_client, f"?character={PAT}")
    assert sent(engine)["character"] == PAT
    assert options(html)[-1] == (PAT, "Pat (player)")
    assert selected(html) == PAT
    assert f'<p class="notice player-notice">{PLAYER_NOTICE}</p>' in html
    assert "holds no beliefs" not in html
    assert '<section class="identities">' not in html
    assert FIRST_ORDER in html
    assert header(html) == "What Pat (player) believes · now · revision 7"


def test_player_notice_wins_over_filters(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, empty_for(PAT))
    html = page(operator_client, f"?character={PAT}&proposition_id={PROPOSITION}&as_of=2")
    assert PLAYER_NOTICE in html
    assert "that proposition" not in html


def test_unknown_url_character_sent_and_mapped(operator_client: Client, engine: FakeEngine) -> None:
    answer(
        engine,
        422,
        error_json(
            "validation_error",
            "character is one of the story's characters",
            field="query.character",
            reason="unknown_character",
        ),
    )
    html = page(operator_client, f"?character={OUTSIDER}")
    assert sent(engine)["character"] == OUTSIDER
    assert options(html)[-1] == (OUTSIDER, "…0000ZZ99 (not in the cast)")
    assert escape(UNKNOWN_CHARACTER) in field(html, "character")
    assert header(html) == "What …0000ZZ99 believes · now"


def test_unknown_character_beside_field(operator_client: Client, engine: FakeEngine) -> None:
    answer(
        engine,
        422,
        error_json("validation_error", "nope", field="query.character", reason="unknown_character"),
    )
    html = page(operator_client)
    assert escape(UNKNOWN_CHARACTER) in field(html, "character")
    assert FIRST_ORDER not in html
    assert '<div class="errors"' not in html


def test_lowercase_cast_id_selects_cast_option(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?character=%20{BOB.lower()}%20")
    assert sent(engine)["character"] == BOB
    assert selected(html) == BOB
    assert options(html) == [(ALICE, "Alice"), (BOB, "Bob")]


def test_malformed_url_character_field_error_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, "?character=not-an-id")
    assert escape(CHARACTER_INVALID) in field(html, "character")
    assert reads(engine) == []
    assert header(html) is None


def test_no_modeled_characters(operator_client: Client, engine: FakeEngine) -> None:
    story = story_cast()
    story["characters"] = [c for c in story["characters"] if c["is_player"]]
    engine.respond("GET", STORY_PATH, 200, json=story)
    html = page(operator_client)
    assert "This story has no modeled characters" in html
    assert reads(engine) == []
    assert header(html) is None
    assert options(html) == []
    assert '<form method="get"' in html


def test_story_read_failed_free_text(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client)
    assert "Enter a character id to read its beliefs" in html
    assert reads(engine) == []
    assert '<input type="text" name="character"' in field(html, "character")
    assert CAST_UNAVAILABLE in html
    html = page(operator_client, f"?character={ALICE.lower()}")
    assert sent(engine)["character"] == ALICE
    assert header(html) == "What …0000CA01 believes · now · revision 7"


# --- the filters --------------------------------------------------------------------------------


def test_filters_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = page(
        operator_client, f"?character={ALICE}&as_of=0&proposition_id=%20{PROPOSITION.lower()}"
    )
    assert sent(engine) == {
        "viewpoint": "omniscient",
        "character": ALICE,
        "limit": "100",
        "as_of": "0",
        "proposition_id": PROPOSITION,
    }
    assert header(html) == "What Alice believes · as of turn 0 · revision 7"


@pytest.mark.parametrize(
    ("query", "name", "error", "story_ok"),
    [
        ("as_of=-1", "as_of", AS_OF_INVALID, True),
        ("as_of=%2B5", "as_of", AS_OF_INVALID, True),
        ("as_of=5.0", "as_of", AS_OF_INVALID, True),
        ("as_of=2147483648", "as_of", TURN_TOO_HIGH, True),
        ("proposition_id=nope", "proposition_id", PROPOSITION_INVALID, True),
        ("character=nope", "character", CHARACTER_INVALID, False),
    ],
)
def test_invalid_filters_make_no_call(
    operator_client: Client,
    engine: FakeEngine,
    query: str,
    name: str,
    error: str,
    story_ok: bool,
) -> None:
    if not story_ok:
        engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, f"?{query}")
    assert escape(error) in field(html, name)
    assert reads(engine) == []
    assert FIRST_ORDER not in html


def test_invalid_as_of_drops_the_time_part(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?as_of=x")
    assert header(html) == "What Alice believes"


def test_as_of_widget_has_no_bounds(operator_client: Client, engine: FakeEngine) -> None:
    widget = field(page(operator_client), "as_of")
    assert 'type="number"' in widget
    assert 'step="1"' in widget
    assert "min=" not in widget
    assert "max=" not in widget
    assert "Blank for now" in widget
    assert "Optional — keep one proposition" in field(page(operator_client), "proposition_id")


# --- paging --------------------------------------------------------------------------------------


def hrefs(html: str, cls: str) -> list[str]:
    return [
        h.replace("&amp;", "&") for h in re.findall(rf'<a class="{cls}" href="\?([^"]*)"', html)
    ]


def test_next_link_carries_defaulted_character(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    (following,) = hrefs(html, "next-page")
    assert dict(httpx.QueryParams(following)) == {"character": ALICE, "after": PP11}
    assert hrefs(html, "first-page") == []


def test_paging_links(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?as_of=3")
    (following,) = hrefs(html, "next-page")
    assert dict(httpx.QueryParams(following)) == {"as_of": "3", "character": ALICE, "after": PP11}
    html = page(operator_client, f"?{following}")
    assert dict(reads(engine)[-1].url.params)["after"] == PP11
    assert hrefs(html, "next-page") == []
    (first,) = hrefs(html, "first-page")
    assert dict(httpx.QueryParams(first)) == {"as_of": "3", "character": ALICE}
    assert row(html, BE[12])
    form = re.search(r'<form method="get".*?</form>', html, re.S)
    assert form is not None
    assert "after" not in form.group(0)


def test_bad_cursor_dropped_not_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?after=bad-cursor&as_of=x")
    assert f'<p class="notice cursor-dropped">{CURSOR_DROPPED}</p>' in html
    assert escape(AS_OF_INVALID) in field(html, "as_of")
    html = page(operator_client, "?after=bad-cursor")
    assert CURSOR_DROPPED in html
    assert "after" not in sent(engine)
    assert all("bad-cursor" not in str(r.url) for r in engine.requests)
    assert row(html, BE[1])


def test_after_422_above_filters_with_first_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        BELIEFS,
        422,
        json=error_json("validation_error", "after is past the end", field="query.after"),
        query={"after": PP11},
    )
    html = page(operator_client, f"?after={PP11}")
    errors = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    assert errors is not None
    assert "after is past the end" in errors.group(1)
    (first,) = hrefs(errors.group(1), "first-page")
    assert dict(httpx.QueryParams(first)) == {"character": ALICE}


@pytest.mark.parametrize(
    ("engine_field", "name"),
    [("query.as_of", "as_of"), ("query.proposition_id", "proposition_id")],
)
def test_other_422_beside_its_field(
    operator_client: Client, engine: FakeEngine, engine_field: str, name: str
) -> None:
    answer(engine, 422, error_json("validation_error", "the Engine says no", field=engine_field))
    html = page(operator_client)
    assert "the Engine says no" in field(html, name)


def test_422_without_field_above_filters(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 422, error_json("validation_error", "the Engine says no"))
    html = page(operator_client)
    errors = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    assert errors is not None
    assert "the Engine says no" in errors.group(1)
    assert "First page" not in errors.group(1)


# --- counts, notices, empty states ---------------------------------------------------------------


def count(html: str) -> str:
    found = re.search(r'<p class="beliefs-count">(.*?)</p>', html)
    assert found is not None
    return found.group(1)


def test_count_label(operator_client: Client, engine: FakeEngine) -> None:
    assert count(page(operator_client)) == "11 beliefs on this page"
    assert count(page(operator_client, "?as_of=4")) == "11 beliefs on this page, matching filters"
    assert count(page(operator_client, f"?after={PP11}")) == "1 belief on this page"
    answer(engine, 200, empty_for())
    assert count(page(operator_client)) == "0 beliefs"
    assert count(page(operator_client, f"?proposition_id={PROPOSITION}")) == (
        "0 beliefs matching filters"
    )


def test_first_order_notice_shown(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert f'<p class="notice first-order">{FIRST_ORDER}</p>' in html
    assert html.index("first-order") < html.index('<table class="beliefs">')
    answer(engine, 200, empty_for())
    assert FIRST_ORDER in page(operator_client)
    # No "nested" column, filter or row: the word appears only in #189's shell notice.
    body = html.split("Nested beliefs aren't readable", 1)[1].lower()
    assert "nested" not in body


def test_no_verified_in_templates() -> None:
    for name in ("beliefs.html", "_belief_row.html"):
        assert "verified" not in (TEMPLATES / name).read_text().lower()


@pytest.mark.parametrize(
    ("query", "line"),
    [
        ("", "Alice holds no beliefs at suspected or above"),
        ("?as_of=2", "Alice holds no beliefs at suspected or above as of turn 2"),
        (f"?proposition_id={PROPOSITION}", "Alice holds no current belief in that proposition"),
        (
            f"?proposition_id={PROPOSITION}&as_of=2",
            "Alice held no belief in that proposition as of turn 2",
        ),
        (
            f"?proposition_id={PROPOSITION}&after={PP11}",
            "No beliefs on this page — the partition may have changed since the last page.",
        ),
    ],
)
def test_empty_states(operator_client: Client, engine: FakeEngine, query: str, line: str) -> None:
    answer(engine, 200, empty_for())
    engine.respond("GET", BELIEFS, 200, json=empty_for(), query={"after": PP11})
    html = page(operator_client, query)
    assert f'<p class="none beliefs-none">{escape(line)}</p>' in html


# --- identities ----------------------------------------------------------------------------------


def identities(html: str) -> str:
    found = re.search(r'<section class="identities">(.*?)</section>', html, re.S)
    assert found is not None
    return found.group(1)


def test_identities_listed(operator_client: Client, engine: FakeEngine) -> None:
    section = identities(page(operator_client))
    assert "<h3>Identities</h3>" in section
    items = re.findall(r'<li class="identity">(.*?)</li>', section, re.S)
    assert len(items) == 2
    lineage = f"/stories/{STORY}/inspect/canon/{FACT}/lineage/"
    assert text(items[0]) == f"…0000EN01 = …0000EN03 · Canon fact {FACT}"
    assert f'<a href="{lineage}"><code>{FACT}</code></a>' in items[0]
    assert text(items[1]) == f"Bob = …0000EN02 · Own belief {BE[1]}"
    assert f'<a href="#b-{BE[1]}"><code>{BE[1]}</code></a>' in items[1]


def test_own_belief_off_the_page_is_unlinked(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("beliefs_page1")
    body["beliefs"] = body["beliefs"][1:]
    answer(engine, 200, body)
    section = identities(page(operator_client))
    assert f"<code>{BE[1]}</code>" in section
    assert f'href="#b-{BE[1]}"' not in section


def test_identities_none_and_on_empty_page(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, empty_for())
    section = identities(page(operator_client))
    assert '<p class="none identities-none">None</p>' in section


def test_identities_first_page_only(operator_client: Client, engine: FakeEngine) -> None:
    section = identities(page(operator_client, f"?after={PP11}"))
    assert "Listed on the first page" in section
    assert '<li class="identity">' not in section


def test_identities_heading_qualified_under_filters(
    operator_client: Client, engine: FakeEngine
) -> None:
    heading = "<h3>Identities (held now — not filtered by As of turn or Proposition id)</h3>"
    assert heading in identities(page(operator_client, "?as_of=1"))
    assert heading in identities(page(operator_client, f"?proposition_id={PROPOSITION}"))
    assert heading not in identities(page(operator_client))


# --- errors --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"), [(422, "viewpoint_unresolved"), (403, "omniscient_required")]
)
def test_viewpoint_unresolved_text(
    operator_client: Client, engine: FakeEngine, status: int, code: str
) -> None:
    answer(engine, status, error_json(code, "omniscient please"))
    html = page(operator_client)
    assert '<p class="omniscient-required">This read needs the omniscient viewpoint</p>' in html
    assert header(html) == "What Alice believes · now"


def test_404_not_found_text(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 404, error_json("not_found", "gone"))
    html = page(operator_client)
    assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    assert field(html, "character")


@pytest.mark.parametrize("code", ["database_unavailable", "ledger_unavailable"])
def test_503_panel_keeps_filters(operator_client: Client, engine: FakeEngine, code: str) -> None:
    answer(engine, 503, error_json(code, "down"))
    html = page(operator_client)
    assert 'class="engine-error"' in html
    assert html.index('<form method="get"') < html.index('class="engine-error"')
    assert "Harbour" in html
    assert header(html) == "What Alice believes · now"


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 503, error_json("erasure_pending", "held"))
    response = operator_client.get(URL)
    assert response.status_code == 503


def test_module_off_after_gate_is_greyed_panel(operator_client: Client, engine: FakeEngine) -> None:
    answer(
        engine,
        409,
        error_json(
            "module_off", "no partitions", module="character_knowledge.partitions", story_id=STORY
        ),
    )
    html = page(operator_client)
    assert '<div class="panel unavailable">' in html
    assert "configuration changed since this page loaded" in html
    assert 'class="engine-error"' not in html

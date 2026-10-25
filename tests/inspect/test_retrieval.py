"""The Inspect Retrieval tab (#141): the GET form, the one ``GET /stories/{id}/retrieve`` read
(``viewpoint=omniscient``), the Resolution and Candidates sections, the display filters and the
read's failures.

Runs against the real urlconf with Retrieval registered, on the schema-validating fake Engine;
the fixture ``tests/fixtures/inspect/retrieval.json`` holds 4 ranked memories of all three kinds
(one retired, one quarantined), 2 episodes, 1 assessment and 2 mentions."""

import copy
import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.held import held_text
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, lite, structured
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/inspect/retrieval/"
RETRIEVE = "/stories/{story_id}/retrieve"
RETRIEVE_PATH = f"/stories/{STORY}/retrieve"
MARA = "01JB2Z8Y5Q3W6K9M1N4P7R0T3A"  # the player
TOBIN = "01JB2Z8Y5Q3W6K9M1N4P7R0T3B"
TAVERN = "01JB2Z8Y5Q3W6K9M1N4P7R0E01"
QUESTION = "What happened at the tavern with Tobin?"
PROMPT = "Retrieval answers a question — enter one to see the memories it returns."
NOTE = "Memories this question retrieves — not a full list (nathanpond/Epistrel#531)"
NOT_RETRIEVED = "That memory isn&#x27;t among what this question retrieves."
HIDDEN = "That memory is retrieved, but the kind/tier filter hides it."
M = "01JB2Z8Y5Q3W6K9M1N4P7R0M"  # memory ids: M + "01".."04", "E1", "E2", "A1"


def fixture() -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / "retrieval.json").read_text())
    return copy.deepcopy(body)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured())
    fake.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=6))
    fake.respond("GET", RETRIEVE, 200, json=fixture())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, params: dict[str, str] | str | None = None) -> str:
    query = params if isinstance(params, str) else urlencode(params or {})
    response = client.get(f"{URL}?{query}" if query else URL)
    assert response.status_code == 200
    return response.content.decode()


def ask(client: Client, **params: str) -> str:
    return page(client, {"q": QUESTION, **params})


def retrieve_calls(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == RETRIEVE_PATH]


def sent(engine: FakeEngine) -> dict[str, list[str]]:
    (call,) = retrieve_calls(engine)
    return parse_qs(call.url.query.decode(), keep_blank_values=True)


def table(html: str, key: str) -> str:
    found = re.search(rf'<table class="retrieved retrieved-{key}">(.*?)</table>', html, re.S)
    assert found is not None, key
    return found.group(1)


def row_ids(html: str, key: str) -> list[str]:
    """The full ids of a candidate table's rows, in order."""
    return re.findall(
        r'<a class="memory-filter" href="[^"]+"><code title="([^"]+)">', table(html, key)
    )


def rows(html: str, key: str) -> list[str]:
    return re.findall(r'<tr class="memory-row[^"]*">(.*?)</tr>', table(html, key), re.S)


def cells(row: str) -> list[str]:
    return [re.sub(r"\s+", " ", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]


def text(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup)).strip()


def row_of(html: str, key: str, memory_id: str) -> list[str]:
    for row in rows(html, key):
        if f'title="{memory_id}"' in row:
            return cells(row)
    raise AssertionError(memory_id)


def field_errors(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    errors = re.search(r'<ul class="errorlist[^"]*"[^>]*>(.*?)</ul>', found.group(1), re.S)
    return text(errors.group(1)) if errors else ""


# --- the form and the one read --------------------------------------------------------------------


def test_the_tab_is_registered_and_offers_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    assert '<a class="active" aria-current="page" href="' + URL + '">Retrieval</a>' in html
    assert '<form method="get" class="retrieval-form">' in html
    assert "<legend>Question</legend>" in html
    assert "<legend>Display filters — these don't change the Engine call</legend>" in html
    assert '<textarea name="q" cols="80" rows="3"' in html
    form = html[html.index('<form method="get" class="retrieval-form">') :]
    assert "maxlength" not in form[: form.index("</form>")]
    assert "blank = head (turn 5)" in html
    assert '<button type="submit">Retrieve</button>' in html
    assert f'<a class="retrieval-clear" href="{URL}">Clear</a>' in html
    assert "hx-get" not in html
    assert "hx-post" not in html
    assert '<option value="01JB2Z8Y5Q3W6K9M1N4P7R0T3A" selected>Mara (player)</option>' in html
    assert '<option value="01JB2Z8Y5Q3W6K9M1N4P7R0T3B">Tobin</option>' in html
    assert PROMPT in html
    assert retrieve_calls(engine) == []
    assert "<title>Inspect · Retrieval · Harbour</title>" in html


def test_a_question_makes_one_omniscient_read_with_defaults_left_out(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client, as_of="", actor=MARA)
    assert sent(engine) == {"q": [QUESTION], "viewpoint": ["omniscient"]}
    assert PROMPT not in html
    assert "As Mara (player) · after turn 5 (head) · quarantined hidden" in text(html)


def test_the_question_is_sent_exactly_as_entered(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(
        operator_client,
        {"q": "  the tavern?\r\n", "as_of": "3", "actor": TOBIN, "include_quarantined": "on"},
    )
    assert sent(engine) == {
        "q": ["  the tavern?\r\n"],
        "viewpoint": ["omniscient"],
        "as_of": ["3"],
        "actor_character_id": [TOBIN],
        "include_quarantined": ["true"],
    }
    assert "As Tobin · after turn 3 · quarantined shown" in text(html)
    # Sticky: the submitted values, checkbox included.
    assert f'<option value="{TOBIN}" selected>Tobin</option>' in html
    assert 'name="as_of" value="3"' in html
    assert re.search(r'<input type="checkbox" name="include_quarantined"[^>]* checked', html)


def test_a_cast_without_a_player_sends_the_first_member(
    operator_client: Client, engine: FakeEngine
) -> None:
    story = story_json(next_turn_index=6)
    for character in story["characters"]:
        character["is_player"] = False
    engine.respond("GET", STORY_PATH, 200, json=story)
    html = ask(operator_client)
    assert sent(engine)["actor_character_id"] == [MARA]
    assert "As Mara · after turn 5 (head)" in text(html)


def test_the_filters_never_change_the_engine_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    ask(operator_client, include_quarantined="1")
    plain = retrieve_calls(engine)[0].url
    engine.reset()
    engine.respond("GET", EFFECTIVE, 200, json=structured())
    engine.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=6))
    engine.respond("GET", RETRIEVE, 200, json=fixture())
    ask(operator_client, include_quarantined="1", kind="semantic", tier="hot", memory=f"{M}02")
    (filtered,) = retrieve_calls(engine)
    assert str(filtered.url) == str(plain)


# --- requests that make no read -------------------------------------------------------------------


def test_a_filter_only_link_prefills_the_form_and_makes_no_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, {"kind": "semantic", "tier": "retired", "memory": "M02"})
    assert retrieve_calls(engine) == []
    assert PROMPT in html
    assert 'class="errorlist' not in html
    assert '<option value="semantic" selected>Semantic</option>' in html
    assert '<option value="retired" selected>retired</option>' in html
    assert 'name="memory" value="M02"' in html


def test_a_filter_only_link_with_a_bad_kind_shows_only_that_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, {"kind": "dream", "as_of": "-1", "actor": "nobody"})
    assert retrieve_calls(engine) == []
    assert PROMPT in html
    assert "Select a valid choice. dream is not one of the available choices." in field_errors(
        html, "kind"
    )
    assert field_errors(html, "as_of") == ""
    assert field_errors(html, "actor") == ""
    assert field_errors(html, "q") == ""


@pytest.mark.parametrize(
    ("params", "name", "message"),
    [
        ({"q": "   "}, "q", "Enter a question."),
        ({"q": ""}, "q", "Enter a question."),
        (
            {"q": "é" * 4001},
            "q",
            "Questions are at most 4,000 characters (this one has 4,001).",
        ),
        ({"q": QUESTION, "kind": "dream"}, "kind", "Select a valid choice."),
        ({"q": QUESTION, "tier": "warm"}, "tier", "Select a valid choice."),
        ({"q": QUESTION, "actor": "01JB2Z8Y5Q3W6K9M1N4P7R0T9Z"}, "actor", "Select a valid choice."),
        ({"q": QUESTION, "as_of": "-1"}, "as_of", "Enter a whole number, 0 or more."),
        ({"q": QUESTION, "as_of": "two"}, "as_of", "Enter a whole number, 0 or more."),
    ],
)
def test_an_invalid_form_shows_the_field_error_and_makes_no_read(
    operator_client: Client,
    engine: FakeEngine,
    params: dict[str, str],
    name: str,
    message: str,
) -> None:
    html = page(operator_client, params)
    assert retrieve_calls(engine) == []
    assert message in field_errors(html, name)
    assert PROMPT not in html


def test_four_thousand_code_points_is_accepted(operator_client: Client, engine: FakeEngine) -> None:
    ask_text = "😀" * 4000  # 4,000 code points, 8,000 UTF-16 units
    page(operator_client, {"q": ask_text})
    assert sent(engine)["q"] == [ask_text]


def test_a_memory_filter_is_never_an_error(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client, memory="  x  ")
    assert len(retrieve_calls(engine)) == 1
    assert field_errors(html, "memory") == ""
    assert NOT_RETRIEVED in html


# --- the resolution -------------------------------------------------------------------------------


def question_markup(html: str) -> str:
    found = re.search(r'<p class="retrieval-question-text">(.*?)</p>', html, re.S)
    assert found is not None
    return found.group(1)


def marks(html: str) -> list[tuple[str, str, str]]:
    """(number, title, text) of each highlighted span."""
    return re.findall(
        r'<mark class="mention" data-mention="(\d+)" title="([^"]*)">(.*?)</mark>',
        question_markup(html),
    )


def mention_cells(html: str) -> list[list[str]]:
    return [
        [text(c) for c in cells(r)]
        for r in re.findall(r'<tr class="mention-row">(.*?)</tr>', html, re.S)
    ]


def answer(engine: FakeEngine, **resolution: Any) -> dict[str, Any]:
    body = fixture()
    body["resolution"].update(resolution)
    engine.respond("GET", RETRIEVE, 200, json=body)
    return body


def mention(start: int, end: int, entity: str | None = TAVERN, reason: str | None = None) -> Any:
    return {"span": [start, end], "kind": "name", "entity_id": entity, "reason": reason}


def test_mentions_are_highlighted_and_resolved(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client)
    assert marks(html) == [("1", f"Name · {TAVERN}", "tavern"), ("2", "Name · Tobin", "Tobin")]
    assert text(question_markup(html)) == "What happened at the tavern 1 with Tobin 2 ?"
    assert mention_cells(html) == [
        ["1", "tavern", "Name", "…4P7R0E01"],
        ["2", "Tobin", "Name", "Tobin"],
    ]
    assert f'<span class="character" title="{TOBIN}">Tobin</span>' in html
    assert re.search(r'<dd class="resolved-entities"><code title="' + TAVERN, html)
    assert "…4P7R0E01</code>, <code" in html
    assert '<span title="social_gathering">Social gathering</span>' in html
    assert "[480, …) min · turns 2\u2013…" in html


def test_a_span_at_zero_and_adjacent_spans(operator_client: Client, engine: FakeEngine) -> None:
    answer(
        engine,
        mentions=[mention(0, 4, None, "unknown_name"), mention(4, 13, MARA), mention(21, 27)],
    )
    html = ask(operator_client)
    assert marks(html) == [
        ("1", "Name · Unknown name", "What"),
        ("2", "Name · Mara (player)", " happened"),
        ("3", f"Name · {TAVERN}", "tavern"),
    ]
    assert mention_cells(html)[0] == ["1", "What", "Name", "Unknown name"]
    assert '<span title="unknown_name">Unknown name</span>' in html


def test_overlapping_and_out_of_range_spans_are_not_highlighted(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(
        engine,
        mentions=[
            mention(17, 27),
            mention(21, 30),  # overlaps the first
            mention(30, 99),  # past the end
            {"span": [5], "kind": "pronoun", "entity_id": None, "reason": "no_antecedent"},
            mention(8, 8),  # empty
        ],
    )
    html = ask(operator_client)
    assert [m[0] for m in marks(html)] == ["1"]
    found = mention_cells(html)
    assert found[1][1] == "tavern wi not highlighted"
    assert found[2][1] == "— not highlighted"
    assert found[3] == ["4", "— not highlighted", "Pronoun", "No antecedent"]
    assert found[4][1] == "— not highlighted"
    assert "not highlighted" not in found[0][1]


def test_html_in_the_question_is_escaped(operator_client: Client, engine: FakeEngine) -> None:
    q = "<script>alert(1)</script> tavern"
    answer(engine, mentions=[mention(0, 8), mention(26, 32)])
    html = page(operator_client, {"q": q})
    assert "<script>alert(1)</script>" not in html
    assert marks(html)[0][2] == "&lt;script&gt;"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


@pytest.mark.parametrize(
    ("resolution", "shown"),
    [
        ({"window": None, "turns": None, "time_reason": None}, "No time expression"),
        (
            {"window": None, "turns": None, "time_reason": "no_calendar"},
            '<span title="no_calendar">No calendar</span>',
        ),
        ({"window": [0, 60], "turns": None}, "[0, 60) min · turns —"),
        ({"window": None, "turns": [3, 4]}, "turns 3\u20134"),
        ({"window": [None, 60], "turns": [None, 4]}, "[…, 60) min · turns …\u20134"),
        ({"window": [1, 2, 3], "turns": [1]}, "<code>[1, 2, 3]</code> · turns <code>[1]</code>"),
    ],
)
def test_the_time_window(
    operator_client: Client, engine: FakeEngine, resolution: dict[str, Any], shown: str
) -> None:
    answer(engine, **resolution)
    html = ask(operator_client)
    found = re.search(r'<dd class="resolved-time">(.*?)</dd>', html, re.S)
    assert found is not None
    assert found.group(1) == shown


def test_no_mentions_or_entities(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, mentions=[], entities=[], event_type=None)
    html = ask(operator_client)
    assert '<tr class="mention-none"><td colspan="4">None</td></tr>' in html
    assert '<dd class="resolved-entities">None</dd>' in html
    assert '<dd class="resolved-event-type">—</dd>' in html
    assert text(question_markup(html)) == QUESTION


# --- the candidates -------------------------------------------------------------------------------


def test_three_tables_in_order_with_engine_order_rows(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client)
    assert html.index("<h3") < html.index('class="candidates-episodes"')
    assert (
        html.index('class="candidates-memories"')
        < html.index('class="candidates-episodes"')
        < html.index('class="candidates-assessments"')
    )
    assert row_ids(html, "memories") == [f"{M}01", f"{M}02", f"{M}03", f"{M}04"]
    assert row_ids(html, "episodes") == [f"{M}E1", f"{M}E2"]
    assert row_ids(html, "assessments") == [f"{M}A1"]
    assert NOTE in html
    assert "Showing" not in html
    assert "(4 of 4)" not in html


def test_a_ranked_memory_row(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client)
    found = row_of(html, "memories", f"{M}01")
    assert [text(c) for c in found] == [
        "…4P7R0M01",
        "Episodic",
        "hot",
        "0.650",
        "0.812",
        "0.900",
        "0.750",
        "0.600",
        "vector lexical",
        "Mara (player)",
        "Mara (player) , Tobin",  # text() spaces out the tags
        "Conversation",
        "turn 5 · beat 2",
        "Mara asked Tobin about the tavern fire.",
        "Pin Unpin Quarantine",  # #143's row actions
    ]
    assert '<span class="arm arm-vector">vector</span> <span class="arm arm-lexical">' in found[8]
    assert (
        f'href="/stories/{STORY}/history/?before_turn=6#turn-5-0">turn 5 · beat 2</a>'
        in (found[12])
    )


def test_the_id_link_keeps_the_question_and_drops_other_filters(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client, as_of="5", actor=TOBIN, include_quarantined="1", kind="episodic")
    href = re.search(r'<a class="memory-filter" href="([^"]+)">', table(html, "memories"))
    assert href is not None
    url = href.group(1).replace("&amp;", "&")
    assert urlsplit(url).path == URL
    assert parse_qs(urlsplit(url).query) == {
        "q": [QUESTION],
        "as_of": ["5"],
        "actor": [TOBIN],
        "include_quarantined": ["1"],
        "memory": [f"{M}01"],
    }


def test_retired_quarantined_and_unscored_rows(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client)
    assert row_of(html, "memories", f"{M}03")[2] == (
        '<span title="retired">retired · rehydrated</span>'
    )
    quarantined = [r for r in rows(html, "memories") if f'title="{M}04"' in r]
    assert '<tr class="memory-row quarantined">' in table(html, "memories")
    assert '<span class="badge quarantined">quarantined</span>' in quarantined[0]
    assert row_of(html, "memories", f"{M}04")[11] == "—"  # no participants
    assert html.count('<tr class="memory-row quarantined">') == 1
    episode = [text(c) for c in row_of(html, "episodes", f"{M}E2")]
    assert episode[2:8] == ["retired · rehydrated", "0.400", "—", "—", "—", "—"]
    assessment = [text(c) for c in row_of(html, "assessments", f"{M}A1")]
    assert assessment[1] == "Reflection"
    assert assessment[12] == "turn 4"


def test_null_score_parts_and_importance_render_a_dash(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture()
    body["memories"][0].update(score=None, similarity=None, recency=None, decayed_importance=None)
    body["memories"][0].update(importance=None, event_type=None, narrative_turn=None, beat=None)
    body["memories"][0].update(character_id=None, arms=[], participants=[])
    engine.respond("GET", RETRIEVE, 200, json=body)
    found = [text(c) for c in row_of(ask(operator_client), "memories", f"{M}01")]
    assert found[3:13] == ["—"] * 10


def test_unknown_arm_kind_and_tier_render_as_code(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture()
    body["memories"][0].update(kind="dream", retrieval_tier="frozen", arms=["vector", "beliefs"])
    engine.respond("GET", RETRIEVE, 200, json=body)
    found = row_of(ask(operator_client), "memories", f"{M}01")
    assert found[1] == "<code>dream</code>"
    assert found[2] == "<code>frozen</code>"
    assert found[8] == '<span class="arm arm-vector">vector</span> <code>beliefs</code>'


def test_no_candidates_says_so(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture()
    body.update(memories=[], episodes=[], assessments=[])
    engine.respond("GET", RETRIEVE, 200, json=body)
    html = ask(operator_client)
    assert "This question retrieved no memories." in html
    assert '<table class="retrieved' not in html


def test_the_legend_gives_the_formula_weights_and_arms(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client)
    legend = re.search(r'<details class="legend score-legend">(.*?)</details>', html, re.S)
    assert legend is not None
    shown = text(legend.group(1))
    assert "score = w_sim·similarity + w_rec·recency + w_imp·decayed importance" in shown
    assert "w_sim = 0.600, w_rec = 0.200, w_imp = 0.200" in shown
    assert "Importance is shown but is not itself a score term." in shown
    assert "(100 for this story)" in shown
    for arm in ("vector", "lexical", "alias", "occasion", "assessment"):
        assert f'<span class="arm arm-{arm}">{arm}</span>' in legend.group(1)
    assert "A memory found by several arms was fused from them." in shown
    assert html.index("score-legend") < html.index('class="candidates-memories"')


def test_missing_weights_read_a_dash(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        EFFECTIVE,
        200,
        json=structured(memory__episodic__weight_recency=None),
    )
    assert "w_rec = —," in text(ask(operator_client))


# --- the display filters --------------------------------------------------------------------------


def test_the_kind_filter_narrows_all_three_tables(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client, kind="reflection")
    assert row_ids(html, "memories") == [f"{M}03"]
    assert row_ids(html, "assessments") == [f"{M}A1"]
    assert 'class="candidates-episodes">Episodes (0 of 2)</h3>\n<p class="retrieved-none">None' in (
        html
    )
    assert "Memories (1 of 4)" in html
    assert "Assessments (1 of 1)" not in html
    assert "Showing 2 of 7 candidates" in html
    assert html.index("Showing 2 of 7") < html.index(NOTE)
    assert f"{M}02" not in table(html, "memories")  # rows of other kinds are absent


def test_the_tier_filter(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client, tier="retired")
    assert row_ids(html, "memories") == [f"{M}03"]
    assert row_ids(html, "episodes") == [f"{M}E2"]
    assert "Showing 2 of 7 candidates" in html


def test_kind_and_tier_combine(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client, kind="episodic", tier="hot")
    assert row_ids(html, "memories") == [f"{M}01", f"{M}04"]
    assert row_ids(html, "episodes") == [f"{M}E1"]
    assert "Showing 3 of 7 candidates" in html


def test_the_memory_filter_by_full_id_or_suffix(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client, memory=f"{M}02".lower())
    assert row_ids(html, "memories") == [f"{M}02"]
    assert "Showing 1 of 7 candidates" in html
    html = ask(operator_client, memory="…4p7r0me1")
    assert row_ids(html, "episodes") == [f"{M}E1"]
    html = ask(operator_client, memory="P7R0E1")  # under 8 characters
    assert NOT_RETRIEVED in html


def test_a_memory_the_question_does_not_retrieve(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client, memory="01JB2Z8Y5Q3W6K9M1N4P7R0Z99")
    assert NOT_RETRIEVED in html
    assert HIDDEN not in html


def test_a_memory_the_kind_filter_hides(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client, memory=f"{M}02", kind="episodic")
    assert HIDDEN in html
    assert NOT_RETRIEVED not in html


def test_an_unknown_kind_never_matches_a_set_filter(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture()
    body["memories"][0]["kind"] = "dream"
    engine.respond("GET", RETRIEVE, 200, json=body)
    html = ask(operator_client, kind="episodic")
    assert row_ids(html, "memories") == [f"{M}04"]


# --- skipped, More and failures -------------------------------------------------------------------


def test_episodic_skipped_shows_the_reason_instead_of_the_tables(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture()
    body.update(memories=[], episodes=[], assessments=[], participants=[], skipped=["episodic"])
    engine.respond("GET", RETRIEVE, 200, json=body)
    html = ask(operator_client)
    assert '<p class="notice unavailable">Needs the <code>episodic</code> module' in html
    assert '<table class="retrieved' not in html
    assert "This question retrieved no memories." not in html
    assert "participants —" in text(html)


def test_more_holds_the_sections_still_to_come(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture()
    body["skipped"] = ["occasion"]
    engine.respond("GET", RETRIEVE, 200, json=body)
    html = ask(operator_client)
    found = re.search(r'<details class="more"><summary>More</summary><pre>(.*?)</pre>', html, re.S)
    assert found is not None
    more = json.loads(found.group(1).replace("&quot;", '"').replace("&#x27;", "'"))
    assert set(more) == {
        "canon",
        "profiles",
        "context",
        "timings",
        "degradation",
        "rehydrated",
        "model_calls",
        "embedding_model_version",
        "position",
        "skipped",
    }
    assert more["skipped"] == ["occasion"]
    assert more["position"] == [6, 0]


def test_a_422_on_as_of_names_the_head(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        RETRIEVE,
        422,
        json=error_json("validation_error", "as_of out of range", field="query.as_of", head=5),
    )
    html = ask(operator_client, as_of="9")
    assert field_errors(html, "as_of") == "Turn must be 0\u20135"
    assert 'class="retrieval-header"' not in html


@pytest.mark.parametrize(
    ("field", "name"), [("query.q", "q"), ("query.actor_character_id", "actor")]
)
def test_a_422_lands_on_its_field(
    operator_client: Client, engine: FakeEngine, field: str, name: str
) -> None:
    engine.respond(
        "GET", RETRIEVE, 422, json=error_json("validation_error", "not acceptable", field=field)
    )
    assert field_errors(ask(operator_client), name) == "not acceptable"


def test_a_422_on_another_field_goes_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", RETRIEVE, 422, json=error_json("validation_error", "odd", field="query.other")
    )
    html = ask(operator_client)
    assert html.index("query.other: odd") < html.index('<form method="get"')


def test_viewpoint_unresolved_renders_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        RETRIEVE,
        422,
        json=error_json("viewpoint_unresolved", "No viewpoint resolves", field="query.viewpoint"),
    )
    html = ask(operator_client)
    errors = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    assert errors is not None
    assert text(errors.group(1)) == "No viewpoint resolves"
    assert html.index("No viewpoint resolves") < html.index('<form method="get"')


def test_not_found_keeps_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RETRIEVE, 404, json=error_json("not_found", "gone"))
    html = ask(operator_client)
    assert '<p class="not-found">Not found (it may have been deleted or truncated)</p>' in html
    assert html.index('<form method="get"') < html.index('class="not-found"')


def test_omniscient_required_is_named(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RETRIEVE, 403, json=error_json("omniscient_required", "no"))
    html = ask(operator_client)
    assert "This read needs the omniscient viewpoint" in html
    assert '<form method="get"' in html


@pytest.mark.parametrize(
    ("status", "code"), [(503, "database_unavailable"), (503, "ledger_unavailable")]
)
def test_other_errors_render_the_panel_below_the_form(
    operator_client: Client, engine: FakeEngine, status: int, code: str
) -> None:
    engine.respond("GET", RETRIEVE, status, json=error_json(code, "refused"))
    html = ask(operator_client)
    assert f"Engine error {status} {code}" in html
    assert html.index('<form method="get"') < html.index('class="engine-error"')


def test_an_unparsable_answer_is_the_unexpected_response_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture()
    body["memories"][0]["arms"] = "vector"  # not a list: the model refuses it
    engine.side_effect("GET", RETRIEVE, lambda request: httpx.Response(200, json=body))
    html = ask(operator_client)
    assert "Engine error 200 bad_response" in html
    assert '<form method="get"' in html


def test_unreachable_renders_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", RETRIEVE, httpx.ReadTimeout("slow"))
    html = ask(operator_client)
    assert "Engine unreachable" in html
    assert '<form method="get"' in html


def test_erasure_pending_is_the_held_wrapper(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RETRIEVE, 503, json=error_json("erasure_pending", "held", story_id=STORY))
    response = operator_client.get(f"{URL}?{urlencode({'q': QUESTION})}")
    assert response.status_code == 503
    assert held_text(None) in response.content.decode()


def test_a_failed_story_read_degrades_to_ids(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable"))
    html = page(operator_client)
    assert '<option value="" selected>Player (default)</option>' in html
    assert "blank = head<" in html.replace("</small>", "<")
    html = ask(operator_client, actor=TOBIN)
    assert f'<option value="{TOBIN}" selected>{TOBIN}</option>' in html
    assert sent(engine)["actor_character_id"] == [TOBIN]
    assert f"As {TOBIN} · after the head · quarantined hidden" in text(html)
    assert f'<code title="{MARA}">…4P7R0T3A</code>' in html  # participants stay ids


def test_a_story_with_no_turns_still_offers_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=0))
    assert "blank = head (no turns yet)" in page(operator_client)


def test_a_greyed_retrieval_tab_makes_no_read(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite())
    html = ask(operator_client)
    assert retrieve_calls(engine) == []
    assert '<div class="panel unavailable">' in html
    assert '<form method="get"' not in html


def test_participants_join_without_stray_space(operator_client: Client, engine: FakeEngine) -> None:
    html = ask(operator_client)
    assert f'Mara (player)</span>, <span class="character" title="{TOBIN}">Tobin</span>' in html

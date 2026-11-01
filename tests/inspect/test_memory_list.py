"""The Memory tab's memory list (#236): every memory of a story from
``GET /admin/stories/{id}/memories``, its filters, paging, rows, row actions and errors.

Runs against the real urlconf on the schema-validating fake Engine, under Structured v2 and
#232's M6 cast (``m6/story.json``: the player Mara, Alice and Bob). ``m6/memories_page1.json``
(revision 12, ``next_cursor`` ``…MM02``) holds Mara's episodic ``…MM01`` (turn 3 · beat 2,
story-level) and Alice's reflection ``…MM02``, a consolidation output of turns 1\u20133 at level 1
with ``…MM01`` as its one source; page 2 (``cursor=…MM02``, C14) is ``m6/memories_empty.json``."""

import copy
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.inspect import AS_OF_INVALID, TURN_TOO_HIGH
from console.held import held_text
from console.paging import CAST_UNAVAILABLE, CHARACTER_INVALID, CURSOR_DROPPED
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, structured_v2
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
BOB = "01JM6CH0000000000000000003"
MM1 = "01JM6MM0000000000000000001"
MM2 = "01JM6MM0000000000000000002"
OUTSIDER = "01JM6CH0000000000000000099"
URL = f"/stories/{STORY}/inspect/memory/list/"
MEMORIES = "/admin/stories/{story_id}/memories"
MEMORIES_PATH = f"/admin/stories/{STORY}/memories"
RETRIEVAL = f"/stories/{STORY}/inspect/retrieval/"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
OMNISCIENT_TEXT = "This read needs the omniscient viewpoint"
ROOT = Path(__file__).resolve().parents[2]


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
    fake.respond("GET", EFFECTIVE, 200, json=structured_v2())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond("GET", MEMORIES, 200, json=fixture("memories_page1"))
    fake.respond("GET", MEMORIES, 200, json=fixture("memories_empty"), query={"cursor": MM2})
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == MEMORIES_PATH]


def sent(engine: FakeEngine) -> dict[str, str]:
    (call,) = reads(engine)
    return dict(call.url.params)


def answer(engine: FakeEngine, status: int, body: dict[str, Any]) -> None:
    engine.respond("GET", MEMORIES, status, json=body)


def answer_raw(engine: FakeEngine, body: dict[str, Any]) -> None:
    """A page the schema refuses (a malformed entry), served unvalidated."""
    engine.side_effect("GET", MEMORIES, lambda request: httpx.Response(200, json=body))


def row(html: str, memory_id: str) -> str:
    found = re.search(rf'<tr id="memory-{memory_id}" class="[^"]*">(.*?)</tr>', html, re.S)
    assert found is not None, memory_id
    return found.group(1)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def text(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup)).strip()


def field(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def options(html: str, name: str) -> list[tuple[str, str]]:
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', field(html, name))


def selected(html: str, name: str) -> str:
    found = re.search(r'<option value="([^"]*)"[^>]*selected', field(html, name))
    assert found is not None
    return found.group(1)


def heading(html: str) -> str | None:
    found = re.search(r'<h3 class="memory-count">(.*?)</h3>', html, re.S)
    return None if found is None else text(found.group(1))


def links(html: str, cls: str) -> list[str]:
    return [h.replace("&amp;", "&") for h in re.findall(rf'<a class="{cls}" href="([^"]*)"', html)]


def query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


# --- the route and its reads ---------------------------------------------------------------------


def test_the_list_reads_once_after_the_shell(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        MEMORIES_PATH,
    ]
    assert reads(engine)[0].url.query.decode() == "viewpoint=omniscient&limit=100"
    assert "<h2>All memories</h2>" in html
    assert f'<a href="/stories/{STORY}/inspect/memory/">← Memory tab</a>' in html
    assert 'aria-current="page"' in html
    assert ">Memory</a>" in html
    assert "Clear filters" not in html


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"kind": "semantic"}, {"kind": "semantic"}),
        ({"retrieval_tier": "retired"}, {"retrieval_tier": "retired"}),
        ({"holder_character_id": ALICE}, {"holder_character_id": ALICE}),
        ({"from_turn": "0"}, {"from_turn": "0"}),
        ({"to_turn": "007"}, {"to_turn": "7"}),
        ({"from_turn": " 2 ", "to_turn": "2"}, {"from_turn": "2", "to_turn": "2"}),
        ({"quarantined": "false"}, {"quarantined": "false"}),
        ({"quarantined": "true"}, {"quarantined": "true"}),
        ({"pinned": "true"}, {"pinned": "true"}),
        ({"pinned": "false"}, {"pinned": "false"}),
    ],
)
def test_filters_are_sent_only_when_set(
    operator_client: Client, engine: FakeEngine, params: dict[str, str], expected: dict[str, str]
) -> None:
    html = page(operator_client, f"?{urlencode(params)}")
    assert sent(engine) == {"viewpoint": "omniscient", "limit": "100", **expected}
    assert heading(html) == "Memories (2 on this page) · filtered Story revision 12"
    assert f'<a class="clear-filters" href="{URL}">Clear filters</a>' in html


def test_any_either_and_blank_send_nothing(operator_client: Client, engine: FakeEngine) -> None:
    blank = {name: "" for name in ("kind", "retrieval_tier", "holder_character_id")}
    blank.update(from_turn="", to_turn="", quarantined="", pinned="")
    html = page(operator_client, f"?{urlencode(blank)}")
    assert sent(engine) == {"viewpoint": "omniscient", "limit": "100"}
    assert heading(html) == "Memories (2 on this page) Story revision 12"
    assert options(html, "kind")[0] == ("", "Any kind")
    assert options(html, "retrieval_tier") == [
        ("", "Any tier"),
        ("hot", "Hot"),
        ("retired", "Retired"),
    ]
    assert options(html, "quarantined") == [("", "Either"), ("true", "Yes"), ("false", "No")]
    assert options(html, "pinned") == [("", "Either"), ("true", "Yes"), ("false", "No")]
    assert [label for _, label in options(html, "kind")] == [
        "Any kind",
        "Episodic",
        "Semantic",
        "Reflection",
    ]
    for name in ("from_turn", "to_turn"):
        widget = field(html, name)
        assert 'type="number"' in widget
        assert 'step="1"' in widget
        assert "min=" not in widget
        assert "max=" not in widget
    assert "Inclusive; memories without a position (such as author facts) are in no range" in field(
        html, "to_turn"
    )


@pytest.mark.parametrize(
    ("params", "where", "error"),
    [
        ({"kind": "bogus"}, "kind", "Choose a kind from the list"),
        ({"kind": "Semantic"}, "kind", "Choose a kind from the list"),
        ({"retrieval_tier": "warm"}, "retrieval_tier", "Choose a tier from the list"),
        ({"quarantined": "yes"}, "quarantined", "Choose Either, Yes or No"),
        ({"pinned": "True"}, "pinned", "Choose Either, Yes or No"),
        ({"from_turn": "-1"}, "from_turn", AS_OF_INVALID),
        ({"from_turn": "1.5"}, "from_turn", AS_OF_INVALID),
        ({"to_turn": "١٢"}, "to_turn", AS_OF_INVALID),
        ({"to_turn": "2147483648"}, "to_turn", TURN_TOO_HIGH),
        ({"holder_character_id": "not-an-id"}, "holder_character_id", CHARACTER_INVALID),
        ({"from_turn": "5", "to_turn": "2"}, None, "To turn is before From turn"),
    ],
)
def test_invalid_filters_make_no_call(
    operator_client: Client,
    engine: FakeEngine,
    params: dict[str, str],
    where: str | None,
    error: str,
) -> None:
    html = page(operator_client, f"?{urlencode(params)}")
    assert reads(engine) == []
    if where is None:
        errors = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
        assert errors is not None
        assert escape(error) in errors.group(1)
    else:
        assert escape(error) in field(html, where)
    assert heading(html) is None


def test_holder_from_the_url_follows_c13(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?holder_character_id={ALICE.lower()}")
    assert sent(engine)["holder_character_id"] == ALICE
    assert selected(html, "holder_character_id") == ALICE
    assert options(html, "holder_character_id") == [
        ("", "Any holder"),
        (MARA, "Mara (player)"),
        (ALICE, "Alice"),
        (BOB, "Bob"),
    ]


def test_a_holder_outside_the_cast_is_kept_and_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, f"?holder_character_id={OUTSIDER}")
    assert sent(engine)["holder_character_id"] == OUTSIDER
    assert options(html, "holder_character_id")[-1] == (OUTSIDER, "…00000099 (not in the cast)")
    assert selected(html, "holder_character_id") == OUTSIDER
    assert "· filtered" in (heading(html) or "")


def test_without_the_story_read_holder_is_free_text(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable"))
    html = page(operator_client, f"?holder_character_id={BOB}")
    assert CAST_UNAVAILABLE in html
    widget = field(html, "holder_character_id")
    assert 'type="text"' in widget
    assert "<select" not in widget
    assert "A character id" in widget
    assert sent(engine)["holder_character_id"] == BOB
    engine.requests.clear()
    html = page(operator_client, "?holder_character_id=nope")
    assert reads(engine) == []
    assert escape(CHARACTER_INVALID) in field(html, "holder_character_id")


def test_a_bad_cursor_is_dropped_with_a_notice(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?kind=episodic&cursor=not-a-cursor")
    assert CURSOR_DROPPED in html
    assert sent(engine) == {"viewpoint": "omniscient", "limit": "100", "kind": "episodic"}
    assert all("not-a-cursor" not in r.url.query.decode() for r in engine.requests)
    assert all("not-a-cursor" not in url for url in links(html, "next-page"))


def test_paging_keeps_the_filters_and_first_page_drops_the_cursor(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, "?kind=reflection&pinned=false")
    (following,) = links(html, "next-page")
    assert query(following) == {"kind": ["reflection"], "pinned": ["false"], "cursor": [MM2]}
    assert links(html, "first-page") == []
    engine.requests.clear()
    html = page(operator_client, "?kind=reflection&pinned=false&cursor=" + MM2)
    assert sent(engine)["cursor"] == MM2
    assert sent(engine)["kind"] == "reflection"
    (first,) = links(html, "first-page")
    assert query(first) == {"kind": ["reflection"], "pinned": ["false"]}
    assert links(html, "next-page") == []
    # Apply carries no cursor, so a changed filter starts over.
    form = re.search(r'<form method="get" id="memory-list-filters"(.*?)</form>', html, re.S)
    assert form is not None
    assert 'name="cursor"' not in form.group(1)


# --- rows ----------------------------------------------------------------------------------------


def test_rows_render_the_memory_and_its_state(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("memories_page1")
    body["memories"][0].update(pinned=True, pinned_by="console:nate")
    reason = "x" * 90
    body["memories"][1].update(
        quarantined=True,
        quarantine_reason=reason,
        retrieval_tier="retired",
        retired_reason="covered",
    )
    answer(engine, 200, body)
    html = page(operator_client)
    assert heading(html) == "Memories (2 on this page) Story revision 12"
    heads = re.findall(r"<th>([^<]*)</th>", html.split('<table class="memory-list">')[1])
    assert heads[:8] == [
        "Id",
        "Kind",
        "Tier",
        "Holder",
        "Position",
        "State",
        "Consolidation",
        "Text",
    ]
    first = row(html, MM1)
    retrieval = f"{RETRIEVAL}?memory={MM1}&amp;tier=hot&amp;include_quarantined=1"
    assert f'<a href="{retrieval}"><code title="{MM1}">…00000001</code></a>' in first
    assert '<span title="episodic">Episodic</span>' in cell(first, "memory-kind")
    assert '<span title="hot">Hot</span>' in cell(first, "memory-tier")
    assert cell(first, "memory-holder") == "story-level"
    history = f"/stories/{STORY}/history/?before_turn=4#turn-3-0"
    assert cell(first, "memory-position") == f'<a href="{history}">turn 3 · beat 2</a>'
    assert cell(first, "memory-state") == "pinned (by console:nate)"
    assert cell(first, "memory-text") == "Mara paid the harbour fee."
    second = row(html, MM2)
    assert '<span title="reflection">Reflection</span>' in cell(second, "memory-kind")
    assert text(cell(second, "memory-tier")) == "Retired (covered)"
    assert f'<span class="character" title="{ALICE}">Alice</span>' in cell(second, "memory-holder")
    assert cell(second, "memory-position") == "—"
    state = cell(second, "memory-state")
    assert f'<span class="badge quarantined" title="{reason}">quarantined</span>' in state
    assert text(state) == f"quarantined {'x' * 80}…"
    more = cell(first, "more")
    for key in ("character_id", "implication_count", "embedded", "membership", "provenance"):
        assert f"&quot;{key}&quot;" in more
    assert "&quot;visibility&quot;" in more
    assert "&quot;quarantined_at&quot;" in more


def test_an_unknown_kind_and_tier_show_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("memories_page1")
    body["memories"][0].update(kind="dream", retrieval_tier="frozen")
    answer_raw(engine, body)  # values the schema doesn't list yet (C2's open enums)
    first = row(page(operator_client), MM1)
    assert cell(first, "memory-kind") == "<code>dream</code>"
    assert cell(first, "memory-tier") == "<code>frozen</code>"
    assert f"memory={MM1}&amp;include_quarantined=1" in cell(first, "memory-id")
    assert "tier=" not in cell(first, "memory-id")


def test_a_player_holder_is_marked(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("memories_page1")
    body["memories"][0]["holder_character_id"] = MARA
    answer(engine, 200, body)
    holder = cell(row(page(operator_client), MM1), "memory-holder")
    assert holder == f'<span class="character" title="{MARA}">Mara (player)</span>'


def test_a_consolidation_output_shows_its_provenance(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    shown = cell(row(html, MM2), "memory-consolidation")
    assert 'title="operation 01JM6QP0000000000000000071"' in shown
    assert text(shown) == f"turns 1\u20133 · scope character:{ALICE} · level 1 · 1 source …00000001"
    assert f"<code>character:{ALICE}</code>" in shown
    sources = re.search(
        r'<details class="sources"><summary>1 source</summary>(.*?)</details>', shown
    )
    assert sources is not None
    assert f'href="{RETRIEVAL}?memory={MM1}&amp;include_quarantined=1"' in sources.group(1)
    # Complement: an ordinary memory's cell is "—".
    assert cell(row(html, MM1), "memory-consolidation") == "—"


@pytest.mark.parametrize(
    ("over", "expected"),
    [
        (
            {"from_turn": 4, "to_turn": 4, "scope": None, "level": 0},
            "turn 4 · level 0 · 1 source …00000001",
        ),
        ({"to_turn": None, "level": None, "source_memory_ids": []}, "from turn 1 · scope shared"),
        ({"from_turn": None, "scope": None, "level": None}, "to turn 3 · 1 source …00000001"),
    ],
)
def test_consolidation_parts_show_only_when_set(
    operator_client: Client, engine: FakeEngine, over: dict[str, Any], expected: str
) -> None:
    body = fixture("memories_page1")
    body["memories"][1]["consolidation"].update({"scope": "shared", **over})
    answer(engine, 200, body)
    shown = cell(row(page(operator_client), MM2), "memory-consolidation")
    assert text(shown) == expected
    if not body["memories"][1]["consolidation"]["source_memory_ids"]:
        assert "<details" not in shown


def test_row_actions_return_to_the_list(operator_client: Client, engine: FakeEngine) -> None:
    path = f"{URL}?kind=episodic&pinned=false&cursor={MM2}"
    answer(engine, 200, fixture("memories_page1"))
    engine.respond("GET", MEMORIES, 200, json=fixture("memories_page1"), query={"cursor": MM2})
    html = page(operator_client, path[len(URL) :])
    actions = cell(row(html, MM1), "actions")
    nexts = re.findall(r'<input type="hidden" name="next" value="([^"]*)">', actions)
    assert [n.replace("&amp;", "&") for n in nexts] == [path, path]
    for cls in ("memory-quarantine", "memory-sanitize"):
        (href,) = re.findall(rf'<a class="button {cls}" href="([^"]*)"', actions)
        assert query(href.replace("&amp;", "&"))["next"] == [path]
    assert f'action="/stories/{STORY}/inspect/memories/{MM1}/pin/"' in actions


def test_empty_pages_say_why(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, fixture("memories_empty"))
    html = page(operator_client)
    assert '<p class="none memories-none">This story has no memories yet</p>' in html
    assert heading(html) == "Memories (0 on this page) Story revision 12"
    html = page(operator_client, "?kind=semantic")
    assert '<p class="none memories-none">No memories match these filters</p>' in html
    assert "This story has no memories yet" not in html


def test_an_empty_later_page_offers_the_first(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?cursor={MM2}")
    assert "This story has no memories yet" in html
    assert links(html, "first-page") == [f"{URL}?"[len(URL) :]]


def test_a_malformed_entry_is_counted_not_fatal(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("memories_page1")
    del body["memories"][0]["kind"]
    answer_raw(engine, body)
    html = page(operator_client)
    assert '<p class="notice memories-unread">1 memory couldn&#x27;t be read</p>' in html
    assert heading(html) == "Memories (1 on this page) Story revision 12"
    assert f'id="memory-{MM1}"' not in html
    assert f'id="memory-{MM2}"' in html
    more = re.search(r'<details class="memories-unread-more">(.*?)</details>', html, re.S)
    assert more is not None
    assert "Mara paid the harbour fee." in more.group(1)
    assert "Mara keeps careful accounts." not in more.group(1)


def test_every_entry_malformed_shows_only_the_notice(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("memories_page1")
    for memory in body["memories"]:
        del memory["kind"]
    answer_raw(engine, body)
    html = page(operator_client)
    assert "2 memories couldn&#x27;t be read" in html
    assert "memories-none" not in html


# --- errors --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "check"),
    [
        (
            422,
            error_json(
                "validation_error", "to_turn must not be before from_turn", field="query.to_turn"
            ),
            "to_turn",
        ),
        (403, error_json("omniscient_required", "omniscient only"), "omniscient"),
        (422, error_json("viewpoint_unresolved", "unresolved"), "omniscient"),
        (404, error_json("not_found", "gone"), "not_found"),
        (503, error_json("database_unavailable", "down"), "panel"),
        (422, error_json("validation_error", "bad limit", field="query.limit"), "above"),
        (422, error_json("validation_error", "bad cursor", field="query.cursor"), "cursor"),
    ],
)
def test_engine_errors(
    operator_client: Client, engine: FakeEngine, status: int, body: dict[str, Any], check: str
) -> None:
    answer(engine, status, body)
    html = page(operator_client, "?kind=episodic&from_turn=1&to_turn=3")
    assert len(reads(engine)) == 1
    assert 'id="memory-list-filters"' in html
    assert selected(html, "kind") == "episodic"
    assert heading(html) is None
    errors = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    if check == "to_turn":
        assert "to_turn must not be before from_turn" in field(html, "to_turn")
        assert errors is None
    elif check == "omniscient":
        assert f'<p class="omniscient-required">{OMNISCIENT_TEXT}</p>' in html
    elif check == "not_found":
        assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    elif check == "panel":
        filters = html.index('id="memory-list-filters"')
        assert html.index("Engine error 503 database_unavailable") > filters
    elif check == "above":
        assert errors is not None
        assert "bad limit" in errors.group(1)
        assert "first-page" not in errors.group(1)
    else:
        assert errors is not None
        assert "bad cursor" in errors.group(1)
        assert 'class="first-page"' in errors.group(1)


def test_erasure_pending_is_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        MEMORIES,
        503,
        json=error_json("erasure_pending", "held"),
        headers={"Retry-After": "30"},
    )
    response = operator_client.get(URL)
    assert response.status_code == 503
    assert held_text("30") in response.content.decode()


def test_a_greyed_memory_tab_makes_no_list_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=structured_v2(memory__episodic__enabled=False))
    html = page(operator_client, "?kind=semantic")
    assert '<div class="panel unavailable">' in html
    assert reads(engine) == []


def test_read_only(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client)
    assert {r.method for r in engine.requests} == {"GET"}
    before = len(engine.requests)
    assert operator_client.post(URL).status_code == 405
    assert len(engine.requests) == before


# --- no page says the Engine can't list memories --------------------------------------------------


def test_no_source_names_the_old_engine_gap() -> None:
    hits = [
        path
        for folder in ("console", "templates")
        for path in (ROOT / folder).rglob("*")
        if path.suffix in (".py", ".html") and "Epistrel#531" in path.read_text(encoding="utf-8")
    ]
    assert hits == []

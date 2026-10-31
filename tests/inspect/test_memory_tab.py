"""The Inspect Memory tab (#140): the story's hot set from one ``GET /admin/stories/{id}/hot-set``
read (budgets, counts, pins, overflow episodes, recent retirements), its text markers, its links
into the Retrieval explorer (#141) and its section-level failures.

Runs against the real urlconf, where Memory and Retrieval (#141) are registered, so the links
into Retrieval render; ``tests.inspect.urls_without_retrieval`` checks they are omitted without
the Retrieval tab."""

import copy
import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.held import held_text
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, structured
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/inspect/memory/"
HOT_SET = "/admin/stories/{story_id}/hot-set"
HOT_SET_PATH = f"/admin/stories/{STORY}/hot-set"
MARA = "01JB2Z8Y5Q3W6K9M1N4P7R0T3A"  # the player
TOBIN = "01JB2Z8Y5Q3W6K9M1N4P7R0T3B"
STRANGER = "01JB2Z8Y5Q3W6K9M1N4P7R0T9Z"  # in counters, not in the cast
STORED_ONLY = "01JB2Z8Y5Q3W6K9M1N4P7R0T8Y"  # in stored_counters only
RETRIEVAL_URLS = "epistrel_console.urls"  # Retrieval registered for real (#141)
STORED_NOTE = "The Engine hasn't stored counts yet; its next commit recounts."
BROWSE_TEXT = (
    "The Engine can't list a story's memories yet (nathanpond/Epistrel#531). "
    "Retrieval shows the memories a question retrieves, filtered by kind and tier."
)
SETTINGS = "/admin/settings"
OMNISCIENT = {"viewpoint": "omniscient"}
TAG = '<span class="tag minor">minor</span>'
SCALED_TITLE = "max(1, \u230abudget \u00d7 fraction\u230b), as the Engine computes it"
WEIGHTS_NOTE = (
    "Minor characters&#x27; budgets can&#x27;t be shown (weights unavailable: {code}) \u2014 a "
    "minor&#x27;s row may read under budget while the Engine counts it over"
)
FRACTION_NOTE = (
    "Minor budgets need the deployment&#x27;s minor_hot_budget_fraction (unavailable: {code})"
)
UNAVAILABLE = "scaled (fraction unavailable)"
SECTIONS = ("Budgets", "Counts", "Pins", "Overflow episodes", "Recent retirements")


def inspect_fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return body


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"hot_set_{name}.json").read_text())
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
    fake.respond("GET", STORY_PATH, 200, json=story_json())
    fake.respond("GET", STORY_PATH, 200, json=inspect_fixture("story_omniscient"), query=OMNISCIENT)
    fake.respond("GET", SETTINGS, 200, json=engine_fixture("get_deployment_settings"))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> str:
    if body is not None:
        engine.respond("GET", HOT_SET, 200, json=body)
    response = client.get(URL)
    assert response.status_code == 200
    return response.content.decode()


def table(html: str, name: str) -> str:
    found = re.search(rf'<table class="{name}">(.*?)</table>', html, re.S)
    assert found is not None, name
    return found.group(1)


def rows(html: str, name: str, row_class: str) -> list[str]:
    return re.findall(rf'<tr class="{row_class}">(.*?)</tr>', table(html, name), re.S)


def cells(row: str) -> list[str]:
    return [re.sub(r"\s+", " ", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]


def text(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup)).strip()


def count_rows(html: str) -> dict[str, list[str]]:
    """Counts rows by their character cell's title (the full id) or text."""
    found: dict[str, list[str]] = {}
    for row in rows(html, "hot-set-counts", "count-row") + rows(
        html, "hot-set-counts", "count-story"
    ):
        row_cells = cells(row)
        title = re.search(r'title="([^"]+)"', row_cells[0])
        found[title.group(1) if title else text(row_cells[0])] = [text(c) for c in row_cells]
    return found


def links(html: str, css: str) -> list[str]:
    return [
        href.replace("&amp;", "&") for href in re.findall(rf'<a class="{css}" href="([^"]+)"', html)
    ]


def query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def hot_set_calls(engine: FakeEngine) -> int:
    return sum(1 for request in engine.requests if request.url.path == HOT_SET_PATH)


# --- the page -------------------------------------------------------------------------------------


def test_the_five_sections_render_in_order_from_one_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, engine, fixture("full"))
    assert hot_set_calls(engine) == 1
    as_of = html.index("As of turn <code>40</code> · revision <code>57</code>")
    positions = [html.index(f"<h2>{section}</h2>") for section in SECTIONS]
    assert as_of < positions[0]
    assert positions == sorted(positions)
    assert positions[-1] < html.index("<h2>Browse memories</h2>")
    assert "<title>Inspect · Memory · Harbour</title>" in html


def test_budgets_are_labelled_formatted_and_sourced(
    operator_client: Client, engine: FakeEngine
) -> None:
    budgets = table(page(operator_client, engine, fixture("full")), "hot-set-budgets")
    found = {
        text(c[0]): (c[1], text(c[2]))
        for c in (cells(r) for r in re.findall(r"<tr class=[^>]+>(.*?)</tr>", budgets, re.S))
    }
    found = {label.replace("&#x27;", "'"): value for label, value in found.items()}
    assert found == {
        "Hot memories per character": (
            "2,000",
            "story dial memory.hot_set.memories_per_character",
        ),
        "Hot vectors per story": ("50,000", "story dial memory.hot_set.vectors_per_story"),
        "Importance floor (retirable below)": (
            "0.150",
            "story dial memory.hot_set.importance_floor",
        ),
        "Retirement deadline after an overflow opens": (
            "10 turns",
            "story dial memory.hot_set.retirement_max_lag",
        ),
        "Keep vectors of retired memories": (
            '<span title="false">no</span>',
            "story dial memory.hot_set.retain_retired_vectors",
        ),
        "Keep vectors of memories off the active timeline": (
            '<span title="true">yes</span>',
            "story dial memory.hot_set.retain_dormant_vectors",
        ),
        "Minor characters' share": (
            "0.2 \u2192 400 per minor",
            "deployment setting minor_hot_budget_fraction",
        ),
        "Pin ceiling per character": ("5", "deployment ceiling"),
        "Pin ceiling per story (memory and text pins)": ("20", "deployment ceiling"),
    }
    assert "<code>memory.hot_set.importance_floor</code>" in budgets


# --- counts ---------------------------------------------------------------------------------------


def test_counts_rows_follow_the_cast_then_other_ids_sorted(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, engine, fixture("full"))
    assert list(count_rows(html)) == [MARA, TOBIN, STORED_ONLY, STRANGER, "Story vectors"]
    counts = table(html, "hot-set-counts")
    assert f'<span class="character" title="{MARA}">Mara (player)</span>' in counts
    assert f'<span class="character" title="{TOBIN}">Tobin</span>' in counts
    assert f'<code title="{STRANGER}">…{STRANGER[-8:]}</code>' in counts


def test_markers_appear_only_when_the_numbers_say_so(
    operator_client: Client, engine: FakeEngine
) -> None:
    found = count_rows(page(operator_client, engine, fixture("full")))
    assert found[MARA][1:4] == ["2,003 over budget", "2,000", "over by 3"]
    # Complement: exactly at budget is "0 left", not over budget.
    assert found[TOBIN][1:4] == ["2,000", "2,000", "0 left"]
    assert found["Story vectors"] == ["Story vectors", "12,345", "50,000", "37,655 left", "12,345"]


def test_a_stored_count_unlike_the_recount_differs(
    operator_client: Client, engine: FakeEngine
) -> None:
    found = count_rows(page(operator_client, engine, fixture("full")))
    assert found[MARA][4] == "2,003"  # equal: no "differs"
    assert found[TOBIN][4] == "1,998 differs (\u22122)"
    assert found[STORED_ONLY][1] == "0"
    assert found[STORED_ONLY][4] == "2 differs (+2)"
    assert found[STRANGER][4] == "0 differs (\u22124)"


def test_stored_counters_null_reads_not_stored_yet_and_never_differs(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, engine, fixture("stored_null"))
    found = count_rows(html)
    assert {row[4] for row in found.values()} == {"not stored yet"}
    assert STORED_ONLY not in found
    assert "differs" not in html
    assert STORED_NOTE in html


def test_the_stored_note_is_absent_when_counts_are_stored(
    operator_client: Client, engine: FakeEngine
) -> None:
    assert STORED_NOTE not in page(operator_client, engine, fixture("full"))


def test_a_cast_character_absent_from_counters_gets_a_zero_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, engine, fixture("empty"))
    found = count_rows(html)
    assert found[MARA][1:5] == ["0", "2,000", "2,000 left", "0"]
    assert found[TOBIN][1:5] == ["0", "2,000", "2,000 left", "0"]
    assert "No character holds hot memories." not in html


def test_no_character_rows_says_so_and_keeps_story_vectors(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=story_json(characters=[]))
    html = page(operator_client, engine, fixture("empty"))
    assert "No character holds hot memories." in html
    assert "No memory pins." in html
    assert list(count_rows(html)) == ["Story vectors"]


def test_a_failed_story_read_still_renders_the_page_with_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable"))
    html = page(operator_client, engine, fixture("full"))
    found = count_rows(html)
    assert list(found) == [*sorted([MARA, TOBIN, STORED_ONLY, STRANGER]), "Story vectors"]
    counts = table(html, "hot-set-counts")
    assert f'<code title="{MARA}">…{MARA[-8:]}</code>' in counts
    assert "Mara" not in counts


# --- pins -----------------------------------------------------------------------------------------


def pin_rows(html: str) -> dict[str, list[str]]:
    found = {}
    for row in rows(html, "hot-set-pins", "pin-row"):
        row_cells = cells(row)
        title = re.search(r'title="([^"]+)"', row_cells[0])
        assert title is not None
        found[title.group(1)] = [text(c) for c in row_cells]
    return found


def test_pins_against_their_ceilings(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, engine, fixture("full"))
    found = pin_rows(html)
    assert found[MARA] == ["Mara (player)", "5", "5", "at ceiling"]
    assert found[TOBIN] == ["Tobin", "2", "5", "—"]  # complement: below the ceiling, no marker
    story = text(table(html, "hot-set-story-pins"))
    assert "Memory pins 9" in story
    assert "Text pins (the story's pinned-memory texts) 3" in story
    assert "Total 12 / 20" in story
    assert "ceiling" not in story


def test_over_ceiling_on_a_character_and_on_the_story_total(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("full")
    body["pins"] = {"memory_pins": 26, "text_pins": 0, "by_character": {MARA: 6, TOBIN: 20}}
    html = page(operator_client, engine, body)
    assert pin_rows(html)[MARA][3] == "over ceiling"
    assert "Total 26 / 20 over ceiling" in text(table(html, "hot-set-story-pins"))


def test_the_story_total_at_its_ceiling(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("full")
    body["pins"] = {"memory_pins": 15, "text_pins": 5, "by_character": {MARA: 1}}
    html = page(operator_client, engine, body)
    assert "Total 20 / 20 at ceiling" in text(table(html, "hot-set-story-pins"))


def test_memory_pins_above_the_per_character_sum_get_a_story_level_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, engine, fixture("full"))
    (row,) = rows(html, "hot-set-pins", "pin-holderless")
    assert cells(row)[:2] == ["Story-level (no holder)", "2"]


def test_equal_sums_show_no_story_level_row(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("full")
    body["pins"]["memory_pins"] = 7
    html = page(operator_client, engine, body)
    assert "Story-level (no holder)" not in html


# --- overflow episodes ----------------------------------------------------------------------------


def episode_rows(html: str) -> list[list[str]]:
    return [[text(c) for c in cells(r)] for r in rows(html, "hot-set-episodes", "episode")]


def test_episodes_in_engine_order_with_due_and_remaining(
    operator_client: Client, engine: FakeEngine
) -> None:
    assert episode_rows(page(operator_client, engine, fixture("full"))) == [
        ["Story", "this story", "30", "40", "due", "12", "3"],  # deadline = head: due
        ["Character", "Mara (player)", "35", "45", "in 5 turns", "5", "3"],
        # Complement: deadline = head + 1 is not due.
        ["Character", "Tobin", "38", "41", "in 1 turn", "1", "1"],
    ]


def test_a_deadline_before_the_head_is_due_with_turns_past(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("full")
    body["open_episodes"][0]["deadline_turn"] = 38
    body["open_episodes"][1]["deadline_turn"] = 39
    found = episode_rows(page(operator_client, engine, body))
    assert found[0][4] == "due (2 turns past)"
    assert found[1][4] == "due (1 turn past)"


def test_an_unknown_scope_renders_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("full")
    body["open_episodes"][0].update(scope="faction", scope_id=STRANGER)
    episodes = table(page(operator_client, engine, body), "hot-set-episodes")
    assert "<code>faction</code>" in episodes
    assert f'<code title="{STRANGER}">…{STRANGER[-8:]}</code>' in episodes


def test_empty_episodes_and_retirements(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, engine, fixture("empty"))
    assert '<p class="episodes-none">None open.</p>' in html
    assert '<p class="retirements-none">None yet.</p>' in html
    assert 'class="hot-set-episodes"' not in html
    assert 'class="hot-set-retirements"' not in html
    assert 'class="legend reasons"' not in html


# --- recent retirements ---------------------------------------------------------------------------


def test_retirements_in_engine_order_with_reasons(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, engine, fixture("full"))
    found = [cells(r) for r in rows(html, "hot-set-retirements", "retirement")]
    assert [text(c[0]) for c in found] == [f"…4P7R0M0{n}" for n in range(1, 6)]
    assert [text(c[1]) for c in found] == [
        "Tobin",
        f"…{STRANGER[-8:]}",
        "story-level (counts toward story vectors only)",
        "Mara (player)",
        "story-level (counts toward story vectors only)",
    ]
    assert [c[2] for c in found] == [
        '<span title="floor">Floor</span>',
        '<span title="covered">Covered</span>',
        '<span title="uncovered">Uncovered</span>',
        "<code>mystery_reason</code>",
        "—",
    ]
    history = f"/stories/{STORY}/history/?before_turn=40#turn-39-0"
    assert found[0][3] == f'<a href="{history}">turn 39</a>'
    assert found[2][3] == "—"
    assert "2026-10-24 14:03:00 UTC" in found[0][4]
    assert found[2][4] == "—"
    found_legend = re.search(r'<dl class="legend reasons">(.*?)</dl>', html, re.S)
    assert found_legend is not None
    legend = text(found_legend.group(1))
    assert legend.startswith("Covered a consolidation output covers it")


def test_more_holds_the_story_id_and_episode_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, engine, fixture("full"))
    more = re.search(r'<details class="more"><summary>More</summary><pre>(.*?)</pre>', html, re.S)
    assert more is not None
    shown = json.loads(more.group(1).replace("&quot;", '"'))
    assert shown == {
        "story_id": STORY,
        "episodes": {
            STORY: "01JB2Z8Y5Q3W6K9M1N4P7R0E01",
            MARA: "01JB2Z8Y5Q3W6K9M1N4P7R0E02",
            TOBIN: "01JB2Z8Y5Q3W6K9M1N4P7R0E03",
        },
    }


# --- links into the Retrieval explorer ------------------------------------------------------------


def test_retirement_links_carry_actor_only_for_a_cast_holder(
    operator_client: Client, engine: FakeEngine
) -> None:
    with override_settings(ROOT_URLCONF=RETRIEVAL_URLS):
        html = page(operator_client, engine, fixture("full"))
    found = [query(url) for url in links(html, "memory-link")]
    base = {"tier": ["retired"], "include_quarantined": ["1"]}
    assert found == [
        {"memory": ["01JB2Z8Y5Q3W6K9M1N4P7R0M01"], **base, "actor": [TOBIN]},
        {"memory": ["01JB2Z8Y5Q3W6K9M1N4P7R0M02"], **base},  # not a cast member
        {"memory": ["01JB2Z8Y5Q3W6K9M1N4P7R0M03"], **base},  # story-level
        {"memory": ["01JB2Z8Y5Q3W6K9M1N4P7R0M04"], **base, "actor": [MARA]},
        {"memory": ["01JB2Z8Y5Q3W6K9M1N4P7R0M05"], **base},
    ]
    assert all(
        url.startswith(f"/stories/{STORY}/inspect/retrieval/?")
        for url in links(html, "memory-link")
    )


def test_retirement_links_omit_actor_when_the_story_read_failed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable"))
    with override_settings(ROOT_URLCONF=RETRIEVAL_URLS):
        html = page(operator_client, engine, fixture("full"))
    assert all("actor" not in query(url) for url in links(html, "memory-link"))
    assert len(links(html, "memory-link")) == 5


def browse_links(html: str) -> list[tuple[str, dict[str, list[str]]]]:
    grid = table(html, "browse-memories")
    return [
        (label, query(href.replace("&amp;", "&")))
        for href, label in re.findall(r'<a href="([^"]+)">([^<]+)</a>', grid)
    ]


def test_the_browse_grid_links_kind_by_tier(operator_client: Client, engine: FakeEngine) -> None:
    with override_settings(ROOT_URLCONF=RETRIEVAL_URLS):
        html = page(operator_client, engine, fixture("full"))
    assert BROWSE_TEXT in html
    assert browse_links(html) == [
        (f"{kind.capitalize()} · {tier}", {"kind": [kind], "tier": [tier]})
        for kind in ("episodic", "semantic", "reflection")
        for tier in ("hot", "retired")
    ]


def test_links_render_with_the_real_urlconf(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, engine, fixture("full"))
    assert len(browse_links(html)) == 6
    assert len(links(html, "memory-link")) == 5
    assert all(
        url.startswith(f"/stories/{STORY}/inspect/retrieval/?")
        for url in links(html, "memory-link")
    )


def test_links_are_omitted_without_the_retrieval_tab(
    operator_client: Client, engine: FakeEngine
) -> None:
    with override_settings(ROOT_URLCONF="tests.inspect.urls_without_retrieval"):
        html = page(operator_client, engine, fixture("full"))
    assert BROWSE_TEXT in html
    assert 'class="browse-memories"' not in html
    assert 'class="memory-link"' not in html
    assert f"…{'01JB2Z8Y5Q3W6K9M1N4P7R0M01'[-8:]}" in html  # the id still renders


# --- episodic off and failures --------------------------------------------------------------------


def test_episodic_false_shows_the_reason_inline_and_the_sections_still_render(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, engine, fixture("episodic_off"))
    notice = html.index('<p class="notice unavailable">Needs the <code>episodic</code> module')
    assert "<code>memory.episodic.enabled</code> is off" in html
    assert html.index("As of turn") < notice < html.index("<h2>Budgets</h2>")
    assert all(f"<h2>{section}</h2>" in html for section in SECTIONS)
    assert "configuration changed" not in html
    assert count_rows(html)[MARA][1] == "7"


def test_episodic_true_shows_no_inline_notice(operator_client: Client, engine: FakeEngine) -> None:
    assert "notice unavailable" not in page(operator_client, engine, fixture("full"))


def assert_sections_absent(html: str) -> None:
    assert "As of turn" not in html
    for section in SECTIONS:
        assert f"<h2>{section}</h2>" not in html
    assert '<table class="hot-set' not in html
    assert BROWSE_TEXT in html
    assert "<h2>Browse memories</h2>" in html


def test_not_found_replaces_the_sections(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", HOT_SET, 404, json=error_json("not_found", "gone"))
    with override_settings(ROOT_URLCONF=RETRIEVAL_URLS):
        html = page(operator_client, engine)
    assert "Not found (it may have been deleted or truncated)" in html
    assert_sections_absent(html)
    assert len(browse_links(html)) == 6
    assert 'aria-label="Inspect tabs"' in html


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (503, "ledger_unavailable"),
        (503, "database_unavailable"),
        (401, "unauthenticated"),
        (422, "validation_error"),
    ],
)
def test_other_errors_render_the_panel_in_place_of_the_sections(
    operator_client: Client, engine: FakeEngine, status: int, code: str
) -> None:
    engine.respond("GET", HOT_SET, status, json=error_json(code, "refused"))
    with override_settings(ROOT_URLCONF=RETRIEVAL_URLS):
        html = page(operator_client, engine)
    assert f"Engine error {status} {code}" in html
    assert "Not found (it may" not in html
    assert_sections_absent(html)
    assert len(browse_links(html)) == 6


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_unreachable_and_timeout_render_the_panel(
    operator_client: Client, engine: FakeEngine, exc: Exception
) -> None:
    engine.fail("GET", HOT_SET, exc)
    html = page(operator_client, engine)
    assert "Engine unreachable" in html
    assert_sections_absent(html)


def test_erasure_pending_is_the_held_wrapper(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        HOT_SET,
        503,
        json=error_json("erasure_pending", "held"),
        headers={"Retry-After": "30"},
    )
    response = operator_client.get(URL)
    assert response.status_code == 503
    html = response.content.decode()
    assert held_text("30") in html
    assert "As of turn" not in html


def test_a_greyed_memory_tab_makes_no_hot_set_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=structured(memory__episodic__enabled=False))
    html = page(operator_client, engine)
    assert "Not available for this story" in html
    assert hot_set_calls(engine) == 0


# --- minor characters' scaled budgets (#168) ---------------------------------------------------


def minor_body() -> dict[str, Any]:
    """20 per character; TOBIN (minor in ``story_omniscient_with_minor``) and MARA hold 5."""
    body = fixture("full")
    body["budgets"]["memories_per_character"] = 20
    body["counters"]["characters"] = {MARA: 5, TOBIN: 5}
    body["stored_counters"]["characters"] = {MARA: 5, TOBIN: 5}
    return body


def with_minor(engine: FakeEngine) -> None:
    omniscient = inspect_fixture("story_omniscient_with_minor")
    engine.respond("GET", STORY_PATH, 200, json=omniscient, query=OMNISCIENT)


def raw_count_rows(html: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for row in rows(html, "hot-set-counts", "count-row"):
        row_cells = cells(row)
        title = re.search(r'title="([^"]+)"', row_cells[0])
        assert title is not None
        found[title.group(1)] = row_cells
    return found


def omniscient_calls(engine: FakeEngine) -> list[Any]:
    return [
        r
        for r in engine.requests
        if r.url.path == f"/stories/{STORY}" and r.url.params.get("viewpoint") == "omniscient"
    ]


def settings_calls(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.url.path == SETTINGS]


def notes(html: str) -> list[str]:
    counts = html.index("<h2>Counts</h2>")
    return re.findall(r'<p class="note">(.*?)</p>', html[counts : html.index("<table", counts)])


def test_memory_tab_reads_weights_and_fraction(operator_client: Client, engine: FakeEngine) -> None:
    with_minor(engine)
    html = page(operator_client, engine, minor_body())

    order = [r.url.path for r in engine.requests]
    hot = order.index(HOT_SET_PATH)
    omniscient = engine.requests.index(omniscient_calls(engine)[0])
    assert hot < omniscient < engine.requests.index(settings_calls(engine)[0])
    assert len(omniscient_calls(engine)) == len(settings_calls(engine)) == 1
    found = raw_count_rows(html)
    tobin = found[TOBIN]
    assert TAG in tobin[0]
    assert "over budget" in tobin[1]
    assert tobin[2] == f'4 <small title="{SCALED_TITLE}">(minor: 20 \u00d7 0.2)</small>'
    assert tobin[3] == "over by 1"
    mara = found[MARA]
    assert TAG not in mara[0]
    assert (mara[2], mara[3]) == ("20", "15 left")
    assert "over budget" not in mara[1]
    assert notes(html) == []
    # Only the Counts row is tagged.
    assert html.count(TAG) == 1


def test_a_fraction_of_one_still_shows_the_scaled_cell(
    operator_client: Client, engine: FakeEngine
) -> None:
    with_minor(engine)
    settings = engine_fixture("get_deployment_settings")
    settings["minor_hot_budget_fraction"] = 1
    engine.respond("GET", SETTINGS, 200, json=settings)
    tobin = raw_count_rows(page(operator_client, engine, minor_body()))[TOBIN]
    assert TAG in tobin[0]
    assert tobin[2] == f'20 <small title="{SCALED_TITLE}">(minor: 20 \u00d7 1.0)</small>'
    assert tobin[3] == "15 left"


def test_failed_hot_set_read_makes_no_extra_reads(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: zero omniscient and settings reads when the hot-set read fails."""
    engine.respond("GET", HOT_SET, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, engine)
    assert omniscient_calls(engine) == []
    assert settings_calls(engine) == []
    assert '<p class="note">' not in html


def test_failed_omniscient_read_shows_note_and_unscaled_rows(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"), query=OMNISCIENT
    )
    html = page(operator_client, engine, minor_body())
    assert notes(html) == [WEIGHTS_NOTE.format(code="503 database_unavailable")]
    found = raw_count_rows(html)
    assert TAG not in html
    assert (found[TOBIN][2], found[TOBIN][3]) == ("20", "15 left")
    assert len(settings_calls(engine)) == 1  # still read: the Budgets row needs it
    budget = table(html, "hot-set-budgets")
    assert "0.2 \u2192 4 per minor" in budget


def test_failed_settings_read_marks_minor_rows_unavailable(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: no "over budget" marker on a minor row whose fraction is unknown, even over
    the unscaled budget."""
    with_minor(engine)
    engine.respond("GET", SETTINGS, 503, json=error_json("database_unavailable", "down"))
    body = minor_body()
    body["counters"]["characters"][TOBIN] = 25  # over even the unscaled 20
    html = page(operator_client, engine, body)
    tobin = raw_count_rows(html)[TOBIN]
    assert TAG in tobin[0]
    assert "over budget" not in tobin[1]
    assert (tobin[2], tobin[3]) == (UNAVAILABLE, UNAVAILABLE)
    assert "<small" not in tobin[2]
    assert notes(html) == [FRACTION_NOTE.format(code="503 database_unavailable")]
    share = rows(html, "hot-set-budgets", "budget-minor_hot_budget_fraction")
    assert cells(share[0])[1] == "\u2014"


def test_failed_settings_read_without_minors_shows_no_note(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SETTINGS, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, engine, minor_body())
    assert notes(html) == []
    share = rows(html, "hot-set-budgets", "budget-minor_hot_budget_fraction")
    assert cells(share[0])[1] == "\u2014"


def test_both_reads_failing_show_both_notes(operator_client: Client, engine: FakeEngine) -> None:
    """Weights unknown, so the story may have minors: the weights note, then the settings
    note."""
    engine.respond(
        "GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"), query=OMNISCIENT
    )
    engine.respond("GET", SETTINGS, 401, json=error_json("unauthenticated", "no"))
    html = page(operator_client, engine, minor_body())
    assert notes(html) == [
        WEIGHTS_NOTE.format(code="503 database_unavailable"),
        FRACTION_NOTE.format(code="401 unauthenticated"),
    ]
    assert TAG not in html
    found = raw_count_rows(html)
    assert (found[TOBIN][2], found[TOBIN][3]) == ("20", "15 left")


@pytest.mark.parametrize(
    ("status", "code", "headers"),
    [(503, "erasure_pending", {"Retry-After": "30"}), (404, "not_found", None)],
    ids=["erasure_pending", "not_found"],
)
def test_held_code_on_omniscient_read_degrades(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    headers: dict[str, str] | None,
) -> None:
    """Only the hot-set read escapes to the held wrapper; the omniscient read's held code (and
    404) degrade to the weights note."""
    engine.respond(
        "GET", STORY_PATH, status, json=error_json(code, "no"), headers=headers, query=OMNISCIENT
    )
    engine.respond("GET", HOT_SET, 200, json=minor_body())
    response = operator_client.get(URL)
    assert response.status_code == 200
    html = response.content.decode()
    assert held_text("30") not in html
    assert notes(html) == [WEIGHTS_NOTE.format(code=f"{status} {code}")]
    assert "As of turn" in html


def test_minor_share_row_links_engine_settings(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, engine, fixture("full"))
    [share] = rows(html, "hot-set-budgets", "budget-minor_hot_budget_fraction")
    label, value, source = cells(share)
    assert label == "Minor characters&#x27; share"
    assert value == "0.2 \u2192 400 per minor"
    assert source == (
        'deployment setting <a href="/engine/settings/"><code>minor_hot_budget_fraction</code></a>'
    )
    order = [
        text(cells(r)[0])
        for r in re.findall(r"<tr class=[^>]+>(.*?)</tr>", table(html, "hot-set-budgets"), re.S)
    ]
    assert order[:2] == ["Hot memories per character", "Minor characters&#x27; share"]
    # Only the share row links.
    assert table(html, "hot-set-budgets").count("<a ") == 1


def test_the_minor_tag_follows_a_bare_id_when_the_cast_read_failed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable"))
    with_minor(engine)
    tobin = raw_count_rows(page(operator_client, engine, minor_body()))[TOBIN]
    assert TAG in tobin[0]
    assert tobin[3] == "over by 1"

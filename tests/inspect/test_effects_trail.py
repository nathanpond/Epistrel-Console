"""The Effects tab's Effects trail section (#255).

Runs on the schema-validating fake Engine, under Structured v3 (#239) and #232's M6 cast
(``m6/story.json``). The history answers are keyed by query (C14):

- ``m6/history_trail_page1.json`` (no ``before_turn``): turns 5-7. Turn 5 has a committed ``gold``
  change and a ``held`` ``room_booked`` reservation; turn 6 none; turn 7 is ``incomplete`` with a
  ``pending`` ``bank_balance`` change dispatched as outbox ``01JM6BX…21``. ``next_before_turn: 5``.
- ``m6/history_trail_page2.json`` (``before_turn=5``): turns 1-4, one ``trust`` change on turn 2,
  no ``next_before_turn``.
"""

import copy
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.effects import INCOMPLETE_TEXT, INCOMPLETE_TITLE, OUTBOX_LIST_NOTICE
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.inspect import OMNISCIENT_TEXT
from console.inspect import history_turn_url, short_id
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED
from console.views import inspect as views
from console.views.inspect_effects_trail import (
    CURSOR_REFUSED,
    EMPTY_CURSOR_PAGE,
    EMPTY_TEXT,
    EMPTY_TRY_OLDER,
    NOT_FOUND,
    TRAIL_LIMIT,
    TRAIL_SCOPE_NOTE,
    count_text,
    read_cursor,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
URL = f"/stories/{STORY}/inspect/effects/trail/"
HISTORY = "/stories/{story_id}/history"
HISTORY_PATH = f"/stories/{STORY}/history"
OUTBOX = "01JM6BX0000000000000000021"
ALICE = "01JM6CH0000000000000000002"
MINUS = "\u2212"
NOTE = escape(TRAIL_SCOPE_NOTE)
SUBNAV = '<nav class="subtabs" aria-label="Effects sections">'


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
    fake.respond("GET", EFFECTIVE, 200, json=structured_v3())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond("GET", HISTORY, 200, json=fixture("history_trail_page1"))
    fake.respond(
        "GET", HISTORY, 200, json=fixture("history_trail_page2"), query={"before_turn": "5"}
    )
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def page(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == HISTORY_PATH]


def answer(engine: FakeEngine, status: int, body: dict[str, Any]) -> None:
    """The history read's answer; ``0`` stands for an unreachable Engine (the schema declares no
    500 on this route, so the fake refuses one: C15)."""
    if status == 0:
        engine.fail("GET", HISTORY, httpx.ConnectError("down"))
    else:
        engine.respond("GET", HISTORY, status, json=body)


def serve_raw(engine: FakeEngine, body: dict[str, Any]) -> None:
    """Serve a history answer the schema refuses (a malformed entry), unvalidated."""
    engine.side_effect("GET", HISTORY, lambda request: httpx.Response(200, json=body))


def block(html: str, turn: int, variant: int = 1) -> str:
    found = re.search(
        rf'<section class="trail-turn" id="trail-turn-{turn}-{variant}">(.*?)</section>', html, re.S
    )
    assert found is not None, turn
    return found.group(1)


def block_ids(html: str) -> list[str]:
    return re.findall(r'<section class="trail-turn" id="(trail-turn-[0-9]+-[0-9]+)">', html)


def test_turns_with_changes_listed(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert block_ids(html) == ["trail-turn-5-1", "trail-turn-7-1"]  # ascending, as served
    five = block(html, 5)
    href = history_turn_url(STORY, 5, 1).replace("&", "&amp;")
    assert f'<h3><a href="{href}">Turn 5 · variant 1</a></h3>' in five
    assert f"<td>{MINUS}5 (120 → 115)</td>" in five
    assert ">Committed</td>" in five
    assert ">Held</td>" in five
    assert ">Reservation</td>" in five
    assert "badge incomplete" not in five
    seven = block(html, 7)
    assert f'<a href="{history_turn_url(STORY, 7, 1)}">Turn 7 · variant 1</a>' in seven
    assert (
        f'<span class="badge incomplete" title="{INCOMPLETE_TITLE}">{INCOMPLETE_TEXT}</span>'
        in seven
    )
    assert ">Pending</td>" in seven
    assert ">Conditional</td>" in seven
    assert f'<code class="outbox-id">{OUTBOX}</code>' in seven
    notice = OUTBOX_LIST_NOTICE.split("{link}")[0]
    assert notice in html
    assert html.index(notice) < html.index('class="trail-turn"')
    # Complement: turn 6 committed nothing, so it has no block.
    assert "trail-turn-6-" not in html
    assert "Turn 6" not in html


def test_one_omniscient_history_read(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client)
    paths = [r.url.path for r in engine.requests]
    assert paths == [f"/stories/{STORY}/effective-config", f"/stories/{STORY}", HISTORY_PATH]
    params = dict(reads(engine)[0].url.params)
    assert params == {"viewpoint": "omniscient", "limit": str(TRAIL_LIMIT)}
    assert TRAIL_LIMIT == 200


def test_count_line(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert '<p class="trail-count">3 State changes on 2 turns on this page</p>' in html
    older = page(operator_client, "?before_turn=5")
    assert '<p class="trail-count">1 State change on 1 turn on this page</p>' in older
    last = fixture("history_trail_page2")
    engine.respond("GET", HISTORY, 200, json=last)
    single = page(operator_client)
    assert '<p class="trail-count">1 State change on 1 turn</p>' in single  # not paged


def test_count_text_wording() -> None:
    assert count_text(1, 1, on_page=False) == "1 State change on 1 turn"
    assert count_text(4, 3, on_page=True) == "4 State changes on 3 turns on this page"


def test_scope_note_always_shown(operator_client: Client, engine: FakeEngine) -> None:
    note = f'<p class="section-intro muted trail-scope">{NOTE}</p>'
    assert note in page(operator_client)
    empty = fixture("history_trail_page2")
    empty["turns"] = [t | {"state_effects": []} for t in empty["turns"]]
    engine.respond("GET", HISTORY, 200, json=empty)
    assert note in page(operator_client)
    answer(engine, 0, {})
    assert note in page(operator_client)


def test_older_and_newest_links(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert f'<a class="next-page" href="{URL}?before_turn=5">Older →</a>' in html
    assert "Newest page" not in html
    older = page(operator_client, "?before_turn=5")
    assert f'<a class="first-page" href="{URL}">Newest page</a>' in older
    assert "Older →" not in older  # page 2 has no next_before_turn
    assert block_ids(older) == ["trail-turn-2-1"]
    assert dict(reads(engine)[-1].url.params)["before_turn"] == "5"
    # **Complement** (#279): no row on page 2 carries an outbox id, so no D11 notice.
    assert OUTBOX_LIST_NOTICE.split("{link}")[0] not in older
    assert "outbox-notice" not in older


@pytest.mark.parametrize("typed", ["x", "-1", "+5", "٣"])
def test_bad_cursor_dropped_not_sent(
    operator_client: Client, engine: FakeEngine, typed: str
) -> None:
    html = page(operator_client, f"?before_turn={typed}")
    assert f'<p class="notice cursor-dropped">{CURSOR_DROPPED}</p>' in html
    assert html.index(CURSOR_DROPPED) < html.index(NOTE)
    assert block_ids(html) == ["trail-turn-5-1", "trail-turn-7-1"]  # the first page
    assert "Newest page" not in html
    # Complement: the dropped value is never sent.
    assert [r for r in reads(engine) if "before_turn" in r.url.params] == []


def test_cursor_rule() -> None:
    assert read_cursor(None) == (None, False)
    assert read_cursor("") == (None, False)
    assert read_cursor("007") == (7, False)
    assert read_cursor("٣") == (None, True)


def test_empty_states(operator_client: Client, engine: FakeEngine) -> None:
    none = fixture("history_trail_page2")
    none["turns"] = [{k: v for k, v in t.items() if k != "state_effects"} for t in none["turns"]]
    engine.respond("GET", HISTORY, 200, json=none)
    html = page(operator_client)
    assert f'<p class="none">{EMPTY_TEXT}</p>' in html
    assert 'class="trail-count"' not in html
    assert "Older" not in html

    older = copy.deepcopy(none)
    older["next_before_turn"] = 1
    engine.respond("GET", HISTORY, 200, json=older)
    html = page(operator_client)
    assert f'<p class="none">{EMPTY_TRY_OLDER}</p>' in html
    assert f'href="{URL}?before_turn=1">Older →</a>' in html

    engine.respond("GET", HISTORY, 200, json=none, query={"before_turn": "3"})
    html = page(operator_client, "?before_turn=3")
    assert f'<p class="none">{EMPTY_CURSOR_PAGE}</p>' in html
    assert f'<a class="first-page" href="{URL}">Newest page</a>' in html


def test_greyed_section_makes_no_history_read(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=structured_v3(states__level="prompt_only"))
    html = page(operator_client)
    assert '<p class="notice unavailable">Needs <code>states.level: on</code>' in html
    assert SUBNAV in html
    assert NOTE not in html
    # Complement: no history read at all.
    assert [r for r in engine.requests if r.url.path.endswith("/history")] == []


def test_lite_m6_greys_with_the_pipeline_clause(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6(**{"states.level": "prompt_only"}))
    html = page(operator_client)
    assert "which needs the structured pipeline" in html
    assert "<code>pipeline: structured</code>" in html
    assert reads(engine) == []


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (403, error_json("omniscient_required", "omniscient"), OMNISCIENT_TEXT),
        (404, error_json("story_not_found", "gone"), NOT_FOUND),
        (0, {}, 'class="engine-error"'),  # unreachable: #43's panel
        (503, error_json("database_unavailable", "down"), 'class="engine-error"'),
        (422, error_json("validation_error", "bad"), 'class="engine-error"'),  # no cursor sent
    ],
)
def test_errors_stay_in_section(
    operator_client: Client, engine: FakeEngine, status: int, body: dict[str, Any], expected: str
) -> None:
    answer(engine, status, body)
    html = page(operator_client)
    assert expected in html
    assert SUBNAV in html
    assert NOTE in html
    assert html.index(SUBNAV) < html.index(expected)
    assert 'class="trail-turn"' not in html


def test_refused_cursor_offers_newest_page(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", HISTORY, 422, json=error_json("validation_error", "bad"), query={"before_turn": "99"}
    )
    html = page(operator_client, "?before_turn=99")
    assert CURSOR_REFUSED in html
    assert f'<a class="first-page" href="{URL}">Newest page</a>' in html
    assert 'class="engine-error"' not in html
    assert len(reads(engine)) == 1  # no second read


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", HISTORY, 503, json=error_json("erasure_pending", "held"))
    response = operator_client.get(URL)
    assert response.status_code == 503
    assert NOTE not in response.content.decode()


def test_module_off_greys(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", HISTORY, 409, json=error_json("module_off", "off", module="states"))
    html = page(operator_client)
    assert '<div class="panel unavailable">' in html
    assert NOTE not in html


def test_malformed_effect_kept_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("history_trail_page1")
    five = next(t for t in body["turns"] if t["turn"] == 5)
    del five["state_effects"][1]["status"]
    serve_raw(engine, body)
    html = page(operator_client)
    five_html = block(html, 5)
    assert five_html.count("<tr><td>") == 1  # the gold row only
    assert '<p class="warning">1 State change couldn&#x27;t be read</p>' in five_html
    assert '<details class="more"><summary>More</summary><pre>' in five_html
    assert "&quot;room_booked&quot;" in five_html
    # Complement: the other block renders, and the raw entry still counts.
    assert f'<code class="outbox-id">{OUTBOX}</code>' in block(html, 7)
    assert "3 State changes on 2 turns on this page" in html


def test_outbox_id_only_in_a_raw_entry_gives_no_notice(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement** (#279): an ``outbox_id`` inside an unparsed raw entry doesn't trigger
    D11's notice; only a parsed row's does."""
    body = fixture("history_trail_page1")
    seven = next(t for t in body["turns"] if t["turn"] == 7)
    assert seven["state_effects"][0]["outbox_id"] == OUTBOX
    del seven["state_effects"][0]["status"]
    serve_raw(engine, body)
    html = page(operator_client)
    assert '<p class="warning">1 State change couldn&#x27;t be read</p>' in block(html, 7)
    assert '<code class="outbox-id">' not in html
    assert OUTBOX_LIST_NOTICE.split("{link}")[0] not in html
    assert "outbox-notice" not in html


def test_non_list_field_still_gets_a_block(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("history_trail_page2")
    two = next(t for t in body["turns"] if t["turn"] == 2)
    two["state_effects"] = {"not": "a list"}
    serve_raw(engine, body)
    html = page(operator_client)
    assert '<p class="warning">1 State change couldn&#x27;t be read</p>' in block(html, 2)
    assert "1 State change on 1 turn" in html


def test_cast_unavailable_short_ids(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", STORY_PATH, httpx.ConnectError("down"))
    html = page(operator_client, "?before_turn=5")
    assert f'<p class="notice cast-unavailable">{CAST_UNAVAILABLE}</p>' in html
    two = block(html, 2)
    assert f"<tr><td>trust</td><td>{short_id(ALICE)}</td>" in two
    assert len(reads(engine)) == 1  # the history read is still made


def test_cast_names_label_scopes(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?before_turn=5")
    assert CAST_UNAVAILABLE not in html
    assert "<tr><td>trust</td><td>Alice</td>" in block(html, 2)

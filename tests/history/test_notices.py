"""History shows each turn's co-author notices under its header (#185).

``m6/history_notices.json`` is an omniscient read of turns 2 and 3 of the M6 story (D16 cast;
the story's head is turn 3, ``next_turn_index`` 4): turn 2 carries one correctable notice, and
turn 3 one correctable notice and one ``correctable: false``. Each notice turn carries its input,
as the Engine returns notices only then (``core/history.py`` ``_notices``). Fix / Clarify links
#263's ``stories:correct``, stubbed by D19's shared urlconf.
"""

import copy
import json
import re
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from django.test import Client, override_settings
from django.utils.html import escape

from console.notices import (
    CHAIN_REASON,
    NOT_CORRECTABLE_REASON,
    NOT_LATEST_REASON,
    NOTICES_OVERRIDDEN_KEY,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.history.helpers import answer
from tests.inspect.helpers import FIXTURES, structured_v3

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
TURN2 = "01JM6NT0000000000000000101"
HEAD = "01JM6NT0000000000000000102"
WRITTEN = "01JM6NT0000000000000000103"
URL = f"/stories/{STORY}/history/"
STUB_URLS = "tests.urls_with_engine_operation"
PREDICATES = "/stories/{story_id}/predicates"
EFFECTIVE = "/stories/{story_id}/effective-config"


def notices_history() -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m6/history_notices"))
    return body


def serve(engine: FakeEngine, history: dict[str, Any] | None = None) -> None:
    answer(engine, history=history or notices_history(), story=engine_fixture("m6/story"))
    predicates = json.loads((FIXTURES / "predicates_list.json").read_text())
    engine.respond("GET", PREDICATES, 200, json=predicates)
    engine.side_effect("GET", EFFECTIVE, lambda request: httpx.Response(200, json=structured_v3()))


def get(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}?{query}" if query else URL)
    assert response.status_code == 200
    return response.content.decode()


def card_of(html: str, notice_id: str) -> str:
    match = re.search(
        rf'<article class="notice-card" id="notice-{notice_id}">.*?</article>', html, re.S
    )
    assert match, f"no card for {notice_id}"
    return match.group(0)


def greyed(reason: str) -> str:
    return (
        f'<span class="disabled" aria-disabled="true" title="{escape(reason)}">Fix / Clarify</span>'
    )


def notices_row(html: str, turn: int) -> str:
    start = html.index(f'<tbody class="turn" id="turn-{turn}-1">')
    body = html[start : html.index("</tbody>", start)]
    match = re.search(r'<tr class="notices"><td colspan="7">.*?</td></tr>', body, re.S)
    assert match, f"no notices row for turn {turn}"
    return match.group(0)


@override_settings(ROOT_URLCONF=STUB_URLS)
def test_head_turn_offers_fix(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = get(operator_client)
    head = notices_row(html, 3)
    card = card_of(head, HEAD)
    assert "Implausible knowledge — Alice had no way to know: “the key was in the drawer”" in card
    query = urlencode({"next": URL})
    assert f'<a class="button" href="/stories/{STORY}/correct/3/?{query}">Fix / Clarify</a>' in (
        card
    )
    assert f'name="next" value="{URL}"' in card
    # **Complement:** turn 2 isn't the latest, and the correction-written card is informational.
    turn2 = card_of(notices_row(html, 2), TURN2)
    assert greyed(NOT_LATEST_REASON) in turn2
    assert 'class="button"' not in turn2
    written = card_of(head, WRITTEN)
    assert "Bob had no way to know: “the stranger is called Tamsin”" in written
    assert greyed(NOT_CORRECTABLE_REASON) in written
    # Every card offers Override.
    assert html.count('<button type="submit">Override</button>') == 3


@override_settings(ROOT_URLCONF=STUB_URLS)
def test_notices_follow_the_state_changes_row(operator_client: Client, engine: FakeEngine) -> None:
    history = notices_history()
    history["turns"][1]["state_effects"] = engine_fixture("m6/history_world")["turns"][0][
        "state_effects"
    ][:1]
    serve(engine, history)
    html = get(operator_client)
    start = html.index('<tbody class="turn" id="turn-3-1">')
    assert start < html.index('<tr class="state-changes">', start)
    assert html.index('<tr class="state-changes">', start) < html.index(
        '<tr class="notices">', start
    )
    assert html.index('<tr class="notices">', start) < html.index('<tr class="item', start)


@override_settings(ROOT_URLCONF=STUB_URLS)
def test_chain_view_greys_fix(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = get(operator_client, "variant=3:2")
    assert html.count(greyed(CHAIN_REASON)) == 2
    assert greyed(NOT_CORRECTABLE_REASON) in card_of(html, WRITTEN)  # not correctable first
    assert "/correct/" not in html


def test_overridden_in_history(operator_client: Client, engine: FakeEngine) -> None:
    session = operator_client.session
    session[NOTICES_OVERRIDDEN_KEY] = [TURN2]
    session.save()
    serve(engine)
    html = get(operator_client)
    row = notices_row(html, 2)
    assert (
        f'<p class="notice-overridden" id="notice-{TURN2}">Overridden this session — Alice: '
        "“Bob left the lantern in the cellar”</p>"
    ) in row
    assert '<article class="notice-card"' not in row
    # The others still show in full; no Fix control without stories:correct (#64).
    assert card_of(html, HEAD)
    assert "Fix / Clarify" not in html


def test_history_without_notices_unchanged(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, history=engine_fixture("get_history"))
    html = get(operator_client)
    assert '<section class="notices"' not in html
    assert '<tr class="notices">' not in html
    assert '<article class="notice-card"' not in html

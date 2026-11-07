"""History's "Edit this line…" link (#305): which rows carry it, where it points, that the
as-recorded view and a chain view offer none, that it costs no Engine read, and that without
``stories:line-edit`` the items render as on main (D19, E24)."""

import copy
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from console.engine.models import History, HistoryItem
from console.history import line_edit_url, turn_groups
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.history.helpers import (
    HISTORY,
    STORY,
    STORY_PATH,
    URL,
    VARIANTS,
    answer,
    history_json,
    page,
    variants_json,
)
from tests.history.test_state_changes import GOLDEN_MAIN, items_fragment

pytestmark = pytest.mark.django_db

WITHOUT = "tests.urls_without_line_edit"
EDIT_LINK = re.compile(r'<a class="edit-line" href="([^"]+)">Edit this line…</a>')
TURN2_REPLY = "01M5R5XK89ZEXJVVY162N1611M"  # get_history_audit's turn 2 message_rendered
M7_STORY = "01JM7ST0000000000000000001"
INSPECT = Path(__file__).parents[1] / "fixtures" / "inspect"
PREDICATES = "/stories/{story_id}/predicates"


def links(html: str) -> list[dict[str, list[str]]]:
    return [parse_qs(urlsplit(h.replace("&amp;", "&")).query) for h in EDIT_LINK.findall(html)]


def raw_item(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m7/history_as_recorded")["items"][0])
    body.update(changes)
    return body


def item(**changes: Any) -> HistoryItem:
    parsed = HistoryItem.from_json(raw_item(**changes))
    assert parsed is not None
    return parsed


def test_rows_link_edit_this_line(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client)
    found = links(html)
    by_event = {q["event_id"][0]: q for q in found}
    assert TURN2_REPLY in by_event
    query = by_event[TURN2_REPLY]
    assert query["turn"] == ["2"]
    assert query["next"] == [URL]
    hrefs = EDIT_LINK.findall(html)
    assert all(h.startswith(reverse("stories:line-edit", args=[STORY]) + "?") for h in hrefs)
    # The link is the last thing in its Text cell.
    assert re.search(r"Edit this line…</a></td>\s*<td>", html)


def test_next_is_the_full_history_url(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client, "viewpoint=omniscient&include_audit=1")
    assert {q["next"][0] for q in links(html)} == {f"{URL}?viewpoint=omniscient&include_audit=1"}


NOT_EDITABLE = {
    "audit": {"channel": "audit", "kind": "guardrail_output_rejected"},
    "erasure marker": {"kind": "erasure_marker"},
    "redacted": {"redacted": True},
    "quarantined": {"quarantined": True},
    "turn 0": {"turn": 0},
    "no turn": {"turn": None},
    "non-ULID message id": {"message_id": "msg-1"},
}


@pytest.mark.parametrize("changes", NOT_EDITABLE.values(), ids=NOT_EDITABLE.keys())
def test_no_link_where_not_editable(changes: dict[str, Any]) -> None:
    assert line_edit_url(M7_STORY, item(), chain=None, next_url="/h/") is not None  # baseline
    assert line_edit_url(M7_STORY, item(**changes), chain=None, next_url="/h/") is None


def test_no_link_on_a_chain_or_as_recorded() -> None:
    assert line_edit_url(M7_STORY, item(), chain="3:2") is None
    assert line_edit_url(M7_STORY, item(), chain=None, view="as_recorded") is None
    assert line_edit_url(M7_STORY, item(), chain=None, view="corrected") is not None


def test_no_link_on_the_second_row_of_a_shared_message() -> None:
    say = raw_item()
    do = raw_item(channel="do", beat=2, text="Points at the ledger.")
    history = History.from_json(
        {**engine_fixture("m7/history_as_recorded"), "items": [say, do], "turns": []}
    )
    assert history is not None
    rows = turn_groups(
        history,
        history.entries,
        [],
        labels={},
        links={},
        actor_filtered=False,
        story_id=M7_STORY,
        next_url="/h/",
    )[0].rows
    assert [r.same_message for r in rows] == [False, True]
    assert rows[0].edit_url is not None
    assert rows[1].edit_url is None
    # Complement: a caller that passes no next_url (Play's transcript) gets no link at all.
    plain = turn_groups(
        history, history.entries, [], labels={}, links={}, actor_filtered=False, story_id=M7_STORY
    )[0].rows
    assert [r.edit_url for r in plain] == [None, None]


def test_no_link_on_a_chain_view(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client, "variant=3:2")
    assert 'class="item' in html
    assert "edit-line" not in html
    assert "Edit this line…" not in html


def _m7_reads(engine: FakeEngine, history: dict[str, Any]) -> None:
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("m7/story"))
    engine.respond("GET", VARIANTS, 200, json=variants_json())
    engine.respond("GET", HISTORY, 200, json=history)
    engine.respond(
        "GET", PREDICATES, 200, json=json.loads((INSPECT / "predicates_list.json").read_text())
    )


def test_no_link_on_as_recorded_view(operator_client: Client, engine: FakeEngine) -> None:
    recorded = engine_fixture("m7/history_as_recorded")
    # The rows would otherwise qualify: active, ULID message ids, turns ≥ 1, none erased.
    assert all(i["turn"] >= 1 and not i.get("redacted") for i in recorded["items"])
    _m7_reads(engine, recorded)
    html = page(operator_client, "view=as_recorded", story_id=M7_STORY)
    assert 'class="history-header as-recorded"' in html
    assert "edit-line" not in html
    assert "Edit this line…" not in html
    # Complement: the same story's corrected view links those rows.
    corrected = copy.deepcopy(recorded)
    corrected["view"] = "corrected"
    for key in ("boundary_tx", "erasure_markers", "derived"):
        corrected.pop(key)
    engine.respond("GET", HISTORY, 200, json=corrected)
    corrected_html = page(operator_client, story_id=M7_STORY)
    assert {q["event_id"][0] for q in links(corrected_html)} == {
        i["message_id"] for i in recorded["items"]
    }


def _calls(engine: FakeEngine) -> list[tuple[str, str, str]]:
    return [(r.method, r.url.path, r.url.query.decode()) for r in engine.requests]


def test_history_makes_no_extra_read(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    page(operator_client, "viewpoint=omniscient&include_audit=1")
    linked = _calls(engine)
    engine.requests.clear()
    with override_settings(ROOT_URLCONF=WITHOUT):
        page(operator_client, "viewpoint=omniscient&include_audit=1")
    assert _calls(engine) == linked


def test_without_the_name_the_markup_is_unchanged(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, history=engine_fixture("get_history"))
    with override_settings(ROOT_URLCONF=WITHOUT):
        html = page(operator_client)
    assert "edit-line" not in html
    assert items_fragment(html) == GOLDEN_MAIN.read_text()
    # Complement: under the real urlconf the same page links its rows.
    assert EDIT_LINK.search(page(operator_client))


def test_history_fixture_holds_editable_rows() -> None:
    """The real-urlconf assertions above rest on ULID message ids in #52's fixture."""
    assert TURN2_REPLY in {i["message_id"] for i in history_json()["items"]}

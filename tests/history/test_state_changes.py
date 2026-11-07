"""History shows each turn's State changes and incomplete turns (#254).

Page tests run the history browser against the schema-validating fake Engine with the M6 cast
(``m6/story.json``: Mara the player, Alice, Bob; D16). ``m6/history_world.json`` is an
omniscient read of turns 3 and 4 (shaped from the Engine's ``core/history.py`` at ``b8fd182``,
C15): turn 3's beat 2 spent gold and held a room, beat 3 changed Alice's hidden ``trust``, and
the turn's ``state_effects`` is the union of the two beat lists without ``beat``; turn 4 is a
staged turn still waiting on its outcome (``incomplete``, ``message: null``, its input only).
``m6/history_world_alice.json`` is Alice's read of the same turns: no ``trust`` change, and no
beat ``effects`` (the Engine leaves the key out of a character read's items).
"""

import copy
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.test import Client, override_settings

from console.effects import INCOMPLETE_TITLE, OUTBOX_LIST_REQUEST
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.history.helpers import HISTORY, answer, page
from tests.inspect.helpers import FIXTURES, structured_v3

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
OP4 = "01JM6QP0000000000000000004"
PREDICATES = "/stories/{story_id}/predicates"
EFFECTIVE = "/stories/{story_id}/effective-config"
# Main's M5 render, before #305's "Edit this line…" (kept as the D19 baseline that
# tests/history/test_line_edit_link.py compares under a urlconf without ``stories:line-edit``),
# and the same render under the real urlconf once #305 registered the name (E24).
GOLDEN_MAIN = Path(__file__).parent / "golden" / "m5_items.html"
GOLDEN = Path(__file__).parent / "golden" / "m5_items_linked.html"
STUB_URLS = "tests.urls_with_engine_operation"
WITHOUT_ENGINE_OPERATION = "tests.urls_without_engine_operation"
OUTBOX = "01JM6BX0000000000000000021"
OUTBOX_NOTICE = "Outbox rows are shown where the Engine reports them"
VIEWPOINT_LINE = "Viewpoint: Alice — hidden States are left out"
MINUS = "\u2212"  # D5's signed delta


def world(name: str = "history_world") -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m6/{name}"))
    return body


def predicates() -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / "predicates_list.json").read_text())
    return body


def serve(engine: FakeEngine, history: dict[str, Any], *, validated: bool = True) -> None:
    """The M6 story's page reads; an unvalidated history (``validated=False``) is served raw
    for the shapes the schema refuses (a change missing ``status``)."""
    answer(engine, history=history if validated else None, story=engine_fixture("m6/story"))
    if not validated:
        engine.side_effect("GET", HISTORY, lambda request: httpx.Response(200, json=history))
    engine.respond("GET", PREDICATES, 200, json=predicates())
    engine.side_effect("GET", EFFECTIVE, lambda request: httpx.Response(200, json=structured_v3()))


def world_page(client: Client, engine: FakeEngine, history: dict[str, Any], **kw: Any) -> str:
    serve(engine, history, **kw)
    return page(client, story_id=STORY)


def turn_body(html: str, turn: int) -> str:
    start = html.index(f'<tbody class="turn" id="turn-{turn}-1">')
    end = html.find('<tbody class="turn"', start + 1)
    return html[start : end if end != -1 else html.index('<p class="paging">', start)]


def state_rows(html: str) -> list[list[str]]:
    """Each ``tr.state-changes`` table's body rows, as their cells' text."""
    found = []
    for table in re.findall(r'<tr class="state-changes">(.*?)</table>', html, re.S):
        for row in re.findall(r"<tr><td>(.*?)</td></tr>", table, re.S):
            found.append([re.sub(r"<[^>]+>", "", c) for c in re.split(r"</td><td[^>]*>", row)])
    return found


def beat_effects(html: str) -> list[str]:
    return re.findall(r'<details class="beat-effects">.*?</details>', html, re.S)


def items_fragment(html: str) -> str:
    """The header line, the notices above the table and the table (the golden's span)."""
    start = html.index('<p class="history-header">')
    end = html.index("</table>", start) + len("</table>")
    return re.sub(
        r'name="csrfmiddlewaretoken" value="[^"]*"',
        'name="csrfmiddlewaretoken" value=""',
        html[start:end],
    )


def test_turn_state_changes_row(operator_client: Client, engine: FakeEngine) -> None:
    html = world_page(operator_client, engine, world())
    assert html.count('<tr class="state-changes">') == 1
    assert '<tr class="state-changes">' in turn_body(html, 3)
    assert "<caption>State changes</caption>" in turn_body(html, 3)
    rows = state_rows(html)
    assert [r[:6] for r in rows] == [
        ["gold", "—", f"{MINUS}5 (120 → 115)", "Committed", "", "paid for the lantern"],
        ["room_booked", "—", "false → true", "Held", "Reservation", "booked a room at the inn"],
        ["trust", "Alice", "2 → 1", "Committed", "", "Alice saw the forged ledger"],
    ]
    assert '<td title="held">Held</td><td title="reservation">Reservation</td>' in html


def test_beat_effects_on_omniscient_only(operator_client: Client, engine: FakeEngine) -> None:
    html = world_page(operator_client, engine, world())
    found = beat_effects(turn_body(html, 3))
    assert len(found) == 2
    assert "<summary>2 State changes at this beat</summary>" in found[0]
    assert f"gold · — · {MINUS}5 (120 → 115) · " in found[0]
    assert (
        '<span title="held">Held</span> · <span title="reservation">Reservation</span>'
        in (found[0])
    )
    assert "<summary>1 State change at this beat</summary>" in found[1]
    assert "trust · Alice · 2 → 1 · " in found[1]
    assert "open" not in found[0].split(">", 1)[0]  # closed by default

    engine.reset()
    alice = world_page(operator_client, engine, world("history_world_alice"))
    assert "beat-effects" not in alice
    assert "trust" not in alice


def test_beat_effects_on_a_character_read_are_not_shown(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement** (#278): Alice's read with the omniscient read's beat ``effects`` injected
    (off-contract, served raw) still shows no per-beat changes, so ``trust`` stays hidden."""
    alice = world("history_world_alice")
    effects = {
        (item["turn"], item["beat"]): item["effects"]
        for item in world()["items"]
        if item.get("effects")
    }
    assert "trust" in json.dumps(list(effects.values()))
    assert len(effects) == 2
    for item in alice["items"]:
        if (item["turn"], item["beat"]) in effects:
            item["effects"] = effects[item["turn"], item["beat"]]

    html = world_page(operator_client, engine, alice, validated=False)
    assert "beat-effects" not in html
    assert "trust" not in html
    assert len(state_rows(html)) == 2  # the turn-level rows still render


def test_character_read_names_hidden_states(operator_client: Client, engine: FakeEngine) -> None:
    alice = world_page(operator_client, engine, world("history_world_alice"))
    assert alice.count(VIEWPOINT_LINE) == 1
    assert f'<p class="notice">{VIEWPOINT_LINE}</p>\n\n<table class="history">' in alice
    assert len(state_rows(alice)) == 2

    omniscient = world_page(operator_client, engine, world())
    assert "hidden States are left out" not in omniscient


def test_incomplete_badge_links_once_registered(
    operator_client: Client, engine: FakeEngine
) -> None:
    with override_settings(ROOT_URLCONF=STUB_URLS):
        html = world_page(operator_client, engine, world())
    header = turn_body(html, 4).split("</tr>", 1)[0]
    badge = f'<span class="badge incomplete" title="{INCOMPLETE_TITLE}">Incomplete</span>'
    assert f'Turn 4 · variant 1 <a href="/operations/engine/{OP4}/">{badge}</a>' in header

    with override_settings(ROOT_URLCONF=WITHOUT_ENGINE_OPERATION):
        plain = world_page(operator_client, engine, world())
    header = turn_body(plain, 4).split("</tr>", 1)[0]
    assert f"Turn 4 · variant 1 {badge}" in header
    assert "<a href" not in header.split("Incomplete")[0]


def test_incomplete_turn_badged_not_no_reply(operator_client: Client, engine: FakeEngine) -> None:
    html = world_page(operator_client, engine, world())
    turn4 = turn_body(html, 4)
    assert 'class="badge incomplete"' in turn4
    assert "no-reply" not in turn4
    assert "badge incomplete" not in turn_body(html, 3)

    engine.reset()
    answer(engine, history=engine_fixture("get_history_ingested"))
    ingested = page(operator_client)
    assert 'class="no-reply"' in ingested
    assert "badge incomplete" not in ingested


def test_malformed_effect_kept_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = world()
    broken = {k: v for k, v in body["turns"][0]["state_effects"][0].items() if k != "status"}
    body["turns"][0]["state_effects"][0] = broken
    body["items"][1]["effects"].append({"state": "gold"})
    html = world_page(operator_client, engine, body, validated=False)
    turn3 = turn_body(html, 3)
    assert [r[0] for r in state_rows(html)] == ["room_booked", "trust"]
    assert '<p class="warning">1 State change couldn&#x27;t be read</p>' in turn3
    assert (
        "&quot;reason&quot;: &quot;paid for the lantern&quot;" in turn3
    )  # the raw entry, pretty JSON
    beat2 = beat_effects(turn3)[0]
    assert "<summary>2 State changes at this beat</summary>" in beat2
    assert '<p class="warning">1 State change couldn&#x27;t be read</p>' in beat2
    assert "<pre>{\n  &quot;state&quot;: &quot;gold&quot;\n}</pre>" in beat2
    # The page renders and turn 4 is as before.
    assert 'class="badge incomplete"' in turn_body(html, 4)
    assert "couldn&#x27;t be read" not in turn_body(html, 4)


def test_beat_with_only_bad_entries(operator_client: Client, engine: FakeEngine) -> None:
    body = world()
    body["items"][2]["effects"] = [{"state": "trust"}]
    html = world_page(operator_client, engine, body, validated=False)
    beat3 = beat_effects(turn_body(html, 3))[1]
    assert "<summary>0 State changes at this beat · 1 couldn&#x27;t be read</summary>" in beat3
    assert "<ul>" not in beat3


def test_turn_with_only_bad_entries_has_no_table(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = world()
    body["turns"][0]["state_effects"] = [{"state": "gold"}, {"state": "trust"}]
    html = world_page(operator_client, engine, body, validated=False)
    row = re.search(r'<tr class="state-changes">.*?</td></tr>', html, re.S)
    assert row is not None
    assert "<table" not in row.group(0)
    assert '<p class="warning">2 State changes couldn&#x27;t be read</p>' in row.group(0)


def test_outbox_notice_only_with_an_outbox_id(operator_client: Client, engine: FakeEngine) -> None:
    body = world()
    body["turns"][0]["state_effects"][0]["outbox_id"] = OUTBOX
    html = world_page(operator_client, engine, body)
    assert html.count(OUTBOX_NOTICE) == 1
    assert f'<a href="{OUTBOX_LIST_REQUEST}">' in html
    assert f'<code class="outbox-id">{OUTBOX}</code>' in html

    engine.reset()
    plain = world_page(operator_client, engine, world())
    assert OUTBOX_NOTICE not in plain


def test_no_extra_reads(operator_client: Client, engine: FakeEngine) -> None:
    world_page(operator_client, engine, world())
    calls = [(r.method, r.url.path) for r in engine.requests]
    assert calls == [
        ("GET", f"/stories/{STORY}"),
        ("GET", f"/stories/{STORY}/variants"),
        ("GET", f"/stories/{STORY}/history"),
        ("GET", f"/stories/{STORY}/predicates"),
    ]
    assert not any(p.endswith(("/states", "/reconciliation")) for _, p in calls)


def test_chain_view_shows_the_read_as_returned(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, world())
    html = page(operator_client, "variant=1", story_id=STORY)
    assert [r[0] for r in state_rows(html)] == ["gold", "room_booked", "trust"]


def test_m5_page_unchanged(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, history=engine_fixture("get_history"))
    html = page(operator_client)
    for marker in ("state-changes", "beat-effects", "badge incomplete"):
        assert marker not in html
    assert items_fragment(html) == GOLDEN.read_text()


def test_alice_read_has_no_beat_effects_key() -> None:
    """C15: the fixture follows the Engine (a character read's items carry no ``effects``)."""
    assert all("effects" not in item for item in world("history_world_alice")["items"])
    assert {e["state"] for e in world("history_world_alice")["turns"][0]["state_effects"]} == {
        "gold",
        "room_booked",
    }
    assert ALICE in json.dumps(world()["turns"][0]["state_effects"])

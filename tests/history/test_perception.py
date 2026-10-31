"""A turn's perception trace (#195): beats by characters, reconstructed from one story-view
history read per cast member.

The fake Engine answers ``GET /stories/{id}/history`` per ``viewpoint`` (C14's query matching)
from ``tests/fixtures/perception/history_<char>.json``: turn 3 of the Alice/Bob/Pat story, a
player input (everyone perceived it), Alice's say (Bob didn't) and Bob's do (Alice got its
envelope, Pat didn't), and the reply (no beat)."""

import copy
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from django.test import Client

from console.engine.models import History, Story
from console.perception import (
    ABSENT_TITLE,
    ALL_TIMED_OUT,
    NO_CAST,
    NO_FIDELITY_TITLE,
    RACE,
    column_labels,
    grid,
    no_beats,
    perception_url,
)
from console.views import perception as perception_views
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import (
    ALICE,
    BOB,
    EFFECTIVE,
    PAT,
    STORY,
    STORY_PATH,
    lite,
    story_cast,
    structured,
    structured_v2,
    with_settings,
)
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "perception"
HISTORY = "/stories/{story_id}/history"
TURN = 3
URL = f"/stories/{STORY}/turns/{TURN}/perception/"
NAMES = {ALICE: "alice", BOB: "bob", PAT: "pat"}


@pytest.fixture
def engine(engine: FakeEngine, monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """History's fake (``tests/history/conftest.py``), behind the perception view too."""
    from console.views import history as history_views

    patched = getattr(history_views, "engine_client_for")  # noqa: B009 - the conftest's patch
    monkeypatch.setattr(perception_views, "engine_client_for", patched)
    return engine


def view(character_id: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(
        (FIXTURES / f"history_{NAMES[character_id]}.json").read_text()
    )
    return copy.deepcopy(body)


def viewpoint(character_id: str) -> dict[str, str]:
    return {"viewpoint": f"character:{character_id}"}


def serve(
    engine: FakeEngine,
    *,
    effective: dict[str, Any] | None = None,
    story: dict[str, Any] | None = None,
    reads: dict[str, dict[str, Any]] | None = None,
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=story or story_cast())
    engine.respond("GET", EFFECTIVE, 200, json=effective or structured_v2())
    for character_id, body in (reads if reads is not None else {c: view(c) for c in NAMES}).items():
        engine.respond("GET", HISTORY, 200, json=body, query=viewpoint(character_id))


def get(client: Client, url: str = URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def history_calls(engine: FakeEngine) -> list[dict[str, list[str]]]:
    return [
        parse_qs(r.url.query.decode(), keep_blank_values=True)
        for r in engine.requests
        if r.url.path.endswith("/history")
    ]


def effective_calls(engine: FakeEngine) -> int:
    return sum(1 for r in engine.requests if r.url.path.endswith("/effective-config"))


def table(html: str) -> str:
    found = re.search(r'<table class="perception">.*?</table>', html, re.S)
    assert found is not None
    return found.group(0)


def headers(html: str) -> list[str]:
    return re.findall(r'<th scope="col"[^>]*>(.*?)</th>', table(html), re.S)[1:]


def body_rows(html: str) -> list[str]:
    return re.findall(r"<tr><th scope=\"row\">.*?</tr>", table(html), re.S)


def cells(row: str) -> list[str]:
    return re.findall(r"<td[^>]*>.*?</td>", row, re.S)


def parsed(body: dict[str, Any]) -> History:
    found = History.from_json(body)
    assert found is not None
    return found


def item(beat: int | None, fidelity: str | None, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(view(ALICE)["items"][1])
    body.update(beat=beat, fidelity=fidelity, **changes)
    return body


def read_of(*items: dict[str, Any], **changes: Any) -> History:
    body = view(ALICE)
    body.update(items=list(items), **changes)
    return parsed(body)


def cast_of(*members: tuple[str, str, bool]) -> list[Any]:
    story = story_cast()
    story["characters"] = [{"id": i, "name": n, "is_player": p} for i, n, p in members]
    found = Story.from_json(story)
    assert found is not None
    return list(found.cast)


CAST = cast_of((ALICE, "Alice", False), (BOB, "Bob", False), (PAT, "Pat", True))


# --- the reads ------------------------------------------------------------------------------------


def test_one_story_view_read_per_cast_member(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    get(operator_client)
    calls = history_calls(engine)
    assert sorted(c["viewpoint"][0] for c in calls) == sorted(
        f"character:{c}" for c in (ALICE, BOB, PAT)
    )
    for call in calls:
        assert call["player_view"] == ["story"]
        assert call["before_turn"] == [str(TURN + 1)]
        assert call["limit"] == ["1"]
        assert "variant" not in call
        assert "as_of" not in call


def test_reads_are_capped_at_20_with_a_note(operator_client: Client, engine: FakeEngine) -> None:
    members = [(f"00000000000000000000000{n:03d}", f"C{n}", False) for n in range(23)]
    story = story_cast()
    story["characters"] = [{"id": i, "name": n, "is_player": p} for i, n, p in members]
    serve(engine, story=story, reads={})
    engine.respond("GET", HISTORY, 200, json=view(ALICE))
    html = get(operator_client)
    calls = history_calls(engine)
    assert len(calls) == 20
    assert sorted(c["viewpoint"][0] for c in calls) == sorted(
        f"character:{i}" for i, _, _ in members[:20]
    )
    assert "Showing the first 20 characters; 3 more not read" in html
    assert "(20 reads)" in html
    assert len(headers(html)) == 20


def test_perception_off_is_greyed_with_zero_history_reads(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine, effective=structured_v2(perception__level="none"))
    html = get(operator_client)
    assert '<div class="panel unavailable">' in html
    assert (
        "Needs <code>perception.level</code> at <code>co_location</code> or above "
        "(this story runs <code>none</code>)"
    ) in html
    assert f'href="/stories/{STORY}/config/">Change it on the story' in html
    assert history_calls(engine) == []
    assert "Reconstructed" not in html


def test_lite_is_greyed_with_the_pipeline_clause(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = with_settings(
        lite(), {"perception.level": "none", "character_knowledge.partitions": False}
    )
    serve(engine, effective=body)
    html = get(operator_client)
    assert (
        "Needs the <code>perception</code> module, which needs the structured pipeline "
        "(<code>pipeline: structured</code>; this story runs <code>lite</code>)"
    ) in html
    assert history_calls(engine) == []


def test_an_unknown_level_is_named(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    body = structured_v2(perception__level="telepathic")  # outside the schema's enum
    engine.side_effect("GET", EFFECTIVE, lambda request: httpx.Response(200, json=body))
    html = get(operator_client)
    assert "(this story runs <code>telepathic</code>)" in html
    assert history_calls(engine) == []


def test_co_location_is_served(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, effective=structured_v2(perception__level="co_location"))
    assert '<table class="perception">' in get(operator_client)


def test_turn_zero_makes_no_effective_config_or_history_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    html = get(operator_client, f"/stories/{STORY}/turns/0/perception/")
    assert '<p class="notice">Turn 0 is the story&#x27;s setup: it has no beats</p>' in html
    assert effective_calls(engine) == 0
    assert history_calls(engine) == []
    assert "Back to turn 0 in History" in html


@pytest.mark.parametrize(
    "effective",
    [
        pytest.param(None, id="read fails"),
        pytest.param(structured_v2(pipeline=None), id="no pipeline"),
        pytest.param(structured_v2(perception__level=None), id="no perception.level"),
        pytest.param(structured(), id="M4 config"),
    ],
)
def test_effective_config_failure_or_missing_key_is_gate_unread_with_no_history_read(
    operator_client: Client, engine: FakeEngine, effective: dict[str, Any] | None
) -> None:
    serve(engine, effective=effective)
    if effective is None:
        engine.respond("GET", EFFECTIVE, 503, json=error_json("database_unavailable", "down"))
    html = get(operator_client)
    assert "Couldn&#x27;t read this story&#x27;s configuration — reload" in html
    assert '<div class="panel unavailable">' in html
    assert history_calls(engine) == []


def test_erasure_pending_is_the_held_wrapper(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    engine.respond(
        "GET",
        EFFECTIVE,
        503,
        json=error_json("erasure_pending", "held"),
        headers={"Retry-After": "5"},
    )
    response = operator_client.get(URL)
    assert response.status_code == 503
    assert "Held until the erasure ledger is re-applied" in response.content.decode()
    assert history_calls(engine) == []


def test_empty_cast_says_so(operator_client: Client, engine: FakeEngine) -> None:
    story = story_cast()
    story["characters"] = []
    serve(engine, story=story)
    html = get(operator_client)
    assert NO_CAST in html
    assert history_calls(engine) == []
    assert "Reconstructed" not in html


# --- the grid -------------------------------------------------------------------------------------


def test_cells_for_content_envelope_none_null_unknown_and_absent(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    html = get(operator_client)
    assert headers(html) == ["Alice", "Bob", "Pat (player)"]
    rows = body_rows(html)
    assert len(rows) == 3
    assert cells(rows[0]) == ['<td class="fidelity-content">perceived</td>'] * 3
    say = cells(rows[1])
    assert say[1] == (
        '<td class="fidelity-none" title="Shown because player view is story; this character '
        'did not perceive this beat">not perceived</td>'
    )
    do = cells(rows[2])
    assert do[0] == (
        '<td class="fidelity-envelope" title="This character perceived only that it happened; '
        'the text is the envelope">envelope</td>'
    )
    # Null, unknown and absent, from the grid itself (an unknown value fails the schema).
    found = grid(
        CAST,
        {
            ALICE: read_of(item(1, None)),
            BOB: read_of(item(1, "telepathic")),
            PAT: read_of(),
        },
        TURN,
    )
    (row,) = found.rows
    null, unknown, absent = row.cells
    assert (null.text, null.css_class, null.title) == ("—", "", NO_FIDELITY_TITLE)
    assert (unknown.text, unknown.css_class, unknown.title) == (
        "Telepathic",
        "fidelity-unknown",
        "telepathic",
    )
    assert (absent.text, absent.title) == ("—", ABSENT_TITLE)


def test_row_headers_name_the_beat_channel_actor_and_text(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    rows = body_rows(get(operator_client))
    assert (
        '<th scope="row">beat 1 · say · Pat (player) <span class="beat-text" '
        'title="Where is the key?">Where is the key?</span></th>' in rows[0]
    )
    # The first read (Alice's) holds the envelope text.
    assert "beat 3 · do · Bob" in rows[2]
    assert "Someone moves near the back door." in rows[2]
    long = "word " * 30
    (row,) = grid(CAST, {ALICE: read_of(item(1, "content", text=long))}, TURN).rows
    assert row.text == ("word " * 16)[:80] + "…"
    assert row.full_text == long.strip()
    (row,) = grid(CAST, {ALICE: read_of(item(1, "content", text=""))}, TURN).rows
    assert row.text == "(no text)"
    outsider = "0000000000000000000000ZZ99"
    narrated = item(1, "content", actor_character_id=outsider, narrated_by=BOB)
    (row,) = grid(CAST, {ALICE: read_of(narrated)}, TURN).rows
    assert row.head == "beat 1 · say · …0000ZZ99 (narrated by Bob)"
    (row,) = grid(CAST, {ALICE: read_of(item(1, "content", actor_character_id=None))}, TURN).rows
    assert row.head == "beat 1 · say · —"


def test_column_labels_follow_c4() -> None:
    labels = column_labels(cast_of((ALICE, "Sam", False), (BOB, "Sam", True), (PAT, "", False)))
    assert labels == {
        ALICE: "Sam (…0000CA01)",
        BOB: "Sam (…0000CA02) (player)",
        PAT: "…0000CA03",
    }


def test_rows_merge_across_reads_and_order_by_beat() -> None:
    twice = item(2, "content", text="Again.")
    found = grid(
        CAST,
        {
            ALICE: read_of(item(2, "content"), twice, item(1, "content", text="First.")),
            BOB: read_of(item(1, "none", text="First."), item(2, "none"), item(4, "content")),
            PAT: read_of(item(3, "content")),
        },
        TURN,
    )
    assert [r.head.split(" · ")[0] for r in found.rows] == [
        "beat 1",
        "beat 2",
        "beat 2",
        "beat 3",
        "beat 4",
    ]
    first, say, again, three, four = found.rows
    assert [c.text for c in first.cells] == ["perceived", "not perceived", "—"]
    assert [c.text for c in say.cells] == ["perceived", "not perceived", "—"]
    assert [c.text for c in again.cells] == ["perceived", "—", "—"]  # a second same-key item
    assert again.text == "Again."
    assert [c.text for c in three.cells] == ["—", "—", "perceived"]
    assert [c.text for c in four.cells] == ["—", "perceived", "—"]


def test_items_of_other_turns_are_ignored(operator_client: Client, engine: FakeEngine) -> None:
    alice = view(ALICE)
    earlier = copy.deepcopy(alice["items"][1])
    earlier.update(turn=TURN - 1, beat=9, text="Yesterday.")
    alice["items"].insert(0, earlier)
    serve(engine, reads={ALICE: alice, BOB: view(BOB), PAT: view(PAT)})
    html = get(operator_client)
    assert len(body_rows(html)) == 3
    assert "Yesterday." not in html


def test_heading_names_the_turn_and_variant(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = get(operator_client)
    assert "<title>Perception — turn 3 — Harbour — Epistrel Console</title>" in html
    assert "Perception — turn 3 · v1 — Harbour" in html
    assert f'href="/stories/{STORY}/history/?before_turn=4#turn-3-0"' in html
    assert "<code>think</code> and <code>latent</code> beats never reach" in html


# --- failures -------------------------------------------------------------------------------------


def test_a_failed_read_marks_its_column_and_lists_it(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    engine.respond(
        "GET", HISTORY, 503, json=error_json("database_unavailable", "down"), query=viewpoint(BOB)
    )
    html = get(operator_client)
    assert headers(html)[1] == "Bob — unavailable (HTTP 503 database_unavailable)"
    for row in body_rows(html):
        assert cells(row)[1] == '<td class="fidelity-unavailable">?</td>'
    assert "Perception unavailable for: Bob (HTTP 503 database_unavailable)" in html


def test_player_view_not_applied_is_a_failed_column(
    operator_client: Client, engine: FakeEngine
) -> None:
    pat = view(PAT)
    pat["player_view"] = "character"
    serve(engine, reads={ALICE: view(ALICE), BOB: view(BOB), PAT: pat})
    engine.respond(
        "GET", HISTORY, 503, json=error_json("erasure_pending", "held"), query=viewpoint(BOB)
    )
    html = get(operator_client)
    assert headers(html)[2] == "Pat (player) — unavailable (player view not applied: character)"
    assert (
        "Perception unavailable for: Bob (HTTP 503 erasure_pending), "
        "Pat (player) (player view not applied: character)"
    ) in html


def test_all_reads_failing_is_the_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, reads={})
    engine.respond(
        "GET", HISTORY, 404, json=error_json("not_found", "gone"), query=viewpoint(ALICE)
    )
    engine.respond(
        "GET", HISTORY, 409, json=error_json("unknown_chain", "no"), query=viewpoint(BOB)
    )
    engine.respond(
        "GET", HISTORY, 409, json=error_json("unknown_chain", "no"), query=viewpoint(PAT)
    )
    html = get(operator_client)
    panel = html.split('<div class="engine-error"')[1].split("</div>")[0]
    assert "<code>not_found</code>" in panel
    assert "unknown_chain" not in panel
    assert '<table class="perception">' not in html
    assert "Perception unavailable for: Alice (HTTP 404 not_found)" in html


def test_all_timeouts_panel_text(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(perception_views, "TRACE_DEADLINE_S", 0.1)
    serve(engine, reads={})
    engine.respond("GET", HISTORY, 200, json=view(ALICE), delay=0.5)
    html = get(operator_client)
    assert ALL_TIMED_OUT in html
    assert '<div class="engine-error"' not in html
    assert '<table class="perception">' not in html
    assert "Alice (timeout), Bob (timeout), Pat (player) (timeout)" in html


def test_timeline_race_between_reads_says_reload(
    operator_client: Client, engine: FakeEngine
) -> None:
    bob = view(BOB)
    bob["timeline_revision"] = 6
    serve(engine, reads={ALICE: view(ALICE), BOB: bob, PAT: view(PAT)})
    html = get(operator_client)
    assert RACE in html
    assert '<table class="perception">' not in html
    pat = view(PAT)
    for entry in [*pat["items"], *pat["turns"]]:
        entry["variant"] = 2
    serve(engine, reads={ALICE: view(ALICE), BOB: view(BOB), PAT: pat})
    assert RACE in get(operator_client)


def test_no_beats_at_that_turn_says_so(operator_client: Client, engine: FakeEngine) -> None:
    empty = view(ALICE)
    empty.update(items=[], turns=[], next_before_turn=None)
    serve(engine, reads={ALICE: empty, BOB: copy.deepcopy(empty)})
    engine.respond(
        "GET", HISTORY, 503, json=error_json("database_unavailable", "down"), query=viewpoint(PAT)
    )
    html = get(operator_client)
    assert no_beats(TURN) in html
    assert "Perception unavailable for: Pat (player) (HTTP 503 database_unavailable)" in html


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ("0:0", "0:0"),
        ("3:2", "3:2"),
        ("3:x", None),
        ("3", None),
        ("", None),
        ("1:2%0A", None),  # a trailing newline (#223)
        ("%D9%A1:%D9%A2", None),  # Arabic-Indic digits are not ASCII 0-9 (#223)
    ],
)
def test_variant_is_forwarded_and_a_bad_one_is_not_sent(
    operator_client: Client, engine: FakeEngine, given: str, sent: str | None
) -> None:
    serve(engine)
    html = get(operator_client, f"{URL}?variant={given}")
    for call in history_calls(engine):
        assert call.get("variant") == ([sent] if sent else None)
    back = re.search(r'<a class="history-back" href="([^"]+)"', html)
    assert back is not None
    assert (f"variant={sent}" in back.group(1)) is bool(sent)


def test_caveat_names_the_engine_request_and_read_count(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    html = get(operator_client)
    assert (
        "Reconstructed: one history read per character with player view story (3 reads). "
        "This shows today&#x27;s audience filter, not a record of what was applied when the "
        "turn ran (Engine request nathanpond/Epistrel#607)."
    ) in html
    assert html.index("Reconstructed") < html.index('<table class="perception">')


def test_building_links_makes_no_engine_call(engine: FakeEngine) -> None:
    assert perception_url(STORY, 3) == URL
    assert perception_url(STORY, 3, "3:2") == f"{URL}?variant=3%3A2"
    assert engine.requests == []


def test_no_verified_wording_in_the_templates() -> None:
    root = Path(__file__).resolve().parents[2] / "templates" / "console" / "perception"
    for template in root.iterdir():
        assert "verified" not in template.read_text().lower(), template


def test_an_unvalidated_unknown_fidelity_renders_humanised(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine, reads={})
    body = view(ALICE)
    body["items"][0]["fidelity"] = "telepathic"
    engine.side_effect("GET", HISTORY, lambda request: httpx.Response(200, json=body))
    html = get(operator_client)
    assert '<td class="fidelity-unknown" title="telepathic">Telepathic</td>' in html

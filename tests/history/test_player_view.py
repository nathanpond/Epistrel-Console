"""Preview the audience filter in History (#62): the Player view control, the header's audience
filter and player view, envelope / not-perceived beats badged and styled, narrated beats.

Two hand-written character reads of the "Harbour" story (Mara the player, Tobin, and Jenni added
to the cast here), as the Engine's ``GET /stories/{id}/history`` returns them (C15):
``get_history_filtered`` (``player_view=character``: an envelope beat, a reaction Jenni narrated,
a beat the player narrated) and ``get_history_story_view`` (``bypassed_by_player_view``: a beat
Tobin did not perceive). Values outside the schema's enums are served unvalidated."""

import copy
import json
import re
from typing import Any

import httpx
import pytest
from django.test import Client

from console.engine.models import HistoryItem
from console.forms.history import PLAYER_VIEW_CHOICES
from console.history import (
    FIDELITY_BADGES,
    FidelityBadge,
    audience_filter_label,
    fidelity_badge,
    narrated_label,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.history.helpers import (
    HISTORY,
    MARA,
    STORY_PATH,
    TOBIN,
    answer,
    history_calls,
    history_json,
    page,
    query_of,
)
from tests.inspect.helpers import FIXTURES
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

JENNI = "01JB2Z8Y5Q3W6K9M1N4P7R0T3C"
OUTSIDER = "01JB2Z8Y5Q3W6K9M1N4P7R0ZZ9"  # not in the cast
PREDICATES = "/stories/{story_id}/predicates"
CHARACTER = f"viewpoint=character:{TOBIN}"


def filtered() -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_history_filtered"))
    return body


def story_view() -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_history_story_view"))
    return body


def cast_story() -> dict[str, Any]:
    story = story_json(next_turn_index=3)
    story["characters"] = [
        *story["characters"],
        {"id": JENNI, "name": "Jenni", "is_player": False},
    ]
    return story


def serve(engine: FakeEngine, body: dict[str, Any] | None = None) -> None:
    """The page's reads (#52's ``answer()``), the three-character cast, the predicate registry
    and ``body`` as the history answer."""
    answer(engine, history=body or filtered())
    engine.respond("GET", STORY_PATH, 200, json=cast_story())
    engine.respond("GET", PREDICATES, 200, json=_predicates())


def serve_unvalidated(engine: FakeEngine, body: dict[str, Any]) -> None:
    """``body`` carries a value outside the schema's enums: served without validation."""
    serve(engine)
    engine.side_effect("GET", HISTORY, lambda request: httpx.Response(200, json=body))


def _predicates() -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / "predicates_list.json").read_text())
    return body


def header(html: str) -> str:
    found = re.search(r'<p class="history-header">(.*?)</p>', html, re.S)
    assert found is not None
    return found.group(1)


def item_rows(html: str) -> list[str]:
    """Each item ``<tr>`` with its cells, in page order."""
    return re.findall(r'<tr class="item [^"]*">.*?</tr>', html, re.S)


def row_with(html: str, text: str) -> str:
    """The beat row holding ``text`` (not the turn's reply, which repeats it)."""
    rows = [r for r in item_rows(html) if text in r and "message_rendered" not in r]
    assert len(rows) == 1, text
    return rows[0]


def row_class(row: str) -> str:
    found = re.match(r'<tr class="([^"]*)">', row)
    assert found is not None
    return found.group(1)


def actor_cell(row: str) -> str:
    cells: list[str] = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    return cells[3]


def select(html: str) -> str:
    found = re.search(r'<select name="player_view"[^>]*>.*?</select>', html, re.S)
    assert found is not None
    return found.group(0)


# --- The control ---------------------------------------------------------------------------------


def test_choices_are_shared() -> None:
    assert PLAYER_VIEW_CHOICES == [
        ("", "story setting"),
        ("character", "Character (filtered)"),
        ("story", "Story (everything, marked)"),
    ]


def test_control_renders_after_the_viewpoint(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = page(operator_client, f"{CHARACTER}&player_view=story")
    viewpoint = html.index('<div class="field field-viewpoint">')
    player = html.index('<div class="field field-player_view">')
    assert viewpoint < player < html.index('<div class="field field-as_of">')
    control = select(html)
    assert "disabled" not in control.split(">", 1)[0]
    assert '<option value="story" selected>Story (everything, marked)</option>' in control
    assert "player-view-note" not in html


def test_player_view_is_sent_only_when_chosen(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    page(operator_client, CHARACTER)
    page(operator_client, f"{CHARACTER}&player_view=character")
    blank, chosen = history_calls(engine)
    assert "player_view" not in blank
    assert chosen["player_view"] == ["character"]


def test_omniscient_drops_player_view(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, history_json())
    html = page(operator_client, "viewpoint=omniscient&player_view=story")
    (call,) = history_calls(engine)
    assert "player_view" not in call
    control = select(html)
    assert control.startswith('<select name="player_view" id="id_player_view" disabled>')
    assert '<option value="story" selected>' in control
    assert (
        '</select><input type="hidden" name="player_view" value="story"> '
        '<small class="muted player-view-note">applies to a character viewpoint</small>'
    ) in html
    # The HTMX controls re-render (the viewpoint select's change) keeps it the same way.
    fragment = page(
        operator_client, "viewpoint=omniscient&player_view=story", **{"HX-Request": "true"}
    )
    assert '<input type="hidden" name="player_view" value="story">' in fragment
    # Back to a character viewpoint: enabled, still "story", no hidden copy.
    again = page(operator_client, f"{CHARACTER}&player_view=story", **{"HX-Request": "true"})
    assert '<option value="story" selected>' in again
    assert 'type="hidden" name="player_view"' not in again
    assert "disabled" not in select(again).split(">", 1)[0]


def test_omniscient_with_blank_player_view_has_no_hidden_input(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine, history_json())
    html = page(operator_client, "viewpoint=omniscient")
    assert 'type="hidden" name="player_view"' not in html
    assert "player-view-note" in html


def test_blank_viewpoint_sends_player_view(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = page(operator_client, "player_view=story")
    (call,) = history_calls(engine)
    assert call["player_view"] == ["story"]
    assert "disabled" not in select(html).split(">", 1)[0]


@pytest.mark.parametrize("viewpoint", [CHARACTER, "viewpoint=omniscient"])
def test_unknown_player_view_is_a_form_error_with_no_history_call(
    operator_client: Client, engine: FakeEngine, viewpoint: str
) -> None:
    serve(engine)
    html = page(operator_client, f"{viewpoint}&player_view=both")
    assert history_calls(engine) == []
    field = html.split('<div class="field field-player_view">', 1)[1].split("</div>", 1)[0]
    assert "Select a valid choice. both is not one of the available choices." in field
    assert 'type="hidden" name="player_view"' not in html


# --- The header ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("none", "None (perception off: every beat in full)"),
        ("co_location", "Co-location"),
        ("full", "Full"),
        ("nothing_perceived", "Nothing perceived (this viewpoint perceived none of these beats)"),
        (
            "bypassed_by_player_view",
            "Bypassed by player view: every beat in full, marked with what this character "
            "would have perceived",
        ),
        ("by_mood", "By mood"),
    ],
)
def test_header_names_each_audience_filter(
    operator_client: Client, engine: FakeEngine, value: str, label: str
) -> None:
    assert audience_filter_label(value) == (label, value)
    body = filtered()
    body["audience_filter"] = value
    if value == "by_mood":
        serve_unvalidated(engine, body)
    else:
        serve(engine, body)
    line = header(page(operator_client, CHARACTER))
    assert (
        f' · audience filter <span class="audience-filter" title="{value}">{label}</span>'
        ' · player view <span class="player-view">character</span>'
    ) in line


def test_header_shows_echoed_player_view_before_chain(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine, story_view())
    line = header(page(operator_client, f"{CHARACTER}&variant=2:2"))
    assert line.index('<span class="audience-filter"') < line.index(
        '<span class="player-view">story</span>'
    )
    assert line.endswith('<span class="player-view">story</span> · chain 2:2')


@pytest.mark.parametrize("query", ["viewpoint=omniscient&player_view=story", ""])
def test_omniscient_header_has_no_audience_filter(
    operator_client: Client, engine: FakeEngine, query: str
) -> None:
    serve(engine, history_json())  # an omniscient echo (asked for, or the story's default)
    line = header(page(operator_client, query))
    assert "audience filter" not in line
    assert "player view" not in line
    assert "<code>omniscient</code>" in line


def test_failed_read_shows_no_header(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    engine.respond(
        "GET",
        HISTORY,
        422,
        json=error_json("as_of_beyond_head"),
    )
    html = page(operator_client, f"{CHARACTER}&player_view=story&as_of=9")
    assert '<p class="history-header">' not in html


# --- Fidelity ------------------------------------------------------------------------------------


def test_fidelity_badges() -> None:
    assert fidelity_badge(None) is None
    assert fidelity_badge("content") is None
    assert fidelity_badge("envelope") is FIDELITY_BADGES["envelope"]
    assert fidelity_badge("none") is FIDELITY_BADGES["none"]
    assert fidelity_badge("half_heard") == FidelityBadge("Half heard", "half_heard", "")


def test_envelope_and_none_rows_are_styled(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = page(operator_client, CHARACTER)
    envelope = row_with(html, "Jenni whispers something to Tobin")
    assert row_class(envelope) == "item channel-say fidelity-envelope"
    assert (
        '<span class="badge-channel">say</span> <span class="badge-fidelity" title="This '
        'character perceived only that it happened; the text is the envelope">envelope</span>'
    ) in envelope
    content = row_with(html, "Tobin glances toward the kitchen.")
    assert row_class(content) == "item channel-do"
    assert "badge-fidelity" not in content

    serve(engine, story_view())
    html = page(operator_client, f"{CHARACTER}&player_view=story")
    unseen = row_with(html, "The key is under the mat.")
    assert row_class(unseen) == "item channel-say fidelity-none"
    assert (
        '<span class="badge-fidelity" title="Shown because player view is story; this character '
        'did not perceive this beat">not perceived</span>'
    ) in unseen
    assert 'class="beat-text"' in unseen  # its beat trace stays, inside the struck row


def test_unknown_fidelity_gets_a_humanised_badge(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = filtered()
    body["items"][1]["fidelity"] = "half_heard"
    serve_unvalidated(engine, body)
    row = row_with(page(operator_client, CHARACTER), "Jenni whispers something to Tobin")
    assert row_class(row) == "item channel-say"
    assert '<span class="badge-fidelity" title="half_heard">Half heard</span>' in row


def test_envelope_item_renders_no_beat_trace(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    row = row_with(page(operator_client, CHARACTER), "Jenni whispers something to Tobin")
    assert "beat-text" not in row
    assert "Trace ·" not in row
    assert '<td class="text">Jenni whispers something to Tobin' in row


@pytest.mark.parametrize(
    ("fixture", "query"),
    [
        ("get_history_filtered", "player_view=character"),
        ("get_history_story_view", "player_view=story"),
    ],
)
def test_console_never_hides_items_itself(
    operator_client: Client, engine: FakeEngine, fixture: str, query: str
) -> None:
    body = copy.deepcopy(engine_fixture(fixture))
    serve(engine, body)
    rows = item_rows(page(operator_client, f"{CHARACTER}&{query}"))
    assert len(rows) == len(body["items"])
    for item, row in zip(body["items"], rows, strict=True):
        badge = fidelity_badge(item.get("fidelity"))
        expected = f"item channel-{item['channel']}"
        if badge is not None:
            expected += f" {badge.row_class}"
            assert f'<span class="badge-fidelity" title="{badge.title}">{badge.text}</span>' in row
        else:
            assert "badge-fidelity" not in row
        assert row_class(row) == expected


# --- Narrated beats ------------------------------------------------------------------------------


def test_narrated_by_names_the_narrator(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = page(operator_client, CHARACTER)
    row = row_with(html, "Tobin glances toward the kitchen.")
    assert actor_cell(row) == "Tobin (narrated by Jenni)"
    plain = row_with(html, "Where is Jenni?")
    assert actor_cell(plain) == "Mara"


def test_unknown_narrator_is_a_short_id(operator_client: Client, engine: FakeEngine) -> None:
    body = filtered()
    body["items"][2]["narrated_by"] = OUTSIDER
    serve(engine, body)
    row = row_with(page(operator_client, CHARACTER), "Tobin glances toward the kitchen.")
    assert actor_cell(row) == f"Tobin (narrated by …{OUTSIDER[-8:]})"


def test_player_narrated_beat_says_the_player(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    row = row_with(page(operator_client, CHARACTER), "The ferry is late again.")
    assert actor_cell(row) == "Tobin (narrated by the player)"


def test_narrated_label_unit() -> None:
    raw = copy.deepcopy(engine_fixture("get_history_filtered")["items"][2])
    item = HistoryItem.from_json(raw)
    assert item is not None
    labels = {TOBIN: "Tobin", MARA: "Mara"}
    assert narrated_label(item, labels) == f"Tobin (narrated by …{JENNI[-8:]})"
    assert narrated_label(item, {**labels, JENNI: "Jenni"}) == "Tobin (narrated by Jenni)"


# --- Links ---------------------------------------------------------------------------------------


def test_variants_view_links_carry_player_view(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = page(operator_client, f"{CHARACTER}&player_view=story&limit=200")
    links = re.findall(r'<a class="view-chain" href="([^"]+)">view</a>', html)
    assert links
    for href in links:
        params = query_of(href)
        assert params["player_view"] == ["story"]
        assert params["viewpoint"] == [f"character:{TOBIN}"]
    # Complement: absent when blank.
    html = page(operator_client, CHARACTER)
    for href in re.findall(r'<a class="view-chain" href="([^"]+)">view</a>', html):
        assert "player_view" not in query_of(href)

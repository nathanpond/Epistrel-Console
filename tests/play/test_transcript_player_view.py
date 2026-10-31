"""Preview the audience filter in the play transcript (#196): a display-only Player view
control, the heading's player view and audience filter, #62's fidelity badges and narrator
labels on the transcript's items, and the requested value carried as ``transcript_player_view``
through every transcript re-fetch the play page makes (turn and ingest POSTs, polls, tabs, the
output-viewpoint switch and the "Older →" link). It never sets a turn's own ``player_view``.

Reads serve #62's ``get_history_filtered`` / ``get_history_story_view`` (Tobin's view of the
"Harbour" story with Jenni in the cast) through the schema-validating fake (C15)."""

import json
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from console.views.play import WaitingCard
from tests.fakes.engine import FakeEngine
from tests.history.test_player_view import JENNI, cast_story, filtered, story_view
from tests.play.helpers import (
    HTMX,
    MARA,
    OPERATION,
    QUEUED_OP,
    RESPOND,
    STORY,
    TOBIN,
    answer_page,
    calls,
    error_json,
    history_json,
    operation_json,
    post_turn,
    respond_json,
    snapshot,
    story_json,
)
from tests.play.test_ingest import answer_ingest, narration, player, post_ingest
from tests.play.test_play_page import queued_row

pytestmark = pytest.mark.django_db

HISTORY_PATH = f"/stories/{STORY}/history"
HISTORY = "/stories/{story_id}/history"
TOBIN_VIEW = f"character:{TOBIN}"
CHOSEN = ["character", "story"]
INVALID = ["Character", "STORY", " story", "story ", "omniscient", "content", "x"]


# --- helpers -------------------------------------------------------------------------------------


def serve_page(engine: FakeEngine, history: dict[str, Any] | None = None) -> None:
    """The play page's reads, the three-character cast and ``history`` (Tobin, filtered)."""
    answer_page(engine, story=cast_story(), history=history or filtered())


def omniscient_history() -> dict[str, Any]:
    body = history_json()
    body.update(output_viewpoint="omniscient", audience_filter="none", player_view="story")
    return body


def history_params(engine: FakeEngine) -> list[dict[str, str]]:
    return [dict(r.url.params) for r in calls(engine, "GET", HISTORY_PATH)]


def last_sent(engine: FakeEngine) -> dict[str, str]:
    found = history_params(engine)
    assert found, "no history read"
    return found[-1]


def play_page(client: Client, **query: str) -> str:
    response = client.get(f"/stories/{STORY}/", query)
    assert response.status_code == 200
    return response.content.decode()


def toggle(client: Client, *, htmx: bool = True, **query: str) -> str:
    """The transcript endpoint, as the toggle (or the output-viewpoint select) asks it."""
    params = {"snapshot": snapshot(story=cast_story()), **query}
    response = client.get(
        reverse("stories:transcript", args=[STORY]), params, headers=HTMX if htmx else {}
    )
    assert response.status_code == 200
    return response.content.decode()


def transcript_div(html: str) -> str:
    """``#transcript``'s markup: up to the waiting area on a page, the out-of-band inputs on a
    toggle answer, or the next out-of-band element of a POST answer."""
    assert '<div id="transcript"' in html
    found = html.split('<div id="transcript"', 1)[1]
    for end in (
        '<div id="turn-status"',
        '<input type="hidden" name="transcript_player_view" id="turn-transcript-player-view" '
        'value="[^"]*" hx-swap-oob',
        '<span id="story-config"',
    ):
        found = re.split(end, found, maxsplit=1)[0]
    return found


def heading(html: str) -> str:
    found = re.search(r"<h2>Transcript <small>(.*?)</small></h2>", html, re.S)
    assert found is not None
    return found.group(1)


def control(html: str) -> str:
    found = re.search(r'<p class="field transcript-player-view">(.*?)</p>', html, re.S)
    assert found is not None
    return found.group(1)


def selected(html: str) -> str:
    found = re.search(r'<option value="([^"]*)" selected>', control(html))
    assert found is not None
    return found.group(1)


def items(html: str) -> list[str]:
    """Each transcript item's markup, from its ``<li class="item …">`` to the next one (a beat
    trace holds lists of its own)."""
    parts = re.split(r'(?=<li class="item )', transcript_div(html))
    return [p for p in parts if p.startswith('<li class="item ')]


def item_with(html: str, text: str) -> str:
    rows = [r for r in items(html) if text in r and "channel-message" not in r]
    assert len(rows) == 1, text
    return rows[0]


def hidden(html: str, element_id: str) -> str:
    """The value of the hidden ``transcript_player_view`` input ``element_id``."""
    found = re.findall(
        rf'<input type="hidden" name="transcript_player_view" id="{element_id}" value="([^"]*)"',
        html,
    )
    assert len(found) == 1, (element_id, found)
    return str(found[0])


def queue_turn(client: Client, engine: FakeEngine, **fields: Any) -> str:
    engine.respond(
        "POST",
        RESPOND,
        202,
        json={"operation_id": QUEUED_OP, "position": 1},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    answer_page(engine)
    return post_turn(client, **fields)


def committed_poll(engine: FakeEngine) -> None:
    committed = operation_json(
        "committed",
        result={"status": 200, "body": respond_json()},
        viewpoint=TOBIN_VIEW,
        timeline_revision=2,
    )
    engine.respond("GET", OPERATION, 200, json=committed)
    serve_page(engine, story_view())


def poll(client: Client, *, htmx: bool = True, **query: str) -> str:
    params = {"viewpoint": TOBIN_VIEW, "deadline": "45", **query}
    url = reverse("stories:poll", args=[STORY, QUEUED_OP])
    response = client.get(url, params, headers=HTMX if htmx else {})
    assert response.status_code == 200
    return response.content.decode()


# --- the control ---------------------------------------------------------------------------------


@pytest.mark.parametrize("value", CHOSEN)
def test_transcript_toggle_reads_history_with_player_view(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    serve_page(engine, filtered() if value == "character" else story_view())
    html = toggle(operator_client, viewpoint=TOBIN_VIEW, player_view=value)
    assert last_sent(engine) == {"viewpoint": TOBIN_VIEW, "limit": "50", "player_view": value}
    assert html.startswith('<div id="transcript">') or '<div id="transcript">' in html
    assert selected(html) == value
    line = control(html)
    assert '<label for="transcript-player-view">Player view</label>' in line
    assert 'name="player_view"' in line
    assert "disabled" not in line
    assert [opt for opt in re.findall(r"<option[^>]*>([^<]*)</option>", line)] == [
        "story setting",
        "Character (filtered)",
        "Story (everything, marked)",
    ]


def test_the_control_sits_under_the_heading_on_its_own_line(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    html = transcript_div(play_page(operator_client, viewpoint=TOBIN_VIEW))
    assert html.index("</h2>") < html.index('<p class="field transcript-player-view">')
    assert html.index('<p class="field transcript-player-view">') < html.index("<ol")


def test_toggle_rereads_at_the_transcripts_own_viewpoint(
    operator_client: Client, engine: FakeEngine
) -> None:
    """The page's turn form shows the story default (Mara); the transcript is Tobin's. The
    toggle's hx-get names the transcript's viewpoint, not the form's select."""
    serve_page(engine)
    html = play_page(operator_client, viewpoint=TOBIN_VIEW)
    line = control(html)
    url = f"/stories/{STORY}/transcript/?viewpoint=character%3A{TOBIN}"
    assert f'hx-get="{url}"' in line
    assert 'hx-include="[name=snapshot]"' in line
    assert 'hx-target="#transcript"' in line
    assert 'hx-swap="outerHTML"' in line
    engine.reset()
    serve_page(engine)
    switched = toggle(operator_client, output_viewpoint=TOBIN_VIEW)  # the select's own swap
    assert f'hx-get="{url}"' in control(switched)
    engine.reset()
    serve_page(engine)
    # The form's select may say something else; the toggle reads the transcript's viewpoint.
    toggle(operator_client, viewpoint=TOBIN_VIEW, player_view="story", output_viewpoint="default")
    assert last_sent(engine)["viewpoint"] == TOBIN_VIEW


def test_no_viewpoint_param_when_the_transcript_has_none(
    operator_client: Client, engine: FakeEngine
) -> None:
    """No viewpoint resolves (no default, no cast): the Engine picks, the control is enabled
    and the value is sent."""
    empty = story_json(characters=[], settings={"default_output_viewpoint": None})
    answer_page(engine, story=empty, history=filtered())
    html = play_page(operator_client, player_view="character")
    line = control(html)
    assert f'hx-get="/stories/{STORY}/transcript/"' in line
    assert "disabled" not in line
    assert last_sent(engine) == {"limit": "50", "player_view": "character"}


@pytest.mark.parametrize("value", INVALID)
def test_invalid_player_view_param_is_not_sent(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    """Complement: only an exact ``character`` / ``story`` is sent; anything else is blanked."""
    serve_page(engine)
    page = play_page(operator_client, viewpoint=TOBIN_VIEW, player_view=value)
    assert "player_view" not in last_sent(engine)
    assert selected(page) == ""
    assert hidden(page, "turn-transcript-player-view") == ""
    engine.reset()
    serve_page(engine)
    html = toggle(operator_client, viewpoint=TOBIN_VIEW, player_view=value)
    assert "player_view" not in last_sent(engine)
    assert selected(html) == ""
    assert hidden(html, "turn-transcript-player-view") == ""


@pytest.mark.parametrize("value", CHOSEN)
def test_omniscient_transcript_sends_no_player_view(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    """Complement: an omniscient viewpoint (explicit, or the story default resolved) never
    sends it; the select is disabled with the value still selected."""
    answer_page(engine, story=cast_story(), history=omniscient_history())
    html = play_page(operator_client, viewpoint="omniscient", player_view=value)
    assert last_sent(engine) == {"viewpoint": "omniscient", "limit": "50"}
    line = control(html)
    assert re.search(r'<select name="player_view"[^>]* disabled>', line)
    assert selected(html) == value
    assert '<small class="muted player-view-note">applies to a character viewpoint</small>' in line
    assert hidden(html, "turn-transcript-player-view") == value  # kept for a later switch back

    engine.reset()
    default_omniscient = cast_story()
    default_omniscient["settings"]["default_output_viewpoint"] = "omniscient"
    answer_page(engine, story=default_omniscient, history=omniscient_history())
    params = {"snapshot": snapshot(story=default_omniscient), "output_viewpoint": "default"}
    response = operator_client.get(
        reverse("stories:transcript", args=[STORY]),
        {**params, "player_view": value},
        headers=HTMX,
    )
    assert last_sent(engine) == {"viewpoint": "omniscient", "limit": "50"}
    assert re.search(r'<select name="player_view"[^>]* disabled>', response.content.decode())


# --- the heading ---------------------------------------------------------------------------------


def test_transcript_heading_names_player_view_and_audience_filter(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    html = play_page(operator_client, viewpoint=TOBIN_VIEW, player_view="character")
    assert heading(html) == (
        'as Tobin · player view <span class="player-view">character</span> · audience filter '
        '<span class="audience-filter" title="full">Full</span>'
    )
    engine.reset()
    serve_page(engine, story_view())
    html = toggle(operator_client, viewpoint=TOBIN_VIEW, player_view="story")
    assert heading(html).startswith('as Tobin · player view <span class="player-view">story')
    assert 'title="bypassed_by_player_view">Bypassed by player view' in heading(html)


def test_a_blank_choice_echoes_the_story_setting(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Blank sends nothing; the heading names what the Engine applied."""
    serve_page(engine)
    html = play_page(operator_client, viewpoint=TOBIN_VIEW)
    assert "player_view" not in last_sent(engine)
    assert 'player view <span class="player-view">character</span>' in heading(html)


def test_an_omniscient_read_shows_only_the_viewpoint(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine, story=cast_story(), history=omniscient_history())
    html = play_page(operator_client, viewpoint="omniscient", player_view="story")
    assert "·" not in heading(html)
    assert "audience-filter" not in heading(html)
    assert control(html)


@pytest.mark.parametrize("value", CHOSEN)
def test_a_failed_read_shows_only_the_viewpoint_and_keeps_the_control(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    serve_page(engine)
    engine.respond("GET", HISTORY, 422, json=error_json("viewpoint_unresolved", "no"))
    html = play_page(operator_client, viewpoint=TOBIN_VIEW, player_view=value)
    assert heading(html) == "as Tobin"
    assert selected(html) == value
    assert "disabled" not in control(html)
    assert "viewpoint_unresolved" in transcript_div(html)
    html = toggle(operator_client, viewpoint=TOBIN_VIEW, player_view=value)
    assert heading(html) == "as Tobin"
    assert selected(html) == value


# --- the items -----------------------------------------------------------------------------------


def test_transcript_items_carry_fidelity_badges_and_narrator(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    html = play_page(operator_client, viewpoint=TOBIN_VIEW, player_view="character")
    envelope = item_with(html, "Jenni whispers something to Tobin")
    assert envelope.startswith('<li class="item channel-say fidelity-envelope"')
    badge = (
        '<span class="badge-fidelity" title="This character perceived only that it happened; '
        'the text is the envelope">envelope</span>'
    )
    assert badge in envelope
    assert envelope.index('<span class="channel">say</span>') < envelope.index(badge)
    assert envelope.index(badge) < envelope.index("<strong>Jenni</strong>")
    reaction = item_with(html, "Tobin glances toward the kitchen.")
    assert "<strong>Tobin (narrated by Jenni)</strong>" in reaction
    assert "badge-fidelity" not in reaction
    assert re.match(r'<li class="item channel-do"', reaction)
    player_beat = item_with(html, "The ferry is late again.")
    assert "<strong>Tobin (narrated by the player)</strong>" in player_beat
    # Complement: every item the Engine returned is rendered; the Console hides none.
    assert len(items(html)) == len(filtered()["items"])


def test_a_not_perceived_beat_keeps_its_trace_struck(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine, story_view())
    html = play_page(operator_client, viewpoint=TOBIN_VIEW, player_view="story")
    unseen = item_with(html, "The key is under the mat.")
    assert unseen.startswith('<li class="item channel-say fidelity-none"')
    assert '<span class="badge-fidelity" title="Shown because player view is story; ' in unseen
    assert ">not perceived</span>" in unseen
    assert "beat-text" in unseen  # its trace, struck by base.html's .fidelity-none rule
    assert len(items(html)) == len(story_view()["items"])
    assert JENNI not in heading(html)


def test_transcript_envelope_item_renders_no_beat_trace(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    html = play_page(operator_client, viewpoint=TOBIN_VIEW, player_view="character")
    envelope = item_with(html, "Jenni whispers something to Tobin")
    assert '<span class="text">Jenni whispers something to Tobin</span>' in envelope
    for absent in ("beat-text", "beat-trace", "<details"):
        assert absent not in envelope


# --- carried through re-fetches ------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", *CHOSEN])
def test_toggle_answer_updates_form_inputs_out_of_band(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    serve_page(engine)
    html = toggle(operator_client, viewpoint=TOBIN_VIEW, player_view=value)
    after = html.split(transcript_div(html), 1)[1]  # no copy lives inside #transcript
    for element_id in ("turn-transcript-player-view", "ingest-transcript-player-view"):
        assert (
            f'<input type="hidden" name="transcript_player_view" id="{element_id}" '
            f'value="{value}" hx-swap-oob="true">'
        ) in after
    assert "transcript_player_view" not in transcript_div(html)


def test_the_forms_hold_the_requested_value(operator_client: Client, engine: FakeEngine) -> None:
    serve_page(engine)
    html = play_page(operator_client, viewpoint=TOBIN_VIEW, player_view="story")
    assert hidden(html, "turn-transcript-player-view") == "story"
    engine.reset()
    serve_page(engine)
    html = play_page(operator_client, viewpoint=TOBIN_VIEW, player_view="story", tab="ingest")
    assert hidden(html, "ingest-transcript-player-view") == "story"


def test_turn_refetch_keeps_transcript_player_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", RESPOND, 200, json=respond_json())
    serve_page(engine, story_view())
    html = post_turn(operator_client, transcript_player_view="story")
    assert last_sent(engine)["player_view"] == "story"
    assert 'player view <span class="player-view">story</span>' in heading(html)
    assert hidden(html, "turn-transcript-player-view") == "story"

    engine.reset()
    engine.respond("POST", RESPOND, 200, json=respond_json())
    serve_page(engine, story_view())
    response = operator_client.post(
        reverse("stories:turn", args=[STORY]),
        {**_turn(), "transcript_player_view": "story"},
    )
    page = response.content.decode()
    assert "<h1>Harbour</h1>" in page  # no-JS: the full page
    assert all(p.get("player_view") == "story" for p in history_params(engine))
    assert selected(page) == "story"
    assert hidden(page, "turn-transcript-player-view") == "story"


def test_an_invalid_turn_form_keeps_transcript_player_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post_turn(operator_client, say="", do="", transcript_player_view="character")
    assert hidden(html, "turn-transcript-player-view") == "character"
    serve_page(engine)
    response = operator_client.post(
        reverse("stories:turn", args=[STORY]),
        {**_turn(say=""), "transcript_player_view": "character"},
    )
    page = response.content.decode()
    assert last_sent(engine)["player_view"] == "character"
    assert hidden(page, "turn-transcript-player-view") == "character"


def test_a_bad_posted_value_is_blanked(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RESPOND, 200, json=respond_json())
    serve_page(engine)
    html = post_turn(operator_client, transcript_player_view="Story")
    assert "player_view" not in last_sent(engine)
    assert hidden(html, "turn-transcript-player-view") == ""


def test_ingest_refetch_keeps_transcript_player_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ingest(engine)
    html = post_ingest(operator_client, player(), narration(), transcript_player_view="character")
    assert last_sent(engine)["player_view"] == "character"
    assert hidden(html, "ingest-transcript-player-view") == "character"
    assert selected(html) == "character"
    assert (
        'hx-include="#ingest [name=snapshot], #ingest [name=output_viewpoint], '
        '#play-form-area [name=transcript_player_view]">New batch'
    ) in html

    engine.reset()
    answer_ingest(engine)
    answer_page(engine, history=filtered())
    page = post_ingest(
        operator_client, player(), narration(), htmx=False, transcript_player_view="story"
    )
    assert all(p.get("player_view") == "story" for p in history_params(engine))
    assert hidden(page, "ingest-transcript-player-view") == "story"


def test_ingest_row_edits_and_refusals_keep_transcript_player_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    added = post_ingest(operator_client, player(), add="1", transcript_player_view="story")
    assert hidden(added, "ingest-transcript-player-view") == "story"
    invalid = post_ingest(operator_client, player(say="", do=""), transcript_player_view="story")
    assert hidden(invalid, "ingest-transcript-player-view") == "story"
    assert not history_params(engine)


def test_poll_url_carries_player_view() -> None:
    card = WaitingCard(STORY, QUEUED_OP, "running", 1, TOBIN_VIEW, 45.0, "2099-01-01T00:00:00Z")
    assert "player_view" not in card.poll_url  # blank: omitted
    with_view = WaitingCard(
        STORY, QUEUED_OP, "running", 1, TOBIN_VIEW, 45.0, "2099-01-01T00:00:00Z", "story"
    )
    assert with_view.poll_url.endswith("&started=2099-01-01T00%3A00%3A00Z&player_view=story")


def test_a_queued_turn_and_its_poll_carry_player_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = queue_turn(operator_client, engine, transcript_player_view="story")
    assert "&amp;player_view=story" in html.split('<div id="turn-status"')[1]
    engine.reset()
    committed_poll(engine)
    done = poll(operator_client, player_view="story")
    assert last_sent(engine)["player_view"] == "story"
    assert 'player view <span class="player-view">story</span>' in heading(done)
    assert hidden(done, "turn-transcript-player-view") == "story"


def test_a_running_poll_keeps_player_view(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 200, json=operation_json("in_flight"))
    html = poll(operator_client, player_view="character")
    assert html.count("player_view=character") == 1


def test_the_no_js_end_of_poll_keeps_player_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    committed_poll(engine)
    page = poll(operator_client, htmx=False, player_view="story")
    assert "<h1>Harbour</h1>" in page
    assert all(p.get("player_view") == "story" for p in history_params(engine))
    assert selected(page) == "story"


def test_a_restored_card_takes_the_pages_player_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    queued_row()
    serve_page(engine)
    html = play_page(operator_client, player_view="character")
    status = html.split('<div id="turn-status">')[1].split('<div id="play-form-area">')[0]
    assert "&amp;player_view=character" in status
    engine.reset()
    serve_page(engine)
    html = play_page(operator_client)
    status = html.split('<div id="turn-status">')[1].split('<div id="play-form-area">')[0]
    assert "player_view" not in status


def test_a_queued_ingest_carries_player_view(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        "/stories/{story_id}/ingest",
        202,
        json={"operation_id": QUEUED_OP, "position": 1},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    html = post_ingest(operator_client, player(), transcript_player_view="character")
    assert "&amp;player_view=character" in html


@pytest.mark.parametrize("value", CHOSEN)
def test_older_link_carries_player_view(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    body = filtered()
    body["next_before_turn"] = 1
    serve_page(engine, body)
    html = play_page(operator_client, viewpoint=TOBIN_VIEW, player_view=value)
    assert (
        f'href="/stories/{STORY}/history/?before_turn=1&amp;viewpoint=character%3A{TOBIN}'
        f'&amp;player_view={value}">Older →</a>'
    ) in html


def test_older_link_omits_a_blank_player_view(operator_client: Client, engine: FakeEngine) -> None:
    """Complement: blank adds nothing."""
    body = filtered()
    body["next_before_turn"] = 1
    serve_page(engine, body)
    html = play_page(operator_client, viewpoint=TOBIN_VIEW)
    link = re.search(r'<a href="([^"]*)">Older →</a>', html)
    assert link is not None
    assert "player_view" not in link.group(1)


def test_output_viewpoint_switch_includes_transcript_player_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    html = play_page(operator_client)
    select = re.search(r'<select name="output_viewpoint"[^>]*>', html)
    assert select is not None
    assert 'hx-include="[name=snapshot], #turn-form [name=transcript_player_view]"' in (
        select.group(0)
    )
    engine.reset()
    serve_page(engine)
    toggle(operator_client, output_viewpoint=TOBIN_VIEW, transcript_player_view="story")
    assert last_sent(engine) == {"viewpoint": TOBIN_VIEW, "limit": "50", "player_view": "story"}
    engine.reset()
    serve_page(engine)
    toggle(
        operator_client,
        output_viewpoint=TOBIN_VIEW,
        transcript_player_view="story",
        player_view="character",
    )
    assert last_sent(engine)["player_view"] == "character"  # the toggle's own value wins


def test_tab_links_keep_transcript_player_view(operator_client: Client, engine: FakeEngine) -> None:
    serve_page(engine)
    html = play_page(operator_client, player_view="story")
    include = (
        'hx-include="#play-form-area [name=snapshot], #play-form-area [name=output_viewpoint], '
        '#play-form-area [name=transcript_player_view]"'
    )
    tabs = html.split('<nav class="tabs">')[1].split("</nav>")[0]
    assert tabs.count(include) == 2
    engine.reset()
    response = operator_client.get(
        f"/stories/{STORY}/",
        {
            "tab": "ingest",
            "snapshot": snapshot(story=cast_story()),
            "output_viewpoint": "default",
            "transcript_player_view": "story",
        },
        headers=HTMX,
    )
    area = response.content.decode()
    assert area.lstrip().startswith('<div id="play-form-area">')
    assert hidden(area, "ingest-transcript-player-view") == "story"
    assert not history_params(engine)
    response = operator_client.get(
        f"/stories/{STORY}/",
        {
            "tab": "turn",
            "snapshot": snapshot(story=cast_story()),
            "transcript_player_view": "Story",
        },
        headers=HTMX,
    )
    assert hidden(response.content.decode(), "turn-transcript-player-view") == ""


# --- never the turn's player view ----------------------------------------------------------------


@pytest.mark.parametrize("value", CHOSEN)
def test_transcript_toggle_never_sets_the_turn_body_player_view(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    """Complement: the respond body carries no ``player_view`` from this control."""
    engine.respond("POST", RESPOND, 200, json=respond_json())
    serve_page(engine)
    post_turn(operator_client, transcript_player_view=value)
    (request,) = calls(engine, "POST", f"/stories/{STORY}/respond")
    body = json.loads(request.content)
    assert "player_view" not in body
    assert "transcript_player_view" not in body
    assert body["actor_character_id"] == MARA


def _turn(**fields: Any) -> dict[str, Any]:
    from tests.play.helpers import turn_data

    return turn_data(**fields)

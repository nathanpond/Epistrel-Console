"""The inspector for earlier turns (#48): a turn played from this Console shows its stored
report (#44's log); any other turn says why there is none."""

import re
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from console.engine.models import RespondResponse
from console.inspect import short_id
from console.models import EngineOperation
from console.views.play import result_inspector, row_inspector
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import (
    HTMX,
    MARA,
    OPERATION,
    QUEUED_OP,
    RESPOND,
    STORY,
    TOBIN,
    TURN_OP,
    answer_page,
    calls,
    operation_json,
    respond_json,
    turn_data,
)
from tests.play.test_cast_participation import JENNI, play_story
from tests.play.test_multi_turn_form import serve_page, snapshot_for
from tests.play.test_turn_outcomes import poll

pytestmark = pytest.mark.django_db


def log_row(**fields: Any) -> EngineOperation:
    values: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "story_id": STORY,
        "kind": "respond",
        "http_method": "POST",
        "path": f"/stories/{STORY}/respond",
        "request": {"body": {"output_viewpoint": f"character:{TOBIN}"}, "headers": {}},
        "http_status": 200,
        "engine_operation_id": TURN_OP,
        "timeline_revision": 2,
        "config_revision": 0,
        "idempotency_key": "01JB2Z8Y5Q3W6K9M1N4P7R0KY9",
        "outcome": "ok",
        "response": respond_json(),
    }
    values.update(fields)
    return EngineOperation.objects.create(**values)


def inspect(client: Client, operation: str, **extra: str) -> str:
    url = reverse("stories:inspector", args=[STORY])
    response = client.get(url, {"operation": operation, **extra}, headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


def test_a_turn_played_here_shows_its_stored_report(operator_client: Client) -> None:
    row = log_row()
    html = inspect(operator_client, TURN_OP)
    assert "Turn 1 (variant 1)" in html
    assert "<h3>Turn report</h3>" in html
    assert "<td>generation</td>" in html
    assert "<dt>Timeline revision</dt><dd>2</dd>" in html
    assert "<code>01JB2Z8Y5Q3W6K9M1N4P7R0KY9</code>" in html
    assert reverse("operations:detail", args=[row.pk]) in html


def test_a_turn_not_in_the_log_has_no_report(operator_client: Client) -> None:
    html = inspect(operator_client, "01JB2Z8Y5Q3W6K9M1N4P7R0ZZZ")
    assert "Report not available — this turn was not played from this Console" in html


def test_an_ingested_turn_says_so(operator_client: Client) -> None:
    html = inspect(operator_client, "01JB2Z8Y5Q3W6K9M1N4P7R0ZZZ", origin="ingested")
    assert "No report — this turn was ingested, not generated" in html


def test_a_still_queued_row_has_no_report_yet(operator_client: Client) -> None:
    log_row(
        engine_operation_id=QUEUED_OP,
        http_status=202,
        outcome="queued",
        response={"operation_id": QUEUED_OP, "position": 1},
    )
    assert "Report not available" in inspect(operator_client, QUEUED_OP)


def test_a_rejected_input_shows_its_violations(operator_client: Client) -> None:
    log_row(
        http_status=422,
        outcome="error",
        error_code="guardrail_violation",
        engine_operation_id=TURN_OP,
        response={
            "error": {
                **engine_fixture("respond_guardrail_violation")["error"],
                "details": {
                    **engine_fixture("respond_guardrail_violation")["error"]["details"],
                    "model_calls": 2,
                    "elapsed_ms": 10,
                },
            }
        },
    )
    html = inspect(operator_client, TURN_OP)
    assert "Input rejected by a guardrail" in html
    assert "<td><code>minors_no_sexual_content</code></td>" in html
    assert "<dt>Input gate</dt><dd>2 model calls, 10 ms</dd>" in html  # #130
    assert "Turn report" not in html


def test_first_load_shows_the_latest_report(operator_client: Client, engine: FakeEngine) -> None:
    log_row(created_at=timezone.now() - timedelta(minutes=5))
    log_row(kind="archive_story", engine_operation_id="", response={"status": "archived"})
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    inspector = html.split('<div id="inspector">')[1]
    assert "Turn 1 (variant 1)" in inspector
    assert "<h3>Turn report</h3>" in inspector


def test_first_load_with_no_turn_asks_to_select_one(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "Select a turn to see its report." in html


def test_transcript_items_select_their_turn(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    inspector_url = reverse("stories:inspector", args=[STORY])
    assert f'hx-get="{inspector_url}?operation={TURN_OP}&amp;origin=generated"' in html
    assert f'href="/stories/{STORY}/?operation={TURN_OP}&amp;origin=generated"' in html


def test_the_no_js_link_renders_the_page_with_that_report(
    operator_client: Client, engine: FakeEngine
) -> None:
    log_row()
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/", {"operation": TURN_OP}).content.decode()
    assert "<h3>Turn report</h3>" in html.split('<div id="inspector">')[1]


# --- the perception trace link (#195) -------------------------------------------------------------

PERCEPTION = f"/stories/{STORY}/turns/1/perception/"
PERCEPTION_ROW = (
    f'<dt>Perception</dt><dd><a class="perception-link" href="{PERCEPTION}">'
    "Perception trace</a></dd>"
)
STRUCTURED_CAPS = ["budgets", "structured", "models", "episodic"]


def test_inspector_links_perception_for_a_structured_report(operator_client: Client) -> None:
    log_row(response=respond_json(capabilities=STRUCTURED_CAPS))
    html = inspect(operator_client, TURN_OP)
    revisions = html.split('<dl class="revisions">')[1].split("</dl>")[0]
    assert PERCEPTION_ROW in revisions
    assert revisions.index("<dt>Retrieval</dt>") < revisions.index("<dt>Perception</dt>")
    EngineOperation.objects.all().delete()
    log_row()  # a Lite report
    assert "perception-link" not in inspect(operator_client, TURN_OP)


def test_transcript_turn_inspector_links_perception(
    operator_client: Client, engine: FakeEngine
) -> None:
    history = engine_fixture("get_history")
    history["items"][0]["propositions"] = []  # a structured turn's input
    answer_page(engine, history=history)
    html = operator_client.get(f"/stories/{STORY}/", {"turn": "1"}).content.decode()
    inspector = html.split('<div id="inspector">')[1]
    link = f'<a class="perception-link" href="{PERCEPTION}">Perception trace</a>'
    assert f'<p class="explain">{link}</p>' in inspector  # not logged: the paragraph form
    assert "variant=" not in link
    answer_page(engine)  # a Lite turn, on a Lite story
    html = operator_client.get(f"/stories/{STORY}/", {"turn": "1"}).content.decode()
    assert "perception-link" not in html.split('<div id="inspector">')[1]


# --- a multi-character turn's renders, narrative and facts (#201) --------------------------------

TURN_URL = f"/stories/{STORY}/turn/"
NARRATIVE = "Tobin says they were at the docks. Jenni looks away. Jenni says she was there too."
FACTS = (
    '<dt>Interaction mode</dt><dd title="sequential">Sequential</dd>'
    '<dt>Audience filter</dt><dd title="full">Full</dd>'
    '<dt>Player view</dt><dd title="character">Character</dd>'
    '<dt>Composed</dt><dd title="polished">Polished</dd>'
)
REACTION = (
    '<li id="beat-3" class="reaction">↳ Jenni (reaction): beat 3 · Do · '
    '<span class="text">Jenni looks away.</span></li>'
)


def beat_json(
    number: int | None, actor: str | None, channel: str, text: str, **extra: Any
) -> dict[str, Any]:
    """A respond beat as the Engine returns it (C15)."""
    found: dict[str, Any] = {
        "turn": 1,
        "variant": 1,
        "beat": number,
        "kind": "player_input" if actor == MARA else f"beat_{channel}",
        "actor_character_id": actor,
        "render_character_ids": None,
        "channel": channel,
        "text": text,
        "operation_id": TURN_OP,
        "message_id": f"01JB2Z8Y5Q3W6K9M1N4P7M01{number or 0:02d}",
        "origin": "generated",
        "propositions": [],
        "fidelity": "content",
    }
    found.update(extra)
    return found


def multi_json(**changes: Any) -> dict[str, Any]:
    """The #200 demo turn: Mara asks, Tobin then Jenni render (sequential), Jenni's reaction
    narrated by Tobin, as Tobin's character view under perception ``full``."""
    beats = [
        beat_json(1, MARA, "say", "Where were you both?"),
        beat_json(2, TOBIN, "say", "At the docks."),
        beat_json(3, JENNI, "do", "Jenni looks away.", narrated_by=TOBIN),
        beat_json(4, JENNI, "say", "Me too."),
        beat_json(
            None,
            None,
            "message",
            NARRATIVE,
            kind="message_rendered",
            render_character_ids=[TOBIN, JENNI],
            propositions=None,
            fidelity=None,
        ),
    ]
    body = respond_json(
        beats=beats,
        order=[TOBIN, JENNI],
        interaction_mode="sequential",
        audience_filter="full",
        player_view="character",
        message={
            "message_id": "01M5R0QNTZV2CSB478YYC57Y3W",
            "text": NARRATIVE,
            "narrative": NARRATIVE,
            "mode": "polished",
        },
    )
    body.update(changes)
    return body


def multi_row(**fields: Any) -> EngineOperation:
    request = {"body": {"actor_character_id": MARA, "output_viewpoint": f"character:{TOBIN}"}}
    fields.setdefault("response", multi_json())
    return log_row(request={**request, "headers": {}}, **fields)


def renders_of(html: str) -> str:
    match = re.search(r'<section class="renders">.*?</section>', html, re.S)
    assert match, "no Renders section"
    return match.group(0)


def assert_named_renders(html: str) -> None:
    found = renders_of(html)
    assert "<h4>Renders</h4>" in found
    headings = re.findall(r'<h5 id="([^"]+)">(.*?)</h5>', found)
    assert headings == [
        ("render-input", "Input — Mara (player)"),
        ("render-1", "Tobin"),
        ("render-2", "Jenni"),
        ("render-other", "Other beats"),
    ]
    tobin = found.split('<h5 id="render-1">')[1].split("<h5")[0]
    assert (
        '<li id="beat-2">beat 2 · Say · <span class="text">At the docks.</span>'
        f'<ol class="reactions">{REACTION}</ol></li>'
    ) in tobin
    jenni = found.split('<h5 id="render-2">')[1].split("<h5")[0]
    assert '<li id="beat-4">beat 4 · Say · <span class="text">Me too.</span></li>' in jenni
    assert "Jenni looks away." not in jenni
    other = found.split('<h5 id="render-other">')[1]
    assert f'<li>Tobin, Jenni: item 5 · Message · <span class="text">{NARRATIVE}</span>' in other


def test_live_multi_turn_shows_renders_and_facts(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", RESPOND, 200, json=multi_json())
    answer_page(engine, story=play_story())
    response = operator_client.post(
        TURN_URL, turn_data(snapshot=snapshot_for(), actor=MARA), headers=HTMX
    )
    html = response.content.decode()
    assert_named_renders(html)
    assert f"{FACTS}</dl>" in html
    assert f'<blockquote class="reply">{NARRATIVE}</blockquote>' in html
    assert "differs from the narrative" not in html  # the text is the same
    # The section sits after the reply block and before the output guardrail.
    assert html.index('<blockquote class="reply">') < html.index('<section class="renders">')
    assert html.index('<section class="renders">') < html.index("<h4>Output guardrail</h4>")


def test_logged_turn_shows_renders_from_the_stored_response(
    operator_client: Client, engine: FakeEngine
) -> None:
    multi_row()
    serve_page(engine)
    html = operator_client.get(f"/stories/{STORY}/", {"operation": TURN_OP}).content.decode()
    inspector = html.split('<div id="inspector">')[1]
    assert_named_renders(inspector)
    assert FACTS in inspector
    assert f'<blockquote class="reply">{NARRATIVE}</blockquote>' in inspector


def test_logged_row_reads_field_by_field(operator_client: Client) -> None:
    """A malformed beat is skipped and a non-string ``order`` entry dropped; the rest shows."""
    body = multi_json(order=[TOBIN, 7, JENNI])
    body["beats"].insert(1, {"beat": "two"})
    multi_row(response=body)
    found = renders_of(inspect(operator_client, TURN_OP))
    assert '<h5 id="render-2">' in found
    assert "item 5" in found  # the reply's place among the parsed beats


def test_htmx_inspector_labels_from_snapshot(operator_client: Client, engine: FakeEngine) -> None:
    multi_row()
    html = inspect(operator_client, TURN_OP, snapshot=snapshot_for())
    assert_named_renders(html)
    # Without a snapshot (or with a bad one): short ids, silently, and no Engine read.
    for extra in ({}, {"snapshot": "tampered"}):
        html = renders_of(inspect(operator_client, TURN_OP, **extra))
        assert f'<h5 id="render-1">{short_id(TOBIN)}</h5>' in html
        assert f"Input — {short_id(MARA)}" in html
        assert "Tobin</h5>" not in html
        assert "Mara (player)" not in html
    assert engine.requests == []


def test_polled_turn_shows_renders(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        RESPOND,
        202,
        json={"operation_id": QUEUED_OP, "position": 1},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    answer_page(engine, story=play_story())
    operator_client.post(TURN_URL, turn_data(snapshot=snapshot_for(), actor=MARA), headers=HTMX)
    committed = operation_json(
        "committed",
        result={"status": 200, "body": multi_json()},
        viewpoint=f"character:{TOBIN}",
        timeline_revision=2,
    )
    engine.respond("GET", OPERATION, 200, json=committed)
    html = poll(operator_client)
    assert_named_renders(html)
    assert FACTS in html
    assert calls(engine, "GET", f"/stories/{STORY}") == []  # labels from the posted snapshot


def test_nothing_perceived_narrative_line(operator_client: Client) -> None:
    body = multi_json(
        audience_filter="nothing_perceived",
        beats=[],
        message={
            "message_id": "01M5R0QNTZV2CSB478YYC57Y3W",
            "text": "",
            "narrative": "",
            "mode": "deterministic",
        },
    )
    multi_row(response=body)
    html = inspect(operator_client, TURN_OP)
    notice = (
        '<p class="notice">This viewpoint perceived nothing this turn — the narrative is empty</p>'
    )
    assert notice in html
    assert '<blockquote class="reply">' not in html
    found = renders_of(html)
    assert html.index(notice) < html.index('<section class="renders">')
    assert found.count("No beats (see warnings)") == 2
    assert '<dd title="nothing_perceived">Nothing perceived' in html


def test_differing_text_under_more(operator_client: Client) -> None:
    body = multi_json()
    body["message"]["text"] = "The plain reply text."
    multi_row(response=body)
    html = inspect(operator_client, TURN_OP)
    assert (
        f'<blockquote class="reply">{NARRATIVE}</blockquote>\n'
        '<details class="more"><summary>More</summary><p>Reply text (differs from the '
        'narrative)</p><blockquote class="reply">The plain reply text.</blockquote></details>'
    ) in html
    # An empty narrative falls back to the text, with nothing under More.
    EngineOperation.objects.all().delete()
    body["message"]["narrative"] = "  "
    multi_row(response=body)
    html = inspect(operator_client, TURN_OP)
    assert '<blockquote class="reply">The plain reply text.</blockquote>' in html
    assert "differs from the narrative" not in html


def test_lite_turn_has_no_renders_or_m5_facts(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** a Lite turn (``audience_filter: none``, none of the M5 fields) shows its
    reply text and no Renders section or M5 fact, live or logged."""
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answer_page(engine)
    answer = operator_client.post(TURN_URL, turn_data(), headers=HTMX).content.decode()
    live = answer.split('<div id="turn-status"', 1)[0]  # the inspector, before the out-of-band
    log_row()
    logged = inspect(operator_client, TURN_OP)
    text = respond_json()["message"]["text"]
    for html in (live, logged):
        assert "renders" not in html
        for label in ("Interaction mode", "Audience filter", "Player view</dt>", "Composed"):
            assert label not in html
        assert '<dl class="facts">' not in html
        assert f'<blockquote class="reply">{escape(text)}</blockquote>' in html


def test_not_played_here_turn_has_no_renders(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** a turn not in the log says so, with no Renders section and no
    ``/operations`` read."""
    html = inspect(operator_client, "01JB2Z8Y5Q3W6K9M1N4P7R0ZZZ", snapshot=snapshot_for())
    assert "Report not available — this turn was not played from this Console" in html
    assert "renders" not in html
    assert not any(r.url.path.startswith("/operations") for r in engine.requests)


def test_transcript_reaction_items_are_indented(
    operator_client: Client, engine: FakeEngine
) -> None:
    """A reaction's item is classed ``reaction`` with "↳ " before its speaker; **complement:**
    a player-narrated item (C16) is neither."""
    answer_page(engine, story=play_story(), history=engine_fixture("get_history_filtered"))
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "li.reaction, tr.reaction td.text { padding-left: 1.5em; }" in html
    items = re.findall(r'<li class="item[^"]*".*?</li>', html, re.S)
    reaction = next(i for i in items if "Tobin glances toward the kitchen." in i)
    assert reaction.startswith('<li class="item channel-do reaction"')
    assert "↳ <strong>Tobin (narrated by Jenni)</strong>" in reaction
    player = next(i for i in items if "The ferry is late again." in i)
    assert "reaction" not in player.split(">", 1)[0]
    assert "↳" not in player
    assert "<strong>Tobin (narrated by the player)</strong>" in player
    # The turn links carry the page's signed snapshot to the inspector (C4).
    inspector_url = reverse("stories:inspector", args=[STORY])
    assert re.search(
        rf'hx-get="{inspector_url}\?operation=[^"]+" hx-include="\[name=snapshot\]"', html
    )


# --- how a multi-character turn was composed (#203) ---------------------------------------------

KNIFE = "01JB2Z8Y5Q3W6K9M1N4P7R0KN1"
ENTITY = f"/stories/{STORY}/inspect/entities/{KNIFE}/"


def composed_json(**changes: Any) -> dict[str, Any]:
    """The #203 demo on #201's turn: Tobin and Jenni named simultaneous, both reaching for the
    knife; a structured turn, so the knife links its entity page."""
    body = multi_json(
        interaction_mode="simultaneous",
        capabilities=STRUCTURED_CAPS,
        roles={
            TOBIN: {"role": "primary", "source": "request"},
            JENNI: {"role": "primary", "source": "request"},
            MARA: {"role": "silent", "source": "default"},
        },
        adjudications=[{"object": KNIFE, "winner": TOBIN, "loser": JENNI, "rule": "request_order"}],
        reconcile=[
            {
                "proposition": "holds:knife",
                "winner_beat": 2,
                "loser_beat": 4,
                "rule": "request_order",
            }
        ],
        escalation_deferred=[{"character_id": JENNI, "reason": "budget", "needed": "speech"}],
    )
    body.update(changes)
    return body


def composition_of(html: str) -> str:
    match = re.search(r'<section class="composition">.*?</section>', html, re.S)
    assert match, "no Composition section"
    return match.group(0)


def assert_composition(html: str) -> None:
    found = composition_of(html)
    assert re.findall(r"<h5>(.*?)</h5>", found) == [
        "Roles",
        "Reactions that needed a render",
        "Adjudications (simultaneous)",
        "Reconcile",
    ]
    rows = re.findall(r"<tr><td>([^<]*)</td><td><span[^>]*>(\w+)</span></td>", found)
    assert rows == [("Mara (player)", "Silent"), ("Tobin", "Primary"), ("Jenni", "Primary")]
    assert '<td><span title="request">this request</span></td>' in found
    assert re.search(rf'<a href="{ENTITY}\?as_of=1&amp;viewpoint=omniscient"><code', found)
    assert "<td>Tobin</td><td>Jenni</td>" in found
    assert '<a href="#beat-2">beat 2</a></td><td><a href="#beat-4">beat 4</a>' in found
    assert 'Jenni\'s reaction needed <span title="speech">speech</span>' in found
    # After the Renders section, before the output guardrail.
    assert html.index("</section>", html.index('<section class="renders">')) < html.index(
        '<section class="composition">'
    )
    assert html.index('<section class="composition">') < html.index("<h4>Output guardrail</h4>")


def test_live_simultaneous_turn_shows_composition(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", RESPOND, 200, json=composed_json())
    answer_page(engine, story=play_story())
    response = operator_client.post(
        TURN_URL, turn_data(snapshot=snapshot_for(), actor=MARA), headers=HTMX
    )
    assert_composition(response.content.decode())


def test_live_answer_keeps_a_malformed_entry_under_more() -> None:
    """The live answer's model drops a bad entry (C2); the panel puts it back under More."""
    bad = {"object": KNIFE, "winner": TOBIN, "rule": "state"}
    body = composed_json()
    body["adjudications"].append(bad)
    found = RespondResponse.from_json(body)
    assert found is not None
    inspector = result_inspector(STORY, found, "", TURN_OP)
    assert inspector.composition is not None
    assert inspector.composition.more == {"adjudications": [bad]}
    assert len(inspector.composition.adjudications) == 1


def test_polled_turn_shows_composition(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        RESPOND,
        202,
        json={"operation_id": QUEUED_OP, "position": 1},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    answer_page(engine, story=play_story())
    operator_client.post(TURN_URL, turn_data(snapshot=snapshot_for(), actor=MARA), headers=HTMX)
    committed = operation_json(
        "committed",
        result={"status": 200, "body": composed_json()},
        viewpoint=f"character:{TOBIN}",
        timeline_revision=2,
    )
    engine.respond("GET", OPERATION, 200, json=committed)
    assert_composition(poll(operator_client))


def test_logged_turn_shows_composition_from_row_response(
    operator_client: Client, engine: FakeEngine
) -> None:
    multi_row(response=composed_json())
    serve_page(engine)
    html = operator_client.get(f"/stories/{STORY}/", {"operation": TURN_OP}).content.decode()
    assert_composition(html.split('<div id="inspector">')[1])
    # The HTMX inspector, labelled from the transcript link's snapshot, with no Engine read.
    engine.reset()
    assert_composition(inspect(operator_client, TURN_OP, snapshot=snapshot_for()))
    assert engine.requests == []


def test_not_played_here_turn_has_no_composition_and_no_operations_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** a turn not in the log makes no ``/operations`` read and shows no
    Composition."""
    html = inspect(operator_client, "01JB2Z8Y5Q3W6K9M1N4P7R0ZZZ", snapshot=snapshot_for())
    assert "Report not available — this turn was not played from this Console" in html
    assert "Composition" not in html
    assert not any("/operations/" in r.url.path for r in engine.requests)


def test_lite_turn_has_no_composition(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** a Lite turn's inspector, live or logged, never contains "Composition"."""
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answer_page(engine)
    live = operator_client.post(TURN_URL, turn_data(), headers=HTMX).content.decode()
    log_row()
    for html in (live, inspect(operator_client, TURN_OP)):
        assert "Composition" not in html
        assert "composition" not in html


def test_errored_row_has_no_composition(operator_client: Client) -> None:
    """**Complement:** a logged row with an error code shows no Composition, whatever its
    response holds; a rejected input neither."""
    multi_row(response=composed_json(), http_status=409, error_code="revision_conflict")
    assert "Composition" not in inspect(operator_client, TURN_OP)
    found = row_inspector(EngineOperation.objects.get())
    assert found.composition is None


def test_a_turn_with_only_roles_shows_roles_alone(operator_client: Client) -> None:
    roles = {JENNI: {"role": "primary", "source": "request"}}
    multi_row(response=multi_json(roles=roles))
    found = composition_of(inspect(operator_client, TURN_OP))
    assert re.findall(r"<h5>(.*?)</h5>", found) == ["Roles"]

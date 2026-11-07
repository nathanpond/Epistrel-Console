"""Each turn's world changes on the play page (#245): the inspector's World strip on the live,
polled and logged paths (regenerate and correct results included), the transcript's Incomplete
badge (D13), and the States level in the play snapshot. Served by the schema-validating fake
Engine, under #239's Structured v3 and #232's ``m6/story.json`` cast (Mara the player, Alice,
Bob)."""

import copy
import re
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from django.template.loader import render_to_string
from django.test import Client, override_settings
from django.urls import reverse
from django.utils.html import escape

from console.effects import (
    INCOMPLETE_TEXT,
    INCOMPLETE_TITLE,
    OUTBOX_LIST_NOTICE,
    OUTBOX_SECTION_NOTICE,
)
from console.engine.models import EffectiveConfig, RespondResponse, Story, TurnClock
from console.inspect import short_id
from console.models import EngineOperation
from console.turn_world import turn_world
from console.views.play import SNAPSHOT_SALT, PlayStory, result_inspector
from console.world import clock_change_text
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import structured_v3
from tests.play.helpers import (
    HTMX,
    OPERATION,
    QUEUED_OP,
    RESPOND,
    answer_page,
    error_json,
    operation_json,
    turn_data,
)

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
OP = "01JM6QP0000000000000000245"
CORRECT_OP = "01JM6QP0000000000000000061"  # corrected.json's beat operation
OUTBOX = "01JM6BX0000000000000000011"  # the dispatched State change's outbox row
REQUIRED = ("01JM6BX0000000000000000012", "01JM6BX0000000000000000013")
STUB_URLS = "tests.urls_with_engine_operation"
# The real routes minus the World/Effects sections, ``inspect:reconciliation`` (#249) included.
NO_RECONCILIATION = "tests.inspect.urls_world_no_sections"
EFFECTIVE = "/stories/{story_id}/effective-config"
PLAY = f"/stories/{STORY}/"
TURN_URL = f"/stories/{STORY}/turn/"
RECONCILIATION = f"/stories/{STORY}/inspect/effects/reconciliation/"
NOTICE_START = OUTBOX_SECTION_NOTICE.partition("{link}")[0]  # E17: #325 registered it
# D11's old notice: a urlconf without ``inspect:outbox`` (D19's fallback).
UNLINKED_NOTICE_START = OUTBOX_LIST_NOTICE.partition("{link}")[0]
ROWS = (
    '<tr><td>gold</td><td>—</td><td>\u22125 (120 → 115)</td><td title="committed">Committed</td>'
    "<td></td><td>paid for the lantern</td><td></td>"
    '<td>Source: <span title="turn">Turn</span></td></tr>',
    '<tr><td>gold</td><td>—</td><td>\u22123 (115 → 112)</td><td title="held">Held</td>'
    '<td title="reservation">Reservation</td><td>held for the ferry fare</td><td></td>',
    "<tr><td>bank_balance</td><td>Mara (player)</td><td>\u221250 (300 → undefined)</td>"
    '<td title="pending">Pending</td><td title="conditional">Conditional</td>'
    "<td>asked the bank for the fare</td><td>requested of the consumer</td>"
    '<td>Source: <span title="turn">Turn</span><br>Effect id: '
    '<code class="outbox-id">01JM6BX0000000000000000010</code></td></tr>',
    "<tr><td>bank_balance</td><td>Mara (player)</td><td>\u221220 (300 → 280)</td>"
    '<td title="pending">Pending</td><td title="conditional">Conditional</td>'
    "<td>wired the shop</td><td>dispatched · outbox ",
    '<tr><td>gold</td><td>Alice</td><td>\u22125 (5 → 0)</td><td title="committed">Committed</td>'
    "<td></td><td>paid Bob back</td><td>capped from 8; transfer out; other side Untracked</td>"
    '<td>Source: <span title="turn">Turn</span><br>Kind: '
    '<span title="transfer_out">Transfer out</span><br>Declared: 8</td></tr>',
)
SETTLED_ROW = (
    "<tr><td>bank_balance</td><td>Mara (player)</td><td>\u221250 (300 → 250)</td>"
    '<td title="committed">Committed</td><td title="conditional">Conditional</td>'
    "<td>the bank confirmed the fare</td><td></td>"
    '<td>Source: <span title="settlement">Settlement</span></td></tr>'
)
NOT_UNDONE = (
    f'<code class="outbox-id">{REQUIRED[0]}</code> · bank · <span title="unknown">Unknown</span>'
    " · balance</li>",
    f'<code class="outbox-id">{REQUIRED[1]}</code> · shop · '
    '<span title="imported_unsettled">Imported unsettled</span></li>',
)
M6_FIELDS = (
    "clock",
    "state_effects",
    "settled_effects",
    "reconciliation",
    "reconciliation_required",
)


# --- answers, pages and log rows ------------------------------------------------------------------


def world_json(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m6/respond_world"))
    for key, value in changes.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value
    return body


def m5_json() -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("respond")
    return body


def with_world(body: dict[str, Any], *, keep_required: bool = False) -> dict[str, Any]:
    """``body`` with ``respond_world.json``'s M6 fields laid over (its own
    ``reconciliation_required`` kept when ``keep_required``)."""
    world = world_json()
    found = copy.deepcopy(body)
    for key in M6_FIELDS:
        if keep_required and key == "reconciliation_required":
            continue
        found[key] = world[key]
    return found


def m6_story() -> dict[str, Any]:
    story: dict[str, Any] = engine_fixture("m6/story")
    return story


def m6_history(*, operation: str = OP, turn: int = 4, incomplete: bool = False) -> dict[str, Any]:
    """``get_history.json`` moved onto the M6 cast: turn ``turn``'s two items, recorded by
    ``operation`` (``incomplete``: its summary marks the turn staged, ``message`` null)."""
    history: dict[str, Any] = copy.deepcopy(engine_fixture("get_history"))
    for item in history["items"]:
        item.update(turn=turn, operation_id=operation)
        if item.get("actor_character_id"):
            item["actor_character_id"] = MARA
        if item.get("render_character_ids"):
            item["render_character_ids"] = [ALICE]
    summary = history["turns"][0]
    summary.update(turn=turn)
    if incomplete:
        summary.update(incomplete=True, message=None)
    history.update(as_of=turn)
    return history


def serve(engine: FakeEngine, **history: Any) -> None:
    """The play page's reads for the M6 story, its configuration Structured v3."""
    answer_page(engine, story=m6_story(), history=m6_history(**history))
    engine.side_effect("GET", EFFECTIVE, lambda request: httpx.Response(200, json=structured_v3()))


def snapshot() -> str:
    story = Story.from_json(m6_story())
    config = EffectiveConfig.from_json(structured_v3())
    assert story is not None
    assert config is not None
    return PlayStory.of(story, 45.0, config=config).signed()


def log_row(**fields: Any) -> EngineOperation:
    values: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "story_id": STORY,
        "kind": "respond",
        "http_method": "POST",
        "path": f"/stories/{STORY}/respond",
        "request": {"body": {"actor_character_id": MARA}, "headers": {}},
        "http_status": 200,
        "engine_operation_id": OP,
        "timeline_revision": 13,
        "config_revision": 6,
        "idempotency_key": "01JM6QP0000000000000000K46",
        "outcome": "ok",
        "response": world_json(),
    }
    values.update(fields)
    return EngineOperation.objects.create(**values)


def post_turn(client: Client) -> str:
    response = client.post(
        TURN_URL,
        turn_data(snapshot=snapshot(), actor=MARA),
        headers=HTMX,
    )
    assert response.status_code == 200
    return response.content.decode()


def queue_turn(client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        RESPOND,
        202,
        json=engine_fixture("respond_queued") | {"operation_id": QUEUED_OP},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    post_turn(client)


def poll(client: Client, engine: FakeEngine, body: dict[str, Any]) -> str:
    committed = operation_json(
        "committed",
        result={"status": 200, "body": body},
        story_id=STORY,
        viewpoint="omniscient",
        timeline_revision=13,
    )
    engine.respond("GET", OPERATION, 200, json=committed)
    url = reverse("stories:poll", args=[STORY, QUEUED_OP])
    params = {"viewpoint": "omniscient", "deadline": "45", "position": "1"}
    response = client.get(url, params, headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


def inspect(client: Client, operation: str = OP, **extra: str) -> str:
    url = reverse("stories:inspector", args=[STORY])
    response = client.get(url, {"operation": operation, **extra}, headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


def page_inspector(client: Client, **query: str) -> str:
    response = client.get(PLAY, query)
    assert response.status_code == 200
    return response.content.decode().split('<div id="inspector">')[1]


def strip_of(html: str) -> str:
    match = re.search(r'<section class="world-strip">.*?</section>', html, re.S)
    assert match, "no World strip"
    return match.group(0)


def call_log(engine: FakeEngine) -> list[tuple[str, str]]:
    return [(r.method, r.url.path) for r in engine.requests]


def assert_full_strip(strip: str, *, linked: bool, names: bool = True) -> None:
    assert strip.startswith('<section class="world-strip">\n<h4>World</h4>')
    assert (
        'World clock: <span class="clock" title="offset 130 min">+02h 10m</span> → '
        '<span class="clock" title="offset 135 min">+02h 15m</span>'
    ) in strip
    assert "<caption>State changes this turn</caption>" in strip
    rows = ROWS if names else ROWS[:2]
    positions = [strip.index(row) for row in rows]
    assert positions == sorted(positions)
    assert "<caption>Settled since the last turn</caption>" in strip
    if names:
        assert SETTLED_ROW in strip
    assert "<p>External effects that stay listed for reconciliation</p>" in strip
    for outbox, line in zip(REQUIRED, NOT_UNDONE, strict=True):
        if linked:
            line = line.replace(
                f'<code class="outbox-id">{outbox}</code>',
                f'<a href="{RECONCILIATION}#outbox-{outbox}">'
                f'<code class="outbox-id">{outbox}</code></a>',
            )
        assert line in strip
    if linked:
        assert (
            f'<p class="reconcile"><a href="{RECONCILIATION}">2 effects to reconcile</a></p>'
            in (strip)
        )
    else:
        assert '<p class="reconcile">2 effects to reconcile</p>' in strip
    notice = NOTICE_START if linked else UNLINKED_NOTICE_START
    assert f'<p class="notice">{notice}' in strip
    order = [
        strip.index(marker)
        for marker in (
            "World clock",
            "State changes this turn",
            "Settled since the last turn",
            "External effects that stay listed",
            "effects to reconcile",
            notice,
        )
    ]
    assert order == sorted(order)


# --- the live, polled and logged paths ------------------------------------------------------------


@override_settings(ROOT_URLCONF=STUB_URLS)
def test_live_turn_shows_the_strip(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RESPOND, 200, json=world_json())
    serve(engine)
    html = post_turn(operator_client)
    strip = strip_of(html)
    assert_full_strip(strip, linked=True)
    # The strip sits after the facts and before the reply.
    assert html.index('<section class="world-strip">') < html.index('<blockquote class="reply">')
    assert "Viewpoint:" not in strip  # an omniscient answer


@override_settings(ROOT_URLCONF=NO_RECONCILIATION)
def test_live_turn_count_is_plain_text_without_the_reconciliation_list(
    operator_client: Client, engine: FakeEngine
) -> None:
    """D19: under a urlconf without ``inspect:reconciliation`` (the real one registers it since
    #249) the count and outbox ids are unlinked."""
    engine.respond("POST", RESPOND, 200, json=world_json())
    serve(engine)
    strip = strip_of(post_turn(operator_client))
    assert_full_strip(strip, linked=False)
    assert "#outbox-" not in strip


def test_unchanged_clock_reads_unchanged() -> None:
    clock = {
        "before": {"offset": 135, "display": "+02h 15m"},
        "after": world_json()["clock"]["after"],
    }
    world = turn_world(world_json(clock=clock), STORY, {})
    assert world is not None
    assert world.clock_unchanged
    strip = render_to_string("console/play/_world_strip.html", {"world": world})
    assert (
        'World clock: <span class="clock" title="offset 135 min">+02h 15m</span> (unchanged)'
    ) in strip
    parsed = TurnClock.from_json(clock)
    assert parsed is not None
    assert world.clock_text == clock_change_text(parsed.before, parsed.after)
    assert re.sub(r"<[^>]+>", "", strip.split("World clock: ")[1].split("</p>")[0]) == (
        world.clock_text
    )


def test_changed_clock_text_matches_clock_change_text() -> None:
    world = turn_world(world_json(), STORY, {})
    assert world is not None
    strip = render_to_string("console/play/_world_strip.html", {"world": world})
    shown = re.sub(r"<[^>]+>", "", strip.split("World clock: ")[1].split("</p>")[0])
    assert shown == world.clock_text == "+02h 10m → +02h 15m"


@override_settings(ROOT_URLCONF=STUB_URLS)
def test_outbox_ids_are_full_and_linked(operator_client: Client) -> None:
    log_row()
    strip = strip_of(inspect(operator_client))
    assert len(OUTBOX) == 26
    assert (
        f'dispatched · outbox <a href="{RECONCILIATION}#outbox-{OUTBOX}">'
        f'<code class="outbox-id">{OUTBOX}</code></a>'
    ) in strip
    for outbox in REQUIRED:
        assert (
            f'<a href="{RECONCILIATION}#outbox-{outbox}"><code class="outbox-id">{outbox}</code>'
            in (strip)
        )
    assert NOTICE_START in strip
    # **Complement:** with no outbox id on any row or line, no notice.
    EngineOperation.objects.all().delete()
    effects = [e for e in world_json()["state_effects"] if "outbox_id" not in e]
    log_row(response=world_json(state_effects=effects, reconciliation_required=None))
    strip = strip_of(inspect(operator_client))
    assert 'outbox-id">01JM6BX0000000000000000010' in strip  # an effect id doesn't trigger it
    assert NOTICE_START not in strip
    assert "there is no list of every row yet" not in strip


def test_count_zero_has_no_link(operator_client: Client) -> None:
    log_row(response=world_json(reconciliation={"count": 0}))
    strip = strip_of(inspect(operator_client))
    assert "to reconcile" not in strip
    # With nothing else: no strip at all.
    EngineOperation.objects.all().delete()
    log_row(response=m5_json() | {"reconciliation": {"count": 0}})
    html = inspect(operator_client)
    assert "world-strip" not in html
    assert "Turn report" in html


def guardrail_error() -> dict[str, Any]:
    error: dict[str, Any] = copy.deepcopy(engine_fixture("respond_guardrail_violation")["error"])
    return error


@pytest.mark.parametrize(
    "fields",
    [
        {"http_status": 423, "outcome": "error", "error_code": "story_busy"},
        {"http_status": 422, "outcome": "error", "error_code": "guardrail_violation"},
        # No error_code, only input violations: the guard's second half on its own.
        {"http_status": 200, "outcome": "ok", "error_code": ""},
    ],
    ids=["story-busy", "guardrail-violation", "violations-only"],
)
def test_error_or_violation_row_shows_no_strip(
    operator_client: Client, fields: dict[str, Any]
) -> None:
    """**Complement** (#277): ``row_inspector`` builds the strip only for a row with no
    ``error_code`` and no input violations, even when the logged body carries M6 world fields."""
    violations = fields["error_code"] != "story_busy"
    response = world_json() | ({"error": guardrail_error()} if violations else {})
    log_row(response=response, **fields)
    html = inspect(operator_client)
    assert "world-strip" not in html
    if violations:
        assert "Input rejected by a guardrail" in html


def test_character_viewpoint_notes_hidden_states(operator_client: Client) -> None:
    log_row(response=world_json(output_viewpoint=f"character:{ALICE}"))
    html = inspect(operator_client, snapshot=snapshot())
    strip = strip_of(html)
    assert '<p class="viewpoint">Viewpoint: Alice — hidden States are left out</p>' in strip
    # **Complement:** an omniscient answer has no such line, and a State the answer doesn't list
    # appears nowhere.
    EngineOperation.objects.all().delete()
    log_row()
    html = inspect(operator_client, snapshot=snapshot())
    assert "Viewpoint:" not in strip_of(html)
    assert "trust" not in html


def test_polled_turn_shows_the_strip(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    queue_turn(operator_client, engine)
    html = poll(operator_client, engine, world_json())
    assert_full_strip(strip_of(html), linked=True)


def test_logged_turn_selected_shows_the_strip(operator_client: Client, engine: FakeEngine) -> None:
    log_row()
    serve(engine)
    assert_full_strip(strip_of(page_inspector(operator_client, operation=OP)), linked=True)
    # The HTMX inspector, labelled from the snapshot, with no Engine read.
    engine.reset()
    assert_full_strip(strip_of(inspect(operator_client, snapshot=snapshot())), linked=True)
    assert engine.requests == []
    # **Complement:** without a snapshot the scope column shows short ids.
    strip = strip_of(inspect(operator_client))
    assert_full_strip(strip, linked=True, names=False)
    assert f"<td>{short_id(MARA)}</td>" in strip
    assert f"<td>{short_id(ALICE)}</td>" in strip
    assert "Mara (player)" not in strip


def test_first_load_shows_the_latest_turns_strip(
    operator_client: Client, engine: FakeEngine
) -> None:
    log_row()
    serve(engine)
    assert_full_strip(strip_of(page_inspector(operator_client)), linked=True)


def test_one_body_gives_the_same_world_on_every_path() -> None:
    """The live answer (``asdict`` plus ``unparsed``), the poll's ``result.body`` and a logged
    row's ``response`` give the same ``TurnWorld``."""
    body = world_json()
    body["state_effects"].append({"state": "gold", "reason": "no status"})
    parsed = RespondResponse.from_json(body)
    assert parsed is not None
    live = result_inspector(STORY, parsed, "", OP)
    polled = result_inspector(STORY, parsed, "", OP, response=body)
    logged = turn_world(body, STORY, {})
    assert live.world == polled.world == logged
    assert live.world is not None
    assert live.world.unread == ("1 State change couldn't be read",)


def test_regenerate_result_shows_the_strip(operator_client: Client, engine: FakeEngine) -> None:
    body = with_world(engine_fixture("regenerate")) | {"turn": 4, "variant": 2}
    log_row(kind="regenerate", path=f"/stories/{STORY}/regenerate", response=body)
    serve(engine)
    inspector = page_inspector(operator_client, turn="4")
    assert "<dt>Previous variant</dt>" in inspector
    strip = strip_of(inspector)
    assert_full_strip(strip, linked=True)  # the not-undone lines, linked since #249


@override_settings(ROOT_URLCONF=STUB_URLS)
def test_correct_result_shows_the_strip(operator_client: Client, engine: FakeEngine) -> None:
    corrected = engine_fixture("m6/corrected")
    body = with_world(corrected, keep_required=True)
    row = log_row(
        kind="correct",
        path=f"/stories/{STORY}/correct",
        engine_operation_id=CORRECT_OP,
        response=body,
    )
    serve(engine, operation=CORRECT_OP)
    strip = strip_of(page_inspector(operator_client, turn="4"))
    (required,) = corrected["reconciliation_required"]
    outbox = required["outbox_id"]
    assert (
        f'<ul class="not-undone"><li><a href="{RECONCILIATION}#outbox-{outbox}">'
        f'<code class="outbox-id">{outbox}</code></a> · bank · '
        '<span title="unknown">Unknown</span> · balance</li></ul>'
    ) in strip
    assert "State changes this turn" in strip
    world_log = call_log(engine)
    # **Complement:** the same path with respond.json makes the same reads.
    engine.reset()
    row.response = m5_json()
    row.save()
    html = page_inspector(operator_client, turn="4")
    assert "world-strip" not in html
    assert call_log(engine) == world_log


def test_malformed_entry_is_kept_raw(operator_client: Client) -> None:
    good = world_json()["state_effects"][0]
    bad = {k: v for k, v in world_json()["state_effects"][1].items() if k != "status"}
    log_row(response=m5_json() | {"state_effects": [good, bad]})
    html = inspect(operator_client)
    strip = strip_of(html)
    assert strip.count("<tr><td>gold</td>") == 1
    assert '<p class="warning">1 State change couldn&#x27;t be read</p>' in strip
    more = strip.split('<details class="more"><summary>More</summary>')[1]
    assert "<p>State changes</p><pre>" in more
    assert escape('"held for the ferry fare"') in more
    # **Complement:** the rest of the inspector renders.
    assert '<blockquote class="reply">' in html
    assert "<h3>Turn report</h3>" in html
    # A list field that isn't a list reads the same way.
    EngineOperation.objects.all().delete()
    log_row(response=m5_json() | {"state_effects": {}})
    strip = strip_of(inspect(operator_client))
    assert '<p class="warning">1 State change couldn&#x27;t be read</p>' in strip
    assert "<caption>" not in strip
    assert "<p>State changes</p><pre>{}</pre>" in strip


def test_every_list_counts_its_unread_entries() -> None:
    world = turn_world(
        m5_json()
        | {
            "settled_effects": [{"state": "gold"}, {"state": "gold"}],
            "reconciliation_required": [{"provider": "bank"}],
            "clock": {"before": 3},
            "reconciliation": {"count": True},
        },
        STORY,
        {},
    )
    assert world is not None
    assert world.unread == (
        "2 settled State changes couldn't be read",
        "1 external effect couldn't be read",
    )
    assert world.clock is None
    assert world.reconcile is None
    assert [label for label, _ in world.raw_more] == [
        "Clock",
        "Settled State changes",
        "External effects",
        "Reconciliation",
    ]


def test_m5_answer_has_no_strip(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RESPOND, 200, json=m5_json())
    serve(engine)
    html = post_turn(operator_client)
    assert "world-strip" not in html
    assert "Turn report" in html


def test_error_answer_has_no_strip(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        RESPOND,
        423,
        json=error_json("story_busy", "busy", operation_id=OP),
        headers={"Retry-After": "2"},
    )
    serve(engine)
    html = post_turn(operator_client)
    assert "Turn refused" in html
    assert "world-strip" not in html


FORBIDDEN = ("/states", "/clock", "/reconciliation", "/effective-config")


def test_strip_makes_no_engine_read(operator_client: Client, engine: FakeEngine) -> None:
    """Each path's call log with ``respond_world.json`` equals the same path's log with
    ``respond.json``: no ``…/states``, ``…/clock``, ``…/reconciliation`` or
    ``…/effective-config`` read is added."""

    def turn(body: dict[str, Any]) -> None:
        engine.respond("POST", RESPOND, 200, json=body)
        post_turn(operator_client)

    def polled(body: dict[str, Any]) -> None:
        queue_turn(operator_client, engine)
        engine.reset()
        poll(operator_client, engine, body)

    def logged(query: dict[str, str]) -> Callable[[dict[str, Any]], None]:
        def run(body: dict[str, Any]) -> None:
            EngineOperation.objects.all().delete()
            log_row(response=body)
            engine.reset()
            page_inspector(operator_client, **query)

        return run

    def htmx(body: dict[str, Any]) -> None:
        EngineOperation.objects.all().delete()
        log_row(response=body)
        engine.reset()
        inspect(operator_client, snapshot=snapshot())

    serve(engine)
    paths = {
        "turn": turn,
        "poll": polled,
        "turn=N": logged({"turn": "4"}),
        "operation": logged({"operation": OP}),
        "htmx": htmx,
    }
    for name, run in paths.items():
        logs = []
        for body in (m5_json(), world_json()):
            engine.reset()
            run(body)
            logs.append(call_log(engine))
        assert logs[0] == logs[1], name
        added = [call for call in logs[1] if call not in logs[0]]
        assert not [c for c in added if c[1].endswith(FORBIDDEN)], name


# --- the Incomplete badge (D13) -------------------------------------------------------------------

BADGE = f'<span class="badge incomplete" title="{INCOMPLETE_TITLE}">{INCOMPLETE_TEXT}</span>'


def transcript_of(html: str) -> str:
    return html.split('<div id="transcript"')[1].split("</ol>")[0]


def test_incomplete_turn_is_badged(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, incomplete=True)
    transcript = transcript_of(operator_client.get(PLAY).content.decode())
    assert transcript.count('class="badge incomplete"') == 1
    # The real urlconf links it to the Engine operation page (#250).
    assert re.search(r">T4</a> <a href=\"[^\"]+\">" + re.escape(BADGE), transcript)
    # **Complement:** get_history.json (no ``incomplete``) has no badge.
    engine.reset()
    serve(engine)
    assert "badge incomplete" not in operator_client.get(PLAY).content.decode()


@override_settings(ROOT_URLCONF=STUB_URLS)
def test_incomplete_badge_links_the_engine_operation_once_registered(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine, incomplete=True)
    transcript = transcript_of(operator_client.get(PLAY).content.decode())
    assert f'>T4</a> <a href="/operations/engine/{OP}/">{BADGE}</a>' in transcript
    assert transcript.count("badge incomplete") == 1


def test_incomplete_badge_links_the_real_engine_operation_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#250 registered ``operations:engine``: the real urlconf links the badge."""
    serve(engine, incomplete=True)
    transcript = transcript_of(operator_client.get(PLAY).content.decode())
    assert f'>T4</a> <a href="/operations/engine/{OP}/">{BADGE}</a>' in transcript


@override_settings(ROOT_URLCONF="tests.urls_without_engine_operation")
def test_incomplete_badge_is_plain_text_before_the_operation_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** under a urlconf lacking ``operations:engine`` (#250) the badge is
    unlinked (D19)."""
    serve(engine, incomplete=True)
    transcript = transcript_of(operator_client.get(PLAY).content.decode())
    assert f">T4</a> {BADGE}" in transcript
    assert "/operations/engine/" not in transcript


# --- the States level in the snapshot -------------------------------------------------------------


def test_the_snapshot_carries_the_states_level() -> None:
    story = Story.from_json(m6_story())
    config = EffectiveConfig.from_json(structured_v3())
    assert story is not None
    assert config is not None
    play = PlayStory.of(story, 45.0, config=config)
    assert play.states == "on"
    restored = PlayStory.unsigned(play.signed(), STORY)
    assert restored is not None
    assert restored.states == "on"
    assert restored == play
    # Unread configuration: None.
    assert PlayStory.of(story, 45.0).states is None


def test_a_snapshot_without_states_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** a snapshot signed without ``states`` fails ``unsigned``, and the turn
    POST re-reads the story."""
    from django.core import signing

    raw = signing.loads(snapshot(), salt=SNAPSHOT_SALT)
    raw.pop("states")
    old = signing.dumps(raw, salt=SNAPSHOT_SALT, compress=True)
    assert PlayStory.unsigned(old, STORY) is None
    engine.respond("POST", RESPOND, 200, json=world_json())
    serve(engine)
    engine.reset()
    response = operator_client.post(TURN_URL, turn_data(snapshot=old, actor=MARA), headers=HTMX)
    assert response.status_code == 200
    assert ("GET", f"/stories/{STORY}") in call_log(engine)

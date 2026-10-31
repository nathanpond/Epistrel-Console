"""Play a multi-character turn from the turn form (#200): the Multi-character section (greyed with
its reason unless the story renders per character), Player view and Whisper to (gated on
perception), the gate-aware body, the Console's own checks, the Engine's refusals placed on the
form, and the kept values.

The play page's reads serve #61's three-character "Harbour" cast (Mara the player, Tobin and
Jenni, both stored reactive) and #188's Structured v2 configuration through the
schema-validating fake (C15)."""

import json
import re
from typing import Any

import httpx
import pytest
from django.core import signing
from django.test import Client
from django.urls import reverse
from django.utils.html import escape

from console.engine.models import EffectiveConfig, Story, StorySettings
from console.inspect import GATE_UNREAD
from console.views.play import SNAPSHOT_SALT, PlayStory
from tests.fakes.engine import FakeEngine
from tests.fakes.story_config import EFFECTIVE
from tests.fixtures import engine_fixture
from tests.inspect.helpers import lite, structured_v2, with_settings
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
    operation_json,
    respond_json,
    turn_data,
)
from tests.play.test_cast_participation import JENNI, omniscient_story, play_story
from tests.play.test_turn_outcomes import oob, poll
from tests.settings.helpers import M5_READ_DEFAULTS

pytestmark = pytest.mark.django_db

STORY_PATH = "/stories/{story_id}"
SETTINGS = "/stories/{story_id}/settings"
RESPOND_PATH = f"/stories/{STORY}/respond"
EFFECTIVE_PATH = f"/stories/{STORY}/effective-config"
PLAY_URL = f"/stories/{STORY}/"
TURN_URL = f"/stories/{STORY}/turn/"
RENDERS_CLAUSE = (
    "Needs <code>multi_character.renders: per_character</code> "
    "(this story runs <code>combined</code>)"
)
PIPELINE_CLAUSE = (
    "needs the structured pipeline (<code>pipeline: structured</code>; "
    "this story runs <code>lite</code>)"
)
SECTION_KEYS = (
    "interaction_mode",
    "sequence_order",
    "simultaneous_tie_break",
    "roles",
    "reactive_interjections",
)
# Extra non-player characters for the ordering and the seven-character tests.
ADA = "01JB2Z8Y5Q3W6K9M1N4P7R0T3D"
BRAM = "01JB2Z8Y5Q3W6K9M1N4P7R0T3E"
CID = "01JB2Z8Y5Q3W6K9M1N4P7R0T3F"
DOV = "01JB2Z8Y5Q3W6K9M1N4P7R0T3G"
EXTRA = [("Ada", ADA), ("Bram", BRAM), ("Cid", CID), ("Dov", DOV)]


# --- helpers --------------------------------------------------------------------------------------


def big_story() -> dict[str, Any]:
    """#61's cast plus Ada, Bram, Cid and Dov (no stored participation): seven in all."""
    story = play_story()
    for name, cid in EXTRA:
        story["characters"].append(
            {"id": cid, "name": name, "is_player": False, "participation": None}
        )
    return story


def settings_body(**settings: Any) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("get_story_settings")
    body["settings"] = {**M5_READ_DEFAULTS, **body["settings"], **settings}
    return body


def config_body(renders: str | None = "per_character", **effective: Any) -> dict[str, Any]:
    """Structured v2 with ``renders``; ``None``: the seeded Lite configuration, which carries
    the M5 keys at their schema defaults (C15)."""
    if renders is None:
        return with_settings(
            lite(),
            {
                "multi_character.renders": "combined",
                "perception.level": "none",
                "character_knowledge.partitions": False,
            },
        )
    return structured_v2(multi_character__renders=renders, **effective)


def serve_page(
    engine: FakeEngine,
    *,
    story: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> None:
    """The play page's reads: the cast story, its settings, history, the omniscient cast read
    (#61's panel) and ``config`` (Structured v2, per-character renders, by default)."""
    found = story or play_story()
    answer_page(engine, story=found)
    engine.respond("GET", SETTINGS, 200, json=settings or settings_body())
    omniscient = omniscient_story()
    omniscient["characters"] = [
        {
            **c,
            "true_age": 30,
            "promoted_to": [],
            "role_id": None,
            "source": None,
            "weight": "major",
            "modeled_at": None,
        }
        for c in found["characters"]
    ]
    engine.respond("GET", STORY_PATH, 200, json=omniscient, query={"viewpoint": "omniscient"})
    engine.respond("GET", EFFECTIVE, 200, json=config or config_body())


def page(client: Client) -> str:
    response = client.get(PLAY_URL)
    assert response.status_code == 200
    return response.content.decode()


def snapshot_for(
    *,
    story: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> str:
    parsed = Story.from_json(story or play_story())
    effective = EffectiveConfig.from_json(config or config_body())
    read = StorySettings.from_json(settings or settings_body())
    assert parsed is not None
    assert effective is not None
    assert read is not None
    return PlayStory.of(parsed, 45.0, settings=read, config=effective).signed()


def post(client: Client, snapshot: str, **fields: Any) -> str:
    data = turn_data(snapshot=snapshot, **fields)
    response = client.post(TURN_URL, data, headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


def sent(engine: FakeEngine) -> dict[str, Any]:
    (request,) = calls(engine, "POST", RESPOND_PATH)
    body: dict[str, Any] = json.loads(request.content)
    return body


def section(html: str) -> str:
    match = re.search(r'<fieldset class="multi-character"(.*?)</fieldset>', html, re.S)
    assert match, "no Multi-character section"
    return match.group(0)


def turn_form(html: str) -> str:
    """The turn form's part of a page or answer (the transcript has its own Player view)."""
    return html.split('<div id="turn-form"', 1)[-1]


def select(html: str, name: str) -> str:
    match = re.search(rf'<select name="{name}"[^>]*>.*?</select>', turn_form(html), re.S)
    assert match, name
    return match.group(0)


def first_option(html: str, name: str) -> str:
    match = re.search(r'<option value=""[^>]*>(.*?)</option>', select(html, name), re.S)
    assert match, name
    return match.group(1)


def field_div(html: str, name: str) -> str:
    match = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert match, name
    return match.group(1)


def answer_respond(engine: FakeEngine) -> None:
    engine.respond("POST", RESPOND, 200, json=respond_json())


# --- the gate -------------------------------------------------------------------------------------


def test_section_enabled_under_per_character(operator_client: Client, engine: FakeEngine) -> None:
    serve_page(engine)
    html = page(operator_client)
    found = section(html)
    assert found.startswith('<fieldset class="multi-character">')
    assert "gate-reason" not in found
    for name in ("interaction_mode", "sequence_order", "simultaneous_tie_break"):
        assert f'name="{name}"' in found
    assert "<th>Character</th><th>Render order</th><th>Role this turn</th>" in found
    assert f'name="order_{TOBIN}"' in found
    assert f'name="role_{JENNI}"' in found
    assert f'name="order_{MARA}"' not in found  # the player has no order or role select
    assert f'name="role_{MARA}"' not in found
    assert "disabled" not in select(html, "player_view")
    assert "disabled" not in select(html, "whisper_to")
    assert "(per-character renders: up to 6, in the Render order below)" in html
    # Player view directly after Output viewpoint, Whisper to directly after Say, the section
    # directly after Render characters.
    order = [
        html.index('class="field field-say"'),
        html.index('class="field field-whisper_to"'),
        html.index('class="field field-do"'),
        html.index('class="field field-output_viewpoint"'),
        html.index('class="field field-player_view"'),
        html.index('class="field field-render_character_ids"'),
        html.index('<fieldset class="multi-character"'),
        html.index('class="field field-length"'),
    ]
    assert order == sorted(order)
    assert "Stored: Reactive" in first_option(html, f"role_{TOBIN}")


def test_section_greyed_with_reason_under_combined(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine, config=config_body("combined"))
    html = page(operator_client)
    found = section(html)
    head = found.split(">", 1)[0]
    assert " disabled" in head
    assert 'title="Needs multi_character.renders: per_character (this story runs combined)"' in head
    reason = re.search(r'<small class="gate-reason" id="gate-multi-character">(.*?)</small>', found)
    assert reason
    assert RENDERS_CLAUSE in reason.group(1)
    config_url = reverse("stories:config", args=[STORY])
    assert f'<a href="{config_url}">Change it on the story\'s Config page</a>' in reason.group(1)
    assert "Config page" not in head  # the link is in the visible line only
    assert "(per-character renders" not in html
    # Perception is full on v2: Player view and Whisper to stay enabled.
    assert "disabled" not in select(html, "player_view")
    assert "disabled" not in select(html, "whisper_to")


def test_section_greyed_with_pipeline_clause_on_lite(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine, config=config_body(None))
    html = page(operator_client)
    found = section(html)
    assert " disabled" in found.split(">", 1)[0]
    assert "Needs the <code>multi_character</code> module, which " + PIPELINE_CLAUSE in found
    assert "Config page" not in found
    for name, gate_id in (("player_view", "gate-player-view"), ("whisper_to", "gate-whisper-to")):
        control = select(html, name)
        assert " disabled" in control
        assert f'aria-describedby="{gate_id}"' in control
        assert "this story runs lite)" in control  # the title
        div = field_div(html, name)
        assert f'<small class="gate-reason" id="{gate_id}">' in div
        assert "Needs the <code>perception</code> module, which " + PIPELINE_CLAUSE in div


def test_section_greyed_when_effective_config_fails(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    engine.fail("GET", EFFECTIVE, httpx.ConnectError("down"))
    html = page(operator_client)
    found = section(html)
    assert " disabled" in found.split(">", 1)[0]
    assert escape(GATE_UNREAD) in found
    assert escape(GATE_UNREAD) in field_div(html, "player_view")
    assert escape(GATE_UNREAD) in field_div(html, "whisper_to")


def test_page_makes_one_effective_config_read(operator_client: Client, engine: FakeEngine) -> None:
    serve_page(engine)
    page(operator_client)
    assert len(calls(engine, "GET", EFFECTIVE_PATH)) == 1
    assert len(calls(engine, "GET", f"/stories/{STORY}/settings")) == 1


def test_first_choices_when_settings_read_fails(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    engine.respond("GET", SETTINGS, 503, json=error_json("unavailable", "down"))
    html = page(operator_client)
    for name in (
        "interaction_mode",
        "sequence_order",
        "simultaneous_tie_break",
        "reactive_interjections",
        "player_view",
    ):
        assert first_option(html, name) == "Story setting (couldn&#x27;t read)", name


def test_first_choices_name_the_story_settings(operator_client: Client, engine: FakeEngine) -> None:
    serve_page(engine)
    html = page(operator_client)
    assert first_option(html, "interaction_mode") == "Story setting (sequential)"
    assert first_option(html, "sequence_order") == "Story setting (request order)"
    assert first_option(html, "simultaneous_tie_break") == "Story setting (request order)"
    assert first_option(html, "reactive_interjections") == "Story setting (Off)"
    assert first_option(html, "player_view") == "Story setting (character)"
    settings = settings_body(
        interaction_mode=None,
        sequence_order="director",
        simultaneous_tie_break="state:initiative",
        reactive_interjections=True,
        player_view="story",
        inactive={"interaction_mode": {"value": "simultaneous", "capability": "multi_character"}},
    )
    serve_page(engine, settings=settings)
    html = page(operator_client)
    assert first_option(html, "interaction_mode") == "Story setting (scenario: simultaneous)"
    assert first_option(html, "sequence_order") == "Story setting (director)"
    assert (
        first_option(html, "simultaneous_tie_break")
        == "Story setting (state:initiative (M6: request order applies))"
    )
    assert "state:" not in select(html, "simultaneous_tie_break").split("</option>", 1)[1]
    assert first_option(html, "reactive_interjections") == "Story setting (On)"
    assert first_option(html, "player_view") == "Story setting (story)"


# --- the body -------------------------------------------------------------------------------------


def test_body_carries_only_chosen_overrides(operator_client: Client, engine: FakeEngine) -> None:
    answer_respond(engine)
    answer_page(engine, story=play_story())
    post(
        operator_client,
        snapshot_for(),
        render_character_ids=[TOBIN, JENNI],
        interaction_mode="sequential",
        sequence_order="",
        simultaneous_tie_break="",
        reactive_interjections="off",
        player_view="story",
        **{f"role_{TOBIN}": "reactive", f"role_{JENNI}": "", f"order_{TOBIN}": ""},
    )
    body = sent(engine)
    assert body["interaction_mode"] == "sequential"
    assert "sequence_order" not in body
    assert "simultaneous_tie_break" not in body
    assert body["reactive_interjections"] is False
    assert body["roles"] == {TOBIN: "reactive"}  # equal to the stored role, and still sent
    assert body["player_view"] == "story"
    assert body["render_character_ids"] == [TOBIN, JENNI]


def test_untouched_section_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    answer_respond(engine)
    answer_page(engine, story=play_story())
    post(operator_client, snapshot_for())
    body = sent(engine)
    for key in (*SECTION_KEYS, "player_view"):
        assert key not in body
    assert "whisper_to" not in body["player_input"]


def test_render_order_sorts_render_character_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_respond(engine)
    story = big_story()
    answer_page(engine, story=story)
    post(
        operator_client,
        snapshot_for(story=story),
        render_character_ids=[BRAM, MARA, JENNI, TOBIN, ADA],
        **{
            f"order_{JENNI}": "2",
            f"order_{BRAM}": "2",
            f"order_{ADA}": "1",
            f"order_{TOBIN}": "",
            f"order_{CID}": "1",  # not picked: ignored
        },
    )
    assert sent(engine)["render_character_ids"] == [ADA, JENNI, BRAM, MARA, TOBIN]


def test_gate_off_never_sends_multi_fields(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** under ``combined`` a crafted POST carrying every section field sends none
    of them, and the render characters keep their posted order."""
    answer_respond(engine)
    answer_page(engine, story=play_story())
    post(
        operator_client,
        snapshot_for(config=config_body("combined")),
        render_character_ids=[JENNI, TOBIN],
        interaction_mode="simultaneous",
        sequence_order="director",
        simultaneous_tie_break="director",
        reactive_interjections="on",
        **{f"role_{TOBIN}": "primary", f"order_{TOBIN}": "1", f"order_{JENNI}": "2"},
    )
    body = sent(engine)
    for key in SECTION_KEYS:
        assert key not in body, key
    assert body["render_character_ids"] == [JENNI, TOBIN]


def test_whisper_disabled_below_full_perception(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** at ``co_location`` Whisper to is greyed and a crafted ``whisper_to`` is
    dropped; Player view is still offered and sent."""
    config = config_body(perception__level="co_location")
    serve_page(engine, config=config)
    html = page(operator_client)
    whisper = select(html, "whisper_to")
    assert " disabled" in whisper
    assert (
        "Needs <code>perception.level: full</code> (this story runs <code>co_location</code>)"
        in (field_div(html, "whisper_to"))
    )
    assert "disabled" not in select(html, "player_view")
    answer_respond(engine)
    post(operator_client, snapshot_for(config=config), whisper_to=[TOBIN], player_view="character")
    body = sent(engine)
    assert "whisper_to" not in body["player_input"]
    assert body["player_view"] == "character"


def test_whisper_sent_under_full_perception(operator_client: Client, engine: FakeEngine) -> None:
    answer_respond(engine)
    answer_page(engine, story=play_story())
    post(operator_client, snapshot_for(), whisper_to=[TOBIN])
    assert sent(engine)["player_input"] == {
        "say": "Is the ferry late again?",
        "whisper_to": [TOBIN],
    }


def test_player_view_disabled_when_perception_none(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** perception ``none`` greys Player view (and Whisper to); neither is sent."""
    config = config_body(perception__level="none")
    serve_page(engine, config=config)
    html = page(operator_client)
    assert " disabled" in select(html, "player_view")
    assert (
        "Needs <code>perception.level</code> at <code>co_location</code> or above "
        "(this story runs <code>none</code>)"
    ) in field_div(html, "player_view")
    assert " disabled" in select(html, "whisper_to")
    answer_respond(engine)
    post(operator_client, snapshot_for(config=config), player_view="story", whisper_to=[TOBIN])
    body = sent(engine)
    assert "player_view" not in body
    assert "whisper_to" not in body["player_input"]


# --- Console checks -------------------------------------------------------------------------------


@pytest.mark.parametrize("say", ["", "   "], ids=["blank", "whitespace"])
def test_whisper_needs_say_makes_no_engine_call(
    operator_client: Client, engine: FakeEngine, say: str
) -> None:
    """**Complement:** a whisper without something to say makes no respond call."""
    answer_page(engine, story=play_story())
    html = post(operator_client, snapshot_for(), say=say, do="waves", whisper_to=[TOBIN])
    assert calls(engine, "POST", RESPOND_PATH) == []
    assert "Whispering needs something to say" in field_div(oob(html, "turn-form"), "whisper_to")


def test_seven_render_characters_refused_without_engine_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** seven render characters (the player included) never reach the Engine."""
    story = big_story()
    answer_page(engine, story=story)
    picked = [c["id"] for c in story["characters"]]
    assert len(picked) == 7
    html = post(operator_client, snapshot_for(story=story), render_character_ids=picked)
    assert calls(engine, "POST", RESPOND_PATH) == []
    assert "At most 6 characters render in one turn (you picked 7)" in field_div(
        oob(html, "turn-form"), "render_character_ids"
    )


def test_seven_render_characters_left_to_the_engine_when_greyed(
    operator_client: Client, engine: FakeEngine
) -> None:
    story = big_story()
    answer_respond(engine)
    answer_page(engine, story=story)
    picked = [c["id"] for c in story["characters"]]
    post(
        operator_client,
        snapshot_for(story=story, config=config_body("combined")),
        render_character_ids=picked,
    )
    assert sent(engine)["render_character_ids"] == picked


# --- Engine refusals ------------------------------------------------------------------------------


def _reason(reason: str, field: str, message: str) -> dict[str, Any]:
    return error_json(
        "validation_error",
        message,
        field=field,
        reason=reason,
        errors=[{"field": field, "message": message, "type": "value_error"}],
    )


RENDER = "body.render_character_ids"
RELOAD = "The story&#x27;s profile no longer renders per character — reload the page"
REASONS = [
    pytest.param(
        _reason("one_render_character", RENDER, "a structured turn renders one character"),
        "render_character_ids",
        "This story&#x27;s profile renders one character per turn",
        "a structured turn renders one character",
        id="one_render_character",
    ),
    pytest.param(
        _reason("player_cannot_render", RENDER, "the player's character is never rendered"),
        "render_character_ids",
        "The player&#x27;s character (Mara) can&#x27;t render in a multi-character turn",
        "is never rendered",
        id="player_cannot_render",
    ),
    pytest.param(
        _reason("too_many_primaries", RENDER, "a structured turn renders at most 6 characters"),
        "render_character_ids",
        "At most 6 characters render in one turn",
        "renders at most 6",
        id="too_many_primaries",
    ),
    pytest.param(
        _reason("per_character_only", "body.roles", "roles needs per-character renders"),
        "multi",
        RELOAD,
        "roles needs per-character renders",
        id="per_character_only",
    ),
    pytest.param(
        _reason("not_yet", "body.simultaneous_tie_break", "not delivered yet"),
        "simultaneous_tie_break",
        "State tie-breaks arrive in Engine M6",
        "not delivered yet",
        id="not_yet",
    ),
    pytest.param(
        error_json(
            "validation_error",
            "invalid",
            errors=[{"field": f"body.roles.{TOBIN}", "message": "not a present character"}],
        ),
        f"role_{TOBIN}",
        "not a present character",
        None,
        id="roles_cid",
    ),
    pytest.param(
        error_json(
            "validation_error",
            "invalid",
            errors=[{"field": "body.player_input.whisper_to", "message": "bad whisper"}],
        ),
        "whisper_to",
        "bad whisper",
        None,
        id="whisper_to",
    ),
]


@pytest.mark.parametrize(("answer", "where", "sentence", "engine_message"), REASONS)
def test_engine_reasons_are_placed(
    operator_client: Client,
    engine: FakeEngine,
    answer: dict[str, Any],
    where: str,
    sentence: str,
    engine_message: str | None,
) -> None:
    engine.respond("POST", RESPOND, 422, json=answer)
    answer_page(engine, story=play_story())
    html = post(
        operator_client,
        snapshot_for(),
        render_character_ids=[MARA, TOBIN],
        whisper_to=[TOBIN],
        **{f"role_{TOBIN}": "primary"},
    )
    assert "<h3>Turn refused</h3>" in html.split('<div id="turn-status"')[0]
    form = oob(html, "turn-form")
    if where == "multi":
        assert f'<ul class="errorlist section-error"><li>{sentence}</li></ul>' in section(form)
        assert 'class="errorlist"' not in form.replace("errorlist section-error", "")
    elif where.startswith("role_"):
        row = section(form).split(f'name="{where}"', 1)[1].split("</tr>", 1)[0]
        assert sentence in row
    else:
        assert sentence in field_div(form, where)
    if engine_message is not None:
        assert engine_message not in form  # a worded reason is not placed twice
    assert 'value="Is the ferry late again?"' in form or "Is the ferry late again?" in form


def test_player_cannot_render_without_a_picked_player(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer = _reason("player_cannot_render", RENDER, "the player's character is never rendered")
    engine.respond("POST", RESPOND, 422, json=answer)
    answer_page(engine, story=play_story())
    html = post(operator_client, snapshot_for(), render_character_ids=[TOBIN, JENNI])
    assert "The player&#x27;s character can&#x27;t render in a multi-character turn" in (
        field_div(oob(html, "turn-form"), "render_character_ids")
    )


def test_module_off_409_reads_reload(operator_client: Client, engine: FakeEngine) -> None:
    message = (
        "interaction_mode, sequence_order and simultaneous_tie_break need per-character renders"
    )
    answer = error_json("module_off", message, story_id=STORY, module="multi_character")
    engine.respond("POST", RESPOND, 409, json=answer)
    answer_page(engine, story=play_story())
    html = post(operator_client, snapshot_for(), interaction_mode="simultaneous")
    inspector = html.split('<div id="turn-status"')[0]
    assert "<h3>Turn refused</h3>" in inspector
    assert RELOAD in inspector
    assert "<dt>Module</dt><dd>multi_character</dd>" in inspector
    assert "HTTP 409 module_off" in inspector
    form = oob(html, "turn-form")
    assert f'<ul class="errorlist section-error"><li>{RELOAD}</li></ul>' in section(form)
    assert '<option value="simultaneous" selected>' in form  # the input is kept


# --- snapshots ------------------------------------------------------------------------------------


def _signed(**changes: Any) -> str:
    raw: dict[str, Any] = signing.loads(snapshot_for(), salt=SNAPSHOT_SALT)
    for key, value in changes.items():
        if value is KeyError:
            raw.pop(key)
        else:
            raw[key] = value
    return signing.dumps(raw, salt=SNAPSHOT_SALT, compress=True)


def _assert_reread(client: Client, engine: FakeEngine, value: str) -> None:
    answer_respond(engine)
    answer_page(engine, story=play_story())
    engine.respond("GET", EFFECTIVE, 200, json=config_body())
    post(client, value, interaction_mode="simultaneous")
    assert len(calls(engine, "GET", f"/stories/{STORY}")) == 1
    assert len(calls(engine, "GET", EFFECTIVE_PATH)) == 1
    assert sent(engine)["interaction_mode"] == "simultaneous"  # the re-read gate is open


@pytest.mark.parametrize(
    "missing", ["renders", "pipeline", "perception", "partitions", "roles", "story_multi"]
)
def test_old_snapshot_without_gate_keys_is_reread(
    operator_client: Client, engine: FakeEngine, missing: str
) -> None:
    _assert_reread(operator_client, engine, _signed(**{missing: KeyError}))


@pytest.mark.parametrize(
    "changes",
    [
        {"story_multi": "sequential"},
        {"story_multi": ["sequential"]},
        {"roles": {TOBIN: ["reactive"]}},
        {"roles": {TOBIN: "reactive"}},
        {"roles": [TOBIN]},
        {"renders": 3},
        {"partitions": "yes"},
    ],
    ids=["multi-str", "multi-list", "role-one", "role-str", "roles-list", "renders-int", "parts"],
)
def test_malformed_snapshot_gate_keys_are_reread(
    operator_client: Client, engine: FakeEngine, changes: dict[str, Any]
) -> None:
    _assert_reread(operator_client, engine, _signed(**changes))


def test_snapshot_round_trips_the_gate_keys() -> None:
    value = snapshot_for()
    found = PlayStory.unsigned(value, STORY)
    assert found is not None
    assert found.renders == "per_character"
    assert found.pipeline == "structured"
    assert found.perception == "full"
    assert found.partitions is True
    assert found.roles == {TOBIN: ("reactive", False), JENNI: ("reactive", False)}
    assert found.story_multi is not None
    assert found.story_multi["interaction_mode"] is None
    assert found.gate.enabled


def test_stored_role_without_fixed_reads_not_fixed() -> None:
    story = play_story()
    story["characters"][1]["participation"] = {"role": "primary", "fixed": True}
    story["characters"][2]["participation"] = None
    parsed = Story.from_json(story)
    assert parsed is not None
    parsed.characters[2]["participation"] = {"role": "silent"}  # raw: no ``fixed``
    play = PlayStory.of(parsed, 45.0)
    assert play.roles == {TOBIN: ("primary", True), JENNI: ("silent", False)}


# --- kept values ----------------------------------------------------------------------------------


MULTI_FIELDS: dict[str, Any] = {
    "render_character_ids": [TOBIN, JENNI],
    "interaction_mode": "simultaneous",
    "simultaneous_tie_break": "director",
    "reactive_interjections": "on",
    "player_view": "story",
    "whisper_to": [TOBIN],
    f"order_{JENNI}": "1",
    f"role_{TOBIN}": "reactive",
}


def _assert_kept(form: str) -> None:
    assert '<option value="simultaneous" selected>' in select(form, "interaction_mode")
    assert '<option value="director" selected>' in select(form, "simultaneous_tie_break")
    assert '<option value="on" selected>' in select(form, "reactive_interjections")
    assert '<option value="story" selected>' in select(form, "player_view")
    assert '<option value="1" selected>' in select(form, f"order_{JENNI}")
    assert '<option value="reactive" selected>' in select(form, f"role_{TOBIN}")
    assert "selected" not in select(form, "whisper_to")  # cleared with say and do


def test_finished_turn_keeps_multi_fields(operator_client: Client, engine: FakeEngine) -> None:
    answer_respond(engine)
    answer_page(engine, story=play_story())
    html = post(operator_client, snapshot_for(), **MULTI_FIELDS)
    _assert_kept(oob(html, "turn-form"))


def test_queued_turn_restores_multi_fields(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        RESPOND,
        202,
        json={"operation_id": QUEUED_OP, "position": 1},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    answer_page(engine, story=play_story())
    post(operator_client, snapshot_for(), **MULTI_FIELDS)
    committed = operation_json(
        "committed",
        result={"status": 200, "body": respond_json()},
        viewpoint=f"character:{TOBIN}",
        timeline_revision=2,
    )
    engine.respond("GET", OPERATION, 200, json=committed)
    _assert_kept(oob(poll(operator_client), "turn-form"))

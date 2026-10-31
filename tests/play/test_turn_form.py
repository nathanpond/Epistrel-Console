"""The turn form (#48): what it sends, what it refuses, and the idempotency key it carries."""

import json
import re
from typing import Any

import pytest
from django.test import Client

from console.cast import CastMember
from console.forms.play import TurnForm, parse_recent_messages
from tests.fakes.engine import FakeEngine
from tests.play.helpers import (
    KEY,
    MARA,
    RESPOND,
    STORY,
    TOBIN,
    answer_page,
    calls,
    post_turn,
    respond_json,
    snapshot,
    story_json,
)

pytestmark = pytest.mark.django_db
CAST = [CastMember(MARA, "Mara", True), CastMember(TOBIN, "Tobin", False)]


def sent(engine: FakeEngine) -> Any:
    (request,) = calls(engine, "POST", f"/stories/{STORY}/respond")
    return request


def test_minimal_turn_sends_only_what_was_given(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answer_page(engine)
    post_turn(operator_client)
    body = json.loads(sent(engine).content)
    # "Story default" is sent explicitly as the story's default viewpoint (Mara's).
    assert body == {
        "actor_character_id": MARA,
        "player_input": {"say": "Is the ferry late again?"},
        "output_viewpoint": f"character:{MARA}",
    }


def test_every_field_maps_to_the_request_body(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answer_page(engine)
    post_turn(
        operator_client,
        say="Hello",
        do="waves",
        output_viewpoint=f"character:{TOBIN}",
        render_character_ids=[TOBIN],
        length="custom",
        length_custom="120",
        temperature="0.7",
        top_p="0.9",
        characters_present=[MARA, TOBIN],
        recent_messages="Tobin: The ferry is late.\n\nA gull cries.\nnobody: hi",
        max_calls="5",
        deadline_s="20",
    )
    body = json.loads(sent(engine).content)
    assert body == {
        "actor_character_id": MARA,
        "player_input": {"say": "Hello", "do": "waves"},
        "output_viewpoint": f"character:{TOBIN}",
        "render_character_ids": [TOBIN],
        "prompt_params": {"length": 120, "temperature": 0.7, "top_p": 0.9},
        "scene_context": {
            "characters_present": [MARA, TOBIN],
            "recent_messages": [
                {"character_id": TOBIN, "text": "The ferry is late."},
                {"text": "A gull cries."},
                {"text": "nobody: hi"},
            ],
        },
        "budget": {"max_calls": 5, "deadline_s": 20.0},
    }


def test_a_named_length_is_sent_as_its_name(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answer_page(engine)
    post_turn(operator_client, length="short", do="", say="Hi")
    assert json.loads(sent(engine).content)["prompt_params"] == {"length": "short"}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"say": "", "do": ""}, "at least one is required"),
        ({"say": "   ", "do": ""}, "at least one is required"),
        ({"length": "custom"}, "Give a number when the length is custom."),
        ({"length": "custom", "length_custom": "4001"}, "less than or equal to 4000"),
        ({"temperature": "2.5"}, "less than or equal to 2.0"),
        ({"top_p": "-0.1"}, "greater than or equal to 0.0"),
        ({"max_calls": "0"}, "greater than or equal to 1"),
        ({"max_calls": "51"}, "less than or equal to 50"),
        ({"deadline_s": "0.1"}, "greater than or equal to 0.5"),
        ({"recent_messages": "\n".join(f"line {i}" for i in range(21))}, "At most 20"),
        ({"recent_messages": "x" * 2001}, "longer than 2000"),
        ({"say": "x" * 8001}, "at most 8000"),
        ({"actor": "01JB2Z8Y5Q3W6K9M1N4P7R0XXX"}, "Select a valid choice"),
    ],
)
def test_refused_input_makes_no_engine_call(
    operator_client: Client, engine: FakeEngine, fields: dict[str, Any], message: str
) -> None:
    html = post_turn(operator_client, **fields)
    assert message in html
    assert "The turn was not sent" in html
    assert engine.requests == []


def test_prefer_and_idempotency_key_are_sent(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answer_page(engine)
    post_turn(operator_client)
    request = sent(engine)
    assert request.headers["Prefer"] == "respond-async"
    assert request.headers["Idempotency-Key"] == KEY


def test_one_render_posted_twice_sends_the_same_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    """A double-click: both posts carry the render's key, so the Engine replays the second."""
    answer_page(engine)
    page = operator_client.get(f"/stories/{STORY}/").content.decode()
    # The owner and relations panel's add form (#79) carries a key of its own.
    page = re.sub(r'<details id="ownership".*?</details>', "", page, flags=re.S)
    # So do the cast panel's role and weight forms (#61).
    page = re.sub(r'<ul class="cast">.*?</ul>', "", page, flags=re.S)
    (key,) = re.findall(r'name="idempotency_key" value="([^"]+)"', page)
    engine.respond("POST", RESPOND, 200, json=respond_json())
    post_turn(operator_client, idempotency_key=key)
    post_turn(operator_client, idempotency_key=key)
    keys = [r.headers["Idempotency-Key"] for r in calls(engine, "POST")]
    assert keys == [key, key]


def test_each_render_mints_a_new_key(operator_client: Client, engine: FakeEngine) -> None:
    """The complement: a new render (and each answer's re-rendered form) has a fresh key."""
    answer_page(engine)
    first = operator_client.get(f"/stories/{STORY}/").content.decode()
    second = operator_client.get(f"/stories/{STORY}/").content.decode()
    pattern = r'name="idempotency_key" value="([^"]+)"'
    assert re.findall(pattern, first) != re.findall(pattern, second)
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answered = post_turn(operator_client)
    (fresh,) = re.findall(pattern, answered)
    assert fresh != KEY


def test_the_page_offers_the_cast_and_story_default(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert f'<option value="{MARA}" selected>Mara</option>' in html
    assert '<option value="default" selected>Story default (Mara)</option>' in html
    assert f'<option value="character:{TOBIN}">Tobin</option>' in html
    assert "omniscient" not in html.split('name="output_viewpoint"')[1].split("</select>")[0]
    assert "blank = everyone except the actor" in html


def test_no_story_default_selects_the_actor(operator_client: Client, engine: FakeEngine) -> None:
    story = story_json(settings={"default_output_viewpoint": None})
    answer_page(engine, story=story)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "Story default" not in html
    assert f'<option value="character:{MARA}" selected>Mara</option>' in html
    (history,) = calls(engine, "GET", f"/stories/{STORY}/history")
    assert history.url.params["viewpoint"] == f"character:{MARA}"
    assert history.url.params["limit"] == "50"


def test_a_tampered_snapshot_is_read_again(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answer_page(engine)
    post_turn(operator_client, snapshot=snapshot() + "x")
    assert len(calls(engine, "GET", f"/stories/{STORY}")) >= 1
    assert len(calls(engine, "POST")) == 1


def test_recent_messages_match_names_case_insensitively() -> None:
    parsed = parse_recent_messages("  tobin : late again\nMara:\nPlain line", CAST)
    assert [(m.character_id, m.text) for m in parsed] == [
        (TOBIN, "late again"),
        (MARA, ""),
        (None, "Plain line"),
    ]


def test_duplicate_names_get_their_id_suffix() -> None:
    twins = [CastMember(MARA, "Mara", True), CastMember(TOBIN, "Mara", False)]
    form = TurnForm(cast=twins, default_viewpoint=None)
    labels = [str(option.choice_label) for option in form["actor"]]
    assert labels == [f"Mara (…{MARA[-6:]})", f"Mara (…{TOBIN[-6:]})"]

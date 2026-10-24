"""The client methods #48 adds (respond, history, story settings), the ``Retry-After`` an
error carries, the fake Engine's scripted sequences and the shared cast helpers."""

import json

import pytest

from console.cast import (
    CastMember,
    character_of,
    default_actor,
    viewpoint_for,
    viewpoint_label,
)
from console.engine.client import EngineClient, EngineError, turn_timeout
from console.engine.models import QueuedTurn, RespondResponse
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import MARA, QUEUED_OP, RESPOND, STORY, TOBIN, error_json

BODY = {"actor_character_id": MARA, "player_input": {"say": "hi"}}


@pytest.fixture
def client(fake_engine: FakeEngine) -> EngineClient:
    return EngineClient(
        "http://engine.test", "k", transport=fake_engine.transport, principal="console:nate"
    )


def test_respond_sends_prefer_key_and_turn_timeout(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("POST", RESPOND, 200, json=engine_fixture("respond_play"))
    result = client.respond(STORY, BODY, idempotency_key="K1", timeout=turn_timeout(20.0))
    assert isinstance(result, RespondResponse)
    assert result.message.text.startswith("[stub generation")
    assert result.report.model_calls[0].purpose == "generation"
    assert result.report.turn == 1
    assert result.report.timeline_revision == 2
    assert [e.channel for e in result.entries] == ["say", "message"]
    (request,) = fake_engine.requests
    assert request.url.path == f"/stories/{STORY}/respond"
    assert json.loads(request.content) == BODY
    assert request.headers["Prefer"] == "respond-async"
    assert request.headers["Idempotency-Key"] == "K1"
    assert request.extensions["timeout"]["read"] == 35.0


def test_respond_without_async_sends_no_prefer(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("POST", RESPOND, 200, json=engine_fixture("respond"))
    client.respond(STORY, BODY, idempotency_key="K1", prefer_async=False)
    (request,) = fake_engine.requests
    assert "Prefer" not in request.headers
    assert request.extensions["timeout"]["read"] == 60.0  # 45 s default + 15 s


def test_respond_202_is_a_queued_turn(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", RESPOND, 202, json={"operation_id": QUEUED_OP, "position": 2})
    assert client.respond(STORY, BODY, idempotency_key="K1") == QueuedTurn(QUEUED_OP, 2)


def test_errors_carry_retry_after(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond(
        "POST",
        RESPOND,
        423,
        json=error_json("story_busy", "busy", reason="in_flight"),
        headers={"Retry-After": "7"},
    )
    with pytest.raises(EngineError) as raised:
        client.respond(STORY, BODY, idempotency_key="K1")
    assert raised.value.retry_after == "7"
    assert raised.value.details == {"reason": "in_flight"}


def test_errors_without_retry_after_have_none(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("POST", RESPOND, 409, json=error_json("story_archived", "archived"))
    with pytest.raises(EngineError) as raised:
        client.respond(STORY, BODY, idempotency_key="K1")
    assert raised.value.retry_after is None


def test_get_history_sends_viewpoint_and_limit(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond(
        "GET", "/stories/{story_id}/history", 200, json=engine_fixture("get_history")
    )
    history = client.get_history(STORY, viewpoint=f"character:{TOBIN}", limit=50, before_turn=4)
    assert next(e.text for e in history.entries) == "Is the ferry late again?"
    assert history.turns[0].message is not None
    (request,) = fake_engine.requests
    assert dict(request.url.params) == {
        "viewpoint": f"character:{TOBIN}",
        "limit": "50",
        "before_turn": "4",
    }
    assert request.method == "GET"


def test_get_history_without_options_sends_no_query(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond(
        "GET", "/stories/{story_id}/history", 200, json=engine_fixture("get_history")
    )
    client.get_history(STORY)
    (request,) = fake_engine.requests
    assert request.url.query == b""


def test_get_story_settings_reads_the_turn_deadline(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond(
        "GET", "/stories/{story_id}/settings", 200, json=engine_fixture("get_story_settings")
    )
    settings = client.get_story_settings(STORY)
    assert settings.effective_budget.turn_deadline == 45.0
    assert settings.settings["turn_concurrency"] == "reject"


def test_turn_timeout_defaults_to_45_plus_15() -> None:
    assert turn_timeout(None) == 60.0
    assert turn_timeout(0.5) == 15.5


def test_a_scripted_sequence_answers_in_order_then_repeats(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond_sequence(
        "POST",
        RESPOND,
        [
            {"status": 423, "json": error_json("story_busy", "busy")},
            {"status": 202, "json": {"operation_id": QUEUED_OP, "position": 1}},
        ],
    )
    with pytest.raises(EngineError):
        client.respond(STORY, BODY, idempotency_key="K1")
    assert isinstance(client.respond(STORY, BODY, idempotency_key="K1"), QueuedTurn)
    assert isinstance(client.respond(STORY, BODY, idempotency_key="K1"), QueuedTurn)


def test_a_scripted_sequence_is_validated(fake_engine: FakeEngine) -> None:
    with pytest.raises(Exception, match="position"):
        fake_engine.respond_sequence(
            "POST", RESPOND, [{"status": 202, "json": {"operation_id": QUEUED_OP}}]
        )
    with pytest.raises(ValueError, match="at least one"):
        fake_engine.respond_sequence("POST", RESPOND, [])


def test_cast_helpers() -> None:
    cast = [CastMember(TOBIN, "Tobin", False), CastMember(MARA, "Mara", True)]
    assert default_actor(cast) == MARA
    assert default_actor([CastMember(TOBIN, "Tobin", False)]) == TOBIN
    assert default_actor([]) is None
    assert character_of(viewpoint_for(MARA)) == MARA
    assert character_of("omniscient") is None
    assert viewpoint_label(viewpoint_for(TOBIN), cast) == "Tobin"
    assert viewpoint_label("omniscient", cast) == "omniscient"
    assert viewpoint_label(None, cast) == "—"

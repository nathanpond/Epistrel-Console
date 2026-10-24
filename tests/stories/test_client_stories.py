"""The client methods #45 adds: requests, timeouts and typed answers."""

import json

import pytest

from console.engine.client import MUTATION_TIMEOUT_S, EngineClient, EngineError
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
SPEC = {"characters": [{"name": "Mara", "definition": "d", "true_age": 34, "is_player": True}]}


@pytest.fixture
def client(fake_engine: FakeEngine) -> EngineClient:
    return EngineClient(
        "http://engine.test", "k", transport=fake_engine.transport, principal="console:nate"
    )


def test_create_story_sends_body_key_and_returns_the_id(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    created = client.create_story(SPEC, idempotency_key="K1")
    assert created.story_id == STORY
    assert [c.name for c in created.characters] == ["Mara", "Tobin"]
    (request,) = fake_engine.requests
    assert json.loads(request.content) == SPEC
    assert request.headers["Idempotency-Key"] == "K1"
    assert request.extensions["timeout"]["read"] == MUTATION_TIMEOUT_S


def test_create_story_refuses_a_200_without_a_story(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    body = {**engine_fixture("create_story"), "applied": False, "story_id": None}
    fake_engine.respond("POST", "/stories", 200, json=body)
    with pytest.raises(EngineError) as raised:
        client.create_story(SPEC, idempotency_key="K1")
    assert raised.value.code == "bad_response"


def test_create_story_refuses_a_null_id(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond(
        "POST", "/stories", 201, json={**engine_fixture("create_story"), "story_id": None}
    )
    with pytest.raises(EngineError, match="bad_response"):
        client.create_story(SPEC, idempotency_key="K1")


def test_get_story_parses_the_cast(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories/{story_id}", 200, json=engine_fixture("get_story"))
    story = client.get_story(STORY)
    assert [(c.name, c.is_player) for c in story.cast] == [("Mara", True), ("Tobin", False)]
    assert story.content_rating.name == "Teen"
    assert fake_engine.requests[0].url.path == f"/stories/{STORY}"


@pytest.mark.parametrize("action", ["archive", "unarchive"])
def test_status_changes(client: EngineClient, fake_engine: FakeEngine, action: str) -> None:
    fake_engine.respond(
        "POST", f"/stories/{{story_id}}/{action}", 200, json=engine_fixture(f"{action}_story")
    )
    result = getattr(client, f"{action}_story")(STORY, idempotency_key="K2")
    assert result.status == ("archived" if action == "archive" else "active")
    (request,) = fake_engine.requests
    assert request.url.path == f"/stories/{STORY}/{action}"
    assert request.headers["Idempotency-Key"] == "K2"


def test_delete_story(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("DELETE", "/stories/{story_id}", 204)
    assert client.delete_story(STORY, idempotency_key="K3") is None
    (request,) = fake_engine.requests
    assert request.method == "DELETE"
    assert request.headers["Idempotency-Key"] == "K3"


def test_delete_story_423(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond(
        "DELETE",
        "/stories/{story_id}",
        423,
        json={"error": {"code": "story_busy", "message": "busy", "details": {}}},
    )
    with pytest.raises(EngineError) as raised:
        client.delete_story(STORY, idempotency_key="K3")
    assert (raised.value.http_status, raised.value.code) == (423, "story_busy")


@pytest.mark.parametrize(("include", "params"), [(False, {}), (True, {"include_retired": "1"})])
def test_list_content_ratings(
    client: EngineClient, fake_engine: FakeEngine, include: bool, params: dict[str, str]
) -> None:
    fake_engine.respond(
        "GET", "/admin/content-ratings", 200, json=engine_fixture("list_content_ratings")
    )
    ratings = client.list_content_ratings(include_retired=include)
    assert [r.name for r in ratings] == ["Everyone", "Teen", "Mature"]
    assert dict(fake_engine.requests[0].url.params) == params


def test_get_deployment_settings(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond(
        "GET", "/admin/settings", 200, json=engine_fixture("get_deployment_settings")
    )
    settings = client.get_deployment_settings()
    assert settings.default_content_rating_id == "00000000000000000000RAT001"
    assert isinstance(settings.profile_defaults, dict)

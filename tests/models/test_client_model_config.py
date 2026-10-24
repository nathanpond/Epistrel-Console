"""The client methods #46 adds: requests, timeouts and typed answers."""

import json

import pytest

from console.engine.client import MUTATION_TIMEOUT_S, EngineClient
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.models.helpers import ITEM, KEY, LIST, item


@pytest.fixture
def client(fake_engine: FakeEngine) -> EngineClient:
    return EngineClient(
        "http://engine.test", "k", transport=fake_engine.transport, principal="console:nate"
    )


def test_list_model_config_keeps_the_engines_order(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fixture = engine_fixture("list_model_config")
    fake_engine.respond("GET", LIST, 200, json=fixture)
    config = client.list_model_config()
    assert list(config.purposes) == list(fixture["purposes"])
    assert config.config_revision == 5
    generation = config.purposes["generation"]
    assert generation.api_key_set
    assert generation.api_key_fingerprint == "082f20f3"
    assert generation.sources.model == "default"
    assert not config.purposes["quality_judge"].api_key_set
    assert config.purposes["lite_summary"].sources.params["seed"] == "inherited"


def test_get_model_config_encodes_the_purpose(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("GET", ITEM, 200, json=item("lite_summary"))
    got = client.get_model_config("lite_summary")
    assert got.purpose == "lite_summary"
    assert got.config_revision == 5
    client.get_model_config("a b")
    assert fake_engine.requests[-1].url.raw_path == b"/admin/model-config/a%20b"


def test_put_model_config_sends_body_and_key(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("PUT", ITEM, 200, json=engine_fixture("put_model_config"))
    saved = client.put_model_config("guardrail_check", {"model": "guard-1"}, idempotency_key="K1")
    assert saved.config_revision == 6
    (request,) = fake_engine.requests
    assert json.loads(request.content) == {"model": "guard-1"}
    assert request.headers["Idempotency-Key"] == "K1"
    assert request.extensions["timeout"]["read"] == MUTATION_TIMEOUT_S


def test_delete_model_config_returns_the_entry_or_none(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("DELETE", ITEM, 200, json=engine_fixture("delete_model_config"))
    dropped = client.delete_model_config("guardrail_check", idempotency_key="K1")
    assert dropped is not None
    assert dropped.config_revision == 7
    fake_engine.respond("DELETE", ITEM, 204)
    assert client.delete_model_config("quality_judge", idempotency_key="K2") is None


def test_api_key_put_and_delete(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("PUT", KEY, 204)
    fake_engine.respond("DELETE", KEY, 204)
    client.put_model_api_key("generation", "sk-x", idempotency_key="K1")
    client.delete_model_api_key("generation", idempotency_key="K2")
    put, delete = fake_engine.requests
    assert json.loads(put.content) == {"api_key": "sk-x"}
    assert put.url.path == delete.url.path == "/admin/model-config/generation/api-key"
    assert delete.method == "DELETE"
    assert put.extensions["timeout"]["read"] == MUTATION_TIMEOUT_S

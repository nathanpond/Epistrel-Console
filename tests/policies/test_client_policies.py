"""The client methods #47 adds: requests, timeouts and typed answers."""

import json

import pytest

from console.engine.client import MUTATION_TIMEOUT_S, EngineClient, EngineError
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.policies.helpers import ITEM, LIST, MINORS, RETIRED, TEEN_POLICY, mutation, policy


@pytest.fixture
def client(fake_engine: FakeEngine) -> EngineClient:
    return EngineClient(
        "http://engine.test", "k", transport=fake_engine.transport, principal="console:nate"
    )


def test_list_policies_parses_and_sends_filters(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("GET", LIST, 200, json=engine_fixture("list_policies"))
    listed = client.list_policies()
    assert [p.id for p in listed] == [MINORS, RETIRED, TEEN_POLICY]
    minors = listed[0]
    assert minors.seeded
    assert not minors.retired
    assert minors.kind == "model_assisted"
    assert minors.rule_type == "model_assisted"
    assert listed[1].retired
    assert dict(fake_engine.requests[-1].url.params) == {}
    client.list_policies("rating", include_retired=True)
    assert dict(fake_engine.requests[-1].url.params) == {
        "scope": "rating",
        "include_retired": "1",
    }


def test_get_policy_encodes_the_id(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", ITEM, 200, json=policy(TEEN_POLICY))
    assert client.get_policy(TEEN_POLICY).rule == {
        "type": "wordlist",
        "words": ["gore", "disembowel"],
    }
    client.get_policy("a b")
    assert fake_engine.requests[-1].url.raw_path == b"/admin/policies/a%20b"


def test_writes_send_body_key_and_mutation_timeout(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("POST", LIST, 201, json=mutation())
    fake_engine.respond("PUT", ITEM, 200, json=mutation(TEEN_POLICY))
    fake_engine.respond("DELETE", ITEM, 200, json=mutation(TEEN_POLICY))
    created = client.create_policy({"name": "n"}, idempotency_key="K1")
    assert created.id == "no_ferries"
    assert created.config_revision == 7
    saved = client.update_policy(TEEN_POLICY, {"name": "n2"}, idempotency_key="K2")
    assert saved.config_revision == 8
    retired = client.retire_policy(TEEN_POLICY, idempotency_key="K3")
    assert retired is not None
    post, put, delete = fake_engine.requests
    assert (post.method, json.loads(post.content), post.headers["Idempotency-Key"]) == (
        "POST",
        {"name": "n"},
        "K1",
    )
    assert (put.method, json.loads(put.content), put.headers["Idempotency-Key"]) == (
        "PUT",
        {"name": "n2"},
        "K2",
    )
    assert (delete.method, delete.headers["Idempotency-Key"]) == ("DELETE", "K3")
    for request in (post, put, delete):
        assert request.extensions["timeout"]["read"] == MUTATION_TIMEOUT_S


def test_retire_already_retired_is_none(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("DELETE", ITEM, 204)
    assert client.retire_policy(RETIRED, idempotency_key="K") is None


def test_a_retired_refusal_is_an_engine_error(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    refusal = {
        "error": {
            "code": "validation_error",
            "message": "policy no_addresses is retired",
            "details": {"id": RETIRED, "reason": "retired"},
        }
    }
    fake_engine.respond("PUT", ITEM, 409, json=refusal)
    with pytest.raises(EngineError) as raised:
        client.update_policy(RETIRED, {"name": "n"}, idempotency_key="K")
    assert raised.value.http_status == 409
    assert raised.value.details["reason"] == "retired"

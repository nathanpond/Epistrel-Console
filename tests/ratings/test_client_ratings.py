"""The content-rating client methods #49 adds: requests, timeouts and typed answers."""

import json

import pytest

from console.engine.client import MUTATION_TIMEOUT_S, EngineClient, EngineError
from console.engine.models import ContentRating, RatingVersion
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import (
    EXPLICIT,
    EXPLICIT_POLICY,
    ITEM,
    LIST,
    VERSIONS,
    error,
    mutation,
)


@pytest.fixture
def client(fake_engine: FakeEngine) -> EngineClient:
    return EngineClient(
        "http://engine.test", "k", transport=fake_engine.transport, principal="console:nate"
    )


def test_get_content_rating_parses_effective_guardrails(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("GET", ITEM, 200, json=engine_fixture("get_content_rating"))
    rating = client.get_content_rating(EXPLICIT)
    assert isinstance(rating, ContentRating)
    assert rating.policy_ids == [EXPLICIT_POLICY]
    assert [(g.policy_id, g.source_rating_id) for g in rating.effective_guardrails] == [
        (EXPLICIT_POLICY, EXPLICIT)
    ]
    assert not rating.retired
    client.get_content_rating("a b")
    assert fake_engine.requests[-1].url.raw_path == b"/admin/content-ratings/a%20b"


def test_list_versions(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", VERSIONS, 200, json=engine_fixture("list_content_rating_versions"))
    versions = client.list_content_rating_versions(EXPLICIT)
    assert all(isinstance(v, RatingVersion) for v in versions)
    assert [v.version for v in versions] == [1, 2]
    assert fake_engine.requests[-1].url.path == f"/admin/content-ratings/{EXPLICIT}/versions"


def test_writes_send_body_key_and_mutation_timeout(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("POST", LIST, 201, json=mutation())
    fake_engine.respond("PUT", ITEM, 200, json=mutation("Explicit"))
    fake_engine.respond("DELETE", ITEM, 200, json=mutation("Explicit"))
    created = client.create_content_rating({"name": "Gritty"}, idempotency_key="K1")
    assert (created.name, created.config_revision) == ("Gritty", 12)
    saved = client.update_content_rating(EXPLICIT, {"name": "Explicit"}, idempotency_key="K2")
    assert saved.config_revision == 9
    retired = client.retire_content_rating(EXPLICIT, idempotency_key="K3")
    assert retired is not None
    post, put, delete = fake_engine.requests
    assert (post.method, json.loads(post.content), post.headers["Idempotency-Key"]) == (
        "POST",
        {"name": "Gritty"},
        "K1",
    )
    assert (put.method, put.url.path, put.headers["Idempotency-Key"]) == (
        "PUT",
        f"/admin/content-ratings/{EXPLICIT}",
        "K2",
    )
    assert (delete.method, delete.headers["Idempotency-Key"]) == ("DELETE", "K3")
    for request in (post, put, delete):
        assert request.extensions["timeout"]["read"] == MUTATION_TIMEOUT_S


def test_retire_already_retired_is_none(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("DELETE", ITEM, 204)
    assert client.retire_content_rating(EXPLICIT, idempotency_key="K") is None


def test_default_refusal_raises_with_reason(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("DELETE", ITEM, 409, json=error("validation_error", reason="is_default"))
    with pytest.raises(EngineError) as raised:
        client.retire_content_rating(EXPLICIT, idempotency_key="K")
    assert (raised.value.http_status, raised.value.details["reason"]) == (409, "is_default")

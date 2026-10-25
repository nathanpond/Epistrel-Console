"""engine_client_for: every call for a signed-in operator carries console:<username> (#43)."""

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from console.engine.client import EngineNotConfigured
from console.engine_access import engine_client_for, engine_client_unauthenticated
from tests.fakes.engine import FakeEngine, with_test_thing

URL = "http://engine.test"


def request_for(user: Any) -> HttpRequest:
    request = RequestFactory().get("/")
    request.user = user
    return request


@override_settings(ENGINE_URL=URL, ENGINE_SERVICE_KEY="k" * 32)
def test_mutating_call_carries_the_operator_principal() -> None:
    fake = FakeEngine(with_test_thing(FakeEngine().schema))
    fake.respond("POST", "/_test/thing", 200, json={"name": "x"})
    fake.respond("GET", "/_test/thing", 200, json={"name": "x"})
    client = engine_client_for(request_for(User(username="nate", is_superuser=True)))
    client._transport = fake.transport
    client._request("POST", "/_test/thing")
    client._request("GET", "/_test/thing")
    post, get = fake.requests
    assert post.headers["x-epistrel-principal"] == "console:nate"
    # #34: the principal goes whenever it is set, reads included.
    assert get.headers["x-epistrel-principal"] == "console:nate"
    assert post.headers["authorization"] == f"Bearer {'k' * 32}"


@override_settings(ENGINE_URL=URL)
def test_list_stories_through_the_operator_client(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories", 200, json={"items": []})
    client = engine_client_for(request_for(User(username="nate", is_superuser=True)))
    client._transport = fake_engine.transport
    assert client.list_stories() == []
    assert fake_engine.requests[0].headers["x-epistrel-principal"] == "console:nate"


@override_settings(ENGINE_URL=URL)
def test_each_call_gets_a_new_client() -> None:
    request = request_for(User(username="nate", is_superuser=True))
    assert engine_client_for(request) is not engine_client_for(request)


@override_settings(ENGINE_URL=URL)
@pytest.mark.parametrize(
    "user",
    [
        AnonymousUser(),
        User(username="staffer", is_staff=True, is_superuser=False),
        User(username="gone", is_superuser=True, is_active=False),
    ],
)
def test_non_operator_cannot_obtain_a_client(user: Any) -> None:
    with pytest.raises(PermissionDenied):
        engine_client_for(request_for(user))


@override_settings(ENGINE_URL=None)
def test_not_configured_propagates() -> None:
    with pytest.raises(EngineNotConfigured):
        engine_client_for(request_for(User(username="nate", is_superuser=True)))
    with pytest.raises(EngineNotConfigured):
        engine_client_unauthenticated()


@override_settings(ENGINE_URL=URL)
def test_unauthenticated_client_has_no_principal() -> None:
    assert engine_client_unauthenticated().principal is None

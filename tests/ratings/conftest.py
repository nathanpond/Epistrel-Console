"""Shared set-up for the content-rating pages (#49): a fake Engine behind ``engine_client_for``
(the ratings views, and the policies views for #47's second write)."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import policies, ratings
from tests.fakes.engine import FakeEngine


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine every rating (and policy) view talks to, through the real recording
    client."""
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(ratings, "engine_client_for", client_with_fake)
    monkeypatch.setattr(policies, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake

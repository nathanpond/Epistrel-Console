"""Shared set-up for the history browser tests (#52): a fake Engine behind
``engine_client_for``."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import history as views
from console.views import play as play_views
from console.views import stories as story_views
from tests.fakes.engine import FakeEngine


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the history view talks to (through the real recording client)."""
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    monkeypatch.setattr(play_views, "engine_client_for", client_with_fake)  # History links
    monkeypatch.setattr(story_views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake

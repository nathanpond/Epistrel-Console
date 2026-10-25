"""Shared set-up for the timeline operation tests (#53): a fake Engine behind
``engine_client_for`` in every view the operations touch."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import history as history_views
from console.views import play as play_views
from console.views import stories as story_views
from console.views import timeline as views
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the timeline, play, history and story views talk to (through the real
    recording client, so every operation lands in the operation log)."""
    fake = FakeEngine()
    answer_relations(fake)  # #79: every owned entity's page reads its relations

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (views, play_views, history_views, story_views):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake

"""Shared set-up for the play view tests (#48): a fake Engine behind ``engine_client_for``."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import play as views
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine every play view talks to (through the real recording client)."""
    fake = FakeEngine()
    answer_relations(fake)  # #79: every owned entity's page reads its relations

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake

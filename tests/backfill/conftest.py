"""The Backfill page tests' fake Engine (#308)."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import backfill as backfill_views
from console.views import story_pages
from tests.backfill.helpers import BACKFILL_PATH, CONFIG_PATH, STORY_PATH, config, m7
from tests.fakes.engine import FakeEngine


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    fake.respond("GET", STORY_PATH, 200, json=m7("story"))
    fake.respond("GET", CONFIG_PATH, 200, json=config())
    fake.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_extracting"))

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    monkeypatch.setattr(backfill_views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake

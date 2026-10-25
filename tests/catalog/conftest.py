"""Shared set-up for the catalog pages (#65; the scenario pages, #68): a fake Engine behind
``engine_client_for``."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import (
    catalog_characters,
    catalog_lifecycle,
    catalog_scenarios,
    media_picker,
    media_proxy,
)
from tests.catalog.media_helpers import answer_media
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import answer_settings


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the catalog views (and the media proxy) talk to, through the real
    recording client. The companion's draft is answered by default (#66: the detail page's
    Publish reads it), and so is ``/admin/settings`` (#69: the detail page's listing-status
    select reads it); a test that cares registers its own. ``GET /media/{id}`` answers a live
    image with the id asked (#71: detail and edit pages look up each attached asset)."""
    fake = FakeEngine()
    fake.respond(
        "GET",
        "/catalog/characters/{character_id}/companion",
        200,
        json=engine_fixture("catalog_characters_companion_draft"),
    )
    answer_settings(fake)
    answer_media(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(catalog_characters, "engine_client_for", client_with_fake)
    monkeypatch.setattr(media_proxy, "engine_client_for", client_with_fake)
    monkeypatch.setattr(catalog_scenarios, "engine_client_for", client_with_fake)
    monkeypatch.setattr(catalog_lifecycle, "engine_client_for", client_with_fake)
    monkeypatch.setattr(media_picker, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake

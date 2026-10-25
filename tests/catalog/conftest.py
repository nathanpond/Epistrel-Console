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
    rating_suggestion,
)
from tests.catalog.helpers import COMPANION, companion_reads
from tests.catalog.media_helpers import answer_media
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fixtures import engine_fixture
from tests.ratings.helpers import answer_settings


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the catalog views (and the media proxy) talk to, through the real
    recording client. The companion is answered by default (#66: the detail page's Publish
    reads its draft; #74: the detail page links its card), and so is ``/admin/settings``
    (#69: the detail page's listing-status select reads it); a test that cares registers its
    own. ``GET /media/{id}`` answers a live image with the id asked (#71: detail and edit pages
    look up each attached asset), and ``GET /catalog/characters`` the list fixture (#73: the
    scenario editor's role character select reads the published characters)."""
    fake = FakeEngine()
    answer_relations(fake)  # #79: every owned entity's page reads its relations
    fake.side_effect("GET", COMPANION, companion_reads())
    answer_settings(fake)
    answer_media(fake)
    fake.respond("GET", "/catalog/characters", 200, json=engine_fixture("list_catalog_characters"))

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(catalog_characters, "engine_client_for", client_with_fake)
    monkeypatch.setattr(media_proxy, "engine_client_for", client_with_fake)
    monkeypatch.setattr(catalog_scenarios, "engine_client_for", client_with_fake)
    monkeypatch.setattr(catalog_lifecycle, "engine_client_for", client_with_fake)
    monkeypatch.setattr(media_picker, "engine_client_for", client_with_fake)
    monkeypatch.setattr(rating_suggestion, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake

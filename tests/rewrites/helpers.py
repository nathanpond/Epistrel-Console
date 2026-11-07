"""Shared set-up for #297's Rewrite page and session page tests: the fake Engine behind #296's
frame, answering #292's ``m7/story.json`` cast and the Full v4 configuration
(``tests/inspect/helpers.py::full_v4``)."""

import copy
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import story_pages
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import full_v4

STORY = "01JM7ST0000000000000000001"
SESSION = "01JM7RW0000000000000000001"
S = f"/stories/{STORY}/"
URL = f"{S}rewrites/"
SESSION_URL = f"{URL}{SESSION}/"
STORY_READ = f"/stories/{STORY}"
CONFIG_READ = f"/stories/{STORY}/effective-config"
LIST = f"/stories/{STORY}/rewrites"
GET_ONE = f"{LIST}/{SESSION}"
LIST_ROUTE = "/stories/{story_id}/rewrites"
ONE_ROUTE = "/stories/{story_id}/rewrites/{session_id}"
M7_PAGES = "tests.urls_with_m7_pages"
KEY_INPUT = re.compile(r'name="idempotency_key" value="([^"]+)"')


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def written(name: str, **changes: Any) -> dict[str, Any]:
    """A session fixture as a session write's answer (``RewriteSessionBody``)."""
    body: dict[str, Any] = m7(name)
    body.setdefault("timeline_revision", 20)
    body.setdefault("idempotency_key", None)
    body.setdefault("apply", None)
    body.update(changes)
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    fake.respond("GET", "/stories/{story_id}", 200, json=m7("story"))
    fake.respond("GET", "/stories/{story_id}/effective-config", 200, json=full_v4())

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def answer_raw(fake: FakeEngine, method: str, route: str, body: Any, status: int = 200) -> None:
    """Answer ``route`` with ``body`` unvalidated (a rejected list entry, an open enum)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    fake.side_effect(method, route, handler)


def calls(fake: FakeEngine) -> list[tuple[str, str]]:
    return [(r.method, r.url.path) for r in fake.requests]


def queries(fake: FakeEngine, method: str, path: str) -> list[list[tuple[str, str]]]:
    return [
        list(r.url.params.multi_items())
        for r in fake.requests
        if r.method == method and r.url.path == path
    ]


def key_in(html: str) -> str:
    keys = KEY_INPUT.findall(html)
    assert keys, "no idempotency key on the page"
    return str(keys[0])

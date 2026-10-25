"""Helpers for the history browser tests (#52): the "Harbour" story (Mara, Tobin) with three
turns, the last one's drafts rejected by the ``no_stub52`` policy (captured from a live edge)."""

import copy
from typing import Any
from urllib.parse import parse_qs, urlsplit

from django.test import Client

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import MARA, STORY, TOBIN, story_json

__all__ = ["MARA", "STORY", "TOBIN"]

URL = f"/stories/{STORY}/history/"
HISTORY = "/stories/{story_id}/history"
VARIANTS = "/stories/{story_id}/variants"
SETTINGS = "/stories/{story_id}/settings"
STORY_PATH = "/stories/{story_id}"
TURN3_OP = "01M5R5XWJH0Y9SEDSFMZEG2SFF"


def history_json(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_history_audit"))
    body.update(changes)
    return body


def variants_json() -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("list_variants")
    return body


def settings_json(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("get_story_settings")
    body.update(changes)
    return body


def policy(policy_id: str, name: str | None) -> dict[str, Any]:
    return {
        "id": policy_id,
        "name": name,
        "scope": "global",
        "source": "global",
        "applies_to": ["text_output"],
        "age_condition": None,
        "kind": "deterministic",
    }


def answer(
    engine: FakeEngine,
    *,
    history: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> None:
    """The page's reads: the story (head 3), its variants, history and settings."""
    engine.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=4))
    engine.respond("GET", VARIANTS, 200, json=variants_json())
    engine.respond("GET", HISTORY, 200, json=history or history_json())
    engine.respond(
        "GET",
        SETTINGS,
        200,
        json=settings or settings_json(effective_policies=[policy("no_stub52", "no stub")]),
    )


def history_calls(engine: FakeEngine) -> list[dict[str, list[str]]]:
    """The query of every history call made, parsed."""
    return [
        parse_qs(request.url.query.decode(), keep_blank_values=True)
        for request in engine.requests
        if request.url.path.endswith("/history")
    ]


def calls_to(engine: FakeEngine, suffix: str) -> int:
    return sum(1 for request in engine.requests if request.url.path.endswith(suffix))


def page(client: Client, query: str = "", **headers: str) -> str:
    response = client.get(f"{URL}?{query}" if query else URL, headers=headers)
    assert response.status_code == 200
    return response.content.decode()


def query_of(href: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(href.replace("&amp;", "&")).query)

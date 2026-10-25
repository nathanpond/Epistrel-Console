"""Helpers for the story settings page tests (#54): the "Harbour" story (Mara, Tobin)."""

import copy
import json
import re
from typing import Any

from tests.engine_panel.helpers import form_data, settings_body
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import MARA, STORY, TOBIN, story_json

__all__ = ["MARA", "STORY", "TOBIN", "form_data", "story_json"]

URL = f"/stories/{STORY}/settings/"
POLICY_URL = f"/stories/{STORY}/settings/policies/"
PINNED_URL = f"/stories/{STORY}/settings/pinned/"
INSTRUCTIONS_URL = f"/stories/{STORY}/settings/instructions/"
STORY_PATH = "/stories/{story_id}"
SETTINGS = "/stories/{story_id}/settings"
PINNED = "/stories/{story_id}/pinned-memories"
INSTRUCTIONS = "/stories/{story_id}/custom-instructions"
CHANGES = "/stories/{story_id}/config-changes"
ADMIN_SETTINGS = "/admin/settings"
POLICY = "/admin/policies/{policy_id}"


def settings_json(fixture: str = "get_story_settings", **settings: Any) -> dict[str, Any]:
    """The settings read; ``settings`` patches its ``settings`` object."""
    body: dict[str, Any] = copy.deepcopy(engine_fixture(fixture))
    body["settings"].update(settings)
    return body


def written_json(**settings: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("patch_story_settings"))
    body["settings"].update(settings)
    return body


def answer_page(
    engine: FakeEngine,
    *,
    settings: dict[str, Any] | None = None,
    deployment: dict[str, Any] | None = None,
    pinned: dict[str, Any] | None = None,
    instructions: dict[str, Any] | None = None,
    changes: dict[str, Any] | None = None,
    story: dict[str, Any] | None = None,
) -> None:
    """Every read the page makes."""
    engine.respond("GET", STORY_PATH, 200, json=story or story_json())
    engine.respond("GET", SETTINGS, 200, json=settings or settings_json())
    engine.respond("GET", ADMIN_SETTINGS, 200, json=deployment or settings_body())
    engine.respond("GET", PINNED, 200, json=pinned or engine_fixture("get_pinned_memories"))
    engine.respond(
        "GET", INSTRUCTIONS, 200, json=instructions or engine_fixture("get_custom_instructions")
    )
    engine.respond("GET", CHANGES, 200, json=changes or engine_fixture("list_story_config_changes"))


def calls(fake: FakeEngine, method: str | None = None, path: str | None = None) -> list[Any]:
    return [
        r
        for r in fake.requests
        if (method is None or r.method == method) and (path is None or r.url.path == path)
    ]


def writes(fake: FakeEngine) -> list[Any]:
    return [r for r in fake.requests if r.method in ("POST", "PUT", "PATCH", "DELETE")]


def body(request: Any) -> Any:
    return json.loads(request.content)


def error(code: str, message: str = "refused", **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def snapshot_of(html: str) -> str:
    match = re.search(r'name="snapshot" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def section(html: str, anchor: str) -> str:
    """The page's ``<section id=anchor>`` markup."""
    match = re.search(rf'<section id="{anchor}">(.*?)</section>', html, re.S)
    assert match is not None, anchor
    return match.group(1)

"""Helpers for the catalog character tests (#65)."""

import copy
import json
import re
from typing import Any

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import answer_ratings, answer_settings, calls, error, text

__all__ = ["answer_ratings", "answer_settings", "calls", "error", "text"]

LIST = "/catalog/characters"
ITEM = "/catalog/characters/{character_id}"
VERSIONS = "/catalog/characters/{character_id}/versions"
CONTENT = "/media/{media_id}/content"
SETTINGS = "/admin/settings"
RATINGS = "/admin/content-ratings"
CHAR = "01JC00000000000000000CHAR1"
CHAR2 = "01JC00000000000000000CHAR2"
MED1 = "01JC000000000000000000MED1"
MED2 = "01JC000000000000000000MED2"
MED3 = "01JC000000000000000000MED3"
MED4 = "01JC000000000000000000MED4"
SCENARIO = "01JC000000000000000000SCN1"
TEEN = "00000000000000000000RAT002"
URL = "/catalog/characters/"
DETAIL = f"/catalog/characters/{CHAR}/"


def listing(*items: dict[str, Any], next_cursor: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("list_catalog_characters"))
    if items:
        body["items"] = list(items)
    body["next_cursor"] = next_cursor
    return body


def list_item(**changes: Any) -> dict[str, Any]:
    item: dict[str, Any] = copy.deepcopy(engine_fixture("list_catalog_characters")["items"][0])
    return {**item, **changes}


def full(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_catalog_character_full"))
    body.update(changes)
    return body


def draft(**changes: Any) -> dict[str, Any]:
    """The draft of :func:`full`: same content, a draft's version fields and timestamps."""
    defaults: dict[str, Any] = {
        "version": None,
        "draft_revision": 5,
        "updated_at": "2026-10-22T10:00:00.000000Z",
    }
    return full(**{**defaults, **changes})


def card(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_catalog_character_card"))
    return {**body, **changes}


def answer_lookups(engine: FakeEngine) -> None:
    """The list page's two lookups: the ratings and ``/admin/settings``."""
    answer_ratings(engine)
    answer_settings(engine)


def list_params(engine: FakeEngine) -> list[tuple[str, str]]:
    (listed,) = calls(engine, "GET", LIST)
    return list(listed.url.params.multi_items())


def item_reads(engine: FakeEngine) -> list[dict[str, str]]:
    return [dict(r.url.params) for r in calls(engine, "GET", ITEM.format(character_id=CHAR))]


# --- #66: create, edit and publish ----------------------------------------------------------------

COMPANION = "/catalog/characters/{character_id}/companion"
PUBLISH = "/catalog/characters/{character_id}/publish"
NEW_URL = "/catalog/characters/new/"
EDIT = f"/catalog/characters/{CHAR}/edit/"
PUBLISH_URL = f"/catalog/characters/{CHAR}/publish/"
NEW_CHAR = "01JC00000000000000000CHAR9"
HIDDEN_FIELDS = (
    "snapshot",
    "idempotency_key",
    "publish_idempotency_key",
    "draft_revision",
    "companion_draft_revision",
)


def companion(draft_revision: int = 3, **changes: Any) -> dict[str, Any]:
    """The companion's draft (``ScenarioFull``) at ``draft_revision``."""
    body: dict[str, Any] = copy.deepcopy(engine_fixture("catalog_characters_companion_draft"))
    return {**body, "draft_revision": draft_revision, **changes}


def patched(draft_revision: int = 6, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("catalog_characters_patch"))
    return {**body, "draft_revision": draft_revision, **changes}


def published(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("catalog_characters_publish"))
    return {**body, **changes}


def answer_edit_reads(
    engine: FakeEngine, body: dict[str, Any] | None = None, companion_revision: int = 3
) -> None:
    """The edit page's reads: the draft, the companion's draft and the ratings."""
    engine.respond("GET", ITEM, 200, json=body if body is not None else draft())
    engine.respond("GET", COMPANION, 200, json=companion(companion_revision))
    answer_ratings(engine)


def form_data(response: Any) -> dict[str, str]:
    """What the rendered form posts back unchanged: every field's shown value and the hidden
    inputs (the snapshot, keys and revisions)."""
    form = response.context["form"]
    data: dict[str, str] = {}
    for name in form.fields:
        value = form[name].value()
        if value is False or value is None:
            continue
        data[name] = "on" if value is True else str(value)
    html = response.content.decode()
    for name in HIDDEN_FIELDS:
        match = re.search(rf'name="{name}" value="([^"]*)"', html)
        if match:
            data[name] = match.group(1)
    return data


def writes(engine: FakeEngine) -> list[Any]:
    """The requests that write (anything but a GET)."""
    return [r for r in engine.requests if r.method != "GET"]


def body_of(request: Any) -> Any:
    return json.loads(request.content)

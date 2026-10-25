"""Helpers for the catalog character tests (#65)."""

import copy
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

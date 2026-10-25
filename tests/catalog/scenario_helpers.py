"""Helpers for the catalog scenario tests (#68)."""

import copy
from typing import Any

from django.http import QueryDict

from tests.catalog.helpers import CHAR, TEEN, answer_ratings, answer_settings, calls
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

S_LIST = "/catalog/scenarios"
S_ITEM = "/catalog/scenarios/{scenario_id}"
S_VERSIONS = "/catalog/scenarios/{scenario_id}/versions"
C_ITEM = "/catalog/characters/{character_id}"
PROFILES = "/engine-profiles"
SCN = "01JC000000000000000000SCN2"
COMPANION_SCN = "01JC000000000000000000SCN1"
NEW_SCN = "01JC000000000000000000SCN9"
URL = "/catalog/scenarios/"
DETAIL = f"/catalog/scenarios/{SCN}/"
EDIT = f"/catalog/scenarios/{SCN}/edit/"
NEW_URL = "/catalog/scenarios/new/"
PATH = f"/catalog/scenarios/{SCN}"
HIDDEN = ("snapshot", "idempotency_key", "draft_revision")

__all__ = ["CHAR", "TEEN", "answer_ratings", "answer_settings", "calls"]


def fixture(name: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"catalog_scenarios_{name}"))
    body.update(changes)
    return body


def detail(**changes: Any) -> dict[str, Any]:
    return fixture("detail", **changes)


def draft(**changes: Any) -> dict[str, Any]:
    return fixture("draft", **changes)


def listing(*items: dict[str, Any], next_cursor: str | None = None) -> dict[str, Any]:
    body = fixture("list")
    if items:
        body["items"] = list(items)
    body["next_cursor"] = next_cursor
    return body


def list_item(index: int = 0, **changes: Any) -> dict[str, Any]:
    item: dict[str, Any] = copy.deepcopy(fixture("list")["items"][index])
    return {**item, **changes}


def card_of(body: dict[str, Any]) -> dict[str, Any]:
    """The public card (``ScenarioCard``) of the first listed scenario, with ``body``'s id."""
    item = list_item(0, id=body["id"])
    for key in ("owner_principal", "updated_at", "rank"):
        item.pop(key)
    return item


def profile(profile_id: str, name: str, **changes: Any) -> dict[str, Any]:
    body = {
        "id": profile_id,
        "name": name,
        "owner_principal": "system",
        "preset": True,
        "version": 1,
        "current_version": 1,
        "settings": {},
        "created_at": "2026-10-01T09:00:00.000000Z",
        "updated_at": "2026-10-01T09:00:00.000000Z",
        "deleted_at": None,
    }
    return {**body, **changes}


def answer_profiles(engine: FakeEngine, *extra: dict[str, Any]) -> None:
    items = [profile("lite-default", "Lite default"), profile("structured", "Structured"), *extra]
    engine.respond("GET", PROFILES, 200, json={"items": items, "next_cursor": None})


def answer_edit_reads(engine: FakeEngine, body: dict[str, Any] | None = None) -> None:
    """The edit page's reads: the draft, the ratings and the engine profiles."""
    engine.respond("GET", S_ITEM, 200, json=body if body is not None else draft())
    answer_ratings(engine)
    answer_profiles(engine)


def posted(response: Any) -> QueryDict:
    """What the rendered editor posts back unchanged: its bound state plus the hidden inputs."""
    import re

    data: QueryDict = response.context["editor"].data.copy()
    html = response.content.decode()
    for name in HIDDEN:
        match = re.search(rf'name="{name}" value="([^"]*)"', html)
        if match:
            data[name] = match.group(1)
    return data


def writes(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method != "GET"]


def as_post(data: QueryDict | dict[str, Any]) -> dict[str, Any]:
    """``data`` as the test client posts it (every value of a repeated key kept)."""
    if isinstance(data, QueryDict):
        return dict(data.lists())
    return data


RATINGS_PATH = "/admin/content-ratings"

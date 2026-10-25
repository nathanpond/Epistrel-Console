"""Helpers for the content-rating tests (#49)."""

import copy
import json
import re
from collections.abc import Sequence
from typing import Any

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

LIST = "/admin/content-ratings"
ITEM = "/admin/content-ratings/{rating_id}"
VERSIONS = "/admin/content-ratings/{rating_id}/versions"
SETTINGS = "/admin/settings"
POLICIES = "/admin/policies"
EVERYONE = "00000000000000000000RAT001"
TEEN = "00000000000000000000RAT002"
MATURE = "00000000000000000000RAT003"
EXPLICIT = "00000000000000000000RAT004"
MIXED = "01M5R0MIX00000000000000006"  # synthetic: inherits from Teen and Mature
EXPLICIT_POLICY = "01M5R0EXPL0000000000000001"
TEEN_POLICY = "01M5R0TEEN0000000000000001"
IDEMPOTENCY_KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KEY"
URL = "/guardrails/ratings/"


def rating(name: str, **changes: Any) -> dict[str, Any]:
    """A ``RatingBody`` from the four seeded ratings (``Mixed``: the synthetic multi-parent
    rating), with ``changes``."""
    if name == "Mixed":
        mixed = {
            "id": MIXED,
            "name": "Mixed",
            "display_order": 25,
            "inherits_from": [TEEN, MATURE],
            "version_id": "01M5R0RTV00000000000000006",
        }
        return {**rating("Teen"), **mixed, **changes}
    for item in engine_fixture("list_content_ratings_all")["items"]:
        if item["name"] == name:
            return {**copy.deepcopy(item), **changes}
    raise KeyError(name)


def detail(name: str, guardrails: Sequence[tuple[str, str]] = (), **changes: Any) -> dict[str, Any]:
    """A ``RatingDetailBody``: the rating with ``(policy_id, source_rating_id)`` guardrails."""
    body = rating(name, **changes)
    body["effective_guardrails"] = [
        {"policy_id": policy_id, "source_rating_id": source} for policy_id, source in guardrails
    ]
    return body


def mutation(name: str = "Gritty", **changes: Any) -> dict[str, Any]:
    """A ``RatingMutation``: the create fixture, or a seeded rating with a revision."""
    if name == "Gritty":
        base: dict[str, Any] = copy.deepcopy(engine_fixture("create_content_rating"))
    else:
        base = {**rating(name), "config_revision": 9, "idempotency_key": IDEMPOTENCY_KEY}
    return {**base, **changes}


def answer_ratings(engine: FakeEngine, *extra: dict[str, Any], **changes: dict[str, Any]) -> None:
    """``GET /admin/content-ratings`` with the four seeded ratings (``changes`` by name) and
    ``extra`` ratings appended."""
    items = [rating(item["name"], **changes.get(item["name"], {})) for item in seeded()]
    engine.respond("GET", LIST, 200, json={"items": [*items, *extra]})


def seeded() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = engine_fixture("list_content_ratings_all")["items"]
    return items


def answer_settings(engine: FakeEngine, default: str = TEEN) -> None:
    """``GET /admin/settings`` with ``default`` as the default rating (Teen, per the planner)."""
    body = copy.deepcopy(engine_fixture("get_deployment_settings"))
    body["default_content_rating_id"] = default
    engine.respond("GET", SETTINGS, 200, json=body)


def rating_policy(policy_id: str, rating_id: str, name: str, **changes: Any) -> dict[str, Any]:
    """A rating-scoped ``PolicyBody`` (built from the Teen wordlist policy fixture)."""
    for item in engine_fixture("list_policies")["items"]:
        if item["id"] == TEEN_POLICY:
            return {
                **copy.deepcopy(item),
                "id": policy_id,
                "scope_id": rating_id,
                "name": name,
                **changes,
            }
    raise KeyError(TEEN_POLICY)


def answer_policies(engine: FakeEngine, *items: dict[str, Any]) -> None:
    """``GET /admin/policies``: Explicit's own policy and Teen's, plus ``items``."""
    base = [
        rating_policy(EXPLICIT_POLICY, EXPLICIT, "No dragons"),
        rating_policy(TEEN_POLICY, TEEN, "No gore words"),
    ]
    engine.respond("GET", POLICIES, 200, json={"items": [*base, *items]})


def answer_versions(engine: FakeEngine, items: list[dict[str, Any]] | None = None) -> None:
    body = engine_fixture("list_content_rating_versions") if items is None else {"items": items}
    engine.respond("GET", VERSIONS, 200, json=body)


def calls(fake: FakeEngine, method: str | None = None, path: str | None = None) -> list[Any]:
    return [
        r
        for r in fake.requests
        if (method is None or r.method == method) and (path is None or r.url.path == path)
    ]


def sent(request: Any) -> Any:
    return json.loads(request.content)


def error(code: str, message: str = "refused", **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def hidden(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, f"no hidden {name}"
    return match.group(1)


def text(html: str) -> str:
    """The page's text: tags dropped, whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()

"""Helpers for the policy page tests (#47)."""

import copy
from typing import Any

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

LIST = "/admin/policies"
ITEM = "/admin/policies/{policy_id}"
RATINGS = "/admin/content-ratings"
MINORS = "minors_no_sexual_content"
TEEN_POLICY = "01M5R0TEEN0000000000000001"
RETIRED = "no_addresses"
TEEN = "00000000000000000000RAT002"
EXPLICIT = "00000000000000000000RAT004"
IDEMPOTENCY_KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KEY"


def policy(policy_id: str, **changes: Any) -> dict[str, Any]:
    """A ``PolicyBody`` from the list fixture, with ``changes``."""
    for item in engine_fixture("list_policies")["items"]:
        if item["id"] == policy_id:
            return {**copy.deepcopy(item), **changes}
    raise KeyError(policy_id)


def mutation(policy_id: str = "no_ferries", **changes: Any) -> dict[str, Any]:
    """A ``PolicyMutation``: the create fixture, or a listed policy with a revision."""
    if policy_id == "no_ferries":
        base: dict[str, Any] = copy.deepcopy(engine_fixture("create_policy"))
    else:
        base = {**policy(policy_id), "config_revision": 8, "idempotency_key": IDEMPOTENCY_KEY}
    return {**base, **changes}


def answer_ratings(engine: FakeEngine, **changes: dict[str, Any]) -> dict[str, Any]:
    """``GET /admin/content-ratings`` with the four seeded ratings (``changes`` by name)."""
    body: dict[str, Any] = copy.deepcopy(engine_fixture("list_content_ratings_all"))
    for item in body["items"]:
        item.update(changes.get(item["name"], {}))
    engine.respond("GET", RATINGS, 200, json=body)
    return body


def answer_list(engine: FakeEngine, items: list[dict[str, Any]] | None = None) -> None:
    """``GET /admin/policies`` (the fixture by default) and the ratings."""
    body = engine_fixture("list_policies") if items is None else {"items": items}
    engine.respond("GET", LIST, 200, json=body)
    answer_ratings(engine)


def calls(fake: FakeEngine, method: str | None = None, path: str | None = None) -> list[Any]:
    return [
        r
        for r in fake.requests
        if (method is None or r.method == method) and (path is None or r.url.path == path)
    ]


def error(code: str, message: str = "refused", **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def wordlist_post(**changes: str | list[str]) -> dict[str, Any]:
    """A valid new-policy POST: "no ferries", global, wordlist ``ferry``."""
    return {
        "id": "",
        "scope": "global",
        "name": "no ferries",
        "applies_to": ["text_output"],
        "rule_type": "wordlist",
        "words": "ferry",
        "patterns": "",
        "prompt": "",
        "on_uncertain": "violation",
        "age_condition": "",
        "message": "Ferries are off-limits",
        "idempotency_key": IDEMPOTENCY_KEY,
        **changes,
    }

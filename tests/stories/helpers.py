"""Helpers for the story page tests (#45)."""

from typing import Any

from tests.fakes.engine import FakeEngine


def summary(story_id: str, name: str | None, status: str, **extra: Any) -> dict[str, Any]:
    """One ``GET /stories`` item."""
    return {
        "id": story_id,
        "name": name,
        "owner_principal": "console:nate",
        "status": status,
        "timeline_revision": extra.get("timeline_revision", 1),
        "next_turn_index": 1,
        "content_rating": {
            "id": "00000000000000000000RAT002",
            "version_id": "00000000000000000000RTV002",
            "name": "Teen",
        },
        "created_at": "2026-10-24T21:36:26Z",
        "archived_at": "2026-10-24T22:00:00Z" if status == "archived" else None,
    }


def calls(fake: FakeEngine, method: str | None = None, path: str | None = None) -> list[Any]:
    """The requests the fake saw, optionally only one method and/or exact path."""
    return [
        r
        for r in fake.requests
        if (method is None or r.method == method) and (path is None or r.url.path == path)
    ]

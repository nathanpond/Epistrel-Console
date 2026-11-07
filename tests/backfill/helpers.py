"""Shared fixtures for the Backfill page tests (#308): the fake Engine wired into the page's
views, the m7 cast and backfill bodies."""

import copy
from typing import Any

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import full_v4

STORY = "01JM7ST0000000000000000001"
BACKFILL_ID = "01JM7BF0000000000000000001"
PAGE = f"/stories/{STORY}/backfill/"
CANCEL = f"/stories/{STORY}/backfill/cancel/"
STORY_PATH = "/stories/{story_id}"
CONFIG_PATH = "/stories/{story_id}/effective-config"
BACKFILL_PATH = "/stories/{story_id}/backfill"
STORY_READ = f"/stories/{STORY}"
CONFIG_READ = f"/stories/{STORY}/effective-config"
BACKFILL_READ = f"/stories/{STORY}/backfill"


def m7(name: str) -> dict[str, Any]:
    body = copy.deepcopy(engine_fixture(f"m7/{name}"))
    assert isinstance(body, dict)
    return body


def config(**changes: Any) -> dict[str, Any]:
    body = full_v4()
    body["story_id"] = STORY
    body.update(changes)
    return body


def paths(fake: FakeEngine) -> list[str]:
    return [f"{r.method} {r.url.path}" for r in fake.requests]

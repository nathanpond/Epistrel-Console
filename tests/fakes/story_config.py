"""Fake-Engine answers for #85's story configuration.

The play page's header reads ``GET /stories/{id}/effective-config`` for the fingerprint and
quality status, so each test fixture that renders the play page calls
:func:`answer_effective_config`: the read is answered from
``tests/fixtures/engine/get_effective_config.json`` for whichever story the page asks about. A
test that needs something else replaces the route with ``FakeEngine.respond``.
"""

import copy
from typing import Any

import httpx

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

EFFECTIVE = "/stories/{story_id}/effective-config"


def effective_config(**changes: Any) -> dict[str, Any]:
    """A ``EffectiveConfig`` body (Lite v2, following, no overrides), with ``changes`` laid
    over."""
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_effective_config"))
    return {**body, **changes}


def answer_effective_config(fake: FakeEngine, **changes: Any) -> None:
    """Answer every story's effective-config read with :func:`effective_config`."""

    def handler(request: httpx.Request) -> httpx.Response:
        story_id = request.url.path.split("/")[-2]
        return httpx.Response(200, json=effective_config(**{"story_id": story_id, **changes}))

    fake.side_effect("GET", EFFECTIVE, handler)

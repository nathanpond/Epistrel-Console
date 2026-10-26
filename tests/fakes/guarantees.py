"""Fake-Engine answers for #57's guarantee tables.

The profile page (#78) and a story's Config page (#85) each read a guarantee table, so a test
fixture that renders either calls :func:`answer_guarantees`: both routes answer the hand-built
Lite table in ``tests/fixtures/inspect/guarantees_lite.json``. A test that needs another table
replaces the route with ``FakeEngine.respond``.
"""

import copy
import json
from pathlib import Path
from typing import Any

from tests.fakes.engine import FakeEngine

PROFILE_GUARANTEES = "/engine-profiles/{profile_id}/guarantees"
STORY_GUARANTEES = "/stories/{story_id}/guarantees"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "inspect"


def guarantees_fixture(name: str = "lite") -> dict[str, Any]:
    """The parsed ``tests/fixtures/inspect/guarantees_<name>.json``."""
    body: dict[str, Any] = json.loads(
        (FIXTURES / f"guarantees_{name}.json").read_text(encoding="utf-8")
    )
    return copy.deepcopy(body)


def answer_guarantees(fake: FakeEngine, name: str = "lite") -> None:
    """Answer both guarantee routes with :func:`guarantees_fixture`."""
    body = guarantees_fixture(name)
    fake.respond("GET", PROFILE_GUARANTEES, 200, json=body)
    fake.respond("GET", STORY_GUARANTEES, 200, json=body)

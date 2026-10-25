"""Helpers for the Inspect tests (#135): the seeded Lite and "Structured (M4)" effective
configurations (``tests/fixtures/inspect/``), with changes laid over ``effective``."""

import copy
import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "inspect"
STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
EFFECTIVE = "/stories/{story_id}/effective-config"
STORY_PATH = "/stories/{story_id}"


def config_body(kind: str, **effective: Any) -> dict[str, Any]:
    """The ``effective_config_<kind>.json`` body; each ``effective`` key is a ``__``-separated
    path set in its ``effective`` document (``None`` removes the key)."""
    body: dict[str, Any] = json.loads((FIXTURES / f"effective_config_{kind}.json").read_text())
    body = copy.deepcopy(body)
    for path, value in effective.items():
        *parents, leaf = path.split("__")
        node = body["effective"]
        for key in parents:
            node = node.setdefault(key, {})
        if value is None:
            node.pop(leaf, None)
        else:
            node[leaf] = value
    return body


def lite(**effective: Any) -> dict[str, Any]:
    return config_body("lite", **effective)


def structured(**effective: Any) -> dict[str, Any]:
    return config_body("structured", **effective)

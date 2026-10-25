"""Fake-Engine fixtures: ``tests/fixtures/engine/<endpoint>.json``, validated against the
recorded schema whenever a test serves them through ``FakeEngine.respond``."""

import json
from pathlib import Path
from typing import Any

ENGINE = Path(__file__).resolve().parent / "engine"


def engine_fixture(name: str) -> Any:
    """The parsed ``tests/fixtures/engine/<name>.json``."""
    return json.loads((ENGINE / f"{name}.json").read_text(encoding="utf-8"))

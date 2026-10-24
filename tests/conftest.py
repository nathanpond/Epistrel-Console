import os

import pytest

from tests.fakes.engine import FakeEngine


@pytest.fixture(autouse=True)
def _no_console_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never depend on the developer's exported CONSOLE_* variables."""
    for name in list(os.environ):
        if name.startswith("CONSOLE_"):
            monkeypatch.delenv(name)


@pytest.fixture
def fake_engine() -> FakeEngine:
    """A fake Engine built from the recorded schema (see tests/fakes/engine.py)."""
    return FakeEngine()

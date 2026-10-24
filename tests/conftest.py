import os

import pytest


@pytest.fixture(autouse=True)
def _no_console_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never depend on the developer's exported CONSOLE_* variables."""
    for name in list(os.environ):
        if name.startswith("CONSOLE_"):
            monkeypatch.delenv(name)

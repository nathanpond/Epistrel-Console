import os
from typing import Any

import pytest

from tests.fakes.engine import FakeEngine

pytest_plugins = ["pytester", "tests.plugins.require_docker", "tests.plugins.engine_live"]


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


OPERATOR_PASSWORD = "operator-test-password-0000"  # noqa: S105 - throwaway test account


@pytest.fixture
def operator(db: None) -> Any:
    """A signed-in-capable operator: an active superuser named ``nate``."""
    from django.contrib.auth.models import User

    return User.objects.create_superuser("nate", "nate@example.invalid", OPERATOR_PASSWORD)


@pytest.fixture
def operator_client(client: Any, operator: Any) -> Any:
    """Django's test client, signed in as :func:`operator`."""
    client.force_login(operator)
    return client


@pytest.fixture(autouse=True)
def badge_probe_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The shell's badge never reaches a real Engine in tests: its own probe (used on pages that
    did not probe already) answers "not configured". Returns the list of its calls."""
    from console import context_processors
    from console.engine_probe import ProbeResult

    calls: list[int] = []

    def offline() -> ProbeResult:
        calls.append(1)
        return ProbeResult("unreachable", False, None, None, None, "not configured", None)

    monkeypatch.setattr(context_processors, "probe_for_badge", offline)
    return calls

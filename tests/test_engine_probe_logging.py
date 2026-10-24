import logging
import time
from collections.abc import Callable

import httpx
import pytest
from django.test import override_settings

from console import engine_probe
from console.engine.client import EngineClient


@pytest.fixture(autouse=True)
def _fresh_log_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_probe, "_last_state", None)
    monkeypatch.setattr(engine_probe, "_last_logged", 0.0)


def client(handler: Callable[[httpx.Request], httpx.Response]) -> EngineClient:
    return EngineClient.from_settings(transport=httpx.MockTransport(handler))


def down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused")


@override_settings(ENGINE_URL="http://engine.test", ENGINE_SERVICE_KEY="sekrit-123")
def test_failure_logged_once_per_minute(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    caplog.set_level(logging.INFO, logger="console.engine_probe")
    for _ in range(3):
        engine_probe.probe(client(down))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "connection failed" in warnings[0].getMessage()
    clock[0] += 61
    engine_probe.probe(client(down))
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
    engine_probe.probe(
        client(lambda r: httpx.Response(200, json={"status": "ok", "checks": {"database": "ok"}}))
    )
    assert any(r.levelno == logging.INFO and "healthy" in r.getMessage() for r in caplog.records)
    assert all("sekrit-123" not in r.getMessage() for r in caplog.records)

"""The status page's service-key line: one authenticated read (GET /stories) (#43)."""

import time

import httpx
import pytest

from console import engine_status
from console.engine.client import EngineClient
from console.engine_status import check_service_key, status_reads
from tests.fakes.engine import FakeEngine, recorded_schema

URL = "http://engine.test"
OK = {"status": "ok", "checks": {"database": "ok"}}


def envelope(code: str) -> dict[str, object]:
    return {"error": {"code": code, "message": "m", "details": {}}}


def test_accepted(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories", 200, json={"items": []})
    result = check_service_key(EngineClient(URL, "k" * 32, transport=fake_engine.transport))
    assert result.line == "Service key: accepted"
    assert fake_engine.requests[0].headers["authorization"] == f"Bearer {'k' * 32}"


def test_rejected(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories", 401, json=envelope("unauthenticated"))
    result = check_service_key(EngineClient(URL, "wrong", transport=fake_engine.transport))
    assert (result.verdict, result.line) == ("rejected", "Service key: rejected (401)")


def test_unreachable_is_not_checked(fake_engine: FakeEngine) -> None:
    fake_engine.fail("GET", "/stories", httpx.ConnectError("refused"))
    result = check_service_key(EngineClient(URL, "k", transport=fake_engine.transport))
    assert result.line == "Service key: not checked — Engine unreachable"


def test_not_configured_makes_no_call() -> None:
    assert check_service_key(None).line == "Service key: not checked — Engine not configured"


def test_other_error_is_unknown_not_rejected(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories", 503, json=envelope("database_unavailable"))
    result = check_service_key(EngineClient(URL, "k", transport=fake_engine.transport))
    # Complement: only a 401 says the key is wrong.
    assert (result.verdict, result.detail) == ("unknown", "unknown — HTTP 503 database_unavailable")


def test_slow_key_check_times_out_at_the_shared_deadline(
    fake_engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_status, "DEADLINE_S", 0.3)
    fake_engine.respond("GET", "/health", 200, json=OK)
    fake_engine.serve("GET", "/openapi.json", 200, json=recorded_schema())
    fake_engine.respond("GET", "/stories", 200, json={"items": []}, delay=1.0)
    started = time.monotonic()
    probed, checked, key = status_reads(EngineClient(URL, "k", transport=fake_engine.transport))
    assert time.monotonic() - started < 0.8
    assert probed.state == "healthy"
    assert checked.verdict == "matches"
    assert key.line == "Service key: unknown — timeout"

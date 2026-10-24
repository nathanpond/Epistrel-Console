"""engine_api_check against the fake Engine serving /openapi.json (#41)."""

import copy
import logging
import time
from typing import Any

import httpx
import pytest

from console import engine_api_check, engine_probe
from console.engine.client import EngineClient
from console.engine_api_check import ApiCheck, check_engine_api
from tests.fakes.engine import FakeEngine, recorded_schema

URL = "http://engine.test"


@pytest.fixture(autouse=True)
def _fresh_log_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_api_check, "_last_verdict", None)
    monkeypatch.setattr(engine_api_check, "_last_logged", 0.0)
    monkeypatch.setattr(engine_probe, "_last_state", None)


def check(fake: FakeEngine) -> ApiCheck:
    return check_engine_api(EngineClient(URL, "sekrit", transport=fake.transport))


def serving(fake: FakeEngine, doc: object, status: int = 200) -> FakeEngine:
    fake.serve("GET", "/openapi.json", status, json=doc)
    return fake


def test_recorded_schema_matches(fake_engine: FakeEngine) -> None:
    result = check(serving(fake_engine, recorded_schema()))
    assert result == ApiCheck("matches", None, None, "m3")
    assert result.as_json() == {
        "verdict": "matches",
        "summary": None,
        "reason": None,
        "engine_version": "m3",
    }


def test_info_version_alone_still_matches(fake_engine: FakeEngine) -> None:
    doc = recorded_schema()
    doc["info"]["version"] = "m9"
    result = check(serving(fake_engine, doc))
    assert (result.verdict, result.engine_version) == ("matches", "m9")


def test_added_path_differs(fake_engine: FakeEngine) -> None:
    doc: dict[str, Any] = recorded_schema()
    doc["paths"]["/brand-new"] = {"post": {"responses": {}}}
    result = check(serving(fake_engine, doc))
    assert result.verdict == "differs"
    assert result.summary == "1 operation added"
    assert result.changes == ("added: POST /brand-new",)
    assert result.reason is None


def test_component_and_other_changes(fake_engine: FakeEngine) -> None:
    doc: dict[str, Any] = copy.deepcopy(recorded_schema())
    doc["components"]["schemas"]["HealthResponse"]["required"] = ["status"]
    doc["webhooks"] = {}
    result = check(serving(fake_engine, doc))
    assert result.summary == "1 component changed, 1 other change"
    assert result.changes == ("changed: schemas/HealthResponse", "changed: webhooks")


@pytest.mark.parametrize(
    ("doc", "status", "reason"),
    [
        ({"detail": "Not Found"}, 404, "HTTP 404"),
        (None, 302, "HTTP 302"),
        ([1, 2], 200, "bad response"),
        ({"openapi": "3.1.0"}, 200, "bad response"),
        ({"paths": []}, 200, "bad response"),
    ],
)
def test_unusable_answers_are_unknown(
    fake_engine: FakeEngine, doc: object, status: int, reason: str
) -> None:
    result = check(serving(fake_engine, doc, status))
    assert result == ApiCheck("unknown", None, reason, None)


def test_non_json_is_bad_response() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
    result = check_engine_api(EngineClient(URL, None, transport=transport))
    assert (result.verdict, result.reason) == ("unknown", "bad response")


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (httpx.ConnectError("refused"), "connection failed"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ReadError("reset"), "transport error"),
    ],
)
def test_transport_errors_are_unknown(fake_engine: FakeEngine, exc: Exception, reason: str) -> None:
    fake_engine.fail("GET", "/openapi.json", exc)
    assert check(fake_engine) == ApiCheck("unknown", None, reason, None)


def test_not_configured() -> None:
    assert check_engine_api(None) == ApiCheck("unknown", None, "not configured", None)


def test_openapi_is_fetched_once_without_the_key(fake_engine: FakeEngine) -> None:
    check(serving(fake_engine, recorded_schema()))
    (request,) = fake_engine.requests
    assert request.url.path == "/openapi.json"
    assert "authorization" not in request.headers


def test_logs_on_change_only(
    fake_engine: FakeEngine, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    caplog.set_level(logging.INFO, logger="console.engine")
    doc: dict[str, Any] = recorded_schema()
    doc["paths"]["/brand-new"] = {"post": {"responses": {}}}
    serving(fake_engine, doc)
    for _ in range(3):
        check(fake_engine)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "differs: 1 operation added" in warnings[0].getMessage()
    clock[0] += 61
    check(fake_engine)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
    serving(fake_engine, recorded_schema())
    check(fake_engine)
    assert any(r.levelno == logging.INFO and "again" in r.getMessage() for r in caplog.records)


def test_unknown_not_logged_while_probe_says_unreachable(
    fake_engine: FakeEngine, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_probe, "_last_state", "unreachable")
    caplog.set_level(logging.INFO, logger="console.engine")
    fake_engine.fail("GET", "/openapi.json", httpx.ConnectError("refused"))
    check(fake_engine)
    assert caplog.records == []

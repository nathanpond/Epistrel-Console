from collections.abc import Callable
from typing import Any

import httpx
import pytest
from django.test import override_settings

from console import engine_probe
from console.engine.client import EngineClient
from console.engine_probe import probe

URL = "http://engine.test:8000"
OK_BODY: dict[str, Any] = {
    "status": "ok",
    "checks": {
        "database": "ok",
        "workers": {"alive": 1, "last_heartbeat_s": 0.4},
        "oldest_overdue_timer_s": None,
        "oldest_overdue_job_s": None,
        "erasure_pending": 0,
        "ffmpeg": "missing",
        "ffprobe": "missing",
    },
    "content_logging": False,
}

Handler = Callable[[httpx.Request], httpx.Response]


def run(handler: Handler) -> tuple[engine_probe.ProbeResult, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    with override_settings(ENGINE_URL=URL, ENGINE_SERVICE_KEY="sekrit-123"):
        result = probe(EngineClient.from_settings(transport=httpx.MockTransport(record)))
    return result, seen


def test_healthy() -> None:
    result, seen = run(lambda r: httpx.Response(200, json=OK_BODY))
    assert result.state == "healthy"
    assert result.reachable
    assert result.http_status == 200
    assert result.status == "ok"
    assert result.checks == OK_BODY["checks"]
    assert result.reason is None
    assert isinstance(result.latency_ms, int)
    request = seen[0]
    assert str(request.url) == f"{URL}/health"
    assert request.headers["User-Agent"].startswith("epistrel-console/")
    # Complement: the probe never sends the service key, in a header or in the URL.
    assert "authorization" not in request.headers
    assert all("sekrit-123" not in value for value in request.headers.values())
    assert "sekrit-123" not in str(request.url)


def test_database_down_is_unhealthy_with_checks() -> None:
    body = {"status": "unavailable", "checks": {"database": "error", "ffmpeg": "missing"}}
    result, _ = run(lambda r: httpx.Response(503, json=body))
    assert result.state == "unhealthy"
    assert result.reason == "database error"
    assert result.checks == body["checks"]


def test_degraded_on_200_is_unhealthy() -> None:
    body = {"status": "degraded", "checks": {"database": "ok", "workers": {"alive": 0}}}
    result, _ = run(lambda r: httpx.Response(200, json=body))
    assert result.state == "unhealthy"
    assert result.reason == "status degraded"


def test_other_status_is_http_code() -> None:
    result, _ = run(lambda r: httpx.Response(401, json={"status": "x", "checks": {}}))
    assert (result.state, result.reason) == ("unhealthy", "HTTP 401")


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, text="not json"), 200),
        (httpx.Response(200, json=[1, 2]), 200),
        (httpx.Response(200, json={"status": "ok"}), 200),
        (httpx.Response(302, headers={"Location": "/elsewhere"}), 302),
        (httpx.Response(401, text="nope"), 401),
    ],
)
def test_bad_response(response: httpx.Response, code: int) -> None:
    result, _ = run(lambda r: response)
    assert result.state == "unhealthy"
    assert result.reason == f"bad response (HTTP {code})"
    assert result.reachable


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (httpx.ConnectError("refused"), "connection failed"),
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ReadError("reset"), "transport error"),
        (RuntimeError("surprise"), "transport error"),
    ],
)
def test_unreachable(exc: Exception, reason: str) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise exc

    result, _ = run(fail)
    assert result.state == "unreachable"
    assert result.reason == reason
    assert not result.reachable
    assert result.latency_ms is None
    assert result.http_status is None


def test_not_configured() -> None:
    with override_settings(ENGINE_URL=None):
        result = probe()
    assert (result.state, result.reason) == ("unreachable", "not configured")

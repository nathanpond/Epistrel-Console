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


def m2(status: str, **checks: Any) -> dict[str, Any]:
    """An M2 body: OK_BODY's checks with ``checks`` overriding them."""
    return {**OK_BODY, "status": status, "checks": {**OK_BODY["checks"], **checks}}


@pytest.mark.parametrize(
    ("checks", "reason"),
    [
        ({"workers": {"alive": 0, "last_heartbeat_s": None}}, "no worker alive"),
        (
            {"workers": {"alive": 0, "last_heartbeat_s": 75.9}},
            "no worker alive, worker heartbeat 75 s ago",
        ),
        ({"workers": {"alive": 1, "last_heartbeat_s": 75.0}}, "worker heartbeat 75 s ago"),
        ({"workers": {"alive": None, "last_heartbeat_s": None}}, "no worker heartbeat"),
        ({"oldest_overdue_job_s": 400.7}, "job overdue 400 s"),
        ({"oldest_overdue_timer_s": 301.0}, "timer overdue 301 s"),
        ({"erasure_pending": 2}, "erasure pending 2"),
        (
            {
                "workers": {"alive": 0, "last_heartbeat_s": 90.0},
                "oldest_overdue_timer_s": 500.0,
                "oldest_overdue_job_s": 600.0,
                "erasure_pending": 1,
            },
            "no worker alive, worker heartbeat 90 s ago, timer overdue 500 s, job overdue 600 s, "
            "erasure pending 1",
        ),
    ],
)
def test_degraded_on_200_with_reasons(checks: dict[str, Any], reason: str) -> None:
    result, _ = run(lambda r: httpx.Response(200, json=m2("degraded", **checks)))
    assert (result.state, result.reason) == ("degraded", reason)
    assert result.badge == f"Engine: Degraded — {reason}"


@pytest.mark.parametrize(
    "checks",
    [
        {},
        # Complement: at or under the thresholds, and zero erasures, give no reason.
        {"workers": {"alive": 1, "last_heartbeat_s": 60.0}, "oldest_overdue_job_s": 300.0},
        {"erasure_pending": 0, "oldest_overdue_timer_s": None},
    ],
)
def test_degraded_without_a_derivable_reason(checks: dict[str, Any]) -> None:
    result, _ = run(lambda r: httpx.Response(200, json=m2("degraded", **checks)))
    assert (result.state, result.reason) == ("degraded", "status degraded")


def test_ffmpeg_missing_stays_healthy_without_a_reason() -> None:
    result, _ = run(lambda r: httpx.Response(200, json=m2("ok", ffmpeg="missing", ffprobe="ok")))
    assert (result.state, result.reason) == ("healthy", None)
    assert result.badge == "Engine: Healthy"


def test_degraded_on_non_200_is_http_code() -> None:
    result, _ = run(lambda r: httpx.Response(500, json=m2("degraded")))
    assert (result.state, result.reason) == ("unhealthy", "HTTP 500")


def test_m2_unavailable_503_is_unhealthy() -> None:
    body = m2("unavailable", database="error", workers={"alive": 0, "last_heartbeat_s": None})
    result, _ = run(lambda r: httpx.Response(503, json=body))
    # Only string-valued checks give the reason; the nested workers object is skipped.
    assert (result.state, result.reason) == ("unhealthy", "database error")


def test_m2_unavailable_without_failing_string_check() -> None:
    result, _ = run(lambda r: httpx.Response(503, json=m2("unavailable")))
    assert (result.state, result.reason) == ("unhealthy", "status unavailable")


def test_m0_body_still_healthy() -> None:
    # Complement: no regression for the old M0 body (no workers, no content_logging).
    result, _ = run(
        lambda r: httpx.Response(200, json={"status": "ok", "checks": {"database": "ok"}})
    )
    assert (result.state, result.reason, result.content_logging) == ("healthy", None, None)


@pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False), ("yes", None)])
def test_content_logging_is_carried(value: object, expected: bool | None) -> None:
    body = {**OK_BODY, "content_logging": value}
    result, _ = run(lambda r: httpx.Response(200, json=body))
    assert result.content_logging is expected


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

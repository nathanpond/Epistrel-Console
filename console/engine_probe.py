"""The single probe of the Engine's /health, and how its answer is classified.

Healthy: 200 with a valid body and ``status == "ok"``. Unhealthy: the Engine answered but reported
a problem (or answered with something that is not a health body). Unreachable: no HTTP answer.
The probe never sends the service key and never raises, so /health cannot 500 because of the
Engine.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from django.conf import settings

from console.version import VERSION

log = logging.getLogger(__name__)

# 3 s, not 2: the Engine's own database check takes up to 2 s, so a 2 s limit would turn
# "Engine database down" into "Engine unreachable".
TIMEOUT_S = 3.0
USER_AGENT = f"epistrel-console/{VERSION}"
REPEAT_LOG_S = 60.0
# Check values that do not mark a check as failing ("missing" is ffmpeg/ffprobe, which the Engine
# says never changes its status).
PASSING_VALUES = frozenset({"ok", "skipped", "missing"})

State = Literal["healthy", "unhealthy", "unreachable"]


@dataclass(frozen=True)
class ProbeResult:
    state: State
    reachable: bool
    http_status: int | None
    status: str | None
    checks: dict[str, Any] | None
    reason: str | None
    latency_ms: int | None


def _valid_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    if not isinstance(body.get("status"), str) or not isinstance(body.get("checks"), dict):
        return None
    return body


def _failing_reason(status: str, checks: dict[str, Any]) -> str:
    failing = [
        f"{name} {value}"
        for name, value in checks.items()
        if isinstance(value, str) and value not in PASSING_VALUES
    ]
    return ", ".join(failing) if failing else f"status {status}"


def classify(response: httpx.Response, latency_ms: int) -> ProbeResult:
    code = response.status_code
    body = _valid_body(response)
    if body is None:
        return ProbeResult(
            "unhealthy", True, code, None, None, f"bad response (HTTP {code})", latency_ms
        )
    status: str = body["status"]
    checks: dict[str, Any] = body["checks"]
    if code == 200 and status == "ok":
        return ProbeResult("healthy", True, code, status, checks, None, latency_ms)
    reason = _failing_reason(status, checks) if code in (200, 503) else f"HTTP {code}"
    return ProbeResult("unhealthy", True, code, status, checks, reason, latency_ms)


def _unreachable(reason: str) -> ProbeResult:
    return ProbeResult("unreachable", False, None, None, None, reason, None)


def probe(transport: httpx.BaseTransport | None = None) -> ProbeResult:
    """GET <ENGINE_URL>/health once and classify the answer."""
    url: str | None = settings.ENGINE_URL
    if url is None:
        result = _unreachable("not configured")
        _log(url, result)
        return result
    started = time.monotonic()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(TIMEOUT_S),
            follow_redirects=False,
            verify=True,
            trust_env=False,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
        ) as client:
            response = client.get(f"{url}/health")
        latency_ms = round((time.monotonic() - started) * 1000)
        result = classify(response, latency_ms)
    except httpx.TimeoutException:
        result = _unreachable("timeout")
    except httpx.ConnectError:
        result = _unreachable("connection failed")
    except httpx.TransportError:
        result = _unreachable("transport error")
    except Exception:
        log.exception("unexpected error probing the Engine at %s", url)
        result = _unreachable("transport error")
    _log(url, result)
    return result


_last_state: State | None = None
_last_logged = 0.0


def _log(url: str | None, result: ProbeResult) -> None:
    """One line per state change, plus at most one repeat per minute while a problem lasts."""
    global _last_state, _last_logged
    now = time.monotonic()
    changed = result.state != _last_state
    if result.state == "healthy":
        if changed and _last_state is not None:
            log.info("Engine at %s is healthy again", url)
    elif changed or now - _last_logged >= REPEAT_LOG_S:
        log.warning("Engine at %s is %s: %s", url, result.state, result.reason)
    else:
        _last_state = result.state
        return
    _last_state = result.state
    _last_logged = now

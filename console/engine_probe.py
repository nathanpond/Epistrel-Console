"""The single probe of the Engine's /health, and how its answer is classified.

Healthy: 200 with a valid body and ``status == "ok"``. Degraded: 200 with ``status == "degraded"``
(workers late, timers or jobs overdue, erasures pending), with the reasons derived from the checks.
Unhealthy: the Engine answered but reported a problem (or answered with something that is not a
health body). Unreachable: no HTTP answer. The state is the Engine's own ``status``; the checks
only produce the reason text, so the Console never overrides the Engine.
The probe goes through the Engine client, never sends the service key (``/health`` is an exempt
route) and never raises, so /health cannot 500 because of the Engine.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from console.engine.client import (
    EngineClient,
    EngineNotConfigured,
    EngineUnreachable,
)
from console.engine.models import HealthResult
from console.engine_access import engine_client_unauthenticated

log = logging.getLogger(__name__)

REPEAT_LOG_S = 60.0
# Check values that do not mark a check as failing ("missing" is ffmpeg/ffprobe, which the Engine
# says never changes its status).
PASSING_VALUES = frozenset({"ok", "skipped", "missing"})

# Mirrors of the Engine's defaults; they only shape reason text (the state is the Engine's).
HEARTBEAT_LATE_S = 60
OVERDUE_ALERT_S = 300

State = Literal["healthy", "degraded", "unhealthy", "unreachable"]
STATE_WORDS: dict[str, str] = {
    "healthy": "Healthy",
    "degraded": "Degraded",
    "unhealthy": "Unhealthy",
    "unreachable": "Unreachable",
}


@dataclass(frozen=True)
class ProbeResult:
    state: State
    reachable: bool
    http_status: int | None
    status: str | None
    checks: dict[str, Any] | None
    reason: str | None
    latency_ms: int | None
    content_logging: bool | None = None

    @property
    def state_word(self) -> str:
        return STATE_WORDS[self.state]

    @property
    def badge(self) -> str:
        """``Engine: Healthy`` or ``Engine: <State> — <reason>``."""
        text = f"Engine: {self.state_word}"
        return f"{text} — {self.reason}" if self.reason else text


def _valid_body(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("status"), str) or not isinstance(raw.get("checks"), dict):
        return None
    return raw


def _failing_reason(status: str, checks: dict[str, Any]) -> str:
    failing = [
        f"{name} {value}"
        for name, value in checks.items()
        if isinstance(value, str) and value not in PASSING_VALUES
    ]
    return ", ".join(failing) if failing else f"status {status}"


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def degraded_reasons(checks: dict[str, Any]) -> list[str]:
    """Why a ``degraded`` Engine is degraded, in a fixed order (seconds floored to int)."""
    reasons: list[str] = []
    workers = checks.get("workers")
    if isinstance(workers, dict):
        alive = workers.get("alive")
        heartbeat = _number(workers.get("last_heartbeat_s"))
        if isinstance(alive, int) and not isinstance(alive, bool) and alive == 0:
            reasons.append("no worker alive")
        elif heartbeat is None:
            reasons.append("no worker heartbeat")
        if heartbeat is not None and heartbeat > HEARTBEAT_LATE_S:
            reasons.append(f"worker heartbeat {int(heartbeat)} s ago")
    for key, label in (("oldest_overdue_timer_s", "timer"), ("oldest_overdue_job_s", "job")):
        overdue = _number(checks.get(key))
        if overdue is not None and overdue > OVERDUE_ALERT_S:
            reasons.append(f"{label} overdue {int(overdue)} s")
    pending = checks.get("erasure_pending")
    if isinstance(pending, int) and not isinstance(pending, bool) and pending > 0:
        reasons.append(f"erasure pending {pending}")
    return reasons


def classify(health: HealthResult) -> ProbeResult:
    code, latency_ms = health.http_status, health.latency_ms
    body = _valid_body(health.raw)
    if body is None:
        return ProbeResult(
            "unhealthy", True, code, None, None, f"bad response (HTTP {code})", latency_ms
        )
    status: str = body["status"]
    checks: dict[str, Any] = body["checks"]
    logging_on = body.get("content_logging")
    content_logging = logging_on if isinstance(logging_on, bool) else None
    if code == 200 and status == "ok":
        return ProbeResult("healthy", True, code, status, checks, None, latency_ms, content_logging)
    if code == 200 and status == "degraded":
        reason = ", ".join(degraded_reasons(checks)) or f"status {status}"
        return ProbeResult(
            "degraded", True, code, status, checks, reason, latency_ms, content_logging
        )
    reason = _failing_reason(status, checks) if code in (200, 503) else f"HTTP {code}"
    return ProbeResult("unhealthy", True, code, status, checks, reason, latency_ms, content_logging)


def _unreachable(reason: str) -> ProbeResult:
    return ProbeResult("unreachable", False, None, None, None, reason, None)


def probe(client: EngineClient | None = None) -> ProbeResult:
    """GET <ENGINE_URL>/health once, through the Engine client, and classify the answer.

    ``client`` defaults to one built from settings; tests pass one on a fake transport.
    """
    if client is None:
        try:
            client = engine_client_unauthenticated()
        except EngineNotConfigured:
            result = _unreachable("not configured")
            _log(None, result)
            return result
    try:
        result = classify(client.health())
    except EngineUnreachable as exc:
        if exc.unexpected:
            log.warning(
                "unexpected error probing the Engine at %s", client.base_url, exc_info=exc.__cause__
            )
        result = _unreachable(exc.reason)
    except AssertionError:
        raise  # the fake Engine's signals reach the test
    except Exception:
        log.exception("unexpected error probing the Engine at %s", client.base_url)
        result = _unreachable("transport error")
    _log(client.base_url, result)
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


def last_state() -> State | None:
    """The state the probe last saw (None before the first probe)."""
    return _last_state

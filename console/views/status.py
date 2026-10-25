"""``/`` (the status page, signed in) and ``/health`` (JSON, public for Docker's healthcheck)."""

from dataclasses import asdict
from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from console.context_processors import remember_probe
from console.engine.client import EngineClient, EngineNotConfigured
from console.engine_access import engine_client_unauthenticated
from console.engine_api_check import ApiCheck
from console.engine_probe import ProbeResult, erasure_pending
from console.engine_status import KeyCheck, probe_engine, status_reads
from console.version import VERSION

# The status page lists at most this many API differences, then "…and N more".
MAX_CHANGES = 20
EMPTY = "—"


def _client() -> EngineClient | None:
    """One client for every question; ``None`` when no Engine URL is configured."""
    try:
        return engine_client_unauthenticated()
    except EngineNotConfigured:
        return None


def engine_state() -> tuple[ProbeResult, ApiCheck]:
    """``/health``'s two questions (no key check)."""
    return probe_engine(_client())


def status_state() -> tuple[ProbeResult, ApiCheck, KeyCheck]:
    """The status page's three reads."""
    return status_reads(_client())


def _seconds(value: object) -> int | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(value)
    return None


def health_rows(checks: dict[str, Any] | None) -> list[tuple[str, str]]:
    """The M2 health checks as ``(label, text)`` rows; nothing for a body without them."""
    if not checks:
        return []
    rows: list[tuple[str, str]] = []
    if "database" in checks:
        rows.append(("Database", str(checks["database"])))
    workers = checks.get("workers")
    if isinstance(workers, dict):
        alive = workers.get("alive")
        ok_alive = isinstance(alive, int) and not isinstance(alive, bool)
        rows.append(("Workers alive", str(alive) if ok_alive else EMPTY))
        heartbeat = _seconds(workers.get("last_heartbeat_s"))
        rows.append(("Last heartbeat", "never" if heartbeat is None else f"{heartbeat} s ago"))
    for key, label in (
        ("oldest_overdue_timer_s", "Oldest overdue timer"),
        ("oldest_overdue_job_s", "Oldest overdue job"),
    ):
        if key in checks:
            overdue = _seconds(checks[key])
            rows.append((label, "none" if overdue is None else f"{overdue} s"))
    return rows


def erasure_line(checks: dict[str, Any] | None) -> tuple[str, str] | None:
    """``("Erasure pending: N" or "Erasure pending: none", the ledger's URL)`` for a health body
    with checks (#87); ``None`` without them."""
    if not checks:
        return None
    pending = erasure_pending(checks)
    try:
        url = reverse("erasure:index")
    except NoReverseMatch:
        url = ""
    return f"Erasure pending: {pending if pending else 'none'}", url


@require_safe
@never_cache
def status(request: HttpRequest) -> HttpResponse:
    result, api, key = status_state()
    remember_probe(request, result)  # the shell's badge renders from this same probe
    context = {
        "version": VERSION,
        "engine_url": settings.ENGINE_URL,
        "result": result,
        "state_word": result.state_word,
        "health_rows": health_rows(result.checks),
        "erasure_line": erasure_line(result.checks),
        "key_set": settings.ENGINE_SERVICE_KEY is not None,
        "key_check": key,
        "api": api,
        "api_changes": api.changes[:MAX_CHANGES],
        "api_more": max(0, len(api.changes) - MAX_CHANGES),
    }
    return render(request, "console/status.html", context)


@login_not_required
@require_safe
@never_cache
def health(request: HttpRequest) -> JsonResponse:
    # Always 200: the Console is the tool you use to find out why the Engine is down, and an API
    # mismatch is informational.
    result, api = engine_state()
    engine = {"url": settings.ENGINE_URL, **asdict(result), "api": api.as_json()}
    return JsonResponse({"status": "ok", "version": VERSION, "engine": engine})

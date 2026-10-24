from dataclasses import asdict

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from console.engine.client import EngineClient, EngineNotConfigured
from console.engine_api_check import ApiCheck
from console.engine_probe import ProbeResult
from console.engine_status import probe_engine
from console.version import VERSION

STATE_WORDS = {"healthy": "Healthy", "unhealthy": "Unhealthy", "unreachable": "Unreachable"}
# The status page lists at most this many API differences, then "…and N more".
MAX_CHANGES = 20


def engine_state() -> tuple[ProbeResult, ApiCheck]:
    """One client for both questions; ``None`` when no Engine URL is configured."""
    try:
        client: EngineClient | None = EngineClient.from_settings()
    except EngineNotConfigured:
        client = None
    return probe_engine(client)


@require_safe
@never_cache
def status(request: HttpRequest) -> HttpResponse:
    result, api = engine_state()
    context = {
        "version": VERSION,
        "engine_url": settings.ENGINE_URL,
        "result": result,
        "state_word": STATE_WORDS[result.state],
        "key_set": settings.ENGINE_SERVICE_KEY is not None,
        "api": api,
        "api_changes": api.changes[:MAX_CHANGES],
        "api_more": max(0, len(api.changes) - MAX_CHANGES),
    }
    return render(request, "console/status.html", context)


@require_safe
@never_cache
def health(request: HttpRequest) -> JsonResponse:
    # Always 200: the Console is the tool you use to find out why the Engine is down, and an API
    # mismatch is informational.
    result, api = engine_state()
    engine = {"url": settings.ENGINE_URL, **asdict(result), "api": api.as_json()}
    return JsonResponse({"status": "ok", "version": VERSION, "engine": engine})

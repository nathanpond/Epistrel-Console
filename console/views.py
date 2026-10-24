from dataclasses import asdict

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from console.engine_probe import probe
from console.version import VERSION

STATE_WORDS = {"healthy": "Healthy", "unhealthy": "Unhealthy", "unreachable": "Unreachable"}


@require_safe
@never_cache
def status(request: HttpRequest) -> HttpResponse:
    result = probe()
    context = {
        "version": VERSION,
        "engine_url": settings.ENGINE_URL,
        "result": result,
        "state_word": STATE_WORDS[result.state],
        "key_set": settings.ENGINE_SERVICE_KEY is not None,
    }
    return render(request, "console/status.html", context)


@require_safe
@never_cache
def health(request: HttpRequest) -> JsonResponse:
    # Always 200: the Console is the tool you use to find out why the Engine is down.
    result = probe()
    engine = {"url": settings.ENGINE_URL, **asdict(result)}
    return JsonResponse({"status": "ok", "version": VERSION, "engine": engine})

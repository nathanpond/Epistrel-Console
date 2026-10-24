"""Test-only URLconf: views that let an Engine client error escape (the middleware catches it)."""

from django.http import HttpRequest, HttpResponse
from django.urls import path

from console.engine.client import (
    EngineError,
    EngineNotConfigured,
    EngineUnreachable,
)
from epistrel_console.urls import urlpatterns as real_urlpatterns

CONFLICT = EngineError(
    "revision_conflict",
    "The timeline moved on",
    {
        "expected_revision": 3,
        "actual_revision": 4,
        "missing": None,
        "errors": [{"loc": ["body", "name"], "msg": "too long"}],
        "api_key": "sk-should-not-show",
    },
    409,
)


def conflict(request: HttpRequest) -> HttpResponse:
    raise CONFLICT


def unreachable(request: HttpRequest) -> HttpResponse:
    raise EngineUnreachable("timeout")


def not_configured(request: HttpRequest) -> HttpResponse:
    raise EngineNotConfigured("no Engine URL is configured")


def empty_details(request: HttpRequest) -> HttpResponse:
    raise EngineError("not_found", "No such story", {}, 404)


def plain_bug(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("not an Engine error")


urlpatterns = [
    path("_test/conflict/", conflict),
    path("_test/unreachable/", unreachable),
    path("_test/not-configured/", not_configured),
    path("_test/empty/", empty_details),
    path("_test/bug/", plain_bug),
    *real_urlpatterns,
]

"""The shared Engine error panel (#43): what an operator sees instead of a 500.

``render_engine_error(request, exc)`` is what :class:`console.middleware.EngineErrorMiddleware`
returns for any uncaught :class:`EngineClientError`, and what a view that degrades per section
can call itself. A full page in the shell for a normal request; only the panel fragment for an
HTMX request (``HX-Request: true``), swapped into the request's target. Always HTTP 200.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from console.engine.client import (
    EngineClientError,
    EngineError,
    EngineNotConfigured,
    EngineUnreachable,
)
from console.redaction import MASK, is_redacted, redact

log = logging.getLogger("console.engine")

EMPTY = "—"
PANEL_TEMPLATE = "console/_engine_error.html"
PAGE_TEMPLATE = "console/engine_error_page.html"


@dataclass(frozen=True)
class Detail:
    key: str
    text: str
    block: bool  # a dict or list, shown as indented JSON in <pre>


@dataclass(frozen=True)
class ErrorPanel:
    """What the panel shows: ``headline`` alone, or an Engine error's status, code and message."""

    headline: str
    http_status: int | None = None
    code: str | None = None
    message: str | None = None
    details: tuple[Detail, ...] = ()
    details_raw: str | None = None  # non-dict details (never sent by the Engine), as JSON


def _details(details: object) -> tuple[tuple[Detail, ...], str | None]:
    if not details:
        return (), None
    if not isinstance(details, dict):
        return (), json.dumps(redact(details), indent=2, ensure_ascii=False, default=str)
    rows = []
    for key, value in details.items():
        if is_redacted(key):
            rows.append(Detail(str(key), MASK, False))
        elif isinstance(value, dict | list):
            text = json.dumps(redact(value), indent=2, ensure_ascii=False, default=str)
            rows.append(Detail(str(key), text, True))
        else:
            rows.append(Detail(str(key), EMPTY if value is None else str(value), False))
    return tuple(rows), None


def panel_for(exc: EngineClientError) -> ErrorPanel:
    if isinstance(exc, EngineError):
        rows, raw = _details(exc.details)
        return ErrorPanel(
            f"Engine error {exc.http_status} {exc.code}",
            exc.http_status,
            exc.code,
            exc.message,
            rows,
            raw,
        )
    if isinstance(exc, EngineUnreachable):
        return ErrorPanel(f"Engine unreachable — {exc.reason}")
    if isinstance(exc, EngineNotConfigured):
        return ErrorPanel("Engine not configured — CONSOLE_ENGINE_URL is unset")
    return ErrorPanel(f"Engine client error — {exc}")


def is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def log_engine_error(request: HttpRequest, exc: EngineClientError) -> None:
    """One WARNING line, without details: ``engine error <status> <code> <method> <path>``."""
    if isinstance(exc, EngineError):
        status, code = str(exc.http_status), exc.code
    elif isinstance(exc, EngineUnreachable):
        status, code = "-", f"unreachable ({exc.reason})"
    elif isinstance(exc, EngineNotConfigured):
        status, code = "-", "not_configured"
    else:
        status, code = "-", type(exc).__name__
    log.warning("engine error %s %s %s %s", status, code, request.method, request.path)


def render_engine_error(request: HttpRequest, exc: EngineClientError) -> HttpResponse:
    log_engine_error(request, exc)
    context: dict[str, Any] = {"engine_error": panel_for(exc)}
    template = PANEL_TEMPLATE if is_htmx(request) else PAGE_TEMPLATE
    return render(request, template, context, status=200)

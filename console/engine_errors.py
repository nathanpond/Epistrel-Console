"""The shared Engine error panel (#43): what an operator sees instead of a 500.

``render_engine_error(request, exc)`` is what :class:`console.middleware.EngineErrorMiddleware`
returns for any uncaught :class:`EngineClientError`, and what a view that degrades per section
can call itself. A full page in the shell for a normal request; only the panel fragment for an
HTMX request (``HX-Request: true``), swapped into the request's target. Always HTTP 200.

#150 adds the configuration-error kind: an embedding mismatch (the codes
``embedding_dimension_mismatch``/``embedding_version_mismatch``, which no route declares today,
or the error classes a failed re-embed job reports) reads as a configuration problem with a link
to fix it, not as an Engine fault. It also adds :func:`degradation_message` for a retrieval
answer's ``embedding_pending`` degradation (#152 is its first caller).
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

# #150: an embedding mismatch is a configuration error, fixed on the embedding edit page.
CONFIG_HEADLINE = "Configuration error"
MISMATCH_CODES = frozenset({"embedding_dimension_mismatch", "embedding_version_mismatch"})
MISMATCH_CLASSES = frozenset({"EmbeddingDimensionMismatch", "EmbeddingVersionMismatch"})
MISMATCH_TEXT = (
    "Embedding configuration mismatch: the deployment's pinned embedding model doesn't match "
    "the configured one."
)
MISMATCH_LINK = "Fix it on the embedding edit page"
EMBEDDING_PENDING = "Re-embedding — this story's search is lexical-only until it finishes"


@dataclass(frozen=True)
class Detail:
    key: str
    text: str
    block: bool  # a dict or list, shown as indented JSON in <pre>


@dataclass(frozen=True)
class Fix:
    """A configuration error's fix line: ``text``, then ``link`` pointing at ``url``."""

    text: str
    link: str
    url: str


@dataclass(frozen=True)
class ErrorPanel:
    """What the panel shows: ``headline`` alone, or an Engine error's status, code and message.
    A ``kind="config"`` panel is headed "Configuration error" and adds its ``fix`` line."""

    headline: str
    http_status: int | None = None
    code: str | None = None
    message: str | None = None
    details: tuple[Detail, ...] = ()
    details_raw: str | None = None  # non-dict details (never sent by the Engine), as JSON
    kind: str = "engine"  # or "config" (#150)
    fix: Fix | None = None


@dataclass(frozen=True)
class Degradation:
    """A retrieval degradation's operator text and the page that explains it."""

    text: str
    url: str


def mismatch_fix() -> Fix:
    """The embedding mismatch's fix line, linking the embedding edit page."""
    from django.urls import reverse

    return Fix(MISMATCH_TEXT, MISMATCH_LINK, reverse("models:edit", args=["embedding"]))


def error_class_fix(error_class: str | None) -> Fix | None:
    """The fix line for a failed job's exception class, when it is an embedding mismatch."""
    return mismatch_fix() if error_class in MISMATCH_CLASSES else None


def degradation_message(cause: str) -> Degradation | None:
    """``embedding_pending``'s text and the re-embed run page (its top: the story's row may be
    on another page); ``None`` for any other cause (the caller humanises those itself)."""
    if cause != "embedding_pending":
        return None
    from django.urls import reverse

    return Degradation(EMBEDDING_PENDING, reverse("models:reembed"))


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
        if exc.code in MISMATCH_CODES:
            return ErrorPanel(
                CONFIG_HEADLINE,
                exc.http_status,
                exc.code,
                exc.message,
                rows,
                raw,
                kind="config",
                fix=mismatch_fix(),
            )
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

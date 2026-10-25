"""The media proxy (#65, to #70's spec; M3 convention 11): ``/media/<id>/content/``.

Every image and video the Console shows comes through here, so the browser never sees the
Engine or its service credential. The view streams ``GET /media/{id}/content`` (the client's
:meth:`~console.engine.client.EngineClient.open_media_content`, the only consumer), forwarding
``Range``, ``If-None-Match`` and ``If-Range`` and nothing else (``exp``/``sig`` and every other
query parameter are dropped). It answers the Engine's status (200, 206, 304, 416 …) with only
Content-Type, Content-Length, Content-Range, ETag and Accept-Ranges of its headers, plus:

- ``Cache-Control: private, max-age=31536000, immutable`` (an id's bytes never change; private
  because they sit behind login);
- on every answer, ``X-Content-Type-Options: nosniff`` and a sandbox CSP, so a stored SVG or HTML
  asset cannot run script on the Console's origin.

A 304 carries ETag and Cache-Control only; a 416 keeps Content-Range.

Errors: with ``?img=1`` (exactly ``1``, what ``<img>`` tags use) an Engine error answers its
status with a small SVG naming the media id and ``<status> <code>``; without it the Engine's
body passes through unchanged. No answer: unreachable 502, timeout 504, not configured 503 (the
SVG or ``text/plain`` saying so). Each is logged at WARNING to ``console.media_proxy``.

GET only (anything else is 405). Login is required (the site-wide middleware). Not recorded in
the operation log (a read).
"""

import logging
from collections.abc import Iterator
from contextlib import ExitStack

from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.utils.html import escape
from django.views.decorators.http import require_GET

from console.engine.client import EngineNotConfigured, EngineUnreachable, StreamedContent
from console.engine_access import engine_client_for

log = logging.getLogger("console.media_proxy")

CACHE_CONTROL = "private, max-age=31536000, immutable"
CSP = (
    "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"
)
NOT_MODIFIED_HEADERS = ("ETag",)
UNREACHABLE = "Engine unreachable"
TIMEOUT = "Engine timeout"
NOT_CONFIGURED = "Engine not configured"
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180" '
    'role="img"><title>{title}</title><rect width="320" height="180" fill="#eeeeee"/>'
    '<g font-family="monospace" font-size="12" fill="#333333">'
    '<text x="12" y="72">Image unavailable</text>'
    '<text x="12" y="94">{media_id}</text>'
    '<text x="12" y="116">{reason}</text></g></svg>'
)


def error_svg(media_id: str, reason: str) -> str:
    """The 320 by 180 placeholder: "Image unavailable", the id and ``reason``, all escaped."""
    title = f"Image unavailable — {media_id} — {reason}"
    return SVG.format(title=escape(title), media_id=escape(media_id), reason=escape(reason))


def _secured(response: HttpResponse | StreamingHttpResponse) -> None:
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = CSP


def _failure(media_id: str, status: int, reason: str, as_image: bool) -> HttpResponse:
    """An answer with no Engine status behind it (502/503/504)."""
    log.warning("media %s: %s %s", media_id, status, reason)
    if as_image:
        response = HttpResponse(
            error_svg(media_id, reason), status=status, content_type="image/svg+xml"
        )
    else:
        response = HttpResponse(reason, status=status, content_type="text/plain; charset=utf-8")
    _secured(response)
    return response


def _unreachable(media_id: str, exc: EngineUnreachable, as_image: bool) -> HttpResponse:
    if exc.reason == "timeout":
        return _failure(media_id, 504, TIMEOUT, as_image)
    return _failure(media_id, 502, UNREACHABLE, as_image)


class _Body:
    """The streamed body: the Engine's chunks; the Engine response closes when the last chunk
    is sent or Django closes the response, whichever comes first. A stream cut short after the
    status went out can only end early (logged)."""

    def __init__(self, content: StreamedContent, stack: ExitStack, media_id: str) -> None:
        self._content = content
        self._stack = stack
        self._media_id = media_id

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self._content.iter_bytes()
        except EngineUnreachable as exc:
            log.warning("media %s: stream cut short (%s)", self._media_id, exc.reason)
        finally:
            self.close()

    def close(self) -> None:
        self._stack.close()


def _engine_error(
    media_id: str, content: StreamedContent, stack: ExitStack, as_image: bool
) -> HttpResponse:
    with stack:
        error = content.error()
        body = content.body
    log.warning("media %s: %s %s", media_id, content.status, error.code)
    if as_image:
        reason = f"{content.status} {error.code}"
        response = HttpResponse(
            error_svg(media_id, reason), status=content.status, content_type="image/svg+xml"
        )
    else:
        content_type = content.headers.get("Content-Type", "application/json")
        response = HttpResponse(body, status=content.status, content_type=content_type)
    if "Content-Range" in content.headers:
        response["Content-Range"] = content.headers["Content-Range"]
    _secured(response)
    return response


@require_GET
def media_content(request: HttpRequest, media_id: str) -> HttpResponse | StreamingHttpResponse:
    as_image = request.GET.get("img") == "1"
    try:
        client = engine_client_for(request)
    except EngineNotConfigured:
        return _failure(media_id, 503, NOT_CONFIGURED, as_image)
    stack = ExitStack()
    try:
        content = stack.enter_context(client.open_media_content(media_id, request.headers))
    except EngineUnreachable as exc:
        stack.close()
        return _unreachable(media_id, exc, as_image)
    try:
        if content.status >= 400:
            return _engine_error(media_id, content, stack, as_image)
    except EngineUnreachable as exc:
        return _unreachable(media_id, exc, as_image)
    if content.status == 304:
        stack.close()
        not_modified = HttpResponse(status=304)
        del not_modified["Content-Type"]
        for name in NOT_MODIFIED_HEADERS:
            if name in content.headers:
                not_modified[name] = content.headers[name]
        not_modified["Cache-Control"] = CACHE_CONTROL
        _secured(not_modified)
        return not_modified
    response = StreamingHttpResponse(_Body(content, stack, media_id), status=content.status)
    for name, value in content.headers.items():
        response[name] = value
    if "Content-Type" not in content.headers:
        del response["Content-Type"]
    response["Cache-Control"] = CACHE_CONTROL
    _secured(response)
    return response

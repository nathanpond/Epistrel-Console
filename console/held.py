"""Held records (#64 convention 16, built by #87): a story whose erasure is pending
re-application after a restore is ``blocked_by_erasure``, and the Engine answers every read
and write of it with 503 ``erasure_pending`` until the ledger is re-applied.

:class:`console.middleware.HeldStoryMiddleware` turns that answer, from any view whose URL has a
``story_id``, into :func:`render_held`: the held message in the story shell with only the story
id, a manual "Retry" and Delete (status 503 with the Engine's ``Retry-After``). An HTMX request
is told to load the story's page instead (``HX-Redirect``), so a polling partial stops.
"""

from typing import TypeGuard
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse

from console.engine.client import EngineClientError, EngineError
from console.engine_errors import is_htmx, log_engine_error

HELD_STATUS = "blocked_by_erasure"  # the story's status while held
ERASURE_PENDING = "erasure_pending"  # the 503's code
HELD_TEXT = "Held until the erasure ledger is re-applied"
HELD_PARAM = "held"  # ``?held=1`` on the delete confirm: the id-only wording
TEMPLATE = "console/stories/held.html"
FRAGMENT_TEMPLATE = "console/stories/_held.html"  # the message and actions alone (#139)


def is_held(exc: object) -> TypeGuard[EngineError]:
    return isinstance(exc, EngineError) and exc.http_status == 503 and exc.code == ERASURE_PENDING


def retry_seconds(retry_after: str | None) -> int | None:
    """``Retry-After`` as whole seconds, or ``None`` when it is absent or not an integer."""
    value = (retry_after or "").strip()
    return int(value) if value.isascii() and value.isdigit() else None


def held_text(retry_after: str | None) -> str:
    """ "Held until the erasure ledger is re-applied — retry in N s", without the retry part
    when ``Retry-After`` is not a usable integer."""
    seconds = retry_seconds(retry_after)
    return HELD_TEXT if seconds is None else f"{HELD_TEXT} — retry in {seconds} s"


def delete_url(story_id: str) -> str:
    """The delete confirm with the id-only wording, returning to the stories list."""
    query = urlencode({HELD_PARAM: "1", "next": reverse("stories:list")})
    return f"{reverse('stories:delete', args=[story_id])}?{query}"


def _ledger_url() -> str:
    try:
        return reverse("erasure:index")
    except NoReverseMatch:
        return ""


def held_context(story_id: str, retry_after: str | None, retry_url: str) -> dict[str, str]:
    """What ``_held.html`` shows: the held text, Retry (``retry_url``), Delete and the ledger
    link."""
    return {
        "story_id": story_id,
        "held_text": held_text(retry_after),
        "retry_url": retry_url,
        "delete_url": delete_url(story_id),
        "ledger_url": _ledger_url(),
    }


def render_held(request: HttpRequest, story_id: str, exc: EngineClientError) -> HttpResponse:
    log_engine_error(request, exc)
    retry_after = exc.retry_after if isinstance(exc, EngineError) else None
    detail = reverse("stories:detail", args=[story_id])
    if is_htmx(request):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = detail
        return response
    retry_url = request.get_full_path() if request.method in ("GET", "HEAD") else detail
    context = held_context(story_id, retry_after, retry_url)
    response = render(request, TEMPLATE, context, status=503)
    if retry_seconds(retry_after) is not None:
        response["Retry-After"] = str(retry_seconds(retry_after))
    return response

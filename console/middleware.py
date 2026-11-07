"""Console middleware."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from console.engine.client import EngineClientError
from console.engine_errors import render_engine_error
from console.held import is_held, render_held
from console.portability import is_importing, render_importing


class EngineErrorMiddleware:
    """Any :class:`EngineClientError` a view lets escape renders the shared error panel (200),
    never a 500 page (#43)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        if isinstance(exception, EngineClientError):
            return render_engine_error(request, exception)
        return None


class HeldStoryMiddleware:
    """A 503 ``erasure_pending`` from any view whose URL has a ``story_id`` renders the held
    message (#87, :mod:`console.held`) instead of the error panel. Listed after
    :class:`EngineErrorMiddleware`, so it sees the exception first."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        match = request.resolver_match
        story_id = match.kwargs.get("story_id") if match is not None else None
        if isinstance(story_id, str) and story_id and is_held(exception):
            return render_held(request, story_id, exception)
        return None


class ImportingStoryMiddleware:
    """A 409 ``story_importing`` from any view whose URL has a ``story_id`` renders the importing
    page (E10, #295, :func:`console.portability.render_importing`; status 409) instead of the
    error panel; an HTMX request gets ``HX-Redirect`` to the story page. Mirrors
    :class:`HeldStoryMiddleware` and is listed right after it."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> HttpResponse | None:
        match = request.resolver_match
        story_id = match.kwargs.get("story_id") if match is not None else None
        if isinstance(story_id, str) and story_id and is_importing(exception):
            return render_importing(request, story_id, exception)
        return None

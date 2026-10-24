"""Console middleware."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from console.engine.client import EngineClientError
from console.engine_errors import render_engine_error


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

"""Stand-ins for the M2 sections not built yet, so the nav resolves (#43). Each owning story
replaces its placeholder with the real view."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe


def placeholder(
    title: str, links: tuple[tuple[str, str], ...] = ()
) -> Callable[[HttpRequest], HttpResponse]:
    """``links``: ``(label, url)`` pairs to sections that are built (e.g. Guardrails →
    Policies while Ratings is still a placeholder)."""

    @require_safe
    def view(request: HttpRequest) -> HttpResponse:
        return render(request, "console/placeholder.html", {"title": title, "links": links})

    view.__name__ = f"placeholder_{title.lower().replace(' ', '_')}"
    return view

"""The Console's routes with only one Knowledge section, ``inspect:exposures``, routed to a stub
(#189): the Knowledge landing's redirect, the sub-navigation and a section URL's zero-call gate are
tested before #190/#192/#193 land, and keep working after (their real section routes are dropped).

``knowledge/probe/`` (``inspect:knowledge-probe``, not a section) renders the Knowledge tab with
one ``GET /stories/{id}/inspect/exposures`` read, for a section read's 409 ``module_off``."""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, URLResolver, include, path

from console.inspect import KNOWLEDGE_SECTIONS, Tab
from console.views.inspect import InspectContext, inspect_page
from epistrel_console import urls as real

MOUNT = "stories/<str:story_id>/inspect/"
KNOWLEDGE_TEMPLATE = "console/inspect/knowledge.html"
SECTION_NAMES = {name for name, _ in KNOWLEDGE_SECTIONS}


def without_sections() -> list[Any]:
    """The real Inspect patterns minus every Knowledge section route."""
    return [p for p in real.inspect_patterns[0] if getattr(p, "name", None) not in SECTION_NAMES]


def mounted(patterns: list[Any]) -> list[URLPattern | URLResolver]:
    """The Console's urlpatterns with ``patterns`` as the Inspect area."""
    return [
        path(MOUNT, include((patterns, "inspect"))),
        *(
            p
            for p in real.urlpatterns
            if isinstance(p, URLPattern | URLResolver) and str(p.pattern) != MOUNT
        ),
    ]


def exposures(request: HttpRequest, story_id: str) -> HttpResponse:
    return inspect_page(request, story_id, Tab.KNOWLEDGE, KNOWLEDGE_TEMPLATE, lambda ctx: {})


def _probe(ctx: InspectContext) -> dict[str, Any]:
    ctx.client.inspect_exposures(ctx.story_id)
    return {}


def probe(request: HttpRequest, story_id: str) -> HttpResponse:
    return inspect_page(request, story_id, Tab.KNOWLEDGE, KNOWLEDGE_TEMPLATE, _probe)


urlpatterns = mounted(
    [
        *without_sections(),
        path("knowledge/exposures/", exposures, name="exposures"),
        path("knowledge/probe/", probe, name="knowledge-probe"),
    ]
)

"""The Console's routes with every Inspect tab registered (#135): each test tab calls
``inspect_page``, and its ``build`` makes one Engine read where :data:`READS` names one, so the
shell's gate and its 409 ``module_off`` handling can be tested before the tabs' own stories
land. A tab its story has registered (:data:`REAL`) keeps its real view."""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, URLResolver, include, path

from console.engine.client import _raise_for_envelope
from console.inspect import Tab
from console.views.inspect import InspectContext, inspect_page
from epistrel_console import urls as real

READS = {
    Tab.RETRIEVAL: "/stories/{story_id}/retrieve",
    Tab.LEDGER: "/stories/{story_id}/inspect/ledger",
}
TEMPLATE = "console/inspect/overview.html"
MOUNT = "stories/<str:story_id>/inspect/"


def _view(tab: Tab) -> Any:
    def build(ctx: InspectContext) -> dict[str, Any]:
        read = READS.get(tab)
        if read is not None:
            _raise_for_envelope(ctx.client._request("GET", read.format(story_id=ctx.story_id)))
        return {"built": tab.value}

    def view(request: HttpRequest, story_id: str) -> HttpResponse:
        return inspect_page(request, story_id, tab, TEMPLATE, build)

    return view


# Tabs whose own story has registered them for real: those keep their real view.
REAL = {Tab(p.name) for p in real.inspect_patterns[0] if isinstance(p, URLPattern) and p.name}

inspect_patterns = (
    [
        *real.inspect_patterns[0],
        *(path(f"{tab.value}/", _view(tab), name=tab.value) for tab in Tab if tab not in REAL),
    ],
    "inspect",
)

urlpatterns: list[URLPattern | URLResolver] = [
    path(MOUNT, include(inspect_patterns)),
    *(
        p
        for p in real.urlpatterns
        if isinstance(p, URLPattern | URLResolver) and str(p.pattern) != MOUNT
    ),
]

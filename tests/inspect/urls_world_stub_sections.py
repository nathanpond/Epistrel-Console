"""The Console's routes with two World sections, ``inspect:states`` and ``inspect:clock``, routed to
stub views (#239, D3): the World landing's redirect, the sub-navigation, the current-section rule
and a greyed section's zero-read short-circuit are tested before #242/#243 land, and keep working
after (their real section routes are dropped here).

Each stub renders through ``inspect_page(request, story_id, Tab.WORLD, "console/inspect/world.html",
build)`` with a ``build`` that records its call in :data:`BUILT`. Two pages below a section:
``world/state/probe/`` (``inspect:state-probe``) passes ``current="inspect:states"``, and
``world/other/`` (``inspect:world-other``) passes nothing, so it marks no section."""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, URLResolver, include, path

from console.inspect import Tab
from console.views.inspect import InspectContext, inspect_page
from epistrel_console import urls as real

MOUNT = "stories/<str:story_id>/inspect/"
WORLD_TEMPLATE = "console/inspect/world.html"
STUBBED = {"states", "state", "clock", "scheduled-events", "external-values"}
BUILT: list[str] = []  # the URL name of each stub build called, in order


def without_stubbed() -> list[Any]:
    """The real Inspect patterns minus any real ``inspect:states`` / ``inspect:state`` /
    ``inspect:clock`` / ``inspect:scheduled-events`` (#244) / ``inspect:external-values`` (#248)
    route (the real one-State page would
    shadow ``world/state/probe/``)."""
    return [p for p in real.inspect_patterns[0] if getattr(p, "name", None) not in STUBBED]


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


def stub(name: str, current: str | None = None) -> Any:
    def build(ctx: InspectContext) -> dict[str, Any]:
        BUILT.append(name)
        return {}

    def view(request: HttpRequest, story_id: str) -> HttpResponse:
        return inspect_page(request, story_id, Tab.WORLD, WORLD_TEMPLATE, build, current=current)

    return view


STATES = path("world/states/", stub("inspect:states"), name="states")
CLOCK = path("world/clock/", stub("inspect:clock"), name="clock")
SUB_PAGES = [
    path("world/state/probe/", stub("inspect:state-probe", "inspect:states"), name="state-probe"),
    path("world/other/", stub("inspect:world-other"), name="world-other"),
]

urlpatterns = mounted([*without_stubbed(), STATES, CLOCK, *SUB_PAGES])

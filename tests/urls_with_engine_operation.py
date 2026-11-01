"""The one D19 stub urlconf (#235): the Console's real routes plus a one-line stub view for each
URL name a later M6 story registers, so a D19 link helper's linked case can be tested before
its owner lands:

- ``operations:engine`` at ``operations/engine/<str:operation_id>/`` (#250);
- ``inspect:reconciliation`` at ``effects/reconciliation/`` under the inspect mount (#249);
- ``engine:provider`` at ``engine/providers/<str:name>/`` (#240);
- ``stories:providers`` at ``stories/<str:story_id>/providers/`` (#247).

Django searches one resolver per namespace, so each namespace's list is rebuilt (a real pattern
of a stub's name dropped, so this keeps working once the owner registers it; the stub appended)
and remounted in place of the real include, as ``tests/inspect/urls_without_retrieval.py``
does. A later story testing a D19 link reuses this file (adding a stub name here if it needs
one) and never adds its own.
"""

from collections.abc import Sequence

from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real


def stub(request: HttpRequest, **kwargs: str) -> HttpResponse:
    return HttpResponse("stub")


def _rebuilt(
    patterns: tuple[Sequence[URLPattern | URLResolver], str], stubs: list[URLPattern]
) -> tuple[list[URLPattern | URLResolver], str]:
    names = {p.name for p in stubs}
    kept = [p for p in patterns[0] if getattr(p, "name", None) not in names]
    return [*kept, *stubs], patterns[1]


MOUNTS = {
    "operations/": _rebuilt(
        real.operation_patterns,
        [path("engine/<str:operation_id>/", stub, name="engine")],
    ),
    "stories/<str:story_id>/inspect/": _rebuilt(
        real.inspect_patterns,
        [path("effects/reconciliation/", stub, name="reconciliation")],
    ),
    "stories/": _rebuilt(
        real.story_patterns,
        [path("<str:story_id>/providers/", stub, name="providers")],
    ),
    "engine/": _rebuilt(
        real.engine_patterns,
        [path("providers/<str:name>/", stub, name="provider")],
    ),
}


def _remounted(pattern: URLPattern | URLResolver) -> URLPattern | URLResolver:
    mount = str(pattern.pattern)
    return path(mount, include(MOUNTS[mount])) if mount in MOUNTS else pattern


urlpatterns: list[URLPattern | URLResolver] = [
    _remounted(p) for p in real.urlpatterns if isinstance(p, URLPattern | URLResolver)
]

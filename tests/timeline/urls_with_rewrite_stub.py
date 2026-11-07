"""The Console's routes plus a stub ``stories:rewrite`` at
``stories/<story_id>/rewrites/<session_id>/`` (E24's linked case, D19's pattern): the one name
regenerate's directed answer links before #297 registers the page. #294 folds this stub into
``tests/urls_with_m7_pages.py``."""

from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

MOUNT = "stories/"


def rewrite_stub(request: HttpRequest, story_id: str, session_id: str) -> HttpResponse:
    return HttpResponse(f"rewrite session {session_id}")


story_patterns = (
    [
        path("<str:story_id>/rewrites/<str:session_id>/", rewrite_stub, name="rewrite"),
        *real.story_patterns[0],
    ],
    "stories",
)

urlpatterns: list[URLPattern | URLResolver] = [
    path(MOUNT, include(story_patterns)),
    *(
        p
        for p in real.urlpatterns
        if isinstance(p, URLPattern | URLResolver) and str(p.pattern) != MOUNT
    ),
]

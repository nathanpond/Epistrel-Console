"""The Console's routes without the Retrieval tab: links into it (``retrieval_url``) are then
omitted (#140, #141)."""

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

MOUNT = "stories/<str:story_id>/inspect/"

inspect_patterns = (
    [p for p in real.inspect_patterns[0] if getattr(p, "name", None) != "retrieval"],
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

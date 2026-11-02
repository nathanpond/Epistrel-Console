"""The Console's routes without Reconciliation: outbox ids then render unlinked and the D12
count without a link (D19, #258)."""

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

MOUNT = "stories/<str:story_id>/inspect/"

inspect_patterns = (
    [p for p in real.inspect_patterns[0] if getattr(p, "name", None) != "reconciliation"],
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

"""The Console's routes without ``stories:line-edit`` (#305): History's rows then carry no
"Edit this line…" link, and ``_items.html`` renders as it did before the link existed (D19)."""

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

MOUNT = "stories/"

story_patterns = (
    [p for p in real.story_patterns[0] if getattr(p, "name", None) != "line-edit"],
    "stories",
)

urlpatterns: list[URLPattern | URLResolver] = [
    path(MOUNT, include(story_patterns)) if str(p.pattern) == MOUNT else p
    for p in real.urlpatterns
    if isinstance(p, URLPattern | URLResolver)
]

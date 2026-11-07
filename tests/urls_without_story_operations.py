"""The Console's routes without ``stories:operations-list`` (#324): the story nav then has no
Operations row, and the operation log filtered to one story has no link to it (D19)."""

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

MOUNT = "stories/"

story_patterns = (
    [p for p in real.story_patterns[0] if getattr(p, "name", None) != "operations-list"],
    "stories",
)

urlpatterns: list[URLPattern | URLResolver] = [
    path(MOUNT, include(story_patterns)) if str(p.pattern) == MOUNT else p
    for p in real.urlpatterns
    if isinstance(p, URLPattern | URLResolver)
]

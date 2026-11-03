"""The Console's routes without ``stories:correct`` (#263): #185's notice cards then offer no
Fix / Clarify control at all, not even greyed (#64's nav rule, D19)."""

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

MOUNT = "stories/"

story_patterns = (
    [p for p in real.story_patterns[0] if getattr(p, "name", None) != "correct"],
    "stories",
)

urlpatterns: list[URLPattern | URLResolver] = [
    path(MOUNT, include(story_patterns)) if str(p.pattern) == MOUNT else p
    for p in real.urlpatterns
    if isinstance(p, URLPattern | URLResolver)
]

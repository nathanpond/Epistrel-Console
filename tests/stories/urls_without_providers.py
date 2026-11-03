"""The Console's routes without the story's Providers page (#247): D19's ``provider_unbound``
link (``console.world.world_refusal``) is then "" (the unlinked rendering)."""

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

MOUNT = "stories/"
DROPPED = {"providers", "provider-bind"}

story_patterns = (
    [p for p in real.story_patterns[0] if getattr(p, "name", None) not in DROPPED],
    "stories",
)

urlpatterns: list[URLPattern | URLResolver] = [
    path(MOUNT, include(story_patterns)) if str(p.pattern) == MOUNT else p
    for p in real.urlpatterns
    if isinstance(p, URLPattern | URLResolver)
]

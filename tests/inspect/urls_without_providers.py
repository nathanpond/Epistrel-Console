"""The Console's routes without ``stories:providers`` (#247) and ``engine:provider`` (#240): the
External values section (#248) then shows no "Bindings:" line and its providers as plain
``<code>`` (D19's unlinked rendering)."""

from collections.abc import Sequence

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

DROPPED = {"stories/": "providers", "engine/": "provider"}


def _without(
    patterns: tuple[Sequence[URLPattern | URLResolver], str], name: str
) -> tuple[list[URLPattern | URLResolver], str]:
    return [p for p in patterns[0] if getattr(p, "name", None) != name], patterns[1]


MOUNTS = {
    "stories/": _without(real.story_patterns, DROPPED["stories/"]),
    "engine/": _without(real.engine_patterns, DROPPED["engine/"]),
}


def _remounted(pattern: URLPattern | URLResolver) -> URLPattern | URLResolver:
    mount = str(pattern.pattern)
    return path(mount, include(MOUNTS[mount])) if mount in MOUNTS else pattern


urlpatterns: list[URLPattern | URLResolver] = [
    _remounted(p) for p in real.urlpatterns if isinstance(p, URLPattern | URLResolver)
]

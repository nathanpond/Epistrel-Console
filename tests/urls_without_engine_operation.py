"""The Console's routes without the Engine operation page (#250): D19's
``console.effects.engine_operation_url`` is then ``None`` (the unlinked rendering of the
Incomplete badges, History's operation ids and the inspector's "Engine record")."""

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

MOUNT = "operations/"

operation_patterns = (
    [p for p in real.operation_patterns[0] if getattr(p, "name", None) != "engine"],
    "operations",
)

urlpatterns: list[URLPattern | URLResolver] = [
    path(MOUNT, include(operation_patterns)) if str(p.pattern) == MOUNT else p
    for p in real.urlpatterns
    if isinstance(p, URLPattern | URLResolver)
]

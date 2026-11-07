"""The Console's routes without the Effects tab's Outbox section (#325): E17's
``console.effects.outbox_list_notice_html(story_id=…)`` then renders D11's old notice (D19's
unregistered fallback)."""

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

MOUNT = "stories/<str:story_id>/inspect/"

inspect_patterns = (
    [p for p in real.inspect_patterns[0] if getattr(p, "name", None) != "outbox"],
    "inspect",
)

urlpatterns: list[URLPattern | URLResolver] = [
    path(MOUNT, include(inspect_patterns)) if str(p.pattern) == MOUNT else p
    for p in real.urlpatterns
    if isinstance(p, URLPattern | URLResolver)
]

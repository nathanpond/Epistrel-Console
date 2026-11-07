"""The Console's routes without ``stories:backfill`` (E24): a coverage gap line then has no
"Fill the gap" link (D19, #332's Retrieval explainer)."""

from django.urls import URLPattern, URLResolver, include, path

from epistrel_console import urls as real

story_patterns = (
    [p for p in real.story_patterns[0] if getattr(p, "name", None) != "backfill"],
    "stories",
)

urlpatterns: list[URLPattern | URLResolver] = [
    path("stories/", include(story_patterns)) if str(p.pattern) == "stories/" else p
    for p in real.urlpatterns
    if isinstance(p, URLPattern | URLResolver)
]

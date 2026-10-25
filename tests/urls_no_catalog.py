"""Test-only URLconf: the real routes without the catalog pages (#65, #67, #68, #70), for the
nav's "Catalog:" group while none of its URLs resolve."""

from django.urls import URLPattern, URLResolver

from epistrel_console.urls import urlpatterns as real_urlpatterns

CATALOG = {"catalog/characters/", "catalog/scenarios/", "personas/", "media/"}

urlpatterns: list[URLPattern | URLResolver] = [
    p
    for p in real_urlpatterns
    if isinstance(p, URLPattern | URLResolver) and str(p.pattern) not in CATALOG
]

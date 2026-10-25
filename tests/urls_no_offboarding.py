"""Test-only URLconf: the real routes without the offboarding pages (#77), for the principal
page's fallback while ``offboarding:new`` does not resolve."""

from django.urls import URLPattern, URLResolver

from epistrel_console.urls import urlpatterns as real_urlpatterns

urlpatterns: list[URLPattern | URLResolver] = [
    p
    for p in real_urlpatterns
    if isinstance(p, URLPattern | URLResolver) and str(p.pattern) != "offboarding/"
]

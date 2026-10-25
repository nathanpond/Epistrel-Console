"""Test-only URLconf: the real routes plus placeholder M3 pages (#64), so the nav sections, the
entity registry and ``principal_link`` can be tested before the stories that build them land."""

from django.http import HttpRequest, HttpResponse
from django.urls import include, path

from epistrel_console.urls import urlpatterns as real_urlpatterns


def placeholder(request: HttpRequest, **kwargs: str) -> HttpResponse:
    return HttpResponse("placeholder")


PATTERNS = [
    path("", placeholder, name="index"),
    path("<str:key>/", placeholder, name="detail"),
]

urlpatterns = [
    path("catalog/characters/", include((PATTERNS, "catalog_characters"))),
    path("principals/", include((PATTERNS, "principals"))),
    path("erasure/", include((PATTERNS, "erasure"))),
    *real_urlpatterns,
]

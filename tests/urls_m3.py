"""Test-only URLconf: the real routes plus placeholder M3 pages (#64), so the nav sections, the
entity registry and ``principal_link`` can be tested before the stories that build them land.
Each placeholder goes once its story builds the real routes (#65 replaced the characters', #68
removed the scenarios', #76 the principals'). The offboarding placeholder (#77) has ``new``
too, for #76's "Offboard…" link."""

from django.http import HttpRequest, HttpResponse
from django.urls import include, path

from epistrel_console.urls import urlpatterns as real_urlpatterns


def placeholder(request: HttpRequest, **kwargs: str) -> HttpResponse:
    return HttpResponse("placeholder")


PATTERNS = [
    path("", placeholder, name="index"),
    path("<str:key>/", placeholder, name="detail"),
]
OFFBOARDING = [
    path("", placeholder, name="index"),
    path("new/", placeholder, name="new"),
]

urlpatterns = [
    path("erasure/", include((PATTERNS, "erasure"))),
    path("offboarding/", include((OFFBOARDING, "offboarding"))),
    *real_urlpatterns,
]

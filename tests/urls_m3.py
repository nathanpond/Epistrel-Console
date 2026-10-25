"""Test-only URLconf: the real routes, kept for the tests written against the placeholder M3 pages
(#64). Each placeholder went once its story built the real routes (#65 replaced the characters', #68
removed the scenarios', #76 the principals', #77 the offboarding ones, #86 the erasure ones)."""

from epistrel_console.urls import urlpatterns as real_urlpatterns

urlpatterns = [*real_urlpatterns]

"""Test-only URLconf with no routes: for helpers that fall back while a page does not exist
(``principal_link`` renders ``<code>`` without ``principals:detail``)."""

from django.urls import URLPattern

urlpatterns: list[URLPattern] = []

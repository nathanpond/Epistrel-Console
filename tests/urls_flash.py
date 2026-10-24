"""Test-only URLconf: the real routes plus a view that flashes a message and redirects (PRG)."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import path

from epistrel_console.urls import urlpatterns as real_urlpatterns


def flash(request: HttpRequest) -> HttpResponse:
    messages.success(request, "Saved revision 3")
    return redirect("/stories/")


urlpatterns = [path("_test/flash/", flash), *real_urlpatterns]

"""The Console's routes plus stub views for the Reconciliation actions (#249) before their
owners land: ``inspect:settle-now`` (#256) and ``inspect:compensate`` (#264), each at its owner's
path under the inspect mount (``effects/reconciliation/<outbox_id>/settle-now/`` and
``…/compensate/``), taking ``[story_id, outbox_id]``. A real pattern of either name is dropped, so
this keeps working once the owners register theirs."""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import path

from epistrel_console import urls as real
from tests.inspect.urls_knowledge_exposures_only import mounted

STUB_NAMES = {"settle-now", "compensate"}


def stub(request: HttpRequest, **kwargs: str) -> HttpResponse:
    return HttpResponse("stub")


def _patterns() -> list[Any]:
    kept = [p for p in real.inspect_patterns[0] if getattr(p, "name", None) not in STUB_NAMES]
    return [
        *kept,
        path("effects/reconciliation/<str:outbox_id>/settle-now/", stub, name="settle-now"),
        path("effects/reconciliation/<str:outbox_id>/compensate/", stub, name="compensate"),
    ]


urlpatterns = mounted(_patterns())

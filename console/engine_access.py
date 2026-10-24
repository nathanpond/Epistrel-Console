"""How views get an Engine client: the only place outside ``console/engine/`` that builds one.

``engine_client_for(request)`` is the client for a signed-in operator: every call it makes carries
``console:<username>`` as the requesting principal. ``engine_client_unauthenticated()`` is for the
status views (``/``, ``/health``) and the badge, which call the Engine for no one in particular.
Both return a new client per call (one ``httpx.Client`` per request, #34).
"""

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from console.engine.client import EngineClient, principal_for
from console.operators import is_operator


def engine_client_for(request: HttpRequest) -> EngineClient:
    """A client whose calls carry ``principal_for(request.user)``.

    Raises :class:`PermissionDenied` unless the user is a signed-in operator, and
    ``EngineNotConfigured`` when no Engine URL is set (the error panel renders it).
    """
    user = request.user
    if not is_operator(user):
        raise PermissionDenied("only a signed-in operator can call the Engine")
    client = EngineClient.from_settings()
    client.principal = principal_for(user)
    return client


def engine_client_unauthenticated() -> EngineClient:
    """A client with no requesting principal (``EngineNotConfigured`` when no URL is set)."""
    return EngineClient.from_settings()

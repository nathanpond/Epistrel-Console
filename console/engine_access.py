"""How views get an Engine client: the only place outside ``console/engine/`` that builds one.

``engine_client_for(request)`` is the client for a signed-in operator: every call it makes carries
``console:<username>`` as the requesting principal, and every mutating call it makes is recorded
in the operation log (#44). ``engine_client_unauthenticated()`` is for the status views (``/``,
``/health``) and the badge, which call the Engine for no one in particular. Both return a new
client per call (one ``httpx.Client`` per request, #34).
"""

import time
from collections.abc import Mapping
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from console import operation_log
from console.engine.client import (
    MUTATING,
    EngineClient,
    EngineResponse,
    EngineUnreachable,
    current_call,
    principal_for,
)
from console.engine.models import OperationRecord
from console.operators import is_operator


class RecordingEngineClient(EngineClient):
    """An :class:`EngineClient` that writes one ``EngineOperation`` row per mutating call (#44).

    It overrides the single request method, so every client method is covered as soon as it
    exists. A call is mutating when its ``@operation`` registration's HTTP method is
    POST/PUT/PATCH/DELETE (a bare ``_request`` call, outside any registered method, goes by the
    request's own method). Reads are never recorded. A call refused before anything was sent
    (no principal) writes nothing; one that got an answer, an error or no answer writes a row.
    """

    operator: Any = None  # the Console user the calls are made for

    def _request(
        self,
        method: str,
        path: str,
        *,
        principal: str | None = None,
        timeout: float | None = None,
        params: dict[str, str] | None = None,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> EngineResponse:
        call = current_call()
        registered = call.operation.method if call is not None else method
        if registered.upper() not in MUTATING:
            return super()._request(
                method,
                path,
                principal=principal,
                timeout=timeout,
                params=params,
                json=json,
                headers=headers,
            )
        started = time.monotonic()
        response: EngineResponse | None = None
        unreachable: str | None = None
        try:
            response = super()._request(
                method,
                path,
                principal=principal,
                timeout=timeout,
                params=params,
                json=json,
                headers=headers,
            )
        except EngineUnreachable as exc:
            unreachable = exc.reason
            raise
        finally:
            if response is not None or unreachable is not None:
                operation_log.record(
                    operation_log.Observed(
                        kind=call.kind if call is not None else f"{method.upper()} {path}",
                        operation=call.operation if call is not None else None,
                        http_method=method.upper(),
                        path=path.split("?", 1)[0],
                        params=params,
                        body=json,
                        headers=dict(headers or {}),
                        principal=principal if principal is not None else self.principal or "",
                        elapsed_ms=round((time.monotonic() - started) * 1000),
                        response=response,
                        unreachable=unreachable,
                    ),
                    self.operator,
                )
        return response


def engine_client_for(request: HttpRequest) -> EngineClient:
    """A client whose calls carry ``principal_for(request.user)`` and whose mutating calls are
    recorded in the operation log.

    Raises :class:`PermissionDenied` unless the user is a signed-in operator, and
    ``EngineNotConfigured`` when no Engine URL is set (the error panel renders it).
    """
    user = request.user
    if not is_operator(user):
        raise PermissionDenied("only a signed-in operator can call the Engine")
    client = RecordingEngineClient.from_settings()
    client.principal = principal_for(user)
    client.operator = user
    return client


def engine_client_unauthenticated() -> EngineClient:
    """A client with no requesting principal (``EngineNotConfigured`` when no URL is set)."""
    return EngineClient.from_settings()


def complete_queued(engine_operation_id: str, op: OperationRecord) -> int:
    """Settle the operation log's ``queued`` row(s) for ``engine_operation_id`` once the Engine
    reports the operation ``committed``, ``failed`` or ``abandoned`` (#48's poll view). Returns
    the rows updated (0 while it is still queued or running)."""
    return operation_log.complete_queued(engine_operation_id, op)

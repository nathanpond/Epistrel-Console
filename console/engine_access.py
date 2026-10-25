"""How views get an Engine client: the only place outside ``console/engine/`` that builds one.

``engine_client_for(request)`` is the client for a signed-in operator: every call it makes, reads
included, carries the operator's acting principal (#64: ``console.acting.acting_principal``,
``console:<username>`` unless they picked another), and every recorded call it makes (each
mutating one, and those ``@operation(recorded=True)`` marks) writes a row in the operation log
(#44). ``engine_client_unauthenticated()`` is for the status views (``/``, ``/health``) and the
badge, which call the Engine for no one in particular. Both return a new client per call (one
``httpx.Client`` per request, #34).

``complete_queued`` and ``complete_job`` settle a recorded ``queued`` row once the Engine reports
the turn or job finished.
"""

import time
from collections.abc import Mapping
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from console import operation_log
from console.acting import acting_principal
from console.engine.client import (
    MUTATING,
    EngineClient,
    EngineResponse,
    EngineUnreachable,
    current_call,
)
from console.engine.models import OperationRecord
from console.operators import is_operator


class RecordingEngineClient(EngineClient):
    """An :class:`EngineClient` that writes one ``EngineOperation`` row per recorded call (#44).

    It overrides the single request method, so every client method is covered as soon as it
    exists. A call is recorded when its ``@operation`` registration says so (``recorded``:
    POST/PUT/PATCH/DELETE by default, and the non-writing POSTs and feedback calls flagged
    ``recorded=True``, #64); a bare ``_request`` call, outside any registered method, goes by
    the request's own method. A call refused before anything was sent (no principal) writes
    nothing; one that got an answer, an error or no answer writes a row.

    The row's ``principal`` is the one the request actually sent: the per-call override if one
    was given, else :attr:`principal`. :attr:`last_operation_id` is the pk of the row the latest
    recorded call wrote (``None`` before one, or when the write failed): the "View in operations
    log" link and ``?report=<pk>`` (#64 conventions 4 and 9).
    """

    operator: Any = None  # the Console user the calls are made for
    last_operation_id: int | None = None

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
        recorded = call.operation.is_recorded if call is not None else method.upper() in MUTATING
        if not recorded:
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
                row = operation_log.record(
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
                        call=call,
                    ),
                    self.operator,
                )
                self.last_operation_id = row.pk if row is not None else None
        return response


def engine_client_for(request: HttpRequest) -> RecordingEngineClient:
    """A client whose calls carry the operator's acting principal (#64; by default
    ``principal_for(request.user)``) and whose recorded calls are written to the operation log.

    Raises :class:`PermissionDenied` unless the user is a signed-in operator, and
    ``EngineNotConfigured`` when no Engine URL is set (the error panel renders it).
    """
    user = request.user
    if not is_operator(user):
        raise PermissionDenied("only a signed-in operator can call the Engine")
    client = RecordingEngineClient.from_settings()
    client.principal = acting_principal(request)
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


def complete_job(
    job_id: str,
    outcome: str,
    error_code: str = "",
    response: object = None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> int:
    """Settle the operation log's ``queued`` row for the Engine job ``job_id`` (a 202 job) on its
    first terminal poll; idempotent (a second call returns 0 and changes nothing). See
    :func:`console.operation_log.complete_job` for the outcome mapping and ``overrides``."""
    return operation_log.complete_job(job_id, outcome, error_code, response, overrides=overrides)

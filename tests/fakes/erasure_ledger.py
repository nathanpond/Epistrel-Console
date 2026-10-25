"""Hand-written Engine answers for the erasure ledger (#87), to the recorded schemas
(``ErasureStatusBody``, ``ErasureBoundaryBody``, ``ReconcileRunBody``)."""

from typing import Any

from tests.fakes.engine import FakeEngine

STATUS = "/admin/erasure/status"
BOUNDARY = "/admin/erasure/boundary"
RECONCILE = "/admin/erasure/reconcile"


def status_body(**changes: Any) -> dict[str, Any]:
    """An idle ledger with nothing pending, one entry applied and no run yet."""
    body: dict[str, Any] = {
        "pending": [],
        "pending_count": 0,
        "applied_count": 1,
        "in_progress": False,
        "last_run_at": None,
        "last_error": None,
        "attempts": {},
    }
    body.update(changes)
    return body


def boundary_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "in_scope": ["events and beats (redacted in place)", "the subject-reference index"],
        "out_of_scope": ["exports already delivered", "candidates (`candidates_remaining`)"],
        "logs": "Logs never carry story content (`EPISTREL_LOG_CONTENT` off).",
    }
    body.update(changes)
    return body


def reconcile_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "run_id": "01JR0000000000000000RUN001",
        "ran": True,
        "settled": 0,
        "failed": 0,
        "pending_count": 0,
    }
    body.update(changes)
    return body


def answer_status(fake: FakeEngine, **changes: Any) -> None:
    fake.respond("GET", STATUS, 200, json=status_body(**changes))

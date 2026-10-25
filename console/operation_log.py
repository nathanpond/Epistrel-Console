"""The operation log (#44): turning one observed mutating Engine call into an ``EngineOperation``
row, and reading guardrail facts back out of a stored response.

The recorder (``console.engine_access.RecordingEngineClient``) observes; this module decides
what the row says. Writing a row never masks the Engine's answer: a failed write is logged at
ERROR (kind and status only, never a body) and swallowed.
"""

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from console.engine.client import Call, EngineResponse, Operation
from console.engine.models import OperationRecord
from console.models import EngineOperation
from console.redaction import redact

log = logging.getLogger("console.operations")

# The request headers worth keeping (#44): never Authorization, never the principal.
KEPT_REQUEST_HEADERS = ("Idempotency-Key", "X-Epistrel-Expected-Revision", "Prefer")
IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replay"
GUARDRAIL_VIOLATION = "guardrail_violation"
STORY_PATH = re.compile(r"^/stories/([^/?]+)")
ADMIN_STORY_PATH = re.compile(r"^/admin/stories/([^/?]+)")  # story maintenance (#50's rebuild)
ULID_LEN = 26
Outcome = EngineOperation.Outcome


@dataclass(frozen=True)
class Observed:
    """One mutating call as the recorder saw it."""

    kind: str
    operation: Operation | None  # the registration, when a public client method made the call
    http_method: str
    path: str  # resolved, without the query string
    params: Mapping[str, str] | list[tuple[str, str]] | None
    body: object | None
    headers: Mapping[str, str]
    principal: str
    elapsed_ms: int
    response: EngineResponse | None = None
    unreachable: str | None = None  # the reason, when no HTTP answer came
    call: Call | None = None  # the running client method (its arguments name the entity, #64)


def request_json(observed: Observed) -> dict[str, Any]:
    """``{"body", "headers"[, "query"]}``, redacted; Authorization and the principal never."""
    sent = {name.lower(): value for name, value in observed.headers.items()}
    kept = {name: sent[name.lower()] for name in KEPT_REQUEST_HEADERS if name.lower() in sent}
    stored: dict[str, Any] = {"body": observed.body, "headers": kept}
    if observed.params:
        stored["query"] = dict(observed.params)
    redacted: dict[str, Any] = redact(stored)
    return redacted


def _json_body(response: EngineResponse) -> tuple[bool, object | None]:
    """``(True, parsed)`` for a JSON body, ``(False, None)`` otherwise (an empty body too)."""
    if not response.content:
        return False, None
    try:
        parsed: object = json.loads(response.content)
    except ValueError:
        return False, None
    return True, parsed


def _int(body: Mapping[str, Any], key: str) -> int | None:
    value = body.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(body: Mapping[str, Any], key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) else None


def _ulid_or_blank(value: str | None) -> str:
    return value if value is not None and len(value) <= ULID_LEN else ""


def _fit(value: str | None, size: int) -> str:
    return (value or "")[:size]


def _first_operation_id(items: object) -> str | None:
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("operation_id"), str):
                found: str = item["operation_id"]
                return found
    return None


def engine_operation_id(body: Mapping[str, Any]) -> str | None:
    """Top-level ``operation_id``; for an ingest batch, the first result's; for a turn (whose
    body has no top-level id), the operation of its beats (#44)."""
    return (
        _str(body, "operation_id")
        or _first_operation_id(body.get("results"))
        or _first_operation_id(body.get("beats"))
    )


def config_revision(body: Mapping[str, Any], path: str) -> int | None:
    """``config_revision``, or ``revision`` on an admin config response."""
    found = _int(body, "config_revision")
    if found is None and path.startswith("/admin/"):
        found = _int(body, "revision")
    return found


def story_id(path: str, body: Mapping[str, Any]) -> str | None:
    """The story the call was about: for admin calls, only a story-maintenance path's story
    (``/admin/stories/{id}/…``); the new story for a call that answers with a ``story_id``
    (create, fork); else the path's story id."""
    if path.startswith("/admin/"):
        admin = ADMIN_STORY_PATH.match(path)
        return admin.group(1) if admin else None
    created = _str(body, "story_id")
    if created is not None:
        return created
    match = STORY_PATH.match(path)
    return match.group(1) if match else None


# --- reading a stored response -------------------------------------------------------------------


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def error_details(response: object) -> dict[str, Any]:
    return _dict(_dict(_dict(response).get("error")).get("details"))


def turn_report_json(response: object) -> dict[str, Any] | None:
    """The turn report in a stored response: the response itself when it has ``model_calls``
    (a turn), else a 503's ``error.details`` when those carry a ``model_calls`` list. A 422
    ``guardrail_violation``'s ``model_calls`` is the input gate's count, not a report
    (``input_gate_spend``)."""
    top = _dict(response)
    if "model_calls" in top:
        return top
    details = error_details(response)
    if isinstance(details.get("model_calls"), list):
        return details
    return None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def input_gate_spend(response: object) -> tuple[int, int] | None:
    """What a refused input's gate spent: a 422 ``guardrail_violation``'s ``error.details``
    ``model_calls`` (a count) and ``elapsed_ms``, or None when either is absent."""
    details = error_details(response)
    calls, elapsed = _count(details.get("model_calls")), _count(details.get("elapsed_ms"))
    return None if calls is None or elapsed is None else (calls, elapsed)


def is_turn(response: object) -> bool:
    return turn_report_json(response) is not None


def _violations(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def input_violations(response: object) -> list[dict[str, Any]]:
    """The violations of a 422 ``guardrail_violation`` (``error.details.violations``)."""
    return _violations(error_details(response).get("violations"))


def output_violations(response: object) -> list[dict[str, Any]]:
    """The released reply's violations (the turn report's ``guardrail.violations``)."""
    report = turn_report_json(response) or {}
    return _violations(_dict(report.get("guardrail")).get("violations"))


def fallback_used(response: object) -> bool:
    report = turn_report_json(response) or {}
    return _dict(report.get("guardrail")).get("fallback_used") is True


def policy_ids(response: object) -> set[str]:
    """Every ``policy_id`` among the response's input and output violations."""
    return {
        v["policy_id"]
        for v in (*input_violations(response), *output_violations(response))
        if isinstance(v.get("policy_id"), str)
    }


def is_guardrail_rejection(error_code: str, response: object) -> bool:
    """A 422 ``guardrail_violation``, or a turn with output violations or the safe fallback."""
    return (
        error_code == GUARDRAIL_VIOLATION
        or bool(output_violations(response))
        or fallback_used(response)
    )


REBUILD_KIND = "rebuild_story"


def rebuild_job_id(kind: str, http_status: int | None, response: object) -> str | None:
    """The worker job a queued rebuild (202) was handed to (#50), else ``None``. The Engine has
    no status read for it, so the row stays ``queued``."""
    if kind != REBUILD_KIND or http_status != 202:
        return None
    return _str(_dict(response), "job_id")


def rebuild_job_text(job_id: str) -> str:
    """What a queued rebuild says, stated honestly: nothing can be polled (#50)."""
    return (
        f"Rebuild running as job {job_id} — the Engine has no job status endpoint for "
        "rebuilds; check the Engine logs"
    )


# --- building and writing rows ------------------------------------------------------------------


def _answer(observed: Observed, response: EngineResponse) -> dict[str, Any]:
    """The answer-side fields of a row for an HTTP response."""
    status = response.status_code
    is_json, body = _json_body(response)
    obj = _dict(body)
    replayed = response.headers.get(REPLAY_HEADER, "").lower() == "true" or "reference" in obj
    if 200 <= status < 300:
        model = observed.operation.response_model if observed.operation else None
        if status == 202:  # a queued turn, or a checking job (#64, #80, #81 complete those)
            queued = isinstance(body, dict)
            outcome, code = (Outcome.QUEUED, "") if queued else (Outcome.ERROR, "bad_response")
        elif replayed or status == 204 or model is None or model.from_json(body) is not None:
            outcome, code = Outcome.OK, ""
        else:
            outcome, code = Outcome.ERROR, "bad_response"
    else:
        error = _dict(obj.get("error"))
        code = _str(error, "code") or f"http_{status}"
        outcome = Outcome.ERROR
    sent_key = {k.lower(): v for k, v in observed.headers.items()}.get(IDEMPOTENCY_HEADER.lower())
    key = sent_key or _str(obj, "idempotency_key") or response.headers.get(IDEMPOTENCY_HEADER)
    stored = redact(body) if is_json else None
    job_id = _str(obj, "job_id") if status == 202 else None
    return {
        "job_id": job_id[:64] if job_id else None,
        "http_status": status,
        "outcome": outcome,
        "error_code": _fit(code, 64),
        "response": stored,
        "engine_operation_id": _ulid_or_blank(engine_operation_id(obj)),
        "timeline_revision": _int(obj, "timeline_revision"),
        "config_revision": config_revision(obj, observed.path),
        "idempotency_key": _fit(key, 255),
        "replayed": replayed,
        "story_id": _ulid_or_blank(story_id(observed.path, obj)),
        "is_guardrail_rejection": is_guardrail_rejection(code, stored),
    }


def row_fields(observed: Observed) -> dict[str, Any]:
    """Every field of the ``EngineOperation`` row for ``observed`` (operator aside)."""
    fields: dict[str, Any] = {
        "principal": _fit(observed.principal, 255),
        "kind": _fit(observed.kind, 64),
        "http_method": observed.http_method,
        "path": observed.path,
        "request": request_json(observed),
        "elapsed_ms": max(observed.elapsed_ms, 0),
    }
    if observed.response is not None:
        fields.update(_answer(observed, observed.response))
    else:
        sent_key = {k.lower(): v for k, v in observed.headers.items()}.get(
            IDEMPOTENCY_HEADER.lower()
        )
        fields.update(
            outcome=Outcome.UNREACHABLE,
            error_code=_fit(observed.unreachable or "unreachable", 64),
            http_status=None,
            response=None,
            idempotency_key=_fit(sent_key, 255),
            story_id=_ulid_or_blank(story_id(observed.path, {})),
        )
    fields.update(entity_fields(observed, fields["story_id"]))
    return fields


def entity_fields(observed: Observed, row_story_id: str) -> dict[str, str | None]:
    """``entity_type``/``entity_id`` for the row (#64 convention 9): the registration's entity
    (``@operation(entity=…, entity_id=…)``), else ``story`` when the row names a story, else
    neither."""
    body: object = None
    if observed.response is not None:
        _, body = _json_body(observed.response)
    ref = observed.call.entity_ref(body) if observed.call is not None else None
    if ref is not None:
        return {"entity_type": ref[0], "entity_id": ref[1][:64]}
    if row_story_id:
        return {"entity_type": "story", "entity_id": row_story_id}
    return {"entity_type": None, "entity_id": None}


def record(observed: Observed, operator: Any) -> EngineOperation | None:
    """Write the row for ``observed``; ``None`` (logged at ERROR) when the write fails."""
    fields = row_fields(observed)
    user = operator if getattr(operator, "pk", None) is not None else None
    username = operator.get_username() if operator is not None else ""
    try:
        with transaction.atomic(durable=False):
            return EngineOperation.objects.create(
                operator=user, operator_username=_fit(username, 150), **fields
            )
    except Exception:
        log.error(
            "operation record not written: kind=%s status=%s",
            fields["kind"],
            fields.get("http_status") or fields["outcome"],
        )
        return None


TERMINAL_FAILURES = frozenset({"failed", "abandoned"})
# What ``complete_job``'s ``overrides`` may set besides the outcome (#64 convention 9).
JOB_OVERRIDES = frozenset({"engine_operation_id", "timeline_revision", "story_id"})


def _complete_row(
    row: EngineOperation,
    outcome: str,
    error_code: str,
    response: object,
    changes: Mapping[str, Any] | None = None,
) -> bool:
    """Settle one ``queued`` row: its outcome, error code (``""`` for none), stored response and
    any further ``changes`` (field → value), with the guardrail flag recomputed. ``False``
    (logged at ERROR) when the write fails."""
    row.outcome = outcome
    row.error_code = _fit(error_code, 64)
    row.response = response
    for name, value in (changes or {}).items():
        setattr(row, name, value)
    row.is_guardrail_rejection = is_guardrail_rejection(row.error_code, response)
    try:
        with transaction.atomic(durable=False):
            row.save()
    except Exception:
        log.error("operation record not updated: kind=%s outcome=%s", row.kind, outcome)
        return False
    return True


def complete_queued(engine_operation_id: str, op: OperationRecord) -> int:
    """Settle the ``queued`` rows for ``engine_operation_id`` from the Engine's record of it
    (#48's poll view calls this through ``console.engine_access``). Returns the rows updated.

    ``committed`` → ``ok``; ``failed``/``abandoned`` → ``error`` with the stored result's error
    code, else the status. Any other status leaves the rows ``queued``. The row's
    ``elapsed_ms`` stays the 202 round trip; the turn's own time is in the stored result.
    """
    if op.status != "committed" and op.status not in TERMINAL_FAILURES:
        return 0
    rows = EngineOperation.objects.filter(
        engine_operation_id=engine_operation_id, outcome=Outcome.QUEUED
    )
    updated = 0
    for row in rows:
        response: object = row.response
        error_code = ""
        if op.result is not None:
            response = redact(op.result.body)
            if op.result.status >= 400:
                error_code = _str(_dict(_dict(response).get("error")), "code") or ""
        if op.status == "committed":
            outcome = Outcome.OK
        else:
            outcome = Outcome.ERROR
            error_code = error_code or op.status
        changes: dict[str, Any] = {}
        if op.timeline_revision is not None:
            changes["timeline_revision"] = op.timeline_revision
        revision = _int(_dict(response), "config_revision")
        if revision is not None:
            changes["config_revision"] = revision
        if _complete_row(row, outcome, error_code, response, changes):
            updated += 1
    return updated


def complete_job(
    job_id: str,
    outcome: str,
    error_code: str = "",
    response: object = None,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> int:
    """Settle the one row still ``queued`` for the Engine job ``job_id`` (a 202 job: a rating
    check, offboarding, an erasure, #64 convention 9). Returns 1 when it settled a row, 0 when
    none was queued, so a second call (the next poll of a finished job) changes nothing.

    The caller maps the job's terminal state: success → ``outcome="ok"``; ``refused`` →
    ``"error"``, ``error_code="rating_refused"``; ``failed`` → ``"error"`` with the job's
    ``error.class``. ``response`` (the job's status body) is stored redacted in place of the
    202's; ``None`` keeps the 202's. ``overrides`` may also set ``engine_operation_id``,
    ``timeline_revision`` and ``story_id``, in the same ``select_for_update`` transaction.
    """
    if outcome not in Outcome.values or outcome == Outcome.QUEUED:
        raise ValueError(f"complete_job: {outcome!r} is not a terminal outcome")
    unknown = set(overrides or {}) - JOB_OVERRIDES
    if unknown:
        raise ValueError(f"complete_job: cannot override {sorted(unknown)}")
    with transaction.atomic(durable=False):
        row = (
            EngineOperation.objects.select_for_update()
            .filter(job_id=job_id, outcome=Outcome.QUEUED)
            .order_by("id")
            .first()
        )
        if row is None:
            return 0
        stored = redact(response) if response is not None else row.response
        return 1 if _complete_row(row, outcome, error_code, stored, overrides) else 0

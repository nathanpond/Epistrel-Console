"""Typed Engine response models: frozen dataclasses, parsed leniently from JSON.

Each model mirrors one component of the recorded schema (``openapi.json``); the contract test
(``tests/engine/test_contract.py``) fails when a field, type or required set drifts from it. A
field is required exactly when it has no default.

``from_json`` ignores keys the model does not know and returns ``None`` when a known field has
the wrong type or a required one is missing, so a malformed answer never raises.

A field typed ``Any`` holds raw JSON (any value), for parts the Console shows only as JSON.
"""

import types
import typing
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from typing import Any, Literal, Self, Union


def _parse(tp: Any, value: object) -> tuple[bool, object]:
    """``(True, parsed)`` when ``value`` fits the annotation ``tp``, else ``(False, None)``."""
    if tp is Any:
        return True, value
    origin = typing.get_origin(tp)
    if origin in (Union, types.UnionType):
        for option in typing.get_args(tp):
            ok, parsed = _parse(option, value)
            if ok:
                return True, parsed
        return False, None
    if tp is type(None):
        return value is None, None
    if origin is Literal:
        return value in typing.get_args(tp) and isinstance(value, str | int | bool), value
    if tp is bool:
        return isinstance(value, bool), value
    if tp is int:
        return isinstance(value, int) and not isinstance(value, bool), value
    if tp is float:
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        return ok, float(value) if ok else None  # type: ignore[arg-type]
    if tp is str:
        return isinstance(value, str), value
    if origin is dict:
        _, item_tp = typing.get_args(tp)
        if not isinstance(value, dict):
            return False, None
        out: dict[str, object] = {}
        for key, item in value.items():
            ok, parsed = _parse(item_tp, item)
            if not ok or not isinstance(key, str):
                return False, None
            out[key] = parsed
        return True, out
    if origin is list:
        (item_tp,) = typing.get_args(tp)
        if not isinstance(value, list):
            return False, None
        items: list[object] = []
        for item in value:
            ok, parsed = _parse(item_tp, item)
            if not ok:
                return False, None
            items.append(parsed)
        return True, items
    if isinstance(tp, type) and is_dataclass(tp):
        parsed = _from_json(tp, value)
        return parsed is not None, parsed
    raise TypeError(f"unsupported model annotation {tp!r}")


def _from_json(cls: type, obj: object) -> object | None:
    if not isinstance(obj, dict):
        return None
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, object] = {}
    for f in fields(cls):
        if f.name not in obj:
            if f.default is MISSING and f.default_factory is MISSING:
                return None
            continue
        ok, parsed = _parse(hints[f.name], obj[f.name])
        if not ok:
            return None
        kwargs[f.name] = parsed
    instance: object = cls(**kwargs)
    return instance


class Model:
    """Base for response models: adds ``from_json``."""

    @classmethod
    def from_json(cls, obj: object) -> Self | None:
        parsed = _from_json(cls, obj)
        return parsed if isinstance(parsed, cls) else None


@dataclass(frozen=True)
class WorkersCheck(Model):
    alive: int | None = None
    last_heartbeat_s: float | None = None


@dataclass(frozen=True)
class HealthChecks(Model):
    database: Literal["ok", "error", "skipped"]
    workers: WorkersCheck = field(default_factory=WorkersCheck)
    oldest_overdue_timer_s: float | None = None
    oldest_overdue_job_s: float | None = None
    ffmpeg: Literal["ok", "missing"] = "missing"
    ffprobe: Literal["ok", "missing"] = "missing"
    erasure_pending: int | None = None


@dataclass(frozen=True)
class HealthResponse(Model):
    status: Literal["ok", "degraded", "unavailable"]
    checks: HealthChecks
    content_logging: bool = False


StoryStatus = Literal["active", "archived", "paused", "blocked_by_erasure"]


@dataclass(frozen=True)
class StoryRating(Model):
    """``StoryRatingBody``: the story's content rating, its current version and its name."""

    id: str
    version_id: str
    name: str


@dataclass(frozen=True)
class StorySummary(Model):
    """``StorySummaryBody``: a story as ``GET /stories`` lists it.

    ``name`` is ``None`` for a story held ``blocked_by_erasure``. Times are the Engine's ISO
    strings (render them with the ``engine_time`` filter).
    """

    id: str
    name: str | None
    owner_principal: str
    status: StoryStatus
    timeline_revision: int
    next_turn_index: int
    content_rating: StoryRating
    created_at: str
    archived_at: str | None


@dataclass(frozen=True)
class StoryList(Model):
    items: list[StorySummary]


# --- Turn reports (#44; #48's inspector reuses them) ------------------------------------------


@dataclass(frozen=True)
class ModelCall(Model):
    """``ModelCall``: one model call attempt as a turn reports it."""

    purpose: str
    model: str
    attempt: int
    outcome: Literal["ok", "timeout", "http_error", "invalid_output", "cancelled"]
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost: float | None = None
    finish_reason: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class TurnBudget(Model):
    """``BudgetBody``: the turn's call cap, the guardrail calls it reserved and the calls made."""

    max_calls: int | None
    reserved: dict[str, int]
    used: int


@dataclass(frozen=True)
class DegradationStep(Model):
    """``DegradationBody``: a degradation step and its cause."""

    step: str
    cause: str


@dataclass(frozen=True)
class GuardrailViolation(Model):
    """``GuardrailViolationBody``: an output policy the released reply breaks."""

    policy_id: str
    name: str
    message: str
    kind: str


@dataclass(frozen=True)
class TurnGuardrail(Model):
    """``GuardrailBody``: the output gate's outcome."""

    violations: list[GuardrailViolation]
    fallback_used: bool


@dataclass(frozen=True)
class QualityStatus(Model):
    status: Literal["certified", "uncertified", "stale"]


@dataclass(frozen=True)
class ProfileVersionRef(Model):
    id: str
    version: int


@dataclass(frozen=True)
class TurnWarning(Model):
    """``WarningBody``: something the turn did that the consumer should know (#85 renders it)."""

    kind: str
    memory_id: str | None = None


@dataclass(frozen=True)
class TurnReport(Model):
    """Where a turn's time and calls went: the report part of ``RespondResponse`` /
    ``RegeneratedBody`` (no schema component of its own; each field matches theirs).

    Only ``model_calls`` and ``elapsed_ms`` are required, so the same model reads a 503's or a
    422's ``details`` (which carry those two) and turns replayed from before the Engine added
    ``fingerprint``, ``capabilities``, ``quality_status``, ``profile`` and ``config_revision``.
    """

    model_calls: list[ModelCall]
    elapsed_ms: int
    call_counts: dict[str, int] = field(default_factory=dict)
    budget: TurnBudget | None = None
    degradation: list[str] = field(default_factory=list)
    degradation_details: list[DegradationStep] = field(default_factory=list)
    guardrail: TurnGuardrail | None = None
    queued_ms: int | None = None
    first_call_offset_ms: int | None = None
    fingerprint: str | None = None
    capabilities: list[str] | None = None
    quality_status: QualityStatus | None = None
    profile: ProfileVersionRef | None = None
    config_revision: int | None = None
    warnings: list[TurnWarning] = field(default_factory=list)
    timeline_revision: int | None = None
    turn: int | None = None
    variant: int | None = None


# --- Operations (#44) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredResult(Model):
    """``StoredResultBody``: the stored response of an operation's ``Idempotency-Key``."""

    status: int
    body: Any


@dataclass(frozen=True)
class OperationStage(Model):
    """``StageBody``: one stage of a long-running operation."""

    name: str
    status: Literal["pending", "running", "done", "failed", "skipped", "partial"]
    at: str | None


OperationLabel = Literal["genesis", "rating_check", "reactivation_check", "offboarding", "erasure"]


@dataclass(frozen=True)
class OperationRecord(Model):
    """``OperationBody``: the Engine's record of one operation (``GET /operations/{id}``).

    ``result`` is present only inside the idempotency result window (24 h by default); its
    ``body`` stays raw JSON. ``creation_report``, ``eval_hooks`` and ``read_set`` are kept as
    raw JSON too: the Console only shows them.
    """

    id: str
    story_id: str | None
    kind: str
    status: str
    narrative_turn: int | None
    turn_variant: int | None
    timeline_revision: int | None
    principal: str
    started_at: str
    committed_at: str | None
    viewpoint: str | None
    origin: str
    batch_operation_ids: list[str] | None
    attempts: list[dict[str, Any]]
    config_revisions: dict[str, Any] | None
    idempotency_key: str | None
    creation_report: Any = None
    eval_hooks: Any = None
    read_set: Any = None
    first_call_offset_ms: int | None = None
    queued_ms: int | None = None
    label: OperationLabel | None = None
    result: StoredResult | None = None
    stages: list[OperationStage] = field(default_factory=list)


@dataclass(frozen=True)
class HealthResult:
    """What ``GET /health`` returned, for any HTTP status.

    ``body`` is the typed model, or ``None`` when the JSON does not match it; ``raw`` is the
    parsed JSON (``None`` when the body is not JSON), so the probe can classify exactly.
    """

    http_status: int
    body: HealthResponse | None
    raw: object | None
    latency_ms: int

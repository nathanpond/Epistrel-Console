"""Typed Engine response models: frozen dataclasses, parsed leniently from JSON.

Each model mirrors one component of the recorded schema (``openapi.json``); the contract test
(``tests/engine/test_contract.py``) fails when a field, type or required set drifts from it. A
field is required exactly when it has no default.

``from_json`` ignores keys the model does not know and returns ``None`` when a known field has
the wrong type or a required one is missing, so a malformed answer never raises.

A field typed ``Any`` holds raw JSON (any value), for parts the Console shows only as JSON.

A JSON key that is not a valid Python name (``class``) is read into a field declared with
``field(metadata={JSON_NAME: "class"})`` (:func:`json_name`).
"""

import types
import typing
from dataclasses import MISSING, Field, dataclass, field, fields, is_dataclass
from typing import Any, Literal, Self, Union

from console.entities import EntityType


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


JSON_NAME = "json"


def json_name(f: Field[Any]) -> str:
    """The JSON key a model field reads: its ``JSON_NAME`` metadata, else its own name."""
    name = f.metadata.get(JSON_NAME, f.name)
    return name if isinstance(name, str) else f.name


def _from_json(cls: type, obj: object) -> object | None:
    if not isinstance(obj, dict):
        return None
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, object] = {}
    for f in fields(cls):
        key = json_name(f)
        if key not in obj:
            if f.default is MISSING and f.default_factory is MISSING:
                return None
            continue
        ok, parsed = _parse(hints[f.name], obj[key])
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


# --- Turns and history (#48; #51-#53 reuse them) ------------------------------------------------

Channel = Literal["say", "do", "think", "latent", "narration", "message", "audit"]


@dataclass(frozen=True)
class HistoryItem(Model):
    """``HistoryItemModel``: one history item (a beat, a narration, a reply message or an audit
    record) with its position and the operation that recorded it.

    An omniscient read's ``OmniscientHistoryItemModel`` has these fields too (plus
    ``true_ages``), so this parses both. The structured-turn extras (#150) stay raw JSON.
    """

    turn: int | None
    variant: int | None
    beat: int | None
    kind: str
    actor_character_id: str | None
    render_character_ids: list[str] | None
    channel: Channel
    text: str
    operation_id: str
    message_id: str
    origin: Literal["generated", "ingested"]
    detected: Any = None
    effects: Any = None
    labels: Any = None
    propositions: Any = None
    quarantined: bool = False
    redacted: bool = False
    visibility: Any = None


def _history_items(raw: list[Any]) -> list[HistoryItem]:
    parsed = (HistoryItem.from_json(item) for item in raw)
    return [item for item in parsed if item is not None]


@dataclass(frozen=True)
class TurnMessage(Model):
    """``TurnMessageModel``: a turn's reply as history lists it."""

    message_id: str
    text: str


@dataclass(frozen=True)
class TurnSummary(Model):
    """``TurnSummaryModel``: one turn on a history page and its reply (``None`` when reply-less,
    e.g. an ingested player turn)."""

    turn: int
    variant: int | None
    message: TurnMessage | None


@dataclass(frozen=True)
class SummarySegment(Model):
    """``SummarySegmentModel``: a Lite rolling-summary segment the prompt includes now
    (``include_summary=1``, #72): its level and the turns it covers."""

    level: int
    covers_from: int
    covers_to: int
    text: str
    operation_id: str


@dataclass(frozen=True)
class History(Model):
    """``HistoryResponse``: a page of a story's history for one output viewpoint.

    Pages hold whole turns, newest page first; ``items`` are ascending within the page.
    ``items`` (and ``audit``) are raw, because the schema's item is a union (omniscient or
    not); :attr:`entries` and :attr:`audit_entries` parse them. #52 reads every field (the
    issue calls this model ``HistoryResponse``).
    """

    items: list[Any]
    turns: list[TurnSummary]
    output_viewpoint: str
    audience_filter: Literal["none", "co_location", "full"]
    as_of: int
    timeline_revision: int
    next_before_turn: int | None
    audit: list[Any] | None = None
    summary: list[SummarySegment] | None = None

    @property
    def entries(self) -> list[HistoryItem]:
        """The items, in the Engine's order (an item that does not parse is left out)."""
        return _history_items(self.items)

    @property
    def audit_entries(self) -> list[HistoryItem] | None:
        """The audit records (``include_audit=1``), parsed as :attr:`entries`; ``None`` when
        the answer has no ``audit`` (not asked for)."""
        return None if self.audit is None else _history_items(self.audit)


@dataclass(frozen=True)
class ReplyMessage(Model):
    """``MessageBody``: a finished reply's transcript row id and text."""

    message_id: str
    text: str
    mode: Literal["polished", "deterministic"] | None = None


@dataclass(frozen=True)
class RespondResponse(Model):
    """``RespondResponse``: one finished turn (``POST /stories/{id}/respond``'s 200).

    ``beats`` are raw (the schema's item is a union); :attr:`entries` parses them, and
    :attr:`report` is the turn report the inspector shows.
    """

    timeline_revision: int
    message: ReplyMessage
    beats: list[Any]
    turn: int
    variant: int
    output_viewpoint: str
    audience_filter: Literal["none", "co_location", "full"]
    model_calls: list[ModelCall]
    elapsed_ms: int
    degradation: list[str]
    degradation_details: list[DegradationStep]
    guardrail: TurnGuardrail
    call_counts: dict[str, int]
    budget: TurnBudget | None
    fingerprint: str | None
    capabilities: list[str]
    quality_status: QualityStatus | None
    profile: ProfileVersionRef | None
    config_revision: int | None
    idempotency_key: str | None = None
    queued_ms: int | None = None
    first_call_offset_ms: int | None = None
    warnings: list[TurnWarning] = field(default_factory=list)

    @property
    def entries(self) -> list[HistoryItem]:
        return _history_items(self.beats)

    @property
    def report(self) -> TurnReport:
        return TurnReport(
            model_calls=self.model_calls,
            elapsed_ms=self.elapsed_ms,
            call_counts=self.call_counts,
            budget=self.budget,
            degradation=self.degradation,
            degradation_details=self.degradation_details,
            guardrail=self.guardrail,
            queued_ms=self.queued_ms,
            first_call_offset_ms=self.first_call_offset_ms,
            fingerprint=self.fingerprint,
            capabilities=self.capabilities,
            quality_status=self.quality_status,
            profile=self.profile,
            config_revision=self.config_revision,
            warnings=self.warnings,
            timeline_revision=self.timeline_revision,
            turn=self.turn,
            variant=self.variant,
        )


@dataclass(frozen=True)
class QueuedTurn(Model):
    """``QueuedTurn``: a turn the Engine queued (``respond``'s 202); poll
    ``GET /operations/{operation_id}``."""

    operation_id: str
    position: int


@dataclass(frozen=True)
class IngestResult(Model):
    """``IngestedEventBody``: what one ingested event became (its operation and turn), and
    whether the story had recorded it before (``replayed``)."""

    client_event_id: str
    operation_id: str
    turn: int
    replayed: bool


@dataclass(frozen=True)
class IngestResponse(Model):
    """``IngestResponse`` (``POST /stories/{id}/ingest``'s 201, or 200 when any event was
    replayed): one result per event in request order, the story's revision after the call and
    what the input gate spent."""

    timeline_revision: int
    results: list[IngestResult]
    model_calls: list[ModelCall]
    elapsed_ms: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class EffectiveBudget(Model):
    """``EffectiveBudgetBody``: a story's per-turn model-call cap and deadline (seconds), the
    guardrail calls a turn reserves and the smallest cap a request may ask for."""

    max_model_calls_per_turn: int
    source: str
    turn_deadline: float
    reserved: dict[str, int]
    minimum_required: int


@dataclass(frozen=True)
class GuardrailCalls(Model):
    """``GuardrailCallsBody``: the model-assisted guardrail calls a turn makes on each side."""

    input: int
    output: int


@dataclass(frozen=True)
class StorySettings(Model):
    """``StorySettingsBody`` (``GET /stories/{id}/settings``): the story's settings (raw), its
    effective and inactive policies (raw ``EffectivePolicyBody`` items, for #54) and its
    effective budget."""

    settings: dict[str, Any]
    effective_policies: list[Any]
    inactive_policies: list[Any]
    guardrail_calls: GuardrailCalls
    effective_budget: EffectiveBudget

    @property
    def effective(self) -> list[EffectivePolicy]:
        """The effective policies, parsed (an item that does not parse is left out)."""
        return _effective_policies(self.effective_policies)

    @property
    def inactive(self) -> list[EffectivePolicy]:
        """The inactive policies (age condition not met yet), parsed."""
        return _effective_policies(self.inactive_policies)


def _effective_policies(raw: list[Any]) -> list[EffectivePolicy]:
    parsed = (EffectivePolicy.from_json(item) for item in raw)
    return [policy for policy in parsed if policy is not None]


# --- A story's settings, pinned memories and custom instructions (#54) -------------------------


@dataclass(frozen=True)
class StorySettingsWrite(StorySettings):
    """``StorySettingsWriteBody`` (``PATCH /stories/{id}/settings``): the settings after the
    write in the read's shape, the story's ``config_revision`` (moved by a write that changed
    something) and its unchanged ``timeline_revision``."""

    timeline_revision: int
    config_revision: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class PinnedItem(Model):
    """``PinnedItemBody``: one pinned memory, its server-minted id and its text."""

    id: str
    text: str


@dataclass(frozen=True)
class PinnedMemories(Model):
    """``PinnedMemoriesBody``: the story's pinned memories in prompt order, and its revision."""

    items: list[PinnedItem]
    timeline_revision: int


@dataclass(frozen=True)
class PinnedMemoriesWritten(Model):
    """``PinnedMemoriesWritten``: the stored pinned memories after a ``PUT``, the edit's
    operation (``None`` when nothing changed), the story's revision and what the input gate
    spent."""

    timeline_revision: int
    items: list[PinnedItem]
    operation_id: str | None
    model_calls: list[ModelCall]
    elapsed_ms: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class CustomInstructions(Model):
    """``CustomInstructionsBody``: the story's custom instructions (``""`` when none)."""

    text: str
    timeline_revision: int


@dataclass(frozen=True)
class CustomInstructionsWritten(Model):
    """``CustomInstructionsWritten``: the stored instructions after a ``PUT`` (see
    :class:`PinnedMemoriesWritten`)."""

    timeline_revision: int
    text: str
    operation_id: str | None
    model_calls: list[ModelCall]
    elapsed_ms: int
    idempotency_key: str | None = None


# --- Stories (#45; #48's play view and #50's settings page reuse them) -------------------------


@dataclass(frozen=True)
class CatalogSource(Model):
    """``CatalogSourceBody``: the catalog version a story character was created from."""

    catalog_id: str
    version: int


@dataclass(frozen=True)
class StoryCharacter(Model):
    """``CharacterBody``: one cast member as a non-omniscient ``GET /stories/{id}`` shows it.

    An omniscient read's ``OmniscientCharacterBody`` has these fields too, so this parses both.
    """

    id: str
    name: str
    is_player: bool
    role_id: str | None = None
    source: CatalogSource | None = None


@dataclass(frozen=True)
class OmniscientStoryCharacter(Model):
    """``OmniscientCharacterBody``: a cast member with its true age (an omniscient read)."""

    id: str
    name: str
    is_player: bool
    true_age: int
    promoted_to: list[str]
    role_id: str | None = None
    source: CatalogSource | None = None


@dataclass(frozen=True)
class StoryPauseProgress(Model):
    """``StoryPauseProgressBody``: records checked, of how many."""

    done: int
    total: int


@dataclass(frozen=True)
class StoryPause(Model):
    """``StoryPauseBody``: why a ``paused`` story is paused and how far its check has got."""

    job_id: str | None
    cause: str
    progress: StoryPauseProgress


@dataclass(frozen=True)
class DormantChain(Model):
    """``DormantChainBody``: turns a truncation left dormant (restorable)."""

    from_turn: int
    variant: int
    through_turn: int
    turns: int
    truncate_operation_id: str | None


@dataclass(frozen=True)
class ChainRef(Model):
    """``ChainRefBody``: a chain, by the ``{turn, variant}`` of its first turn."""

    turn: int
    variant: int


@dataclass(frozen=True)
class VariantChain(Model):
    """``VariantChainBody``: one variant chain of the story's timeline."""

    turn: int
    variant: int
    membership: Literal["active", "discarded", "dormant", "superseded"]
    tip_turn: int
    turn_count: int
    membership_tx: str | None
    parent: ChainRef | None
    requires_check: bool


@dataclass(frozen=True)
class Variants(Model):
    """``VariantsBody`` (``GET /stories/{id}/variants``): every chain of the story's variant
    tree (#52)."""

    items: list[VariantChain]


# --- Timeline operations (#53) --------------------------------------------------------------------


@dataclass(frozen=True)
class RegenerateResult(RespondResponse):
    """``RegeneratedBody`` (``POST /stories/{id}/regenerate``): the respond body for the new
    variant of the latest turn, plus ``previous_variant`` (now dormant, swap back to it)."""

    previous_variant: int = field(kw_only=True)


@dataclass(frozen=True)
class TruncateResult(Model):
    """``TruncatedBody`` (``POST /stories/{id}/truncate``): the rewind's operation, the new
    ``next_turn_index`` and how many operations became ``discarded``."""

    timeline_revision: int
    operation_id: str
    next_turn_index: int
    to_turn: int
    discarded_operations: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class UntruncateResult(Model):
    """``UntruncatedBody`` (``POST /stories/{id}/untruncate``): the most recent rewind undone.
    ``swapped`` means newer turns had been written over it, so the undo was a swap to the old
    chain (``active_chain``) and the newer turns became dormant."""

    timeline_revision: int
    operation_id: str
    next_turn_index: int
    truncate_operation_id: str
    restored_operations: int
    swapped: bool
    active_chain: ChainRef | None
    dormant_operations: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class SwapResult(Model):
    """``SwappedBody`` (``POST /stories/{id}/swap``): the chain now active. Swapping to the
    active chain writes nothing (``operation_id`` null, revision unchanged)."""

    timeline_revision: int
    operation_id: str | None
    next_turn_index: int
    active_chain: ChainRef
    activated_operations: int
    dormant_operations: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ForkCharacter(Model):
    """``ForkCharacterBody``: a character of the fork and the source character it copies."""

    id: str
    name: str
    source_id: str


@dataclass(frozen=True)
class ForkedFrom(Model):
    """``ForkedFromBody``: the source story, the revision the fork equals, the last turn copied
    and the chain followed (``None``: the active one)."""

    story_id: str
    revision: int
    at_turn: int
    variant_chain: ChainRef | None


@dataclass(frozen=True)
class ForkResult(Model):
    """``ForkedBody`` (``POST /stories/{id}/fork``'s 201): the new story."""

    timeline_revision: int
    story_id: str
    operation_id: str
    next_turn_index: int
    name: str | None
    forked_from: ForkedFrom
    characters: list[ForkCharacter]
    counts: dict[str, int]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class RatingCheckStarted(Model):
    """``RatingCheckStartedBody``: a rating check started (202). A tightening rating change
    answers it (#80), and so do untruncate, swap and fork when turns they would make active must
    be re-checked under the current guardrails first (#81). The story is paused until check
    ``job_id`` ends (``GET /stories/{id}/content-rating/checks/{job_id}``)."""

    timeline_revision: int
    job_id: str
    operation_id: str
    status: str
    config_revision: int
    idempotency_key: str | None = None


# #53's name for the same 202 (a reactivation re-check started).
ReactivationCheck = RatingCheckStarted


@dataclass(frozen=True)
class RatingApplied(Model):
    """``RatingAppliedBody``: a story rating change applied at once (a relaxing one), or the
    rating the story already has (``changed`` false, nothing written). ``rating_version`` is a
    version id (a string), not the integer version."""

    timeline_revision: int
    applied: bool
    changed: bool
    content_rating_id: str
    rating_version: str
    config_revision: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class CheckProgress(Model):
    """``CheckProgressBody``: records checked, of how many (``total`` 0: not counted yet)."""

    done: int
    total: int


@dataclass(frozen=True)
class CheckTarget(Model):
    """``CheckTargetBody``: the rating a check evaluates and the version id it evaluates."""

    rating_id: str
    version: str


@dataclass(frozen=True)
class CheckViolation(Model):
    """``CheckViolationBody``: one record breaking one policy. ``position`` stays raw
    (``{turn, variant, beat}`` or ``{kind, id}``); a record that could not be checked has a
    ``code`` and may lack the policy. Never the text: any other key the Engine adds is dropped
    by the parse, so it can never render."""

    position: dict[str, Any]
    policy_id: str | None
    policy_name: str | None
    code: str | None = None


@dataclass(frozen=True)
class RatingCheck(Model):
    """``RatingCheckBody``: a rating check (#80) or a reactivation re-check (#81), as ``GET
    /stories/{id}/content-rating/checks/{job_id}`` answers it. ``status`` is ``checking`` until
    it ends ``applied``, ``refused``, ``failed`` (or, for ``kind: reactivation``,
    ``reactivated``). ``error`` (``{class, message}``) and ``outcome`` stay raw."""

    kind: str
    job_id: str
    story_id: str
    status: str
    progress: CheckProgress
    started_at: str
    started_by: str
    finished_at: str | None
    target: CheckTarget
    from_rating_id: str
    operation_id: str | None
    rating_version: str | None
    config_revision: int | None
    violations: list[CheckViolation]
    error: dict[str, Any] | None
    outcome: dict[str, Any] | None


@dataclass(frozen=True)
class ScenarioOrigin(Model):
    """``ScenarioOriginBody``: the scenario version a story was created from."""

    id: str
    version: int


@dataclass(frozen=True)
class Story(Model):
    """``StoryBody``: one story as ``GET /stories/{id}`` returns it.

    ``characters`` holds raw items, because the schema's item is a union
    (``OmniscientCharacterBody`` or ``CharacterBody``); :attr:`cast` parses them.
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
    characters: list[Any]
    settings: dict[str, Any]
    engine_profile: ProfileVersionRef
    undo_available: bool
    dormant_chains: list[DormantChain]
    variant_chains: list[VariantChain]
    pause: StoryPause | None = None
    scenario: ScenarioOrigin | None = None

    @property
    def cast(self) -> list[StoryCharacter]:
        """The cast, in the Engine's order (an item that does not parse is left out)."""
        parsed = (StoryCharacter.from_json(item) for item in self.characters)
        return [character for character in parsed if character is not None]


@dataclass(frozen=True)
class CreatedCharacter(Model):
    """``CreatedCharacterBody``: a new story's character id and name."""

    id: str
    name: str
    role_id: str | None = None


@dataclass(frozen=True)
class StoryCreated(Model):
    """``StoryCreatedBody``: what ``POST /stories`` answers.

    ``story_id`` and ``content_rating`` are ``None`` only on a scenario creation's 200
    ``applied: false``; ``report`` and ``impact`` (#75: typed, :class:`CreationReport` and
    :class:`ImpactReport`) come with scenario creations only.
    """

    timeline_revision: int
    story_id: str | None
    characters: list[CreatedCharacter]
    content_rating: StoryRating | None
    applied: bool = True
    idempotency_key: str | None = None
    engine_profile: ProfileVersionRef | None = None
    scenario: ScenarioOrigin | None = None
    report: CreationReport | None = None
    impact: ImpactReport | None = None


@dataclass(frozen=True)
class StoryStatusChange(Model):
    """``StatusBody``: a story's status and revision after an archive or unarchive."""

    timeline_revision: int
    status: StoryStatus
    idempotency_key: str | None = None


@dataclass(frozen=True)
class StoryCharacterAdded(Model):
    """``StoryCharacterBody``: a character added to a running story (#72): its id, name, place
    in the cast (``ordinal``) and the story's revision after the addition."""

    timeline_revision: int
    id: str
    name: str
    ordinal: int
    idempotency_key: str | None = None


# --- Content ratings (#45's select; #49 adds the detail and version models) -------------------


@dataclass(frozen=True)
class ContentRatingSummary(Model):
    """``RatingBody``: one content rating as ``GET /admin/content-ratings`` lists it."""

    id: str
    name: str
    description: str
    display_order: int
    prompt_guidance: str
    image_guidance: str
    policy_ids: list[str]
    inherits_from: list[str]
    version_id: str
    version: int
    created_at: str
    created_by: str
    retired_at: str | None

    @property
    def retired(self) -> bool:
        return self.retired_at is not None


@dataclass(frozen=True)
class ContentRatingList(Model):
    """``RatingList``: content ratings in display order."""

    items: list[ContentRatingSummary]


@dataclass(frozen=True)
class EffectiveGuardrail(Model):
    """``EffectiveGuardrail``: a policy a rating applies, and the rating (itself or one it
    inherits from) that contributes it."""

    policy_id: str
    source_rating_id: str


@dataclass(frozen=True)
class ContentRating(ContentRatingSummary):
    """``RatingDetailBody``: a rating at its latest version, with its effective guardrails."""

    effective_guardrails: list[EffectiveGuardrail]


@dataclass(frozen=True)
class ContentRatingMutation(ContentRatingSummary):
    """``RatingMutation``: a rating after a write, with the deployment's ``config_revision``."""

    config_revision: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class RatingVersion(ContentRatingSummary):
    """One version of a rating, as ``GET /admin/content-ratings/{id}/versions`` lists it (a
    ``RatingBody``)."""


@dataclass(frozen=True)
class RatingVersionList(Model):
    """``RatingList`` as the versions endpoint answers it: one rating's versions."""

    items: list[RatingVersion]


# --- Deployment settings (#45 reads the default rating; #50 edits them) -----------------------


@dataclass(frozen=True)
class DeploymentSettings(Model):
    """``SettingsBody``: every deployment setting, flat, with ``config_revision``.

    ``profile_defaults`` (a nested profile document) and ``feedback_retention`` (an enum or an
    integer) are raw JSON.
    """

    config_revision: int
    default_content_rating_id: str
    audit_retention_days: int = 90
    cascade_threshold: int = 5
    default_engine_profile_id: str = "00000000000000000000PRF001"
    default_safe_fallback: str = "{name} changes the subject."
    embedding_batch_max: int = 64
    embedding_hnsw_ef_construction: int = 64
    embedding_hnsw_ef_search: int = 40
    embedding_hnsw_m: int = 16
    entailment_max_records: int = 30
    erasure_shared_media: Literal["purge", "retire"] = "purge"
    escalation_retention_days: int = 90
    extraction_shadow_retention_days: int = 90
    extraction_shadow_sample_rate: float = 0.1
    feedback_export: bool = False
    feedback_regen_window_s: int = 300
    feedback_retention: Any = "story_lifetime"
    feedback_tags: list[str] = field(default_factory=list)
    feedback_text_max_chars: int = 2000
    gate_promotion_min_agreement: float = 0.98
    gate_promotion_min_cases: int = 200
    idempotency_key: str | None = None
    idempotency_result_window: int = 24
    job_retention_days: int = 7
    listing_status_values: list[str] = field(default_factory=list)
    lite_summary_chunk: int = 10
    max_characters_at_start_ceiling: int | None = None
    max_characters_at_start_default: int = 16
    max_custom_instructions_chars: int = 4000
    max_pinned_memories: int = 50
    max_pinned_per_character: int = 200
    max_pinned_per_story: int = 2000
    media_allowed_types: list[str] = field(default_factory=list)
    media_max_bytes: int = 52428800
    media_moderation_timeout_ms: int = 5000
    media_moderation_webhook_url: str | None = None
    media_multipart_threshold: int = 8388608
    media_signed_url_ttl: int = 300
    media_sweep_interval: int = 3600
    media_upload_ttl: int = 86400
    memory_importance_half_life_turns: int = 200
    minor_age_threshold: int = 18
    neighborhood_max_facts: int = 50
    offboarding_report_retention_days: int = 90
    profile_defaults: Any = None
    protected_index_max: int = 5000
    public_base_url: str | None = None
    rating_check_model_calls_per_record: int = 2
    rating_check_retention_days: int = 30
    readset_change_retention_days: int = 7
    reembed_batch_size: int = 500
    require_primary_image: bool = False
    retrieval_lexical_k: int = 50
    retrieval_rehydrate_max_rows: int = 20
    retrieval_rrf_k: int = 60
    retrieval_vector_k: int = 50
    structured_retry_budget: int = 2
    thunks_max_open: int = 500
    timer_lag_alert: float = 300.0
    turn_queue_max: int = 5
    uncertain_below: float = 0.5
    validation_model_calls: int = 0
    worker_heartbeat_timeout: float = 60.0


# --- Engine operations panel (#50): rebuild and configuration changes --------------------------


@dataclass(frozen=True)
class RebuildResult(Model):
    """``RebuildResponse``: rows written per projection table (Engine order) and the
    ``maintenance`` operation that wrote them."""

    timeline_revision: int
    counts: dict[str, int]
    operation_id: str
    idempotency_key: str | None = None


@dataclass(frozen=True)
class RebuildQueued(Model):
    """``RebuildQueuedResponse`` (202): a large story's rebuild, handed to a worker job. The
    Engine has no status read for that job."""

    timeline_revision: int
    job_id: str
    idempotency_key: str | None = None


ConfigClass = Literal["interpretive", "prospective"]
CONFIG_CLASSES: tuple[ConfigClass, ...] = typing.get_args(ConfigClass)


@dataclass(frozen=True)
class ConfigChange(Model):
    """``ConfigChangeItem``: one changed setting, the revision it produced, its old and new
    values (raw JSON, secrets redacted by the Engine), who made it (``actor``, verbatim) and
    when. ``change_class`` is the JSON ``class``."""

    config_revision: int
    setting: str
    change_class: ConfigClass = field(metadata={JSON_NAME: "class"})
    old: Any
    new: Any
    actor: str
    at: str


@dataclass(frozen=True)
class ConfigChanges(Model):
    """``ConfigChangesBody``: one page, newest first, and the cursor to the next (``after``)."""

    items: list[ConfigChange]
    next: str | None


# --- Model configuration (#46) -----------------------------------------------------------------

ConfigSource = Literal["own", "inherited", "default"]


@dataclass(frozen=True)
class ConfigSources(Model):
    """``Sources``: where each effective value came from (``params`` per key). ``inherited``
    is the purpose's parent row (``lite_summary`` → ``consolidation``), ``default`` the
    ``default`` row; a param no row sets has no key."""

    base_url: ConfigSource
    model: ConfigSource
    api_key: ConfigSource
    params: dict[str, ConfigSource]


@dataclass(frozen=True)
class ModelConfigEntry(Model):
    """``ModelConfigEntry``: one purpose's effective configuration.

    ``api_key`` is the Engine's mask (``"***"``) when a key applies, else ``None``; the key
    itself is never returned. ``params`` holds the effective values as JSON (the Engine sends
    only the keys some row sets).
    """

    base_url: str | None
    model: str | None
    params: dict[str, Any]
    api_key: str | None
    api_key_fingerprint: str | None
    sources: ConfigSources

    @property
    def api_key_set(self) -> bool:
        return self.api_key is not None


@dataclass(frozen=True)
class ModelConfig(Model):
    """``ModelConfigBody``: the adapter, ``config_revision`` and every purpose in the
    Engine's order (``default`` included)."""

    adapter: Literal["openai", "stub"]
    config_revision: int
    purposes: dict[str, ModelConfigEntry]


@dataclass(frozen=True)
class ModelConfigItem(ModelConfigEntry):
    """``ModelConfigItem``: one purpose with the ``config_revision`` it was read at."""

    purpose: str
    config_revision: int


@dataclass(frozen=True)
class ModelConfigMutation(ModelConfigItem):
    """``ModelConfigMutation``: one purpose after a write, with the revision it produced."""

    idempotency_key: str | None = None


# --- Guardrail policies (#47; #54 adds story and character policies) ---------------------------

# ``Scope-Output`` (the ``scope`` property's ``$ref``; the component was renamed once, #47).
PolicyScope = Literal["global", "rating", "story", "character"]
PolicyKind = Literal["deterministic", "model_assisted"]


@dataclass(frozen=True)
class EffectivePolicy(Model):
    """``EffectivePolicyBody``: a policy that applies to a story (or would, once its age
    condition holds), as ``GET /stories/{id}/settings`` lists it (#52's audit names; #54).

    ``name`` is ``None`` while a catalog erasure reaching its text is pending (still
    enforced, Engine ADR-161). ``scope`` and ``applies_to`` stay raw JSON."""

    id: str
    name: str | None
    scope: Any
    source: str
    applies_to: list[Any]
    age_condition: str | None
    kind: PolicyKind


# ``Target``: what a policy applies to. ``image_*`` targets are used from M8.
PolicyTarget = Literal["text_input", "text_output", "image_prompt", "image_output"]
# ``PolicyFields.age_condition``'s one non-null value (bodies return it as a plain string).
AgeCondition = Literal["minor_involved_or_present"]
POLICY_SCOPES: tuple[PolicyScope, ...] = typing.get_args(PolicyScope)
POLICY_TARGETS: tuple[PolicyTarget, ...] = typing.get_args(PolicyTarget)
AGE_CONDITIONS: tuple[AgeCondition, ...] = typing.get_args(AgeCondition)


@dataclass(frozen=True)
class Policy(Model):
    """``PolicyBody``: a policy at its current version.

    ``rule`` is the raw rule object (``type`` plus ``words`` / ``patterns`` / ``names`` /
    ``prompt`` and ``on_uncertain``); the form layer parses it per type. ``scope_id`` is the
    rating (rating scope), the story or the character; ``None`` for a global policy.
    """

    id: str
    scope: PolicyScope
    scope_id: str | None
    story_id: str | None
    name: str
    applies_to: list[PolicyTarget]
    kind: PolicyKind
    effect: str
    rule: dict[str, Any]
    age_condition: str | None
    message: str
    version: int
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str
    retired_at: str | None

    @property
    def rule_type(self) -> str:
        """The rule's ``type`` (``wordlist``, ``regex``, ``entity_list``, ``model_assisted``)."""
        value = self.rule.get("type")
        return value if isinstance(value, str) else ""

    @property
    def retired(self) -> bool:
        return self.retired_at is not None

    @property
    def seeded(self) -> bool:
        """Created by the Engine itself (a seed migration), not by an operator."""
        return self.created_by == "system"


@dataclass(frozen=True)
class PolicyList(Model):
    """``PolicyList``: the policies a ``GET /admin/policies`` asked for."""

    items: list[Policy]


@dataclass(frozen=True)
class PolicyMutation(Policy):
    """``PolicyMutation``: a policy after a write, with the deployment's ``config_revision``."""

    config_revision: int
    idempotency_key: str | None = None


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


# --- Validation reports (#64, M3 convention 4) ---------------------------------------------------
#
# Shared by every M3 page that shows what the Engine found: a publish (its 201 body, or a 422
# ``publish_invalid``'s ``details.report``), a scenario validation and a story creation (#75's
# ``CreationReport`` is built from :class:`ReportEntry`). Render one with ``{% include
# "console/_validation_report.html" with report=… label="Companion" anchor_base="" %}``.

EntryOutcome = Literal["ignored", "dropped", "kept", "deferred", "inactive", "rejected"]


@dataclass(frozen=True)
class EntryLocation(Model):
    """``EntryLocation`` (and ``ElementLocation``, which parses into the same class): the catalog
    ``entry`` and its ``version`` (``"draft"`` for a draft) and, where they apply, the role id,
    the dotted ``field`` path (``cast.roles[1].seeded_memories[0].audience``), the seed's id and
    the media id (``ElementLocation`` only)."""

    entry: str
    version: int | Literal["draft"]
    field: str | None = None
    role: str | None = None
    seed_id: str | None = None
    media_id: str | None = None


@dataclass(frozen=True)
class ReportEntry(Model):
    """The Engine's ``Entry``: one finding, its stable ``code``, ``location``, ``outcome``
    (``rejected`` for an error) and, when there is one, the ``rule`` applied, the ``source`` it
    names and the exact ``text`` affected."""

    code: str
    location: EntryLocation
    outcome: EntryOutcome
    rule: str | None = None
    source: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class ValidationReport(Model):
    """``ValidationReport``: ``errors`` (each ``rejected``; any one refuses a publish),
    ``warnings`` (every other outcome; never blocking) and ``unavailable`` (the checks this
    deployment cannot run)."""

    errors: list[ReportEntry] = field(default_factory=list)
    warnings: list[ReportEntry] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.errors or self.warnings or self.unavailable)


@dataclass(frozen=True)
class Promotion(Model):
    """``PromotionBody``: a story character promoted to a new **draft** catalog character
    (#72): its ``catalog_id`` and draft revision, the warnings (``definition_truncated``, an
    image left out as ``media_retired``) and the story's unchanged ``timeline_revision``."""

    timeline_revision: int
    catalog_id: str
    draft_revision: int
    warnings: list[ReportEntry]
    idempotency_key: str | None = None


# --- Catalog characters (#65) --------------------------------------------------------------------
#
# A card (``CharacterCard``) is the public projection every list and ``projection=card`` read
# answers; ``CatalogCharacter`` is ``CharacterFull`` (``projection=full``), private fields
# included. ``CardImage`` and ``CardRating`` are shared with the scenario pages (#68).

OPEN_ENUM = "open_enum"
"""Field metadata: a ``str`` field the schema types as a string enum. Read as a plain string so
a value the Console does not know yet renders raw (#64 convention 17) instead of failing the
whole answer; the contract test compares it with the enum's members."""

EntryStatus = Literal["draft", "published", "archived"]


def open_enum(**kwargs: Any) -> Any:
    """``field(...)`` for an :data:`OPEN_ENUM` string."""
    return field(metadata={OPEN_ENUM: True}, **kwargs)


@dataclass(frozen=True)
class CardImage(Model):
    """``CardImage``: a public image of a card, by media id."""

    media_id: str
    alt_text: str | None = None


@dataclass(frozen=True)
class CardRating(Model):
    """``CardRating``: a catalog entry's content rating, the version it pins and its name."""

    id: str
    version_id: str
    name: str


@dataclass(frozen=True)
class CardImages(Model):
    """``CharacterCardImages``: the primary image and the gallery, in order."""

    primary: CardImage | None = None
    gallery: list[CardImage] = field(default_factory=list)


@dataclass(frozen=True)
class CatalogCharacterCard(Model):
    """``CharacterCard``: a character's public card. ``version`` is ``None`` on a never-published
    draft."""

    id: str
    version: int | None
    name: str | None
    public_description: str | None
    images: CardImages
    labels: list[str]
    content_rating: CardRating | None
    listing_status: str
    status: EntryStatus


@dataclass(frozen=True)
class CatalogCharacterListItem(CatalogCharacterCard):
    """``CharacterListItem``: a card as ``GET /catalog/characters`` lists it, with its owner, its
    last change and (when searching with ``q``) its ``rank``."""

    owner_principal: str
    updated_at: str
    rank: float | None = None


@dataclass(frozen=True)
class CatalogCharacterPage(Model):
    """``CharacterListBody``: one page of cards; ``next_cursor`` reads the next."""

    items: list[CatalogCharacterListItem]
    next_cursor: str | None


@dataclass(frozen=True)
class Disposition(Model):
    """One disposition axis (``WarmthDisposition`` and the four others): its five-step
    ``setting`` and free-text ``nuance``."""

    setting: str = open_enum()
    nuance: str | None = None


@dataclass(frozen=True)
class Dispositions(Model):
    """``Dispositions``: the five axes, each missing until set (a publish needs all five)."""

    threat_response: Disposition | None = None
    dominance: Disposition | None = None
    honesty: Disposition | None = None
    warmth: Disposition | None = None
    caution: Disposition | None = None


@dataclass(frozen=True)
class InteractionStyle(Model):
    speech_style: str | None = None
    vocabulary: str | None = None
    mannerisms: list[str] = field(default_factory=list)
    sample_dialogue: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LikesDislikes(Model):
    likes: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CharacterDefinition(Model):
    """``CharacterDefinition``: the private definition (never on a card)."""

    true_age: int | None = None
    appearance: str | None = None
    personality: str | None = None
    values_motivations: str | None = None
    background_flavor: str | None = None
    likes_dislikes: LikesDislikes | None = None
    dispositions: Dispositions | None = None
    interaction_style: InteractionStyle | None = None
    deflection_style: str | None = None
    safe_fallback_text: str | None = None


@dataclass(frozen=True)
class CharacterImage(Model):
    """``CharacterImageDetail`` (and ``ReferenceImageDetail``, which adds ``audience``): an
    image with what the full projection says about it."""

    media_id: str
    alt_text: str | None = None
    caption: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    generation_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReferenceImage(CharacterImage):
    """``ReferenceImageDetail``: a visual-identity reference image and its ``audience``
    (``perceivable`` or ``restricted``)."""

    audience: str | None = open_enum(default=None)


@dataclass(frozen=True)
class CharacterImages(Model):
    """``CharacterImagesDetail``: the primary image and the gallery, in full."""

    primary: CharacterImage | None = None
    gallery: list[CharacterImage] = field(default_factory=list)


@dataclass(frozen=True)
class VisualIdentity(Model):
    """``VisualIdentityDetail``: what image generation uses for the character."""

    base_appearance_prompt: str | None = None
    negative_prompt: str | None = None
    restricted_details_prompt: str | None = None
    preferred_image_model: str | None = None
    seed: int | None = None
    adapter_params: dict[str, Any] | None = None
    reference_images: list[ReferenceImage] = field(default_factory=list)


@dataclass(frozen=True)
class CatalogCharacter(Model):
    """``CharacterFull``: a character as ``projection=full`` gives it, private fields included.

    ``version`` is the version read (``None`` for a draft), ``current_version`` the latest
    published one (``None`` before the first publish); ``draft_revision`` is the draft's save
    counter (only on a draft read; not comparable to ``version``).
    """

    id: str
    status: EntryStatus
    listing_status: str
    owner_principal: str
    version: int | None
    current_version: int | None
    created_at: str
    updated_at: str
    published_at: str | None
    companion_id: str | None
    name: str | None
    public_description: str | None
    images: CharacterImages
    labels: list[str]
    content_rating: CardRating | None
    aliases: list[str]
    definition: CharacterDefinition
    visual_identity: VisualIdentity | None
    draft_revision: int | None = None


@dataclass(frozen=True)
class CatalogVersion(Model):
    """``CharacterVersionBody``: one published version."""

    version: int
    published_at: str
    published_by: str


@dataclass(frozen=True)
class CatalogVersionList(Model):
    """``CharacterVersionsBody``: every published version (the Engine lists them oldest
    first)."""

    items: list[CatalogVersion]


# --- Catalog character writes (#66) --------------------------------------------------------------


@dataclass(frozen=True)
class CatalogCharacterDraft(CatalogCharacter):
    """``CharacterDraftBody``: the draft after a create or a save, in ``full``.
    ``draft_revision`` is the revision the next save or publish sends; ``config_revision`` the
    deployment's (unchanged by the write)."""

    draft_revision: int = field()  # required here (no default), optional on a read
    config_revision: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class CompanionPublished(Model):
    """``CompanionPublishedBody``: the companion version a character's publish made (always one:
    its draft with ``with_companion``, else its latest content re-pinned), its draft's new
    revision and that version's validation report."""

    id: str
    version: int
    draft_revision: int
    report: ValidationReport


@dataclass(frozen=True)
class CharacterPublished(Model):
    """``CharacterPublishedBody``: the version a publish made, the draft's new revision, the
    report (no errors; warnings and the checks the deployment cannot run) and the companion
    version made with it."""

    id: str
    version: int
    draft_revision: int
    status: EntryStatus
    published_at: str
    report: ValidationReport
    companion: CompanionPublished
    config_revision: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class CompanionDraft(Model):
    """The part of ``GET /catalog/characters/{id}/companion?draft=true&projection=full``
    (``ScenarioFull``) a character's publish needs: the companion scenario's id, status, versions
    and its draft's ``draft_revision``. Only these fields of the scenario are read (the scenario
    pages' full model is #68's); the contract test compares each with ``ScenarioFull``."""

    id: str
    status: EntryStatus
    version: int | None
    current_version: int | None
    companion_of: str | None
    draft_revision: int | None = None


# --- personas (#67) -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Persona(Model):
    """``Persona``: a principal's saved player identity (not a catalog entry). A story keeps a
    snapshot of the persona it was created with, so later edits never reach a story."""

    id: str
    owner_principal: str
    name: str
    gender: str | None
    age: int
    description: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PersonaWritten(Persona):
    """``PersonaBody``: the persona after a create or an edit, with the deployment's
    ``config_revision`` (unchanged) and the call's ``idempotency_key``."""

    config_revision: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class PersonaPage(Model):
    """``PersonaListBody``: one page of personas, newest first; ``next_cursor`` reads the next."""

    items: list[Persona]
    next_cursor: str | None


# --- catalog scenarios (#68) ----------------------------------------------------------------------
#
# A card (``ScenarioCard``) is what lists and ``projection=card`` answer; ``Scenario`` is
# ``ScenarioFull``. The groups #68 edits, the card's media (#71), the cast and the world seeds
# (#73) are typed; the sections Lite stores but does not use yet (``starting_state``'s
# states, clock, scheduled events, predicates, closed sets and rules; planned reveals) stay raw
# JSON, which the pages show and edit as JSON.

ScenarioRoleKind = Literal["npc", "player"]


@dataclass(frozen=True)
class ScenarioCardMedia(Model):
    """``ScenarioCardMedia``: one public image or video of a scenario card."""

    media_id: str
    alt_text: str | None
    caption: str | None


@dataclass(frozen=True)
class ScenarioCardMediaSet(Model):
    """``ScenarioCardMediaSet``: the card's cover, gallery and videos."""

    cover: ScenarioCardMedia | None
    gallery: list[ScenarioCardMedia]
    videos: list[ScenarioCardMedia]


@dataclass(frozen=True)
class ScenarioCardRole(Model):
    """``ScenarioCardRole``: a role as the public card shows it."""

    id: str
    public_label: str
    setup_description: str
    kind: str = open_enum()
    fill: str = open_enum()


@dataclass(frozen=True)
class ScenarioCard(Model):
    """``ScenarioCard``: a scenario's public card. ``version`` is ``None`` on a never-published
    draft; ``companion_of`` names the catalog character a companion scenario belongs to."""

    id: str
    version: int | None
    listing_status: str
    status: EntryStatus
    companion_of: str | None
    name: str | None
    public_description: str | None
    media: ScenarioCardMediaSet
    labels: list[str]
    content_rating: CardRating | None
    roles: list[ScenarioCardRole]


@dataclass(frozen=True)
class ScenarioListItem(ScenarioCard):
    """``ScenarioListItem``: a card as ``GET /catalog/scenarios`` lists it, with its owner, its
    last change and (when searching) its ``rank``."""

    owner_principal: str
    updated_at: str
    rank: float | None = None


@dataclass(frozen=True)
class ScenarioPage(Model):
    """``ScenarioListBody``: one page of cards; ``next_cursor`` reads the next."""

    items: list[ScenarioListItem]
    next_cursor: str | None


@dataclass(frozen=True)
class ScenarioVideo(Model):
    """``ScenarioVideo`` (#71): a video on the card and the caption the scenario shows with it
    (``None``: the asset's own caption shows)."""

    media_id: str
    caption: str | None = None


@dataclass(frozen=True)
class ScenarioMediaSet(Model):
    """``ScenarioMedia`` (#71): the card's cover image, gallery images and videos, by media id."""

    cover: str | None = None
    gallery: list[str] = field(default_factory=list)
    videos: list[ScenarioVideo] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioPublicCard(Model):
    """``ScenarioPublicCard``: name, public description and the media (#71)."""

    name: str | None = None
    public_description: str | None = None
    media: ScenarioMediaSet = field(default_factory=ScenarioMediaSet)


@dataclass(frozen=True)
class ScenarioMetadata(Model):
    labels: list[str] = field(default_factory=list)
    content_rating_id: str | None = None


@dataclass(frozen=True)
class Location(Model):
    """``ScenarioLocation``: a named place roles and the opening start at."""

    id: str
    name: str
    description: str | None = None


@dataclass(frozen=True)
class Entity(Model):
    """``ScenarioEntity``: a named entity with its aliases and, optionally, its ``kind``
    (without one it is ``other``)."""

    id: str
    name: str
    kind: str | None = open_enum(default=None)
    aliases: list[str] = field(default_factory=list)
    description: str | None = None


@dataclass(frozen=True)
class World(Model):
    """``ScenarioWorld``: the world's description (setting and lore), named locations, named
    entities and the story's own event types."""

    description: str | None = None
    locations: list[Location] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    event_types: list[str] | None = None


@dataclass(frozen=True)
class Opening(Model):
    """``ScenarioOpening``: the opening narration, starting location and roles present."""

    narration: str | None = None
    starting_location: str | None = None
    roles_present: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Seed(Model):
    """``Seed`` (#73): a seeded item, a role's or the world's (``starting_state``). ``id`` is
    the Engine's, assigned when a save stores the seed (sent back to keep it); holder, audience
    and disclosure may be unset on a draft (publishing needs all three)."""

    text: str
    id: str | None = None
    holder: str | None = None
    audience: list[str] | None = None
    disclosure: str | None = open_enum(default=None)
    kind: str | None = open_enum(default=None)
    valid_time: dict[str, Any] | None = None


@dataclass(frozen=True)
class StartingState(Model):
    """``ScenarioStartingState``: the opening, the world seeds (#73) and the sections Lite
    stores but does not use yet, raw."""

    opening: Opening = field(default_factory=Opening)
    seeded_memories: list[Seed] = field(default_factory=list)
    states: list[dict[str, Any]] = field(default_factory=list)
    clock: dict[str, Any] | None = None
    scheduled_events: list[dict[str, Any]] = field(default_factory=list)
    predicates: list[dict[str, Any]] = field(default_factory=list)
    closed_sets: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Drivers(Model):
    """``ScenarioDrivers``: what Lite puts in the prompt as author notes, and the planned reveals
    (raw)."""

    director_guidance: str | None = None
    plot_hooks: list[str] = field(default_factory=list)
    objectives: list[str] = field(default_factory=list)
    milestones: list[str] = field(default_factory=list)
    endings: list[str] = field(default_factory=list)
    planned_reveals: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioPolicy(Model):
    """``ScenarioPolicy``: a story policy a story made from the scenario starts with (no id,
    scope or kind). ``rule`` is the raw rule object (``type`` plus its entries)."""

    name: str
    rule: Any
    message: str
    applies_to: list[PolicyTarget] | None = None
    age_condition: AgeCondition | None = None


@dataclass(frozen=True)
class StorySettingsDefaults(Model):
    """``StorySettingsDefaults``: the settings a story made from the scenario starts with
    (``None``: the deployment's default), its policies and the providers it requires."""

    allow_new_arrivals: bool | None = None
    ambiguous_retcon_timing: str | None = open_enum(default=None)
    audit_retention_days: int | None = None
    backfill_conflict_policy: str | None = open_enum(default=None)
    default_output_viewpoint: str | None = None
    interaction_mode: str | None = open_enum(default=None)
    player_authority: str | None = open_enum(default=None)
    turn_concurrency: str | None = open_enum(default=None)
    policies: list[ScenarioPolicy] = field(default_factory=list)
    required_providers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Recommendation(Model):
    """``ScenarioRecommendation``: the engine profile and capabilities the scenario
    recommends."""

    engine_profile_id: str | None = None
    capabilities: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VisualStyle(Model):
    """``VisualStyle``: the art-style prompts every image of the scenario uses."""

    prompt: str | None = None
    negative_prompt: str | None = None


@dataclass(frozen=True)
class CreationRules(Model):
    """``CreationRules``: the dotted paths a story creator may override, and ``bounds`` (raw)."""

    overridable: list[str] = field(default_factory=list)
    bounds: dict[str, Any] | None = None


@dataclass(frozen=True)
class RoleCharacter(Model):
    """``RoleCharacter`` (#73): the catalog character a ``default`` or ``locked`` role is filled
    with, and its version (``None``: the latest published when a story is created)."""

    catalog_id: str
    version: int | None = None


@dataclass(frozen=True)
class RoleEligibility(Model):
    """``RoleEligibility`` (#73): who may fill a role. ``age_range`` is ``[min, max]`` (either
    end ``None``), raw: the schema types it as a two-item tuple."""

    labels: list[str] = field(default_factory=list)
    content_ratings: list[str] = field(default_factory=list)
    age_range: Any = None


@dataclass(frozen=True)
class RoleRelationship(Model):
    """``RoleRelationship`` (#73): the role's relationship ``name`` to another role."""

    to_role: str
    name: str


@dataclass(frozen=True)
class ScenarioRole(Model):
    """``ScenarioRole`` (#73): a character slot, its fill (``open``, ``default`` or ``locked``,
    the last two naming ``character``), who may fill it, its seeds and starting facts.
    ``name_overrides`` is raw (``NameOverrides``; edited as JSON)."""

    id: str
    public_label: str
    setup_description: str
    kind: str = open_enum()
    fill: str = open_enum(default="open")
    required: bool = True
    character: RoleCharacter | None = None
    eligibility: RoleEligibility | None = None
    starting_location: str | None = None
    participation: str | None = open_enum(default=None)
    seeded_memories: list[Seed] = field(default_factory=list)
    relationships: list[RoleRelationship] = field(default_factory=list)
    name_overrides: Any = None
    state_initial_values: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScenarioCast(Model):
    """``ScenarioCast`` (#73): the roles and the starting-cast limit (``None``: the
    deployment's default)."""

    max_characters_at_start: int | None = None
    roles: list[ScenarioRole] = field(default_factory=list)


@dataclass(frozen=True)
class Scenario(Model):
    """``ScenarioFull``: a scenario as ``projection=full`` gives it. ``version`` is the version
    read (``None`` for a draft), ``current_version`` the latest published one;
    ``draft_revision`` is the draft's save counter (on a draft read)."""

    id: str
    status: EntryStatus
    listing_status: str
    owner_principal: str
    version: int | None
    current_version: int | None
    created_at: str
    updated_at: str
    published_at: str | None
    companion_of: str | None
    pinned_character_version: int | None
    schema_version: Literal[1]
    public_card: ScenarioPublicCard
    metadata: ScenarioMetadata
    world: World
    cast: ScenarioCast
    starting_state: StartingState
    drivers: Drivers
    story_settings_defaults: StorySettingsDefaults
    recommendation: Recommendation
    visual_style: VisualStyle
    creation_rules: CreationRules
    draft_revision: int | None = None


@dataclass(frozen=True)
class ScenarioDraft(Scenario):
    """``ScenarioDraftBody``: the draft after a create or a save, in ``full``;
    ``draft_revision`` is what the next save sends."""

    draft_revision: int = field()  # required here, and never null
    config_revision: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ValidationResult(Model):
    """``ValidationResult`` (#74): a scenario validation's answer, a dry run that writes
    nothing. ``valid`` is true when the report has no error."""

    valid: bool
    report: ValidationReport


@dataclass(frozen=True)
class ScenarioPublished(Model):
    """``ScenarioPublishedBody`` (#74): the version a scenario's publish made, the draft's new
    revision (the draft still equals what was published), its status and the validation report
    the version keeps (no errors; its warnings and the checks the deployment cannot run)."""

    config_revision: int
    id: str
    version: int
    draft_revision: int
    status: EntryStatus
    published_at: str
    report: ValidationReport
    idempotency_key: str | None = None


@dataclass(frozen=True)
class EngineProfile(Model):
    """``EngineProfile`` (#78): a profile at one version. ``version`` is the one read (the
    latest unless the read named another), ``current_version`` the latest. ``settings`` is what
    that version sets, raw (the profile schema, :class:`ProfileSchema`, describes it; a setting
    left out takes the default). A deleted profile still reads, with its ``deleted_at``.

    ``settings`` is compared with an object schema of any keys: the recorded schema spells out
    the modules delivered when it was recorded, and the Console shows whatever this deployment
    delivers (the contract test widens it)."""

    id: str
    name: str
    owner_principal: str
    preset: bool
    version: int
    current_version: int
    settings: dict[str, Any]
    created_at: str
    updated_at: str
    deleted_at: str | None


@dataclass(frozen=True)
class EngineProfilePage(Model):
    """``ProfileListBody``: a page of profiles at their latest version, newest first."""

    items: list[EngineProfile]
    next_cursor: str | None


@dataclass(frozen=True)
class ProfileVersion(Model):
    """``ProfileVersion``: one version of a profile, when it was made and by whom."""

    version: int
    created_at: str
    created_by: str


@dataclass(frozen=True)
class ProfileVersionList(Model):
    """``ProfileVersionsBody``: every version of a profile, oldest first."""

    items: list[ProfileVersion]


@dataclass(frozen=True)
class SettingTags(Model):
    """``SettingTagsBody``: one setting's three tags: ``behavioral``/``data_producing``,
    ``interpretive``/``prospective`` and quality ``relevant``/``irrelevant``. Plain strings, so a
    tag the Console does not know renders raw."""

    tag: str = field(metadata={OPEN_ENUM: True})
    class_: str = field(metadata={JSON_NAME: "class", OPEN_ENUM: True})
    quality: str = field(metadata={OPEN_ENUM: True})


@dataclass(frozen=True)
class ProfileSchema(Model):
    """``ProfileSchemaBody`` (#78): what a profile may set on this deployment. ``schema`` is the
    JSON Schema of the delivered modules (raw: the Console walks it, ``console.profile_schema``),
    ``tags`` each delivered setting's tags by dotted path, ``delivered`` each delivered module
    with its levels, and ``purposes`` the model purposes ``models.purposes`` may route."""

    schema: dict[str, Any]
    tags: dict[str, SettingTags]
    delivered: dict[str, list[str]]
    purposes: list[str]


# --- catalog lifecycle and listing status (#69) ---------------------------------------------------


@dataclass(frozen=True)
class CatalogCharacterStatus(Model):
    """``CharacterStatusBody``: a character after an archive or unarchive (a no-op answers the
    same), with its companion scenario, which is archived and unarchived with it."""

    config_revision: int
    id: str
    status: EntryStatus
    listing_status: str
    current_version: int | None
    created_at: str
    updated_at: str
    published_at: str | None
    companion_id: str | None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ScenarioStatus(Model):
    """``ScenarioStatusBody``: a scenario after an archive or unarchive; ``companion_of`` names
    the character a companion follows (``None`` for an authored scenario)."""

    config_revision: int
    id: str
    status: EntryStatus
    listing_status: str
    current_version: int | None
    created_at: str
    updated_at: str
    published_at: str | None
    companion_of: str | None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ListingStatus(Model):
    """``ListingStatusBody``: an entry's listing status after a ``PUT …/listing-status`` (one of
    the deployment's values; Epistrel never interprets it). ``config_revision`` is unchanged."""

    config_revision: int
    id: str
    listing_status: str
    idempotency_key: str | None = None


# --- media (#70) ----------------------------------------------------------------------------------

SubjectKind = Literal["character", "catalog_character", "persona"]
MediaKind = Literal["image", "video"]


@dataclass(frozen=True)
class SubjectRef(Model):
    """``SubjectRef`` / ``DepictedSubject``: a story ``character``, a ``catalog_character`` or a
    ``persona``, by id. One model for an asset's ``depicted_subjects`` and erasure's subject
    (#86)."""

    kind: SubjectKind
    id: str

    def to_json(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}


@dataclass(frozen=True)
class MediaAsset(Model):
    """``MediaAsset``: an asset's metadata. ``width``/``height``/``duration_ms`` are ``None`` when
    no probe could read them; ``state`` is ``live``, ``retired`` or (M8) ``candidate``, read as
    an open string so a new state renders raw; ``poster_media_id`` names a video's poster."""

    id: str
    owner_principal: str
    kind: MediaKind
    mime_type: str
    size: int
    width: int | None
    height: int | None
    duration_ms: int | None
    checksum: str
    content_rating_id: str | None
    content_rating_version_id: str
    alt_text: str | None
    caption: str | None
    generation_metadata: dict[str, Any] | None
    depicted_subjects: list[SubjectRef]
    created_at: str
    created_by: str
    poster_media_id: str | None
    # Open enums (``open_enum()``, spelled out so a subclass may add required fields).
    source: str = field(metadata={OPEN_ENUM: True})
    state: str = field(metadata={OPEN_ENUM: True})


@dataclass(frozen=True)
class MediaUploaded(MediaAsset):
    """``MediaUploadedBody``: the asset an upload stored (or the existing one for identical
    bytes), with the deployment's ``config_revision`` (unchanged) and the call's key."""

    config_revision: int
    idempotency_key: str | None = None


@dataclass(frozen=True)
class MediaPage(Model):
    """``MediaListBody``: one page of assets, newest first; ``next_cursor`` reads the next."""

    items: list[MediaAsset]
    next_cursor: str | None


# --- story creation from a scenario (#75) ---------------------------------------------------------
#
# What ``POST /stories`` answers for a ``StoryFromScenario``: the creation report (#119, #122: the
# cast as created, the entries, what Lite deferred or left inactive, and a structured story's
# materialization, #149) and the engine profile's impact report (#109). The report's entries are
# the shared :class:`ReportEntry` (#64 convention 4).

FieldClass = Literal[
    "public_card",
    "knowledge_seed",
    "story_rule",
    "starting_state",
    "director",
    "configuration",
    "recommendation",
    "style",
]
MaterializedOutcome = Literal["canon", "memory", "deferred", "merge_conflict", "entity", "rule"]


@dataclass(frozen=True)
class ClassCapabilityCounts(Model):
    """``ClassCapabilityCounts``: items counted by field class and by the capability needed."""

    by_class: dict[str, int] = field(default_factory=dict)
    by_capability: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class OutcomeCounts(Model):
    """``OutcomeCounts``: a structured creation's materialized items by outcome."""

    by_outcome: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CreationCounts(Model):
    """``CreationCounts``: the report's totals (every projection carries them)."""

    errors: int = 0
    conflicts: int = 0
    warnings: int = 0
    deferred: ClassCapabilityCounts = field(default_factory=ClassCapabilityCounts)
    inactive: ClassCapabilityCounts = field(default_factory=ClassCapabilityCounts)
    materialized: OutcomeCounts | None = None


@dataclass(frozen=True)
class DeferredItem(Model):
    """``DeferredItem``: scenario content the story's profile cannot use yet, kept for the
    capability it needs (``payload``: the content, raw)."""

    field_ref: str
    location: EntryLocation
    capability_needed: str
    payload: dict[str, Any]
    field_class: str = open_enum()


@dataclass(frozen=True)
class InactiveItem(Model):
    """``InactiveItem``: a setting kept but inactive under the story's profile (``value``
    raw)."""

    name: str
    value: Any
    capability: str
    location: EntryLocation
    field_class: str = open_enum()


@dataclass(frozen=True)
class MaterializedEntry(Model):
    """``MaterializedEntry``: one item a structured creation materialized and its outcomes."""

    field_ref: str
    location: EntryLocation
    outcomes: list[MaterializedOutcome]
    rule: str | None = None
    winner: str | None = None
    loser: str | None = None


@dataclass(frozen=True)
class CastSource(Model):
    """``CastSource``: the catalog character and version a cast member was copied from."""

    catalog_id: str
    version: int


@dataclass(frozen=True)
class CatalogCastCard(Model):
    """``CatalogCastCard``: a cast member filled from the catalog, with its card."""

    id: str
    version: int | None
    name: str | None
    public_description: str | None
    images: CardImages
    labels: list[str]
    content_rating: CardRating | None
    listing_status: str
    status: EntryStatus
    character_id: str
    role_id: str
    source: CastSource
    kind: Literal["catalog"] = "catalog"


@dataclass(frozen=True)
class MemberCastCard(Model):
    """``MemberCastCard``: an ad-hoc cast member or a player."""

    kind: Literal["ad_hoc", "player"]
    character_id: str
    role_id: str
    name: str


@dataclass(frozen=True)
class CreationReport(Model):
    """``CreationReport``: what a creation from a scenario applied. The ``card`` projection
    carries ``counts`` and ``unavailable`` only; ``full`` (what the Console always asks for)
    every list. ``errors``/``warnings``/``unavailable`` make it render through the shared
    ``_validation_report.html`` like a :class:`ValidationReport`."""

    counts: CreationCounts
    story_id: str | None = None
    cast: list[CatalogCastCard | MemberCastCard] = field(default_factory=list)
    errors: list[ReportEntry] | None = None
    warnings: list[ReportEntry] | None = None
    deferred: list[DeferredItem] | None = None
    inactive: list[InactiveItem] | None = None
    materialized: list[MaterializedEntry] | None = None
    unavailable: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UnmetRecommendation(Model):
    """``UnmetRecommendation``: a capability the scenario recommends that the profile lacks."""

    capability: str
    recommended_by: str


@dataclass(frozen=True)
class BackfillEstimate(Model):
    model_calls: int
    tokens: int
    duration_s: float


@dataclass(frozen=True)
class Backfill(Model):
    needed: bool
    estimate: BackfillEstimate | None = None


@dataclass(frozen=True)
class AffectedStory(Model):
    """``AffectedStory``: one story a profile change reaches (empty for a creation)."""

    story_id: str
    capabilities_gained: list[str]
    capabilities_lost: list[str]
    backfill: Backfill
    upgrade_unavailable: bool
    capabilities_after: list[str] | None = None
    fingerprint_before: str | None = None
    fingerprint_after: str | None = None
    quality_status_after: QualityStatus | None = None


@dataclass(frozen=True)
class ImpactReport(Model):
    """``ImpactReport`` (#109): what the engine profile means for the story: the scenario's
    recommendations it leaves unmet, the warnings, the guarantees lost, and the capabilities,
    quality status and fingerprints after."""

    affected_stories: list[AffectedStory]
    affected_count: int
    recommendations_unmet: list[UnmetRecommendation]
    guarantees_lost: list[str]
    warnings: list[str]
    capabilities_after: list[str] | None = None
    quality_status_after: QualityStatus | None = None
    fingerprint_before: str | None = None
    fingerprint_after: str | None = None


# --- engine profile writes (#84) -----------------------------------------------------------------


@dataclass(frozen=True)
class RequiredModule(Model):
    """A module a dependency rule requires, at one of ``levels`` (any level that is on when
    empty)."""

    module: str
    levels: list[str]


@dataclass(frozen=True)
class MissingDependency(Model):
    """A module that is on (at ``level``) without what it ``requires`` (FR-PROF-4)."""

    module: str
    level: str
    requires: list[RequiredModule]


@dataclass(frozen=True)
class UndeliveredSetting(Model):
    """A setting naming a module, or a level of one, this deployment has not delivered:
    ``setting`` is its dotted path in the settings."""

    module: str
    level: str | None
    setting: str


@dataclass(frozen=True)
class ProfileValidationWarning(Model):
    """A validator warning (``below_safety_minimum``): the setting, its value, the minimum a turn
    needs and the reservation that makes it up."""

    code: str
    setting: str
    model_calls: int
    minimum_required: int
    reservation: dict[str, int]


@dataclass(frozen=True)
class ProfileValidation(Model):
    """``ProfileValidation``: ``valid`` exactly when both lists are empty; ``warnings`` never
    block. The client also builds one from a 422 ``invalid_profile`` (its ``details`` carry the
    same two lists)."""

    valid: bool
    missing_dependencies: list[MissingDependency]
    undelivered: list[UndeliveredSetting]
    warnings: list[ProfileValidationWarning] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileWritten(Model):
    """``ProfileBody``: the profile after a create or a copy, at its latest version, with the
    deployment's ``config_revision``. ``settings`` is raw, as on :class:`EngineProfile`."""

    config_revision: int
    id: str
    name: str
    owner_principal: str
    preset: bool
    version: int
    current_version: int
    settings: dict[str, Any]
    created_at: str
    updated_at: str
    deleted_at: str | None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ProfileEdit(Model):
    """``ProfileEditBody``: an edit's two stages. ``applied`` false: the ``impact`` report held
    it back and nothing was written (``result`` ``None``)."""

    config_revision: int
    applied: bool
    impact: ImpactReport
    result: EngineProfile | None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ProfileWarning(Model):
    """What a delete warns of: a story still running under the profile (``profile_in_use``) or
    a scenario recommending it (``profile_recommended``)."""

    kind: str = open_enum()
    story_id: str | None = None
    scenario_id: str | None = None


@dataclass(frozen=True)
class ProfileDeleted(Model):
    """``ProfileDeletedBody``: when it was deleted, and the stories and scenarios still naming
    it (at most 1,000 of each; the counts name them all)."""

    config_revision: int
    id: str
    deleted_at: str
    warnings: list[ProfileWarning]
    stories_using: int
    scenarios_recommending: int
    idempotency_key: str | None = None


# --- a story's effective configuration (#85) -----------------------------------------------------


@dataclass(frozen=True)
class ProfileRef(Model):
    """``ProfileRef``: the profile a story runs under and the version it runs on. ``pinned``:
    it keeps an earlier version (an upgrade was unavailable); ``deleted``: the profile is
    deleted and still serves the story."""

    id: str
    version: int
    pinned: bool
    deleted: bool


@dataclass(frozen=True)
class OverridesRef(Model):
    """``OverridesRef``: the story's overrides of its profile's dials and their ``revision``
    (0 while it has none). ``settings`` is raw, as on :class:`EngineProfile`."""

    revision: int
    settings: dict[str, Any]


@dataclass(frozen=True)
class EffectiveConfig(Model):
    """``EffectiveConfig``: the deployment's defaults, the profile and version, the story's
    overrides, every delivered setting at its effective value, and the configuration's
    fingerprint, capabilities and quality status. The settings documents stay raw, so modules
    a later Engine delivers survive (the contract test widens them)."""

    story_id: str
    deployment_defaults: dict[str, Any]
    profile: ProfileRef
    overrides: OverridesRef
    effective: dict[str, Any]
    fingerprint: str
    capabilities: list[str]
    quality_status: QualityStatus


@dataclass(frozen=True)
class StoryProfileWrite(Model):
    """``StoryProfileWriteBody``: an assignment's or an overrides write's two stages.
    ``applied`` false: the ``impact`` report held it back and nothing was written (``result``
    ``None``); applied, ``result`` is the story's effective configuration after it."""

    timeline_revision: int
    applied: bool
    impact: ImpactReport
    result: EffectiveConfig | None
    config_revision: int
    idempotency_key: str | None = None


# --- principal lookup (#76) -----------------------------------------------------------------------
#
# ``GET /principals/{principal}`` answers ``PrincipalLookup``: ``owned`` and ``related``, each
# with one page per entity type under the Engine's plural key. :class:`PrincipalLinks` reads it
# by hand (not field by field) so a missing type stays visible as missing and any key the
# Console does not know is kept for the page's "More".

LOOKUP_TYPES = ("stories", "characters", "scenarios", "personas", "media", "engine_profiles")


@dataclass(frozen=True)
class CompanionRef(Model):
    """``CompanionRef``: a character's companion scenario, which follows it (#116)."""

    id: str


@dataclass(frozen=True)
class OwnedItem(Model):
    """``OwnedItem``: one entity the principal owns. ``name`` is ``None`` while the entity is
    held for an erasure; ``status``, ``version`` and ``listing_status`` are ``None`` where the
    type has none. A character names its ``companion``."""

    id: str
    name: str | None
    status: str | None
    version: int | None
    listing_status: str | None
    companion: CompanionRef | None = None


@dataclass(frozen=True)
class OwnedPage(Model):
    """``OwnedPage``: the principal's entities of one type: the count, a page newest first and
    the next page's cursor (``None`` on the last)."""

    count: int
    items: list[OwnedItem]
    next_cursor: str | None


@dataclass(frozen=True)
class RelatedItem(Model):
    """``RelatedItem``: one entity with relations naming the principal, and their names."""

    id: str
    relations: list[str]


@dataclass(frozen=True)
class RelatedPage(Model):
    """``RelatedPage``: the related entities of one type, newest relation first."""

    count: int
    items: list[RelatedItem]
    next_cursor: str | None


def _unknown(obj: dict[str, Any], model: type) -> dict[str, Any]:
    known = {json_name(f) for f in fields(model)}
    return {key: value for key, value in obj.items() if key not in known}


def _pages[P: (OwnedPage, RelatedPage)](
    side: str, obj: object, page_model: type[P], item_model: type, more: dict[str, Any]
) -> dict[str, P] | None:
    """The side's pages by type key (``None`` when the side or a page is malformed); unknown
    type, page and item keys go to ``more`` under a dotted name (``owned.stories.<id>``)."""
    if not isinstance(obj, dict):
        return None
    pages: dict[str, P] = {}
    for key, value in obj.items():
        if key not in LOOKUP_TYPES:
            more[f"{side}.{key}"] = value
            continue
        page = page_model.from_json(value)
        if page is None or not isinstance(value, dict):
            return None
        pages[key] = page
        extra = _unknown(value, page_model)
        if extra:
            more[f"{side}.{key}"] = extra
        for raw in value.get("items", []):
            item_extra = _unknown(raw, item_model) if isinstance(raw, dict) else {}
            if item_extra:
                more[f"{side}.{key}.{raw.get('id')}"] = item_extra
    return pages


@dataclass(frozen=True)
class PrincipalLinks(Model):
    """``PrincipalLookup``: everything linked to ``principal`` (as the Engine echoes it), the
    ``owned`` and ``related`` pages keyed by type (:data:`LOOKUP_TYPES`; a type the Engine left
    out is absent), and ``more``: the keys the Console does not know, by dotted name."""

    principal: str
    owned: dict[str, OwnedPage]
    related: dict[str, RelatedPage]
    more: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: object) -> Self | None:
        if not isinstance(obj, dict) or not isinstance(obj.get("principal"), str):
            return None
        more: dict[str, Any] = {
            key: value for key, value in obj.items() if key not in ("principal", "owned", "related")
        }
        owned = _pages("owned", obj.get("owned"), OwnedPage, OwnedItem, more)
        related = _pages("related", obj.get("related"), RelatedPage, RelatedItem, more)
        if owned is None or related is None:
            return None
        return cls(obj["principal"], owned, related, more)


# --- offboarding (#77) ---------------------------------------------------------------------------

OwnedAction = Literal["delete", "transfer", "keep"]
RelationAction = Literal["remove", "keep"]
OWNED_ACTIONS: tuple[OwnedAction, ...] = typing.get_args(OwnedAction)
RELATION_ACTIONS: tuple[RelationAction, ...] = typing.get_args(RelationAction)
# The types ``actions`` names besides ``relations``, in the Engine's (and the Console's) order.
OFFBOARDING_TYPES = LOOKUP_TYPES


@dataclass(frozen=True)
class OffboardingActions(Model):
    """``OffboardingActions``: what ``POST /admin/offboarding`` does per type it owns, and to
    the relations naming it on entities it does not own."""

    stories: OwnedAction = "keep"
    characters: OwnedAction = "keep"
    scenarios: OwnedAction = "keep"
    personas: OwnedAction = "keep"
    media: OwnedAction = "keep"
    engine_profiles: OwnedAction = "keep"
    relations: RelationAction = "keep"

    def to_json(self) -> dict[str, str]:
        """All seven keys, ``keep`` included (#77: the Console always sends every one)."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def transfers(self) -> bool:
        return any(getattr(self, t) == "transfer" for t in OFFBOARDING_TYPES)

    @property
    def all_keep(self) -> bool:
        return all(value == "keep" for value in self.to_json().values())


@dataclass(frozen=True)
class OffboardingWarning(Model):
    """``OffboardingWarningBody``: something the offboarding would do that touches other
    principals (``shared_story``, ``profile_in_use``, ``media_referenced``,
    ``default_profile``), the entity and the other principals."""

    kind: str
    entity_type: str
    entity_id: str
    principals: list[str]


@dataclass(frozen=True)
class OffboardingPlan(Model):
    """``OffboardingPlanBody``: a dry run, or a start its warnings held (``applied`` false):
    the counts per type (count key → number) and the warnings. Nothing was written."""

    config_revision: int
    applied: bool
    counts: dict[str, dict[str, int]]
    warnings: list[OffboardingWarning]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class OffboardingQueued(Model):
    """``OffboardingQueuedBody`` (202): the job a worker carries out, and the counts and
    warnings it was confirmed over."""

    config_revision: int
    applied: bool
    job_id: str
    counts: dict[str, dict[str, int]]
    warnings: list[OffboardingWarning]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class OffboardingProgress(Model):
    """``OffboardingProgress``: entities done of the snapshot's total."""

    done: int
    total: int


@dataclass(frozen=True)
class CurrentEntity(Model):
    """``CurrentEntity``: the entity a job is working on now (``type`` as the Engine names it)."""

    type: str
    id: str


@dataclass(frozen=True)
class OffboardingError(Model):
    """``OffboardingError``: why a failed job stopped (exception class, contentless message)."""

    error_class: str = field(metadata={JSON_NAME: "class"})
    message: str


@dataclass(frozen=True)
class OffboardingJob(Model):
    """``OffboardingView``: an offboarding job's ``status`` (``queued``, ``running``, ``done``,
    ``failed``), ``progress``, ``current`` entity, and once it ends its ``report`` (with the
    ``error`` of a failed one). ``report``, ``request`` and ``counts`` stay raw (#77 renders
    the report generically, so a key the Console does not know still shows)."""

    job_id: str
    principal: str
    status: str = field(metadata={OPEN_ENUM: True})
    progress: OffboardingProgress
    current: CurrentEntity | None
    counts: dict[str, dict[str, int]]
    report: dict[str, Any] | None
    error: OffboardingError | None
    request: dict[str, Any]
    created_by: str
    created_at: str
    finished_at: str | None


# --- owners and relations (#79) -------------------------------------------------------------------
#
# One model each for the story and the non-story bodies: a story's answer carries the story's
# ``timeline_revision`` (``StoryOwnerBody``, ``StoryRelationBody``), any other entity's the
# deployment's ``config_revision`` (``OwnerBody``, ``RelationBody``); both are unchanged by the
# write, and the one the body does not carry is ``None``.


@dataclass(frozen=True)
class OwnerTransfer(Model):
    """``OwnerBody`` / ``StoryOwnerBody``: the entity's owner after a transfer and before it.
    Equal when the request named the current owner: nothing was recorded then."""

    entity_type: EntityType
    entity_id: str
    owner_principal: str
    previous_owner: str
    idempotency_key: str | None = None
    config_revision: int | None = None
    timeline_revision: int | None = None

    @property
    def changed(self) -> bool:
        return self.owner_principal != self.previous_owner


@dataclass(frozen=True)
class RelationItem(Model):
    """``RelationItem``: one relation, the related principal, the relation's name (free text,
    never interpreted), and who added it and when."""

    principal: str
    relation: str
    created_at: str
    created_by: str


@dataclass(frozen=True)
class RelationRecord(RelationItem):
    """``RelationBody`` / ``StoryRelationBody``: the relation an add answered with, added (201)
    or already there (200, nothing recorded)."""

    entity_type: EntityType
    entity_id: str
    idempotency_key: str | None = None
    config_revision: int | None = None
    timeline_revision: int | None = None


@dataclass(frozen=True)
class Relations(Model):
    """``RelationsBody``: the entity's owner and its relations, ordered by relation name, then
    principal."""

    entity_type: EntityType
    entity_id: str
    owner_principal: str
    items: list[RelationItem]


# --- #82: rating suggestions -----------------------------------------------------------------

SuggestionVersion = int | Literal["draft"]


@dataclass(frozen=True)
class EntryRef(Model):
    """``EntryRef``: the catalog entry a rating suggestion reviewed, its type, id and version
    (``"draft"`` for the draft)."""

    type: Literal["character", "scenario"]
    id: str
    version: SuggestionVersion


@dataclass(frozen=True)
class Element(Model):
    """``Element``: the part of the entry a concern names, its ``kind`` (``field``, ``seed``,
    ``role``, ``reveal``, ``cast_character`` or ``media_caption``) and where it sits (the
    Engine's ``ElementLocation``, parsed into #64's :class:`EntryLocation`)."""

    location: EntryLocation
    kind: str = open_enum()


@dataclass(frozen=True)
class Concern(Model):
    """``Concern``: one conflict between the entry and a rating, the ``policy_id`` or the quoted
    rating-description ``clause`` it breaks (exactly one), the element and why."""

    element: Element
    explanation: str
    policy_id: str | None = None
    clause: str | None = None


@dataclass(frozen=True)
class SuggestionRow(Model):
    """``SuggestionRow``: one rating's row. ``appropriate`` is ``None`` when its model call
    failed (``error``) and nothing deterministic was found; ``truncated``: the model saw only
    part of the entry; ``dropped_concerns``: concerns naming something the model wasn't given."""

    rating_id: str
    rating_name: str
    rating_version: str
    appropriate: bool | None
    concerns: list[Concern]
    error: str | None = open_enum(default=None)
    truncated: bool = False
    dropped_concerns: int = 0


@dataclass(frozen=True)
class EvaluatedRating(Model):
    """``EvaluatedRating``: a rating the table evaluated, at the version (id) it used."""

    rating_id: str
    rating_version: str


@dataclass(frozen=True)
class UnavailableCharacter(Model):
    """``UnavailableCharacter``: a default or locked role's character a scenario review could
    not read (missing, unpublished or archived) and skipped; ``version`` ``None``: the latest."""

    role: str
    entry: str
    version: int | None
    reason: str = "character_unavailable"


@dataclass(frozen=True)
class SuggestionModelCall:
    """One of a rating suggestion's ``model_calls`` (free-form objects in the schema): every
    field optional (``None`` when missing or of another type), unknown keys ignored."""

    purpose: str | None = None
    model: str | None = None
    attempt: int | None = None
    outcome: str | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost: float | None = None

    @classmethod
    def from_raw(cls, raw: object) -> Self:
        found = raw if isinstance(raw, dict) else {}

        def text(key: str) -> str | None:
            value = found.get(key)
            return value if isinstance(value, str) else None

        def whole(key: str) -> int | None:
            value = found.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        cost = found.get("cost")
        return cls(
            purpose=text("purpose"),
            model=text("model"),
            attempt=whole("attempt"),
            outcome=text("outcome"),
            latency_ms=whole("latency_ms"),
            tokens_in=whole("tokens_in"),
            tokens_out=whole("tokens_out"),
            cost=(
                float(cost)
                if isinstance(cost, int | float) and not isinstance(cost, bool)
                else None
            ),
        )


@dataclass(frozen=True)
class RatingSuggestion(Model):
    """``RatingSuggestion`` (#82): the advisory table, one row per rating in display order, the
    rating versions evaluated, the entry, the cast characters skipped and the model calls made
    (raw objects: :meth:`calls` reads them). It writes nothing and the Engine never applies it."""

    entry: EntryRef
    rows: list[SuggestionRow]
    evaluated: list[EvaluatedRating]
    advisory: Literal[True] = True
    unavailable: list[UnavailableCharacter] = field(default_factory=list)
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0

    def calls(self) -> list[SuggestionModelCall]:
        return [SuggestionModelCall.from_raw(raw) for raw in self.model_calls]


# --- Message feedback (#83) ---------------------------------------------------------------------


@dataclass(frozen=True)
class ModelIdentity(Model):
    """``ModelIdentity``: one model purpose's model as a fingerprint names it (the configured id
    and the version the provider reports, ``None`` until an adapter reports one)."""

    id: str | None
    reported_version: str | None = None


@dataclass(frozen=True)
class FeedbackContext(Model):
    """``FeedbackContext``: the configuration a rated message was produced under, copied when
    the rating was first written. ``profile_version`` is ``<profile id>@<version>``;
    ``quality_status`` is the schema's ``QualityStatusValue`` kept as a plain string."""

    operation_id: str
    config_fingerprint: str | None = None
    models: dict[str, ModelIdentity] | None = None
    profile_version: str | None = None
    quality_status: str | None = open_enum(default=None)
    rating_version_id: str | None = None


@dataclass(frozen=True)
class FeedbackHistoryEntry(Model):
    """``FeedbackHistoryEntry``: an earlier value of a rating and why it stopped being current
    (``kind``: ``replaced`` or ``retracted``)."""

    rating: int
    tags: list[str]
    text: str | None
    rated_at: str
    replaced_at: str
    kind: str = open_enum()


@dataclass(frozen=True)
class MessageFeedback(Model):
    """``Feedback``: the requesting principal's rating of one generated message (``rating``
    ``None`` when the principal has none), its ``context`` (``None`` without a rating) and the
    ``history`` of earlier values, newest first."""

    story_id: str
    message_id: str
    turn: int
    variant: int
    principal: str
    rating: int | None
    tags: list[str]
    text: str | None
    compacted_at: str | None
    created_at: str | None
    updated_at: str | None
    context: FeedbackContext | None
    history: list[FeedbackHistoryEntry]


# --- erasure (#86) --------------------------------------------------------------------------------


@dataclass(frozen=True)
class StoryScope:
    """An erasure's ``StoryScope``: within one story."""

    story: str

    def to_json(self) -> dict[str, str]:
        return {"story": self.story}


@dataclass(frozen=True)
class CatalogScope:
    """An erasure's ``CatalogScope``: the catalog (its characters, scenarios, personas, media)."""

    def to_json(self) -> dict[str, bool]:
        return {"catalog": True}


type ErasureScope = StoryScope | CatalogScope


def scope_from_json(raw: object) -> ErasureScope | None:
    """The scope a request body sent (``{story: <id>}`` or ``{catalog: true}``), else ``None``."""
    if isinstance(raw, dict):
        story = raw.get("story")
        if isinstance(story, str) and story:
            return StoryScope(story)
        if raw.get("catalog") is True:
            return CatalogScope()
    return None


@dataclass(frozen=True)
class ErasureCandidate(Model):
    """``CandidateBody``: a mention erased only when confirmed (ADR-154). ``snippet`` is the dry
    run's text around it, never stored (the operation log keeps ``"***"``)."""

    candidate_id: str
    record_table: str
    record_id: str
    field: str
    snippet: str
    rule: str


@dataclass(frozen=True)
class LedgerEntry(Model):
    """``LedgerEntryBody``: the erasure ledger's row (identifiers, scope, counts; never a name)."""

    erasure_id: str
    at: str
    scope: str
    story_id: str | None
    counts: dict[str, Any]


@dataclass(frozen=True)
class ReferencingStory(Model):
    """``ReferencingStoryBody``: a story still naming a catalog subject, left untouched."""

    story_id: str
    character_ids: list[str]


@dataclass(frozen=True)
class ErasureResult(Model):
    """``ErasureBody``: what an erasure did, or (``dry_run``) would do. ``subject`` is the
    Engine's ``{kind, id}`` object (:attr:`subject_ref` reads it); ``residue`` is ``None`` when
    no scan ran (a dry run)."""

    config_revision: int
    dry_run: bool
    subject: dict[str, str]
    scope: str
    story_id: str | None
    records: dict[str, int]
    prose_segments: int
    media: int
    idempotency_results: int
    regeneration: str
    degradation: list[str]
    referencing_stories: list[ReferencingStory]
    timeline_revision: int | None = None
    erasure_id: str | None = None
    operation_id: str | None = None
    idempotency_key: str | None = None
    ledger_entry: LedgerEntry | None = None
    residue: int | None = None
    candidates_remaining: int = 0
    candidates: list[ErasureCandidate] = field(default_factory=list)

    @property
    def subject_ref(self) -> SubjectRef | None:
        return SubjectRef.from_json(self.subject)

    @property
    def nothing(self) -> bool:
        """Every count zero and no candidate: nothing to erase."""
        return (
            not any(self.records.values())
            and self.prose_segments == 0
            and self.media == 0
            and self.idempotency_results == 0
            and not self.candidates
        )

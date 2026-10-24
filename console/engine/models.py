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
    ``applied: false``; ``report`` and ``impact`` are raw JSON (scenario creations only).
    """

    timeline_revision: int
    story_id: str | None
    characters: list[CreatedCharacter]
    content_rating: StoryRating | None
    applied: bool = True
    idempotency_key: str | None = None
    engine_profile: ProfileVersionRef | None = None
    scenario: ScenarioOrigin | None = None
    report: Any = None
    impact: Any = None


@dataclass(frozen=True)
class StoryStatusChange(Model):
    """``StatusBody``: a story's status and revision after an archive or unarchive."""

    timeline_revision: int
    status: StoryStatus
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


@dataclass(frozen=True)
class ContentRatingList(Model):
    """``RatingList``: content ratings in display order."""

    items: list[ContentRatingSummary]


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

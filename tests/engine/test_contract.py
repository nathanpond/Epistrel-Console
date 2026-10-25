"""The client's registered operations and typed models agree with the recorded schema.

The two "bites" tests run first and prove the check fails on a known-bad registry or model.
"""

import types
import typing
from dataclasses import MISSING, dataclass, fields
from typing import Any, Literal

import pytest

from console.engine.client import OPERATIONS, Operation
from console.engine.models import (
    AffectedStory,
    AgeCondition,
    Backfill,
    BackfillEstimate,
    CardImage,
    CardImages,
    CardRating,
    CastSource,
    CatalogCastCard,
    CatalogCharacter,
    CatalogCharacterCard,
    CatalogCharacterDraft,
    CatalogCharacterListItem,
    CatalogCharacterPage,
    CatalogCharacterStatus,
    CatalogSource,
    CatalogVersion,
    CatalogVersionList,
    ChainRef,
    CharacterDefinition,
    CharacterImage,
    CharacterImages,
    CharacterPublished,
    CheckProgress,
    CheckTarget,
    CheckViolation,
    ClassCapabilityCounts,
    CompanionDraft,
    CompanionPublished,
    CompanionRef,
    ConfigChange,
    ConfigChanges,
    ConfigSources,
    ContentRating,
    ContentRatingList,
    ContentRatingMutation,
    ContentRatingSummary,
    CreatedCharacter,
    CreationCounts,
    CreationReport,
    CreationRules,
    CurrentEntity,
    CustomInstructions,
    CustomInstructionsWritten,
    DeferredItem,
    DegradationStep,
    DeploymentSettings,
    Disposition,
    Dispositions,
    DormantChain,
    Drivers,
    EffectiveBudget,
    EffectiveGuardrail,
    EffectivePolicy,
    EngineProfile,
    EngineProfilePage,
    Entity,
    ForkCharacter,
    ForkedFrom,
    ForkResult,
    GuardrailCalls,
    GuardrailViolation,
    HealthChecks,
    HealthResponse,
    History,
    HistoryItem,
    ImpactReport,
    InactiveItem,
    InteractionStyle,
    LikesDislikes,
    ListingStatus,
    Location,
    MaterializedEntry,
    MediaAsset,
    MediaPage,
    MediaUploaded,
    MemberCastCard,
    ModelCall,
    ModelConfig,
    ModelConfigEntry,
    ModelConfigItem,
    ModelConfigMutation,
    OffboardingActions,
    OffboardingError,
    OffboardingJob,
    OffboardingPlan,
    OffboardingProgress,
    OffboardingQueued,
    OffboardingWarning,
    OmniscientStoryCharacter,
    Opening,
    OperationRecord,
    OperationStage,
    OutcomeCounts,
    OwnedItem,
    OwnedPage,
    OwnerTransfer,
    Persona,
    PersonaPage,
    PersonaWritten,
    PinnedItem,
    PinnedMemories,
    PinnedMemoriesWritten,
    Policy,
    PolicyKind,
    PolicyList,
    PolicyMutation,
    PolicyScope,
    PolicyTarget,
    ProfileSchema,
    ProfileVersion,
    ProfileVersionList,
    ProfileVersionRef,
    Promotion,
    QualityStatus,
    QueuedTurn,
    RatingApplied,
    RatingCheck,
    RatingCheckStarted,
    RatingVersion,
    RatingVersionList,
    ReactivationCheck,
    RebuildQueued,
    RebuildResult,
    Recommendation,
    ReferenceImage,
    RegenerateResult,
    RelatedItem,
    RelatedPage,
    RelationItem,
    RelationRecord,
    Relations,
    ReplyMessage,
    RespondResponse,
    RoleCharacter,
    RoleEligibility,
    RoleRelationship,
    Scenario,
    ScenarioCard,
    ScenarioCardMedia,
    ScenarioCardMediaSet,
    ScenarioCardRole,
    ScenarioCast,
    ScenarioDraft,
    ScenarioListItem,
    ScenarioMediaSet,
    ScenarioMetadata,
    ScenarioOrigin,
    ScenarioPage,
    ScenarioPolicy,
    ScenarioPublicCard,
    ScenarioRole,
    ScenarioStatus,
    ScenarioVideo,
    Seed,
    SettingTags,
    StartingState,
    StoredResult,
    Story,
    StoryCharacter,
    StoryCharacterAdded,
    StoryCreated,
    StoryList,
    StoryPause,
    StoryPauseProgress,
    StoryRating,
    StorySettings,
    StorySettingsDefaults,
    StorySettingsWrite,
    StoryStatusChange,
    StorySummary,
    SubjectRef,
    SummarySegment,
    SwapResult,
    TruncateResult,
    TurnBudget,
    TurnGuardrail,
    TurnMessage,
    TurnReport,
    TurnSummary,
    TurnWarning,
    UnmetRecommendation,
    UntruncateResult,
    VariantChain,
    Variants,
    VisualIdentity,
    VisualStyle,
    WorkersCheck,
    World,
)
from tests.engine.contract import check_operation, compare_model, compare_type
from tests.fakes.engine import recorded_schema

DOC = recorded_schema()
HEALTH_REF = {"$ref": "#/components/schemas/HealthResponse"}


# #78: ``EngineProfile.settings`` is raw JSON the profile schema describes (whatever modules
# this deployment delivers), not the modules the recorded schema spells out.
SETTINGS_ANY = {"type": "object", "additionalProperties": True}
# #79: one model reads the story and the non-story owner and relation bodies; each carries one
# revision (the story's ``timeline_revision`` or the deployment's ``config_revision``), so the
# model has both, optional. The widening gives each body both, nullable and not required.
REVISIONS = ("config_revision", "timeline_revision")
NULLABLE_INT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
OWNERSHIP_BODIES = ("OwnerBody", "StoryOwnerBody", "RelationBody", "StoryRelationBody")


def _widened(doc: dict[str, Any]) -> dict[str, Any]:
    """``doc`` with the Engine's ``EntryLocation`` widened by ``ElementLocation``'s ``media_id``:
    the one Console class parses both (#64), so a model holding a ``ValidationReport`` (a
    publish's answer, #66) is compared against this. ``EngineProfile.settings`` is widened to
    any object (#78). The owner and relation bodies carry both revisions, optional (#79)."""
    import copy

    widened = copy.deepcopy(doc)
    schemas = widened["components"]["schemas"]
    schemas["EntryLocation"]["properties"]["media_id"] = schemas["ElementLocation"]["properties"][
        "media_id"
    ]
    schemas["EngineProfile"]["properties"]["settings"] = SETTINGS_ANY
    for name in OWNERSHIP_BODIES:
        body = schemas[name]
        body["properties"].update(dict.fromkeys(REVISIONS, NULLABLE_INT))
        body["required"] = [r for r in body["required"] if r not in REVISIONS]
    return widened


WIDENED = _widened(DOC)


def test_bites_on_unknown_operation() -> None:
    problems = check_operation(DOC, Operation("GET", "/nope", None, False))
    assert problems == ["GET /nope: not in the recorded schema"]


def test_bites_on_model_with_extra_field() -> None:
    @dataclass(frozen=True)
    class Extra:
        status: Literal["ok", "degraded", "unavailable"]
        checks: HealthChecks
        content_logging: bool = False
        uptime_s: float = 0.0

    problems = compare_model(DOC, Extra, HEALTH_REF, "Extra")
    assert any("uptime_s" in p for p in problems)


def test_bites_on_wrong_type_enum_and_required() -> None:
    @dataclass(frozen=True)
    class Wrong:
        status: Literal["ok", "unavailable"]  # missing "degraded"
        checks: dict[str, str]  # the schema's checks is a nested object
        content_logging: bool  # required here, defaulted in the schema

    problems = compare_model(DOC, Wrong, HEALTH_REF, "Wrong")
    assert any("enum" in p for p in problems)
    assert any("checks" in p and "map" in p for p in problems)
    assert any("required" in p for p in problems)


def test_bites_on_nested_mismatch() -> None:
    @dataclass(frozen=True)
    class Workers:
        alive: int  # the schema says int | null, not required
        last_heartbeat_s: float | None = None

    problems = compare_model(DOC, Workers, {"$ref": "#/components/schemas/WorkersCheck"}, "W")
    assert problems


@pytest.mark.parametrize("name", sorted(OPERATIONS))
def test_registered_operations_match_the_recorded_schema(name: str) -> None:
    assert check_operation(WIDENED, OPERATIONS[name]) == []


def test_the_widening_only_adds_media_id() -> None:
    """The widened document differs from the recorded one by ``EntryLocation.media_id`` alone,
    and the publish registration does not match the recorded one without it (so the widening
    is what makes it pass, nothing else)."""
    entry = DOC["components"]["schemas"]["EntryLocation"]["properties"]
    widened = WIDENED["components"]["schemas"]["EntryLocation"]["properties"]
    assert set(widened) - set(entry) == {"media_id"}
    publish = OPERATIONS["POST /catalog/characters/{character_id}/publish"]
    problems = check_operation(DOC, publish)
    assert problems
    assert all("media_id" in p for p in problems)


@pytest.mark.parametrize(
    ("model", "component"),
    [
        (HealthResponse, "HealthResponse"),
        (HealthChecks, "HealthChecks"),
        (WorkersCheck, "WorkersCheck"),
        (StoryList, "StoryList"),
        (StorySummary, "StorySummaryBody"),
        (StoryRating, "StoryRatingBody"),
        (OperationRecord, "OperationBody"),
        (StoredResult, "StoredResultBody"),
        (OperationStage, "StageBody"),
        (ModelCall, "ModelCall"),
        (TurnBudget, "BudgetBody"),
        (DegradationStep, "DegradationBody"),
        (GuardrailViolation, "GuardrailViolationBody"),
        (TurnGuardrail, "GuardrailBody"),
        (QualityStatus, "QualityStatus"),
        (ProfileVersionRef, "ProfileVersionRef"),
        (TurnWarning, "WarningBody"),
        (Story, "StoryBody"),
        (StoryCharacter, "CharacterBody"),
        (OmniscientStoryCharacter, "OmniscientCharacterBody"),
        (CatalogSource, "CatalogSourceBody"),
        (StoryPause, "StoryPauseBody"),
        (StoryPauseProgress, "StoryPauseProgressBody"),
        (DormantChain, "DormantChainBody"),
        (VariantChain, "VariantChainBody"),
        (ChainRef, "ChainRefBody"),
        (ScenarioOrigin, "ScenarioOriginBody"),
        (StoryCreated, "StoryCreatedBody"),
        (StoryCharacterAdded, "StoryCharacterBody"),
        (Promotion, "PromotionBody"),
        (CreatedCharacter, "CreatedCharacterBody"),
        (StoryStatusChange, "StatusBody"),
        (ContentRatingSummary, "RatingBody"),
        (ContentRatingList, "RatingList"),
        (ContentRating, "RatingDetailBody"),
        (EffectiveGuardrail, "EffectiveGuardrail"),
        (ContentRatingMutation, "RatingMutation"),
        (RatingVersion, "RatingBody"),
        (RatingVersionList, "RatingList"),
        (DeploymentSettings, "SettingsBody"),
        (ModelConfig, "ModelConfigBody"),
        (ModelConfigEntry, "ModelConfigEntry"),
        (ModelConfigItem, "ModelConfigItem"),
        (ModelConfigMutation, "ModelConfigMutation"),
        (ConfigSources, "Sources"),
        (Policy, "PolicyBody"),
        (PolicyList, "PolicyList"),
        (PolicyMutation, "PolicyMutation"),
        (RespondResponse, "RespondResponse"),
        (ReplyMessage, "MessageBody"),
        (QueuedTurn, "QueuedTurn"),
        (History, "HistoryResponse"),
        (HistoryItem, "HistoryItemModel"),
        (TurnSummary, "TurnSummaryModel"),
        (TurnMessage, "TurnMessageModel"),
        (StorySettings, "StorySettingsBody"),
        (EffectivePolicy, "EffectivePolicyBody"),
        (SummarySegment, "SummarySegmentModel"),
        (Variants, "VariantsBody"),
        (EffectiveBudget, "EffectiveBudgetBody"),
        (GuardrailCalls, "GuardrailCallsBody"),
        (RebuildResult, "RebuildResponse"),
        (RebuildQueued, "RebuildQueuedResponse"),
        (ConfigChange, "ConfigChangeItem"),
        (ConfigChanges, "ConfigChangesBody"),
        (RegenerateResult, "RegeneratedBody"),
        (TruncateResult, "TruncatedBody"),
        (UntruncateResult, "UntruncatedBody"),
        (SwapResult, "SwappedBody"),
        (ForkResult, "ForkedBody"),
        (ForkCharacter, "ForkCharacterBody"),
        (ForkedFrom, "ForkedFromBody"),
        (ReactivationCheck, "RatingCheckStartedBody"),
        # #80: a story's rating change and its check.
        (RatingCheckStarted, "RatingCheckStartedBody"),
        (RatingApplied, "RatingAppliedBody"),
        (RatingCheck, "RatingCheckBody"),
        (CheckProgress, "CheckProgressBody"),
        (CheckTarget, "CheckTargetBody"),
        (CheckViolation, "CheckViolationBody"),
        (StorySettingsWrite, "StorySettingsWriteBody"),
        (PinnedItem, "PinnedItemBody"),
        (PinnedMemories, "PinnedMemoriesBody"),
        (PinnedMemoriesWritten, "PinnedMemoriesWritten"),
        (CustomInstructions, "CustomInstructionsBody"),
        (CustomInstructionsWritten, "CustomInstructionsWritten"),
        (CardImage, "CardImage"),
        (CardRating, "CardRating"),
        (CardImages, "CharacterCardImages"),
        (CatalogCharacterCard, "CharacterCard"),
        (CatalogCharacterListItem, "CharacterListItem"),
        (CatalogCharacterPage, "CharacterListBody"),
        (CatalogCharacter, "CharacterFull"),
        (CharacterDefinition, "CharacterDefinition"),
        (Dispositions, "Dispositions"),
        (Disposition, "WarmthDisposition"),
        (Disposition, "CautionDisposition"),
        (InteractionStyle, "InteractionStyle"),
        (LikesDislikes, "LikesDislikes"),
        (CharacterImages, "CharacterImagesDetail"),
        (CharacterImage, "CharacterImageDetail"),
        (ReferenceImage, "ReferenceImageDetail"),
        (VisualIdentity, "VisualIdentityDetail"),
        (CatalogVersion, "CharacterVersionBody"),
        (CatalogVersionList, "CharacterVersionsBody"),
        (CatalogCharacterDraft, "CharacterDraftBody"),
        (CharacterPublished, "CharacterPublishedBody"),
        (CompanionPublished, "CompanionPublishedBody"),
        (Persona, "Persona"),
        (PersonaWritten, "PersonaBody"),
        (PersonaPage, "PersonaListBody"),
        (ScenarioCardMedia, "ScenarioCardMedia"),
        (ScenarioCardMediaSet, "ScenarioCardMediaSet"),
        (ScenarioCardRole, "ScenarioCardRole"),
        (ScenarioCard, "ScenarioCard"),
        (ScenarioListItem, "ScenarioListItem"),
        (ScenarioPage, "ScenarioListBody"),
        (ScenarioPublicCard, "ScenarioPublicCard"),
        (ScenarioMediaSet, "ScenarioMedia"),
        (ScenarioVideo, "ScenarioVideo"),
        (ScenarioMetadata, "ScenarioMetadata"),
        (Location, "ScenarioLocation"),
        (Entity, "ScenarioEntity"),
        (World, "ScenarioWorld"),
        (Opening, "ScenarioOpening"),
        (StartingState, "ScenarioStartingState"),
        (Drivers, "ScenarioDrivers"),
        (ScenarioPolicy, "ScenarioPolicy"),
        (StorySettingsDefaults, "StorySettingsDefaults"),
        (Recommendation, "ScenarioRecommendation"),
        (VisualStyle, "VisualStyle"),
        (CreationRules, "CreationRules"),
        (Scenario, "ScenarioFull"),
        (ScenarioCast, "ScenarioCast"),
        (ScenarioRole, "ScenarioRole"),
        (RoleCharacter, "RoleCharacter"),
        (RoleEligibility, "RoleEligibility"),
        (RoleRelationship, "RoleRelationship"),
        (Seed, "Seed"),
        (ScenarioDraft, "ScenarioDraftBody"),
        (CatalogVersion, "ScenarioVersionBody"),
        (CatalogVersionList, "ScenarioVersionsBody"),
        (CatalogCharacterStatus, "CharacterStatusBody"),
        (ScenarioStatus, "ScenarioStatusBody"),
        (ListingStatus, "ListingStatusBody"),
        (SubjectRef, "SubjectRef"),
        (SubjectRef, "DepictedSubject"),
        (MediaAsset, "MediaAsset"),
        (MediaUploaded, "MediaUploadedBody"),
        (MediaPage, "MediaListBody"),
        # #75: a story creation from a scenario's reports.
        (CreationReport, "CreationReport"),
        (CreationCounts, "CreationCounts"),
        (ClassCapabilityCounts, "ClassCapabilityCounts"),
        (OutcomeCounts, "OutcomeCounts"),
        (DeferredItem, "DeferredItem"),
        (InactiveItem, "InactiveItem"),
        (MaterializedEntry, "MaterializedEntry"),
        (CastSource, "CastSource"),
        (CatalogCastCard, "CatalogCastCard"),
        (MemberCastCard, "MemberCastCard"),
        (ImpactReport, "ImpactReport"),
        (AffectedStory, "AffectedStory"),
        (Backfill, "Backfill"),
        (BackfillEstimate, "BackfillEstimate"),
        (UnmetRecommendation, "UnmetRecommendation"),
        # #76: the principal lookup's pages.
        (OwnedPage, "OwnedPage"),
        (OwnedItem, "OwnedItem"),
        (CompanionRef, "CompanionRef"),
        (RelatedPage, "RelatedPage"),
        (RelatedItem, "RelatedItem"),
        # #77: offboarding.
        (OffboardingActions, "OffboardingActions"),
        (OffboardingWarning, "OffboardingWarningBody"),
        (OffboardingPlan, "OffboardingPlanBody"),
        (OffboardingQueued, "OffboardingQueuedBody"),
        (OffboardingProgress, "OffboardingProgress"),
        (CurrentEntity, "CurrentEntity"),
        (OffboardingError, "OffboardingError"),
        (OffboardingJob, "OffboardingView"),
        # #78: engine profiles.
        (EngineProfile, "EngineProfile"),
        (EngineProfilePage, "ProfileListBody"),
        (ProfileVersion, "ProfileVersion"),
        (ProfileVersionList, "ProfileVersionsBody"),
        (SettingTags, "SettingTagsBody"),
        (ProfileSchema, "ProfileSchemaBody"),
        # #79: owners and relations.
        (OwnerTransfer, "OwnerBody"),
        (OwnerTransfer, "StoryOwnerBody"),
        (RelationRecord, "RelationBody"),
        (RelationRecord, "StoryRelationBody"),
        (RelationItem, "RelationItem"),
        (Relations, "RelationsBody"),
    ],
)
def test_models_match_their_components(model: type, component: str) -> None:
    ref = {"$ref": f"#/components/schemas/{component}"}
    assert compare_model(WIDENED, model, ref, component) == []


def test_companion_draft_reads_fields_of_scenario_full() -> None:
    """#66: ``CompanionDraft`` reads a few fields of the companion read's ``ScenarioFull``
    (#68 owns the whole model): each is a ``ScenarioFull`` property of the same type, and
    required here only when required there."""
    node = DOC["components"]["schemas"]["ScenarioFull"]
    properties = node["properties"]
    hints = typing.get_type_hints(CompanionDraft)
    problems = []
    for f in fields(CompanionDraft):
        if f.name not in properties:
            problems.append(f"{f.name}: not a ScenarioFull property")
            continue
        problems.extend(compare_type(DOC, hints[f.name], properties[f.name], f.name))
        has_default = f.default is not MISSING or f.default_factory is not MISSING
        if not has_default and f.name not in node["required"]:
            problems.append(f"{f.name}: required here but optional in ScenarioFull")
    assert problems == []
    op = OPERATIONS["GET /catalog/characters/{character_id}/companion"]
    assert op.response_model is None


def test_engine_profile_settings_is_the_only_widening_of_engine_profile() -> None:
    """#78: the widened ``EngineProfile`` differs from the recorded one by ``settings`` alone,
    which the recorded schema types as an object (of the modules it knew)."""
    recorded = DOC["components"]["schemas"]["EngineProfile"]
    widened = WIDENED["components"]["schemas"]["EngineProfile"]
    assert {k: v for k, v in recorded.items() if k != "properties"} == {
        k: v for k, v in widened.items() if k != "properties"
    }
    differs = [
        k for k in recorded["properties"] if recorded["properties"][k] != widened["properties"][k]
    ]
    assert differs == ["settings"]
    assert recorded["properties"]["settings"]["type"] == "object"


def test_a_scenario_card_read_matches_the_card_component() -> None:
    """``get_scenario`` holds the route's registration (``Scenario``, the full projection);
    the card read's model is checked against ``ScenarioCard`` above."""
    op = OPERATIONS["GET /catalog/scenarios/{scenario_id}"]
    assert op.response_model is Scenario
    assert check_operation(DOC, op) == []


def test_scenario_form_enums_match_the_schema() -> None:
    """#68's selects offer exactly the schema's values."""
    from console.forms.scenario import ENTITY_KINDS, SETTING_ENUMS

    schemas = DOC["components"]["schemas"]

    def enum(node: dict[str, typing.Any]) -> list[str]:
        options = node.get("anyOf") or [node]
        (values,) = [o["enum"] for o in options if "enum" in o]
        return list(values)

    assert enum(schemas["ScenarioEntity"]["properties"]["kind"]) == list(ENTITY_KINDS)
    settings = schemas["StorySettingsDefaults"]["properties"]
    for name, values in SETTING_ENUMS.items():
        assert enum(settings[name]) == list(values), name


def test_health_checks_both_declared_statuses() -> None:
    responses = DOC["paths"]["/health"]["get"]["responses"]
    assert {"200", "503"} <= set(responses)


@pytest.mark.parametrize("component", ["RespondResponse", "RegeneratedBody"])
def test_turn_report_fields_match_the_turn_responses(component: str) -> None:
    """``TurnReport`` has no component of its own: each of its fields is one of the turn
    response's, with the same type (only its required set is looser, by design)."""
    node = DOC["components"]["schemas"][component]
    properties = node["properties"]
    hints = typing.get_type_hints(TurnReport)
    problems = []
    for f in fields(TurnReport):
        if f.name not in properties:
            problems.append(f"{f.name}: not a {component} property")
            continue
        tp = hints[f.name]
        schema = properties[f.name]
        required = f.name in node["required"]
        if not required or typing.get_origin(tp) is not types.UnionType:
            problems.extend(compare_type(DOC, tp, schema, f"{component}.{f.name}"))
        else:  # required in the schema but optional here: compare the non-null part
            (inner,) = [a for a in typing.get_args(tp) if a is not type(None)]
            nullable = any(o.get("type") == "null" for o in schema.get("anyOf", []))
            if nullable:
                problems.extend(compare_type(DOC, tp, schema, f"{component}.{f.name}"))
            else:
                problems.extend(compare_type(DOC, inner, schema, f"{component}.{f.name}"))
    assert problems == []


def test_raw_json_fields_accept_any_schema() -> None:
    @dataclass(frozen=True)
    class Raw:
        status: int
        body: Any

    assert compare_model(DOC, Raw, {"$ref": "#/components/schemas/StoredResultBody"}, "R") == []


def _property(component: str, name: str) -> dict[str, Any]:
    node: dict[str, Any] = DOC["components"]["schemas"][component]["properties"][name]
    return node


def test_policy_scope_is_the_enum_the_scope_property_references() -> None:
    """Found by the ``scope`` property's ``$ref``, not by component name (the Engine renamed
    the component once, #47)."""
    for component in ("PolicyBody", "PolicyMutation"):
        ref = _property(component, "scope")
        assert compare_type(DOC, PolicyScope, ref, f"{component}.scope") == []
    query = DOC["paths"]["/admin/policies"]["get"]["parameters"]
    (scope,) = [p for p in query if p["name"] == "scope"]
    assert compare_type(DOC, PolicyScope | None, scope["schema"], "query scope") == []


def test_policy_kind_and_targets_match_the_schema_enums() -> None:
    assert compare_type(DOC, PolicyKind, _property("PolicyBody", "kind"), "kind") == []
    targets = _property("PolicyReplacement", "applies_to")
    assert compare_type(DOC, list[PolicyTarget], targets, "applies_to") == []


@pytest.mark.parametrize("component", ["PolicyFields", "PolicyReplacement", "StoryPolicyItem"])
def test_age_condition_is_the_bodies_one_value(component: str) -> None:
    options = _property(component, "age_condition")["anyOf"]
    consts = {o["const"] for o in options if "const" in o}
    assert consts == set(typing.get_args(AgeCondition))
    assert {"type": "null"} in options


def test_respond_checks_its_202_against_queued_turn() -> None:
    op = OPERATIONS["POST /stories/{story_id}/respond"]
    assert op.status_models == ((202, QueuedTurn),)
    wrong = Operation(op.method, op.path, op.response_model, False, ((202, RespondResponse),))
    assert any("202" in problem for problem in check_operation(DOC, wrong))


def test_a_status_model_for_an_undeclared_status_is_reported() -> None:
    op = OPERATIONS["POST /stories/{story_id}/respond"]
    extra = Operation(op.method, op.path, op.response_model, False, ((201, QueuedTurn),))
    assert "no 201 response in the schema" in " ".join(check_operation(DOC, extra))


def test_report_location_matches_element_location() -> None:
    """#64: ``EntryLocation`` is the Engine's ``ElementLocation`` (``EntryLocation`` plus
    ``media_id``), so both parse into it."""
    from console.engine.models import EntryLocation

    ref = {"$ref": "#/components/schemas/ElementLocation"}
    assert compare_model(DOC, EntryLocation, ref, "ElementLocation") == []
    element = DOC["components"]["schemas"]["ElementLocation"]["properties"]
    entry = DOC["components"]["schemas"]["EntryLocation"]["properties"]
    assert set(element) - set(entry) == {"media_id"}


@pytest.mark.parametrize(
    ("model_name", "component"),
    [("ReportEntry", "Entry"), ("ValidationReport", "ValidationReport")],
)
def test_report_models_match_their_components(model_name: str, component: str) -> None:
    """Compared with the Engine's ``EntryLocation`` widened by ``ElementLocation``'s
    ``media_id`` (the one class parses both, #64)."""
    import copy

    from console.engine import models

    doc = copy.deepcopy(DOC)
    schemas = doc["components"]["schemas"]
    schemas["EntryLocation"]["properties"]["media_id"] = schemas["ElementLocation"]["properties"][
        "media_id"
    ]
    ref = {"$ref": f"#/components/schemas/{component}"}
    assert compare_model(doc, getattr(models, model_name), ref, component) == []


def test_an_open_enum_is_compared_with_the_schemas_enum() -> None:
    """#65: an ``OPEN_ENUM`` ``str`` field passes against a string enum, and bites when the
    schema's value is not an enum or the field is not a string."""
    from dataclasses import field as dc_field

    from console.engine.models import OPEN_ENUM

    @dataclass(frozen=True)
    class Open:
        setting: str = dc_field(metadata={OPEN_ENUM: True})
        nuance: str | None = None

    ref = {"$ref": "#/components/schemas/WarmthDisposition"}
    assert compare_model(DOC, Open, ref, "Open") == []

    @dataclass(frozen=True)
    class Plain:  # without the flag a str against an enum is reported
        setting: str
        nuance: str | None = None

    assert any("enum" in p for p in compare_model(DOC, Plain, ref, "Plain"))

    @dataclass(frozen=True)
    class NotEnum:
        media_id: str = dc_field(metadata={OPEN_ENUM: True})
        alt_text: str | None = None

    problems = compare_model(DOC, NotEnum, {"$ref": "#/components/schemas/CardImage"}, "N")
    assert any("not a string enum" in p for p in problems)


def test_a_union_response_must_match_one_of_its_options() -> None:
    """#65: ``GET /catalog/characters/{id}`` answers a card or the full projection; the
    registered model must match one of them."""
    op = OPERATIONS["GET /catalog/characters/{character_id}"]
    assert op.response_model is CatalogCharacter
    wrong = Operation(op.method, op.path, CatalogVersion, False)
    assert any("no anyOf option" in p for p in check_operation(DOC, wrong))


def test_the_principal_lookup_types_match_the_schema() -> None:
    """#76: ``PrincipalLinks`` reads ``PrincipalLookup`` by hand; its six type keys (and the
    page's table order) are the schema's ``OwnedEntities``/``RelatedEntities`` properties, each
    a registry lookup key, and the lookup is registered without a response model."""
    from console.engine.models import LOOKUP_TYPES
    from console.entities import BY_LOOKUP_KEY
    from console.views.principals import ENGINE_LOOKUP_KEYS

    schemas = DOC["components"]["schemas"]
    assert set(schemas["PrincipalLookup"]["properties"]) == {"principal", "owned", "related"}
    for side in ("OwnedEntities", "RelatedEntities"):
        assert set(schemas[side]["properties"]) == set(LOOKUP_TYPES)
        assert set(schemas[side]["required"]) == set(LOOKUP_TYPES)
    assert tuple(ENGINE_LOOKUP_KEYS) == LOOKUP_TYPES
    for key, entity_type in ENGINE_LOOKUP_KEYS.items():
        assert BY_LOOKUP_KEY[key].type == entity_type
    assert OPERATIONS["GET /principals/{principal_id}"].response_model is None


@pytest.mark.parametrize("component", OWNERSHIP_BODIES)
def test_the_ownership_bodies_widen_only_their_revisions(component: str) -> None:
    """#79: each widened body differs from the recorded one by the revisions alone: the one it
    carries (required, an integer) becomes optional and nullable, and the other is added."""
    recorded = DOC["components"]["schemas"][component]
    widened = WIDENED["components"]["schemas"][component]
    (carried,) = [r for r in REVISIONS if r in recorded["properties"]]
    assert carried in recorded["required"]
    assert recorded["properties"][carried]["type"] == "integer"
    assert set(widened["properties"]) - set(recorded["properties"]) == set(REVISIONS) - {carried}
    same = set(recorded["properties"]) - {carried}
    assert all(recorded["properties"][k] == widened["properties"][k] for k in same)
    assert set(recorded["required"]) - set(widened["required"]) == {carried}

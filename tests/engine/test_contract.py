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
    Adjudication,
    AffectedStory,
    AgeCondition,
    AmendmentChecks,
    AmendmentDetail,
    AmendmentPage,
    AmendmentSummary,
    AnalysisQueued,
    AuthoredBelief,
    AuthorPosition,
    Backfill,
    BackfillCancelled,
    BackfillConflict,
    BackfillConflictResolved,
    BackfillEstimate,
    BackfillProgress,
    BackfillStarted,
    BeliefItem,
    Beliefs,
    BeliefTransition,
    BeliefWritten,
    BlastRadius,
    Bound,
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
    Certification,
    CertificationImported,
    CertificationInvalidated,
    CertificationLost,
    CertificationPage,
    CertificationRun,
    CertificationRunRequested,
    ChainMembership,
    ChainRef,
    CharacterDefinition,
    CharacterImage,
    CharacterImages,
    CharacterPublished,
    CharacterWeighted,
    CheckProgress,
    CheckTarget,
    CheckViolation,
    ClassCapabilityCounts,
    ClockAdvanced,
    ClockSettings,
    ClockSettingsWritten,
    ClockValue,
    CompanionDraft,
    CompanionPublished,
    CompanionRef,
    Compensated,
    Concern,
    ConfigChange,
    ConfigChanges,
    ConfigSources,
    ConflictCounts,
    ConflictStatement,
    ConsolidationProvenance,
    ContentRating,
    ContentRatingList,
    ContentRatingMutation,
    ContentRatingSummary,
    CouldHaveKnownFlag,
    CoverageGap,
    CreatedCharacter,
    CreationCounts,
    CreationReport,
    CreationRules,
    CurrentEntity,
    CustomInstructions,
    CustomInstructionsWritten,
    DeferredCharacter,
    DeferredItem,
    DegradationStep,
    DeploymentSettings,
    DirectedPending,
    Directive,
    DirectiveApplied,
    Disposition,
    Dispositions,
    DivergenceItem,
    Divergences,
    DormantChain,
    Drivers,
    EffectiveBudget,
    EffectiveConfig,
    EffectiveGuardrail,
    EffectivePolicy,
    Element,
    EngineProfile,
    EngineProfilePage,
    Entity,
    EntityBody,
    EntityProfileBody,
    EntryRef,
    EpisodeBody,
    ErasureCandidate,
    ErasureResult,
    EscalationDeferred,
    EscalationYield,
    Estimate,
    EvalMetric,
    EvalRun,
    EvalRunPage,
    EvaluatedRating,
    ExportJob,
    ExportStarted,
    ExposureItem,
    Exposures,
    ExternalFact,
    ExternalFacts,
    ExternalRead,
    ExtractionShadow,
    FactBody,
    FactListBody,
    FeedbackContext,
    FeedbackHistoryEntry,
    FloorResult,
    FloorsMet,
    ForkCharacter,
    ForkedFrom,
    ForkResult,
    ForwardOnlyGap,
    GapFill,
    GapFillPreview,
    GuaranteeRequiredModule,
    GuaranteeStatus,
    GuaranteeTable,
    GuardrailCalls,
    GuardrailViolation,
    HealthChecks,
    HealthResponse,
    HeldRender,
    HiddenFact,
    History,
    HistoryItem,
    HotCountsBody,
    HotSetBody,
    HotSetBudgets,
    HotSetPins,
    IdentityItem,
    ImpactReport,
    Implication,
    ImportImpact,
    ImportJob,
    ImportStarted,
    InactiveItem,
    InspectPosition,
    Instant,
    InteractionStyle,
    Interval,
    InventoryBody,
    Ledger,
    LedgerEntry,
    LedgerItem,
    LikesDislikes,
    ListedMemory,
    ListingOutcome,
    ListingResolved,
    ListingStatus,
    Location,
    LocationAdjacency,
    MaterializedEntry,
    MediaAsset,
    MediaPage,
    MediaUploaded,
    MemberCastCard,
    MemoryList,
    MemoryView,
    MemoryWritten,
    MentionBody,
    MessageFeedback,
    MissingDependency,
    ModelCall,
    ModelConfig,
    ModelConfigEntry,
    ModelConfigItem,
    ModelConfigMutation,
    ModeledAt,
    ModelIdentity,
    ModelTiers,
    NarrativePosition,
    NestedSeed,
    Notice,
    OffboardingActions,
    OffboardingError,
    OffboardingJob,
    OffboardingPlan,
    OffboardingProgress,
    OffboardingQueued,
    OffboardingWarning,
    OmniscientStoryCharacter,
    Opening,
    OperationLabel,
    OperationRecord,
    OperationStage,
    OperationSummary,
    Outbox,
    OutboxPosition,
    OutboxRow,
    OutcomeCounts,
    OverridesRef,
    OwnedItem,
    OwnedPage,
    OwnerTransfer,
    Participation,
    ParticipationSet,
    PendingEffect,
    Persona,
    PersonaPage,
    PersonaWritten,
    PinnedItem,
    PinnedMemories,
    PinnedMemoriesWritten,
    PlacementBody,
    Policy,
    PolicyKind,
    PolicyList,
    PolicyMutation,
    PolicyScope,
    PolicyTarget,
    Predicate,
    PredicateDecision,
    PredicateList,
    PredicateViolation,
    ProfileDeleted,
    ProfileEdit,
    ProfileRef,
    ProfileSchema,
    ProfileValidation,
    ProfileValidationWarning,
    ProfileVersion,
    ProfileVersionList,
    ProfileVersionRef,
    ProfileWarning,
    ProfileWritten,
    PromotedDraft,
    Promotion,
    Proposal,
    ProposalApproved,
    ProposalDismissed,
    ProposalEvidence,
    Proposition,
    Provider,
    ProviderBinding,
    ProviderBindings,
    ProviderBindingWritten,
    ProviderDetail,
    ProviderList,
    ProviderMutation,
    Pruned,
    PruneExcluded,
    PruneImpact,
    PrunePosition,
    PruneResult,
    PruneRun,
    PruneRuns,
    PruneTarget,
    QualityStatus,
    QualityStatusValue,
    QuestionOption,
    QueuedBackfill,
    QueuedTurn,
    RatingApplied,
    RatingCheck,
    RatingCheckStarted,
    RatingSuggestion,
    RatingVersion,
    RatingVersionList,
    ReactivationCheck,
    RebuildQueued,
    RebuildResult,
    Recommendation,
    Recommended,
    Reconcile,
    Reconciliation,
    ReconciliationCount,
    ReconciliationListing,
    ReembedRetry,
    ReembedStatus,
    ReferenceImage,
    ReferencingStory,
    RegenerateResult,
    RejectedBinding,
    RelatedItem,
    RelatedPage,
    RelationItem,
    RelationRecord,
    Relations,
    RelationshipBody,
    ReplyMessage,
    RequiredEffect,
    RequiredModule,
    RequiredSetting,
    ResolutionBody,
    RespondResponse,
    RetirementBody,
    RetrievalBody,
    RetrievedMemoryBody,
    Reverification,
    RewriteApplied,
    RewriteApply,
    RewriteList,
    RewritePreview,
    RewriteQuestion,
    RewriteSession,
    RewriteSessionWritten,
    RewriteSummary,
    RoleCharacter,
    RoleEligibility,
    RoleRelationship,
    RunEstimate,
    Scenario,
    ScenarioAnchor,
    ScenarioCard,
    ScenarioCardMedia,
    ScenarioCardMediaSet,
    ScenarioCardRole,
    ScenarioCast,
    ScenarioChannel,
    ScenarioClock,
    ScenarioDraft,
    ScenarioListItem,
    ScenarioMediaSet,
    ScenarioMetadata,
    ScenarioOrigin,
    ScenarioPage,
    ScenarioPolicy,
    ScenarioPublicCard,
    ScenarioRole,
    ScenarioScheduledEvent,
    ScenarioState,
    ScenarioStatus,
    ScenarioVideo,
    SceneBody,
    ScheduledEvent,
    ScheduledEvents,
    ScheduledEventWritten,
    Seed,
    SettingTags,
    SettledNow,
    ShadowSample,
    StartingState,
    StateConstraints,
    StateDeclared,
    StateDefinition,
    StateDefinitionWritten,
    StateEffect,
    StateProposal,
    StateProposals,
    StateView,
    StateWritten,
    StoredResult,
    Story,
    StoryBackfill,
    StoryCertificationRunRequested,
    StoryCharacter,
    StoryCharacterAdded,
    StoryCreated,
    StoryList,
    StoryOperations,
    StoryPause,
    StoryPauseProgress,
    StoryProfileRef,
    StoryProfileWrite,
    StoryPruning,
    StoryRating,
    StoryReembed,
    StorySettings,
    StorySettingsDefaults,
    StorySettingsWrite,
    StoryState,
    StoryStates,
    StoryStatus,
    StoryStatusChange,
    StorySummary,
    SubjectRef,
    SuggestionRow,
    SummarySegment,
    SwapResult,
    Thunk,
    TruncateResult,
    TurnBudget,
    TurnClock,
    TurnGuardrail,
    TurnMessage,
    TurnRange,
    TurnReport,
    TurnRole,
    TurnSpan,
    TurnSummary,
    TurnWarning,
    TypedLiteral,
    UnavailableCharacter,
    UndeliveredSetting,
    UnmetRecommendation,
    UntruncateResult,
    VariantChain,
    Variants,
    Visibility,
    VisualIdentity,
    VisualStyle,
    WorkersCheck,
    World,
    WorldClock,
    YieldCluster,
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
EFFECTIVE_DOCUMENTS = ("deployment_defaults", "effective")
OWNERSHIP_BODIES = ("OwnerBody", "StoryOwnerBody", "RelationBody", "StoryRelationBody")
CORE_BACKFILL = "epistrel__core__backfill__Backfill"
CONFLICT_REF = {"$ref": "#/components/schemas/Conflict"}
GAP_FILLABLE = "gap_fillable"
PROFILE_LIST = "ProfileListBody"
ENGINE_PROFILE_REF = {"$ref": "#/components/schemas/EngineProfile"}


def _widened(doc: dict[str, Any]) -> dict[str, Any]:
    """``doc`` with the Engine's ``EntryLocation`` widened by ``ElementLocation``'s ``media_id``:
    the one Console class parses both (#64), so a model holding a ``ValidationReport`` (a
    publish's answer, #66) is compared against this. ``EngineProfile.settings`` is widened to
    any object (#78). The owner and relation bodies carry both revisions, optional (#79).
    ``DepictedSubject`` gains ``SubjectRef``'s optional ``story`` (#291). ``StoryPauseProgressBody``
    is read leniently (#295). ``EffectiveConfig.gap_fillable`` (#340) and the profile list's
    ``quality`` (#339) are optional: an Engine before nathanpond/Epistrel#789/#790 (the release
    smoke's) omits them."""
    import copy

    widened = copy.deepcopy(doc)
    schemas = widened["components"]["schemas"]
    schemas["EntryLocation"]["properties"]["media_id"] = schemas["ElementLocation"]["properties"][
        "media_id"
    ]
    schemas["EngineProfile"]["properties"]["settings"] = SETTINGS_ANY
    schemas["ProfileBody"]["properties"]["settings"] = SETTINGS_ANY  # #84: create and copy
    for key in EFFECTIVE_DOCUMENTS:  # #85: a story's settings documents, raw
        schemas["EffectiveConfig"]["properties"][key] = SETTINGS_ANY
    schemas["OverridesRef"]["properties"]["settings"] = SETTINGS_ANY
    for name in OWNERSHIP_BODIES:
        body = schemas[name]
        body["properties"].update(dict.fromkeys(REVISIONS, NULLABLE_INT))
        body["required"] = [r for r in body["required"] if r not in REVISIONS]
    # #291: one ``SubjectRef`` model reads ``DepictedSubject`` too; M7 gave ``SubjectRef`` an
    # optional ``story`` that ``DepictedSubject`` lacks, so it is widened by that one member.
    schemas["DepictedSubject"]["properties"]["story"] = schemas["SubjectRef"]["properties"]["story"]
    # #292: a backfill's conflict_list items are typed as any JSON, though the Engine fills
    # it with Conflict objects; the model reads them entry by entry as Conflict (E15).
    schemas[CORE_BACKFILL]["properties"]["conflict_list"]["items"] = CONFLICT_REF
    # #295 (E10): a story read's ``pause.progress`` is read leniently: a backfill's pause carries
    # its own progress (nathanpond/Epistrel#783), so ``done``/``total`` are optional and the
    # backfill's ``chunks_done``/``chunks_total``/``phase`` are optional members too.
    progress = schemas["StoryPauseProgressBody"]
    progress["properties"].update(
        {
            "done": NULLABLE_INT,
            "total": NULLABLE_INT,
            "chunks_done": NULLABLE_INT,
            "chunks_total": NULLABLE_INT,
            "phase": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        }
    )
    progress["required"] = []
    # #340: ``gap_fillable`` (nathanpond/Epistrel#790) is required in the schema; the model reads
    # it optional and nullable, so an Engine before #790 still parses (the boxes then fall back).
    effective = schemas["EffectiveConfig"]
    effective["properties"][GAP_FILLABLE] = {
        "anyOf": [effective["properties"][GAP_FILLABLE], {"type": "null"}]
    }
    effective["required"] = [r for r in effective["required"] if r != GAP_FILLABLE]
    # #339: the list's items are ``ListedProfile`` (``EngineProfile`` with ``quality`` required,
    # nathanpond/Epistrel#789); the one ``EngineProfile`` model reads both, ``quality`` optional.
    schemas[PROFILE_LIST]["properties"]["items"]["items"] = ENGINE_PROFILE_REF
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
        (Backfill, "epistrel__core__profiles__impact__Backfill"),  # M7 renamed it (E15)
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
        (ErasureResult, "ErasureBody"),
        (ErasureCandidate, "CandidateBody"),
        (LedgerEntry, "LedgerEntryBody"),
        (ReferencingStory, "ReferencingStoryBody"),
        # #78: engine profiles.
        (EngineProfile, "EngineProfile"),
        (EngineProfilePage, "ProfileListBody"),
        (ProfileVersion, "ProfileVersion"),
        (ProfileVersionList, "ProfileVersionsBody"),
        (SettingTags, "SettingTagsBody"),
        (ProfileSchema, "ProfileSchemaBody"),
        # #84: engine profile writes.
        (ProfileValidation, "ProfileValidation"),
        (MissingDependency, "MissingDependency"),
        (RequiredModule, "epistrel__core__profiles__validate__RequiredModule"),
        (UndeliveredSetting, "UndeliveredSetting"),
        (ProfileValidationWarning, "ProfileValidationWarning"),
        (ProfileWritten, "ProfileBody"),
        (ProfileEdit, "ProfileEditBody"),
        (ProfileWarning, "ProfileWarning"),
        (ProfileDeleted, "ProfileDeletedBody"),
        # #85: a story's effective configuration.
        (ProfileRef, "ProfileRef"),
        (OverridesRef, "OverridesRef"),
        (EffectiveConfig, "EffectiveConfig"),
        (StoryProfileWrite, "StoryProfileWriteBody"),
        # #79: owners and relations.
        (OwnerTransfer, "OwnerBody"),
        (OwnerTransfer, "StoryOwnerBody"),
        (RelationRecord, "RelationBody"),
        (RelationRecord, "StoryRelationBody"),
        (RelationItem, "RelationItem"),
        (Relations, "RelationsBody"),
        # #82: the rating suggestion.
        (RatingSuggestion, "RatingSuggestion"),
        (SuggestionRow, "SuggestionRow"),
        (Concern, "Concern"),
        (Element, "Element"),
        (EntryRef, "EntryRef"),
        (EvaluatedRating, "EvaluatedRating"),
        (UnavailableCharacter, "UnavailableCharacter"),
        # #83: message feedback.
        (MessageFeedback, "Feedback"),
        (FeedbackContext, "FeedbackContext"),
        (FeedbackHistoryEntry, "FeedbackHistoryEntry"),
        (ModelIdentity, "ModelIdentity"),
        # #140: a story's hot set.
        (HotSetBody, "HotSetBody"),
        (HotSetBudgets, "HotSetBudgets"),
        (HotCountsBody, "HotCountsBody"),
        (HotSetPins, "HotSetPins"),
        (EpisodeBody, "EpisodeBody"),
        (RetirementBody, "RetirementBody"),
        # #141: the retrieval explorer.
        (RetrievalBody, "RetrievalBody"),
        (RetrievedMemoryBody, "RetrievedMemoryBody"),
        (ResolutionBody, "ResolutionBody"),
        (MentionBody, "MentionBody"),
        # #143: memory pins and quarantine.
        (MemoryView, "MemoryView"),
        (MemoryWritten, "MemoryWritten"),
        (PredicateList, "PredicateListBody"),
        (Predicate, "PredicateBody"),
        (Implication, "ImplicationBody"),
        (Proposal, "ProposalBody"),
        (PredicateDecision, "PredicateDecisionBody"),
        (PredicateViolation, "ViolationBody"),
        # #137: canon facts.
        (FactListBody, "FactListBody"),
        (FactBody, "FactBody"),
        (Proposition, "Proposition"),
        (TypedLiteral, "TypedLiteral"),
        (Interval, "Interval"),
        (Instant, "Instant"),
        (Bound, "Bound"),
        (Visibility, "Visibility"),
        # #148: thunks and the promissory ledger.
        (Ledger, "LedgerBody"),
        (Thunk, "ThunkBody"),
        (LedgerItem, "LedgerItemBody"),
        (RejectedBinding, "RejectedBindingBody"),
        (InspectPosition, "epistrel__api__routes__inspect__PositionBody"),
        # #149: eval reports.
        (EscalationYield, "EscalationYieldBody"),
        (YieldCluster, "YieldClusterBody"),
        (ExtractionShadow, "ExtractionShadowBody"),
        (ShadowSample, "ShadowSampleBody"),
        # #58: the scene and an entity's standing profile.
        (SceneBody, "SceneBody"),
        (EntityProfileBody, "EntityProfileBody"),
        (EntityBody, "EntityBody"),
        (RelationshipBody, "RelationshipBody"),
        (PlacementBody, "PlacementBody"),
        (InventoryBody, "InventoryBody"),
        # #150: the embedding re-embed run.
        (ReembedStatus, "ReembedStatusBody"),
        (StoryReembed, "StoryReembedBody"),
        (ReembedRetry, "ReembedRetryBody"),
        # #57: the guarantee table.
        (GuaranteeTable, "GuaranteeTable"),
        (GuaranteeStatus, "GuaranteeStatus"),
        (GuaranteeRequiredModule, "epistrel__core__profiles__guarantees__RequiredModule"),
        (RequiredSetting, "RequiredSetting"),
        # #183: the nested components M5's changed components reference.
        (Adjudication, "AdjudicationBody"),
        (TurnRole, "RoleBody"),
        (EscalationDeferred, "EscalationDeferredBody"),
        (DeferredCharacter, "DeferredCharacterBody"),
        (Reconcile, "ReconcileBody"),
        (Participation, "ParticipationBody"),
        (ModeledAt, "ModeledAtBody"),
        (CouldHaveKnownFlag, "CouldHaveKnownFlagBody"),
        (HiddenFact, "HiddenFactBody"),
        (LocationAdjacency, "LocationAdjacency"),
        (ScenarioChannel, "ScenarioChannel"),
        (NestedSeed, "NestedSeed"),
        # #186: M5's character knowledge and character controls.
        (Beliefs, "BeliefsBody"),
        (BeliefItem, "BeliefItemBody"),
        (IdentityItem, "IdentityItemBody"),
        (NarrativePosition, "NarrativePositionBody"),
        (Divergences, "DivergencesBody"),
        (DivergenceItem, "DivergenceItemBody"),
        (Exposures, "ExposuresBody"),
        (ExposureItem, "ExposureItemBody"),
        (BeliefWritten, "BeliefWritten"),
        (AuthoredBelief, "AuthoredBeliefBody"),
        (BeliefTransition, "TransitionBody"),
        (AuthorPosition, "epistrel__api__routes__admin__beliefs__PositionBody"),
        (CharacterWeighted, "CharacterWeightedBody"),
        (ParticipationSet, "ParticipationSetBody"),
        # #231: the nested components M6's changed components reference.
        (StateEffect, "StateEffectModel"),
        (Notice, "NoticeBody"),
        (TurnClock, "TurnClockBody"),
        (ClockValue, "ClockValueBody"),
        (RequiredEffect, "RequiredEffectBody"),
        (PendingEffect, "PendingEffectBody"),
        (ReconciliationCount, "ReconciliationCountBody"),
        (StoryProfileRef, "StoryProfileRef"),
        # #232: the M6 operations' answers.
        (StoryStates, "StatesBody"),
        (StoryState, "StateBody"),
        (StateView, "StateViewBody"),
        (StateWritten, "StateWrittenBody"),
        (StateDeclared, "StateDeclaredBody"),
        (StateDefinitionWritten, "StateDefinitionWriteBody"),
        (StateProposals, "StateProposalsBody"),
        (StateProposal, "StateProposalBody"),
        (ProposalEvidence, "EvidenceBody"),
        (AnalysisQueued, "AnalysisQueuedBody"),
        (ProposalApproved, "ProposalApprovedBody"),
        (ProposalDismissed, "ProposalDismissedBody"),
        (WorldClock, "ClockBody"),
        (ClockSettings, "ClockSettingsBody"),
        (ClockSettingsWritten, "ClockSettingsWriteBody"),
        (ClockAdvanced, "ClockAdvancedBody"),
        (ScheduledEvents, "ScheduledEventsBody"),
        (ScheduledEvent, "ScheduledEventBody"),
        (ScheduledEventWritten, "ScheduledEventWrittenBody"),
        (ProviderList, "ProviderList"),
        (Provider, "ProviderBody"),
        (ProviderDetail, "ProviderItem"),
        (ProviderMutation, "ProviderMutation"),
        (ProviderBindings, "BindingList"),
        (ProviderBinding, "BindingBody"),
        (ProviderBindingWritten, "BindingMutation"),
        (ExternalFacts, "ExternalFactsBody"),
        (ExternalRead, "ExternalReadItemBody"),
        (ExternalFact, "ExternalFactBody"),
        (Reconciliation, "Reconciliation"),
        (ReconciliationListing, "Listing"),
        (ListingOutcome, "OutcomeView"),
        (Reverification, "Reverification"),
        (SettledNow, "SettledNowBody"),
        (Compensated, "CompensatedBody"),
        (MemoryList, "MemoryListBody"),
        (ListedMemory, "ListedMemory"),
        (ConsolidationProvenance, "ConsolidationProvenance"),
        # #267: the scenario editor's and detail page's read-only views.
        (ScenarioState, "ScenarioState"),
        (StateConstraints, "StateConstraints"),
        # #268: the detail page's read-only clock and event views.
        (ScenarioClock, "ScenarioClock"),
        (ScenarioAnchor, "ScenarioAnchor"),
        (ScenarioScheduledEvent, "ScenarioScheduledEvent"),
        # M7 (#291): the nested components M7's changed components reference.
        (CoverageGap, "CoverageGap"),
        (FloorsMet, "FloorsMet"),
        (CertificationLost, "CertificationLost"),
        (ForwardOnlyGap, "ForwardOnlyGap"),
        (TurnSpan, "TurnSpan"),
        (QueuedBackfill, "Queued"),
        (BackfillStarted, "BackfillStartedBody"),
        (PromotedDraft, "PromotedDraftBody"),
        (AmendmentChecks, "AmendmentChecksBody"),
        (StoryPruning, "PruningBody"),
        (ModelTiers, "ModelTiers"),
        (DirectedPending, "DirectedPendingBody"),
        # #292: the M7 operations' answers (E15's names; a prefixed name's row names its
        # component).
        (HeldRender, "HeldRender"),
        (QuestionOption, "QuestionOption"),
        (RewriteQuestion, "RewriteQuestion"),
        (BlastRadius, "BlastRadius"),
        (RewritePreview, "epistrel__orchestrator__rewrite__api__Preview"),
        (RewriteSession, "RewriteSession"),
        (RewriteApply, "Applied"),
        (RewriteSessionWritten, "RewriteSessionBody"),
        (RewriteSummary, "RewriteSummary"),
        (RewriteList, "RewriteList"),
        (RewriteApplied, "AppliedBody"),
        (DirectiveApplied, "DirectiveApplied"),
        (Directive, "DirectiveBody"),
        (AmendmentSummary, "AmendmentSummary"),
        (AmendmentPage, "AmendmentPage"),
        (AmendmentDetail, "AmendmentDetail"),
        (TurnRange, "TurnRange"),
        (Estimate, "Estimate"),
        (BackfillProgress, "Progress"),
        (ConflictCounts, "ConflictCounts"),
        (Recommended, "Recommended"),
        (ConflictStatement, "Statement"),
        (BackfillConflict, "Conflict"),
        (StoryBackfill, "epistrel__core__backfill__Backfill"),
        (GapFillPreview, "epistrel__core__backfill__Preview"),
        (GapFill, "GapFillBody"),
        (BackfillCancelled, "BackfillCancelledBody"),
        (BackfillConflictResolved, "epistrel__api__routes__backfill__ResolvedBody"),
        (RunEstimate, "RunEstimate"),
        (CertificationRunRequested, "CertificationRunBody"),
        (StoryCertificationRunRequested, "StoryCertificationRunBody"),
        (CertificationRun, "CertificationRun"),
        (FloorResult, "FloorResult"),
        (Certification, "Certification"),
        (CertificationPage, "CertificationPage"),
        (CertificationImported, "CertificationImportedBody"),
        (CertificationInvalidated, "CertificationInvalidatedBody"),
        (EvalMetric, "EvalMetric"),
        (EvalRun, "EvalRun"),
        (EvalRunPage, "EvalRunPage"),
        (PrunePosition, "Position"),
        (PruneTarget, "PruneTarget"),
        (PruneExcluded, "Excluded"),
        (PruneImpact, "PruneImpact"),
        (PruneResult, "PruneResult"),
        (Pruned, "PruneBody"),
        (PruneRun, "PruneRun"),
        (PruneRuns, "PruneRuns"),
        (ExportStarted, "ExportStartedBody"),
        (ExportJob, "ExportJob"),
        (ImportImpact, "ImportImpactBody"),
        (ImportStarted, "ImportStartedBody"),
        (ImportJob, "ImportJob"),
        (OperationSummary, "OperationSummaryBody"),
        (StoryOperations, "OperationsBody"),
        (OutboxPosition, "OutboxPosition"),
        (OutboxRow, "OutboxRow"),
        (Outbox, "OutboxBody"),
        (ListingResolved, "epistrel__api__routes__reconciliation__ResolvedBody"),
        (StateDefinition, "StateDefinitionBody"),
    ],
)
def test_models_match_their_components(model: type, component: str) -> None:
    ref = {"$ref": f"#/components/schemas/{component}"}
    assert compare_model(WIDENED, model, ref, component) == []


def test_scenario_state_views_are_strict() -> None:
    """#267: the fixture's three States parse (``gold`` with its ``StateConstraints``); an
    extra key, or ``constraints.min`` as a string, fails to parse (``extra="forbid"``)."""
    from tests.fixtures import engine_fixture

    stored = engine_fixture("catalog_scenarios_draft_states")["starting_state"]["states"]
    parsed = [ScenarioState.from_json(entry) for entry in stored]
    assert [s.name for s in parsed if s is not None] == ["gold", "door_open", "bank_balance"]
    gold = parsed[0]
    assert gold is not None
    assert gold.constraints == StateConstraints(min=0)
    assert ScenarioState.from_json({**stored[0], "colour": "gold"}) is None
    assert ScenarioState.from_json({**stored[0], "constraints": {"min": "0"}}) is None
    assert StateConstraints.from_json({"min": 0, "step": 1}) is None


def test_scenario_clock_views_are_strict() -> None:
    """#268: the fixture's clock (with its anchor) and its events (an int and an ``HH:MM``
    ``planned``) parse; a clock with an extra key, and an event with ``planned: 1.5``, fail to
    parse (``extra="forbid"``, ``StrictInt``)."""
    from tests.fixtures import engine_fixture

    start = engine_fixture("catalog_scenarios_draft_clock")["starting_state"]
    clock = ScenarioClock.from_json(start["clock"])
    assert clock is not None
    assert clock.anchor == ScenarioAnchor(2026, 3, 14, "Saturday", "06:00", "morning")
    assert clock.pacing_table == [[0, 50, 5], [50, None, 15]]
    events = [ScenarioScheduledEvent.from_json(e) for e in start["scheduled_events"]]
    assert [e.planned for e in events if e is not None] == ["18:00", 130, "02:10"]
    assert ScenarioClock.from_json({**start["clock"], "cells": 3}) is None
    assert ScenarioAnchor.from_json({"time": "06:00", "hour": 6}) is None
    assert (
        ScenarioScheduledEvent.from_json({**start["scheduled_events"][1], "planned": 1.5}) is None
    )
    assert ScenarioScheduledEvent.from_json({"proposition": "x"}) is None  # planned required


def test_operation_labels_match_the_schema() -> None:
    """``OperationLabel`` lists the recorded enum (#257's filter reads it); the record's
    ``label`` itself is an open enum (C2)."""
    recorded = DOC["components"]["schemas"]["OperationLabel"]["enum"]
    assert list(typing.get_args(OperationLabel)) == recorded
    assert len(recorded) == 6


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


def test_profile_body_settings_is_the_only_widening_of_profile_body() -> None:
    """#84: like ``EngineProfile``, the create/copy answer differs by ``settings`` alone."""
    recorded = DOC["components"]["schemas"]["ProfileBody"]["properties"]
    widened = WIDENED["components"]["schemas"]["ProfileBody"]["properties"]
    assert [k for k in recorded if recorded[k] != widened[k]] == ["settings"]


def test_the_effective_config_widens_only_its_settings_documents() -> None:
    """#85: ``EffectiveConfig`` differs by its two settings documents and ``OverridesRef`` by
    ``settings`` alone, each an object in the recorded schema (of the modules it knew). #340
    adds ``gap_fillable``, an array in the recording, made nullable and not required."""
    schemas = DOC["components"]["schemas"]
    widened = WIDENED["components"]["schemas"]
    for component, keys in (
        ("EffectiveConfig", list(EFFECTIVE_DOCUMENTS)),
        ("OverridesRef", ["settings"]),
    ):
        recorded = schemas[component]["properties"]
        differs = [k for k in recorded if recorded[k] != widened[component]["properties"][k]]
        extra = [GAP_FILLABLE] if component == "EffectiveConfig" else []
        assert sorted(differs) == sorted(keys + extra)
        assert all(recorded[k]["type"] == "object" for k in keys)
    assert schemas["EffectiveConfig"]["properties"][GAP_FILLABLE]["type"] == "array"
    assert GAP_FILLABLE in schemas["EffectiveConfig"]["required"]
    assert GAP_FILLABLE not in widened["EffectiveConfig"]["required"]


def test_the_profile_list_widens_only_its_items_to_engine_profile() -> None:
    """#339: ``ListedProfile`` is ``EngineProfile`` with ``quality`` required and not nullable,
    so reading the list's items as ``EngineProfile`` widens nothing but ``quality``."""
    schemas = DOC["components"]["schemas"]
    listed, profile = schemas["ListedProfile"], schemas["EngineProfile"]
    differs = [
        k for k in listed["properties"] if listed["properties"][k] != profile["properties"][k]
    ]
    assert differs == ["quality"]
    assert set(listed["properties"]) == set(profile["properties"])
    floors = {"$ref": "#/components/schemas/FloorsMet"}
    assert listed["properties"]["quality"]["$ref"] == floors["$ref"]
    assert floors in profile["properties"]["quality"]["anyOf"]
    assert set(listed["required"]) - set(profile["required"]) == {"quality"}
    assert set(profile["required"]) <= set(listed["required"])
    widened = WIDENED["components"]["schemas"][PROFILE_LIST]["properties"]["items"]["items"]
    assert widened == ENGINE_PROFILE_REF


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


def test_co_author_knowledge_enums_match_the_schema() -> None:
    """#233: every surface's co-author knowledge values are the recorded enum, and the creation
    default is the Engine's."""
    from console.forms.scenario import C7_DEFAULTS, C7_SETTINGS
    from console.forms.stories import CREATION_SETTINGS
    from console.forms.story_from_scenario import OVERRIDE_TYPES
    from console.story_settings import CONSTANT_DEFAULTS, SETTING_CHOICES

    schemas = DOC["components"]["schemas"]

    def enum(node: dict[str, typing.Any]) -> list[str]:
        options = node.get("anyOf") or [node]
        (values,) = [o["enum"] for o in options if "enum" in o]
        return list(values)

    defaults = schemas["StorySettingsDefaults"]["properties"]["co_author_knowledge"]
    override = OVERRIDE_TYPES["story_settings_defaults.co_author_knowledge"]
    assert override.kind == "enum"
    assert list(override.options) == enum(defaults)
    assert "co_author_knowledge" in C7_SETTINGS
    choices = [value for value, _ in SETTING_CHOICES["co_author_knowledge"]]
    assert choices == enum(schemas["StorySettingsPatch"]["properties"]["co_author_knowledge"])
    creation = schemas["CreationSettings"]["properties"]["co_author_knowledge"]
    assert "anyOf" not in creation  # a plain enum: never null at creation
    assert choices == enum(creation)
    default = creation["default"]
    assert CONSTANT_DEFAULTS["co_author_knowledge"] == default
    assert CREATION_SETTINGS["co_author_knowledge"] == default
    assert C7_DEFAULTS["co_author_knowledge"] == default


def test_tie_break_choices_match_the_schema_pattern() -> None:
    """#198: the one tie-break list both forms offer fits both schemas' pattern."""
    import re

    from console.story_settings import SETTING_CHOICES

    schemas = DOC["components"]["schemas"]
    for component in ("CreationSettings", "StorySettingsDefaults"):
        node = schemas[component]["properties"]["simultaneous_tie_break"]
        (pattern,) = [o["pattern"] for o in node.get("anyOf") or [node] if "pattern" in o]
        for value, _ in SETTING_CHOICES["simultaneous_tie_break"]:
            assert re.fullmatch(pattern, value), (component, value)


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


def test_a_pattern_properties_map_is_a_dict() -> None:
    """#183: an object with one ``patternProperties`` entry (and no ``properties``) is a
    ``dict[str, X]`` compared on its value schema; a wrong value type or two patterns bite."""
    one = {
        "type": "object",
        "patternProperties": {"^[a-z]+$": {"type": "array", "items": {"type": "string"}}},
    }
    assert compare_type(DOC, dict[str, list[str]], one, "groups") == []
    assert compare_type(DOC, dict[str, int], one, "groups") != []
    two = {
        "type": "object",
        "patternProperties": {"^a$": {"type": "integer"}, "^b$": {"type": "integer"}},
    }
    assert compare_type(DOC, dict[str, int], two, "groups") != []


def test_an_open_enum_reads_a_const() -> None:
    """#183: an ``OPEN_ENUM`` ``str`` passes against a ``const`` (a one-member enum), while a
    plain ``str`` against an ``enum`` is still reported."""
    from dataclasses import field as dc_field

    from console.engine.models import OPEN_ENUM

    @dataclass(frozen=True)
    class Deferred:
        character_id: str
        fixed: bool
        reason: str = dc_field(metadata={OPEN_ENUM: True})

    ref = {"$ref": "#/components/schemas/DeferredCharacterBody"}
    assert compare_model(DOC, Deferred, ref, "Deferred") == []

    @dataclass(frozen=True)
    class Plain:
        object: str
        winner: str
        loser: str
        rule: str

    problems = compare_model(DOC, Plain, {"$ref": "#/components/schemas/AdjudicationBody"}, "P")
    assert any("enum" in p for p in problems)


M5_OPERATIONS = (
    "GET /stories/{story_id}/inspect/beliefs",
    "GET /stories/{story_id}/inspect/divergences",
    "GET /stories/{story_id}/inspect/exposures",
    "POST /admin/stories/{story_id}/beliefs",
    "PATCH /stories/{story_id}/characters/{character_id}/participation",
    "POST /stories/{story_id}/characters/{character_id}/weight",
    "POST /stories/{story_id}/entities/{entity_id}/model",
)


@pytest.mark.parametrize("name", M5_OPERATIONS)
def test_the_m5_operations_are_registered(name: str) -> None:
    """#186: the seven M5 operations are registered with a response model, so
    ``test_registered_operations_match_the_recorded_schema`` checks each one."""
    assert name in OPERATIONS
    assert OPERATIONS[name].response_model is not None


def test_open_enum_reads_a_const() -> None:
    """#186: the three M5 pages' ``output_viewpoint`` is a bare ``const`` in the schema, read as
    a one-member enum by an ``OPEN_ENUM`` field. **Complement:** an open enum over a schema
    string with neither ``enum`` nor ``const`` is still a problem."""
    from dataclasses import field as dc_field

    from console.engine.models import OPEN_ENUM

    for component in ("BeliefsBody", "DivergencesBody", "ExposuresBody"):
        node = DOC["components"]["schemas"][component]["properties"]["output_viewpoint"]
        assert node.get("const") == "omniscient"
        assert "enum" not in node

    @dataclass(frozen=True)
    class Page:
        items: list[DivergenceItem]
        revision: int
        next_after_id: str | None
        output_viewpoint: str = dc_field(default="omniscient", metadata={OPEN_ENUM: True})

    ref = {"$ref": "#/components/schemas/DivergencesBody"}
    assert compare_model(DOC, Page, ref, "Page") == []

    @dataclass(frozen=True)
    class Plain:  # ``BeliefItemBody.kind`` is a plain string: no enum, no const
        belief_id: str
        proposition_id: str
        proposition: Any
        polarity: str
        current_level: str
        current_scalar: float | None
        since: NarrativePosition
        provenance: str | None
        provenance_source_character: str | None
        held_from: NarrativePosition
        held_to: NarrativePosition | None
        valid_from: int | None
        valid_to: int | None
        tx_from: str
        provenance_kind: str | None = dc_field(metadata={OPEN_ENUM: True})
        kind: str = dc_field(metadata={OPEN_ENUM: True})
        attributed_holder_id: str | None = None  # M7 (#291)
        attributed_level: str | None = None
        depth: int | None = None
        speaker_character_id: str | None = None

    problems = compare_model(DOC, Plain, {"$ref": "#/components/schemas/BeliefItemBody"}, "P")
    assert problems == ["P.kind: an open enum's schema is not a string enum"]


# #232: ``OPERATIONS``' keys on main before M6's operations (generated once from origin/main;
# a frozen literal, not a git call at test time).
MAIN_OPERATIONS = frozenset(
    {
        "DELETE /admin/content-ratings/{rating_id}",
        "DELETE /admin/model-config/{purpose}",
        "DELETE /admin/model-config/{purpose}/api-key",
        "DELETE /admin/policies/{policy_id}",
        "DELETE /admin/stories/{story_id}/facts/{fact_id}/lock",
        "DELETE /admin/stories/{story_id}/memories/{memory_id}/quarantine",
        "DELETE /catalog/characters/{character_id}",
        "DELETE /catalog/characters/{character_id}/relations/{relation}/{principal}",
        "DELETE /catalog/scenarios/{scenario_id}",
        "DELETE /catalog/scenarios/{scenario_id}/relations/{relation}/{principal}",
        "DELETE /engine-profiles/{profile_id}",
        "DELETE /engine-profiles/{profile_id}/relations/{relation}/{principal}",
        "DELETE /media/{media_id}",
        "DELETE /media/{media_id}/relations/{relation}/{principal}",
        "DELETE /personas/{persona_id}",
        "DELETE /personas/{persona_id}/relations/{relation}/{principal}",
        "DELETE /stories/{story_id}",
        "DELETE /stories/{story_id}/memories/{memory_id}/pin",
        "DELETE /stories/{story_id}/messages/{message_id}/feedback",
        "DELETE /stories/{story_id}/relations/{relation}/{principal}",
        "GET /admin/config-changes",
        "GET /admin/content-ratings",
        "GET /admin/content-ratings/{rating_id}",
        "GET /admin/content-ratings/{rating_id}/versions",
        "GET /admin/erasure/boundary",
        "GET /admin/erasure/status",
        "GET /admin/evals/escalation-yield",
        "GET /admin/evals/extraction-shadow",
        "GET /admin/model-config",
        "GET /admin/model-config/{purpose}",
        "GET /admin/offboarding/{job_id}",
        "GET /admin/policies",
        "GET /admin/policies/{policy_id}",
        "GET /admin/reembed/status",
        "GET /admin/settings",
        "GET /admin/stories/{story_id}/facts",
        "GET /admin/stories/{story_id}/hot-set",
        "GET /catalog/characters",
        "GET /catalog/characters/{character_id}",
        "GET /catalog/characters/{character_id}/companion",
        "GET /catalog/characters/{character_id}/relations",
        "GET /catalog/characters/{character_id}/versions",
        "GET /catalog/scenarios",
        "GET /catalog/scenarios/{scenario_id}",
        "GET /catalog/scenarios/{scenario_id}/relations",
        "GET /catalog/scenarios/{scenario_id}/versions",
        "GET /engine-profiles",
        "GET /engine-profiles/schema",
        "GET /engine-profiles/{profile_id}",
        "GET /engine-profiles/{profile_id}/guarantees",
        "GET /engine-profiles/{profile_id}/relations",
        "GET /engine-profiles/{profile_id}/versions",
        "GET /health",
        "GET /media",
        "GET /media/{media_id}",
        "GET /media/{media_id}/content",
        "GET /media/{media_id}/relations",
        "GET /openapi.json",
        "GET /operations/{operation_id}",
        "GET /personas",
        "GET /personas/{persona_id}",
        "GET /personas/{persona_id}/relations",
        "GET /principals/{principal_id}",
        "GET /stories",
        "GET /stories/{story_id}",
        "GET /stories/{story_id}/config-changes",
        "GET /stories/{story_id}/content-rating/checks/{job_id}",
        "GET /stories/{story_id}/custom-instructions",
        "GET /stories/{story_id}/effective-config",
        "GET /stories/{story_id}/entities/{entity_id}/profile",
        "GET /stories/{story_id}/guarantees",
        "GET /stories/{story_id}/history",
        "GET /stories/{story_id}/inspect/beliefs",
        "GET /stories/{story_id}/inspect/divergences",
        "GET /stories/{story_id}/inspect/exposures",
        "GET /stories/{story_id}/inspect/ledger",
        "GET /stories/{story_id}/messages/{message_id}/feedback",
        "GET /stories/{story_id}/pinned-memories",
        "GET /stories/{story_id}/predicates",
        "GET /stories/{story_id}/relations",
        "GET /stories/{story_id}/retrieve",
        "GET /stories/{story_id}/scene",
        "GET /stories/{story_id}/settings",
        "GET /stories/{story_id}/variants",
        "PATCH /catalog/characters/{character_id}",
        "PATCH /catalog/scenarios/{scenario_id}",
        "PATCH /personas/{persona_id}",
        "PATCH /stories/{story_id}/characters/{character_id}/participation",
        "PATCH /stories/{story_id}/profile-overrides",
        "PATCH /stories/{story_id}/settings",
        "POST /admin/content-ratings",
        "POST /admin/erasure",
        "POST /admin/erasure/reconcile",
        "POST /admin/offboarding",
        "POST /admin/policies",
        "POST /admin/reembed/retry",
        "POST /admin/stories/{story_id}/beliefs",
        "POST /admin/stories/{story_id}/consolidate",
        "POST /admin/stories/{story_id}/facts",
        "POST /admin/stories/{story_id}/facts/{fact_id}/lock",
        "POST /admin/stories/{story_id}/facts/{fact_id}/supersede",
        "POST /admin/stories/{story_id}/memories/{memory_id}/quarantine",
        "POST /admin/stories/{story_id}/memories/{memory_id}/sanitize",
        "POST /admin/stories/{story_id}/rebuild",
        "POST /catalog/characters",
        "POST /catalog/characters/{character_id}/archive",
        "POST /catalog/characters/{character_id}/publish",
        "POST /catalog/characters/{character_id}/rating-suggestion",
        "POST /catalog/characters/{character_id}/relations",
        "POST /catalog/characters/{character_id}/unarchive",
        "POST /catalog/scenarios",
        "POST /catalog/scenarios/{scenario_id}/archive",
        "POST /catalog/scenarios/{scenario_id}/publish",
        "POST /catalog/scenarios/{scenario_id}/rating-suggestion",
        "POST /catalog/scenarios/{scenario_id}/relations",
        "POST /catalog/scenarios/{scenario_id}/unarchive",
        "POST /catalog/scenarios/{scenario_id}/validate",
        "POST /engine-profiles",
        "POST /engine-profiles/{profile_id}/copy",
        "POST /engine-profiles/{profile_id}/relations",
        "POST /engine-profiles/{profile_id}/validate",
        "POST /media",
        "POST /media/{media_id}/relations",
        "POST /personas",
        "POST /personas/{persona_id}/relations",
        "POST /stories",
        "POST /stories/{story_id}/archive",
        "POST /stories/{story_id}/characters",
        "POST /stories/{story_id}/characters/{character_id}/promote",
        "POST /stories/{story_id}/characters/{character_id}/weight",
        "POST /stories/{story_id}/entities/{entity_id}/model",
        "POST /stories/{story_id}/fork",
        "POST /stories/{story_id}/impact-preview",
        "POST /stories/{story_id}/ingest",
        "POST /stories/{story_id}/memories/{memory_id}/pin",
        "POST /stories/{story_id}/predicates/{predicate_id}/approve",
        "POST /stories/{story_id}/predicates/{predicate_id}/reject",
        "POST /stories/{story_id}/regenerate",
        "POST /stories/{story_id}/relations",
        "POST /stories/{story_id}/respond",
        "POST /stories/{story_id}/swap",
        "POST /stories/{story_id}/truncate",
        "POST /stories/{story_id}/unarchive",
        "POST /stories/{story_id}/untruncate",
        "PUT /admin/content-ratings/{rating_id}",
        "PUT /admin/model-config/{purpose}",
        "PUT /admin/model-config/{purpose}/api-key",
        "PUT /admin/policies/{policy_id}",
        "PUT /admin/settings",
        "PUT /catalog/characters/{character_id}/listing-status",
        "PUT /catalog/characters/{character_id}/owner",
        "PUT /catalog/scenarios/{scenario_id}/listing-status",
        "PUT /catalog/scenarios/{scenario_id}/owner",
        "PUT /engine-profiles/{profile_id}",
        "PUT /engine-profiles/{profile_id}/owner",
        "PUT /media/{media_id}/owner",
        "PUT /personas/{persona_id}/owner",
        "PUT /stories/{story_id}/content-rating",
        "PUT /stories/{story_id}/custom-instructions",
        "PUT /stories/{story_id}/engine-profile",
        "PUT /stories/{story_id}/messages/{message_id}/feedback",
        "PUT /stories/{story_id}/owner",
        "PUT /stories/{story_id}/pinned-memories",
    }
)

M6_OPERATIONS = (
    "GET /stories/{story_id}/states",
    "GET /stories/{story_id}/states/{name}",
    "PUT /stories/{story_id}/states/{name}",
    "POST /stories/{story_id}/states",
    "PATCH /stories/{story_id}/states/{name}/definition",
    "GET /stories/{story_id}/state-proposals",
    "POST /stories/{story_id}/state-proposals/analyze",
    "POST /stories/{story_id}/state-proposals/{proposal_id}/approve",
    "POST /stories/{story_id}/state-proposals/{proposal_id}/dismiss",
    "GET /stories/{story_id}/clock",
    "PATCH /stories/{story_id}/clock",
    "POST /stories/{story_id}/clock/advance",
    "GET /stories/{story_id}/scheduled-events",
    "POST /stories/{story_id}/scheduled-events",
    "PATCH /stories/{story_id}/scheduled-events/{event_id}",
    "DELETE /stories/{story_id}/scheduled-events/{event_id}",
    "GET /admin/external-providers",
    "GET /admin/external-providers/{name}",
    "POST /admin/external-providers",
    "PUT /admin/external-providers/{name}",
    "DELETE /admin/external-providers/{name}",
    "GET /stories/{story_id}/providers",
    "PUT /stories/{story_id}/providers/{name}/binding",
    "GET /stories/{story_id}/inspect/external-facts",
    "GET /stories/{story_id}/reconciliation",
    "POST /stories/{story_id}/reconciliation/{outbox_id}/settle-now",
    "POST /stories/{story_id}/reconciliation/{outbox_id}/compensate",
    "POST /stories/{story_id}/correct",
    "GET /admin/stories/{story_id}/memories",
    "GET /admin/stories/{story_id}/facts/{fact_id}",
)


@pytest.mark.parametrize("name", M6_OPERATIONS)
def test_the_m6_operations_are_registered(name: str) -> None:
    """#232: the 30 M6 operations are registered, each with a response model but the 204
    provider delete, so ``test_registered_operations_match_the_recorded_schema`` checks each."""
    assert name in OPERATIONS
    if name != "DELETE /admin/external-providers/{name}":
        assert OPERATIONS[name].response_model is not None


M7_OPERATIONS = (
    "POST /stories/{story_id}/rewrites",
    "GET /stories/{story_id}/rewrites",
    "GET /stories/{story_id}/rewrites/{session_id}",
    "POST /stories/{story_id}/rewrites/{session_id}/answers",
    "POST /stories/{story_id}/rewrites/{session_id}/apply",
    "POST /stories/{story_id}/rewrites/{session_id}/cancel",
    "POST /stories/{story_id}/directives",
    "GET /stories/{story_id}/amendments",
    "GET /stories/{story_id}/amendments/{amendment_id}",
    "POST /stories/{story_id}/amendments/{amendment_id}/undo",
    "POST /stories/{story_id}/amendments/{amendment_id}/redo",
    "POST /stories/{story_id}/entities/merge",
    "POST /stories/{story_id}/backfill",
    "GET /stories/{story_id}/backfill",
    "DELETE /stories/{story_id}/backfill",
    "POST /stories/{story_id}/backfill/conflicts/{cid}",
    "POST /engine-profiles/{profile_id}/upgrade-pinned",
    "POST /engine-profiles/{profile_id}/certification-runs",
    "POST /stories/{story_id}/certification-runs",
    "GET /certification-runs/{run_id}",
    "GET /certifications",
    "GET /certifications/{certification_id}",
    "POST /admin/certifications/import",
    "POST /admin/certifications/{certification_id}/invalidate",
    "GET /admin/evals/runs",
    "GET /admin/evals/runs/{run_id}",
    "POST /stories/{story_id}/prune",
    "GET /stories/{story_id}/prune-runs",
    "POST /admin/stories/{story_id}/export",
    "GET /admin/stories/exports/{job_id}",
    "GET /admin/stories/exports/{job_id}/bundle",
    "POST /admin/stories/import",
    "GET /admin/stories/imports/{job_id}",
    "GET /stories/{story_id}/operations",
    "GET /stories/{story_id}/outbox",
    "POST /stories/{story_id}/reconciliation/{listing}/resolve",
    "GET /stories/{story_id}/states/{name}/definition",
)
MAIN_M6 = MAIN_OPERATIONS | frozenset(M6_OPERATIONS)


@pytest.mark.parametrize("name", M7_OPERATIONS)
def test_the_m7_operations_are_registered(name: str) -> None:
    """#292 (E20): the 37 M7 operations are registered, each with a response model but the
    streamed bundle, so test_registered_operations_match_the_recorded_schema checks each."""
    assert name in OPERATIONS
    if name != "GET /admin/stories/exports/{job_id}/bundle":
        assert OPERATIONS[name].response_model is not None
    else:
        assert OPERATIONS[name].response_model is None


def test_every_registered_route_is_new_or_main() -> None:
    """E20 **complement:** against main's set plus M6's, exactly the 37 M7 operations are added
    and none is removed; the single-binding read is still unregistered, and list_stories'
    registration is unchanged."""
    assert len(MAIN_M6) == 193
    assert set(OPERATIONS) - MAIN_M6 == set(M7_OPERATIONS)
    assert MAIN_M6 - set(OPERATIONS) == set()
    assert len(M7_OPERATIONS) == 37
    assert len(set(M7_OPERATIONS)) == 37
    assert "GET /stories/{story_id}/providers/{name}/binding" not in OPERATIONS
    stories = OPERATIONS["GET /stories"]
    assert (stories.method, stories.path, stories.response_model) == ("GET", "/stories", StoryList)


CHANGED_REGISTRATIONS: dict[str, tuple[type, tuple[tuple[int, type], ...]]] = {
    "POST /stories/{story_id}/regenerate": (RegenerateResult, ((202, DirectedPending),)),
    "PUT /stories/{story_id}/engine-profile": (StoryProfileWrite, ((202, StoryProfileWrite),)),
    "PATCH /stories/{story_id}/profile-overrides": (
        StoryProfileWrite,
        ((202, StoryProfileWrite),),
    ),
    "POST /admin/stories/import": (ImportImpact, ((202, ImportStarted),)),
    "POST /stories/{story_id}/backfill": (GapFill, ((202, GapFill),)),
    "POST /stories/{story_id}/directives": (Directive, ((202, Directive),)),
    "POST /engine-profiles/{profile_id}/certification-runs": (
        CertificationRunRequested,
        ((202, CertificationRunRequested),),
    ),
    "POST /stories/{story_id}/certification-runs": (
        StoryCertificationRunRequested,
        ((202, StoryCertificationRunRequested),),
    ),
}


@pytest.mark.parametrize("name", sorted(CHANGED_REGISTRATIONS))
def test_changed_registrations_carry_their_202s(name: str) -> None:
    """E20: the registrations whose 202 answers another model (or the same one) name it, and
    the directed 202s (no job follows) are ended."""
    model, others = CHANGED_REGISTRATIONS[name]
    op = OPERATIONS[name]
    assert (op.response_model, op.status_models) == (model, others)
    ended = name in {"POST /stories/{story_id}/regenerate", "POST /stories/{story_id}/directives"}
    assert op.ended is ended


def test_the_backfill_widening_only_types_conflict_list() -> None:
    """The widened backfill component differs from the recorded one by conflict_list's
    items alone, and the backfill read does not match the recorded one without it."""
    recorded = DOC["components"]["schemas"][CORE_BACKFILL]["properties"]
    widened = WIDENED["components"]["schemas"][CORE_BACKFILL]["properties"]
    assert recorded["conflict_list"]["items"] == {}
    assert widened["conflict_list"]["items"] == CONFLICT_REF
    assert {k: v for k, v in widened.items() if k != "conflict_list"} == {
        k: v for k, v in recorded.items() if k != "conflict_list"
    }
    problems = check_operation(DOC, OPERATIONS["GET /stories/{story_id}/backfill"])
    assert problems
    assert all("conflict_list" in p for p in problems)


VIEWPOINT_READS = (
    "/stories/{story_id}/states",
    "/stories/{story_id}/states/{name}",
    "/stories/{story_id}/scheduled-events",
)


@pytest.mark.parametrize("path", VIEWPOINT_READS)
def test_the_three_viewpoint_reads_are_declared(path: str) -> None:
    """Engine bug nathanpond/Epistrel#682 (D18) is fixed in Engine M7: the recorded schema now
    declares ``?viewpoint=`` on the three reads the client sends it on. #293 updates the
    ``get_states``/``get_state``/``list_scheduled_events`` docstrings and D18's code comment in
    ``console/engine/client.py``. **Complement:** ``inspect/external-facts`` still declares
    it."""
    query = [p["name"] for p in DOC["paths"][path]["get"].get("parameters", [])]
    assert "viewpoint" in query
    declared = DOC["paths"]["/stories/{story_id}/inspect/external-facts"]["get"]["parameters"]
    assert "viewpoint" in [p["name"] for p in declared]


def test_status_enums_are_open() -> None:
    """#291 (#183's rule, E15): the story status and chain membership fields are open enums, and
    their alias Literals (and the quality status one) are pinned to the recorded enums."""
    from console.engine.models import OPEN_ENUM

    schemas = DOC["components"]["schemas"]
    assert set(typing.get_args(StoryStatus)) == set(schemas["StoryStatus"]["enum"])
    assert set(typing.get_args(ChainMembership)) == set(schemas["Membership"]["enum"])
    assert set(typing.get_args(QualityStatusValue)) == set(schemas["QualityStatusValue"]["enum"])
    assert "importing" in typing.get_args(StoryStatus)
    assert "pruned" in typing.get_args(ChainMembership)
    for model, name in (
        (Story, "status"),
        (StorySummary, "status"),
        (StoryStatusChange, "status"),
        (VariantChain, "membership"),
        (FloorsMet, "status"),
    ):
        (f,) = [f for f in fields(model) if f.name == name]
        assert f.metadata.get(OPEN_ENUM), (model.__name__, name)

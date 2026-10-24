"""The client's registered operations and typed models agree with the recorded schema.

The two "bites" tests run first and prove the check fails on a known-bad registry or model.
"""

import types
import typing
from dataclasses import dataclass, fields
from typing import Any, Literal

import pytest

from console.engine.client import OPERATIONS, Operation
from console.engine.models import (
    AgeCondition,
    CatalogSource,
    ChainRef,
    ConfigSources,
    ContentRatingList,
    ContentRatingSummary,
    CreatedCharacter,
    DegradationStep,
    DeploymentSettings,
    DormantChain,
    GuardrailViolation,
    HealthChecks,
    HealthResponse,
    ModelCall,
    ModelConfig,
    ModelConfigEntry,
    ModelConfigItem,
    ModelConfigMutation,
    OmniscientStoryCharacter,
    OperationRecord,
    OperationStage,
    Policy,
    PolicyKind,
    PolicyList,
    PolicyMutation,
    PolicyScope,
    PolicyTarget,
    ProfileVersionRef,
    QualityStatus,
    ScenarioOrigin,
    StoredResult,
    Story,
    StoryCharacter,
    StoryCreated,
    StoryList,
    StoryPause,
    StoryPauseProgress,
    StoryRating,
    StoryStatusChange,
    StorySummary,
    TurnBudget,
    TurnGuardrail,
    TurnReport,
    TurnWarning,
    VariantChain,
    WorkersCheck,
)
from tests.engine.contract import check_operation, compare_model, compare_type
from tests.fakes.engine import recorded_schema

DOC = recorded_schema()
HEALTH_REF = {"$ref": "#/components/schemas/HealthResponse"}


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
    assert check_operation(DOC, OPERATIONS[name]) == []


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
        (CreatedCharacter, "CreatedCharacterBody"),
        (StoryStatusChange, "StatusBody"),
        (ContentRatingSummary, "RatingBody"),
        (ContentRatingList, "RatingList"),
        (DeploymentSettings, "SettingsBody"),
        (ModelConfig, "ModelConfigBody"),
        (ModelConfigEntry, "ModelConfigEntry"),
        (ModelConfigItem, "ModelConfigItem"),
        (ModelConfigMutation, "ModelConfigMutation"),
        (ConfigSources, "Sources"),
        (Policy, "PolicyBody"),
        (PolicyList, "PolicyList"),
        (PolicyMutation, "PolicyMutation"),
    ],
)
def test_models_match_their_components(model: type, component: str) -> None:
    ref = {"$ref": f"#/components/schemas/{component}"}
    assert compare_model(DOC, model, ref, component) == []


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

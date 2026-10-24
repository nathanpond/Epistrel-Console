"""The client's registered operations and typed models agree with the recorded schema.

The two "bites" tests run first and prove the check fails on a known-bad registry or model.
"""

from dataclasses import dataclass
from typing import Literal

import pytest

from console.engine.client import OPERATIONS, Operation
from console.engine.models import (
    HealthChecks,
    HealthResponse,
    StoryList,
    StoryRating,
    StorySummary,
    WorkersCheck,
)
from tests.engine.contract import check_operation, compare_model
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
    ],
)
def test_models_match_their_components(model: type, component: str) -> None:
    ref = {"$ref": f"#/components/schemas/{component}"}
    assert compare_model(DOC, model, ref, component) == []


def test_health_checks_both_declared_statuses() -> None:
    responses = DOC["paths"]["/health"]["get"]["responses"]
    assert {"200", "503"} <= set(responses)

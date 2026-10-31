"""Shared fixtures and builders for the engine-profile write tests (#84)."""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django import forms
from django.http import HttpRequest
from django.test import override_settings

from console import profile_schema
from console.engine.client import EngineClient
from console.engine.models import ProfileSchema
from console.engine_access import engine_client_for
from console.views import engine_panel, profile_defaults, profile_edit, profiles, stories
from tests.fakes.engine import FakeEngine
from tests.fakes.guarantees import answer_guarantees
from tests.fakes.ownership import answer_relations
from tests.fixtures import engine_fixture

LITE = "00000000000000000000PRF001"
OTHER = "01JPRF00000000000000000002"
LIST = "/engine-profiles"
ITEM = "/engine-profiles/{profile_id}"
VALIDATE = "/engine-profiles/{profile_id}/validate"
COPY = "/engine-profiles/{profile_id}/copy"
SCHEMA = "/engine-profiles/schema"
SETTINGS = "/admin/settings"
STORY = "/stories/{story_id}"
SCHEMA_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "profile_schema.json"
STORY_A = "01JSTORY000000000000000001"
STORY_B = "01JSTORY000000000000000002"
FP_BEFORE = "a" * 64
FP_AFTER = "b" * 64


def schema_body() -> dict[str, Any]:
    body: dict[str, Any] = json.loads(SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    return body


def schema_model(body: dict[str, Any] | None = None) -> ProfileSchema:
    parsed = ProfileSchema.from_json(body if body is not None else schema_body())
    assert parsed is not None
    return parsed


def profile(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_engine_profile"))
    return {**body, **changes}


def other_profile(**changes: Any) -> dict[str, Any]:
    """A user profile (not a preset, not the default) at version 3."""
    base: dict[str, Any] = {
        "id": OTHER,
        "name": "Lite-short",
        "owner_principal": "alice",
        "preset": False,
        "version": 3,
        "current_version": 3,
        "settings": {"memory": {"rolling_summary": {"window_messages": 10}}},
    }
    return profile(**{**base, **changes})


def deployment(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_deployment_settings"))
    return {**body, **changes}


def written(**changes: Any) -> dict[str, Any]:
    """A ``ProfileBody`` (create or copy answer)."""
    body = other_profile(version=1, current_version=1)
    body.pop("updated_at", None)
    return {**body, "updated_at": "2026-10-25T10:00:00Z", "config_revision": 7, **changes}


def validation(**changes: Any) -> dict[str, Any]:
    return {"valid": True, "missing_dependencies": [], "undelivered": [], "warnings": [], **changes}


def low_cap_warning() -> dict[str, Any]:
    return {
        "code": "below_safety_minimum",
        "setting": "budgets.model_calls",
        "model_calls": 1,
        "minimum_required": 3,
        "reservation": {"generation": 1, "guardrail_input": 1, "guardrail_output": 1},
    }


def affected(story_id: str = STORY_A, **changes: Any) -> dict[str, Any]:
    return {
        "story_id": story_id,
        "capabilities_gained": [],
        "capabilities_lost": ["polish"],
        "backfill": {"needed": False, "estimate": None},
        "upgrade_unavailable": False,
        "capabilities_after": ["rolling_summary"],
        "fingerprint_before": FP_BEFORE,
        "fingerprint_after": FP_AFTER,
        "quality_status_after": {"status": "uncertified"},
        **changes,
    }


def impact(*stories: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {
        "affected_stories": list(stories),
        "affected_count": len(stories),
        "recommendations_unmet": [],
        "guarantees_lost": [],
        "warnings": [],
        "capabilities_after": None,
        "quality_status_after": None,
        "fingerprint_before": None,
        "fingerprint_after": None,
        **changes,
    }


def edit_body(
    applied: bool, result: dict[str, Any] | None, report: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "applied": applied,
        "config_revision": 8,
        "impact": report if report is not None else impact(),
        "result": result,
    }


def deleted_body(**changes: Any) -> dict[str, Any]:
    return {
        "config_revision": 9,
        "id": OTHER,
        "deleted_at": "2026-10-25T11:00:00Z",
        "warnings": [],
        "stories_using": 0,
        "scenarios_recommending": 0,
        **changes,
    }


def story(story_id: str, name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_story"))
    return {**body, "id": story_id, "name": name}


def fresh_cache() -> Iterator[None]:
    """For an autouse fixture: the profile schema cache cleared around each test."""
    profile_schema.clear_schema_cache()
    yield
    profile_schema.clear_schema_cache()


def fake_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeEngine]:
    """For an ``engine`` fixture: a fake Engine behind every profile view, through the real
    recording client, answering the settings, the schema and the Lite profile by default."""
    fake = FakeEngine()
    answer_relations(fake)
    answer_guarantees(fake)  # #57: the profile page reads its guarantee table

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (profiles, profile_edit, profile_defaults, stories, engine_panel):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    fake.respond("GET", SETTINGS, 200, json=deployment())
    fake.respond("GET", SCHEMA, 200, json=schema_body())
    fake.respond("GET", ITEM, 200, json=profile())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def posted(response: Any, **changes: Any) -> dict[str, Any]:
    """The rendered form's values as a POST would send them (the page's hidden tokens
    included), with ``changes`` laid over: a ``None`` change drops the field."""
    context = response.context
    assert context is not None
    form = context["form"]
    data: dict[str, Any] = {}
    for name, field in form.fields.items():
        value = form[name].value()
        if isinstance(field, forms.BooleanField):
            if value:
                data[name] = "on"
            continue
        if value is None:
            value = ""
        data[name] = value
    data["snapshot"] = context["snapshot"]
    data["idempotency_key"] = context["idempotency_key"]
    for name, value in changes.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
    return data


def field(path: str, root: str = "settings") -> str:
    """The form field name of a dotted settings path."""
    return "__".join((root, *path.split(".")))


def null(path: str, root: str = "settings") -> str:
    return f"null__{field(path, root)}"


def body_of(request: Any) -> Any:
    return json.loads(request.content)


# --- the seeded Structured profile (#204) -------------------------------------------------------
# Copied from the Engine at origin/main: ``core/seeds/profiles.py``'s ``STRUCTURED_M4_SETTINGS``
# (version 1, migration 0047) and ``STRUCTURED_SETTINGS`` (version 2, migration
# ``0080_structured_profile_m5``), and ``M5_PROFILE_DEFAULTS`` (what 0080 adds to the
# deployment's ``profile_defaults``).

STRUCTURED = "00000000000000000000PRF002"
STRUCTURED_V1_SETTINGS: dict[str, Any] = {
    "pipeline": "structured",
    "memory": {
        "rolling_summary": {"enabled": False},
        "episodic": {"enabled": True, "k": 40, "hybrid_search": True, "reranking": False},
        "consolidation": {"enabled": True, "cadence_turns": 20},
    },
    "pins": {"enabled": True, "max_pinned": 50},
    "verification": {
        "level": "entailment",
        "extraction_mode": "always",
        "entailment_gate": 1.0,
        "contradiction_checking": True,
    },
    "commitment": {"enabled": True, "salience": "rules_and_model"},
    "repair": {"max_attempts": 1, "polish": {"enabled": True, "max_regenerations": 2}},
    "budgets": {"model_calls": None, "deadline_ms": 45_000},
    "models": {"generation": {"temperature": 0.8, "top_p": 0.95, "response_length": "medium"}},
}
STRUCTURED_V2_SETTINGS: dict[str, Any] = {
    **STRUCTURED_V1_SETTINGS,
    "character_knowledge": {
        "partitions": True,
        "nested_beliefs": {"enabled": True, "depth": 2},
        "lazy_resolution": True,
        "testimony": "model",
        "max_adjudications_per_turn": 2,
        "max_lazy_resolutions_per_turn": 20,
    },
    "perception": {"level": "full"},
    "multi_character": {"renders": "per_character", "interjection_max_words": 6},
    "budgets": {"model_calls": 16, "deadline_ms": 60_000},
}
M5_PROFILE_DEFAULTS: dict[str, Any] = {
    "character_knowledge": {
        "partitions": False,
        "nested_beliefs": {"enabled": False, "depth": 2},
        "lazy_resolution": False,
        "testimony": "off",
        "max_adjudications_per_turn": 2,
        "max_lazy_resolutions_per_turn": 20,
    },
    "perception": {"level": "none"},
    "multi_character": {"renders": "combined", "interjection_max_words": 6},
}


def structured_profile(version: int = 2, **changes: Any) -> dict[str, Any]:
    """The seeded ``Structured`` profile read at ``version`` (1 or 2; latest is 2)."""
    settings = STRUCTURED_V2_SETTINGS if version == 2 else STRUCTURED_V1_SETTINGS
    base: dict[str, Any] = {
        "id": STRUCTURED,
        "name": "Structured",
        "owner_principal": "system",
        "preset": False,
        "version": version,
        "current_version": 2,
        "settings": copy.deepcopy(settings),
    }
    return profile(**{**base, **changes})


def m5_deployment(**changes: Any) -> dict[str, Any]:
    """The deployment settings with M5's ``profile_defaults`` added, as migration 0080 does."""
    body = deployment(**changes)
    defaults = dict(body.get("profile_defaults") or {})
    for key, value in M5_PROFILE_DEFAULTS.items():
        defaults.setdefault(key, copy.deepcopy(value))
    body["profile_defaults"] = defaults
    return body

import json

import httpx
import jsonschema
import pytest

from tests.fakes.engine import (
    FakeEngine,
    UnconfiguredEngineCall,
    UndeclaredEngineCall,
    recorded_schema,
    with_test_thing,
)

OK = {"status": "ok", "checks": {"database": "ok"}}


def get(fake: FakeEngine, path: str) -> httpx.Response:
    with httpx.Client(transport=fake.transport) as client:
        return client.get(f"http://engine.test{path}")


def test_undeclared_call_raises(fake_engine: FakeEngine) -> None:
    with pytest.raises(UndeclaredEngineCall):
        get(fake_engine, "/definitely/not/a/route")


def test_undeclared_method_raises(fake_engine: FakeEngine) -> None:
    with (
        httpx.Client(transport=fake_engine.transport) as client,
        pytest.raises(UndeclaredEngineCall),
    ):
        client.delete("http://engine.test/health")


def test_declared_but_unconfigured_raises(fake_engine: FakeEngine) -> None:
    with pytest.raises(UnconfiguredEngineCall):
        get(fake_engine, "/health")


def test_registering_an_undeclared_operation_raises(fake_engine: FakeEngine) -> None:
    with pytest.raises(UndeclaredEngineCall):
        fake_engine.respond("GET", "/nope", 200, json={})


def test_canned_response_violating_schema_is_rejected(fake_engine: FakeEngine) -> None:
    with pytest.raises(jsonschema.ValidationError):
        fake_engine.respond("GET", "/health", 200, json={"status": "fine", "checks": {}})
    with pytest.raises(jsonschema.ValidationError):
        fake_engine.respond("GET", "/health", 200, json={"status": "ok"})


def test_undeclared_status_is_rejected(fake_engine: FakeEngine) -> None:
    with pytest.raises(ValueError, match="declares no 418"):
        fake_engine.respond("GET", "/health", 418, json=OK)


def test_valid_response_is_served_and_recorded(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/health", 200, json=OK, headers={"X-Thing": "1"})
    response = get(fake_engine, "/health?verbose=1")
    assert response.status_code == 200
    assert response.json() == OK
    assert response.headers["x-thing"] == "1"
    assert [r.url.path for r in fake_engine.requests] == ["/health"]
    fake_engine.reset()
    assert fake_engine.requests == []


def test_path_templates_match(fake_engine: FakeEngine) -> None:
    story = "01J9Z8Q4N5V6W7X8Y9Z0A1B2C3"
    envelope = {"error": {"code": "not_found", "message": "no", "details": {}}}
    fake_engine.respond("GET", "/stories/{story_id}", 404, json=envelope)
    assert get(fake_engine, f"/stories/{story}").status_code == 404
    with pytest.raises(UndeclaredEngineCall):
        get(fake_engine, f"/stories/{story}/nope/deeper/still")


def test_nxx_and_error_envelope_validation() -> None:
    fake = FakeEngine(with_test_thing(recorded_schema()))
    fake.respond(
        "GET", "/_test/thing", 404, json={"error": {"code": "x", "message": "y", "details": {}}}
    )
    with pytest.raises(jsonschema.ValidationError):
        fake.respond("GET", "/_test/thing", 404, json={"detail": "Not Found"})


def test_serve_routes_outside_the_schema(fake_engine: FakeEngine) -> None:
    fake_engine.serve("GET", "/openapi.json", 200, json={"anything": True})
    assert get(fake_engine, "/openapi.json").json() == {"anything": True}


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/health"), ("get", "/health"), ("GET", "/stories/01J9Z8")]
)
def test_serve_refuses_declared_routes(fake_engine: FakeEngine, method: str, path: str) -> None:
    # A declared route is answered through respond(), which validates the body (#109).
    with pytest.raises(ValueError, match="use respond"):
        fake_engine.serve(method, path, 200, json={"bogus": 1})
    with pytest.raises(UnconfiguredEngineCall):
        get(fake_engine, path)


def test_respond_requires_the_declared_body(fake_engine: FakeEngine) -> None:
    with pytest.raises(ValueError, match="declares a JSON body"):
        fake_engine.respond("GET", "/health", 200)
    with pytest.raises(ValueError, match="declares a JSON body"):
        fake_engine.respond("GET", "/health", 200, json=None)
    with pytest.raises(UnconfiguredEngineCall):
        get(fake_engine, "/health")


def test_fail_raises_transport_error(fake_engine: FakeEngine) -> None:
    fake_engine.fail("GET", "/health", httpx.ConnectError("refused"))
    with pytest.raises(httpx.ConnectError):
        get(fake_engine, "/health")


# #186: each M5 fixture and the answer it is (method, route template, status). The
# ``*_bad_item.json`` files are not schema-valid on purpose (the leniency tests read them).
M5_FIXTURES = {
    "story": ("GET", "/stories/{story_id}", 200),
    "beliefs_page1": ("GET", "/stories/{story_id}/inspect/beliefs", 200),
    "beliefs_empty": ("GET", "/stories/{story_id}/inspect/beliefs", 200),
    "divergences": ("GET", "/stories/{story_id}/inspect/divergences", 200),
    "exposures": ("GET", "/stories/{story_id}/inspect/exposures", 200),
    "belief_written": ("POST", "/admin/stories/{story_id}/beliefs", 201),
    "participation_set": (
        "PATCH",
        "/stories/{story_id}/characters/{character_id}/participation",
        200,
    ),
    "character_weighted": ("POST", "/stories/{story_id}/characters/{character_id}/weight", 200),
    "entity_modeled": ("POST", "/stories/{story_id}/entities/{entity_id}/model", 201),
}
ID_KEYS = frozenset({"id", "character", "witness", "speaker"})


def _id_strings(value: object, key: str | None = None) -> list[str]:
    """Every string in ``value`` under a key that names an id (``id``, ``*_id``,
    ``character``, ``witness``, ``speaker``, each of ``entities``)."""
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _id_strings(v, k)]
    if isinstance(value, list):
        inner = "id" if key == "entities" else None
        return [s for v in value for s in _id_strings(v, inner)]
    named = key is not None and (key in ID_KEYS or key.endswith("_id"))
    return [value] if named and isinstance(value, str) else []


@pytest.mark.parametrize("name", sorted(M5_FIXTURES))
def test_m5_fixtures_validate(fake_engine: FakeEngine, name: str) -> None:
    from console.ids import is_ulid
    from tests.fixtures import ENGINE

    method, route, status = M5_FIXTURES[name]
    body = json.loads((ENGINE / "m5" / f"{name}.json").read_text(encoding="utf-8"))
    fake_engine.respond(method, route, status, json=body)  # validated against the schema
    ids = _id_strings(body)
    assert ids
    assert [i for i in ids if not is_ulid(i)] == []


def test_every_m5_fixture_is_in_the_table() -> None:
    """**Complement:** a file under ``m5/`` (but a ``*_bad_item.json``) missing from the table
    fails."""
    from tests.fixtures import ENGINE

    files = {p.stem for p in (ENGINE / "m5").glob("*.json") if not p.stem.endswith("_bad_item")}
    assert files == set(M5_FIXTURES)


# #232 (D16): each M6 fixture and the answer it is (method, route template, status). The
# ``*_bad_item.json`` files are not schema-valid on purpose (the leniency tests read them).
M6_FIXTURES = {
    "story": ("GET", "/stories/{story_id}", 200),
    "states": ("GET", "/stories/{story_id}/states", 200),
    "states_alice": ("GET", "/stories/{story_id}/states", 200),
    "state_view": ("GET", "/stories/{story_id}/states/{name}", 200),
    "states_empty": ("GET", "/stories/{story_id}/states", 200),  # #242
    "state_view_trust": ("GET", "/stories/{story_id}/states/{name}", 200),  # #242
    "state_written": ("PUT", "/stories/{story_id}/states/{name}", 200),
    "state_written_trust": ("PUT", "/stories/{story_id}/states/{name}", 200),  # #251
    "state_declared": ("POST", "/stories/{story_id}/states", 201),
    "state_definition_written": ("PATCH", "/stories/{story_id}/states/{name}/definition", 200),
    "state_proposals": ("GET", "/stories/{story_id}/state-proposals", 200),
    "state_proposals_mixed": ("GET", "/stories/{story_id}/state-proposals", 200),  # #266
    "analysis_queued": ("POST", "/stories/{story_id}/state-proposals/analyze", 202),
    "proposal_approved": (
        "POST",
        "/stories/{story_id}/state-proposals/{proposal_id}/approve",
        201,
    ),
    "proposal_dismissed": (
        "POST",
        "/stories/{story_id}/state-proposals/{proposal_id}/dismiss",
        200,
    ),
    "clock": ("GET", "/stories/{story_id}/clock", 200),
    "clock_unanchored": ("GET", "/stories/{story_id}/clock", 200),
    "clock_external": ("GET", "/stories/{story_id}/clock", 200),  # #243
    "clock_custom_calendar": ("GET", "/stories/{story_id}/clock", 200),  # #243
    "clock_settings_written": ("PATCH", "/stories/{story_id}/clock", 200),
    "clock_advanced": ("POST", "/stories/{story_id}/clock/advance", 200),
    "clock_advanced_to_time": ("POST", "/stories/{story_id}/clock/advance", 200),  # #252
    "scheduled_events": ("GET", "/stories/{story_id}/scheduled-events", 200),
    "scheduled_events_alice": ("GET", "/stories/{story_id}/scheduled-events", 200),
    "scheduled_events_statuses": ("GET", "/stories/{story_id}/scheduled-events", 200),  # #244
    "scheduled_event_written": ("POST", "/stories/{story_id}/scheduled-events", 201),
    "scheduled_event_created_at_time": (
        "POST",
        "/stories/{story_id}/scheduled-events",
        201,
    ),  # #253
    "scheduled_event_rescheduled": (
        "PATCH",
        "/stories/{story_id}/scheduled-events/{event_id}",
        200,
    ),  # #262
    "scheduled_event_cancelled": (
        "DELETE",
        "/stories/{story_id}/scheduled-events/{event_id}",
        200,
    ),  # #262
    "providers": ("GET", "/admin/external-providers", 200),
    "provider": ("GET", "/admin/external-providers/{name}", 200),
    "provider_mutation": ("POST", "/admin/external-providers", 201),
    "bindings": ("GET", "/stories/{story_id}/providers", 200),
    "bindings_orphan": ("GET", "/stories/{story_id}/providers", 200),  # #247
    "binding_written": ("PUT", "/stories/{story_id}/providers/{name}/binding", 200),
    "external_facts": ("GET", "/stories/{story_id}/inspect/external-facts", 200),
    "external_facts_page2": ("GET", "/stories/{story_id}/inspect/external-facts", 200),  # #248
    "external_facts_empty": ("GET", "/stories/{story_id}/inspect/external-facts", 200),
    "reconciliation": ("GET", "/stories/{story_id}/reconciliation", 200),
    "reconciliation_actions": ("GET", "/stories/{story_id}/reconciliation", 200),  # #249
    "reconciliation_empty": ("GET", "/stories/{story_id}/reconciliation", 200),  # #249
    "settled_now": ("POST", "/stories/{story_id}/reconciliation/{outbox_id}/settle-now", 200),
    "settled_now_queued": (
        "POST",
        "/stories/{story_id}/reconciliation/{outbox_id}/settle-now",
        200,
    ),
    "compensated": ("POST", "/stories/{story_id}/reconciliation/{outbox_id}/compensate", 201),
    "corrected": ("POST", "/stories/{story_id}/correct", 200),
    "respond_world": ("POST", "/stories/{story_id}/respond", 200),  # #245
    "memories_page1": ("GET", "/admin/stories/{story_id}/memories", 200),
    "memories_empty": ("GET", "/admin/stories/{story_id}/memories", 200),
    "fact": ("GET", "/admin/stories/{story_id}/facts/{fact_id}", 200),
    "operation_staged": ("GET", "/operations/{operation_id}", 200),  # #250
    "operation_staged_incomplete": ("GET", "/operations/{operation_id}", 200),  # #250
    "history_world": ("GET", "/stories/{story_id}/history", 200),  # #254
    "history_world_alice": ("GET", "/stories/{story_id}/history", 200),  # #254
    "history_notices": ("GET", "/stories/{story_id}/history", 200),  # #185
    "history_correct_turn": ("GET", "/stories/{story_id}/history", 200),  # #263
    "history_trail_page1": ("GET", "/stories/{story_id}/history", 200),  # #255
    "history_trail_page2": ("GET", "/stories/{story_id}/history", 200),  # #255
}
M6_ID_KEYS = frozenset({"id", "compensates", "compensated_by"})
# An external clock's ``provider_id`` is a provider's name, a slug (D16), not an Engine id (#243).
M6_SKIPPED = frozenset({"external_ids", "manifest", "request_id", "provider_id"})
# The answers that carry no Engine id at all (the clock, the provider registry, bindings).
M6_ID_LESS = frozenset(
    {
        "clock",
        "clock_unanchored",
        "clock_external",
        "clock_custom_calendar",
        "clock_settings_written",
        "providers",
        "provider",
        "provider_mutation",
        "bindings",
        "bindings_orphan",
        "binding_written",
        "states_alice",
        "states_empty",
        "state_view",
        "state_definition_written",
        "memories_empty",
        "external_facts_empty",  # #248
        "reconciliation_empty",  # #249
    }
)


def _m6_id_strings(value: object, key: str | None = None) -> list[str]:
    """Every string in ``value`` under a key that names an id: ``id``, ``*_id``, ``*_ids`` (each
    item), ``compensates`` and ``compensated_by``, and the keys of a ``values`` map (a
    per-character State's cast ids). ``external_ids`` (provider-side values such as
    ``account_id``), manifest contents and a model call's ``request_id`` (the model
    provider's) are skipped.

    Besides D16's prefixes, State definitions and State ids use ``01JM6SD``, and outcomes
    ``01JM60C`` (D16's ``01JM6OC`` holds a letter O, outside the Crockford alphabet a ULID
    uses, so a digit zero stands in)."""
    if key in M6_SKIPPED:
        return []
    if isinstance(value, dict):
        keyed = list(value) if key == "values" else []  # a per-character State's cast ids
        return keyed + [s for k, v in value.items() for s in _m6_id_strings(v, k)]
    if isinstance(value, list):
        inner = "id" if key is not None and key.endswith("_ids") else None
        return [s for v in value for s in _m6_id_strings(v, inner)]
    named = key is not None and (key in M6_ID_KEYS or key.endswith("_id"))
    return [value] if named and isinstance(value, str) else []


@pytest.mark.parametrize("name", sorted(M6_FIXTURES))
def test_m6_fixtures_validate(fake_engine: FakeEngine, name: str) -> None:
    from console.ids import is_ulid
    from tests.fixtures import ENGINE

    method, route, status = M6_FIXTURES[name]
    body = json.loads((ENGINE / "m6" / f"{name}.json").read_text(encoding="utf-8"))
    fake_engine.respond(method, route, status, json=body)  # validated against the schema
    ids = _m6_id_strings(body)
    assert bool(ids) is (name not in M6_ID_LESS)
    assert [i for i in ids if not is_ulid(i)] == []


def test_every_m6_fixture_is_in_the_table() -> None:
    """**Complement:** a file under ``m6/`` (but a ``*_bad_item.json``) missing from the table
    fails."""
    from tests.fixtures import ENGINE

    files = {p.stem for p in (ENGINE / "m6").glob("*.json") if not p.stem.endswith("_bad_item")}
    assert files == set(M6_FIXTURES)


def test_the_m6_id_rule_skips_provider_side_values() -> None:
    """The id rule reads ``*_ids`` items and the two outbox links, and skips ``external_ids``
    and manifests (their ``account_id`` is the provider's, not an Engine id)."""
    body = {
        "source_memory_ids": ["a"],
        "compensates": "b",
        "external_ids": {"account_id": "acct-1"},
        "manifest": {"values": {"x": {"provider_id": "p"}}},
        "outbox_id": "c",
    }
    assert sorted(_m6_id_strings(body)) == ["a", "b", "c"]


# #292 (E16, E20): each M7 fixture and the answer it is (method, route template, status), the
# page-story fixtures folded in at reconcile included. The ``*_bad_item.json`` files are not
# schema-valid on purpose (the leniency tests read them); ``bundle.epistrel.tgz`` is binary.
M7_REWRITES = "/stories/{story_id}/rewrites"
M7_SESSION = "/stories/{story_id}/rewrites/{session_id}"
M7_FIXTURES = {
    "story": ("GET", "/stories/{story_id}", 200),
    # rewrites
    "rewrite_open": ("POST", M7_REWRITES, 201),
    "rewrite_session": ("GET", M7_SESSION, 200),
    "rewrite_session_lite": ("GET", M7_SESSION, 200),
    "rewrite_session_applied": ("GET", M7_SESSION, 200),
    "rewrite_session_directed": ("GET", M7_SESSION, 200),
    "rewrites": ("GET", M7_REWRITES, 200),
    "rewrite_applied": ("POST", f"{M7_SESSION}/apply", 200),
    "rewrite_cancelled": ("POST", f"{M7_SESSION}/cancel", 200),
    # directives
    "directive_steer": ("POST", "/stories/{story_id}/directives", 200),
    "directive_applied": ("POST", "/stories/{story_id}/directives", 200),
    "directive_routed": ("POST", "/stories/{story_id}/directives", 202),
    "directive_lite": ("POST", "/stories/{story_id}/directives", 200),
    "directed_pending": ("POST", "/stories/{story_id}/regenerate", 202),
    # amendments and merge
    "amendments": ("GET", "/stories/{story_id}/amendments", 200),
    "amendment": ("GET", "/stories/{story_id}/amendments/{amendment_id}", 200),
    "undo_session": ("POST", "/stories/{story_id}/amendments/{amendment_id}/undo", 201),
    "redo_session": ("POST", "/stories/{story_id}/amendments/{amendment_id}/redo", 201),
    "merge_session": ("POST", "/stories/{story_id}/entities/merge", 201),
    # backfill
    "backfill_preview": ("POST", "/stories/{story_id}/backfill", 200),
    "backfill_started": ("POST", "/stories/{story_id}/backfill", 202),
    "backfill_resume_preview": ("POST", "/stories/{story_id}/backfill", 200),
    "backfill_extracting": ("GET", "/stories/{story_id}/backfill", 200),
    "backfill_paused": ("GET", "/stories/{story_id}/backfill", 200),
    "backfill_completed": ("GET", "/stories/{story_id}/backfill", 200),
    "backfill_cancelled": ("DELETE", "/stories/{story_id}/backfill", 200),
    "backfill_conflict_resolved": ("POST", "/stories/{story_id}/backfill/conflicts/{cid}", 200),
    # upgrades
    "upgrade_pinned_impact": ("POST", "/engine-profiles/{profile_id}/upgrade-pinned", 200),
    "upgrade_pinned_applied": ("POST", "/engine-profiles/{profile_id}/upgrade-pinned", 200),
    # certification
    "certification_estimate": ("POST", "/engine-profiles/{profile_id}/certification-runs", 200),
    "certification_queued": ("POST", "/engine-profiles/{profile_id}/certification-runs", 202),
    "story_certification_estimate": ("POST", "/stories/{story_id}/certification-runs", 200),
    "certification_run": ("GET", "/certification-runs/{run_id}", 200),
    "certification_run_running": ("GET", "/certification-runs/{run_id}", 200),
    "certifications": ("GET", "/certifications", 200),
    "certification": ("GET", "/certifications/{certification_id}", 200),
    "certification_imported": ("POST", "/admin/certifications/import", 200),
    "certification_invalidated": (
        "POST",
        "/admin/certifications/{certification_id}/invalidate",
        200,
    ),
    # evals
    "eval_runs": ("GET", "/admin/evals/runs", 200),
    "eval_run": ("GET", "/admin/evals/runs/{run_id}", 200),
    # pruning, export and import
    "prune_impact": ("POST", "/stories/{story_id}/prune", 200),
    "prune_applied": ("POST", "/stories/{story_id}/prune", 200),
    "prune_runs": ("GET", "/stories/{story_id}/prune-runs", 200),
    "export_started": ("POST", "/admin/stories/{story_id}/export", 202),
    "export_job_running": ("GET", "/admin/stories/exports/{job_id}", 200),
    "export_job_ready": ("GET", "/admin/stories/exports/{job_id}", 200),
    "export_job_failed": ("GET", "/admin/stories/exports/{job_id}", 200),
    "export_job_expired": ("GET", "/admin/stories/exports/{job_id}", 200),
    "import_impact": ("POST", "/admin/stories/import", 200),
    "import_started": ("POST", "/admin/stories/import", 202),
    "import_job_running": ("GET", "/admin/stories/imports/{job_id}", 200),
    "import_job_ready": ("GET", "/admin/stories/imports/{job_id}", 200),
    "import_job_refused": ("GET", "/admin/stories/imports/{job_id}", 200),
    # catch-up
    "operations": ("GET", "/stories/{story_id}/operations", 200),
    "outbox": ("GET", "/stories/{story_id}/outbox", 200),
    "listing_resolved": ("POST", "/stories/{story_id}/reconciliation/{listing}/resolve", 200),
    "state_definition": ("GET", "/stories/{story_id}/states/{name}/definition", 200),
    # existing calls
    "presets": ("GET", "/engine-profiles", 200),
    "beliefs_nested": ("GET", "/stories/{story_id}/inspect/beliefs", 200),
    # page-story fixtures (E16, folded into #292)
    "history_as_recorded": ("GET", "/stories/{story_id}/history", 200),  # #304
    "rewrite_two_questions": ("GET", M7_SESSION, 200),  # #298
    "rewrite_line_edit": ("POST", M7_REWRITES, 201),  # #305
    "impact_preview_backfill": ("PUT", "/stories/{story_id}/engine-profile", 200),  # #307
    "assign_backfill_started": ("PUT", "/stories/{story_id}/engine-profile", 202),  # #307
    "profile_edit_backfill_impact": ("PUT", "/engine-profiles/{profile_id}", 200),  # #311
    "profile_edit_backfills_applied": ("PUT", "/engine-profiles/{profile_id}", 200),  # #311
    "reconciliation_m7": ("GET", "/stories/{story_id}/reconciliation", 200),  # #326
}
M7_ID_KEYS = frozenset({"id", "compensates", "compensated_by", "undoes", "redoes", "reverted_by"})
# E16's skips: a question's ``options`` (``o1``…) and the option ids naming them, a floor's
# ``metric_id`` (a metric name), and ``models`` (model names under ``id``).
M7_SKIPPED = frozenset(
    {"options", "recommended_option_id", "answer_option_id", "metric_id", "models"}
)
# The answers that carry no Engine id at all (estimates, an eval report, a State definition).
M7_ID_LESS = frozenset(
    {
        "backfill_preview",
        "certification_estimate",
        "story_certification_estimate",
        "eval_runs",
        "eval_run",
        "state_definition",
    }
)
# An eval ``run_id`` is a report path stem and a metric's ``id`` a metric name, not ULIDs (E16):
# skipped in the eval fixtures only.
M7_EVAL_FIXTURES = frozenset({"eval_runs", "eval_run"})
M7_EVAL_SKIPPED = frozenset({"run_id", "metrics"})


def _m7_id_strings(value: object, key: str | None = None, *, skip: frozenset[str]) -> list[str]:
    """Every string in ``value`` under a key that names an id: ``id``, ``*_id``, ``*_ids``
    (each item), and the outbox and amendment links (:data:`M7_ID_KEYS`); keys in ``skip`` (a
    subtree too) are passed over."""
    if key in skip:
        return []
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _m7_id_strings(v, k, skip=skip)]
    if isinstance(value, list):
        inner = "id" if key is not None and key.endswith("_ids") else None
        return [s for v in value for s in _m7_id_strings(v, inner, skip=skip)]
    named = key is not None and (key in M7_ID_KEYS or key.endswith("_id"))
    return [value] if named and isinstance(value, str) else []


@pytest.mark.parametrize("name", sorted(M7_FIXTURES))
def test_m7_fixtures_validate(fake_engine: FakeEngine, name: str) -> None:
    from console.ids import is_ulid
    from tests.fixtures import ENGINE

    method, route, status = M7_FIXTURES[name]
    body = json.loads((ENGINE / "m7" / f"{name}.json").read_text(encoding="utf-8"))
    fake_engine.respond(method, route, status, json=body)  # validated against the schema
    skip = M7_SKIPPED | (M7_EVAL_SKIPPED if name in M7_EVAL_FIXTURES else frozenset())
    ids = _m7_id_strings(body, skip=skip)
    assert bool(ids) is (name not in M7_ID_LESS)
    assert [i for i in ids if not is_ulid(i)] == []


def test_every_m7_fixture_is_in_the_table() -> None:
    """**Complement:** a file in ``glob("m7/*.json")`` (but a ``*_bad_item.json``) missing from
    the table fails."""
    from tests.fixtures import ENGINE

    files = {p.stem for p in (ENGINE / "m7").glob("*.json") if not p.stem.endswith("_bad_item")}
    assert files == set(M7_FIXTURES)
    assert len(M7_FIXTURES) == 67


def test_the_m7_id_rule_skips_option_and_metric_names() -> None:
    """The id rule reads the amendment links and ``*_ids`` items and skips E16's names: option
    ids, a floor's metric and model names; an eval ``run_id`` only where it is skipped."""
    body = {
        "reverted_by": "a",
        "entity_ids": ["b"],
        "options": [{"id": "o1"}],
        "recommended_option_id": "o1",
        "floors": [{"metric_id": "NFR-QUAL-1.x"}],
        "models": {"generation": {"id": "claude"}},
        "run_id": "full-2026-11-05",
    }
    assert sorted(_m7_id_strings(body, skip=M7_SKIPPED)) == ["a", "b", "full-2026-11-05"]
    assert sorted(_m7_id_strings(body, skip=M7_SKIPPED | M7_EVAL_SKIPPED)) == ["a", "b"]

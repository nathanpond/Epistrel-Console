"""The M7 client operations (#292): history rewrites, directives, amendments and merge, backfill
and profile upgrades, certification and evals, pruning, export and import, story operations,
the outbox and State definitions, against the schema-validating fake Engine; the new parameters
on existing calls; regenerate's directed 202; and the typed refusal details.

Fixtures: ``tests/fixtures/engine/m7/`` (E16: one cast, ``story.json``: the player Mara, and
Alice and Bob, every other file reusing their ids)."""

import copy
import inspect
import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from console.engine.client import (
    BUNDLE_RESPONSE_HEADERS,
    EXPECTED_REVISION_HEADER,
    MEDIA_CONNECT_TIMEOUT_S,
    MEDIA_READ_TIMEOUT_S,
    MUTATION_TIMEOUT_S,
    EngineClient,
    EngineError,
    UploadPart,
    import_record,
    turn_timeout,
)
from console.engine.models import (
    AmendmentDetail,
    AmendmentPage,
    AmendmentSummary,
    BackfillCancelled,
    BackfillConflict,
    BackfillConflictResolved,
    Certification,
    CertificationImported,
    CertificationInvalidated,
    CertificationPage,
    CertificationRun,
    CertificationRunRequested,
    DirectedPending,
    Directive,
    DoneTotal,
    ErasureResult,
    EvalRun,
    EvalRunPage,
    ExportJob,
    ExportStarted,
    FloorResult,
    GapFill,
    GapFillPreview,
    ImpactReport,
    ImportImpact,
    ImportingDetails,
    ImportJob,
    ImportStarted,
    ListingResolved,
    Model,
    OperationSummary,
    Outbox,
    OutboxRow,
    PauseDetails,
    ProfileEdit,
    Pruned,
    PruneRun,
    PruneRuns,
    PruneTarget,
    RegenerateResult,
    RewriteApplied,
    RewriteList,
    RewriteQuestion,
    RewriteSession,
    RewriteSessionWritten,
    RewriteStaleDetails,
    RewriteSummary,
    StateDefinition,
    StoryBackfill,
    StoryCertificationRunRequested,
    StoryOperations,
    StoryProfileWrite,
    rejected_entries,
)
from console.engine.timeouts import PROFILE_EDIT, UPLOAD
from console.engine_access import engine_client_for
from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import ENGINE, engine_fixture
from tests.play.helpers import error_json

URL = "http://engine.test"
SERVICE_KEY = "service-key-for-the-m7-client-test-0000"
PRINCIPAL = "console:test"
STORY = "01JM7ST0000000000000000001"
NEW_STORY = "01JM7ST0000000000000000003"
MARA, ALICE, BOB = (f"01JM7CH{'0' * 17}{n:02d}" for n in (1, 2, 3))
SESSION = "01JM7RW0000000000000000001"
AMENDMENT = "01JM7AM0000000000000000001"
REVERTED = "01JM7AM0000000000000000002"
CONFLICT = "01JM7CF0000000000000000001"
RUN = "01JM7CR0000000000000000001"
CERTIFICATION = "01JM7CT0000000000000000001"
EXPORT_JOB = "01JM7JB0000000000000000006"
IMPORT_JOB = "01JM7JB0000000000000000007"
LISTING = "01JM7RQ0000000000000000001"
FULL = "00000000000000000000PRF002"
EVAL_RUN = "full-4710ae26cb73-2026-11-05"
KEY = "01JM7KY0000000000000000099"

REWRITES = "/stories/{story_id}/rewrites"
SESSION_ROUTE = "/stories/{story_id}/rewrites/{session_id}"
DIRECTIVES = "/stories/{story_id}/directives"
AMENDMENTS = "/stories/{story_id}/amendments"
AMENDMENT_ROUTE = "/stories/{story_id}/amendments/{amendment_id}"
MERGE = "/stories/{story_id}/entities/merge"
BACKFILL = "/stories/{story_id}/backfill"
CONFLICT_ROUTE = "/stories/{story_id}/backfill/conflicts/{cid}"
UPGRADE = "/engine-profiles/{profile_id}/upgrade-pinned"
PROFILE_RUNS = "/engine-profiles/{profile_id}/certification-runs"
STORY_RUNS = "/stories/{story_id}/certification-runs"
RUN_ROUTE = "/certification-runs/{run_id}"
CERTIFICATIONS = "/certifications"
CERTIFICATION_ROUTE = "/certifications/{certification_id}"
CERT_IMPORT = "/admin/certifications/import"
INVALIDATE = "/admin/certifications/{certification_id}/invalidate"
EVAL_RUNS = "/admin/evals/runs"
EVAL_RUN_ROUTE = "/admin/evals/runs/{run_id}"
PRUNE = "/stories/{story_id}/prune"
PRUNE_RUNS = "/stories/{story_id}/prune-runs"
EXPORT = "/admin/stories/{story_id}/export"
EXPORT_STATUS = "/admin/stories/exports/{job_id}"
BUNDLE = "/admin/stories/exports/{job_id}/bundle"
IMPORT = "/admin/stories/import"
IMPORT_STATUS = "/admin/stories/imports/{job_id}"
OPERATIONS_ROUTE = "/stories/{story_id}/operations"
OUTBOX = "/stories/{story_id}/outbox"
RESOLVE = "/stories/{story_id}/reconciliation/{listing}/resolve"
STATE_DEFINITION = "/stories/{story_id}/states/{name}/definition"

REWRITE_BODY = {"instruction": "Bob never forged the ledger; he only copied it"}
ANSWERS = [{"question_id": "01JM7QN0000000000000000001", "option_id": "o1"}]
DIRECTIVE_BODY = {"text": "It starts raining", "kind": "durable"}
SESSION_OPTIONS = {"preview": "preview_first", "resolution_mode": "synchronous"}
MERGE_BODY = {"entity_ids": ["01JM7EN0000000000000000001", "01JM7EN0000000000000000002"]}
RESOLUTION = {"accept_recommendation": True}
RECORD = {
    "schema_version": 1,
    "certification_id": "01JM7CT0000000000000000003",
    "fingerprint": "sha256:" + "a" * 64,
    "seed_count": 2,
    "report_sha256": "0" * 64,
    "run_at": "2026-11-05T12:00:00Z",
}
METADATA = {"owner": "console:nate", "name": "Harbour Ledger (copy)", "ignore_warnings": False}
BUNDLE_BYTES = (ENGINE / "m7" / "bundle.epistrel.tgz").read_bytes()


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def client(fake: FakeEngine) -> EngineClient:
    return EngineClient(URL, SERVICE_KEY, transport=fake.transport, principal=PRINCIPAL)


def query(request: httpx.Request) -> list[tuple[str, str]]:
    return parse_qsl(request.url.query.decode(), keep_blank_values=True)


def body(request: httpx.Request) -> Any:
    return json.loads(request.content) if request.content else None


def read_timeout(request: httpx.Request) -> float:
    timeout: dict[str, float] = request.extensions["timeout"]
    return timeout["read"]


def upload() -> UploadPart:
    """A fresh bundle upload (each call streams its own file)."""
    return UploadPart(
        "harbour.epistrel.tgz", "application/gzip", len(BUNDLE_BYTES), io.BytesIO(BUNDLE_BYTES)
    )


UPLOAD_ARG = object()  # an ``Op`` argument replaced by a fresh :func:`upload` per call


@dataclass(frozen=True)
class Op:
    """One M7 operation: its client method and how a test calls it, the route it answers on,
    the fixture and status served, and the model it parses."""

    name: str
    method: str
    route: str
    fixture: str
    status: int
    model: type[Model]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    path: str = ""  # the request path the call sends


def _key(**kwargs: Any) -> dict[str, Any]:
    return {"idempotency_key": KEY, **kwargs}


S = f"/stories/{STORY}"
R = f"{S}/rewrites/{SESSION}"
A = f"{S}/amendments/{AMENDMENT}"
OPS = [
    # history rewrites
    Op("open_rewrite", "POST", REWRITES, "rewrite_open", 201, RewriteSessionWritten,
       (STORY, REWRITE_BODY), _key(), f"{S}/rewrites"),
    Op("list_rewrites", "GET", REWRITES, "rewrites", 200, RewriteList, (STORY,),
       path=f"{S}/rewrites"),
    Op("get_rewrite", "GET", SESSION_ROUTE, "rewrite_session", 200, RewriteSession,
       (STORY, SESSION), path=R),
    Op("answer_rewrite", "POST", f"{SESSION_ROUTE}/answers", "rewrite_open", 200,
       RewriteSessionWritten, (STORY, SESSION, ANSWERS), _key(), f"{R}/answers"),
    Op("apply_rewrite", "POST", f"{SESSION_ROUTE}/apply", "rewrite_applied", 200, RewriteApplied,
       (STORY, SESSION), _key(base_revision=20), f"{R}/apply"),
    Op("cancel_rewrite", "POST", f"{SESSION_ROUTE}/cancel", "rewrite_cancelled", 200,
       RewriteSessionWritten, (STORY, SESSION), _key(), f"{R}/cancel"),
    # directives
    Op("apply_directive", "POST", DIRECTIVES, "directive_applied", 200, Directive,
       (STORY, DIRECTIVE_BODY), _key(expected_revision=None), f"{S}/directives"),
    # amendments and merge
    Op("list_amendments", "GET", AMENDMENTS, "amendments", 200, AmendmentPage, (STORY,),
       path=f"{S}/amendments"),
    Op("get_amendment", "GET", AMENDMENT_ROUTE, "amendment", 200, AmendmentDetail,
       (STORY, AMENDMENT), path=A),
    Op("undo_amendment", "POST", f"{AMENDMENT_ROUTE}/undo", "undo_session", 201,
       RewriteSessionWritten, (STORY, AMENDMENT, SESSION_OPTIONS), _key(), f"{A}/undo"),
    Op("redo_amendment", "POST", f"{AMENDMENT_ROUTE}/redo", "redo_session", 201,
       RewriteSessionWritten, (STORY, AMENDMENT, SESSION_OPTIONS), _key(), f"{A}/redo"),
    Op("merge_entities", "POST", MERGE, "merge_session", 201, RewriteSessionWritten,
       (STORY, MERGE_BODY), _key(), f"{S}/entities/merge"),
    # backfill and profile upgrades
    Op("start_backfill", "POST", BACKFILL, "backfill_preview", 200, GapFill, (STORY,),
       _key(ignore_warnings=False), f"{S}/backfill"),
    Op("get_backfill", "GET", BACKFILL, "backfill_paused", 200, StoryBackfill, (STORY,),
       path=f"{S}/backfill"),
    Op("cancel_backfill", "DELETE", BACKFILL, "backfill_cancelled", 200, BackfillCancelled,
       (STORY,), _key(), f"{S}/backfill"),
    Op("resolve_backfill_conflict", "POST", CONFLICT_ROUTE, "backfill_conflict_resolved", 200,
       BackfillConflictResolved, (STORY, CONFLICT, RESOLUTION), _key(),
       f"{S}/backfill/conflicts/{CONFLICT}"),
    Op("upgrade_pinned_stories", "POST", UPGRADE, "upgrade_pinned_impact", 200, ProfileEdit,
       (FULL,), _key(ignore_warnings=False), f"/engine-profiles/{FULL}/upgrade-pinned"),
    # certification and evals
    Op("request_certification_run_profile", "POST", PROFILE_RUNS, "certification_estimate",
       200, CertificationRunRequested, (FULL,), _key(confirm=False),
       f"/engine-profiles/{FULL}/certification-runs"),
    Op("request_certification_run_story", "POST", STORY_RUNS, "story_certification_estimate",
       200, StoryCertificationRunRequested, (STORY,), _key(confirm=False),
       f"{S}/certification-runs"),
    Op("get_certification_run", "GET", RUN_ROUTE, "certification_run", 200, CertificationRun,
       (RUN,), path=f"/certification-runs/{RUN}"),
    Op("list_certifications", "GET", CERTIFICATIONS, "certifications", 200, CertificationPage,
       path=CERTIFICATIONS),
    Op("get_certification", "GET", CERTIFICATION_ROUTE, "certification", 200, Certification,
       (CERTIFICATION,), path=f"/certifications/{CERTIFICATION}"),
    Op("import_certification", "POST", CERT_IMPORT, "certification_imported", 200,
       CertificationImported, (RECORD,), _key(), CERT_IMPORT),
    Op("invalidate_certification", "POST", INVALIDATE, "certification_invalidated", 200,
       CertificationInvalidated, (CERTIFICATION,),
       _key(reason="manual", note="The judge model changed"),
       f"/admin/certifications/{CERTIFICATION}/invalidate"),
    Op("list_eval_runs", "GET", EVAL_RUNS, "eval_runs", 200, EvalRunPage, path=EVAL_RUNS),
    Op("get_eval_run", "GET", EVAL_RUN_ROUTE, "eval_run", 200, EvalRun, (EVAL_RUN,),
       path=f"{EVAL_RUNS}/{EVAL_RUN}"),
    # pruning, export and import
    Op("prune_story", "POST", PRUNE, "prune_impact", 200, Pruned, (STORY,),
       _key(ignore_warnings=False, expected_revision=None), f"{S}/prune"),
    Op("list_prune_runs", "GET", PRUNE_RUNS, "prune_runs", 200, PruneRuns, (STORY,),
       path=f"{S}/prune-runs"),
    Op("export_story", "POST", EXPORT, "export_started", 202, ExportStarted, (STORY,), _key(),
       f"/admin{S}/export"),
    Op("export_status", "GET", EXPORT_STATUS, "export_job_ready", 200, ExportJob,
       (EXPORT_JOB,), path=f"/admin/stories/exports/{EXPORT_JOB}"),
    Op("import_story", "POST", IMPORT, "import_impact", 200, ImportImpact,
       (METADATA, UPLOAD_ARG), _key(), IMPORT),
    Op("import_status", "GET", IMPORT_STATUS, "import_job_running", 200, ImportJob,
       (IMPORT_JOB,), path=f"/admin/stories/imports/{IMPORT_JOB}"),
    # story operations, outbox and definitions
    Op("list_operations", "GET", OPERATIONS_ROUTE, "operations", 200, StoryOperations,
       (STORY,), path=f"{S}/operations"),
    Op("list_outbox", "GET", OUTBOX, "outbox", 200, Outbox, (STORY,), path=f"{S}/outbox"),
    Op("resolve_listing", "POST", RESOLVE, "listing_resolved", 200, ListingResolved,
       (STORY, LISTING), _key(resolution="applied_elsewhere", expected_revision=None),
       f"{S}/reconciliation/{LISTING}/resolve"),
    Op("get_state_definition", "GET", STATE_DEFINITION, "state_definition", 200,
       StateDefinition, (STORY, "gold"), path=f"{S}/states/gold/definition"),
]  # fmt: skip
BY_NAME = {op.name: op for op in OPS}
# E2's 13 always-omniscient methods, plus the State definition read.
OMNISCIENT = (
    "open_rewrite",
    "list_rewrites",
    "get_rewrite",
    "answer_rewrite",
    "apply_rewrite",
    "cancel_rewrite",
    "list_amendments",
    "get_amendment",
    "undo_amendment",
    "redo_amendment",
    "merge_entities",
    "apply_directive",
    "list_outbox",
    "get_state_definition",
)
NO_EXPECTED_REVISION = (
    "open_rewrite",
    "answer_rewrite",
    "apply_rewrite",
    "cancel_rewrite",
    "undo_amendment",
    "redo_amendment",
    "merge_entities",
    "start_backfill",
    "cancel_backfill",
    "resolve_backfill_conflict",
    "export_story",
    "import_story",
    "request_certification_run_profile",
    "request_certification_run_story",
)
TIMELINE_WRITES = ("apply_directive", "prune_story", "resolve_listing")
TURN_TIMEOUT_WRITES = frozenset(
    {
        "open_rewrite",
        "answer_rewrite",
        "apply_rewrite",
        "undo_amendment",
        "redo_amendment",
        "merge_entities",
        "apply_directive",
        "resolve_listing",
    }
)


def serve(fake: FakeEngine, op: Op, fixture: str | None = None, status: int | None = None) -> None:
    fake.respond(op.method, op.route, status or op.status, json=m7(fixture or op.fixture))


def call(engine: EngineClient, op: Op, **overrides: Any) -> Any:
    method: Callable[..., Any] = getattr(engine, op.name)
    args = tuple(upload() if a is UPLOAD_ARG else a for a in op.args)
    return method(*args, **{**op.kwargs, **overrides})


def sent(fake: FakeEngine, op: Op, **overrides: Any) -> httpx.Request:
    """The one request ``op`` sends (served its fixture)."""
    serve(fake, op)
    call(client(fake), op, **overrides)
    [request] = fake.requests
    fake.reset()
    return request


# --- every operation round-trips (E16) --------------------------------------------------------


def test_thirty_seven_operations_are_tabled() -> None:
    """The 36 JSON operations are tabled here; the 37th, the bundle stream, has its own tests."""
    assert len(OPS) == 36
    assert len(BY_NAME) == 36
    assert len(OMNISCIENT) == 14
    assert hasattr(EngineClient, "open_export_bundle")


@pytest.mark.parametrize("op", OPS, ids=lambda op: op.name)
def test_each_operation_round_trips(fake_engine: FakeEngine, op: Op) -> None:
    serve(fake_engine, op)
    answer = call(client(fake_engine), op)
    assert isinstance(answer, op.model)
    assert answer == op.model.from_json(m7(op.fixture))
    [request] = fake_engine.requests
    assert request.method == op.method
    assert request.url.path == op.path
    if op.method != "GET":
        assert request.headers["Idempotency-Key"] == KEY


def test_path_segments_are_encoded(fake_engine: FakeEngine) -> None:
    """Every id goes into its path ``quote(…, safe="")``-encoded."""
    op = BY_NAME["get_rewrite"]
    serve(fake_engine, op)
    client(fake_engine).get_rewrite(STORY, "a/b c")
    [request] = fake_engine.requests
    assert request.url.raw_path.decode().split("?")[0] == f"{S}/rewrites/a%2Fb%20c"


# --- request rules (E2, E19) ---------------------------------------------------------------


@pytest.mark.parametrize("name", OMNISCIENT)
def test_omniscient_routes_always_send_omniscient(fake_engine: FakeEngine, name: str) -> None:
    request = sent(fake_engine, BY_NAME[name])
    pairs = query(request)
    assert ("viewpoint", "omniscient") in pairs
    assert [k for k, _ in pairs].count("viewpoint") == 1
    assert "actor_character_id" not in dict(pairs)


@pytest.mark.parametrize("name", OMNISCIENT)
def test_omniscient_methods_take_no_viewpoint(name: str) -> None:
    """**Complement:** none of the 14 has a ``viewpoint`` parameter; ``get_history`` keeps its
    own (pinned by the next test only)."""
    assert "viewpoint" not in inspect.signature(getattr(EngineClient, name)).parameters
    assert "viewpoint" in inspect.signature(EngineClient.get_history).parameters


def test_as_recorded_history_forces_omniscient(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories/{story_id}/history", 200, json=m7("history_as_recorded"))
    engine = client(fake_engine)
    alice = f"character:{ALICE}"
    engine.get_history(
        STORY,
        viewpoint=alice,
        view="as_recorded",
        actor_character_id=ALICE,
        player_view="character",
    )
    engine.get_history(STORY, viewpoint=alice, view="corrected")
    engine.get_history(STORY, viewpoint=alice)
    recorded, corrected, bare = (dict(query(r)) for r in fake_engine.requests)
    assert recorded == {"viewpoint": "omniscient", "view": "as_recorded"}
    assert corrected == {"viewpoint": alice, "view": "corrected"}
    assert bare == {"viewpoint": alice}


def test_list_rewrites_repeats_state(fake_engine: FakeEngine) -> None:
    op = BY_NAME["list_rewrites"]
    states = ("awaiting_answers", "awaiting_approval")
    request = sent(fake_engine, op, states=states, cursor="", limit=50)
    assert query(request) == [
        ("viewpoint", "omniscient"),
        ("state", "awaiting_answers"),
        ("state", "awaiting_approval"),
        ("limit", "50"),
    ]
    assert query(sent(fake_engine, op)) == [("viewpoint", "omniscient")]


@pytest.mark.parametrize("name", NO_EXPECTED_REVISION)
def test_session_writes_send_no_expected_revision(fake_engine: FakeEngine, name: str) -> None:
    """E19: the session, backfill, export, import and certification writes send none, and take
    no argument for one."""
    assert EXPECTED_REVISION_HEADER not in sent(fake_engine, BY_NAME[name]).headers
    assert "expected_revision" not in inspect.signature(getattr(EngineClient, name)).parameters


@pytest.mark.parametrize("name", TIMELINE_WRITES)
def test_timeline_writes_send_expected_revision_only_when_given(
    fake_engine: FakeEngine, name: str
) -> None:
    """**Complement:** ``apply_directive``, ``prune_story`` and ``resolve_listing`` send it when
    given and not when ``None``."""
    op = BY_NAME[name]
    assert sent(fake_engine, op, expected_revision=20).headers[EXPECTED_REVISION_HEADER] == "20"
    assert EXPECTED_REVISION_HEADER not in sent(fake_engine, op).headers


def test_write_bodies(fake_engine: FakeEngine) -> None:
    def sent_body(name: str, *, fixture: str | None = None, **overrides: Any) -> Any:
        op = BY_NAME[name]
        serve(fake_engine, op, fixture)
        call(client(fake_engine), op, **overrides)
        found = body(fake_engine.requests[-1])
        fake_engine.reset()
        return found

    assert sent_body("apply_rewrite", base_revision=7) == {"base_revision": 7}
    assert sent_body("apply_rewrite", base_revision=None) == {"base_revision": None}
    assert sent_body("answer_rewrite") == {"answers": ANSWERS}
    assert sent_body("open_rewrite") == REWRITE_BODY
    assert sent_body("prune_story", targets=None, ignore_warnings=False) == {
        "ignore_warnings": False
    }
    assert sent_body("prune_story", targets=["01JM7CN0000000000000000011"]) == {
        "ignore_warnings": False,
        "targets": ["01JM7CN0000000000000000011"],
    }
    assert sent_body("start_backfill", capabilities=["episodic"], ignore_warnings=True) == {
        "ignore_warnings": True,
        "capabilities": ["episodic"],
    }
    assert sent_body("start_backfill") == {"ignore_warnings": False}
    assert sent_body("upgrade_pinned_stories", upgrade_mode="forward_only") == {
        "ignore_warnings": False,
        "upgrade_mode": "forward_only",
    }
    assert sent_body("upgrade_pinned_stories") == {"ignore_warnings": False}
    assert sent_body("invalidate_certification") == {
        "reason": "manual",
        "note": "The judge model changed",
    }
    assert sent_body(
        "request_certification_run_profile", version=4, suites=["quality"], seeds=2, sample=20
    ) == {"confirm": False, "version": 4, "suites": ["quality"], "seeds": 2, "sample": 20}
    assert sent_body("request_certification_run_profile") == {"confirm": False}
    assert sent_body("request_certification_run_story", confirm=True, seeds=3) == {
        "confirm": True,
        "seeds": 3,
    }
    assert (
        "version" not in inspect.signature(EngineClient.request_certification_run_story).parameters
    )
    assert sent_body("resolve_listing") == {"resolution": "applied_elsewhere"}
    assert sent_body("resolve_backfill_conflict") == RESOLUTION
    assert sent_body("import_certification") == RECORD
    assert sent_body("merge_entities") == MERGE_BODY
    assert sent_body("undo_amendment") == SESSION_OPTIONS
    assert sent_body("apply_directive") == DIRECTIVE_BODY


@pytest.mark.parametrize("name", ["cancel_rewrite", "export_story", "cancel_backfill"])
def test_bodyless_writes_send_no_body(fake_engine: FakeEngine, name: str) -> None:
    """**Complement:** these send no body at all."""
    assert sent(fake_engine, BY_NAME[name]).content == b""


def test_list_operations_formats_times(fake_engine: FakeEngine) -> None:
    op = BY_NAME["list_operations"]
    since = datetime(2026, 11, 1, tzinfo=UTC)
    until = datetime(2026, 11, 2, 3, 4, 5, 600000, tzinfo=UTC)
    request = sent(fake_engine, op, since=since, until=until, kind="turn", limit=200)
    assert dict(query(request)) == {
        "kind": "turn",
        "since": "2026-11-01T00:00:00Z",
        "until": "2026-11-02T03:04:05.600000Z",
        "limit": "200",
    }
    assert query(sent(fake_engine, op, label=None)) == []
    assert dict(query(sent(fake_engine, op, label="staged_turn", after_id="")))["label"] == (
        "staged_turn"
    )


def test_list_operations_refuses_a_naive_time(fake_engine: FakeEngine) -> None:
    """**Complement:** a naive datetime raises ``ValueError`` and sends nothing."""
    serve(fake_engine, BY_NAME["list_operations"])
    with pytest.raises(ValueError, match="aware"):
        client(fake_engine).list_operations(STORY, until=datetime(2026, 11, 1))
    assert fake_engine.requests == []


def test_filters_are_sent_only_when_given(fake_engine: FakeEngine) -> None:
    """A filter is left out only when ``None``; an empty cursor is left out."""
    amendments = sent(fake_engine, BY_NAME["list_amendments"], status="reverted", cursor="")
    assert dict(query(amendments)) == {"viewpoint": "omniscient", "status": "reverted"}
    backfill = sent(fake_engine, BY_NAME["get_backfill"], conflicts="all", limit=200)
    assert dict(query(backfill)) == {"conflicts": "all", "limit": "200"}
    assert query(sent(fake_engine, BY_NAME["get_backfill"])) == []
    certifications = sent(
        fake_engine,
        BY_NAME["list_certifications"],
        profile_id=FULL,
        cursor="01JM7CT0000000000000000002",
    )
    assert dict(query(certifications)) == {
        "profile_id": FULL,
        "cursor": "01JM7CT0000000000000000002",
    }
    outbox = sent(fake_engine, BY_NAME["list_outbox"], status="intended", provider="bank")
    assert dict(query(outbox)) == {
        "viewpoint": "omniscient",
        "status": "intended",
        "provider": "bank",
    }
    assert query(sent(fake_engine, BY_NAME["list_eval_runs"], cursor="")) == []


# --- the models: E15 ----------------------------------------------------------------------

type LenientPage = (
    RewriteList
    | AmendmentPage
    | StoryOperations
    | Outbox
    | CertificationPage
    | EvalRunPage
    | PruneRuns
)
LENIENT: list[tuple[str, type[LenientPage], str, type[Model]]] = [
    ("rewrites", RewriteList, "items", RewriteSummary),
    ("amendments", AmendmentPage, "items", AmendmentSummary),
    ("operations", StoryOperations, "operations", OperationSummary),
    ("outbox", Outbox, "rows", OutboxRow),
    ("certifications", CertificationPage, "items", Certification),
    ("eval_runs", EvalRunPage, "items", EvalRun),
    ("prune_runs", PruneRuns, "prune_runs", PruneRun),
]


@pytest.mark.parametrize(("name", "model", "key", "item"), LENIENT)
def test_lenient_pages_keep_a_malformed_entry_raw(
    name: str, model: type[LenientPage], key: str, item: type[Model]
) -> None:
    raw = m7(f"{name}_bad_item")
    page = model.from_json(raw)
    assert page is not None
    kept = getattr(page, key)
    assert len(kept) == 2
    assert all(isinstance(entry, item) for entry in kept)
    assert page.unparsed == {key: raw[key]}
    assert rejected_entries(page.unparsed[key], item.from_json) == [raw[key][1]]


@pytest.mark.parametrize(("name", "model", "key", "item"), LENIENT)
def test_the_good_pages_parse_whole(
    name: str, model: type[LenientPage], key: str, item: type[Model]
) -> None:
    """**Complement:** the good fixtures have an empty ``unparsed``."""
    page = model.from_json(m7(name))
    assert page is not None
    assert page.unparsed == {}
    assert len(getattr(page, key)) == len(m7(name)[key])


def test_a_session_with_a_bad_question_still_parses() -> None:
    raw = m7("rewrite_two_questions")
    raw["questions"][1]["options"] = "not a list"
    session = RewriteSession.from_json(raw)
    assert session is not None
    assert [q.id for q in session.questions] == [raw["questions"][0]["id"]]
    assert session.unparsed == {"questions": raw["questions"]}
    assert rejected_entries(session.unparsed["questions"], RewriteQuestion.from_json) == [
        raw["questions"][1]
    ]


def test_a_backfill_with_a_bad_conflict_still_parses() -> None:
    raw = m7("backfill_paused")
    raw["conflict_list"][0]["statements"] = None
    backfill = StoryBackfill.from_json(raw)
    assert backfill is not None
    assert [c.id for c in backfill.conflict_list] == [raw["conflict_list"][1]["id"]]
    assert rejected_entries(backfill.unparsed["conflict_list"], BackfillConflict.from_json) == [
        raw["conflict_list"][0]
    ]


def test_write_answers_are_strict() -> None:
    applied = m7("rewrite_applied")
    del applied["amendment_id"]
    assert RewriteApplied.from_json(applied) is None
    pruned = m7("prune_applied")
    del pruned["impact"]
    assert Pruned.from_json(pruned) is None
    nested = m7("rewrite_applied")
    nested["session"]["questions"] = [{"id": "01JM7QN0000000000000000009"}]
    assert RewriteApplied.from_json(nested) is None  # a nested session gets no leniency
    written = m7("rewrite_open")
    written["questions"][0]["severity"] = 7
    assert RewriteSessionWritten.from_json(written) is None
    assert RewriteSessionWritten.from_json(m7("rewrite_open")) is not None


ERASURE: dict[str, Any] = {
    "config_revision": 7,
    "dry_run": False,
    "subject": {"kind": "character", "id": BOB},
    "scope": "story",
    "story_id": STORY,
    "records": {"story.characters": 1},
    "prose_segments": 2,
    "media": 0,
    "idempotency_results": 0,
    "regeneration": "redaction",
    "degradation": [],
    "referencing_stories": [],
    "erasure_id": "01JM7ER0000000000000000001",
    "promoted_drafts": [
        {
            "catalog_id": "01JM7CC0000000000000000001",
            "character_id": BOB,
            "companion_ids": [],
            "follow_up": {"kind": "review_draft"},
        }
    ],
}


def _strict(model: type[Model], good: dict[str, Any], bad: dict[str, Any]) -> None:
    """D15 (#342): the good answer parses whole; with one bad entry the answer is ``None``."""
    assert model.from_json(good) is not None
    assert model.from_json(bad) is None


def test_profile_edit_backfills_are_strict() -> None:
    good = m7("profile_edit_backfills_applied")
    assert len(good["backfills"]) == 1
    bad = copy.deepcopy(good)
    bad["backfills"].append({"id": "01JM7BF0000000000000000009", "status": "queued"})  # no job
    _strict(ProfileEdit, good, bad)


def test_story_profile_write_backfill_is_strict() -> None:
    good = m7("assign_backfill_started")
    bad = copy.deepcopy(good)
    bad["backfill"] = {"id": 1}
    _strict(StoryProfileWrite, good, bad)


def test_erasure_promoted_drafts_are_strict() -> None:
    good = copy.deepcopy(ERASURE)
    parsed = ErasureResult.from_json(good)
    assert parsed is not None
    assert [d.character_id for d in parsed.promoted_drafts] == [BOB]
    bad = copy.deepcopy(good)
    bad["promoted_drafts"].append({"catalog_id": "01JM7CC0000000000000000002"})
    _strict(ErasureResult, good, bad)


def test_impact_certifications_lost_are_strict() -> None:
    good = m7("upgrade_pinned_impact")
    assert len(good["impact"]["certifications_lost"]) == 1
    bad = copy.deepcopy(good)
    bad["impact"]["certifications_lost"].append({})
    _strict(ImpactReport, good["impact"], bad["impact"])
    # The write that carries the report fails whole too.
    _strict(ProfileEdit, good, bad)


def test_a_write_answer_that_does_not_parse_is_a_bad_response(fake_engine: FakeEngine) -> None:
    op = BY_NAME["open_rewrite"]
    fake_engine.side_effect(op.method, op.route, lambda r: httpx.Response(201, json={"id": 1}))
    with pytest.raises(EngineError) as caught:
        call(client(fake_engine), op)
    assert caught.value.code == "bad_response"


def test_raw_fields_stay_raw() -> None:
    session = RewriteSession.from_json(m7("rewrite_session"))
    assert session is not None
    assert session.preview is not None
    assert session.preview.changes[0] == m7("rewrite_session")["preview"]["changes"][0]
    threshold = RewriteSession.from_json(
        {**m7("rewrite_session"), "resolution_mode": {"threshold": "high"}}
    )
    assert threshold is not None
    assert threshold.resolution_mode == {"threshold": "high"}
    job = ExportJob.from_json(m7("export_job_ready"))
    assert job is not None
    assert job.manifest == m7("export_job_ready")["manifest"]
    imported = ImportJob.from_json(m7("import_job_ready"))
    assert imported is not None
    assert imported.report == m7("import_job_ready")["report"]
    assert imported.progress == {"done": 8, "total": 8}
    assert ImportJob.from_json({"job_id": IMPORT_JOB, "status": "queued", "story_id": None}) == (
        ImportJob(IMPORT_JOB, "queued", None)
    )


def test_unknown_enums_are_kept() -> None:
    lite = RewriteSession.from_json({**m7("rewrite_session_lite"), "pipeline": "hybrid"})
    assert lite is not None
    assert lite.pipeline == "hybrid"
    job = ImportJob.from_json({**m7("import_job_running"), "status": "later"})
    assert job is not None
    assert job.status == "later"
    raw = m7("rewrite_two_questions")
    raw["questions"][0]["severity"] = "medium"
    session = RewriteSession.from_json(raw)
    assert session is not None
    assert session.questions[0].severity == "medium"
    run = EvalRun.from_json({**m7("eval_run"), "source": "imported"})
    assert run is not None
    assert run.source == "imported"


def test_a_question_with_a_non_string_severity_is_dropped() -> None:
    """**Complement:** ``severity: 7`` drops the question into ``unparsed``."""
    raw = m7("rewrite_two_questions")
    raw["questions"][0]["severity"] = 7
    session = RewriteSession.from_json(raw)
    assert session is not None
    assert [q.id for q in session.questions] == [raw["questions"][1]["id"]]
    assert session.unparsed == {"questions": raw["questions"]}


def test_the_typed_views() -> None:
    """``GapFill.preview``, ``PruneImpact.targets``, ``Certification.floors_view``,
    ``RewriteSessionWritten.session`` and the slot position read their raw members."""
    preview = GapFill.from_json(m7("backfill_preview"))
    assert preview is not None
    assert isinstance(preview.preview, GapFillPreview)
    assert preview.preview.range.to_turn == 3
    resume = GapFill.from_json(m7("backfill_resume_preview"))
    assert resume is not None
    assert resume.preview is None
    assert resume.conflicts_open == 2
    assert resume.conflicts is not None
    pruned = Pruned.from_json(m7("prune_impact"))
    assert pruned is not None
    targets = pruned.impact.targets()
    [tail] = targets["discarded_tails"]
    assert isinstance(tail, PruneTarget)
    assert (tail.from_position.turn, tail.to_position.turn) == (3, 4)
    certification = Certification.from_json(m7("certification"))
    assert certification is not None
    passed, failed, insufficient = certification.floors_view()
    assert all(isinstance(f, FloorResult) for f in (*passed, *failed, *insufficient))
    assert (len(passed), len(failed), len(insufficient)) == (6, 1, 1)
    assert insufficient[0].intervention_type == "repair_rung_1"
    broken = Certification.from_json({**m7("certification"), "floors_failed": [{"metric_id": "x"}]})
    assert broken is not None
    assert broken.floors_view()[1] == []
    written = RewriteSessionWritten.from_json(m7("rewrite_open"))
    assert written is not None
    assert written.session is written
    resolved = ListingResolved.from_json(m7("listing_resolved"))
    assert resolved is not None
    assert resolved.slot_position is not None
    assert resolved.slot_position.slot_k == 1


# --- typed refusal details --------------------------------------------------------------------


def test_rewrite_stale_details_read_the_new_preview(fake_engine: FakeEngine) -> None:
    preview = {**m7("rewrite_session")["preview"], "base_revision": 22}
    question = m7("rewrite_two_questions")["questions"][1]
    details: dict[str, Any] = {
        "base_revision": 22,
        "preview": preview,
        "new_questions": [question, {"id": 3}],
        "carried_answers": [{"question_id": "01JM7QN0000000000000000001", "option_id": "o1"}],
        "reasons": [{"kind": "record_changed", "refs": []}],
        "stale_count": 1,
    }
    op = BY_NAME["apply_rewrite"]
    fake_engine.respond(op.method, op.route, 409, json=error_json("rewrite_stale", **details))
    with pytest.raises(EngineError) as caught:
        call(client(fake_engine), op)
    stale = RewriteStaleDetails.from_details(caught.value.details)
    assert stale is not None
    assert stale.preview is not None
    assert stale.preview.base_revision == 22
    assert [q.id for q in stale.new_questions] == [question["id"]]
    assert stale.reason is None
    assert stale.stale_count == 1
    assert stale.reasons == details["reasons"]
    assert stale.carried_answers == details["carried_answers"]


def test_rewrite_stale_details_are_lenient() -> None:
    """**Complement:** ``reason: base_mismatch`` with no preview gives ``preview is None``, and
    ``details: {}`` gives every member ``None``/empty without raising."""
    mismatch = RewriteStaleDetails.from_details({"reason": "base_mismatch", "preview": "x"})
    assert mismatch is not None
    assert (mismatch.reason, mismatch.preview) == ("base_mismatch", None)
    empty = RewriteStaleDetails.from_details({})
    assert empty == RewriteStaleDetails(None, None, None, [], None, [], None)
    odd = RewriteStaleDetails.from_details({"reasons": "x", "stale_count": True})
    assert odd is not None
    assert (odd.reasons, odd.stale_count) == ([], None)
    assert RewriteStaleDetails.from_details(None) is None


def test_importing_and_pause_details() -> None:
    importing = error_json(
        "story_importing",
        story_id=NEW_STORY,
        job_id=IMPORT_JOB,
        stage="guardrails",
        progress={"done": 3, "total": 8},
    )
    found = ImportingDetails.from_details(importing["error"]["details"])
    assert found == ImportingDetails(NEW_STORY, IMPORT_JOB, "guardrails", DoneTotal(3, 8))
    half = ImportingDetails.from_details({"progress": {"done": 3, "total": "8"}})
    assert half == ImportingDetails(None, None, None, None)
    paused = error_json(
        "story_paused",
        story_id=STORY,
        job_id="01JM7JB0000000000000000001",
        cause="backfill_conflicts",
        progress={"phase": "reconciling", "chunks_done": 3, "chunks_total": 3},
        conflicts_open=2,
        backfill_id="01JM7BF0000000000000000001",
    )
    pause = PauseDetails.from_details(paused["error"]["details"])
    assert pause is not None
    assert (pause.cause, pause.conflicts_open) == ("backfill_conflicts", 2)
    assert pause.backfill_id == "01JM7BF0000000000000000001"
    assert pause.progress == {"phase": "reconciling", "chunks_done": 3, "chunks_total": 3}


def test_a_rating_check_pause_reads_unchanged() -> None:
    """**Complement:** a rating-check pause's ``{done, total}`` reads as ``progress``
    unchanged."""
    details = {"job_id": "01JM7JB0000000000000000009", "cause": "rating_check"}
    pause = PauseDetails.from_details({**details, "progress": {"done": 1, "total": 4}})
    assert pause == PauseDetails(
        "rating_check", "01JM7JB0000000000000000009", {"done": 1, "total": 4}, None, None
    )
    assert PauseDetails.from_details([]) is None
    assert ImportingDetails.from_details("x") is None


# --- streaming and multipart --------------------------------------------------------------


def _bundle(request: httpx.Request) -> httpx.Response:
    headers = {
        "Content-Type": "application/gzip",
        "Content-Length": str(len(BUNDLE_BYTES)),
        "Content-Disposition": 'attachment; filename="harbour.epistrel.tgz"',
        "ETag": '"not-kept"',
    }
    return httpx.Response(200, content=BUNDLE_BYTES, headers=headers)


def test_open_export_bundle_streams(fake_engine: FakeEngine) -> None:
    fake_engine.side_effect("GET", BUNDLE, _bundle)
    with client(fake_engine).open_export_bundle(EXPORT_JOB) as streamed:
        assert streamed.status == 200
        assert b"".join(streamed.iter_bytes(7)) == BUNDLE_BYTES
        assert dict(streamed.headers) == {
            "Content-Type": "application/gzip",
            "Content-Length": str(len(BUNDLE_BYTES)),
            "Content-Disposition": 'attachment; filename="harbour.epistrel.tgz"',
        }
    assert BUNDLE_RESPONSE_HEADERS == ("Content-Type", "Content-Length", "Content-Disposition")
    [request] = fake_engine.requests
    assert request.url.path == f"/admin/stories/exports/{EXPORT_JOB}/bundle"
    assert request.headers["Accept"] == "*/*"
    assert request.headers["Authorization"] == f"Bearer {SERVICE_KEY}"
    assert "range" not in request.headers
    timeout = request.extensions["timeout"]
    assert (timeout["connect"], timeout["read"]) == (MEDIA_CONNECT_TIMEOUT_S, MEDIA_READ_TIMEOUT_S)


@pytest.mark.django_db
def test_an_expired_bundle_reads_its_error_and_is_never_recorded(
    fake_engine: FakeEngine, operator: Any
) -> None:
    """**Complement:** a 410 ``export_expired`` comes back as ``StreamedContent.error()``, and no
    operation-log row is written. The recorded schema declares neither the 409 nor the 410
    (an Engine schema gap), so the fake answers it undeclared."""
    fake_engine.respond_undeclared("GET", BUNDLE, 410, json=error_json("export_expired"))
    with override_settings(ENGINE_URL=URL, ENGINE_SERVICE_KEY=SERVICE_KEY):
        engine = engine_client_for(_operator_request(operator))
        engine._transport = fake_engine.transport
        with engine.open_export_bundle(EXPORT_JOB) as streamed:
            assert streamed.status == 410
            assert streamed.error().code == "export_expired"
    assert EngineOperation.objects.count() == 0


def test_import_story_is_multipart(fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", IMPORT, 200, json=m7("import_impact"))
    engine = client(fake_engine)
    held = engine.import_story(METADATA, upload(), idempotency_key=KEY)
    assert isinstance(held, ImportImpact)
    assert held.applied is False
    fake_engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    started = engine.import_story(METADATA, upload(), idempotency_key=KEY)
    assert isinstance(started, ImportStarted)
    assert started.story_id == NEW_STORY
    request = fake_engine.requests[-1]
    content_type = request.headers["Content-Type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=", 1)[1].encode()
    parts = [p for p in request.content.split(b"--" + boundary) if p.strip(b"-\r\n")]
    names = [p.split(b'name="', 1)[1].split(b'"', 1)[0] for p in parts]
    assert names == [b"metadata", b"file"]
    metadata = parts[0].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")
    assert json.loads(metadata) == METADATA
    assert BUNDLE_BYTES in parts[1]
    assert read_timeout(request) == UPLOAD


def test_import_record_keeps_no_bytes() -> None:
    """**Complement:** the #44 row's stored body holds the file's name, size and type and no
    bytes."""
    part = upload()
    assert import_record({"metadata": METADATA, "file": part}) == {
        "metadata": METADATA,
        "file": {
            "name": "harbour.epistrel.tgz",
            "size": len(BUNDLE_BYTES),
            "content_type": "application/gzip",
        },
    }
    assert import_record({}) == {"metadata": None, "file": None}


# --- timeouts and the operation log (#44, E19) ---------------------------------------------


def test_m7_timeouts(fake_engine: FakeEngine) -> None:
    for op in OPS:
        serve(fake_engine, op)
    engine = client(fake_engine)
    for op in OPS:
        call(engine, op)
    by_name = dict(zip((op.name for op in OPS), fake_engine.requests, strict=True))
    assert read_timeout(by_name["open_rewrite"]) == turn_timeout(None)
    assert read_timeout(by_name["apply_directive"]) == turn_timeout(None)
    assert read_timeout(by_name["list_outbox"]) == engine.timeout
    assert read_timeout(by_name["cancel_backfill"]) == MUTATION_TIMEOUT_S
    assert read_timeout(by_name["import_story"]) == UPLOAD
    assert read_timeout(by_name["upgrade_pinned_stories"]) == PROFILE_EDIT
    for op in OPS:
        timeout = read_timeout(by_name[op.name])
        if op.method == "GET":
            assert timeout == engine.timeout, op.name
        elif op.name in TURN_TIMEOUT_WRITES:
            assert timeout == turn_timeout(None), op.name
        elif op.name not in {"import_story", "upgrade_pinned_stories"}:
            assert timeout == MUTATION_TIMEOUT_S, op.name


def _operator_request(user: Any) -> HttpRequest:
    request = RequestFactory().post("/")
    request.user = user
    return request


# The 202 answers a recorded write gets in the recording test, by method.
QUEUED_202 = {
    "export_story": "export_started",
    "import_story": "import_started",
    "start_backfill": "backfill_started",
    "request_certification_run_profile": "certification_queued",
}


@pytest.mark.django_db
def test_m7_writes_are_recorded(fake_engine: FakeEngine, operator: Any) -> None:
    writes = [op for op in OPS if op.method != "GET"]
    assert len(writes) == 20
    for op in writes:
        fixture = QUEUED_202.get(op.name)
        serve(fake_engine, op, fixture, 202 if fixture else None)
    fake_engine.respond("POST", DIRECTIVES, 202, json=m7("directive_routed"))
    keys = [f"01JM7KY{'0' * 17}{n:02d}" for n in range(len(writes))]
    with override_settings(ENGINE_URL=URL, ENGINE_SERVICE_KEY=SERVICE_KEY):
        engine = engine_client_for(_operator_request(operator))
        engine._transport = fake_engine.transport
        for op, key in zip(writes, keys, strict=True):
            call(engine, op, idempotency_key=key)
    rows = {r.kind: r for r in EngineOperation.objects.order_by("id")}
    assert list(rows) == [op.name for op in writes]
    for op, key in zip(writes, keys, strict=True):
        row = rows[op.name]
        assert row.idempotency_key == key, op.name
        if op.path.startswith(("/stories/", "/admin/stories/0")):
            assert (row.entity_type, row.entity_id, row.story_id) == ("story", STORY, STORY)
    for name in ("upgrade_pinned_stories", "request_certification_run_profile"):
        assert (rows[name].entity_type, rows[name].entity_id) == ("engine_profile", FULL)
    imported = rows["import_certification"]
    assert (imported.entity_type, imported.entity_id) == (
        "certification",
        RECORD["certification_id"],
    )
    invalidated = rows["invalidate_certification"]
    assert (invalidated.entity_type, invalidated.entity_id) == ("certification", CERTIFICATION)
    story_import = rows["import_story"]
    assert (story_import.entity_type, story_import.entity_id) == ("story", NEW_STORY)
    assert story_import.request["body"]["file"] == upload().described()
    assert story_import.request["body"]["metadata"] == METADATA
    queued = {
        "export_story": EXPORT_JOB,
        "import_story": IMPORT_JOB,
        "start_backfill": "01JM7JB0000000000000000001",  # read from ``started.job_id``
        "request_certification_run_profile": "01JM7JB0000000000000000005",
    }
    for name, job_id in queued.items():
        assert (rows[name].outcome, rows[name].job_id) == (EngineOperation.Outcome.QUEUED, job_id)
    assert rows["apply_directive"].http_status == 202
    assert rows["apply_directive"].outcome == EngineOperation.Outcome.OK  # ``ended`` (E19)
    assert rows["open_rewrite"].outcome == EngineOperation.Outcome.OK
    for request in fake_engine.requests:
        assert EXPECTED_REVISION_HEADER not in request.headers  # none was given


# --- the changed signatures on existing calls ------------------------------------------------

PROFILES = "/engine-profiles"
BELIEFS = "/stories/{story_id}/inspect/beliefs"
ASSIGN = "/stories/{story_id}/engine-profile"
OVERRIDES = "/stories/{story_id}/profile-overrides"
PROFILE = "/engine-profiles/{profile_id}"


def _existing(fake: FakeEngine) -> EngineClient:
    fake.respond("GET", PROFILES, 200, json=m7("presets"))
    fake.respond("GET", BELIEFS, 200, json=m7("beliefs_nested"))
    fake.respond("PUT", ASSIGN, 200, json=m7("impact_preview_backfill"))
    fake.respond("PATCH", OVERRIDES, 200, json=m7("impact_preview_backfill"))
    fake.respond("PUT", PROFILE, 200, json=m7("profile_edit_backfill_impact"))
    return client(fake)


def _existing_calls(engine: EngineClient, **given: Any) -> None:
    """Each changed call once, with its new argument when ``given`` names it."""
    engine.list_engine_profiles(**({"preset": given["preset"]} if "preset" in given else {}))
    engine.inspect_beliefs(STORY, ALICE, **({"kind": given["kind"]} if "kind" in given else {}))
    mode = {"upgrade_mode": given["upgrade_mode"]} if "upgrade_mode" in given else {}
    engine.assign_engine_profile(STORY, FULL, ignore_warnings=False, idempotency_key=KEY, **mode)
    engine.patch_profile_overrides(
        STORY, {"a": 1}, expected_revision=None, ignore_warnings=False, idempotency_key=KEY, **mode
    )
    engine.update_engine_profile(
        FULL, name="Full", settings={}, ignore_warnings=False, idempotency_key=KEY, **mode
    )


def test_changed_signatures_send_only_when_given(fake_engine: FakeEngine) -> None:
    _existing_calls(_existing(fake_engine), preset=True, kind="nested", upgrade_mode="backfill")
    profiles, beliefs, assign, overrides, update = fake_engine.requests
    assert ("preset", "true") in query(profiles)
    assert dict(query(beliefs))["kind"] == "nested"
    for request in (assign, overrides, update):
        assert body(request)["upgrade_mode"] == "backfill"
        assert "upgradeMode" not in body(request)
    fake_engine.reset()
    client(fake_engine).list_engine_profiles(preset=False)
    assert ("preset", "false") in query(fake_engine.requests[0])


def test_bare_calls_send_what_main_sends(fake_engine: FakeEngine) -> None:
    """**Complement:** the bare calls' queries and bodies equal main's (literal values written
    from main's code)."""
    _existing_calls(_existing(fake_engine))
    profiles, beliefs, assign, overrides, update = fake_engine.requests
    assert query(profiles) == [("limit", "50")]
    assert query(beliefs) == [("viewpoint", "omniscient"), ("character", ALICE), ("limit", "100")]
    assert body(assign) == {"profile_id": FULL, "ignore_warnings": False}
    assert body(overrides) == {"settings": {"a": 1}, "ignore_warnings": False}
    assert body(update) == {"name": "Full", "settings": {}, "ignore_warnings": False}
    fake_engine.reset()
    fake_engine.respond("GET", "/stories/{story_id}/history", 200, json=m7("history_as_recorded"))
    client(fake_engine).get_history(STORY)
    assert fake_engine.requests[0].url.query == b""


def test_regenerate_parses_a_directed_202(fake_engine: FakeEngine) -> None:
    regenerate = "/stories/{story_id}/regenerate"
    fake_engine.respond("POST", regenerate, 202, json=m7("directed_pending"))
    engine = client(fake_engine)
    pending = engine.regenerate(STORY, {"turn": 4}, expected_revision=None, idempotency_key=KEY)
    assert isinstance(pending, DirectedPending)
    assert isinstance(pending.rewrite_session, RewriteSession)
    assert pending.rewrite_session.held_render is not None
    assert pending.rewrite_session.held_render.model_calls == 3
    fake_engine.respond("POST", regenerate, 200, json=engine_fixture("regenerate"))
    result = engine.regenerate(STORY, {"turn": 4}, expected_revision=None, idempotency_key=KEY)
    assert isinstance(result, RegenerateResult)


def test_the_fixtures_share_one_cast() -> None:
    """E16: every M7 fixture reuses ``story.json``'s cast and ids."""
    story = m7("story")
    assert story["id"] == STORY
    assert [(c["id"], c["name"], c["is_player"]) for c in story["characters"]] == [
        (MARA, "Mara", True),
        (ALICE, "Alice", False),
        (BOB, "Bob", False),
    ]
    assert story["engine_profile"] == {"id": FULL, "version": 4, "pinned": False}
    settings = story["settings"]
    assert (settings["prune_policy"], settings["backfill_conflict_policy"]) == (None, "ask")
    assert settings["ambiguous_retcon_timing"] == "ask_author"
    for name in ("rewrite_open", "rewrite_session", "operations", "export_started"):
        assert STORY in json.dumps(m7(name)), name
    for name in ("backfill_paused", "beliefs_nested", "history_as_recorded"):
        text = json.dumps(m7(name))
        assert ALICE in text, name
        assert BOB in text, name
    assert m7("certification")["id"] == CERTIFICATION
    from tests.inspect.helpers import full_v4

    config = full_v4()
    assert config["profile"] == {"id": FULL, "version": 4, "pinned": False, "deleted": False}
    assert config["quality_status"]["certification_id"] == CERTIFICATION
    passed = [f["metric_id"] for f in m7("certification")["floors_passed"]]
    assert config["quality_status"]["floors_passed"] == passed
    assert config["effective"]["verification"]["shape_tightening"] == "on"

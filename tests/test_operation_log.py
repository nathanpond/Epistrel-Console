"""The operation log's recorder (#44): every mutating call made through
``engine_client_for(request)`` writes one row; reads write none; secrets never land in it."""

import contextlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from console.engine.client import (
    Call,
    EngineClient,
    EngineError,
    EngineUnreachable,
    Operation,
    calling,
)
from console.engine.models import OperationRecord, TurnReport
from console.engine_access import RecordingEngineClient, complete_queued, engine_client_for
from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

URL = "http://engine.test"
SERVICE_KEY = "service-key-for-the-operation-log-test-000"
STORY = "01JA0000000000000000000ST1"
KEY = "01JA0000000000000000000KEY"
TURN_OP = "01JA0000000000000000000OP3"
QUEUED_OP = "01JA0000000000000000000OP1"
API_KEY = "sk-live-this-must-never-be-stored-0000"

RESPOND = Call("respond", Operation("POST", "/stories/{story_id}/respond", None, False))
RESPOND_TYPED = Call("respond", Operation("POST", "/stories/{story_id}/respond", TurnReport, False))
TRUNCATE = Call("truncate", Operation("POST", "/stories/{story_id}/truncate", None, False))
PUT_API_KEY = Call(
    "put_api_key", Operation("PUT", "/admin/model-config/{purpose}/api-key", None, False)
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("engine_settings"),
]


@pytest.fixture
def engine_settings() -> Any:
    with override_settings(ENGINE_URL=URL, ENGINE_SERVICE_KEY=SERVICE_KEY):
        yield


def _request(user: Any) -> HttpRequest:
    request = RequestFactory().post("/")
    request.user = user
    return request


def client_for(operator: Any, transport: httpx.BaseTransport) -> EngineClient:
    client = engine_client_for(_request(operator))
    client._transport = transport
    return client


def respond(client: EngineClient, call: Call = RESPOND, **body: Any) -> httpx.Response:
    with calling(call):
        return client._request(
            "POST",
            f"/stories/{STORY}/respond",
            json={"actor_character_id": "c1", "player_input": {"say": "hi"}, **body},
            headers={"Idempotency-Key": KEY, "Prefer": "respond-async"},
            timeout=60,
        )


def test_the_operator_client_records(operator: Any) -> None:
    assert isinstance(engine_client_for(_request(operator)), RecordingEngineClient)


def test_a_successful_mutating_call_writes_one_row(operator: Any, fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", "/stories/{story_id}/respond", 200, json=engine_fixture("respond"))
    respond(client_for(operator, fake_engine.transport))
    (row,) = EngineOperation.objects.all()
    assert row.kind == "respond"
    assert row.outcome == "ok"
    assert row.http_method == "POST"
    assert row.path == f"/stories/{STORY}/respond"
    assert row.http_status == 200
    assert row.operator == operator
    assert row.operator_username == "nate"
    assert row.principal == "console:nate"
    assert row.story_id == STORY
    assert row.timeline_revision == 7
    assert row.config_revision == 4
    assert row.idempotency_key == KEY
    assert row.engine_operation_id == TURN_OP  # a turn's id comes from its beats
    assert row.error_code == ""
    assert row.replayed is False
    assert row.is_guardrail_rejection is False
    assert row.elapsed_ms is not None
    assert row.response is not None
    assert row.response["model_calls"][0]["purpose"] == "generation"
    assert row.request == {
        "body": {"actor_character_id": "c1", "player_input": {"say": "hi"}},
        "headers": {"Idempotency-Key": KEY, "Prefer": "respond-async"},
    }


def test_a_registered_client_method_is_recorded_under_its_name(
    operator: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``@operation`` names the call: the decorator alone makes a new method recorded."""
    from console.engine import client as client_module

    # Registered into a copy of the registry, so the contract test never sees it.
    monkeypatch.setattr(client_module, "OPERATIONS", dict(client_module.OPERATIONS))

    @client_module.operation("POST", "/stories/{story_id}/truncate")
    def truncate(self: EngineClient, story_id: str) -> httpx.Response:
        return self._request("POST", f"/stories/{story_id}/truncate", json={"to_turn": 1})

    fake = FakeEngine()
    fake.respond("POST", "/stories/{story_id}/truncate", 200, json=engine_fixture("truncate"))
    truncate(client_for(operator, fake.transport), STORY)
    (row,) = EngineOperation.objects.all()
    assert row.kind == "truncate"
    # No key sent: the Engine's echoed key is the row's.
    assert row.idempotency_key == "01JA0000000000000000000KY2"
    assert row.engine_operation_id == "01JA0000000000000000000OP2"
    assert row.timeline_revision == 9


def test_a_guardrail_violation_is_an_error_row_with_its_violations(
    operator: Any, fake_engine: FakeEngine
) -> None:
    fake_engine.respond(
        "POST",
        "/stories/{story_id}/respond",
        422,
        json=engine_fixture("respond_guardrail_violation"),
    )
    response = respond(client_for(operator, fake_engine.transport))
    assert response.status_code == 422  # the raw answer reaches the caller unchanged
    (row,) = EngineOperation.objects.all()
    assert row.outcome == "error"
    assert row.http_status == 422
    assert row.error_code == "guardrail_violation"
    assert row.is_guardrail_rejection is True
    assert row.response is not None
    violation = row.response["error"]["details"]["violations"][0]
    assert violation["policy_id"] == "minors_no_sexual_content"
    assert row.idempotency_key == KEY  # the Console's sent key


def test_unreachable_is_recorded_and_the_error_still_raised(operator: Any) -> None:
    fake = FakeEngine()
    fake.fail("POST", "/stories/{story_id}/respond", httpx.ConnectError("refused"))
    with pytest.raises(EngineUnreachable):
        respond(client_for(operator, fake.transport))
    (row,) = EngineOperation.objects.all()
    assert row.outcome == "unreachable"
    assert row.error_code == "connection failed"
    assert row.http_status is None
    assert row.response is None
    assert row.story_id == STORY
    assert row.idempotency_key == KEY


def test_a_timeout_is_unreachable(operator: Any) -> None:
    fake = FakeEngine()
    fake.fail("POST", "/stories/{story_id}/respond", httpx.ReadTimeout("slow"))
    with pytest.raises(EngineUnreachable):
        respond(client_for(operator, fake.transport))
    assert EngineOperation.objects.get().error_code == "timeout"


def test_read_calls_write_no_row(operator: Any, fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories", 200, json={"items": []})
    fake_engine.respond(
        "GET", "/operations/{operation_id}", 200, json=engine_fixture("get_operation")
    )
    client = client_for(operator, fake_engine.transport)
    client.list_stories()
    client.get_operation(TURN_OP)
    assert len(fake_engine.requests) == 2
    assert EngineOperation.objects.count() == 0


def test_an_api_key_is_stored_redacted(operator: Any, fake_engine: FakeEngine) -> None:
    fake_engine.respond("PUT", "/admin/model-config/{purpose}/api-key", 204)
    client = client_for(operator, fake_engine.transport)
    with calling(PUT_API_KEY):
        client._request("PUT", "/admin/model-config/generation/api-key", json={"api_key": API_KEY})
    # The key did reach the Engine...
    assert API_KEY in fake_engine.requests[0].content.decode()
    (row,) = EngineOperation.objects.all()
    # ...but not the log.
    assert row.request["body"] == {"api_key": "***"}
    assert API_KEY not in json.dumps(row.request)
    assert row.kind == "put_api_key"
    assert row.outcome == "ok"
    assert row.http_status == 204
    assert row.story_id == ""  # admin calls have no story


def test_the_service_key_appears_in_no_stored_field(operator: Any) -> None:
    fake = FakeEngine()
    fake.respond("POST", "/stories/{story_id}/respond", 200, json=engine_fixture("respond"))
    fake.respond("POST", "/stories/{story_id}/truncate", 200, json=engine_fixture("truncate"))
    fake.respond("PUT", "/admin/model-config/{purpose}/api-key", 204)
    client = client_for(operator, fake.transport)
    respond(client)
    with calling(TRUNCATE):
        client._request("POST", f"/stories/{STORY}/truncate", json={"to_turn": 1})
    with calling(PUT_API_KEY):
        client._request(
            "PUT", "/admin/model-config/generation/api-key", json={"api_key": SERVICE_KEY}
        )
    fake.fail("POST", "/stories/{story_id}/respond", httpx.ConnectError("refused"))
    with pytest.raises(EngineUnreachable):
        respond(client)
    # Every request carried the key in Authorization...
    assert all(r.headers["authorization"] == f"Bearer {SERVICE_KEY}" for r in fake.requests)
    rows = list(EngineOperation.objects.values())
    assert len(rows) == 4
    # ...and no field of any row holds it.
    for row in rows:
        assert SERVICE_KEY not in json.dumps(row, default=str)
        assert "authorization" not in json.dumps(row["request"]).lower()
        assert "x-epistrel-principal" not in json.dumps(row["request"]).lower()


def test_a_queued_turn_is_queued_and_settled_in_place(
    operator: Any, fake_engine: FakeEngine
) -> None:
    fake_engine.respond(
        "POST", "/stories/{story_id}/respond", 202, json=engine_fixture("respond_queued")
    )
    respond(client_for(operator, fake_engine.transport))
    row = EngineOperation.objects.get()
    assert row.outcome == "queued"
    assert row.http_status == 202
    assert row.engine_operation_id == QUEUED_OP
    elapsed = row.elapsed_ms

    still_running = OperationRecord.from_json(
        {**engine_fixture("get_operation"), "status": "in_flight", "result": None}
    )
    assert still_running is not None
    assert complete_queued(QUEUED_OP, still_running) == 0
    assert EngineOperation.objects.get().outcome == "queued"

    committed = OperationRecord.from_json(engine_fixture("get_operation"))
    assert committed is not None
    assert complete_queued(QUEUED_OP, committed) == 1
    row.refresh_from_db()
    assert row.outcome == "ok"
    assert row.timeline_revision == 7
    assert row.config_revision == 4
    assert row.response is not None
    assert row.response["model_calls"]
    assert row.elapsed_ms == elapsed  # still the 202 round trip
    # Already settled: a second completion changes nothing.
    assert complete_queued(QUEUED_OP, committed) == 0


@pytest.mark.parametrize("status", ["failed", "abandoned"])
def test_a_queued_turn_that_fails_becomes_an_error(
    operator: Any, fake_engine: FakeEngine, status: str
) -> None:
    fake_engine.respond(
        "POST", "/stories/{story_id}/respond", 202, json=engine_fixture("respond_queued")
    )
    respond(client_for(operator, fake_engine.transport))
    failed = OperationRecord.from_json(
        {**engine_fixture("get_operation"), "status": status, "result": None}
    )
    assert failed is not None
    complete_queued(QUEUED_OP, failed)
    row = EngineOperation.objects.get()
    assert (row.outcome, row.error_code) == ("error", status)


def test_a_failed_queued_turn_takes_the_stored_error_code(
    operator: Any, fake_engine: FakeEngine
) -> None:
    fake_engine.respond(
        "POST", "/stories/{story_id}/respond", 202, json=engine_fixture("respond_queued")
    )
    respond(client_for(operator, fake_engine.transport))
    error = {"error": {"code": "model_unavailable", "message": "down", "details": {}}}
    failed = OperationRecord.from_json(
        {
            **engine_fixture("get_operation"),
            "status": "failed",
            "result": {"status": 503, "body": error},
        }
    )
    assert failed is not None
    complete_queued(QUEUED_OP, failed)
    assert EngineOperation.objects.get().error_code == "model_unavailable"


def test_a_replay_is_ok_and_marked(operator: Any, fake_engine: FakeEngine) -> None:
    fake_engine.respond(
        "POST",
        "/stories/{story_id}/respond",
        200,
        json=engine_fixture("respond"),
        headers={"Idempotent-Replay": "true", "Idempotency-Key": KEY},
    )
    client = client_for(operator, fake_engine.transport)
    respond(client)
    respond(client)  # a double-click: two rows, both replays here
    rows = list(EngineOperation.objects.all())
    assert len(rows) == 2
    assert all(r.outcome == "ok" and r.replayed for r in rows)


def test_an_unparseable_answer_is_a_bad_response_error(operator: Any) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"surprise": True}))
    respond(client_for(operator, transport), call=RESPOND_TYPED)
    row = EngineOperation.objects.get()
    assert (row.outcome, row.error_code) == ("error", "bad_response")


def test_a_reference_replay_is_not_a_bad_response(operator: Any) -> None:
    body = {"operation_id": TURN_OP, "reference": True, "idempotency_key": KEY}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    respond(client_for(operator, transport), call=RESPOND_TYPED)
    row = EngineOperation.objects.get()
    assert (row.outcome, row.replayed, row.engine_operation_id) == ("ok", True, TURN_OP)


def test_a_safe_fallback_is_a_guardrail_rejection_but_ok(
    operator: Any, fake_engine: FakeEngine
) -> None:
    body = engine_fixture("respond")
    body["guardrail"] = {"violations": [], "fallback_used": True}
    fake_engine.respond("POST", "/stories/{story_id}/respond", 200, json=body)
    respond(client_for(operator, fake_engine.transport))
    row = EngineOperation.objects.get()
    assert (row.outcome, row.is_guardrail_rejection) == ("ok", True)


def test_a_clean_turn_is_not_a_guardrail_rejection(operator: Any, fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", "/stories/{story_id}/respond", 200, json=engine_fixture("respond"))
    respond(client_for(operator, fake_engine.transport))
    assert EngineOperation.objects.get().is_guardrail_rejection is False


def test_a_failed_write_never_masks_the_engine_answer(
    operator: Any,
    fake_engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(**kwargs: Any) -> None:
        raise RuntimeError(f"disk full {kwargs}")

    monkeypatch.setattr(EngineOperation.objects, "create", broken)
    fake_engine.respond("POST", "/stories/{story_id}/respond", 200, json=engine_fixture("respond"))
    with caplog.at_level(logging.ERROR, logger="console.operations"):
        response = respond(client_for(operator, fake_engine.transport))
    assert response.status_code == 200
    (record,) = [r for r in caplog.records if r.name == "console.operations"]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "operation record not written: kind=respond status=200"
    assert "Mara" not in caplog.text  # never the body


def test_a_deleted_operator_keeps_the_username(operator: Any, fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", "/stories/{story_id}/respond", 200, json=engine_fixture("respond"))
    respond(client_for(operator, fake_engine.transport))
    operator.delete()
    row = EngineOperation.objects.get()
    assert row.operator is None
    assert row.operator_username == "nate"


def test_the_unauthenticated_client_records_nothing(fake_engine: FakeEngine) -> None:
    from console.engine_access import engine_client_unauthenticated

    assert type(engine_client_unauthenticated()) is EngineClient


# --- #64: the acting principal, and the new nullable columns ----------------------------------


def test_a_call_made_while_acting_as_alice_records_both(
    operator: Any, fake_engine: FakeEngine
) -> None:
    from console.models import ActingPrincipal

    ActingPrincipal.objects.create(user=operator, principal="alice")
    fake_engine.respond("POST", "/stories/{story_id}/respond", 200, json=engine_fixture("respond"))
    respond(client_for(operator, fake_engine.transport))
    (row,) = EngineOperation.objects.all()
    assert (row.operator, row.operator_username, row.principal) == (operator, "nate", "alice")
    sent = fake_engine.requests[0].headers["x-epistrel-principal"]
    assert sent == "alice"
    assert (row.entity_type, row.entity_id) == ("story", STORY)


def test_a_per_call_principal_is_the_one_recorded(operator: Any, fake_engine: FakeEngine) -> None:
    fake_engine.respond(
        "POST", "/stories/{story_id}/truncate", 200, json=engine_fixture("truncate")
    )
    client = client_for(operator, fake_engine.transport)
    with calling(TRUNCATE):
        client._request("POST", f"/stories/{STORY}/truncate", principal="bob", json={})
    assert EngineOperation.objects.get().principal == "bob"


def test_the_new_columns_are_nullable(operator: Any) -> None:
    row = EngineOperation.objects.create(
        operator_username="nate",
        principal="console:nate",
        kind="x",
        http_method="POST",
        path="/x",
        outcome="ok",
    )
    row.refresh_from_db()
    assert (row.entity_type, row.entity_id, row.job_id) == (None, None, None)


@pytest.mark.django_db(transaction=True)
def test_existing_rows_migrate_and_name_their_story() -> None:
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    before = [("console", "0001_engine_operation")]
    after = [("console", "0002_acting_principal")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    old = executor.loader.project_state(before).apps.get_model("console", "EngineOperation")
    common = {"operator_username": "nate", "principal": "console:nate", "http_method": "POST"}
    old.objects.create(kind="respond", path="/x", outcome="ok", story_id=STORY, **common)
    old.objects.create(kind="put_settings", path="/admin/settings", outcome="ok", **common)
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(after)
    new = executor.loader.project_state(after).apps.get_model("console", "EngineOperation")
    rows = {r.kind: (r.entity_type, r.entity_id, r.job_id) for r in new.objects.all()}
    assert rows == {"respond": ("story", STORY, None), "put_settings": (None, None, None)}


# --- a sanitize keeps only lengths (#144) --------------------------------------------------------

MEMORY = "01JA0000000000000000000ME1"
SANITIZE = "/admin/stories/{story_id}/memories/{memory_id}/sanitize"
SPAN = "the vault code is 4471"
SURROGATE = "…"


def sanitize_written(text: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(
        (Path(__file__).parent / "fixtures" / "inspect" / "memory_written.json").read_text()
    )
    body["memory"].update(id=MEMORY, text=text)
    return body


def sanitize_refusal(code: str) -> dict[str, Any]:
    if code == "span_not_found":
        details: dict[str, Any] = {"memory_id": MEMORY}
        message = f"span not found in memory {MEMORY}"
    else:
        details = {
            "violations": [
                {
                    "policy_id": "no_codes",
                    "name": "No codes",
                    "message": f"The surrogate quotes a code: {SURROGATE!r} near {SPAN!r}",
                    "kind": "forbidden_content",
                }
            ],
            "model_calls": 1,
            "elapsed_ms": 3,
        }
        message = f"The input breaks a policy: {SPAN}"
    return {"error": {"code": code, "message": message, "details": details}}


def sanitize(operator: Any, fake: FakeEngine, span: str = SPAN, surrogate: str = SURROGATE) -> None:
    client = client_for(operator, fake.transport)
    with contextlib.suppress(EngineError):  # a refusal is still recorded
        client.sanitize_memory(STORY, MEMORY, span, surrogate, idempotency_key=KEY)


def stored(row: EngineOperation) -> str:
    return json.dumps(row.request) + json.dumps(row.response)


def test_a_sanitize_stores_only_the_lengths_of_span_and_surrogate(
    operator: Any, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("POST", SANITIZE, 200, json=sanitize_written("The safe is locked: …."))
    sanitize(operator, fake_engine)
    (sent,) = fake_engine.requests
    assert json.loads(sent.content) == {"span": SPAN, "surrogate": SURROGATE}
    (row,) = EngineOperation.objects.all()
    assert row.kind == "sanitize_memory"
    assert (row.entity_type, row.entity_id) == ("story", STORY)
    assert row.path == f"/admin/stories/{STORY}/memories/{MEMORY}/sanitize"
    assert row.request["body"] == {"span_length": 22, "surrogate_length": 1}
    assert row.response is not None
    assert row.response["memory"]["text"] == {"text_length": 22}
    assert row.response["memory"]["id"] == MEMORY  # the rest of the answer is kept
    assert row.engine_operation_id == "01JB2Z8Y5Q3W6K9M1N4P7R0P01"
    assert SPAN not in stored(row)


@pytest.mark.parametrize("code", ["span_not_found", "guardrail_violation"])
def test_a_refused_sanitize_keeps_codes_and_policies_but_no_text(
    operator: Any, fake_engine: FakeEngine, code: str
) -> None:
    fake_engine.respond("POST", SANITIZE, 422, json=sanitize_refusal(code))
    sanitize(operator, fake_engine)
    (row,) = EngineOperation.objects.all()
    assert (row.outcome, row.error_code) == ("error", code)
    assert row.request["body"] == {"span_length": 22, "surrogate_length": 1}
    assert SPAN not in stored(row)
    assert row.response is not None
    error = row.response["error"]
    assert set(error["message"]) == {"text_length"}
    if code == "guardrail_violation":
        assert row.is_guardrail_rejection is True
        (violation,) = error["details"]["violations"]
        assert violation["policy_id"] == "no_codes"
        assert (violation["name"], violation["kind"]) == ("No codes", "forbidden_content")
        assert set(violation["message"]) == {"text_length"}
        assert SURROGATE not in stored(row)
        assert error["details"]["model_calls"] == 1
    else:
        assert error["details"] == {"memory_id": MEMORY}


def test_a_surrogate_containing_the_span_leaves_no_span_in_the_row(
    operator: Any, fake_engine: FakeEngine
) -> None:
    fake_engine.respond("POST", SANITIZE, 200, json=sanitize_written("Bobby met Ann."))
    sanitize(operator, fake_engine, "Bob", "Bobby")
    row = EngineOperation.objects.get()
    assert row.request["body"] == {"span_length": 3, "surrogate_length": 5}
    assert "Bob" not in stored(row)
    assert row.response is not None
    assert row.response["memory"]["text"] == {"text_length": 14}


def test_a_malformed_sanitize_body_stores_null_lengths() -> None:
    from console.operation_log import sanitize_request

    assert sanitize_request({"span": 3, "surrogate": "x"}) == {
        "span_length": None,
        "surrogate_length": None,
    }
    assert sanitize_request("Bob") == {"span_length": None, "surrogate_length": None}


def test_other_kinds_are_stored_unchanged(operator: Any, fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", "/stories/{story_id}/respond", 200, json=engine_fixture("respond"))
    respond(client_for(operator, fake_engine.transport))
    (row,) = EngineOperation.objects.all()
    assert row.request["body"] == {"actor_character_id": "c1", "player_input": {"say": "hi"}}
    assert row.response == engine_fixture("respond")


@pytest.mark.parametrize("answer", ["ok", "span_not_found", "guardrail_violation"])
def test_the_operations_pages_show_only_lengths_and_offer_no_fetch(
    operator: Any,
    operator_client: Any,
    fake_engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
) -> None:
    from console.views import operations

    if answer == "ok":
        fake_engine.respond("POST", SANITIZE, 200, json=sanitize_written("The safe: …."))
    else:
        fake_engine.respond("POST", SANITIZE, 422, json=sanitize_refusal(answer))
    sanitize(operator, fake_engine)
    row = EngineOperation.objects.get()
    reads = FakeEngine()

    def reading_client(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = reads.transport
        return client

    monkeypatch.setattr(operations, "engine_client_for", reading_client)
    listing = operator_client.get("/operations/").content.decode()
    detail = operator_client.get(f"/operations/{row.pk}/").content.decode()
    fetched = operator_client.get(f"/operations/{row.pk}/?fetch=1").content.decode()
    for html in (listing, detail, fetched):
        assert SPAN not in html
        assert "4471" not in html
    for html in (detail, fetched):
        assert "Fetch from Engine" not in html
        if answer == "ok":  # the row names an Engine operation: the note stands in its place
            assert "Re-reading the Engine's record isn't offered for sanitize calls" in html
    assert "span_length" in detail
    assert reads.requests == []

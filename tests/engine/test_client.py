import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from console.engine.client import (
    OPERATIONS,
    Call,
    EngineClient,
    EngineClientError,
    EngineError,
    EngineNotConfigured,
    EngineUnreachable,
    _raise_for_envelope,
    current_call,
)
from console.engine.models import (
    Adjudication,
    BackfillStarted,
    ClockValue,
    CouldHaveKnownFlag,
    CoverageGap,
    DeferredCharacter,
    DeploymentSettings,
    DirectedPending,
    EscalationDeferred,
    ForkResult,
    HealthChecks,
    HealthResponse,
    History,
    HistoryItem,
    InspectPosition,
    Ledger,
    LocationAdjacency,
    MaterializedEntry,
    MessageFeedback,
    ModelTiers,
    NestedSeed,
    Notice,
    OmniscientStoryCharacter,
    OperationRecord,
    Participation,
    Proposition,
    QualityStatus,
    Reconcile,
    ReconciliationCount,
    RegenerateResult,
    ReplyMessage,
    RequiredEffect,
    RespondResponse,
    RetrievalBody,
    Seed,
    StartingState,
    StateEffect,
    Story,
    StoryList,
    StoryProfileWrite,
    StorySettingsDefaults,
    TruncateResult,
    TurnClock,
    TurnRole,
    UtteranceLabels,
    Variants,
    WorkersCheck,
    rejected_entries,
)
from tests.fakes.engine import FakeEngine, recorded_schema, with_test_thing
from tests.fixtures import engine_fixture

URL = "http://engine.test"
KEY = "sekrit-123"
OK_BODY: dict[str, Any] = {
    "status": "ok",
    "checks": {
        "database": "ok",
        "workers": {"alive": 2, "last_heartbeat_s": 0.4},
        "oldest_overdue_timer_s": None,
        "oldest_overdue_job_s": None,
        "ffmpeg": "missing",
        "ffprobe": "ok",
        "erasure_pending": 0,
    },
    "content_logging": False,
}
DOWN_BODY = {"status": "unavailable", "checks": {"database": "error"}}


@pytest.fixture
def thing_engine() -> FakeEngine:
    return FakeEngine(with_test_thing(recorded_schema()))


def client(fake: FakeEngine, key: str | None = KEY) -> EngineClient:
    return EngineClient(URL, key, transport=fake.transport)


def test_health_parses_200(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/health", 200, json=OK_BODY)
    result = client(fake_engine).health()
    assert result.http_status == 200
    assert result.raw == OK_BODY
    assert result.body == HealthResponse(
        status="ok",
        checks=HealthChecks(
            database="ok",
            workers=WorkersCheck(alive=2, last_heartbeat_s=0.4),
            ffmpeg="missing",
            ffprobe="ok",
            erasure_pending=0,
        ),
        content_logging=False,
    )
    assert isinstance(result.latency_ms, int)


def test_health_parses_503_and_fills_defaults(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/health", 503, json=DOWN_BODY)
    result = client(fake_engine).health()
    assert result.http_status == 503
    assert result.body is not None
    assert result.body.status == "unavailable"
    assert result.body.checks.database == "error"
    assert result.body.checks.workers == WorkersCheck()


@pytest.mark.parametrize(
    "raw",
    [
        {"status": "ok"},
        {"status": "fine", "checks": {"database": "ok"}},
        {"status": "ok", "checks": {"database": 1}},
        {"status": "ok", "checks": {"database": "ok", "workers": {"alive": "two"}}},
        {"status": "ok", "checks": {"database": "ok"}, "content_logging": 0},
        [1, 2],
    ],
)
def test_wrong_shaped_body_gives_none(raw: object) -> None:
    # Raw transport: the fake would (rightly) refuse to serve a body the schema forbids.
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=raw))
    result = EngineClient(URL, KEY, transport=transport).health()
    assert result.body is None
    assert result.raw == raw


def test_non_json_health_has_no_raw() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
    result = EngineClient(URL, None, transport=transport).health()
    assert (result.body, result.raw) == (None, None)


def test_extra_keys_are_ignored() -> None:
    body = {**OK_BODY, "new_field": 1, "checks": {**OK_BODY["checks"], "gpu": "ok"}}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    result = EngineClient(URL, None, transport=transport).health()
    assert result.body is not None
    assert result.body.checks.database == "ok"


def test_no_key_on_exempt_route_but_key_elsewhere(thing_engine: FakeEngine) -> None:
    thing_engine.respond("GET", "/health", 200, json=OK_BODY)
    thing_engine.respond("GET", "/_test/thing", 200, json={"name": "x"})
    engine = client(thing_engine)
    engine.health()
    engine._request("GET", "/_test/thing")
    health_req, thing_req = thing_engine.requests
    # Complement: the exempt route never carries the key, in a header or the URL.
    assert "authorization" not in health_req.headers
    assert all(KEY not in v for v in health_req.headers.values())
    assert thing_req.headers["authorization"] == f"Bearer {KEY}"
    for request in thing_engine.requests:
        assert request.headers["user-agent"].startswith("epistrel-console/")
        assert request.headers["accept"] == "application/json"


@pytest.mark.parametrize("key", [None, ""])
def test_no_key_set_sends_no_authorization(thing_engine: FakeEngine, key: str | None) -> None:
    thing_engine.respond("GET", "/health", 200, json=OK_BODY)
    thing_engine.respond("GET", "/_test/thing", 200, json={"name": "x"})
    engine = client(thing_engine, key=key)
    engine.health()
    engine._request("GET", "/_test/thing")
    assert all("authorization" not in r.headers for r in thing_engine.requests)


def test_exempt_routes_with_base_path_prefix() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    engine = EngineClient("http://engine.test/prefix/", KEY, transport=httpx.MockTransport(record))
    engine.health()
    engine._request("GET", "/stories")
    assert str(seen[0].url) == "http://engine.test/prefix/health"
    assert "authorization" not in seen[0].headers
    assert seen[1].headers["authorization"] == f"Bearer {KEY}"


def test_principal_header_sent(thing_engine: FakeEngine) -> None:
    thing_engine.respond("POST", "/_test/thing", 200, json={"name": "x"})
    thing_engine.respond("GET", "/_test/thing", 200, json={"name": "x"})
    engine = client(thing_engine)
    engine._request("POST", "/_test/thing", principal="console:nate")
    engine._request("GET", "/_test/thing")
    post, get = thing_engine.requests
    assert post.headers["x-epistrel-principal"] == "console:nate"
    assert "x-epistrel-principal" not in get.headers


def raw_principal(request: httpx.Request) -> bytes:
    """The principal header's bytes as sent."""
    return next(v for k, v in request.headers.raw if k.lower() == b"x-epistrel-principal")


def test_latin1_principal_is_sent_as_latin1_bytes(thing_engine: FakeEngine) -> None:
    # #64: the Engine decodes headers as Latin-1, so "José" arrives as "José".
    thing_engine.respond("POST", "/_test/thing", 200, json={"name": "x"})
    client(thing_engine)._request("POST", "/_test/thing", principal="console:José")
    assert raw_principal(thing_engine.requests[0]) == b"console:Jos\xe9"
    assert raw_principal(thing_engine.requests[0]).decode("latin-1") == "console:José"


def test_non_latin1_principal_is_percent_encoded(thing_engine: FakeEngine) -> None:
    # Only a default ``console:<username>`` can be outside Latin-1 (#43's rule, kept for it).
    thing_engine.respond("POST", "/_test/thing", 200, json={"name": "x"})
    client(thing_engine)._request("POST", "/_test/thing", principal="console:Joŝ")
    assert raw_principal(thing_engine.requests[0]) == b"console:Jo%C5%9D"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
def test_mutating_call_without_principal_sends_nothing(
    thing_engine: FakeEngine, method: str
) -> None:
    with pytest.raises(ValueError, match="principal"):
        client(thing_engine)._request(method, "/_test/thing")
    assert thing_engine.requests == []


@pytest.mark.parametrize(
    ("principal", "why"),
    [
        ("", "blank"),
        ("   ", "blank"),
        ("x" * 257, "at most 256"),
        ("console:" + "é" * 249, "at most 256"),  # 257 Latin-1 characters (#64)
        # 200 characters, 1208 once encoded: the Engine checks what it receives (#108).
        ("console:" + "ŝ" * 200, "at most 256"),
        # 11 + 41 * 6 = 257 encoded, but 93 UTF-8 bytes and 52 characters (#117).
        ("console:aaa" + "ŝ" * 41, r"at most 256 characters once encoded \(257\)"),
        ("console:a\r\nX-Evil: 1", "control"),
        ("a\nb", "control"),
        ("a\x00b", "control"),
        ("a\tb", "control"),
        ("\tconsole:nate", "control"),
        ("a\x1bb", "control"),
        ("a\x7fb", "control"),
        ("console:\x80", "control"),  # C1: sent as Latin-1, the Engine refuses it (#64)
        ("a\x9fb", "control"),
        ("system", "reserved"),
        (" system ", "reserved"),
    ],
)
def test_bad_principal_refused_before_sending(
    thing_engine: FakeEngine, principal: str, why: str
) -> None:
    with pytest.raises(ValueError, match=f"principal.*{why}"):
        client(thing_engine)._request("POST", "/_test/thing", principal=principal)
    assert thing_engine.requests == []


@pytest.mark.parametrize(
    "principal",
    [
        "x" * 256,
        "console:" + "é" * 41,
        "console:" + "é" * 248,  # 256 Latin-1 characters (#64)
        "console:aa" + "ŝ" * 41,  # exactly 256 once encoded (#117)
        "console:" + "é" * 41 + "  ",
        "café",
        "console:\xa0\xff",
        "systems",
    ],
)
def test_principal_the_engine_accepts_is_sent(thing_engine: FakeEngine, principal: str) -> None:
    # A Latin-1 principal is sent as its Latin-1 bytes; "console:aa" plus 41 "ŝ" is 10 + 246 =
    # 256 characters percent-encoded.
    thing_engine.respond("POST", "/_test/thing", 200, json={"name": "x"})
    client(thing_engine)._request("POST", "/_test/thing", principal=principal)
    sent = raw_principal(thing_engine.requests[0]).decode("latin-1")
    assert 1 <= len(sent.strip(" ")) <= 256
    assert not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in sent)
    if principal == "console:aa" + "ŝ" * 41:
        assert sent == "console:aa" + "%C5%9D" * 41
        assert len(sent) == 256
    else:
        assert sent == principal


def test_error_envelope_raises_engine_error(thing_engine: FakeEngine) -> None:
    envelope = {"error": {"code": "not_found", "message": "No such thing", "details": {"id": 1}}}
    thing_engine.respond("GET", "/_test/thing", 404, json=envelope)
    response = client(thing_engine)._request("GET", "/_test/thing")
    with pytest.raises(EngineError) as caught:
        _raise_for_envelope(response)
    assert caught.value.code == "not_found"
    assert caught.value.message == "No such thing"
    assert caught.value.details == {"id": 1}
    assert caught.value.http_status == 404


def test_envelope_without_details_defaults_to_empty() -> None:
    # Off-schema (the Engine always sends details), so a raw response, not the fake.
    response = httpx.Response(409, json={"error": {"code": "conflict", "message": "m"}})
    with pytest.raises(EngineError) as caught:
        _raise_for_envelope(response)
    assert (caught.value.code, caught.value.details) == ("conflict", {})


@pytest.mark.parametrize(
    ("response", "code", "message"),
    [
        (
            httpx.Response(502, text="Bad gateway " + "x" * 300),
            "http_502",
            ("Bad gateway " + "x" * 300)[:200],
        ),
        (httpx.Response(500, json={"error": {"code": 5, "message": "m"}}), "http_500", None),
        (
            httpx.Response(500, json={"error": {"code": "c", "message": "m", "details": [1]}}),
            "c",
            "m",
        ),
        (httpx.Response(503), "http_503", ""),
    ],
)
def test_envelope_fallbacks(response: httpx.Response, code: str, message: str | None) -> None:
    with pytest.raises(EngineError) as caught:
        _raise_for_envelope(response)
    assert caught.value.code == code
    assert caught.value.details == {}
    if message is not None:
        assert caught.value.message == message


def test_success_does_not_raise() -> None:
    _raise_for_envelope(httpx.Response(204))


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (httpx.ConnectError("refused"), "connection failed"),
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ReadError("reset"), "transport error"),
    ],
)
def test_transport_errors_raise_unreachable(
    fake_engine: FakeEngine, exc: Exception, reason: str
) -> None:
    fake_engine.fail("GET", "/health", exc)
    with pytest.raises(EngineUnreachable) as caught:
        client(fake_engine).health()
    assert caught.value.reason == reason
    assert not caught.value.unexpected
    assert caught.value.__cause__ is exc


def test_unexpected_error_is_transport_error_with_cause() -> None:
    boom = RuntimeError("surprise")

    def fail(request: httpx.Request) -> httpx.Response:
        raise boom

    with pytest.raises(EngineUnreachable) as caught:
        EngineClient(URL, None, transport=httpx.MockTransport(fail)).health()
    assert caught.value.reason == "transport error"
    assert caught.value.unexpected
    assert caught.value.__cause__ is boom


def test_fake_engine_signals_are_not_swallowed(fake_engine: FakeEngine) -> None:
    with pytest.raises(AssertionError):
        client(fake_engine)._request("GET", "/nope")


def test_openapi_returns_document(fake_engine: FakeEngine) -> None:
    fake_engine.serve("GET", "/openapi.json", 200, json={"openapi": "3.1.0", "paths": {}})
    assert client(fake_engine).openapi() == {"openapi": "3.1.0", "paths": {}}
    assert "authorization" not in fake_engine.requests[0].headers


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (404, {"detail": "Not Found"}, "http_404"),
        (200, [1], "bad_response"),
        (200, None, "bad_response"),
    ],
)
def test_openapi_errors(fake_engine: FakeEngine, status: int, body: object, code: str) -> None:
    fake_engine.serve("GET", "/openapi.json", status, json=body)
    with pytest.raises(EngineError) as caught:
        client(fake_engine).openapi()
    assert caught.value.code == code
    assert caught.value.http_status == status


def test_operations_registered() -> None:
    assert OPERATIONS["GET /health"].response_model is HealthResponse
    assert OPERATIONS["GET /stories"].response_model is StoryList
    assert OPERATIONS["GET /openapi.json"].schema_exempt


def test_repr_hides_key() -> None:
    engine = EngineClient(URL, KEY)
    assert KEY not in repr(engine)
    assert "set" in repr(engine)


def test_from_settings(settings: Any) -> None:
    settings.ENGINE_URL = URL
    settings.ENGINE_SERVICE_KEY = KEY
    engine = EngineClient.from_settings()
    assert engine.base_url == URL
    settings.ENGINE_URL = None
    with pytest.raises(EngineNotConfigured):
        EngineClient.from_settings()


# --- #43: list_stories and the client's default principal --------------------------------------


def test_list_stories_parses_the_fixture(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories", 200, json=engine_fixture("list_stories"))
    stories = client(fake_engine).list_stories()
    assert [s.status for s in stories] == ["active", "blocked_by_erasure"]
    first, held = stories
    assert first.name == "The lighthouse"
    assert first.content_rating.name == "General"
    assert first.timeline_revision == 7
    assert held.name is None  # a held story lists with a null name
    request = fake_engine.requests[0]
    assert request.url.path == "/stories"
    assert request.url.query == b""
    assert request.headers["authorization"] == f"Bearer {KEY}"


def test_list_stories_sends_the_status_filter(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories", 200, json={"items": []})
    assert client(fake_engine).list_stories(status="archived") == []
    assert fake_engine.requests[0].url.params["status"] == "archived"


def test_list_stories_401_raises_engine_error(fake_engine: FakeEngine) -> None:
    envelope = {"error": {"code": "unauthenticated", "message": "no", "details": {}}}
    fake_engine.respond("GET", "/stories", 401, json=envelope)
    with pytest.raises(EngineError) as caught:
        client(fake_engine).list_stories()
    assert (caught.value.http_status, caught.value.code) == (401, "unauthenticated")


def test_list_stories_bad_body_is_bad_response() -> None:
    engine = EngineClient(
        URL, KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"x": 1}))
    )
    with pytest.raises(EngineError) as caught:
        engine.list_stories()
    assert (caught.value.code, caught.value.http_status) == ("bad_response", 200)


def test_default_principal_is_sent_and_a_per_call_one_wins(thing_engine: FakeEngine) -> None:
    thing_engine.respond("POST", "/_test/thing", 200, json={"name": "x"})
    engine = EngineClient(URL, KEY, transport=thing_engine.transport, principal="console:nate")
    engine._request("POST", "/_test/thing")
    engine._request("POST", "/_test/thing", principal="console:other")
    first, second = thing_engine.requests
    assert first.headers["x-epistrel-principal"] == "console:nate"
    assert second.headers["x-epistrel-principal"] == "console:other"


def test_bad_default_principal_is_refused_before_sending(thing_engine: FakeEngine) -> None:
    engine = EngineClient(URL, KEY, transport=thing_engine.transport, principal="system")
    with pytest.raises(ValueError, match="reserved"):
        engine._request("POST", "/_test/thing")
    assert thing_engine.requests == []


# --- #44: request bodies and headers, get_operation, the call context --------------------------


def test_request_sends_a_json_body_and_extra_headers(thing_engine: FakeEngine) -> None:
    thing_engine.respond("POST", "/_test/thing", 200, json={"name": "x"})
    client(thing_engine)._request(
        "POST",
        "/_test/thing",
        principal="console:nate",
        json={"to_turn": 2},
        headers={"Idempotency-Key": "k1", "Prefer": "respond-async"},
    )
    (sent,) = thing_engine.requests
    assert json.loads(sent.content) == {"to_turn": 2}
    assert sent.headers["idempotency-key"] == "k1"
    assert sent.headers["prefer"] == "respond-async"


def test_extra_headers_never_replace_the_clients_own(thing_engine: FakeEngine) -> None:
    thing_engine.respond("POST", "/_test/thing", 200, json={"name": "x"})
    client(thing_engine)._request(
        "POST",
        "/_test/thing",
        principal="console:nate",
        headers={
            "Authorization": "Bearer forged",
            "X-Epistrel-Principal": "console:mallory",
            "user-agent": "forged",
        },
    )
    (sent,) = thing_engine.requests
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert sent.headers["x-epistrel-principal"] == "console:nate"
    assert sent.headers["user-agent"].startswith("epistrel-console/")


def test_get_operation_parses_the_record(fake_engine: FakeEngine) -> None:
    fake_engine.respond(
        "GET", "/operations/{operation_id}", 200, json=engine_fixture("get_operation")
    )
    record = client(fake_engine).get_operation("01JA0000000000000000000OP3", viewpoint="omniscient")
    assert record.id == "01JA0000000000000000000OP3"
    assert record.status == "committed"
    assert record.principal == "console:nate"
    assert record.config_revisions == {"story": 4}
    assert record.result is not None
    assert record.result.status == 200
    assert record.result.body["timeline_revision"] == 7
    assert fake_engine.requests[0].url.params["viewpoint"] == "omniscient"


def test_get_operation_404_is_an_engine_error(fake_engine: FakeEngine) -> None:
    error = {"error": {"code": "not_found", "message": "no such operation", "details": {}}}
    fake_engine.respond("GET", "/operations/{operation_id}", 404, json=error)
    with pytest.raises(EngineError) as caught:
        client(fake_engine).get_operation("01JA0000000000000000000OP9")
    assert (caught.value.http_status, caught.value.code) == (404, "not_found")


def test_a_registered_method_names_its_call(fake_engine: FakeEngine) -> None:
    seen: list[Call | None] = []

    class Spy(EngineClient):
        def _request(self, *args: Any, **kwargs: Any) -> httpx.Response:
            seen.append(current_call())
            return super()._request(*args, **kwargs)

    fake_engine.respond("GET", "/stories", 200, json={"items": []})
    Spy(URL, KEY, transport=fake_engine.transport).list_stories()
    (call,) = seen
    assert call is not None
    assert call.kind == "list_stories"
    assert call.operation == OPERATIONS["GET /stories"]
    assert current_call() is None  # reset once the method returns


# --- M5 models (#183) -----------------------------------------------------------------------------

C1 = "01JB2Z8Y5Q3W6K9M1N4P7R0T3A"
C2 = "01JB2Z8Y5Q3W6K9M1N4P7R0T3B"
PROPOSITION: dict[str, Any] = {
    "subject_id": C2,
    "predicate_id": "01JB2Z8Y5Q3W6K9M1N4P7R0PW1",
    "object_id": "01JB2Z8Y5Q3W6K9M1N4P7R0PC1",
    "polarity": "affirmed",
    "valid_time": {"kind": "interval", "start": {"outer": 40, "inner": 40}, "end": None},
}


@pytest.mark.parametrize(
    "value",
    ["none", "co_location", "full", "nothing_perceived", "bypassed_by_player_view", "later_value"],
)
def test_history_parses_every_audience_filter(value: str) -> None:
    body = {**engine_fixture("get_history"), "audience_filter": value}
    history = History.from_json(body)
    assert history is not None
    assert history.audience_filter == value
    assert history.player_view == "character"


def _item() -> dict[str, Any]:
    item: dict[str, Any] = engine_fixture("get_history")["items"][0]
    return item


# M6 (#231): minimal valid nested bodies, reused below.
STATE_EFFECT: dict[str, Any] = {
    "state": "gold",
    "scope_key": C1,
    "reason": "spent",
    "source": "turn",
    "contingency": "none",
    "status": "committed",
}
NOTICE: dict[str, Any] = {
    "id": "01JM6NT0000000000000000001",
    "kind": "implausible_knowledge",
    "character_id": C1,
    "claim_text": "Mara knew the vault code.",
    "turn": 3,
    "correctable": True,
}


@pytest.mark.parametrize(
    ("model", "body", "name"),
    [
        (HistoryItem, _item(), "fidelity"),
        (
            OmniscientStoryCharacter,
            {"id": C1, "name": "Tobin", "is_player": False, "true_age": 41, "promoted_to": []},
            "weight",
        ),
        (Participation, {"role": "primary", "fixed": False}, "role"),
        (TurnRole, {"role": "primary", "source": "default"}, "source"),
        (LocationAdjacency, {"location": "hall"}, "barrier"),
        (Seed, {"text": "The ferry is late."}, "confidence"),
        (NestedSeed, {"attributed_holder": "mother", "proposition": "p"}, "attributed_confidence"),
        (StorySettingsDefaults, {}, "sequence_order"),
        (ReplyMessage, {"message_id": "01M5R0QNTZV2CSB478YYC57Y3V", "text": "Hi."}, "mode"),
        (DeferredCharacter, {"character_id": C1, "reason": "budget", "fixed": False}, "reason"),
        (StateEffect, STATE_EFFECT, "owner"),
        (Notice, NOTICE, "kind"),
        (StorySettingsDefaults, {}, "co_author_knowledge"),
    ],
)
def test_open_enums_keep_unknown_values(model: Any, body: dict[str, Any], name: str) -> None:
    parsed = model.from_json({**body, name: "later_value"})
    assert parsed is not None
    assert getattr(parsed, name) == "later_value"
    # A non-string still fails that model's parse.
    assert model.from_json({**body, name: 7}) is None


MULTI: dict[str, Any] = {
    "order": [C1, C2],
    "interaction_mode": "simultaneous",
    "roles": {
        C1: {"role": "primary", "source": "default"},
        C2: {"role": "reactive", "source": "escalated"},
    },
    "adjudications": [{"object": "the key", "winner": C1, "loser": C2, "rule": "request_order"}],
    "escalation_deferred": [{"character_id": C2, "reason": "budget", "needed": "primary"}],
    "deferred_characters": [{"character_id": C2, "reason": "budget", "fixed": False}],
    "reduced_characters": [C2],
    "reconcile": [{"proposition": "p1", "winner_beat": 1, "loser_beat": 2, "rule": "earlier_beat"}],
    "player_view": "story",
}


def _respond(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("respond")
    body.update(changes)
    return body


def test_respond_parses_the_multi_character_fields() -> None:
    body = _respond(**MULTI)
    body["message"] = {
        **body["message"],
        "narrative": "They both reach for it.",
        "mode": "polished",
    }
    turn = RespondResponse.from_json(body)
    assert turn is not None
    assert turn.order == [C1, C2]
    assert turn.interaction_mode == "simultaneous"
    assert turn.roles == {
        C1: TurnRole(role="primary", source="default"),
        C2: TurnRole(role="reactive", source="escalated"),
    }
    assert turn.adjudications == [Adjudication("the key", C1, C2, "request_order")]
    assert turn.escalation_deferred == [EscalationDeferred(C2, "budget", "primary")]
    assert turn.deferred_characters == [DeferredCharacter(C2, "budget", False)]
    assert turn.reduced_characters == [C2]
    assert turn.reconcile == [Reconcile("p1", 1, 2, "earlier_beat")]
    assert turn.message.narrative == "They both reach for it."
    assert turn.message.mode == "polished"
    assert turn.player_view == "story"
    assert turn.unparsed == {}


def test_respond_without_the_multi_character_fields_reads_defaults() -> None:
    body = _respond()
    for name in MULTI:
        body.pop(name, None)
    turn = RespondResponse.from_json(body)
    assert turn is not None
    assert (turn.order, turn.roles, turn.adjudications, turn.reconcile) == ([], {}, [], [])
    assert (turn.escalation_deferred, turn.deferred_characters, turn.reduced_characters) == (
        [],
        [],
        [],
    )
    assert turn.interaction_mode is None
    assert turn.player_view is None
    assert turn.message.narrative is None


def test_respond_keeps_an_unknown_role_source() -> None:
    turn = RespondResponse.from_json(
        _respond(roles={C1: {"role": "primary", "source": "conjured"}})
    )
    assert turn is not None
    assert turn.roles[C1].source == "conjured"


def test_respond_keeps_malformed_entries_raw() -> None:
    roles = {C1: {"role": "primary", "source": "default"}, C2: {"source": "default"}}
    adjudications = [
        {"object": "the key", "winner": C1, "loser": C2, "rule": "director"},
        {"object": "the cup", "loser": C2, "rule": "director"},
    ]
    turn = RespondResponse.from_json(_respond(roles=roles, adjudications=adjudications))
    assert turn is not None
    assert turn.roles == {C1: TurnRole(role="primary", source="default")}
    assert turn.adjudications == [Adjudication("the key", C1, C2, "director")]
    assert turn.unparsed == {"roles": roles, "adjudications": adjudications}


def test_respond_keeps_a_wrong_container_raw() -> None:
    turn = RespondResponse.from_json(_respond(roles=[], adjudications={}))
    assert turn is not None
    assert turn.roles == {}
    assert turn.adjudications == []
    assert turn.unparsed == {"roles": [], "adjudications": {}}


def test_regenerate_inherits_the_lenient_read() -> None:
    body = _respond(roles={C2: {"source": "default"}}, previous_variant=1)
    result = RegenerateResult.from_json(body)
    assert result is not None
    assert result.roles == {}
    assert result.unparsed == {"roles": {C2: {"source": "default"}}}


def _thunk(**changes: Any) -> dict[str, Any]:
    thunk: dict[str, Any] = {
        "id": "01JB2Z8Y5Q3W6K9M1N4P7R0TH1",
        "sketch": {},
        "description": "a stranger is at the harbour",
        "open_slot": "subject",
        "state": "open",
        "position": {"turn": 3, "variant": None, "beat": 2},
        "salient": True,
        "valid_time": 120,
        "resolved_fact_id": None,
    }
    thunk.update(changes)
    return thunk


def _hidden(**changes: Any) -> dict[str, Any]:
    fact: dict[str, Any] = {
        "fact_id": "01JB2Z8Y5Q3W6K9M1N4P7R0F01",
        "proposition": PROPOSITION,
        "scope": "characters",
        "characters": [C1],
        "valid_from": 40,
        "valid_to": None,
        "narrative_turn": 2,
    }
    fact.update(changes)
    return fact


def test_ledger_keeps_malformed_hidden_facts_and_flags_raw() -> None:
    good_flag = {"character_id": C1, "severity": "high", "gap_minutes": 30}
    flags = [good_flag, {"character_id": C2, "severity": "low", "gap_minutes": "x"}]
    bad_fact = {k: v for k, v in _hidden().items() if k != "fact_id"}
    hidden = [_hidden(), bad_fact]
    body = {
        "thunks": [_thunk(could_have_known_flags=flags), _thunk(id="01JB2Z8Y5Q3W6K9M1N4P7R0TH2")],
        "ledger": [],
        "revision": 4,
        "next_after_id": None,
        "hidden_facts": hidden,
        "output_viewpoint": "omniscient",
    }
    ledger = Ledger.from_json(body)
    assert ledger is not None
    assert [f.fact_id for f in ledger.hidden_facts] == ["01JB2Z8Y5Q3W6K9M1N4P7R0F01"]
    assert isinstance(ledger.hidden_facts[0].proposition_view, Proposition)
    assert ledger.unparsed == {"hidden_facts": hidden}
    first, second = ledger.thunks
    assert first.could_have_known_flags == [CouldHaveKnownFlag(C1, "high", 30)]
    assert first.unparsed == {"could_have_known_flags": flags}
    assert first.position == InspectPosition(turn=3, variant=None, beat=2)
    assert second.could_have_known_flags == []
    assert second.unparsed == {}
    assert ledger.output_viewpoint == "omniscient"


def test_a_hidden_fact_with_an_unparseable_proposition_stays_typed() -> None:
    body = {
        "thunks": [],
        "ledger": [],
        "revision": 4,
        "next_after_id": None,
        "hidden_facts": [_hidden(proposition={"subject_id": C1})],
    }
    ledger = Ledger.from_json(body)
    assert ledger is not None
    (fact,) = ledger.hidden_facts
    assert fact.proposition_view is None
    assert fact.proposition == {"subject_id": C1}
    assert ledger.unparsed == {}


def test_ledger_reads_a_wrong_hidden_facts_container_as_empty() -> None:
    body = {"thunks": [], "ledger": [], "revision": 4, "next_after_id": None, "hidden_facts": {}}
    ledger = Ledger.from_json(body)
    assert ledger is not None
    assert ledger.hidden_facts == []
    assert ledger.unparsed == {"hidden_facts": {}}


def test_creation_report_parses_belief_outcomes() -> None:
    entry = MaterializedEntry.from_json(
        {
            "field_ref": "cast.roles[0].seeded_memories[0]",
            "location": {"entry": "01JB2Z8Y5Q3W6K9M1N4P7R0SC1", "version": "draft"},
            "outcomes": ["belief", "seed_audience_unfilled"],
        }
    )
    assert entry is not None
    assert entry.outcomes == ["belief", "seed_audience_unfilled"]


LABELS: dict[str, Any] = {
    "intent": "deceptive",
    "belief_relation": "inconsistent",
    "objective_relation": "false",
}


def test_utterance_labels_read_classification() -> None:
    labels = UtteranceLabels.from_json(
        {**LABELS, "classification": "lie", "intent_corrected": True}
    )
    assert labels is not None
    assert (labels.classification, labels.intent_corrected, labels.extras) == ("lie", True, {})


@pytest.mark.parametrize(
    ("extra", "classification", "corrected", "extras"),
    [
        ({"classification": "nonsense"}, "nonsense", False, {}),
        ({"classification": None}, None, False, {}),
        ({}, None, False, {}),
        ({"classification": 3}, None, False, {"classification": 3}),
        ({"intent_corrected": "yes"}, None, False, {"intent_corrected": "yes"}),
        ({"later_label": "x"}, None, False, {"later_label": "x"}),
    ],
)
def test_utterance_labels_keep_odd_values(
    extra: dict[str, Any], classification: str | None, corrected: bool, extras: dict[str, Any]
) -> None:
    labels = UtteranceLabels.from_json({**LABELS, **extra})
    assert labels is not None
    assert (labels.classification, labels.intent_corrected, labels.extras) == (
        classification,
        corrected,
        extras,
    )


# --- M6 models (#231) -----------------------------------------------------------------------------

OP_ID = "01JM6QP0000000000000000001"


def test_operation_with_a_staged_label_parses() -> None:
    body = {
        **engine_fixture("get_operation"),
        "label": "staged_turn",
        "staged": True,
        "stages": [{"name": "outcome", "status": "done", "at": None, "revision": 12}],
    }
    record = OperationRecord.from_json(body)
    assert record is not None
    assert (record.label, record.staged) == ("staged_turn", True)
    assert record.stages[0].revision == 12
    # Complement: a label the Console doesn't know yet is kept as its string (C2).
    later = OperationRecord.from_json({**body, "label": "later_label"})
    assert later is not None
    assert later.label == "later_label"
    # An operation without the M6 fields reads the defaults.
    plain = OperationRecord.from_json(engine_fixture("get_operation"))
    assert plain is not None
    assert plain.staged is False


def test_creation_report_parses_m6_outcomes() -> None:
    entry = MaterializedEntry.from_json(
        {
            "field_ref": "starting_state.states[0]",
            "location": {"entry": "01JB2Z8Y5Q3W6K9M1N4P7R0SC1", "version": "draft"},
            "outcomes": ["state", "clock", "event"],
        }
    )
    assert entry is not None
    assert entry.outcomes == ["state", "clock", "event"]


COMMITTED_SET: dict[str, Any] = {
    **STATE_EFFECT,
    "kind": "set",
    "old_value": 10,
    "new_value": 4,
}
PENDING_EXTERNAL: dict[str, Any] = {
    **STATE_EFFECT,
    "state": "balance",
    "status": "pending",
    "owner": "external",
    "effect_id": "01JM6EF0000000000000000001",
    "old_value": 120,
    "new_value": None,
}
REQUIRED: dict[str, Any] = {
    "outbox_id": "01JM6OB0000000000000000001",
    "provider": "bank",
    "status": "dispatched",
    "value": "120",
}
CLOCK: dict[str, Any] = {
    "before": {"offset": 360, "display": "06:00"},
    "after": {"offset": 375, "display": "06:15"},
}
M6_TURN_FIELDS = (
    "state_effects",
    "settled_effects",
    "notices",
    "reconciliation_required",
    "clock",
    "reconciliation",
)


def test_respond_parses_the_m6_turn_fields() -> None:
    body = _respond(
        state_effects=[COMMITTED_SET, PENDING_EXTERNAL],
        settled_effects=[COMMITTED_SET],
        clock=CLOCK,
        reconciliation={"count": 2},
        notices=[NOTICE],
        reconciliation_required=[REQUIRED],
    )
    turn = RespondResponse.from_json(body)
    assert turn is not None
    first, pending = turn.state_effects
    assert (first.state, first.old_value, first.new_value, first.kind) == ("gold", 10, 4, "set")
    assert (pending.status, pending.owner) == ("pending", "external")
    assert pending.effect_id == "01JM6EF0000000000000000001"
    assert pending.new_value is None
    assert turn.settled_effects == [first]
    assert turn.clock == TurnClock(ClockValue(360, "06:00"), ClockValue(375, "06:15"))
    assert turn.reconciliation == ReconciliationCount(2)
    assert turn.notices == [
        Notice(
            "01JM6NT0000000000000000001",
            "implausible_knowledge",
            C1,
            "Mara knew the vault code.",
            3,
            True,
        )
    ]
    assert turn.notices[0].external_effects is False
    assert turn.reconciliation_required == [
        RequiredEffect("01JM6OB0000000000000000001", "bank", "dispatched", "120")
    ]
    assert turn.unparsed == {}


def test_respond_without_the_m6_turn_fields_reads_defaults() -> None:
    body = _respond()
    for name in M6_TURN_FIELDS:
        body.pop(name, None)
    turn = RespondResponse.from_json(body)
    assert turn is not None
    assert (turn.state_effects, turn.settled_effects, turn.notices) == ([], [], [])
    assert turn.reconciliation_required == []
    assert (turn.clock, turn.reconciliation) == (None, None)


def test_respond_keeps_malformed_m6_entries_raw() -> None:
    no_status = {k: v for k, v in STATE_EFFECT.items() if k != "status"}
    no_claim = {k: v for k, v in NOTICE.items() if k != "claim_text"}
    effects = [COMMITTED_SET, no_status]
    notices = [no_claim, NOTICE]
    turn = RespondResponse.from_json(_respond(state_effects=effects, notices=notices))
    assert turn is not None
    assert [e.state for e in turn.state_effects] == ["gold"]
    assert [n.id for n in turn.notices] == [NOTICE["id"]]
    assert turn.unparsed == {"state_effects": effects, "notices": notices}
    # A wrong container reads as the default, with the raw value in ``unparsed``.
    odd = RespondResponse.from_json(_respond(state_effects={}))
    assert odd is not None
    assert odd.state_effects == []
    assert odd.unparsed == {"state_effects": {}}


def test_regenerate_inherits_the_lenient_m6_lists() -> None:
    bad = {"outbox_id": "01JM6OB0000000000000000002"}
    result = RegenerateResult.from_json(
        _respond(reconciliation_required=[REQUIRED, bad], previous_variant=1)
    )
    assert result is not None
    assert [r.provider for r in result.reconciliation_required] == ["bank"]
    assert result.unparsed == {"reconciliation_required": [REQUIRED, bad]}


def test_a_malformed_turn_clock_fails_the_answer() -> None:
    """Single-object turn fields stay strict (D15 makes only lists lenient)."""
    assert RespondResponse.from_json(_respond(clock={"before": {"offset": 1}})) is None
    assert RespondResponse.from_json(_respond(reconciliation={"count": "two"})) is None


def _history_with_turns(*turns: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("get_history")
    body["turns"] = list(turns)
    return body


def test_history_keeps_a_turns_malformed_effect_raw() -> None:
    bad = {"state": "gold"}
    second_effects = [COMMITTED_SET, bad]
    body = _history_with_turns(
        {"turn": 1, "variant": 1, "message": None, "state_effects": [COMMITTED_SET]},
        {
            "turn": 2,
            "variant": 1,
            "message": None,
            "incomplete": True,
            "state_effects": second_effects,
        },
    )
    history = History.from_json(body)
    assert history is not None
    first, second = history.turns
    assert [e.state for e in second.state_effects] == ["gold"]
    assert second.unparsed == {"state_effects": second_effects}
    assert first.unparsed == {}
    assert len(first.state_effects) == 1
    # The raw body the caller passed is left as it was.
    assert body["turns"][1]["state_effects"] == second_effects
    # Complement: ``incomplete`` reads ``True``, and an absent one ``False``.
    assert (second.incomplete, first.incomplete) == (True, False)


def test_history_keeps_a_turns_malformed_notice_raw() -> None:
    notices = [NOTICE, {"id": "x"}]
    history = History.from_json(
        _history_with_turns({"turn": 1, "variant": 1, "message": None, "notices": notices})
    )
    assert history is not None
    (turn,) = history.turns
    assert [n.id for n in turn.notices] == [NOTICE["id"]]
    assert turn.unparsed == {"notices": notices}


def test_a_non_bool_incomplete_fails_the_history_page() -> None:
    body = _history_with_turns({"turn": 1, "variant": 1, "message": None, "incomplete": "yes"})
    assert History.from_json(body) is None


def test_rejected_entries_only_the_bad_ones() -> None:
    bad = {"state": "gold"}
    raw = [COMMITTED_SET, bad, PENDING_EXTERNAL]
    assert rejected_entries(raw, StateEffect.from_json) == [bad]
    # Complement: a non-list raw value is one rejected entry; a clean list rejects nothing.
    assert rejected_entries({}, StateEffect.from_json) == [{}]
    assert rejected_entries([COMMITTED_SET, PENDING_EXTERNAL], StateEffect.from_json) == []


def test_beat_effects_are_typed_beside_the_raw() -> None:
    effects = [COMMITTED_SET, {"state": "gold"}]
    item = HistoryItem.from_json({**_item(), "effects": effects})
    assert item is not None
    assert [e.state for e in item.effect_entries] == ["gold"]
    assert item.effects_unparsed == [{"state": "gold"}]
    assert item.effects == effects
    # ``None`` has no entries either way; an odd value reaches More as one rejected entry.
    none = HistoryItem.from_json({**_item(), "effects": None})
    assert none is not None
    assert (none.effect_entries, none.effects_unparsed) == ([], [])
    odd = HistoryItem.from_json({**_item(), "effects": {"gold": 4}})
    assert odd is not None
    assert (odd.effect_entries, odd.effects_unparsed) == ([], [{"gold": 4}])


@pytest.mark.parametrize(
    ("model", "fixture", "name"),
    [
        (TruncateResult, "truncate", "reconciliation_required"),
        (ForkResult, "fork", "effects_pending_in_source"),
    ],
)
def test_timeline_answers_need_their_effect_lists(model: Any, fixture: str, name: str) -> None:
    body: dict[str, Any] = engine_fixture(fixture)
    assert model.from_json(body) is not None
    assert model.from_json({k: v for k, v in body.items() if k != name}) is None


def test_timeline_answers_parse_their_effect_entries() -> None:
    pending = {"outbox_id": "01JM6OB0000000000000000003", "status": "intended", "effect": {}}
    fork = ForkResult.from_json({**engine_fixture("fork"), "effects_pending_in_source": [pending]})
    assert fork is not None
    assert fork.effects_pending_in_source[0].status == "intended"
    truncated = TruncateResult.from_json(
        {**engine_fixture("truncate"), "reconciliation_required": [REQUIRED]}
    )
    assert truncated is not None
    assert truncated.reconciliation_required[0].outbox_id == REQUIRED["outbox_id"]
    # Write answers stay strict (D15): one bad entry fails the answer.
    assert (
        TruncateResult.from_json({**engine_fixture("truncate"), "reconciliation_required": [{}]})
        is None
    )


def test_scenario_states_stay_raw() -> None:
    state = {
        "name": "weather",
        "type": "enum",
        "constraints": {"allowed_values": ["rain", "sun"]},
        "initial_value": "rain",
        "visibility": "player_visible",
    }
    clock = {"anchor": {"time": "06:00", "day": 1}, "mode": "paced"}
    event = {"proposition": "The market opens.", "planned": "09:00", "place": "square"}
    starting = StartingState.from_json(
        {"states": [state], "clock": clock, "scheduled_events": [event]}
    )
    assert starting is not None
    assert starting.states[0] == state
    assert starting.clock == clock
    assert starting.scheduled_events[0] == event


# --- #291: the M7 recording's changed models --------------------------------------------------

STORY_ID = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
GAP = {"capability": "rolling_summary", "from_turn": 1, "to_turn": 4}
BAD_GAP = {"capability": "rolling_summary", "from_turn": "one", "to_turn": 4}


def test_an_importing_story_parses(fake_engine: FakeEngine) -> None:
    """M7's ``importing`` status: a ``Story`` and a ``StoryList`` item read it (open enums).
    **Complement:** a status the Console doesn't know yet parses as its string, and a
    non-string fails the item."""
    listed = engine_fixture("list_stories")
    listed["items"][0]["status"] = "importing"
    story = engine_fixture("get_story")
    story["status"] = "importing"
    fake_engine.respond("GET", "/stories", 200, json=listed)
    fake_engine.respond("GET", "/stories/{story_id}", 200, json=story)
    engine = client(fake_engine)
    assert engine.list_stories()[0].status == "importing"
    assert engine.get_story(story["id"]).status == "importing"
    assert Story.from_json({**story, "status": "later_status"}) is not None
    assert StoryList.from_json({"items": [{**listed["items"][0], "status": "later_status"}]})
    assert Story.from_json({**story, "status": 7}) is None


def test_a_pruned_chain_parses(fake_engine: FakeEngine) -> None:
    body = engine_fixture("list_variants")
    body["items"][1]["membership"] = "pruned"
    fake_engine.respond("GET", "/stories/{story_id}/variants", 200, json=body)
    chains = client(fake_engine).list_variants(STORY_ID)
    assert [c.membership for c in chains] == ["active", "pruned"]
    assert Variants.from_json(body) is not None


FEEDBACK_BODY: dict[str, Any] = {
    "story_id": STORY_ID,
    "message_id": "01JB2Z8Y5Q3W6K9M1N4P7R0MS1",
    "turn": 3,
    "variant": 1,
    "principal": "console:nate",
    "rating": 4,
    "tags": [],
    "text": None,
    "pruned_at": "2026-11-01T09:00:00Z",
    "created_at": "2026-10-31T09:00:00Z",
    "updated_at": "2026-10-31T09:00:00Z",
    "context": None,
    "history": [],
}


def test_feedback_reads_pruned_at(fake_engine: FakeEngine) -> None:
    """**Complement:** an M6 body (``compacted_at``, no ``pruned_at``) misses a required member."""
    fake_engine.respond(
        "GET", "/stories/{story_id}/messages/{message_id}/feedback", 200, json=FEEDBACK_BODY
    )
    feedback = client(fake_engine).get_message_feedback(STORY_ID, FEEDBACK_BODY["message_id"])
    assert feedback.pruned_at == "2026-11-01T09:00:00Z"
    old = {k: v for k, v in FEEDBACK_BODY.items() if k != "pruned_at"}
    assert MessageFeedback.from_json({**old, "compacted_at": None}) is None


def test_a_request_keyed_listing_parses(fake_engine: FakeEngine) -> None:
    """Engine #661: a consumer-applied effect, keyed by its request id, has no outbox id.
    **Complement:** a redacted outbox row still parses."""
    keyed = {
        "outbox_id": None,
        "request_id": "req-7",
        "provider": "bank",
        "status": "unknown",
        "reason": "imported_unsettled",
        "since": "2026-10-31T18:05:00Z",
    }
    redacted = engine_fixture("m6/reconciliation")["listings"][3]
    fake_engine.respond(
        "GET", "/stories/{story_id}/reconciliation", 200, json={"listings": [keyed, redacted]}
    )
    first, second = client(fake_engine).get_reconciliation(STORY_ID).listings
    assert (first.outbox_id, first.request_id, first.reason) == (
        None,
        "req-7",
        "imported_unsettled",
    )
    assert (second.outbox_id, second.request_id, second.value) == (
        redacted["outbox_id"],
        None,
        None,
    )


def _retrieval(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "inspect" / "retrieval.json").read_text()
    )
    body.update(changes)
    return body


def test_retrieval_coverage_gaps_are_lenient(fake_engine: FakeEngine) -> None:
    """One good and one malformed gap: the good one typed, the raw list kept; a non-list reads as
    ``[]`` with the raw value kept. **Complement:** the read never fails on a bad gap."""
    fake_engine.respond(
        "GET", "/stories/{story_id}/retrieve", 200, json=_retrieval(coverage_gaps=[GAP])
    )
    read = client(fake_engine).retrieve(STORY_ID, q="lantern")
    assert read.coverage_gaps == [CoverageGap("rolling_summary", 1, 4)]
    mixed = RetrievalBody.from_json(_retrieval(coverage_gaps=[GAP, BAD_GAP]))
    assert mixed is not None
    assert mixed.coverage_gaps == [CoverageGap("rolling_summary", 1, 4)]
    assert mixed.unparsed == {"coverage_gaps": [GAP, BAD_GAP]}
    odd = RetrievalBody.from_json(_retrieval(coverage_gaps={"capability": "x"}))
    assert odd is not None
    assert odd.coverage_gaps == []
    assert odd.unparsed == {"coverage_gaps": {"capability": "x"}}
    assert RetrievalBody.from_json(_retrieval(coverage_gaps=[BAD_GAP])) is not None


def test_turn_coverage_gaps_are_lenient() -> None:
    """**Complement:** an absent field reads as ``[]``."""
    turn = RespondResponse.from_json(_respond(coverage_gaps=[GAP, BAD_GAP]))
    assert turn is not None
    assert turn.coverage_gaps == [CoverageGap("rolling_summary", 1, 4)]
    assert turn.unparsed == {"coverage_gaps": [GAP, BAD_GAP]}
    regenerated = RegenerateResult.from_json(_respond(coverage_gaps=[BAD_GAP], previous_variant=1))
    assert regenerated is not None
    assert regenerated.coverage_gaps == []
    plain = _respond()
    plain.pop("coverage_gaps", None)
    absent = RespondResponse.from_json(plain)
    assert absent is not None
    assert absent.coverage_gaps == []


def test_quality_status_carries_its_certification() -> None:
    """**Complement:** the M6 body ``{status: "uncertified"}`` still parses, every new field
    ``None``."""
    certified = QualityStatus.from_json(
        {
            "status": "certified",
            "certification_id": "01JM7CE0000000000000000001",
            "floors_passed": ["coherence"],
            "invalidated_reason": None,
        }
    )
    assert certified is not None
    assert certified.certification_id == "01JM7CE0000000000000000001"
    assert certified.floors_passed == ["coherence"]
    assert certified.invalidated_reason is None
    stale = QualityStatus.from_json({"status": "stale", "invalidated_reason": "canary"})
    assert stale is not None
    assert stale.invalidated_reason == "canary"
    m6 = QualityStatus.from_json({"status": "uncertified"})
    assert m6 == QualityStatus("uncertified")
    assert m6.certification_id is None
    assert m6.failed_floors is None
    assert m6.intervention_types_covered is None
    assert m6.intervention_types_insufficient is None


def test_settings_parse_the_m7_settings(fake_engine: FakeEngine) -> None:
    """**Complement:** a body without the 23 settings reads the schema's defaults."""
    body = {**engine_fixture("get_deployment_settings"), "model_tiers": {"strongest": "gpt-x"}}
    body["rewrite_stale_retries"] = 3
    fake_engine.respond("GET", "/admin/settings", 200, json=body)
    settings = client(fake_engine).get_deployment_settings()
    assert settings.model_tiers == ModelTiers("gpt-x")
    assert settings.rewrite_stale_retries == 3
    bare = DeploymentSettings.from_json(engine_fixture("get_deployment_settings"))
    assert bare is not None
    assert bare.model_tiers == ModelTiers(None)
    assert (bare.bundle_export_ttl_hours, bare.bundle_max_uncompressed_bytes) == (24, 4294967296)
    assert (bare.certification_canary_enabled, bare.certification_recertify_interval_s) == (
        False,
        None,
    )
    assert (bare.rewrite_ambiguity_margin, bare.lite_reach_parallelism) == (0.15, 4)


def acting(fake: FakeEngine) -> EngineClient:
    """A client with a requesting principal (the writes need one)."""
    return EngineClient(URL, KEY, transport=fake.transport, principal="console:nate")


DIRECTED_PENDING: dict[str, Any] = {
    "timeline_revision": 8,
    "previous_variant": {"turn": 3, "variant": 1},
    "rewrite_session": {
        "id": "01JM7RS0000000000000000001",
        "story_id": STORY_ID,
        "kind": "directed_regenerate",
        "state": "open",
        "instruction": "Make it rain",
        "resolution_mode": "synchronous",
        "preview_mode": "preview_first",
        "pipeline": "structured",
        "principal": "console:nate",
        "created_at": "2026-11-06T10:00:00Z",
        "updated_at": "2026-11-06T10:00:00Z",
        "expires_at": "2026-11-07T10:00:00Z",
    },
    "disposition": "awaiting_rewrite",
}


def test_regenerate_202_is_bad_response_until_s2(fake_engine: FakeEngine) -> None:
    """M7's 202 (a directed regenerate's ``DirectedPendingBody``) is registered but not yet
    returned: ``regenerate`` still parses ``RegenerateResult`` (#292 widens it).
    **Complement:** a 200 still parses."""
    regenerate = "/stories/{story_id}/regenerate"
    fake_engine.respond("POST", regenerate, 202, json=DIRECTED_PENDING)
    assert DirectedPending.from_json(DIRECTED_PENDING) is not None
    engine = acting(fake_engine)
    with pytest.raises(EngineClientError) as caught:
        engine.regenerate(STORY_ID, {"turn": 3}, expected_revision=None, idempotency_key="k")
    assert isinstance(caught.value, EngineError)
    assert caught.value.code == "bad_response"
    fake_engine.respond("POST", regenerate, 200, json=engine_fixture("regenerate"))
    result = engine.regenerate(STORY_ID, {"turn": 3}, expected_revision=None, idempotency_key="k2")
    assert isinstance(result, RegenerateResult)


def test_profile_writes_parse_their_202(fake_engine: FakeEngine) -> None:
    """A write that started a backfill answers 202 with the same body and its ``backfill``."""
    from tests.fakes.story_config import effective_config
    from tests.profile_helpers import edit_body, impact

    backfill = {"id": "01JM7BF0000000000000000001", "job_id": "01JM7JB0000000000000000001"}
    body = {
        **edit_body(True, effective_config(story_id=STORY_ID), impact()),
        "timeline_revision": 5,
        "backfill": {**backfill, "status": "queued"},
    }
    fake_engine.respond("PUT", "/stories/{story_id}/engine-profile", 202, json=body)
    fake_engine.respond("PATCH", "/stories/{story_id}/profile-overrides", 202, json=body)
    engine = acting(fake_engine)
    assigned = engine.assign_engine_profile(
        STORY_ID, "00000000000000000000PRF001", ignore_warnings=False, idempotency_key="a"
    )
    patched = engine.patch_profile_overrides(
        STORY_ID, {}, expected_revision=None, ignore_warnings=False, idempotency_key="b"
    )
    for written in (assigned, patched):
        assert isinstance(written, StoryProfileWrite)
        assert written.backfill == BackfillStarted(backfill["id"], backfill["job_id"], "queued")

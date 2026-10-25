import json
from typing import Any

import httpx
import pytest

from console.engine.client import (
    OPERATIONS,
    Call,
    EngineClient,
    EngineError,
    EngineNotConfigured,
    EngineUnreachable,
    _raise_for_envelope,
    current_call,
)
from console.engine.models import HealthChecks, HealthResponse, StoryList, WorkersCheck
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


def test_non_ascii_principal_is_percent_encoded(thing_engine: FakeEngine) -> None:
    thing_engine.respond("POST", "/_test/thing", 200, json={"name": "x"})
    client(thing_engine)._request("POST", "/_test/thing", principal="console:José")
    assert thing_engine.requests[0].headers["x-epistrel-principal"] == "console:Jos%C3%A9"


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
        # 200 characters, 1208 once encoded: the Engine checks what it receives (#108).
        ("console:" + "é" * 200, "at most 256"),
        # 11 + 41 * 6 = 257 encoded, but 93 UTF-8 bytes and 52 characters (#117).
        ("console:aaa" + "é" * 41, r"at most 256 characters once encoded \(257\)"),
        ("console:a\r\nX-Evil: 1", "control"),
        ("a\nb", "control"),
        ("a\x00b", "control"),
        ("a\tb", "control"),
        ("\tconsole:nate", "control"),
        ("a\x1bb", "control"),
        ("a\x7fb", "control"),
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
        "console:aa" + "é" * 41,  # exactly 256 once encoded (#117)
        "console:" + "é" * 41 + "  ",
        "console:\x80",
        "systems",
    ],
)
def test_principal_the_engine_accepts_is_sent(thing_engine: FakeEngine, principal: str) -> None:
    # "console:" plus 41 "é" is 8 + 246 = 254 characters encoded; C1 controls go percent-encoded.
    thing_engine.respond("POST", "/_test/thing", 200, json={"name": "x"})
    client(thing_engine)._request("POST", "/_test/thing", principal=principal)
    sent = thing_engine.requests[0].headers["x-epistrel-principal"]
    assert 1 <= len(sent.strip(" ")) <= 256
    assert not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in sent)
    if principal == "console:aa" + "é" * 41:
        assert sent == "console:aa" + "%C3%A9" * 41
        assert len(sent) == 256


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

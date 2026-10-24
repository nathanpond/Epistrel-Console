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


def test_fail_raises_transport_error(fake_engine: FakeEngine) -> None:
    fake_engine.fail("GET", "/health", httpx.ConnectError("refused"))
    with pytest.raises(httpx.ConnectError):
        get(fake_engine, "/health")

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

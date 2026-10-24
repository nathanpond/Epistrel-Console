import tomllib
from pathlib import Path

import pytest
from django.test import Client, override_settings

from console import views
from console.engine_api_check import ApiCheck
from console.engine_probe import ProbeResult

RESULTS = {
    "healthy": ProbeResult("healthy", True, 200, "ok", {"database": "ok"}, None, 12),
    "unhealthy": ProbeResult(
        "unhealthy", True, 503, "unavailable", {"database": "error"}, "database error", 15
    ),
    "unreachable": ProbeResult("unreachable", False, None, None, None, "connection failed", None),
}
MATCHES = ApiCheck("matches", None, None, "m3")
PYPROJECT_VERSION = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
)["project"]["version"]


@pytest.fixture(params=list(RESULTS))
def state(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    name: str = request.param
    monkeypatch.setattr(views, "engine_state", lambda: (RESULTS[name], MATCHES))
    return name


@override_settings(ENGINE_URL="http://engine.test:8000")
def test_health_is_200_whatever_the_engine_state(client: Client, state: str) -> None:
    response = client.get("/health")
    # Complement: an unreachable Engine must not make the Console's own health fail.
    assert response.status_code == 200
    assert "no-cache" in response["Cache-Control"]
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == PYPROJECT_VERSION
    assert body["engine"]["state"] == state
    assert body["engine"]["url"] == "http://engine.test:8000"
    assert set(body["engine"]) == {
        "url",
        "state",
        "reachable",
        "http_status",
        "status",
        "checks",
        "reason",
        "latency_ms",
        "api",
    }


def test_health_head_works(client: Client, state: str) -> None:
    assert client.head("/health").status_code == 200


def test_health_refuses_post(client: Client, state: str) -> None:
    assert client.post("/health").status_code == 405


@override_settings(ENGINE_URL="http://engine.test:8000")
def test_status_page_shows_state_and_reason(client: Client, state: str) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "no-cache" in response["Cache-Control"]
    html = response.content.decode()
    assert views.STATE_WORDS[state] in html
    if RESULTS[state].reason:
        assert str(RESULTS[state].reason) in html
    assert "http://engine.test:8000" in html
    assert PYPROJECT_VERSION in html


def test_status_page_lists_checks_and_latency(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "engine_state", lambda: (RESULTS["healthy"], MATCHES))
    html = client.get("/").content.decode()
    assert "database: ok" in html
    assert "12 ms" in html
    assert 'href="/health"' in html
    assert 'href="/admin/"' in html


@override_settings(ENGINE_SERVICE_KEY="sekrit-123")
def test_service_key_never_shown(client: Client, state: str) -> None:
    page = client.get("/").content.decode()
    health = client.get("/health").content.decode()
    assert "Service key: set" in page
    assert "sekrit-123" not in page
    assert "sekrit-123" not in health


@override_settings(ENGINE_SERVICE_KEY=None)
def test_service_key_not_set(client: Client, state: str) -> None:
    assert "Service key: not set" in client.get("/").content.decode()


@override_settings(ENGINE_URL=None)
def test_unconfigured_url_shown(client: Client, state: str) -> None:
    assert "not configured" in client.get("/").content.decode()


def test_status_page_renders_nested_checks_readably(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The shape Engine main returns: an object, numbers and null among the string checks (#96).
    checks = {
        "database": "ok",
        "workers": {"alive": 2, "last_heartbeat_s": 2.2},
        "oldest_overdue_timer_s": None,
        "erasure_pending": 0,
    }
    result = ProbeResult("healthy", True, 200, "ok", checks, None, 9)
    monkeypatch.setattr(views, "engine_state", lambda: (result, MATCHES))
    html = client.get("/").content.decode()
    assert "database: ok" in html
    assert "workers: alive 2, last heartbeat 2.2 s" in html
    assert "oldest_overdue_timer_s: —" in html
    assert "erasure_pending: 0" in html
    # Complement: no Python reprs.
    for repr_text in ("{", "&#x27;", "None"):
        assert repr_text not in html.split("<dt>Checks</dt>")[1].split("</ul>")[0]


API_CHECKS = {
    "matches": ApiCheck("matches", None, None, "m3"),
    "differs": ApiCheck(
        "differs",
        "1 operation added, 1 component changed",
        None,
        "m4",
        ("added: POST /stories", "changed: schemas/HealthResponse"),
    ),
    "unknown": ApiCheck("unknown", None, "connection failed", None),
}
API_LINES = {
    "matches": "API: matches",
    "differs": "API: differs — 1 operation added, 1 component changed",
    "unknown": "API: unknown — connection failed",
}


@pytest.fixture(params=list(API_CHECKS))
def verdict(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    name: str = request.param
    monkeypatch.setattr(views, "engine_state", lambda: (RESULTS["healthy"], API_CHECKS[name]))
    return name


def test_status_page_shows_the_api_line(client: Client, verdict: str) -> None:
    html = " ".join(client.get("/").content.decode().split())
    assert API_LINES[verdict] in html
    version = API_CHECKS[verdict].engine_version
    if version:
        assert f"Engine version: {version}" in html
    else:
        assert "Engine version:" not in html
    if verdict == "differs":
        assert "<li>added: POST /stories</li>" in html
        assert "<li>changed: schemas/HealthResponse</li>" in html


def test_health_carries_the_api_verdict_and_stays_200(client: Client, verdict: str) -> None:
    response = client.get("/health")
    # Complement: a differing or unknown API never turns /health non-200.
    assert response.status_code == 200
    assert response.json()["engine"]["api"] == API_CHECKS[verdict].as_json()
    assert set(response.json()["engine"]["api"]) == {
        "verdict",
        "summary",
        "reason",
        "engine_version",
    }


def test_change_list_is_capped(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    changes = tuple(f"added: GET /p{i:02d}" for i in range(23))
    api = ApiCheck("differs", "23 operations added", None, "m4", changes)
    monkeypatch.setattr(views, "engine_state", lambda: (RESULTS["healthy"], api))
    html = client.get("/").content.decode()
    assert "<li>added: GET /p19</li>" in html
    assert "added: GET /p20" not in html
    assert "<li>…and 3 more</li>" in html


def test_version_shown_even_when_unreachable(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = ApiCheck("matches", None, None, "m3")
    monkeypatch.setattr(views, "engine_state", lambda: (RESULTS["unreachable"], api))
    assert "Engine version: m3" in client.get("/").content.decode()

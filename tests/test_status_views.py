import tomllib
from pathlib import Path

import pytest
from django.test import Client, override_settings

from console import views
from console.engine_probe import ProbeResult

RESULTS = {
    "healthy": ProbeResult("healthy", True, 200, "ok", {"database": "ok"}, None, 12),
    "unhealthy": ProbeResult(
        "unhealthy", True, 503, "unavailable", {"database": "error"}, "database error", 15
    ),
    "unreachable": ProbeResult("unreachable", False, None, None, None, "connection failed", None),
}
PYPROJECT_VERSION = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
)["project"]["version"]


@pytest.fixture(params=list(RESULTS))
def state(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    name: str = request.param
    monkeypatch.setattr(views, "probe", lambda: RESULTS[name])
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
    monkeypatch.setattr(views, "probe", lambda: RESULTS["healthy"])
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
    monkeypatch.setattr(views, "probe", lambda: result)
    html = client.get("/").content.decode()
    assert "database: ok" in html
    assert "workers: alive 2, last heartbeat 2.2 s" in html
    assert "oldest_overdue_timer_s: —" in html
    assert "erasure_pending: 0" in html
    # Complement: no Python reprs.
    for repr_text in ("{", "&#x27;", "None"):
        assert repr_text not in html.split("<dt>Checks</dt>")[1].split("</ul>")[0]

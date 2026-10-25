import tomllib
from pathlib import Path

import pytest
from django.test import Client, override_settings

from console.engine_api_check import ApiCheck
from console.engine_probe import ProbeResult
from console.engine_status import KeyCheck
from console.views import status as views

RESULTS = {
    "healthy": ProbeResult("healthy", True, 200, "ok", {"database": "ok"}, None, 12),
    "unhealthy": ProbeResult(
        "unhealthy", True, 503, "unavailable", {"database": "error"}, "database error", 15
    ),
    "unreachable": ProbeResult("unreachable", False, None, None, None, "connection failed", None),
}
MATCHES = ApiCheck("matches", None, None, "m3")
ACCEPTED = KeyCheck("accepted", "accepted")
PYPROJECT_VERSION = tomllib.loads(
    (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
)["project"]["version"]


@pytest.fixture(params=list(RESULTS))
def state(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    name: str = request.param
    use(monkeypatch, RESULTS[name], MATCHES)
    return name


def use(
    monkeypatch: pytest.MonkeyPatch,
    result: ProbeResult,
    api: ApiCheck,
    key: KeyCheck = ACCEPTED,
) -> None:
    """``/health`` sees ``(result, api)``; ``/`` sees those and the key check."""
    monkeypatch.setattr(views, "engine_state", lambda: (result, api))
    monkeypatch.setattr(views, "status_state", lambda: (result, api, key))


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
        "content_logging",
        "api",
    }


def test_health_head_works(client: Client, state: str) -> None:
    assert client.head("/health").status_code == 200


def test_health_refuses_post(client: Client, state: str) -> None:
    assert client.post("/health").status_code == 405


@override_settings(ENGINE_URL="http://engine.test:8000")
def test_status_page_shows_state_and_reason(operator_client: Client, state: str) -> None:
    response = operator_client.get("/")
    assert response.status_code == 200
    assert "no-cache" in response["Cache-Control"]
    html = response.content.decode()
    assert RESULTS[state].state_word in html
    assert RESULTS[state].badge in html  # the shell's badge, from the same probe
    if RESULTS[state].reason:
        assert str(RESULTS[state].reason) in html
    assert response.context["engine_url"] == "http://engine.test:8000"
    assert "<dt>URL</dt>\n  <dd>http://engine.test:8000</dd>" in html
    assert PYPROJECT_VERSION in html


def test_status_page_lists_checks_and_latency(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, RESULTS["healthy"], MATCHES)
    html = operator_client.get("/").content.decode()
    assert "database: ok" in html
    assert "12 ms" in html
    assert 'href="/health"' in html
    assert 'href="/admin/"' in html


@override_settings(ENGINE_SERVICE_KEY="sekrit-123")
def test_service_key_never_shown(operator_client: Client, state: str) -> None:
    page = operator_client.get("/").content.decode()
    health = operator_client.get("/health").content.decode()
    assert "Service key: accepted" in page
    assert "no service key configured" not in page
    assert "sekrit-123" not in page
    assert "sekrit-123" not in health


@override_settings(ENGINE_SERVICE_KEY=None)
def test_service_key_not_set(operator_client: Client, state: str) -> None:
    assert "(no service key configured)" in operator_client.get("/").content.decode()


@override_settings(ENGINE_URL=None)
def test_unconfigured_url_shown(operator_client: Client, state: str) -> None:
    assert "not configured" in operator_client.get("/").content.decode()


def test_status_page_renders_nested_checks_readably(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The shape Engine main returns: an object, numbers and null among the string checks (#96).
    checks = {
        "database": "ok",
        "workers": {"alive": 2, "last_heartbeat_s": 2.2},
        "oldest_overdue_timer_s": None,
        "erasure_pending": 0,
    }
    result = ProbeResult("healthy", True, 200, "ok", checks, None, 9)
    use(monkeypatch, result, MATCHES)
    html = operator_client.get("/").content.decode()
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
    use(monkeypatch, RESULTS["healthy"], API_CHECKS[name])
    return name


def test_status_page_shows_the_api_line(operator_client: Client, verdict: str) -> None:
    html = " ".join(operator_client.get("/").content.decode().split())
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


def test_change_list_is_capped(operator_client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    changes = tuple(f"added: GET /p{i:02d}" for i in range(23))
    api = ApiCheck("differs", "23 operations added", None, "m4", changes)
    use(monkeypatch, RESULTS["healthy"], api)
    html = operator_client.get("/").content.decode()
    assert "<li>added: GET /p19</li>" in html
    assert "added: GET /p20" not in html
    assert "<li>…and 3 more</li>" in html


def test_version_shown_even_when_unreachable(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = ApiCheck("matches", None, None, "m3")
    use(monkeypatch, RESULTS["unreachable"], api)
    assert "Engine version: m3" in operator_client.get("/").content.decode()


KEY_LINES = {
    "accepted": KeyCheck("accepted", "accepted"),
    "rejected": KeyCheck("rejected", "rejected (401)"),
    "not_checked": KeyCheck("not_checked", "not checked — Engine unreachable"),
}


@pytest.mark.parametrize("name", list(KEY_LINES))
def test_status_page_shows_the_service_key_line(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    use(monkeypatch, RESULTS["healthy"], MATCHES, KEY_LINES[name])
    html = operator_client.get("/").content.decode()
    assert f"Service key: {KEY_LINES[name].detail}" in html
    # Complement: exactly one key line, and never the other verdicts.
    for other in set(KEY_LINES) - {name}:
        assert f"Service key: {KEY_LINES[other].detail}" not in html


M2_CHECKS = {
    "database": "ok",
    "workers": {"alive": 1, "last_heartbeat_s": 3.7},
    "oldest_overdue_timer_s": None,
    "oldest_overdue_job_s": 412.9,
    "erasure_pending": 0,
    "ffmpeg": "ok",
    "ffprobe": "ok",
}


def test_status_page_shows_the_m2_health_rows(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = ProbeResult("degraded", True, 200, "degraded", M2_CHECKS, "job overdue 412 s", 5, True)
    use(monkeypatch, result, MATCHES)
    html = operator_client.get("/").content.decode()
    assert "Engine: Degraded — job overdue 412 s" in html
    assert "Database: ok" in html
    assert "Workers alive: 1" in html
    assert "Last heartbeat: 3 s ago" in html
    assert "Oldest overdue timer: none" in html
    assert "Oldest overdue job: 412 s" in html
    assert "content logging: on" in html


def test_status_page_null_workers_and_logging_off(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = {**M2_CHECKS, "workers": {"alive": None, "last_heartbeat_s": None}}
    result = ProbeResult("healthy", True, 200, "ok", checks, None, 5, False)
    use(monkeypatch, result, MATCHES)
    html = operator_client.get("/").content.decode()
    assert "Workers alive: —" in html
    assert "Last heartbeat: never" in html
    # Complement: content logging is shown only when on.
    assert "content logging" not in html


def test_health_json_carries_degraded_and_content_logging(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = ProbeResult("degraded", True, 200, "degraded", M2_CHECKS, "job overdue 412 s", 5, True)
    use(monkeypatch, result, MATCHES)
    engine = client.get("/health").json()["engine"]
    assert engine["state"] == "degraded"
    assert engine["reason"] == "job overdue 412 s"
    assert engine["checks"] == M2_CHECKS
    assert engine["content_logging"] is True

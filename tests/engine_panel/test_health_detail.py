"""/engine/'s health detail (#50): #43's probe in detail, degraded values highlighted."""

import re
from typing import Any

import pytest
from django.test import Client

from console import context_processors
from console.engine_probe import ProbeResult
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture


def _probe(monkeypatch: pytest.MonkeyPatch, result: ProbeResult) -> list[int]:
    calls: list[int] = []

    def probe() -> ProbeResult:
        calls.append(1)
        return result

    monkeypatch.setattr(context_processors, "probe_for_badge", probe)
    return calls


def _checks(**changes: Any) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "database": "ok",
        "workers": {"alive": 1, "last_heartbeat_s": 3.4},
        "oldest_overdue_timer_s": None,
        "oldest_overdue_job_s": None,
        "erasure_pending": 0,
    }
    checks.update(changes)
    return checks


def _row(html: str, label: str) -> str:
    match = re.search(rf"<th>{label}</th>\s*<td>(.*?)</td>", html, re.S)
    assert match is not None, label
    return match.group(1)


@pytest.fixture
def stories(engine: FakeEngine) -> FakeEngine:
    engine.respond("GET", "/stories", 200, json=engine_fixture("list_stories"))
    return engine


def test_each_degraded_reason_is_highlighted(
    operator_client: Client, stories: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = _checks(
        workers={"alive": 0, "last_heartbeat_s": 75.9},
        oldest_overdue_timer_s=301.2,
        oldest_overdue_job_s=450.0,
        erasure_pending=2,
    )
    reason = "no worker alive, worker heartbeat 75 s ago, timer overdue 301 s, job overdue 450 s"
    result = ProbeResult("degraded", True, 200, "degraded", checks, reason, 12, True)
    probes = _probe(monkeypatch, result)

    html = operator_client.get("/engine/").content.decode()

    assert '<span class="degraded">0</span>' in _row(html, "Workers alive")
    assert '<span class="degraded">75 s ago</span>' in _row(html, "Last heartbeat")
    assert '<span class="muted">&gt; 60 s</span>' in _row(html, "Last heartbeat")
    assert '<span class="degraded">301 s</span>' in _row(html, "Oldest overdue timer")
    assert '<span class="muted">&gt; 300 s</span>' in _row(html, "Oldest overdue timer")
    assert '<span class="degraded">450 s</span>' in _row(html, "Oldest overdue job")
    assert '<span class="degraded">2</span>' in _row(html, "Erasures pending")
    assert _row(html, "Database") == "<span>ok</span>"
    assert _row(html, "Content logging") == "<span>on</span>"
    assert reason in html
    assert '<a href="/engine/">Refresh</a>' in html
    # #43's probe, shared with the badge: one probe, no extra Engine call for health.
    assert probes == [1]
    assert [r.url.path for r in stories.requests] == ["/stories"]


def test_a_missing_heartbeat_and_a_database_error_are_highlighted(
    operator_client: Client, stories: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = _checks(workers={"alive": None, "last_heartbeat_s": None}, database="error")
    _probe(monkeypatch, ProbeResult("unhealthy", True, 503, "unavailable", checks, "x", 5))

    html = operator_client.get("/engine/").content.decode()

    assert _row(html, "Workers alive") == "<span>—</span>"
    assert '<span class="degraded">never</span>' in _row(html, "Last heartbeat")
    assert '<span class="degraded">error</span>' in _row(html, "Database")


def test_a_healthy_engine_highlights_nothing(
    operator_client: Client, stories: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = ProbeResult("healthy", True, 200, "ok", _checks(), None, 4, False)
    _probe(monkeypatch, result)

    html = operator_client.get("/engine/").content.decode()

    assert 'class="degraded"' not in html
    assert _row(html, "Workers alive") == "<span>1</span>"
    assert _row(html, "Last heartbeat") == "<span>3 s ago</span>"
    assert _row(html, "Oldest overdue timer") == "<span>none</span>"
    assert _row(html, "Content logging") == "<span>off</span>"
    assert "<strong>Healthy</strong>" in html


def test_an_unreachable_engine_shows_its_reason_and_no_rows(
    operator_client: Client, stories: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probe(monkeypatch, ProbeResult("unreachable", False, None, None, None, "timeout", None))

    html = operator_client.get("/engine/").content.decode()

    assert "<strong>Unreachable</strong> — timeout" in html
    assert "health-detail" not in html
    # The rest of the page still works.
    assert "Rebuild projections" in html


def test_the_page_links_the_re_embed_run(
    operator_client: Client, stories: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#150: "Re-embed run" always shows beside Deployment settings · Config changes."""
    _probe(monkeypatch, ProbeResult("unreachable", False, None, None, None, "timeout", None))

    html = operator_client.get("/engine/").content.decode()

    assert (
        '<a href="/engine/config-changes/">Config changes</a> · '
        '<a href="/models/embedding/reembed/">Re-embed run</a></p>' in html
    )

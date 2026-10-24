"""The shell every signed-in page shares: nav, operator, logout and the Engine badge (#43)."""

import pytest
from django.test import Client, override_settings

from console import context_processors
from console.engine_probe import ProbeResult

BADGES = {
    "healthy": (
        ProbeResult("healthy", True, 200, "ok", {"database": "ok"}, None, 3),
        "Engine: Healthy",
    ),
    "degraded": (
        ProbeResult("degraded", True, 200, "degraded", {}, "no worker alive", 3),
        "Engine: Degraded — no worker alive",
    ),
    "unhealthy": (
        ProbeResult("unhealthy", True, 503, "unavailable", {}, "database error", 3),
        "Engine: Unhealthy — database error",
    ),
    "unreachable": (
        ProbeResult("unreachable", False, None, None, None, "connection failed", None),
        "Engine: Unreachable — connection failed",
    ),
}


@pytest.mark.parametrize("state", list(BADGES))
def test_badge_renders_each_state(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    result, text = BADGES[state]
    calls: list[int] = []

    def fake() -> ProbeResult:
        calls.append(1)
        return result

    monkeypatch.setattr(context_processors, "probe_for_badge", fake)
    html = operator_client.get("/stories/").content.decode()
    assert f'class="badge state-{state}"' in html
    assert text in html
    assert 'href="/" title="Engine status"' in html
    # Probed once per request, however often the template reads the badge.
    assert calls == [1]
    # Complement: no other state's text.
    for other, (_, other_text) in BADGES.items():
        if other != state:
            assert other_text not in html


def test_health_never_runs_the_badge_probe(
    client: Client, monkeypatch: pytest.MonkeyPatch, badge_probe_calls: list[int]
) -> None:
    from console.engine_api_check import ApiCheck
    from console.views import status

    healthy = BADGES["healthy"][0]
    monkeypatch.setattr(
        status, "engine_state", lambda: (healthy, ApiCheck("matches", None, None, None))
    )
    assert client.get("/health").status_code == 200
    assert badge_probe_calls == []


def test_status_page_badge_reuses_the_page_probe(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch, badge_probe_calls: list[int]
) -> None:
    from console.engine_api_check import ApiCheck
    from console.engine_status import KeyCheck
    from console.views import status

    degraded = BADGES["degraded"][0]
    monkeypatch.setattr(
        status,
        "status_state",
        lambda: (degraded, ApiCheck("matches", None, None, None), KeyCheck("accepted", "accepted")),
    )
    html = operator_client.get("/").content.decode()
    assert "Engine: Degraded — no worker alive" in html
    # Complement: no second probe from the context processor.
    assert badge_probe_calls == []


def test_active_section_is_marked(operator_client: Client) -> None:
    html = operator_client.get("/guardrails/ratings/").content.decode()
    assert '<a href="/guardrails/ratings/" class="active">Guardrails</a>' in html
    assert '<a href="/stories/">Stories</a>' in html


@pytest.mark.parametrize("path", ["/guardrails/ratings/", "/engine/"])
def test_placeholders_render_in_the_shell(operator_client: Client, path: str) -> None:
    response = operator_client.get(path)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Not built yet." in html
    assert "Log out" in html


@override_settings(ROOT_URLCONF="tests.urls_flash")
def test_flash_message_renders_once_below_the_nav(operator_client: Client) -> None:
    response = operator_client.post("/_test/flash/")
    assert response.status_code == 302
    first = operator_client.get("/stories/").content.decode()
    second = operator_client.get("/stories/").content.decode()
    assert first.count("Saved revision 3") == 1
    assert first.index("Saved revision 3") > first.index("Log out")
    assert '<li class="success">Saved revision 3</li>' in first
    # Complement: shown once, then gone.
    assert "Saved revision 3" not in second

"""The status page asks /health and /openapi.json concurrently, once each, with no retries."""

import logging
import time
from typing import Any

import httpx
import pytest
from django.test import Client, override_settings

from console import engine_api_check, engine_probe, engine_status
from console.engine.client import EngineClient
from tests.fakes.engine import FakeEngine, recorded_schema

OK = {"status": "ok", "checks": {"database": "ok"}}


def use(fake: FakeEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    client = EngineClient("http://engine.test", None, transport=fake.transport)
    monkeypatch.setattr(EngineClient, "from_settings", classmethod(lambda cls: client))


@override_settings(ENGINE_URL="http://engine.test")
def test_page_waits_for_the_slower_call_not_the_sum(
    client: Client, fake_engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_engine.respond("GET", "/health", 200, json=OK, delay=1.0)
    fake_engine.serve("GET", "/openapi.json", 200, json=recorded_schema(), delay=1.0)
    use(fake_engine, monkeypatch)
    started = time.monotonic()
    html = client.get("/").content.decode()
    elapsed = time.monotonic() - started
    assert "Healthy" in html
    assert "API: matches" in html
    assert elapsed < 1.8, f"took {elapsed:.2f} s: the two calls ran one after the other"


@override_settings(ENGINE_URL="http://engine.test")
@pytest.mark.parametrize("path", ["/", "/health"])
def test_unreachable_engine_gets_one_request_per_endpoint(
    client: Client, fake_engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    fake_engine.fail("GET", "/health", httpx.ConnectError("refused"))
    fake_engine.fail("GET", "/openapi.json", httpx.ConnectError("refused"))
    use(fake_engine, monkeypatch)
    response = client.get(path)
    assert response.status_code == 200
    paths = sorted(r.url.path for r in fake_engine.requests)
    # Complement: no retries.
    assert paths == ["/health", "/openapi.json"]


@override_settings(ENGINE_URL=None)
def test_not_configured_makes_no_requests(client: Client) -> None:
    body: dict[str, Any] = client.get("/health").json()
    assert body["engine"]["state"] == "unreachable"
    assert body["engine"]["api"]["reason"] == "not configured"


@pytest.mark.parametrize("slow", ["/health", "/openapi.json"])
def test_a_call_past_the_deadline_reports_timeout(
    fake_engine: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    slow: str,
) -> None:
    # The two FutureTimeout fallbacks (#109): the page gives up at the deadline, not the call.
    monkeypatch.setattr(engine_status, "DEADLINE_S", 0.3)
    delay = {"/health": 0.0, "/openapi.json": 0.0, slow: 1.0}
    fake_engine.respond("GET", "/health", 200, json=OK, delay=delay["/health"])
    fake_engine.serve(
        "GET", "/openapi.json", 200, json=recorded_schema(), delay=delay["/openapi.json"]
    )
    client = EngineClient("http://engine.test", None, transport=fake_engine.transport)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="console.engine"):
        probed, checked = engine_status.probe_engine(client)
    elapsed = time.monotonic() - started
    assert elapsed < 0.8, f"took {elapsed:.2f} s: waited for the slow call"
    if slow == "/health":
        assert (probed.state, probed.reason) == ("unreachable", "timeout")
        assert checked.verdict == "matches"
    else:
        assert probed.state == "healthy"
        assert (checked.verdict, checked.reason) == ("unknown", "timeout")
    deadline_lines = [
        r.getMessage() for r in caplog.records if "reported as timeout" in r.getMessage()
    ]
    assert deadline_lines == [
        f"Engine at http://engine.test: no answer from {slow} within 0.3 s; reported as timeout"
    ]


def test_an_abandoned_call_still_logs_its_own_outcome_later(
    fake_engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Documented in engine_status (#117): the page says `timeout` at the deadline; the call is
    # not cancelled, and its own line (here `unknown: bad response`) follows when it finishes.
    monkeypatch.setattr(engine_status, "DEADLINE_S", 0.3)
    monkeypatch.setattr(engine_api_check, "_last_verdict", None)
    monkeypatch.setattr(engine_api_check, "_last_logged", 0.0)
    monkeypatch.setattr(engine_probe, "_last_state", None)
    fake_engine.respond("GET", "/health", 200, json=OK)
    fake_engine.serve("GET", "/openapi.json", 200, json=["not", "a", "schema"], delay=0.8)
    client = EngineClient("http://engine.test", None, transport=fake_engine.transport)
    late = "Engine API at http://engine.test unknown: bad response"
    with caplog.at_level(logging.WARNING, logger="console.engine"):
        _, checked = engine_status.probe_engine(client)
        assert (checked.verdict, checked.reason) == ("unknown", "timeout")
        assert late not in caplog.messages
        give_up = time.monotonic() + 5.0
        while late not in caplog.messages and time.monotonic() < give_up:
            time.sleep(0.05)
    assert caplog.messages == [
        "Engine at http://engine.test: no answer from /openapi.json within 0.3 s; "
        "reported as timeout",
        late,
    ]

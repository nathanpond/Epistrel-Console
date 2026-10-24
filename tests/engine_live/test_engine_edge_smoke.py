"""The Console against the live Engine (``ENGINE_LIVE_URL``), through its real client and pages.

Run by ci.yml's engine-smoke job against the Engine ``edge`` image; locally, see the README.
"""

import pytest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_api_check import check_engine_api
from console.engine_probe import probe
from tests.plugins.engine_live import live_url

pytestmark = pytest.mark.engine_live


@pytest.fixture
def url() -> str:
    return live_url()


def test_client_health(url: str) -> None:
    result = EngineClient(url, None).health()
    assert result.http_status == 200
    assert result.body is not None, f"/health did not parse as HealthResponse: {result.raw}"
    assert result.body.status == "ok"
    assert result.body.checks.database == "ok"


def test_probe_says_healthy(url: str) -> None:
    result = probe(EngineClient(url, None))
    assert result.state == "healthy", result.reason


def test_status_page_and_health(url: str, client: Client) -> None:
    with override_settings(ENGINE_URL=url):
        page = client.get("/").content.decode()
        health = client.get("/health")
    assert "Healthy" in page
    assert "Unhealthy" not in page
    assert "Unreachable" not in page
    assert health.status_code == 200
    engine = health.json()["engine"]
    assert engine["state"] == "healthy"
    assert engine["checks"]["database"] == "ok"


@pytest.mark.xfail(
    strict=False,
    reason="Engine edge API drift is reported by the daily drift check (#40), not the PR gate",
)
def test_live_engine_api_matches_the_recording(url: str) -> None:
    assert check_engine_api(EngineClient(url, None)).verdict == "matches"

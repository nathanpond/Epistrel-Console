import time

from django.test import override_settings

from console.engine_probe import probe
from tests.stub_engine import base_url, stub_engine


def test_live_healthy_engine() -> None:
    with stub_engine() as server, override_settings(ENGINE_URL=base_url(server)):
        result = probe()
    assert result.state == "healthy"
    assert result.latency_ms is not None


def test_slow_engine_times_out_quickly() -> None:
    with stub_engine(delay_s=5) as server, override_settings(ENGINE_URL=base_url(server)):
        started = time.monotonic()
        result = probe()
        elapsed = time.monotonic() - started
    assert (result.state, result.reason) == ("unreachable", "timeout")
    # It gave up rather than waiting for the answer.
    assert elapsed < 4.5

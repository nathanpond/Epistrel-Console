"""Markers for tests that need a live Engine.

``engine_live``: the smoke suite in ``tests/engine_live/``, run by ci.yml's engine-smoke job. It
reads the Engine's URL from ``ENGINE_LIVE_URL``; selecting it without that variable is a usage
error, so a misconfigured job can never pass with zero tests. ``engine_edge``: docker tests that
pull the live Engine ``edge`` image (#32's compose run), left out when the live smoke is off.
"""

import os

import pytest

URL_VAR = "ENGINE_LIVE_URL"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "engine_live: needs a live Engine at ENGINE_LIVE_URL; `pytest -m engine_live`"
    )
    config.addinivalue_line("markers", "engine_edge: docker test that runs the live Engine edge")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # trylast: runs after `-m` deselection, so only selected live tests count.
    if os.environ.get(URL_VAR, "").strip():
        return
    if any(item.get_closest_marker("engine_live") for item in items):
        raise pytest.UsageError(f"{URL_VAR} is required to run engine_live tests")


def live_url() -> str:
    return os.environ[URL_VAR].strip().rstrip("/")

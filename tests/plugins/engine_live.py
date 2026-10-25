"""Markers for tests that need a live Engine.

``engine_live``: the smoke suite in ``tests/engine_live/``, run by ci.yml's engine-smoke job. It
reads the Engine's URL from ``ENGINE_LIVE_URL`` and the service key from
``ENGINE_LIVE_SERVICE_KEY`` (#42); selecting it without either is a usage error, so a
misconfigured job can never pass with zero tests. ``engine_auth``: a live test that needs an Engine
enforcing service auth and its M2 routes (Engine #44); the release smoke leaves it out while the
newest Engine release has neither. ``engine_edge``: docker tests that pull the live Engine ``edge``
image (#32's compose run), left out when the live smoke is off.
"""

import os

import pytest

URL_VAR = "ENGINE_LIVE_URL"
KEY_VAR = "ENGINE_LIVE_SERVICE_KEY"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "engine_live: needs a live Engine at ENGINE_LIVE_URL; `pytest -m engine_live`"
    )
    config.addinivalue_line(
        "markers", "engine_auth: engine_live test that needs an Engine enforcing service auth"
    )
    config.addinivalue_line("markers", "engine_edge: docker test that runs the live Engine edge")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # trylast: runs after `-m` deselection, so only selected live tests count.
    if not any(item.get_closest_marker("engine_live") for item in items):
        return
    if not os.environ.get(URL_VAR, "").strip():
        raise pytest.UsageError(f"{URL_VAR} is required to run engine_live tests")
    # Empty counts as unset; like the Console's own key, the value is never trimmed.
    if not os.environ.get(KEY_VAR, ""):
        raise pytest.UsageError(f"{KEY_VAR} is required to run engine_live tests")


def live_url() -> str:
    return os.environ[URL_VAR].strip().rstrip("/")


def live_key() -> str:
    return os.environ[KEY_VAR]

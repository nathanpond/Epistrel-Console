"""REQUIRE_DOCKER_TESTS=1 (CI's docker job): a docker-marked test that would skip fails instead.

Skips whose reason starts with ``port `` (a local port already in use) stay skips. M0's container
tests are unchanged; their skips (no CLI, no daemon, pull failed) become failures only in CI.
"""

import os
from collections.abc import Generator

import pytest


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """With REQUIRE_DOCKER_TESTS=1 (CI's docker job), a docker test that would skip fails.

    Skips whose reason starts with ``port `` (a local port already in use) stay skips.
    """
    report = yield
    if (
        os.environ.get("REQUIRE_DOCKER_TESTS") == "1"
        and item.get_closest_marker("docker") is not None
        and report.skipped
    ):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        reason = reason.removeprefix("Skipped: ")
        if not reason.startswith("port "):
            report.outcome = "failed"
            report.longrepr = f"REQUIRE_DOCKER_TESTS=1 but the test would skip: {reason}"
    return report

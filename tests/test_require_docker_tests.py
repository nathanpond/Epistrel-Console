"""REQUIRE_DOCKER_TESTS=1 turns a would-be docker skip into a failure (both directions)."""

import pytest

from tests.helpers import REPO

SAMPLE = """
import pytest

pytestmark = pytest.mark.docker


def test_daemon_missing():
    pytest.skip("docker daemon is not answering")


@pytest.mark.skipif(True, reason="docker CLI not available")
def test_cli_missing():
    pass


def test_port_busy():
    pytest.skip("port 7740 is in use")


def test_passes():
    pass
"""

NOT_DOCKER = """
import pytest


def test_plain_skip():
    pytest.skip("not a docker test")
"""


def run(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, require: str | None
) -> pytest.RunResult:
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    if require is None:
        monkeypatch.delenv("REQUIRE_DOCKER_TESTS", raising=False)
    else:
        monkeypatch.setenv("REQUIRE_DOCKER_TESTS", require)
    pytester.makepyfile(test_sample=SAMPLE, test_plain=NOT_DOCKER)
    return pytester.runpytest_subprocess(
        "-p", "tests.plugins.require_docker", "-p", "no:django", "-p", "no:cacheprovider", "-rA"
    )


def test_skips_fail_when_required(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(pytester, monkeypatch, "1")
    # A skipif mark skips during setup, so that one is reported as an error: the run still fails.
    result.assert_outcomes(passed=1, failed=1, errors=1, skipped=2)
    assert result.ret != 0
    result.stdout.fnmatch_lines(
        [
            "*REQUIRE_DOCKER_TESTS=1 but the test would skip: docker CLI not available*",
            "*REQUIRE_DOCKER_TESTS=1 but the test would skip: docker daemon is not answering*",
        ]
    )


@pytest.mark.parametrize("value", [None, "0", ""])
def test_skips_stay_skips_otherwise(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    result = run(pytester, monkeypatch, value)
    result.assert_outcomes(passed=1, skipped=4)
    assert result.ret == 0

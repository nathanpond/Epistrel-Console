"""engine_live tests: deselected by default, and an error (never a skip) without ENGINE_LIVE_URL or
ENGINE_LIVE_SERVICE_KEY."""

import pytest

from tests.helpers import REPO

LIVE = """
import pytest

@pytest.mark.engine_live
def test_live():
    pass

def test_plain():
    pass
"""


def run(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, *args: str) -> pytest.RunResult:
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    pytester.makeini("[pytest]\naddopts = -m 'not docker and not engine_live'\n")
    pytester.makepyfile(test_live=LIVE)
    return pytester.runpytest_subprocess(
        "-p", "tests.plugins.engine_live", "-p", "no:django", "-p", "no:cacheprovider", *args
    )


KEY = "live-key-not-a-secret"


def test_selected_without_url_is_a_usage_error(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ENGINE_LIVE_URL", raising=False)
    monkeypatch.setenv("ENGINE_LIVE_SERVICE_KEY", KEY)
    result = run(pytester, monkeypatch, "-m", "engine_live")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*ENGINE_LIVE_URL is required to run engine_live tests*"])
    # Complement: it must not pass quietly as a skip.
    assert "skipped" not in result.stdout.str()
    assert "passed" not in result.stdout.str()


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_url_is_unset(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ENGINE_LIVE_URL", value)
    monkeypatch.setenv("ENGINE_LIVE_SERVICE_KEY", KEY)
    assert run(pytester, monkeypatch, "-m", "engine_live").ret == pytest.ExitCode.USAGE_ERROR


def test_deselected_by_default(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENGINE_LIVE_URL", raising=False)
    monkeypatch.delenv("ENGINE_LIVE_SERVICE_KEY", raising=False)
    result = run(pytester, monkeypatch)
    result.assert_outcomes(passed=1, deselected=1)


@pytest.mark.parametrize("key", [None, ""], ids=["unset", "empty"])
def test_selected_without_key_is_a_usage_error(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    monkeypatch.setenv("ENGINE_LIVE_URL", "http://127.0.0.1:9")
    if key is None:
        monkeypatch.delenv("ENGINE_LIVE_SERVICE_KEY", raising=False)
    else:
        monkeypatch.setenv("ENGINE_LIVE_SERVICE_KEY", key)
    result = run(pytester, monkeypatch, "-m", "engine_live")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*ENGINE_LIVE_SERVICE_KEY is required to run engine_live tests*"])
    assert "skipped" not in result.stdout.str()
    assert "passed" not in result.stdout.str()


def test_selected_with_url_and_key_runs(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENGINE_LIVE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ENGINE_LIVE_SERVICE_KEY", KEY)
    result = run(pytester, monkeypatch, "-m", "engine_live")
    result.assert_outcomes(passed=1, deselected=1)

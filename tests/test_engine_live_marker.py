"""engine_live tests: deselected by default, and an error (never a skip) without ENGINE_LIVE_URL."""

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


def test_selected_without_url_is_a_usage_error(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ENGINE_LIVE_URL", raising=False)
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
    assert run(pytester, monkeypatch, "-m", "engine_live").ret == pytest.ExitCode.USAGE_ERROR


def test_deselected_by_default(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENGINE_LIVE_URL", raising=False)
    result = run(pytester, monkeypatch)
    result.assert_outcomes(passed=1, deselected=1)


def test_selected_with_url_runs(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENGINE_LIVE_URL", "http://127.0.0.1:9")
    result = run(pytester, monkeypatch, "-m", "engine_live")
    result.assert_outcomes(passed=1, deselected=1)

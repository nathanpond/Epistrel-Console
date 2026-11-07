"""engine_live tests: deselected by default, and an error (never a skip) without ENGINE_LIVE_URL or
ENGINE_LIVE_SERVICE_KEY."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

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


def test_full_profile_refuses_a_profile_named_structured() -> None:
    """#293 (#282): the live smoke finds Full by its stable id and checks its name, so a seed
    still (or again) named "Structured" fails loudly instead of silently testing it."""
    from console.engine.client import EngineClient
    from tests.engine_live.test_engine_edge_smoke import FULL_PROFILE_ID, _full_profile

    asked: list[str] = []

    def answer(name: str) -> Callable[[str], SimpleNamespace]:
        def get_engine_profile(profile_id: str) -> SimpleNamespace:
            asked.append(profile_id)
            return SimpleNamespace(name=name, preset=True, current_version=4)

        return get_engine_profile

    stub = SimpleNamespace(get_engine_profile=answer("Structured"))
    with pytest.raises(AssertionError, match="Structured"):
        _full_profile(cast(EngineClient, stub))
    assert asked == [FULL_PROFILE_ID]
    # Complement: the seeded Full passes and its id comes back.
    stub = SimpleNamespace(get_engine_profile=answer("Full"))
    assert _full_profile(cast(EngineClient, stub)) == FULL_PROFILE_ID

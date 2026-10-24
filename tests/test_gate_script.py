""".github/scripts/gate.sh: green only when every job succeeded; engine-smoke alone may skip,
and only when the live smoke is off."""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers import REPO

GATE = REPO / ".github" / "scripts" / "gate.sh"
JOBS = ("LINT", "TYPES", "TEST", "DOCKER", "ENGINE_SMOKE", "ENGINE_RELEASE_SMOKE")


def gate(tmp_path: Path, live_smoke: str | None = None, **results: str) -> tuple[int, str, str]:
    env = {"PATH": "/usr/bin:/bin", "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")}
    env.update(dict.fromkeys(JOBS, "success"))
    env.update(results)
    if live_smoke is not None:
        env["LIVE_SMOKE"] = live_smoke
    bash = shutil.which("bash")
    assert bash is not None
    proc = subprocess.run(  # noqa: S603
        [bash, str(GATE)], env=env, capture_output=True, text=True, check=False
    )
    written = tmp_path / "summary.md"
    summary = written.read_text() if written.exists() else ""
    return proc.returncode, proc.stdout, summary


def test_all_success_passes(tmp_path: Path) -> None:
    code, out, summary = gate(tmp_path, "true")
    assert code == 0
    assert "gate: all jobs succeeded" in out
    assert "| engine-smoke | success |" in summary
    assert "| engine-release-smoke | success |" in summary


@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
def test_any_job_not_succeeding_fails_with_live_smoke(
    tmp_path: Path, job: str, result: str
) -> None:
    code, out, _ = gate(tmp_path, "true", **{job: result})
    assert code == 1
    assert "did not succeed" in out


SMOKES = ("ENGINE_SMOKE", "ENGINE_RELEASE_SMOKE")


@pytest.mark.parametrize("smoke", SMOKES)
def test_skipped_smoke_passes_only_when_live_smoke_is_off(tmp_path: Path, smoke: str) -> None:
    assert gate(tmp_path, "false", **{smoke: "skipped"})[0] == 0
    # Complement: by default (a PR) the smoke may not be skipped.
    assert gate(tmp_path, None, **{smoke: "skipped"})[0] == 1


def test_both_smokes_skipped_pass_when_live_smoke_is_off(tmp_path: Path) -> None:
    assert gate(tmp_path, "false", **dict.fromkeys(SMOKES, "skipped"))[0] == 0


@pytest.mark.parametrize("job", ["LINT", "TYPES", "TEST", "DOCKER"])
def test_live_smoke_off_does_not_excuse_other_skips(tmp_path: Path, job: str) -> None:
    assert gate(tmp_path, "false", **{job: "skipped"})[0] == 1


@pytest.mark.parametrize("smoke", SMOKES)
def test_failed_smoke_fails_even_with_live_smoke_off(tmp_path: Path, smoke: str) -> None:
    assert gate(tmp_path, "false", **{smoke: "failure"})[0] == 1


def test_missing_release_smoke_result_fails(tmp_path: Path) -> None:
    assert gate(tmp_path, "false", ENGINE_RELEASE_SMOKE="")[0] == 1


def test_missing_result_fails(tmp_path: Path) -> None:
    env_results = {"LINT": ""}
    assert gate(tmp_path, "true", **env_results)[0] == 1

"""Guards over release.yml (#38) and the release-tags ruleset."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_workflows import (
    WORKFLOWS,
    gate_bypasses,
    jobs,
    load,
    tagging_bypasses,
    triggers,
    write_permissions,
)
from tests.workflow_steps import run_step, step

RELEASE = WORKFLOWS / "release.yml"


def test_release_runs_on_v_tags_without_cancelling() -> None:
    doc = load(RELEASE)
    assert triggers(doc) == {"push": {"tags": ["v*"]}}
    assert doc["concurrency"] == {"group": "release-${{ github.ref }}", "cancel-in-progress": False}
    assert doc["permissions"] == {"contents": "read"}


def test_release_runs_the_full_gate_against_the_matching_engine() -> None:
    doc = load(RELEASE)
    assert gate_bypasses(doc) == []
    ci = jobs(doc)["ci"]
    assert ci["with"]["engine_image"] == "${{ needs.validate.outputs.engine_image }}"
    # Complement: a release never turns the live Engine smoke off.
    assert "live_smoke" not in ci["with"]
    assert set(jobs(doc)["publish"]["needs"]) == {"validate", "ci"}


def test_a_release_without_the_gate_fails_the_rule() -> None:
    doc = copy.deepcopy(load(RELEASE))
    doc["jobs"]["publish"]["needs"] = ["validate"]
    doc["jobs"]["publish"]["if"] = "always()"
    assert gate_bypasses(doc) == [
        "publish does not need ci",
        "publish does not require ci to succeed",
    ]


def test_only_publish_writes() -> None:
    doc = load(RELEASE)
    publish = jobs(doc)["publish"]
    assert publish["permissions"] == {"contents": "write", "packages": "write"}
    others = {name: job for name, job in jobs(doc).items() if name != "publish"}
    assert write_permissions({"jobs": others}) == []


def test_publish_shares_the_rollback_concurrency_group() -> None:
    assert jobs(load(RELEASE))["publish"]["concurrency"] == {
        "group": "release-latest",
        "cancel-in-progress": False,
    }


def test_validate_outputs_the_engine_image() -> None:
    validate = jobs(load(RELEASE))["validate"]
    assert validate["outputs"]["engine_image"] == "${{ steps.validate.outputs.engine_image }}"


def test_release_tags_ruleset_is_admin_only() -> None:
    doc: dict[str, Any] = json.loads(
        (WORKFLOWS.parent / "rulesets" / "release-tags.json").read_text()
    )
    assert doc["name"] == "release-tags"
    assert doc["target"] == "tag"
    assert doc["enforcement"] == "active"
    assert doc["conditions"]["ref_name"] == {"include": ["refs/tags/v*"], "exclude": []}
    assert sorted(rule["type"] for rule in doc["rules"]) == ["creation", "deletion", "update"]
    assert doc["bypass_actors"] == [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
    ]


# --- a failed run publishes nothing (#106) ----------------------------------------------------

ENGINE_GUARD = "The gate named the Engine it tested"
RELEASE_STEP = "Create or update the GitHub release"
MUTATIONS = ("imagetools create", "gh release", "build-push-action", "login-action")


def publish_job() -> dict[str, Any]:
    job: dict[str, Any] = copy.deepcopy(jobs(load(RELEASE))["publish"])
    return job


def guard_problems(job: dict[str, Any]) -> list[str]:
    """Why an empty Engine output could still reach a push or a release."""
    names = [s.get("name") for s in job["steps"]]
    if ENGINE_GUARD not in names:
        return ["no Engine-output guard"]
    guard = job["steps"][names.index(ENGINE_GUARD)]
    problems = []
    before = job["steps"][: names.index(ENGINE_GUARD)]
    if any(m in str(s.get("uses", "")) + str(s.get("run", "")) for s in before for m in MUTATIONS):
        problems.append("a step that logs in, pushes or releases comes before the guard")
    if "if" in guard or guard.get("continue-on-error", False) is not False:
        problems.append("the guard can be skipped or ignored")
    return problems


def test_release_tags_only_after_a_passing_smoke() -> None:
    job = publish_job()
    assert tagging_bypasses(job) == []
    # The GitHub release is created after the tags moved, and only if they did.
    ids = [s.get("id") or s.get("name") for s in job["steps"]]
    assert ids.index("tag") < ids.index(RELEASE_STEP)
    assert "if" not in step(job, RELEASE_STEP)


@pytest.mark.parametrize("mutation", ["tag before smoke", "tags on build"])
def test_release_tagging_rule_bites(mutation: str) -> None:
    job = publish_job()
    if mutation == "tag before smoke":
        tag = job["steps"].pop(next(i for i, s in enumerate(job["steps"]) if s.get("id") == "tag"))
        smoke = next(i for i, s in enumerate(job["steps"]) if s.get("id") == "smoke")
        job["steps"].insert(smoke, tag)
        assert tagging_bypasses(job) == ["steps are not build, then smoke, then tag"]
    else:
        step(job, "build")["with"]["tags"] = "${{ steps.meta.outputs.tags }}"
        assert tagging_bypasses(job) == ["build does not push by digest only"]


def test_engine_guard_runs_first() -> None:
    job = publish_job()
    assert job["steps"][0]["name"] == ENGINE_GUARD
    assert guard_problems(job) == []


def test_engine_guard_rule_bites() -> None:
    job = publish_job()
    guard = job["steps"].pop(0)
    job["steps"].insert(4, guard)
    assert guard_problems(job) == ["a step that logs in, pushes or releases comes before the guard"]
    guard["continue-on-error"] = True
    assert "the guard can be skipped or ignored" in guard_problems(job)
    job["steps"].remove(guard)
    assert guard_problems(job) == ["no Engine-output guard"]


@pytest.mark.parametrize(
    ("digest", "version", "rc"),
    [("sha256:abc", "m3", 0), ("", "m3", 1), ("sha256:abc", "", 1), ("", "", 1)],
)
def test_engine_guard_refuses_empty_outputs(
    tmp_path: Path, digest: str, version: str, rc: int
) -> None:
    script = step(publish_job(), ENGINE_GUARD)["run"]
    result = run_step(script, tmp_path, env={"ENGINE_DIGEST": digest, "ENGINE_VERSION": version})
    assert result.rc == rc, result.output
    if rc:
        assert "nothing is published" in result.output
    assert result.calls == []


def test_tags_and_latest_follow_the_validate_outputs() -> None:
    meta = step(publish_job(), "meta")["with"]
    enable = "enable=${{ needs.validate.outputs.highest_%s }}"
    assert meta["tags"].splitlines() == [
        "type=semver,pattern={{version}}",
        "type=semver,pattern={{major}}.{{minor}}," + enable % "minor",
        "type=semver,pattern={{major}}," + enable % "major",
    ]
    assert meta["flavor"].strip() == (
        "${{ needs.validate.outputs.highest == 'true' && 'latest=auto' || 'latest=false' }}"
    )


GH_STUB = """
case "$1 $2" in
  "release view")
    [ -n "${EXISTING:-}" ] || exit 1
    case "$*" in *body*) printf 'Earlier notes\\n' ;; esac
    exit 0 ;;
  "api -X") printf '## What changed\\n- a change\\n'; exit 0 ;;
  "release create"|"release edit") exit 0 ;;
esac
"""


def run_release_step(tmp_path: Path, prerelease: str, highest: str, existing: str = "") -> Any:
    env = {
        "TAG": "v0.2.0",
        "VERSION": "0.2.0",
        "PRERELEASE": prerelease,
        "HIGHEST": highest,
        "DIGEST": "sha256:console",
        "ENGINE_DIGEST": "sha256:engine",
        "ENGINE_VERSION": "m3",
        "EXISTING": existing,
    }
    script = step(publish_job(), RELEASE_STEP)["run"]
    return run_step(script, tmp_path, env=env, stubs={"gh": GH_STUB})


@pytest.mark.parametrize(
    ("prerelease", "highest", "flags"),
    [
        ("false", "true", []),
        ("false", "false", ["--latest=false"]),
        ("true", "false", ["--prerelease", "--latest=false"]),
        ("true", "true", ["--prerelease"]),
    ],
)
def test_release_flags_follow_prerelease_and_highest(
    tmp_path: Path, prerelease: str, highest: str, flags: list[str]
) -> None:
    result = run_release_step(tmp_path, prerelease, highest)
    assert result.rc == 0, result.output
    (create,) = result.called("gh", "release", "create")
    assert create == [
        "gh", "release", "create", "v0.2.0", "--verify-tag", "--title", "v0.2.0",
        *flags, "--notes-file", "body.md",
    ]  # fmt: skip
    body = (tmp_path / "body.md").read_text()
    assert "Image: `ghcr.io/nathanpond/epistrel-console:0.2.0@sha256:console`" in body
    assert "Tested with Engine m3 (`sha256:engine`)" in body
    assert result.called("gh", "release", "edit") == []


def test_a_rerun_edits_the_existing_release_and_creates_nothing(tmp_path: Path) -> None:
    result = run_release_step(tmp_path, "false", "true", existing="1")
    assert result.rc == 0, result.output
    assert result.called("gh", "release", "create") == []
    (edit,) = result.called("gh", "release", "edit")
    assert edit == ["gh", "release", "edit", "v0.2.0", "--notes-file", "body.md"]
    assert "Earlier notes" in (tmp_path / "body.md").read_text()

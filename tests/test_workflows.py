"""Guards over .github/workflows/*.yml: pinned actions, no floating downloads, least privilege.

Each rule is a pure function over parsed YAML, checked against the real files and against an
in-memory bad sample, so every rule is seen to fail when it should. Whether the pinned versions
are current is Dependabot's job, not this file's.
"""

import copy
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"


def load(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return doc


def workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def triggers(doc: dict[str, Any]) -> Any:
    # PyYAML reads the bare key `on` as boolean True.
    raw: dict[Any, Any] = doc
    return raw["on"] if "on" in raw else raw.get(True)


def jobs(doc: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = doc.get("jobs") or {}
    return found


def steps(doc: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for job_id, job in jobs(doc).items():
        for step in job.get("steps") or []:
            yield job_id, step


def scalars(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for value in node.values():
            yield from scalars(value)
    elif isinstance(node, list):
        for value in node:
            yield from scalars(value)
    elif isinstance(node, str):
        yield node


# --- rules ---------------------------------------------------------------------------------


def unpinned_uses(doc: dict[str, Any]) -> list[str]:
    """``uses:`` without an ``@ref`` (local ``./`` and ``docker://`` are fine)."""
    refs = [job["uses"] for job in jobs(doc).values() if "uses" in job]
    refs += [step["uses"] for _, step in steps(doc) if "uses" in step]
    return [
        ref
        for ref in refs
        if not ref.startswith(("./", "docker://")) and not re.search(r"@\S+$", ref)
    ]


def floating_downloads(doc: dict[str, Any]) -> list[str]:
    """A value of exactly ``latest``, or a ``/releases/latest/`` URL anywhere."""
    return [v for v in scalars(doc) if v.strip() == "latest" or "/releases/latest/" in v]


def missing_permissions(doc: dict[str, Any]) -> bool:
    return "permissions" not in doc


def ungated_jobs(doc: dict[str, Any]) -> list[str]:
    """ci.yml jobs other than ``gate`` that ``gate`` does not ``need``."""
    gate = jobs(doc).get("gate") or {}
    needs = gate.get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    return sorted(set(jobs(doc)) - {"gate"} - set(needs))


def write_permissions(doc: dict[str, Any]) -> list[str]:
    found = []
    for scope in [doc, *jobs(doc).values()]:
        perms = scope.get("permissions")
        if perms == "write-all" or (
            isinstance(perms, dict) and any(v == "write" for v in perms.values())
        ):
            found.append(str(perms))
    return found


def checkouts_persisting_credentials(doc: dict[str, Any]) -> list[str]:
    return [
        job_id
        for job_id, step in steps(doc)
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and (step.get("with") or {}).get("persist-credentials") is not False
    ]


def setup_uv_without_cache(doc: dict[str, Any]) -> list[str]:
    return [
        job_id
        for job_id, step in steps(doc)
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
        and (step.get("with") or {}).get("enable-cache") is not True
    ]


def sets_engine_url(doc: dict[str, Any], job_id: str) -> bool:
    """``CONSOLE_ENGINE_URL`` set (even empty) for ``job_id``, by env or a GITHUB_ENV write."""
    job = jobs(doc).get(job_id) or {}
    envs = [doc.get("env") or {}, job.get("env") or {}]
    envs += [step.get("env") or {} for step in job.get("steps") or []]
    if any("CONSOLE_ENGINE_URL" in env for env in envs):
        return True
    return any(
        "CONSOLE_ENGINE_URL" in str(step.get("run", ""))
        and "GITHUB_ENV" in str(step.get("run", ""))
        for step in job.get("steps") or []
    )


def dockerfile_uv_version() -> str:
    match = re.search(r"uv[:=@]v?(\d+\.\d+\.\d+)", (REPO_ROOT / "Dockerfile").read_text())
    assert match, "no pinned uv in the Dockerfile"
    return match.group(1)


# --- the real files ------------------------------------------------------------------------


def test_there_are_workflows() -> None:
    assert CI in workflow_files()


@pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
def test_workflow_is_pinned_and_least_privilege(path: Path) -> None:
    doc = load(path)
    assert unpinned_uses(doc) == []
    assert floating_downloads(doc) == []
    assert not missing_permissions(doc)
    assert checkouts_persisting_credentials(doc) == []
    assert setup_uv_without_cache(doc) == []


def test_ci_runs_on_pull_request_and_workflow_call() -> None:
    on = triggers(load(CI))
    assert "pull_request" in on
    assert "workflow_call" in on
    assert on["workflow_call"]["inputs"]["ref"]["default"] == ""


def test_ci_concurrency_cancels_only_prs() -> None:
    concurrency = load(CI)["concurrency"]
    assert concurrency["group"] == "ci-${{ github.event.pull_request.number || github.ref }}"
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_ci_permissions_are_read_only() -> None:
    doc = load(CI)
    assert doc["permissions"] == {"contents": "read"}
    assert write_permissions(doc) == []


def test_gate_needs_every_job_and_always_runs() -> None:
    doc = load(CI)
    assert ungated_jobs(doc) == []
    assert jobs(doc)["gate"]["if"] == "always()"


def test_ci_jobs_run_the_planned_checks() -> None:
    runs = {
        job_id: [str(step.get("run", "")) for step in job.get("steps") or []]
        for job_id, job in jobs(load(CI)).items()
    }
    for command in (
        "uv lock --check",
        "uv sync --frozen",
        "uv run ruff check --output-format=github .",
        "uv run ruff format --check .",
    ):
        assert command in runs["lint"]
    assert "uv run mypy" in runs["types"]
    for command in (
        "uv run pytest",
        "uv run python manage.py check",
        "uv run python manage.py makemigrations --check --dry-run",
    ):
        assert command in runs["test"]
    assert any(r.startswith("uv run pytest -m") for r in runs["docker"])


def test_mypy_is_not_weakened_in_ci() -> None:
    weakening = ("--no-strict", "--allow-untyped", "--no-warn", "--ignore-missing-imports")
    for _, step in steps(load(CI)):
        run = str(step.get("run", ""))
        if "mypy" in run:
            assert not any(flag in run for flag in weakening), run


def test_docker_job_requires_docker_tests() -> None:
    docker = jobs(load(CI))["docker"]
    step = next(s for s in docker["steps"] if "pytest -m" in str(s.get("run", "")))
    assert step["env"]["REQUIRE_DOCKER_TESTS"] == "1"


def test_uv_and_python_versions_match_the_image() -> None:
    doc = load(CI)
    assert doc["env"]["UV_VERSION"] == dockerfile_uv_version()
    python = (REPO_ROOT / ".python-version").read_text().strip()
    for _, step in steps(doc):
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
            assert step["with"]["python-version"] == python
            assert step["with"]["version"] == "${{ env.UV_VERSION }}"


def test_no_workflow_sets_the_engine_url_for_ci_tests() -> None:
    assert not sets_engine_url(load(CI), "test")


# --- each rule bites -----------------------------------------------------------------------

SAMPLE: dict[str, Any] = {
    "on": {"pull_request": None},
    "permissions": {"contents": "read"},
    "jobs": {
        "a": {
            "steps": [
                {"uses": "actions/checkout@v7", "with": {"persist-credentials": False}},
                {"uses": "./local-action"},
                {"uses": "docker://alpine:3"},
                {"uses": "astral-sh/setup-uv@v10.3.2", "with": {"enable-cache": True}},
            ]
        },
        "b": {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]},
        "gate": {"needs": ["a", "b"], "steps": []},
    },
}


def sample() -> dict[str, Any]:
    return copy.deepcopy(SAMPLE)


def test_sample_passes_every_rule() -> None:
    doc = sample()
    assert unpinned_uses(doc) == []
    assert floating_downloads(doc) == []
    assert not missing_permissions(doc)
    assert ungated_jobs(doc) == []
    assert write_permissions(doc) == []
    assert checkouts_persisting_credentials(doc) == []
    assert setup_uv_without_cache(doc) == []
    assert not sets_engine_url(doc, "b")


def test_unpinned_uses_bites() -> None:
    doc = sample()
    doc["jobs"]["a"]["steps"].append({"uses": "foo/bar"})
    doc["jobs"]["c"] = {"uses": "org/repo/.github/workflows/x.yml"}
    assert unpinned_uses(doc) == ["org/repo/.github/workflows/x.yml", "foo/bar"]


@pytest.mark.parametrize(
    "value", ["latest", " latest ", "https://github.com/x/y/releases/latest/download/z.tgz"]
)
def test_floating_download_bites(value: str) -> None:
    doc = sample()
    doc["jobs"]["a"]["steps"].append({"uses": "x/y@v1", "with": {"version": value}})
    assert floating_downloads(doc) == [value]


def test_missing_permissions_bites() -> None:
    doc = sample()
    del doc["permissions"]
    assert missing_permissions(doc)


def test_ungated_job_bites() -> None:
    doc = sample()
    doc["jobs"]["c"] = {"steps": []}
    assert ungated_jobs(doc) == ["c"]


def test_write_permission_bites() -> None:
    doc = sample()
    doc["jobs"]["a"]["permissions"] = {"contents": "write"}
    assert write_permissions(doc) == ["{'contents': 'write'}"]


def test_persisted_credentials_bite() -> None:
    doc = sample()
    doc["jobs"]["a"]["steps"][0]["with"] = {}
    assert checkouts_persisting_credentials(doc) == ["a"]


def test_setup_uv_without_cache_bites() -> None:
    doc = sample()
    doc["jobs"]["a"]["steps"][3]["with"] = {}
    assert setup_uv_without_cache(doc) == ["a"]


@pytest.mark.parametrize(
    "where",
    ["workflow", "job", "step", "github_env", "empty"],
)
def test_engine_url_guard_bites(where: str) -> None:
    doc = sample()
    job = doc["jobs"]["b"]
    if where == "workflow":
        doc["env"] = {"CONSOLE_ENGINE_URL": "http://x"}
    elif where == "job":
        job["env"] = {"CONSOLE_ENGINE_URL": "http://x"}
    elif where == "step":
        job["steps"][0]["env"] = {"CONSOLE_ENGINE_URL": "http://x"}
    elif where == "github_env":
        job["steps"].append({"run": 'echo "CONSOLE_ENGINE_URL=http://x" >> "$GITHUB_ENV"'})
    else:
        job["env"] = {"CONSOLE_ENGINE_URL": ""}
    assert sets_engine_url(doc, "b")

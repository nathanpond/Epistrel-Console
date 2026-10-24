"""Guards over .github/workflows/*.yml: pinned actions, no floating downloads, least privilege.

Each rule is a pure function over parsed YAML, checked against the real files and against an
in-memory bad sample, so every rule is seen to fail when it should. Whether the pinned versions
are current is Dependabot's job, not this file's.
"""

import copy
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests import workflow_steps
from tests.workflow_steps import StepRun, run_step

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


# A mypy flag that turns a strict check off, or points mypy at other settings.
MYPY_WEAKENING_FLAG = re.compile(
    r"--(?:no-(?:strict|warn|check|disallow)\S*|allow-\S+|disable-error-code\S*"
    r"|ignore-missing-imports|follow-imports\S*|config-file\S*)"
)


def mypy_weakenings(ci: dict[str, Any], pyproject: dict[str, Any]) -> list[str]:
    """How the gate's mypy is less than ``strict = true`` with every check on (#110).

    In the workflow: any mypy command carrying a weakening flag. In ``[tool.mypy]`` and each
    ``[[tool.mypy.overrides]]``: ``strict`` off, an ``ignore_*``/``allow_*`` turned on,
    ``disable_error_code``, or a ``disallow_*``/``warn_*``/``check_*``/``strict_*`` turned off.
    """
    found = []
    for _, step in steps(ci):
        run = str(step.get("run", ""))
        if "mypy" in run:
            found += [f"ci: {flag}" for flag in MYPY_WEAKENING_FLAG.findall(run)]
    mypy = (pyproject.get("tool") or {}).get("mypy") or {}
    if mypy.get("strict") is not True:
        found.append("pyproject: strict is not true")
    sections = [("[tool.mypy]", mypy)]
    sections += [(f"override {o.get('module')}", o) for o in mypy.get("overrides") or []]
    for where, section in sections:
        for key, value in section.items():
            on = key.startswith(("ignore_", "allow_")) and value is True
            off = key.startswith(("disallow_", "warn_", "check_", "strict_")) and value is False
            if on or off or key == "disable_error_code":
                found.append(f"pyproject {where}: {key} = {value!r}")
    return found


def load_pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_mypy_is_not_weakened() -> None:
    assert mypy_weakenings(load(CI), load_pyproject()) == []


@pytest.mark.parametrize(
    "flag",
    [
        "--disable-error-code=no-untyped-def",
        "--allow-untyped-defs",
        "--no-strict-optional",
        "--no-warn-unused-ignores",
        "--ignore-missing-imports",
        "--follow-imports=skip",
        "--config-file=lax.toml",
    ],
)
def test_mypy_flag_weakening_bites(flag: str) -> None:
    ci = copy.deepcopy(load(CI))
    types = jobs(ci)["types"]["steps"]
    index = next(i for i, s in enumerate(types) if s.get("run") == "uv run mypy")
    types[index]["run"] = f"uv run mypy {flag}"
    assert mypy_weakenings(ci, load_pyproject()) == [f"ci: {flag}"]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"strict": False}, "pyproject: strict is not true"),
        ({"disable_error_code": ["no-untyped-def"]}, "disable_error_code"),
        ({"warn_unused_ignores": False}, "warn_unused_ignores = False"),
        ({"allow_untyped_defs": True}, "allow_untyped_defs = True"),
        ({"overrides": [{"module": "tests.*", "ignore_errors": True}]}, "ignore_errors = True"),
        (
            {"overrides": [{"module": "x", "disable_error_code": ["attr-defined"]}]},
            "override x: disable_error_code",
        ),
        (
            {"overrides": [{"module": "y", "ignore_missing_imports": True}]},
            "ignore_missing_imports = True",
        ),
    ],
)
def test_pyproject_mypy_weakening_bites(change: dict[str, Any], expected: str) -> None:
    pyproject = copy.deepcopy(load_pyproject())
    pyproject["tool"]["mypy"].update(change)
    found = mypy_weakenings(load(CI), pyproject)
    assert len(found) == 1, found
    assert expected in found[0]


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


# --- the live Engine smoke (#36) ------------------------------------------------------------

ENGINE_UP = REPO_ROOT / ".github" / "scripts" / "engine-up.sh"
LIVE_ON = "github.event_name == 'pull_request' || inputs.live_smoke"


def engine_env_vars(script: str) -> set[str]:
    return set(re.findall(r"-e (EPISTREL_[A-Z_]+)=", script))


def test_ci_workflow_call_inputs_and_outputs() -> None:
    call = triggers(load(CI))["workflow_call"]
    assert call["inputs"]["live_smoke"] == {
        "description": call["inputs"]["live_smoke"]["description"],
        "type": "boolean",
        "required": False,
        "default": True,
    }
    assert call["inputs"]["engine_image"]["default"] == "ghcr.io/nathanpond/epistrel:edge"
    assert call["outputs"]["engine_digest"]["value"] == (
        "${{ jobs.engine-smoke.outputs.engine_digest }}"
    )
    assert call["outputs"]["engine_version"]["value"] == (
        "${{ jobs.engine-smoke.outputs.engine_version }}"
    )


def test_engine_smoke_runs_on_every_pr_and_is_gated() -> None:
    doc = load(CI)
    smoke = jobs(doc)["engine-smoke"]
    # Never `inputs.live_smoke != false`: on a PR inputs is null, and null == false in Actions.
    assert LIVE_ON in smoke["if"]
    assert LIVE_ON in doc["env"]["LIVE_SMOKE"]
    assert "engine-smoke" in jobs(doc)["gate"]["needs"]
    runs = [str(s.get("run", "")) for s in smoke["steps"]]
    assert "uv run pytest -m engine_live" in runs
    assert runs.index(".github/scripts/engine-up.sh wait") < runs.index(
        "uv run pytest -m engine_live"
    )
    assert smoke["env"]["ENGINE_LIVE_URL"] == "http://127.0.0.1:8000"
    assert smoke["services"]["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert "pg_isready" in smoke["services"]["postgres"]["options"]


def test_gate_uses_the_script_with_every_result() -> None:
    gate = jobs(load(CI))["gate"]
    step = gate["steps"][-1]
    assert step["run"] == ".github/scripts/gate.sh"
    assert set(step["env"]) == {"LINT", "TYPES", "TEST", "DOCKER", "ENGINE_SMOKE"}


def test_docker_job_drops_engine_edge_only_without_live_smoke() -> None:
    docker = jobs(load(CI))["docker"]
    step = next(s for s in docker["steps"] if "pytest -m" in str(s.get("run", "")))
    assert step["env"]["MARKERS"] == (
        "${{ env.LIVE_SMOKE == 'true' && 'docker' || 'docker and not engine_edge' }}"
    )
    assert step["run"] == 'uv run pytest -m "$MARKERS"'


def test_engine_gets_exactly_its_four_variables() -> None:
    assert engine_env_vars(ENGINE_UP.read_text()) == {
        "EPISTREL_DATABASE_URL",
        "EPISTREL_LOG_LEVEL",
        "EPISTREL_SERVICE_CREDENTIALS",
        "EPISTREL_CONFIG_KEY",
    }


def test_engine_variable_rule_bites() -> None:
    script = ENGINE_UP.read_text() + "\n    -e EPISTREL_MODEL=x \\\n"
    assert "EPISTREL_MODEL" in engine_env_vars(script)


# --- publishing edge (#37) ------------------------------------------------------------------

PUBLISH_EDGE = WORKFLOWS / "publish-edge.yml"


def gate_bypasses(doc: dict[str, Any], gated: str = "publish") -> list[str]:
    """Problems if ``gated`` could run without ``ci`` (= ci.yml) succeeding first."""
    problems = []
    ci = jobs(doc).get("ci") or {}
    if ci.get("uses") != "./.github/workflows/ci.yml":
        problems.append("ci does not use ci.yml")
    job = jobs(doc).get(gated) or {}
    needs = job.get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    if "ci" not in needs:
        problems.append(f"{gated} does not need ci")
    # No `if` means the implicit success(): every need succeeded. An explicit one must say so.
    if "if" in job and "needs.ci.result == 'success'" not in str(job["if"]):
        problems.append(f"{gated} does not require ci to succeed")
    return problems


def test_publish_edge_triggers_and_concurrency() -> None:
    doc = load(PUBLISH_EDGE)
    on = triggers(doc)
    assert on["push"] == {"branches": ["main"]}
    assert on["workflow_dispatch"]["inputs"]["ref"]["default"] == "main"
    assert doc["concurrency"] == {"group": "publish-edge", "cancel-in-progress": False}
    assert doc["permissions"] == {"contents": "read"}


def test_publish_edge_runs_the_gate_without_live_smoke() -> None:
    doc = load(PUBLISH_EDGE)
    assert gate_bypasses(doc) == []
    assert jobs(doc)["ci"]["with"]["live_smoke"] is False
    assert jobs(doc)["ci"]["with"]["ref"] == "${{ needs.resolve.outputs.sha }}"


def test_publishing_without_the_gate_fails_the_rule() -> None:
    doc = copy.deepcopy(load(PUBLISH_EDGE))
    doc["jobs"]["ci"]["uses"] = "./.github/workflows/other.yml"
    doc["jobs"]["publish"]["needs"] = ["resolve"]
    doc["jobs"]["publish"]["if"] = "always()"
    assert gate_bypasses(doc) == [
        "ci does not use ci.yml",
        "publish does not need ci",
        "publish does not require ci to succeed",
    ]


def test_publish_edge_dispatch_only_from_main() -> None:
    first = jobs(load(PUBLISH_EDGE))["resolve"]["steps"][0]
    assert first["if"] == (
        "github.event_name == 'workflow_dispatch' && github.ref != 'refs/heads/main'"
    )
    assert "exit 1" in first["run"]


def test_publish_edge_pushes_by_digest_and_tags_after_the_smoke() -> None:
    publish = jobs(load(PUBLISH_EDGE))["publish"]
    names = [s.get("id") or s.get("name") or s.get("uses") for s in publish["steps"]]
    assert names.index("build") < names.index("smoke") < names.index("tag")
    build = next(s for s in publish["steps"] if s.get("id") == "build")
    assert "push-by-digest=true" in build["with"]["outputs"]
    assert "tags" not in build["with"]
    assert build["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert build["with"]["provenance"] is False


def test_publish_edge_retags_only_existing_images() -> None:
    doc = load(PUBLISH_EDGE)
    assert jobs(doc)["retag"]["if"] == "needs.resolve.outputs.existing_digest != ''"
    assert jobs(doc)["ci"]["if"] == "needs.resolve.outputs.existing_digest == ''"


TAGGING_STEPS = ("build", "smoke", "tag")


def tagging_bypasses(job: dict[str, Any]) -> list[str]:
    """Ways a tag could move without the image being built and passing its smoke first (#105).

    The ``tag`` step runs on the implicit ``success()``, so the build and smoke steps must exist
    in that order, never be skipped (no ``if``), and never swallow a failure
    (``continue-on-error``). The build pushes by digest only, so a failure before the tag moves
    nothing.
    """
    problems = []
    ids = [s.get("id") for s in job.get("steps") or []]
    by_id = {s["id"]: s for s in job.get("steps") or [] if s.get("id") in TAGGING_STEPS}
    missing = [i for i in TAGGING_STEPS if i not in by_id]
    if missing:
        return [f"no {i} step" for i in missing]
    if not ids.index("build") < ids.index("smoke") < ids.index("tag"):
        problems.append("steps are not build, then smoke, then tag")
    build = by_id["build"].get("with") or {}
    if "tags" in build or "push-by-digest=true" not in str(build.get("outputs", "")):
        problems.append("build does not push by digest only")
    for i in TAGGING_STEPS:
        if "if" in by_id[i]:
            problems.append(f"{i} has an if")
        if by_id[i].get("continue-on-error", False) is not False:
            problems.append(f"{i} continues on error")
    if job.get("continue-on-error", False) is not False:
        problems.append("the job continues on error")
    return problems


def test_publish_edge_tags_only_after_a_passing_smoke() -> None:
    assert tagging_bypasses(jobs(load(PUBLISH_EDGE))["publish"]) == []


def test_tagging_rule_bites() -> None:
    doc = copy.deepcopy(load(PUBLISH_EDGE))
    job = doc["jobs"]["publish"]
    steps_by_id = {s.get("id"): s for s in job["steps"]}
    steps_by_id["tag"]["if"] = "always()"
    steps_by_id["smoke"]["continue-on-error"] = True
    steps_by_id["build"]["with"]["tags"] = "${{ steps.meta.outputs.tags }}"
    assert tagging_bypasses(job) == [
        "build does not push by digest only",
        "smoke continues on error",
        "tag has an if",
    ]
    tag = job["steps"].pop(job["steps"].index(steps_by_id["tag"]))
    job["steps"].insert(0, tag)
    assert "steps are not build, then smoke, then tag" in tagging_bypasses(job)
    del job["steps"][0]
    assert tagging_bypasses(job) == ["no tag step"]


# The `ref` step resolves what edge is built from: only main's history, never a short SHA.

REF_STEP = workflow_steps.step(jobs(load(PUBLISH_EDGE))["resolve"], "ref")["run"]


def git(repo: Path, *args: str) -> str:
    git_bin = shutil.which("git")
    assert git_bin is not None
    done = subprocess.run(  # noqa: S603
        [git_bin, "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return done.stdout.strip()


@pytest.fixture(scope="module")
def edge_repo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A clone-like repo: ``main`` (= ``origin/main``) at A, and ``feature`` at B off main."""
    repo = tmp_path_factory.mktemp("edge-repo")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "A")
    main = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", main)
    git(repo, "switch", "-q", "-c", "feature")
    git(repo, "commit", "-q", "--allow-empty", "-m", "B")
    feature = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "main")
    return {"repo": repo, "main": main, "feature": feature}


def resolve_ref(script: str, repo: Path, event: str, requested: str, pushed: str) -> StepRun:
    env = {"EVENT": event, "REQUESTED": requested, "PUSHED": pushed}
    return run_step(script, repo, env=env)


def edge_ref_refusals(script: str, edge_repo: dict[str, Any]) -> dict[str, str]:
    """For each dispatch the ref step must refuse, the refusal it printed ('' if it ran on)."""
    main, feature, repo = edge_repo["main"], edge_repo["feature"], edge_repo["repo"]
    cases = {"off-main sha": feature, "off-main branch": "feature", "short sha": main[:7]}
    found = {}
    for case, requested in cases.items():
        result = resolve_ref(script, repo, "workflow_dispatch", requested, main)
        found[case] = result.output if result.rc != 0 else ""
    return found


def test_edge_is_built_only_from_mains_history(edge_repo: dict[str, Any]) -> None:
    refusals = edge_ref_refusals(REF_STEP, edge_repo)
    assert "not reachable from origin/main" in refusals["off-main sha"]
    assert "not reachable from origin/main" in refusals["off-main branch"]
    assert "looks like a short SHA" in refusals["short sha"]


@pytest.mark.parametrize(
    ("event", "requested"),
    [
        ("workflow_dispatch", "main"),
        ("workflow_dispatch", "<full>"),
        ("push", ""),
        ("push", "feature"),
    ],
)
def test_edge_ref_resolves_main(edge_repo: dict[str, Any], event: str, requested: str) -> None:
    main = edge_repo["main"]
    requested = main if requested == "<full>" else requested
    result = resolve_ref(REF_STEP, edge_repo["repo"], event, requested, main)
    assert result.rc == 0, result.output
    # A push always builds the pushed commit, whatever `inputs.ref` holds.
    assert result.outputs == {"sha": main, "short": main[:7]}


@pytest.mark.parametrize(
    ("check", "disabled", "case"),
    [
        ('git merge-base --is-ancestor "$sha" origin/main', "true", "off-main sha"),
        ("'^[0-9a-f]{7,39}$'", "'^never$'", "short sha"),
    ],
)
def test_edge_ref_refusals_bite(
    edge_repo: dict[str, Any], check: str, disabled: str, case: str
) -> None:
    # Disable one check in the step and the refusal it owns goes away.
    assert REF_STEP.count(check) == 1
    mutated = REF_STEP.replace(check, disabled)
    assert edge_ref_refusals(mutated, edge_repo)[case] == ""


# --- the daily drift check (#40) ------------------------------------------------------------

ENGINE_DRIFT = WORKFLOWS / "engine-drift.yml"


def blocking_triggers(doc: dict[str, Any]) -> list[str]:
    return sorted(set(triggers(doc)) & {"pull_request", "pull_request_target", "push"})


def runs_schema_check(doc: dict[str, Any]) -> bool:
    return any("engine_schema --check" in str(step.get("run", "")) for _, step in steps(doc))


def test_drift_check_never_runs_on_prs_or_pushes() -> None:
    doc = load(ENGINE_DRIFT)
    assert blocking_triggers(doc) == []
    assert triggers(doc)["schedule"] == [{"cron": "17 6 * * *"}]
    assert "workflow_dispatch" in triggers(doc)
    assert doc["permissions"] == {"contents": "read"}
    assert jobs(doc)["drift"]["permissions"] == {"contents": "read", "issues": "write"}
    assert doc["concurrency"] == {"group": "engine-drift", "cancel-in-progress": False}


def test_the_pr_gate_never_checks_drift() -> None:
    assert not runs_schema_check(load(CI))
    assert runs_schema_check(load(ENGINE_DRIFT))


def test_drift_rules_bite() -> None:
    doc = copy.deepcopy(load(ENGINE_DRIFT))
    doc["on"] = {"pull_request": None, "push": {"branches": ["main"]}}
    assert blocking_triggers(doc) == ["pull_request", "push"]
    ci = copy.deepcopy(load(CI))
    ci["jobs"]["test"]["steps"].append({"run": "uv run python manage.py engine_schema --check"})
    assert runs_schema_check(ci)


def test_drift_issue_step_runs_only_with_a_verdict() -> None:
    drift = jobs(load(ENGINE_DRIFT))["drift"]
    step = next(s for s in drift["steps"] if "tools.drift_issue" in str(s.get("run", "")))
    assert step["if"] == (
        "always() && steps.wait.outcome == 'success' && steps.check.outcome == 'success'"
    )
    assert step["env"]["CODE"] == "${{ steps.check.outputs.code }}"

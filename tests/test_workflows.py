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


# A mypy flag that turns a strict check off, or points mypy at other settings or files.
MYPY_WEAKENING_FLAG = re.compile(
    r"--(?:no-(?:strict|warn|check|disallow|extra|local)\S*|allow-\S+|implicit-\S+"
    r"|disable-error-code\S*|ignore-missing-imports|follow-\S+|config-file\S*|exclude\S*)"
)
# What `[tool.mypy]` may hold (#116): anything else, or another value, is a weakening. A check
# that is part of --strict or stricter still may be set, but only to true.
MYPY_FILES = ("epistrel_console", "console", "tools", "tests", "manage.py")
MYPY_STRICTER = re.compile(
    r"(?:disallow|warn|check|strict)_\w+|extra_checks|local_partial_types|strict"
)
# An inline config comment, or a whole-line `# type: ignore` (above the code, it skips the file),
# with or without a trailing `# reason` (#120) or an empty code list (`[]`, `[ , ]`: mypy 2.4 drops
# empty codes, leaving a whole-file ignore, #122). A whole-line `# type: ignore[code]` is an error.
MYPY_INLINE = re.compile(
    r"^\s*#\s*(?:mypy\s*:|type\s*:\s*ignore\s*(?:\[[\s,]*\]\s*)?(?:#.*)?$)", re.MULTILINE
)
# typing's no-type-check decorator turns checking off for a function or class (#122). Its name
# is spelled in pieces here so this file, which is scanned too, holds no match.
MYPY_NO_CHECK = re.compile("no_type" + "_check")
# Directories the source scan skips: dot-dirs (.venv, .git, caches), bytecode, node packages.
MYPY_SCAN_SKIP = re.compile(r"^(?:\..*|__pycache__|node_modules)$")
# Config files mypy reads in place of `[tool.mypy]` when present (#120).
MYPY_SHADOWING_FILES = ("mypy.ini", ".mypy.ini")


def mypy_setting_problem(key: str, value: Any, top: bool) -> str | None:
    if MYPY_STRICTER.fullmatch(key):
        return None if value is True else "must be true"
    if top and key == "files":
        missing = [f for f in MYPY_FILES if f not in (value or [])]
        return f"misses {missing}" if missing else None
    if top and key == "plugins":
        return None if value == ["mypy_django_plugin.main"] else "is not the django plugin"
    if key == "enable_error_code" or (not top and key == "module"):
        return None
    return "is not an allowed setting"


def mypy_weakenings(ci: dict[str, Any], pyproject: dict[str, Any]) -> list[str]:
    """How the gate's mypy is less than ``strict = true`` with every check on (#110, #116).

    In the workflow: any mypy command carrying a weakening flag. In ``[tool.mypy]`` and each
    ``[[tool.mypy.overrides]]``: ``strict`` not true, or any setting outside the allow-list
    (stricter checks set to true, the full ``files`` list, the django plugin,
    ``enable_error_code``).
    """
    found = []
    for _, step in steps(ci):
        run = str(step.get("run", ""))
        if "mypy" in run:
            found += [f"ci: {flag}" for flag in MYPY_WEAKENING_FLAG.findall(run)]
    mypy = (pyproject.get("tool") or {}).get("mypy") or {}
    if mypy.get("strict") is not True:
        found.append("pyproject: strict is not true")
    sections = [("[tool.mypy]", {k: v for k, v in mypy.items() if k != "overrides"}, True)]
    sections += [(f"override {o.get('module')}", o, False) for o in mypy.get("overrides") or []]
    for where, section, top in sections:
        for key, value in section.items():
            problem = mypy_setting_problem(key, value, top)
            if problem and not (top and key == "strict"):
                found.append(f"pyproject {where}: {key} = {value!r} {problem}")
    return found


def mypy_inline_weakenings(sources: dict[str, str]) -> list[str]:
    """``path:line`` of each inline mypy config comment, whole-line ``# type: ignore`` or use of
    the no-type-check decorator, plus every ``.pyi`` (mypy checks a stub in place of its module,
    #122)."""
    found = [f"{path}: stub" for path in sources if path.endswith(".pyi")]
    return found + [
        f"{path}:{text.count(chr(10), 0, m.start()) + 1}"
        for path, text in sources.items()
        for pattern in (MYPY_INLINE, MYPY_NO_CHECK)
        for m in pattern.finditer(text)
    ]


def mypy_config_shadows(root: Path) -> list[str]:
    """Config files in ``root`` that mypy could read instead of pyproject's ``[tool.mypy]``."""
    found = [name for name in MYPY_SHADOWING_FILES if (root / name).exists()]
    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file() and re.search(
        r"^\s*\[mypy", setup_cfg.read_text(encoding="utf-8"), re.MULTILINE
    ):
        found.append("setup.cfg [mypy]")
    return found


def mypy_sources(root: Path = REPO_ROOT) -> dict[str, str]:
    """Every first-party ``.py`` and ``.pyi`` under ``root``, not only ``MYPY_FILES``: mypy follows
    imports into any package here (#122). Read as utf-8-sig, so a BOM cannot hide line 1."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not MYPY_SCAN_SKIP.match(d))
        files += [Path(dirpath) / n for n in sorted(filenames) if n.endswith((".py", ".pyi"))]
    return {str(f.relative_to(root)): f.read_text(encoding="utf-8-sig") for f in files}


def load_pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_mypy_is_not_weakened() -> None:
    assert mypy_weakenings(load(CI), load_pyproject()) == []
    assert mypy_inline_weakenings(mypy_sources()) == []
    assert len(mypy_sources()) > 20
    assert "tests/test_workflows.py" in mypy_sources()
    assert mypy_config_shadows(REPO_ROOT) == []


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("mypy.ini", "[mypy]\nstrict = False\n", ["mypy.ini"]),
        (".mypy.ini", "[mypy]\n", [".mypy.ini"]),
        ("setup.cfg", "[metadata]\nname = x\n\n[mypy]\nstrict = False\n", ["setup.cfg [mypy]"]),
        ("setup.cfg", "[mypy-console.*]\nignore_errors = True\n", ["setup.cfg [mypy]"]),
        ("setup.cfg", "[metadata]\nname = x\n", []),
    ],
)
def test_mypy_config_shadow_bites(
    tmp_path: Path, name: str, text: str, expected: list[str]
) -> None:
    assert mypy_config_shadows(tmp_path) == []
    (tmp_path / name).write_text(text, encoding="utf-8")
    assert mypy_config_shadows(tmp_path) == expected


@pytest.mark.parametrize(
    "line",
    [
        "# mypy: ignore-errors",
        "#mypy: disable-error-code=attr-defined",
        "# type: ignore",
        "# type: ignore  # legacy",
        "#type:ignore#x",
        # Each of these was checked to skip the whole file under mypy 2.4 (#122).
        "# type: ignore[]",
        "# type: ignore[ , ]",
        "# type: ignore[,]",
        "# type: ignore[,,]",
        "# type: ignore[ ]",
        "# type: ignore[\t]",
        "# type: ignore []",
        "#type:ignore[]",
        "\t# type: ignore[]",
        "# type: ignore[]  # why",
        "@typing." + "no_type" + "_check",
        "from typing import " + "no_type" + "_check",
    ],
)
def test_inline_mypy_weakening_bites(line: str) -> None:
    sources = {"console/x.py": f'"""Doc."""\n{line}\nimport os\n', "console/y.py": "x = 1\n"}
    assert mypy_inline_weakenings(sources) == ["console/x.py:2"]
    # A `# type: ignore[code]` on a line of code is a normal, checked (warn_unused_ignores) ignore.
    assert mypy_inline_weakenings({"a.py": "x: int = y  # type: ignore[name-defined]\n"}) == []


@pytest.mark.parametrize("line", ["# type: ignore[misc]", "# type: ignore [misc]"])
def test_whole_line_ignore_with_a_code_is_allowed(line: str) -> None:
    # mypy reports a module-level `ignore[code]` as an error rather than skipping the file.
    assert mypy_inline_weakenings({"console/x.py": f"{line}\nimport os\n"}) == []


def write_source(root: Path, rel: str, data: bytes) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)


def test_bom_does_not_hide_a_line_one_ignore(tmp_path: Path) -> None:
    write_source(tmp_path, "console/x.py", b"\xef\xbb\xbf# type: ignore\nimport os\n")
    assert mypy_inline_weakenings(mypy_sources(tmp_path)) == ["console/x.py:1"]


def test_a_stub_is_refused(tmp_path: Path) -> None:
    write_source(tmp_path, "console/apps.py", b"x = 1\n")
    write_source(tmp_path, "console/apps.pyi", b"x: int\n")
    assert mypy_inline_weakenings(mypy_sources(tmp_path)) == ["console/apps.pyi: stub"]


def test_packages_outside_mypy_files_are_scanned(tmp_path: Path) -> None:
    write_source(tmp_path, "helperpkg/__init__.py", b"# type: ignore\n")
    write_source(tmp_path, "top.py", b"# mypy: ignore-errors\n")
    assert sorted(mypy_inline_weakenings(mypy_sources(tmp_path))) == [
        "helperpkg/__init__.py:1",
        "top.py:1",
    ]


def test_scan_skips_virtualenvs_caches_and_node_packages(tmp_path: Path) -> None:
    for skipped in (".venv/lib/x.py", ".git/x.py", "a/__pycache__/x.py", "node_modules/x.py"):
        write_source(tmp_path, skipped, b"# type: ignore\n")
    write_source(tmp_path, "console/x.py", b"x = 1\n")
    assert list(mypy_sources(tmp_path)) == ["console/x.py"]


@pytest.mark.parametrize(
    "flag",
    [
        "--disable-error-code=no-untyped-def",
        "--allow-untyped-defs",
        "--no-strict-optional",
        "--no-warn-unused-ignores",
        "--ignore-missing-imports",
        "--follow-imports=skip",
        "--follow-untyped-imports",
        "--implicit-reexport",
        "--no-extra-checks",
        "--exclude=console/",
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
        ({"follow_imports": "skip"}, "follow_imports = 'skip' is not an allowed setting"),
        ({"follow_untyped_imports": True}, "follow_untyped_imports = True is not an allowed"),
        ({"implicit_reexport": True}, "implicit_reexport = True is not an allowed setting"),
        ({"implicit_optional": True}, "implicit_optional = True is not an allowed setting"),
        ({"extra_checks": False}, "extra_checks = False must be true"),
        ({"local_partial_types": False}, "local_partial_types = False must be true"),
        ({"exclude": ["console/"]}, "exclude = ['console/'] is not an allowed setting"),
        ({"files": ["epistrel_console", "tests"]}, "misses ['console', 'tools', 'manage.py']"),
        ({"plugins": []}, "plugins = [] is not the django plugin"),
        (
            {"overrides": [{"module": "z", "follow_imports": "skip"}]},
            "override z: follow_imports = 'skip' is not an allowed setting",
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
    for path in workflow_files():
        for _, step in steps(load(path)):
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                assert step["with"]["python-version"] == python, path.name
                assert step["with"]["version"] == "${{ env.UV_VERSION }}", path.name
                assert load(path)["env"]["UV_VERSION"] == dockerfile_uv_version(), path.name


def test_requires_python_and_the_image_match_the_pin() -> None:
    # One Python everywhere (#116): .python-version, pyproject, the Dockerfile's stages.
    python = (REPO_ROOT / ".python-version").read_text().strip()
    assert load_pyproject()["project"]["requires-python"] == f">={python}"
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    bases = re.findall(r"^FROM\s+python:(\S+?)-slim\b", dockerfile, re.MULTILINE)
    assert bases, dockerfile
    assert set(bases) == {python}, bases


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
    """Every EPISTREL_* the script can pass: its ENGINE_SETTINGS entries and any literal `-e`."""
    table = re.search(r"^ENGINE_SETTINGS=\((.*?)^\)", script, re.M | re.S)
    entries = re.findall(r'^\s*"(EPISTREL_[A-Z_]+)=', table.group(1), re.M) if table else []
    return set(entries) | set(re.findall(r"-e (EPISTREL_[A-Z_]+)=", script))


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
    assert set(step["env"]) == {
        "LINT",
        "TYPES",
        "TEST",
        "DOCKER",
        "ENGINE_SMOKE",
        "ENGINE_RELEASE_SMOKE",
    }
    assert step["env"]["ENGINE_RELEASE_SMOKE"] == "${{ needs.engine-release-smoke.result }}"


def test_docker_job_drops_engine_edge_only_without_live_smoke() -> None:
    docker = jobs(load(CI))["docker"]
    step = next(s for s in docker["steps"] if "pytest -m" in str(s.get("run", "")))
    assert step["env"]["MARKERS"] == (
        "${{ env.LIVE_SMOKE == 'true' && 'docker' || 'docker and not engine_edge' }}"
    )
    assert step["run"] == 'uv run pytest -m "$MARKERS"'


# The newest Engine release (0.1.1) has no service auth and no /stories (#42).
RELEASE_SMOKE_RUN = 'uv run pytest -m "engine_live and not engine_auth"'
CI_CREDENTIAL = "ci-only-credential-not-a-secret-0000"


def test_engine_up_passes_the_ci_credential_and_hands_it_to_the_smoke() -> None:
    script = ENGINE_UP.read_text()
    assert f"SERVICE_KEY={CI_CREDENTIAL}  # CI-only, not a secret" in script
    assert len(CI_CREDENTIAL) >= 32
    assert '"EPISTREL_SERVICE_CREDENTIALS=$SERVICE_KEY"' in script
    assert 'echo "ENGINE_LIVE_SERVICE_KEY=$SERVICE_KEY" >> "${GITHUB_ENV:-/dev/null}"' in script


@pytest.mark.parametrize("workflow", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_the_live_service_key_has_one_source(workflow: Path) -> None:
    # Only engine-up.sh sets it; drift's engine_schema --check reads the exempt /openapi.json and
    # sets no Console key either.
    text = workflow.read_text()
    assert "ENGINE_LIVE_SERVICE_KEY" not in text
    assert CI_CREDENTIAL not in text
    if workflow.name == "engine-drift.yml":
        assert "CONSOLE_ENGINE_SERVICE_KEY" not in text


def test_engine_may_get_only_these_four_variables() -> None:
    assert engine_env_vars(ENGINE_UP.read_text()) == {
        "EPISTREL_DATABASE_URL",
        "EPISTREL_LOG_LEVEL",
        "EPISTREL_SERVICE_CREDENTIALS",
        "EPISTREL_CONFIG_KEY",
    }


def test_engine_variable_rule_bites() -> None:
    script = ENGINE_UP.read_text() + "\n    -e EPISTREL_MODEL=x \\\n"
    assert "EPISTREL_MODEL" in engine_env_vars(script)
    table = ENGINE_UP.read_text().replace(
        '  "EPISTREL_LOG_LEVEL=INFO"\n', '  "EPISTREL_LOG_LEVEL=INFO"\n  "EPISTREL_MODEL=x"\n'
    )
    assert "EPISTREL_MODEL" in engine_env_vars(table)


def test_engine_release_smoke_starts_the_newest_engine_release() -> None:
    doc = load(CI)
    job = jobs(doc)["engine-release-smoke"]
    smoke = jobs(doc)["engine-smoke"]
    assert job["if"] == smoke["if"]
    assert LIVE_ON in job["if"]
    assert "engine-release-smoke" in jobs(doc)["gate"]["needs"]
    assert job["services"] == smoke["services"]
    assert job["env"] == {"ENGINE_LIVE_URL": "http://127.0.0.1:8000"}
    assert job["permissions"] == {"contents": "read"}
    steps = job["steps"]
    runs = [str(s.get("run", "")) for s in steps]
    resolve = next(s for s in steps if s.get("name") == "newest Engine release")
    assert resolve["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert (
        "gh release view --repo nathanpond/Epistrel --json tagName --jq .tagName" in resolve["run"]
    )
    assert (
        'echo "ENGINE_IMAGE=ghcr.io/nathanpond/epistrel:${tag#v}" >> "$GITHUB_ENV"'
        in resolve["run"]
    )
    assert '[ -n "$tag" ] ||' in resolve["run"]
    order = [
        steps.index(resolve),
        runs.index(".github/scripts/engine-up.sh start"),
        runs.index(".github/scripts/engine-up.sh wait"),
        runs.index(".github/scripts/engine-up.sh identify"),
        runs.index(RELEASE_SMOKE_RUN),
    ]
    assert order == sorted(order)
    # Complement: only the release smoke leaves engine_auth out; edge runs every live test.
    assert "uv run pytest -m engine_live" not in runs
    assert RELEASE_SMOKE_RUN not in [str(s.get("run", "")) for s in smoke["steps"]]
    # The same tail as engine-smoke: summary always, Engine and Postgres logs on failure.
    assert [s for s in steps if s.get("name") in ("summary", "logs")] == [
        s for s in smoke["steps"] if s.get("name") in ("summary", "logs")
    ]
    start = next(s for s in steps if s.get("run") == ".github/scripts/engine-up.sh start")
    assert start["env"] == {"NETWORK": "${{ job.services.postgres.network }}"}
    # Its outputs are not the gate's: a release names the Engine engine-smoke tested.
    assert "outputs" not in job


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
    cases = {
        "off-main sha": feature,
        "off-main branch": "feature",
        "short sha": main[:7],
        "upper-case short sha": main[:7].upper(),
        "short sha with a suffix": main[:7] + "~0",
    }
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
    assert "looks like a short SHA" in refusals["upper-case short sha"]
    assert "looks like a short SHA" in refusals["short sha with a suffix"]


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
        ("'^[0-9a-f]{7,39}([~^].*)?$'", "'^never$'", "short sha"),
    ],
)
def test_edge_ref_refusals_bite(
    edge_repo: dict[str, Any], check: str, disabled: str, case: str
) -> None:
    # Disable one check in the step and the refusal it owns goes away.
    assert REF_STEP.count(check) == 1
    mutated = REF_STEP.replace(check, disabled)
    assert edge_ref_refusals(mutated, edge_repo)[case] == ""


# --- what the publish steps do (#112, #113) ------------------------------------------------

RELEASE = WORKFLOWS / "release.yml"
PUBLISHERS = [pytest.param(PUBLISH_EDGE, id="publish-edge"), pytest.param(RELEASE, id="release")]
DIGEST, OTHER_DIGEST = "sha256:" + "a" * 64, "sha256:" + "b" * 64
IMAGE = "ghcr.io/nathanpond/epistrel-console"


def publish_step(workflow: Path, key: str) -> str:
    script: str = workflow_steps.step(jobs(load(workflow))["publish"], key)["run"]
    return script


def manifest_stub(manifests: dict[str, str]) -> str:
    """``docker``: ``imagetools inspect <ref>`` prints ``{{json .Manifest}}`` for the refs in
    ``manifests`` and "not found" for any other; ``imagetools create``, ``run`` and ``logs``
    succeed.
    """
    cases = "".join(
        f"""    '{r}') echo '{{"digest": "{d}"}}'; exit 0 ;;\n""" for r, d in manifests.items()
    )
    return f"""
if [ "$1 $2 $3" = "buildx imagetools inspect" ]; then
  case "$4" in
{cases}  esac
  echo "ERROR: $4: not found" >&2; exit 1
fi
[ "$1 $2 $3" = "buildx imagetools create" ] && exit 0
case "$1" in run|logs) exit 0 ;; esac
"""


def smoke(tmp_path: Path, script: str, healthy: bool) -> StepRun:
    curl = "echo ok; exit 0" if healthy else "echo 'curl: (7) refused' >&2; exit 7"
    stubs = {"docker": manifest_stub({}), "curl": curl, "sleep": "exit 0"}
    return run_step(script, tmp_path, env={"DIGEST": DIGEST}, stubs=stubs)


@pytest.mark.parametrize("workflow", PUBLISHERS)
def test_smoke_fails_closed_on_the_built_digest(tmp_path: Path, workflow: Path) -> None:
    script = publish_step(workflow, "smoke")
    failed = smoke(tmp_path / "down", script, healthy=False)
    assert failed.rc != 0
    assert "did not answer /health with 200 in 60 s" in failed.output
    assert len(failed.called("curl")) == 30
    assert failed.called("docker", "logs") == [["docker", "logs", "console-smoke"]]
    passed = smoke(tmp_path / "up", script, healthy=True)
    assert passed.rc == 0, passed.output
    # The image that ran is the one just built, by digest: never a tag that may hold another.
    for result in (failed, passed):
        assert [c[-1] for c in result.called("docker", "run")] == [f"{IMAGE}@{DIGEST}"]


@pytest.mark.parametrize(
    "mutation",
    [
        ('in 60 s"\nexit 1', 'in 60 s"\nexit 0'),
        ("if curl -fsS", "if true || curl -fsS"),
        ('"$IMAGE@$DIGEST"', '"$IMAGE:edge"'),
    ],
)
def test_smoke_rule_bites(tmp_path: Path, mutation: tuple[str, str]) -> None:
    script = publish_step(PUBLISH_EDGE, "smoke")
    old, new = mutation
    assert script.count(old) == 1
    mutated = script.replace(old, new)
    failed = smoke(tmp_path / "down", mutated, healthy=False)
    ran = [c[-1] for c in failed.called("docker", "run")]
    assert failed.rc == 0 or ran != [f"{IMAGE}@{DIGEST}"]


def tag(tmp_path: Path, script: str, manifests: dict[str, str]) -> StepRun:
    env = {"DIGEST": DIGEST, "TAGS": f"{IMAGE}:edge\n{IMAGE}:sha-abc1234\n"}
    return run_step(script, tmp_path, env=env, stubs={"docker": manifest_stub(manifests)})


@pytest.mark.parametrize("workflow", PUBLISHERS)
def test_tag_moves_every_tag_and_checks_each_one(tmp_path: Path, workflow: Path) -> None:
    script = publish_step(workflow, "tag")
    tags = {f"{IMAGE}:edge": DIGEST, f"{IMAGE}:sha-abc1234": DIGEST}
    result = tag(tmp_path / "ok", script, tags)
    assert result.rc == 0, result.output
    assert result.called("docker", "buildx", "imagetools", "create") == [
        ["docker", "buildx", "imagetools", "create", "-t", f"{IMAGE}:edge",
         "-t", f"{IMAGE}:sha-abc1234", f"{IMAGE}@{DIGEST}"],
    ]  # fmt: skip
    wrong = tag(tmp_path / "wrong", script, {**tags, f"{IMAGE}:sha-abc1234": OTHER_DIGEST})
    assert wrong.rc == 1
    assert f"sha-abc1234 resolves to {OTHER_DIGEST}, not {DIGEST}" in wrong.output


def test_tag_post_condition_bites(tmp_path: Path) -> None:
    script = publish_step(PUBLISH_EDGE, "tag")
    old = '[ "$got" = "$DIGEST" ] ||'
    assert script.count(old) == 1
    tags = {f"{IMAGE}:edge": DIGEST, f"{IMAGE}:sha-abc1234": OTHER_DIGEST}
    assert tag(tmp_path, script.replace(old, "true ||"), tags).rc == 0


EXISTING_STEP = workflow_steps.step(jobs(load(PUBLISH_EDGE))["resolve"], "existing")["run"]


def existing(tmp_path: Path, script: str, manifests: dict[str, str]) -> StepRun:
    stubs = {"docker": manifest_stub(manifests)}
    return run_step(script, tmp_path, env={"SHORT": "abc1234"}, stubs=stubs)


def test_rollback_to_a_published_commit_takes_the_retag_path(tmp_path: Path) -> None:
    found = existing(tmp_path / "found", EXISTING_STEP, {f"{IMAGE}:sha-abc1234": DIGEST})
    assert found.rc == 0, found.output
    assert found.outputs == {"digest": DIGEST}
    missing = existing(tmp_path / "missing", EXISTING_STEP, {})
    assert missing.rc == 0, missing.output
    assert missing.outputs == {}
    assert "gate-and-build path" in missing.output


def test_existing_digest_rule_bites(tmp_path: Path) -> None:
    old = 'echo "digest=$digest" >> "$GITHUB_OUTPUT"'
    assert EXISTING_STEP.count(old) == 1
    mutated = EXISTING_STEP.replace(old, "true")
    result = existing(tmp_path, mutated, {f"{IMAGE}:sha-abc1234": DIGEST})
    assert result.outputs != {"digest": DIGEST}


def test_only_a_dispatch_looks_for_an_existing_image() -> None:
    # A push always gates and builds; a dispatch (edge rollback) re-tags when it can (#119).
    resolve = jobs(load(PUBLISH_EDGE))["resolve"]
    dispatch = "github.event_name == 'workflow_dispatch'"
    assert workflow_steps.step(resolve, "existing")["if"] == dispatch
    login = next(s for s in resolve["steps"] if str(s.get("uses", "")).startswith("docker/login"))
    assert login["if"] == dispatch
    assert resolve["outputs"]["existing_digest"] == "${{ steps.existing.outputs.digest }}"


# --- the re-tag path and the summary (#119) -------------------------------------------------

RETAG_JOB = jobs(load(PUBLISH_EDGE))["retag"]
RETAG_STEP: str = workflow_steps.step(RETAG_JOB, "Re-point edge")["run"]


def retag(tmp_path: Path, script: str, edge: str) -> StepRun:
    """Re-point edge at DIGEST, with the registry answering that edge is now at ``edge``."""
    stubs = {"docker": manifest_stub({f"{IMAGE}:edge": edge})}
    return run_step(script, tmp_path, env={"DIGEST": DIGEST}, stubs=stubs)


def test_retag_points_edge_at_the_existing_digest_and_checks_it(tmp_path: Path) -> None:
    assert RETAG_JOB["needs"] == "resolve"
    assert workflow_steps.step(RETAG_JOB, "Re-point edge")["env"] == {
        "DIGEST": "${{ needs.resolve.outputs.existing_digest }}"
    }
    ok = retag(tmp_path / "ok", RETAG_STEP, DIGEST)
    assert ok.rc == 0, ok.output
    assert ok.called("docker", "buildx", "imagetools", "create") == [
        ["docker", "buildx", "imagetools", "create", "-t", f"{IMAGE}:edge", f"{IMAGE}@{DIGEST}"],
    ]
    assert ok.called("docker", "buildx", "imagetools", "inspect")[0][4] == f"{IMAGE}:edge"
    # Nothing is built or run on this path: it only moves a tag.
    assert [c[:3] for c in ok.calls] == [["docker", "buildx", "imagetools"]] * 2
    wrong = retag(tmp_path / "wrong", RETAG_STEP, OTHER_DIGEST)
    assert wrong.rc == 1
    assert f"edge resolves to {OTHER_DIGEST}, not {DIGEST}" in wrong.output


@pytest.mark.parametrize(
    "mutation",
    [
        ('[ "$got" = "$DIGEST" ] ||', "true ||"),
        ('"$IMAGE@$DIGEST"', '"$IMAGE:sha-latest"'),
        ('-t "$IMAGE:edge"', '-t "$IMAGE:latest"'),
    ],
)
def test_retag_rule_bites(tmp_path: Path, mutation: tuple[str, str]) -> None:
    old, new = mutation
    assert RETAG_STEP.count(old) == 1
    mutated = RETAG_STEP.replace(old, new)
    ok = retag(tmp_path / "ok", mutated, DIGEST)
    wrong = retag(tmp_path / "wrong", mutated, OTHER_DIGEST)
    expected = ["docker", "buildx", "imagetools", "create", "-t", f"{IMAGE}:edge",
                f"{IMAGE}@{DIGEST}"]  # fmt: skip
    assert wrong.rc == 0 or ok.called("docker", "buildx", "imagetools", "create") != [expected]


SUMMARY_JOB = jobs(load(PUBLISH_EDGE))["summary"]
SUMMARY_STEP: str = SUMMARY_JOB["steps"][0]["run"]
SUMMARY_TAIL = 'retag=$RETAG)." >> "$GITHUB_STEP_SUMMARY"'
SHA = "c" * 40
# needs.*.result per outcome: (RESOLVE, CI, PUBLISH, RETAG, publish's STAGE output).
SUMMARY_CASES = {
    "retag": ("success", "skipped", "skipped", "success", ""),
    "publish": ("success", "success", "success", "skipped", "done"),
    "resolve failed": ("failure", "skipped", "skipped", "skipped", ""),
    "gate failed": ("success", "failure", "skipped", "skipped", ""),
    "build failed": ("success", "success", "failure", "skipped", "build"),
    "smoke failed": ("success", "success", "failure", "skipped", "smoke"),
    "tag failed": ("success", "success", "failure", "skipped", "tag"),
    "publish cancelled": ("success", "success", "cancelled", "skipped", ""),
    "retag failed": ("success", "skipped", "skipped", "failure", ""),
}
FAILED_STAGE = {
    "resolve failed": "resolve (ref/ancestry)",
    "gate failed": "gate (ci.yml)",
    "build failed": "publish: build",
    "smoke failed": "publish: smoke",
    "tag failed": "publish: tag",
    "publish cancelled": "publish: not started",
    "retag failed": "retag",
}


def summary(tmp_path: Path, script: str, case: str) -> StepRun:
    resolve, ci, publish, retag_result, stage = SUMMARY_CASES[case]
    env = {
        "RESOLVE": resolve, "CI": ci, "PUBLISH": publish, "RETAG": retag_result,
        "STAGE": stage, "SHA": SHA if resolve == "success" else "",
        "DIGEST": DIGEST if publish == "success" else "",
        "EXISTING": OTHER_DIGEST if retag_result != "skipped" else "",
    }  # fmt: skip
    return run_step(script, tmp_path / case.replace(" ", "-"), env=env)


def test_summary_job_always_runs_after_every_job() -> None:
    assert SUMMARY_JOB["if"] == "always()"
    assert SUMMARY_JOB["needs"] == ["resolve", "ci", "publish", "retag"]
    assert SUMMARY_JOB["steps"][0]["env"] == {
        "RESOLVE": "${{ needs.resolve.result }}",
        "CI": "${{ needs.ci.result }}",
        "PUBLISH": "${{ needs.publish.result }}",
        "RETAG": "${{ needs.retag.result }}",
        "STAGE": "${{ needs.publish.outputs.stage }}",
        "DIGEST": "${{ needs.publish.outputs.digest }}",
        "EXISTING": "${{ needs.resolve.outputs.existing_digest }}",
        "SHA": "${{ needs.resolve.outputs.sha }}",
    }


def test_summary_names_the_digest_published(tmp_path: Path) -> None:
    retagged = summary(tmp_path, SUMMARY_STEP, "retag")
    assert retagged.rc == 0, retagged.output
    assert retagged.summary.splitlines() == [
        "## edge re-pointed (re-tag path, no build)", "", "| | |", "|---|---|",
        f"| commit | `{SHA}` |", f"| digest | `{OTHER_DIGEST}` |",
    ]  # fmt: skip
    published = summary(tmp_path, SUMMARY_STEP, "publish")
    assert published.rc == 0, published.output
    assert published.summary.splitlines() == [
        "## edge published (gate-and-build path)", "", "| | |", "|---|---|",
        f"| commit | `{SHA}` |", f"| digest | `{DIGEST}` |",
    ]  # fmt: skip


@pytest.mark.parametrize("case", list(FAILED_STAGE))
def test_summary_names_the_stage_that_failed(tmp_path: Path, case: str) -> None:
    result = summary(tmp_path, SUMMARY_STEP, case)
    assert result.rc == 1
    lines = result.summary.splitlines()
    assert lines[0] == "## Nothing published"
    resolve, ci, publish, retag_result, _ = SUMMARY_CASES[case]
    assert lines[2] == (
        f"Stage that failed: **{FAILED_STAGE[case]}** "
        f"(resolve={resolve} ci={ci} publish={publish} retag={retag_result})."
    )


@pytest.mark.parametrize(
    "mutation",
    [
        (f"{SUMMARY_TAIL}\nexit 1", f"{SUMMARY_TAIL}\nexit 0"),
        ('"| digest | \\`$EXISTING\\` |"', '"| digest | n/a |"'),
        ('"| digest | \\`$DIGEST\\` |"', '"| digest | n/a |"'),
        ('stage="gate (ci.yml)"', 'stage="publish: ${STAGE:-not started}"'),
        ('stage="retag"', 'stage="gate (ci.yml)"'),
        ('stage="resolve (ref/ancestry)"', 'stage="retag"'),
    ],
)  # fmt: skip
def test_summary_rule_bites(tmp_path: Path, mutation: tuple[str, str]) -> None:
    old, new = mutation
    assert SUMMARY_STEP.count(old) == 1
    mutated = SUMMARY_STEP.replace(old, new)
    survived = True
    for case in SUMMARY_CASES:
        good, bad = summary(tmp_path / "good", SUMMARY_STEP, case), summary(tmp_path, mutated, case)
        survived &= (good.rc, good.summary) == (bad.rc, bad.summary)
    assert not survived


@pytest.mark.parametrize("workflow", PUBLISHERS)
def test_publish_cache_cleanup_and_smoke_engine(tmp_path: Path, workflow: Path) -> None:
    publish = jobs(load(workflow))["publish"]
    build = workflow_steps.step(publish, "build")["with"]
    assert build["cache-from"] == "type=gha,scope=publish"
    assert build["cache-to"] == "type=gha,mode=max,scope=publish"
    cleanup = workflow_steps.step(publish, "Remove the smoke container")
    assert cleanup["if"] == "always()"
    assert cleanup["run"].strip() == "docker rm -f console-smoke || true"
    # An unreachable Engine: /health still answers 200 (#30), and the smoke never needs one up.
    ran = smoke(tmp_path, publish_step(workflow, "smoke"), healthy=True)
    assert ran.called("docker", "run") == [
        ["docker", "run", "-d", "--name", "console-smoke", "--platform", "linux/amd64",
         "-p", "127.0.0.1:7739:7739", "-e", "CONSOLE_ENGINE_URL=http://127.0.0.1:9",
         "-e", "CONSOLE_ENGINE_SERVICE_KEY=smoke-only-key-not-a-secret", f"{IMAGE}@{DIGEST}"],
    ]  # fmt: skip


def test_publish_edge_tags_name_and_label_the_built_commit() -> None:
    doc = load(PUBLISH_EDGE)
    meta = workflow_steps.step(jobs(doc)["publish"], "meta")["with"]
    assert meta["tags"].splitlines() == [
        "type=raw,value=edge",
        "type=raw,value=sha-${{ needs.resolve.outputs.short }},"
        "enable=${{ github.event_name == 'push' }}",
    ]  # fmt: skip
    revision = "org.opencontainers.image.revision=${{ needs.resolve.outputs.sha }}"
    assert meta["labels"] == revision
    assert meta["annotations"] == revision
    build = workflow_steps.step(jobs(doc)["publish"], "build")["with"]
    assert build["labels"] == "${{ steps.meta.outputs.labels }}"
    assert build["annotations"] == "${{ steps.meta.outputs.annotations }}"


@pytest.mark.parametrize("workflow", PUBLISHERS)
def test_binfmt_and_buildkit_are_pinned_exactly(workflow: Path) -> None:
    doc = load(workflow)
    assert re.fullmatch(
        r"docker\.io/tonistiigi/binfmt:qemu-v\d+\.\d+\.\d+-\d+", doc["env"]["BINFMT_IMAGE"]
    )
    assert re.fullmatch(r"moby/buildkit:v\d+\.\d+\.\d+", doc["env"]["BUILDKIT_IMAGE"])
    by_uses = {str(s.get("uses", "")).split("@")[0]: s for s in jobs(doc)["publish"]["steps"]}
    assert by_uses["docker/setup-qemu-action"]["with"]["image"] == "${{ env.BINFMT_IMAGE }}"
    assert by_uses["docker/setup-buildx-action"]["with"]["driver-opts"] == (
        "image=${{ env.BUILDKIT_IMAGE }}"
    )


def test_publish_edge_permissions_per_job() -> None:
    doc = load(PUBLISH_EDGE)
    assert jobs(doc)["resolve"]["permissions"] == {"contents": "read", "packages": "read"}
    assert jobs(doc)["publish"]["permissions"] == {"contents": "read", "packages": "write"}
    assert jobs(doc)["retag"]["permissions"] == {"contents": "read", "packages": "write"}
    assert "permissions" not in jobs(doc)["summary"]


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

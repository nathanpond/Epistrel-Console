"""Serving entry points refuse to start without a valid Engine URL; tooling does not."""

import subprocess

import pytest

from tests.helpers import PYTHON, REPO, scrubbed_env

TIMEOUT = 20
REQUIRED_LINE = "error: CONSOLE_ENGINE_URL: is required"
BAD_LINE = "error: CONSOLE_ENGINE_URL: must be an http:// or https:// URL with a host"


def run(args: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [PYTHON, *args],
        cwd=REPO,
        env=scrubbed_env(**env),
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )


@pytest.mark.parametrize(
    ("env", "line"),
    [({}, REQUIRED_LINE), ({"CONSOLE_ENGINE_URL": "localhost:8000"}, BAD_LINE)],
)
def test_runserver_refuses_and_exits(env: dict[str, str], line: str) -> None:
    result = run(["manage.py", "runserver", "--noreload"], **env)
    assert result.returncode == 1
    assert line in result.stderr
    assert "development server at" not in result.stdout + result.stderr


@pytest.mark.parametrize("module", ["epistrel_console.wsgi", "epistrel_console.asgi"])
@pytest.mark.parametrize(
    ("env", "line"),
    [({}, REQUIRED_LINE), ({"CONSOLE_ENGINE_URL": "localhost:8000"}, BAD_LINE)],
)
def test_entry_point_import_refuses(module: str, env: dict[str, str], line: str) -> None:
    result = run(["-c", f"import {module}"], **env)
    assert result.returncode == 1
    assert line in result.stderr


@pytest.mark.parametrize("module", ["epistrel_console.wsgi", "epistrel_console.asgi"])
def test_entry_point_import_starts_with_valid_url(module: str) -> None:
    result = run(["-c", f"import {module}"], CONSOLE_ENGINE_URL="http://127.0.0.1:9")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", ["epistrel_console.wsgi", "epistrel_console.asgi"])
def test_misspelled_variable_names_itself(module: str) -> None:
    result = run(
        ["-c", f"import {module}"], CONSOLE_ENGINE_URL="http://127.0.0.1:9", CONSOLE_ENGINE_ULR="x"
    )
    assert result.returncode == 1
    assert "error: CONSOLE_ENGINE_ULR: unknown setting" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["manage.py", "check"],
        ["manage.py", "migrate", "--plan"],
        ["manage.py", "makemigrations", "--check", "--dry-run"],
    ],
)
def test_tooling_runs_without_engine_url(args: list[str]) -> None:
    result = run(args)
    assert result.returncode == 0, result.stderr

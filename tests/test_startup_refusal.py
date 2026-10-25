"""Serving entry points refuse to start without a valid Engine URL and service key; tooling does
not."""

import subprocess

import pytest

from tests.helpers import PYTHON, REPO, SERVICE_KEY, scrubbed_env

TIMEOUT = 20
REQUIRED_LINE = "error: CONSOLE_ENGINE_URL: is required"
BAD_LINE = "error: CONSOLE_ENGINE_URL: must be an http:// or https:// URL with a host"
KEY_LINE = "error: CONSOLE_ENGINE_SERVICE_KEY: is required"
URL = "http://127.0.0.1:9"
KEYED = {"CONSOLE_ENGINE_SERVICE_KEY": SERVICE_KEY}
# Each case: the environment, and the exact stderr lines it must refuse with.
REFUSALS = [
    ({**KEYED}, [REQUIRED_LINE]),
    ({**KEYED, "CONSOLE_ENGINE_URL": "localhost:8000"}, [BAD_LINE]),
    ({"CONSOLE_ENGINE_URL": URL}, [KEY_LINE]),
    ({"CONSOLE_ENGINE_URL": URL, "CONSOLE_ENGINE_SERVICE_KEY": ""}, [KEY_LINE]),
    ({}, [REQUIRED_LINE, KEY_LINE]),
]
REFUSAL_IDS = ["no-url", "bad-url", "no-key", "empty-key", "neither"]


def error_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("error: ")]


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


@pytest.mark.parametrize(("env", "lines"), REFUSALS, ids=REFUSAL_IDS)
def test_runserver_refuses_and_exits(env: dict[str, str], lines: list[str]) -> None:
    result = run(["manage.py", "runserver", "--noreload"], **env)
    assert result.returncode == 1
    assert error_lines(result.stderr) == lines
    assert "development server at" not in result.stdout + result.stderr


@pytest.mark.parametrize(("env", "lines"), REFUSALS, ids=REFUSAL_IDS)
def test_runserver_refuses_before_the_autoreloader(env: dict[str, str], lines: list[str]) -> None:
    # No --noreload: a refusal placed after the reloader starts would hang here (TimeoutExpired)
    # or print the server banner.
    result = run(["manage.py", "runserver"], **env)
    assert result.returncode == 1
    assert error_lines(result.stderr) == lines
    assert "development server at" not in result.stdout + result.stderr


@pytest.mark.parametrize("module", ["epistrel_console.wsgi", "epistrel_console.asgi"])
@pytest.mark.parametrize(("env", "lines"), REFUSALS, ids=REFUSAL_IDS)
def test_entry_point_import_refuses(module: str, env: dict[str, str], lines: list[str]) -> None:
    result = run(["-c", f"import {module}"], **env)
    assert result.returncode == 1
    assert error_lines(result.stderr) == lines


@pytest.mark.parametrize("module", ["epistrel_console.wsgi", "epistrel_console.asgi"])
@pytest.mark.parametrize("key", [SERVICE_KEY, " "], ids=["key", "whitespace-key"])
def test_entry_point_import_starts_with_valid_url_and_key(module: str, key: str) -> None:
    # A whitespace-only key is set (never trimmed, #29); the Engine's 401 judges it.
    result = run(["-c", f"import {module}"], CONSOLE_ENGINE_URL=URL, CONSOLE_ENGINE_SERVICE_KEY=key)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["-c", "import epistrel_console.wsgi"],
        ["-c", "import epistrel_console.asgi"],
        ["manage.py", "runserver", "--noreload"],
    ],
    ids=["wsgi", "asgi", "runserver"],
)
def test_misspelled_variable_names_itself(args: list[str]) -> None:
    result = run(args, CONSOLE_ENGINE_URL=URL, CONSOLE_ENGINE_ULR="x", **KEYED)
    assert result.returncode == 1
    assert "error: CONSOLE_ENGINE_ULR: unknown setting" in result.stderr
    assert "development server at" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["manage.py", "check"],
        ["manage.py", "migrate", "--plan"],
        ["manage.py", "makemigrations", "--check", "--dry-run"],
    ],
)
def test_tooling_runs_without_engine_url_or_key(args: list[str]) -> None:
    result = run(args)
    assert result.returncode == 0, result.stderr

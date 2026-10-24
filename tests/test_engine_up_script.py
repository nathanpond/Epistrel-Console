""".github/scripts/engine-up.sh start: the Engine gets only the settings its image knows (#124).

A fake ``docker`` on PATH records every call and answers the settings probe from FAKE_KNOWN, so
these run without Docker. The probe program itself runs against a stand-in ``epistrel.config``.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers import REPO

ENGINE_UP = REPO / ".github" / "scripts" / "engine-up.sh"
IMAGE = "ghcr.io/nathanpond/epistrel:0.1.1"
DATABASE_URL = (
    "EPISTREL_DATABASE_URL=postgresql+psycopg://epistrel:epistrel-dev@postgres:5432/epistrel"
)
LOG_LEVEL = "EPISTREL_LOG_LEVEL=INFO"
CREDENTIALS = "EPISTREL_SERVICE_CREDENTIALS=dev-only-credential-change-me-00000000"
CONFIG_KEY = "EPISTREL_CONFIG_KEY=ZXBpc3RyZWwtZGV2LW9ubHktY29uZmlnLWtleS0wMDA="
# What the probe printed for the real images (checked 2026-10-24, the `edge` list trimmed).
RELEASED = [
    "EPISTREL_DATABASE_URL",
    "EPISTREL_DEBUG",
    "EPISTREL_HOST",
    "EPISTREL_LOG_LEVEL",
    "EPISTREL_PORT",
]
EDGE = [*RELEASED, "EPISTREL_AUTH_DISABLED", "EPISTREL_CONFIG_KEY", "EPISTREL_SERVICE_CREDENTIALS"]

FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\0' "$@" >> "$FAKE_LOG"; printf '\\036' >> "$FAKE_LOG"
if [ "$1" = run ] && [ "$2" = --rm ]; then
  [ "${FAKE_PROBE_RC:-0}" = 0 ] || { echo "probe broke" >&2; exit "$FAKE_PROBE_RC"; }
  printf '%s\\n' $FAKE_KNOWN
fi
exit 0
"""


def start(tmp_path: Path, known: list[str], probe_rc: int = 0) -> tuple[int, str, list[list[str]]]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    docker = bindir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    log.write_text("")
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "ENGINE_IMAGE": IMAGE,
        "NETWORK": "net1",
        "FAKE_LOG": str(log),
        "FAKE_KNOWN": " ".join(known),
        "FAKE_PROBE_RC": str(probe_rc),
    }
    bash = shutil.which("bash")
    assert bash is not None
    proc = subprocess.run(  # noqa: S603
        [bash, str(ENGINE_UP), "start"], env=env, capture_output=True, text=True, check=False
    )
    calls = [call.split("\0")[:-1] for call in log.read_text().split("\x1e") if call]
    return proc.returncode, proc.stdout + proc.stderr, calls


def engine_run(calls: list[list[str]]) -> list[str] | None:
    return next((c for c in calls if c[:2] == ["run", "-d"]), None)


def test_a_released_engine_gets_only_the_settings_it_knows(tmp_path: Path) -> None:
    code, out, calls = start(tmp_path, RELEASED)
    assert code == 0, out
    assert engine_run(calls) == [
        "run", "-d", "--name", "engine", "--network", "net1", "-p", "127.0.0.1:8000:8000",
        "-e", DATABASE_URL, "-e", LOG_LEVEL, IMAGE,
    ]  # fmt: skip
    assert f"{IMAGE} has no EPISTREL_SERVICE_CREDENTIALS; not passed" in out
    assert f"{IMAGE} has no EPISTREL_CONFIG_KEY; not passed" in out


def test_edge_gets_all_four(tmp_path: Path) -> None:
    code, out, calls = start(tmp_path, EDGE)
    assert code == 0, out
    run = engine_run(calls)
    assert run is not None
    assert run[8:] == [
        "-e",
        DATABASE_URL,
        "-e",
        LOG_LEVEL,
        "-e",
        CREDENTIALS,
        "-e",
        CONFIG_KEY,
        IMAGE,
    ]
    assert "not passed" not in out


def test_a_name_that_only_starts_like_a_setting_does_not_count(tmp_path: Path) -> None:
    # grep -x: EPISTREL_CONFIG_KEY_V2 is not EPISTREL_CONFIG_KEY.
    _, _, calls = start(
        tmp_path, [*RELEASED, "EPISTREL_CONFIG_KEY_V2", "XEPISTREL_SERVICE_CREDENTIALS"]
    )
    run = engine_run(calls)
    assert run is not None
    assert CONFIG_KEY not in run
    assert CREDENTIALS not in run


def test_the_probe_runs_the_image_python_after_the_pull(tmp_path: Path) -> None:
    _, _, calls = start(tmp_path, RELEASED)
    assert calls[0] == ["pull", IMAGE]
    probe = calls[1]
    assert probe[:5] == ["run", "--rm", "--entrypoint", "python", IMAGE]
    assert probe[5] == "-c"
    assert not any(arg.startswith("EPISTREL_") for arg in probe)
    assert calls[2][:2] == ["run", "-d"]
    assert len(calls) == 3


def test_a_failed_probe_starts_nothing(tmp_path: Path) -> None:
    code, out, calls = start(tmp_path, EDGE, probe_rc=1)
    assert code == 1
    assert f"::error::could not read the settings {IMAGE} accepts" in out
    assert engine_run(calls) is None


@pytest.mark.parametrize("known", [[], ["EPISTREL_LOG_LEVEL", "EPISTREL_PORT"]])
def test_an_image_without_database_url_starts_nothing(tmp_path: Path, known: list[str]) -> None:
    code, out, calls = start(tmp_path, known)
    assert code == 1
    assert f"::error::{IMAGE} has no EPISTREL_DATABASE_URL setting" in out
    assert engine_run(calls) is None


def test_the_probe_program_lists_the_settings_model(tmp_path: Path) -> None:
    probe = re.search(r"^PROBE='(.*?)'$", ENGINE_UP.read_text(), re.M | re.S)
    assert probe is not None
    pkg = tmp_path / "epistrel"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "config.py").write_text(
        'ENV_PREFIX = "EPISTREL_"\n'
        "class Settings:\n"
        '    model_fields = {"database_url": 1, "log_level": 2, "media_s3_bucket": 3}\n'
    )
    out = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(tmp_path)!r})\n" + probe.group(1),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.split() == [
        "EPISTREL_DATABASE_URL",
        "EPISTREL_LOG_LEVEL",
        "EPISTREL_MEDIA_S3_BUCKET",
    ]


def test_wait_names_the_image_that_did_not_come_up() -> None:
    assert 'echo "::error::Engine ${ENGINE_IMAGE:-} did not become healthy in $WAIT_S s"' in (
        ENGINE_UP.read_text()
    )

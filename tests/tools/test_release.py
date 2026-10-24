"""tools.release: tag shape, pyproject match, rc rules, tag-line arithmetic and the Engine image.

Ported from the Engine's tests/release/test_validate.py, plus the Engine-image selection the
Console adds. Image existence goes through an injected lookup, so no registry is needed.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.release.validate import ENGINE_IMAGE, ReleaseError, parse_tag, validate

REPO_ROOT = Path(__file__).resolve().parents[2]


def having(*tags: str) -> Callable[[str], bool]:
    present = {f"{ENGINE_IMAGE}:{tag}" for tag in tags}
    return lambda ref: ref in present


ANY = having("0.0.1", "0.1.0", "0.1.1", "0.1.5", "0.2.0", "1.2.3", "1.2.3-rc.1", "1.3.0")


def test_first_stable_release_is_highest_everywhere() -> None:
    out = validate("v0.1.0", "0.1.0", [], ANY)
    assert out.lines() == [
        "version=0.1.0",
        "prerelease=false",
        "highest=true",
        "highest_minor=true",
        "highest_major=true",
        "already_released=false",
        f"engine_image={ENGINE_IMAGE}:0.1.0",
    ]


def test_prerelease_never_moves_floating_tags() -> None:
    out = validate("v0.1.1-rc.1", "0.1.1rc1", ["v0.1.0"], ANY)
    assert out.version == "0.1.1-rc.1"
    assert out.prerelease is True
    assert (out.highest, out.highest_minor, out.highest_major) == (False, False, False)


def test_lower_stable_version_publishes_without_latest() -> None:
    out = validate("v0.1.5", "0.1.5", ["v0.2.0", "v0.1.4"], ANY)
    assert out.highest is False
    assert out.highest_minor is True  # highest within 0.1.x
    assert out.highest_major is False  # 0.2.0 exists


def test_lower_patch_after_a_higher_minor() -> None:
    out = validate("v1.2.3", "1.2.3", ["v1.3.0"], ANY)
    assert (out.highest, out.highest_minor, out.highest_major) == (False, True, False)


@pytest.mark.parametrize(
    "tag", ["0.1.0", "1.2.3", "v0.1", "v1.2", "v0.1.0-beta.1", "v1.2.3-beta.1", "v0.1.0rc1", "x"]
)
def test_bad_tag_shapes_are_refused(tag: str) -> None:
    with pytest.raises(ReleaseError, match="must look like"):
        parse_tag(tag)


def test_tag_must_match_pyproject_version() -> None:
    with pytest.raises(ReleaseError, match=r"pyproject\.toml says 0\.1\.0"):
        validate("v0.2.0", "0.1.0", [], ANY)
    assert validate("v0.1.0", "0.1.0", [], ANY).version == "0.1.0"
    assert validate("v1.2.3-rc.1", "1.2.3rc1", [], ANY).version == "1.2.3-rc.1"


def test_rc_for_an_already_released_stable_is_refused() -> None:
    with pytest.raises(ReleaseError, match="already released"):
        validate("v0.1.0-rc.2", "0.1.0rc2", ["v0.1.0"], ANY)


def test_rerun_of_a_released_tag_is_allowed_and_flagged() -> None:
    out = validate("v0.1.0", "0.1.0", ["v0.1.0"], ANY)
    assert out.already_released is True
    assert out.highest is True


# --- the Engine image (Console only) --------------------------------------------------------


def test_stable_needs_the_same_engine_version() -> None:
    assert validate("v0.1.1", "0.1.1", [], having("0.1.1")).engine_image == f"{ENGINE_IMAGE}:0.1.1"


def test_stable_refused_without_its_engine() -> None:
    with pytest.raises(ReleaseError, match=r"no Engine image for 0\.1\.1"):
        validate("v0.1.1", "0.1.1", [], having("0.1.1-rc.1", "0.1.0"))


def test_rc_prefers_the_exact_engine_rc() -> None:
    out = validate("v0.1.1-rc.1", "0.1.1rc1", [], having("0.1.1-rc.1", "0.1.1"))
    assert out.engine_image == f"{ENGINE_IMAGE}:0.1.1-rc.1"


def test_rc_falls_back_to_the_engine_stable() -> None:
    out = validate("v0.1.1-rc.2", "0.1.1rc2", [], having("0.1.1-rc.1", "0.1.1"))
    assert out.engine_image == f"{ENGINE_IMAGE}:0.1.1"


def test_rc_refused_with_neither() -> None:
    with pytest.raises(ReleaseError, match="no Engine image"):
        validate("v0.1.1-rc.1", "0.1.1rc1", [], having("0.1.0"))


def test_checks_run_in_order() -> None:
    # A wrong pyproject version is reported before a missing Engine image.
    with pytest.raises(ReleaseError, match="pyproject"):
        validate("v9.9.9", "0.1.0", [], having())
    with pytest.raises(ReleaseError, match="already released"):
        validate("v0.1.0-rc.1", "0.1.0rc1", ["v0.1.0"], having())


def test_engine_lookup_is_not_called_for_bad_tags() -> None:
    calls: list[str] = []

    def lookup(ref: str) -> bool:
        calls.append(ref)
        return True

    with pytest.raises(ReleaseError):
        validate("v1.2", "1.2.0", [], lookup)
    assert calls == []


# --- the CLI, with a fake `docker` on PATH ------------------------------------------------------

FAKE_DOCKER = """#!/bin/sh
# Answers `docker buildx imagetools inspect <ref>` from $FAKE_IMAGES (space-separated refs).
ref="$4"
if [ -n "$FAKE_DOCKER_BROKEN" ]; then echo "error: dial tcp: no route to host" >&2; exit 1; fi
for image in $FAKE_IMAGES; do [ "$image" = "$ref" ] && { echo "Name: $ref"; exit 0; }; done
echo "ERROR: $ref: not found" >&2
exit 1
"""


def run_cli(
    tmp_path: Path, *args: str, images: str = "", broken: bool = False
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.1.1"\n')
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    env["FAKE_IMAGES"] = images
    if broken:
        env["FAKE_DOCKER_BROKEN"] = "1"
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "tools.release", "validate", "--pyproject", str(pyproject), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=REPO_ROOT,
        env=env,
    )


def test_cli_prints_outputs(tmp_path: Path) -> None:
    released = tmp_path / "released.txt"
    released.write_text("v0.0.1\nnot-a-tag\n")
    ok = run_cli(
        tmp_path,
        "--tag",
        "v0.1.1",
        "--released-file",
        str(released),
        images=f"{ENGINE_IMAGE}:0.1.1",
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    lines = ok.stdout.splitlines()
    assert "highest=true" in lines
    assert f"engine_image={ENGINE_IMAGE}:0.1.1" in lines


def test_cli_refuses_a_version_mismatch(tmp_path: Path) -> None:
    bad = run_cli(tmp_path, "--tag", "v9.9.9")
    assert bad.returncode == 1
    assert bad.stdout.startswith("error: tag v9.9.9 is version 9.9.9 but pyproject.toml says 0.1.1")


def test_cli_refuses_a_missing_engine(tmp_path: Path) -> None:
    bad = run_cli(tmp_path, "--tag", "v0.1.1", images=f"{ENGINE_IMAGE}:0.1.0")
    assert bad.returncode == 1
    assert bad.stdout.startswith("error: no Engine image for 0.1.1")


def test_cli_lookup_failure_is_not_not_found(tmp_path: Path) -> None:
    bad = run_cli(tmp_path, "--tag", "v0.1.1", broken=True)
    assert bad.returncode == 1
    assert bad.stdout.strip() == (
        f"error: could not check Engine image {ENGINE_IMAGE}:0.1.1: "
        "error: dial tcp: no route to host"
    )

"""CLI: `python -m tools.release validate --tag vX.Y.Z [--pyproject P] [--released-file F]`.

Prints GITHUB_OUTPUT-style `key=value` lines; exit 1 with `error: …` when the tag must not ship.
The Engine image is looked up with `docker buildx imagetools inspect` (`--engine-image-lookup`).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.release.validate import ReleaseError, read_project_version, validate

REPO_ROOT = Path(__file__).resolve().parents[2]
NOT_FOUND = ("not found", "manifest unknown")


def docker_image_exists(ref: str) -> bool:
    """True when the registry has `ref`; False when it says not found; else a ReleaseError."""
    docker = shutil.which("docker")
    if docker is None:
        raise ReleaseError(f"could not check Engine image {ref}: docker is not installed")
    try:
        result = subprocess.run(
            [docker, "buildx", "imagetools", "inspect", ref],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReleaseError(f"could not check Engine image {ref}: timed out") from exc
    if result.returncode == 0:
        return True
    stderr = result.stderr.strip()
    if any(marker in stderr.lower() for marker in NOT_FOUND):
        return False
    first = stderr.splitlines()[0] if stderr else f"exit {result.returncode}"
    raise ReleaseError(f"could not check Engine image {ref}: {first}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.release")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="check a release tag against pyproject and past releases")
    v.add_argument("--tag", required=True)
    v.add_argument("--pyproject", type=Path, default=REPO_ROOT / "pyproject.toml")
    v.add_argument(
        "--released-file",
        type=Path,
        default=None,
        help="file with one released tag per line (from `gh release list`)",
    )
    v.add_argument(
        "--engine-image-lookup",
        choices=["docker"],
        default="docker",
        help="how to check that the Engine image exists",
    )
    args = parser.parse_args(argv)
    try:
        released = (
            [line.strip() for line in args.released_file.read_text().splitlines() if line.strip()]
            if args.released_file
            else []
        )
        outputs = validate(
            args.tag, read_project_version(args.pyproject), released, docker_image_exists
        )
    except (OSError, ReleaseError) as exc:
        print(f"error: {exc}")
        return 1
    print("\n".join(outputs.lines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

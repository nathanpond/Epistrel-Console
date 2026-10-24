"""The Console's version, read once from pyproject.toml (the project is not installed)."""

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _read_version() -> str:
    try:
        with PYPROJECT.open("rb") as handle:
            version = tomllib.load(handle)["project"]["version"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        return "unknown"
    return str(version)


VERSION = _read_version()

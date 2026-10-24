"""Shared helpers for tests that run the Console in a subprocess."""

import os
import shutil
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def scrubbed_env(**extra: str) -> dict[str, str]:
    """PATH, HOME and DJANGO_SETTINGS_MODULE only, plus the variables under test."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DJANGO_SETTINGS_MODULE": "epistrel_console.settings",
    }
    env.update(extra)
    return env


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def working_copy(dest: Path) -> Path:
    """A copy of the project (no db, caches or VCS) so a test never touches the repo's files."""
    ignore = shutil.ignore_patterns(
        ".git",
        ".venv",
        "*.sqlite3",
        "secret_key",
        "__pycache__",
        ".*_cache",
        "staticfiles",
        ".idea",
    )
    target = dest / "console-copy"
    shutil.copytree(REPO, target, ignore=ignore)
    return target

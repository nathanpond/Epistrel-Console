"""Where Django's SECRET_KEY comes from.

A non-empty DJANGO_SECRET_KEY wins. With debug on and no env key, a fixed dev fallback is used and
no file is touched. With debug off, the key lives in ``<data dir>/secret_key``: read if present,
otherwise generated once (0600) so each install gets its own key without the operator doing
anything, and no secret is ever baked into the image.
"""

import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

DEV_FALLBACK = "django-insecure-dev-only-not-a-secret"
FILE_NAME = "secret_key"
MIN_LENGTH = 50


class StartupError(Exception):
    """A configuration problem that stops the Console starting; ``name`` is what to fix."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name}: {reason}")
        self.name = name
        self.reason = reason


def resolve(env_value: str | None, data_dir: Path, debug: bool) -> str:
    env_key = (env_value or "").strip()
    if env_key:
        return env_key
    if debug:
        return DEV_FALLBACK
    path = data_dir / FILE_NAME
    try:
        return _read(path)
    except FileNotFoundError:
        pass
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # A concurrent first start won the race: use its key.
        return _read(path)
    except OSError as exc:
        raise StartupError(str(path), exc.strerror or str(exc)) from exc
    key = get_random_secret_key()
    with os.fdopen(fd, "w") as handle:
        handle.write(key + "\n")
    return key


def _read(path: Path) -> str:
    try:
        key = path.read_text().strip()
        path.chmod(0o600)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise StartupError(str(path), exc.strerror or str(exc)) from exc
    if len(key) < MIN_LENGTH:
        raise StartupError(str(path), "secret key file is too short — delete it to regenerate")
    return key


def data_dir(raw: str | None, base_dir: Path) -> Path:
    """CONSOLE_DATA_DIR: default ``base_dir``; relative to ``base_dir``; ``~`` expanded."""
    value = (raw or "").strip()
    path = Path(value).expanduser() if value else base_dir
    if not path.is_absolute():
        path = base_dir / path
    return path


def ensure_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StartupError("CONSOLE_DATA_DIR", f"{path} is not writable") from exc
    if not os.access(path, os.W_OK):
        raise StartupError("CONSOLE_DATA_DIR", f"{path} is not writable")

"""Where Django's SECRET_KEY comes from.

A non-empty DJANGO_SECRET_KEY wins. With debug on and no env key, a fixed dev fallback is used and
no file is touched. With debug off, the key lives in ``<data dir>/secret_key``: read if present,
otherwise generated once (0600) so each install gets its own key without the operator doing
anything, and no secret is ever baked into the image.
"""

import os
import tempfile
import time
from pathlib import Path

from django.core.management.utils import get_random_secret_key

DEV_FALLBACK = "django-insecure-dev-only-not-a-secret"
FILE_NAME = "secret_key"
MIN_LENGTH = 50
TOO_SHORT = "secret key file is too short — delete it to regenerate"
RACE_RETRIES = 20
RACE_WAIT_S = 0.05


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
    key = get_random_secret_key()
    try:
        _publish(path, key)
    except FileExistsError:
        # A concurrent first start won the race: use its key.
        return _read_winner(path)
    except OSError as exc:
        raise StartupError(str(path), exc.strerror or str(exc)) from exc
    return key


def _publish(path: Path, key: str) -> None:
    """Write the key to a private temp file, then link it into place.

    The link is atomic and fails if the file exists, so ``path`` only ever appears complete and a
    concurrent first start can never read a half-written key.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{FILE_NAME}.")  # 0600
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(key + "\n")
        os.link(tmp, path)
    finally:
        os.unlink(tmp)


def _read_winner(path: Path) -> str:
    # Retry briefly on a short file: a winner that wrote in place (an older Console) may be
    # mid-write.
    for _ in range(RACE_RETRIES):
        try:
            return _read(path)
        except StartupError as exc:
            if exc.reason != TOO_SHORT:
                raise
        time.sleep(RACE_WAIT_S)
    return _read(path)


def _read(path: Path) -> str:
    try:
        key = path.read_text().strip()
        path.chmod(0o600)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise StartupError(str(path), exc.strerror or str(exc)) from exc
    if len(key) < MIN_LENGTH:
        raise StartupError(str(path), TOO_SHORT)
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

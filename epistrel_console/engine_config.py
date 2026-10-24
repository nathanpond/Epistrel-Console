"""Where the Console's Engine lives, read from the process environment.

The Engine URL is required to serve. The service key is optional until the Engine enforces
service auth (epic #3); when set it is kept out of every log line, error message and ``repr()``.

Validation is not a Django system check: ``makemigrations`` runs every registered check, and
runserver's autoreloader prints check errors and keeps watching instead of exiting. Serving
entry points call :func:`require` instead, and tooling (mypy, pytest, migrate) never does.
"""

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

URL_VAR = "CONSOLE_ENGINE_URL"
KEY_VAR = "CONSOLE_ENGINE_SERVICE_KEY"
DATA_DIR_VAR = "CONSOLE_DATA_DIR"

# Every CONSOLE_* variable the Console understands. Anything else starting with CONSOLE_ is a typo
# and refuses start, so a misspelled name reports itself instead of only "is required".
KNOWN_VARS = frozenset({URL_VAR, KEY_VAR, DATA_DIR_VAR})

REQUIRED = "is required"
BAD_URL = "must be an http:// or https:// URL with a host"
UNKNOWN = "unknown setting"


@dataclass(frozen=True)
class EngineConfig:
    """The parsed Engine settings. ``url`` is ``None`` when unset or invalid."""

    url: str | None
    service_key: str | None = field(default=None, repr=False)
    errors: tuple[tuple[str, str], ...] = ()


def parse_url(raw: str | None) -> tuple[str | None, str | None]:
    """Return ``(normalised_url, None)`` or ``(None, reason)``."""
    value = (raw or "").strip()
    if not value:
        return None, REQUIRED
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None, BAD_URL
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    netloc = parts.netloc
    if (
        scheme not in ("http", "https")
        or not host
        or any(ch.isspace() for ch in host)
        or "@" in netloc
        or netloc.endswith(":")
        or port == 0
        or "?" in value
        or "#" in value
    ):
        return None, BAD_URL
    path = parts.path.rstrip("/")
    return f"{scheme}://{netloc}{path}", None


def parse(environ: Mapping[str, str]) -> EngineConfig:
    """Parse the Engine settings from ``environ`` without side effects."""
    errors: list[tuple[str, str]] = []
    url, reason = parse_url(environ.get(URL_VAR))
    if reason:
        errors.append((URL_VAR, reason))
    errors.extend(
        (name, UNKNOWN)
        for name in sorted(environ)
        if name.startswith("CONSOLE_") and name not in KNOWN_VARS
    )
    key = environ.get(KEY_VAR) or None
    return EngineConfig(url=url, service_key=key, errors=tuple(errors))


def require() -> None:
    """Refuse to serve without a valid Engine URL: print each problem and exit 1.

    Re-reads ``os.environ`` rather than trusting settings, so nothing can mask a refusal.
    """
    config = parse(os.environ)
    if not config.errors:
        return
    for name, reason in config.errors:
        print(f"error: {name}: {reason}", file=sys.stderr)
    sys.exit(1)

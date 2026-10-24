"""Debug-off settings for plain HTTP on localhost: CSRF origins, no secure cookies or HSTS."""

import json
import subprocess
from pathlib import Path
from typing import Any

from tests.helpers import PYTHON, REPO, scrubbed_env

NAMES = [
    "CSRF_TRUSTED_ORIGINS",
    "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE",
    "SECURE_HSTS_SECONDS",
    "SILENCED_SYSTEM_CHECKS",
]
PRINT = (
    "import json, django; from django.conf import settings; "
    f"print(json.dumps({{n: getattr(settings, n) for n in {NAMES!r}}}))"
)


def debug_off_settings(data_dir: Path, **env: str) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603
        [PYTHON, "-c", PRINT],
        cwd=REPO,
        env=scrubbed_env(DJANGO_DEBUG="0", CONSOLE_DATA_DIR=str(data_dir), **env),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    values: dict[str, Any] = json.loads(result.stdout)
    return values


def test_csrf_origins_default_to_the_console_port(tmp_path: Path) -> None:
    values = debug_off_settings(tmp_path)
    assert values["CSRF_TRUSTED_ORIGINS"] == ["http://127.0.0.1:7739", "http://localhost:7739"]


def test_csrf_origins_override_drops_blanks(tmp_path: Path) -> None:
    values = debug_off_settings(
        tmp_path, DJANGO_CSRF_TRUSTED_ORIGINS="https://a.test, ,https://b.test"
    )
    assert values["CSRF_TRUSTED_ORIGINS"] == ["https://a.test", "https://b.test"]


def test_no_secure_cookies_hsts_or_silenced_checks(tmp_path: Path) -> None:
    # Secure cookies would break login over plain-HTTP localhost; `check --deploy` warnings are
    # tolerated, never silenced.
    values = debug_off_settings(tmp_path)
    assert values["SESSION_COOKIE_SECURE"] is False
    assert values["CSRF_COOKIE_SECURE"] is False
    assert values["SECURE_HSTS_SECONDS"] == 0
    assert values["SILENCED_SYSTEM_CHECKS"] == []

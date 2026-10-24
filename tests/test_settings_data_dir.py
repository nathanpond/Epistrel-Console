import json
import os
import subprocess
from pathlib import Path

from tests.helpers import PYTHON, REPO, scrubbed_env

PRINT = (
    "import json, django; from django.conf import settings; "
    "print(json.dumps(str(settings.DATABASES['default']['NAME'])))"
)


def db_name(**env: str) -> Path:
    result = subprocess.run(  # noqa: S603
        [PYTHON, "-c", PRINT],
        cwd=REPO,
        env=scrubbed_env(**env),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    return Path(json.loads(result.stdout))


def test_database_follows_absolute_data_dir(tmp_path: Path) -> None:
    assert db_name(CONSOLE_DATA_DIR=str(tmp_path)) == tmp_path / "db.sqlite3"


def test_database_follows_relative_data_dir(tmp_path: Path) -> None:
    # A relative CONSOLE_DATA_DIR resolves against BASE_DIR (the repo), not the process cwd.
    relative = os.path.relpath(tmp_path, REPO)
    name = db_name(CONSOLE_DATA_DIR=relative)
    assert name.resolve() == (tmp_path / "db.sqlite3").resolve()


def test_debug_off_writes_key_into_data_dir(tmp_path: Path) -> None:
    db_name(CONSOLE_DATA_DIR=str(tmp_path), DJANGO_DEBUG="0")
    assert (tmp_path / "secret_key").exists()

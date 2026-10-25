"""Dev environment end to end: migrate, then runserver serves the admin login on SQLite."""

import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.helpers import PYTHON, SERVICE_KEY, free_port, scrubbed_env, working_copy

BUDGET_S = 30


@pytest.fixture
def copy_dir(tmp_path: Path) -> Path:
    return working_copy(tmp_path)


def wait_for(url: str) -> httpx.Response:
    deadline = time.monotonic() + BUDGET_S
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return httpx.get(url, timeout=2)
        except httpx.TransportError as exc:
            last = exc
            time.sleep(0.3)
    raise AssertionError(f"server never answered {url}: {last}")


@pytest.fixture
def server(copy_dir: Path) -> Iterator[tuple[int, Path]]:
    env = scrubbed_env(
        CONSOLE_ENGINE_URL="http://127.0.0.1:9", CONSOLE_ENGINE_SERVICE_KEY=SERVICE_KEY
    )
    migrate = subprocess.run(  # noqa: S603
        [PYTHON, "manage.py", "migrate", "--noinput"],
        cwd=copy_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert migrate.returncode == 0, migrate.stderr
    port = free_port()
    proc = subprocess.Popen(  # noqa: S603
        [PYTHON, "manage.py", "runserver", "--noreload", f"127.0.0.1:{port}"],
        cwd=copy_dir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield port, copy_dir
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_dev_runserver_serves_admin_login(server: tuple[int, Path]) -> None:
    port, copy_dir = server
    response = wait_for(f"http://127.0.0.1:{port}/admin/login/")
    assert response.status_code == 200
    assert (copy_dir / "db.sqlite3").exists()

"""Dev environment end to end: runserver against a stub Engine, then with the Engine gone."""

import subprocess
import time
from pathlib import Path

import httpx

from tests.helpers import PYTHON, free_port, scrubbed_env, working_copy
from tests.stub_engine import base_url, stub_engine


def wait_for(url: str, budget_s: float = 30) -> httpx.Response:
    deadline = time.monotonic() + budget_s
    while True:
        try:
            return httpx.get(url, timeout=10)
        except httpx.TransportError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)


def test_status_follows_the_engine(tmp_path: Path) -> None:
    copy_dir = working_copy(tmp_path)
    port = free_port()
    with stub_engine() as engine:
        env = scrubbed_env(CONSOLE_ENGINE_URL=base_url(engine))
        proc = subprocess.Popen(  # noqa: S603
            [PYTHON, "manage.py", "runserver", "--noreload", f"127.0.0.1:{port}"],
            cwd=copy_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            page = wait_for(f"http://127.0.0.1:{port}/")
            assert "Healthy" in page.text
            assert httpx.get(f"http://127.0.0.1:{port}/health").json()["engine"]["state"] == (
                "healthy"
            )
            engine.shutdown()
            engine.server_close()
            page = httpx.get(f"http://127.0.0.1:{port}/", timeout=10)
            assert "Unreachable" in page.text
            assert "connection failed" in page.text
            health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=10)
            assert health.status_code == 200
            assert health.json()["engine"]["state"] == "unreachable"
        finally:
            proc.terminate()
            proc.wait(timeout=10)

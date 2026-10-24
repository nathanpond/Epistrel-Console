"""The built image: health, static files, secret key, refusal and contents (marker ``docker``)."""

import json
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest

from tests.helpers import REPO, free_port

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available"),
]
TAG = "epistrel-console:test"


def docker(
    *args: str, check: bool = True, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


@pytest.fixture(scope="module")
def image() -> str:
    docker("build", "-t", TAG, str(REPO), timeout=900)
    return TAG


class Run:
    def __init__(self) -> None:
        self.containers: list[str] = []
        self.volumes: list[str] = []

    def volume(self) -> str:
        name = f"n8-test-{uuid.uuid4().hex[:8]}"
        docker("volume", "create", name)
        self.volumes.append(name)
        return name

    def start(self, image: str, volume: str, *env: str, port: int | None = None) -> str:
        args = ["run", "-d", "-v", f"{volume}:/data"]
        for item in env:
            args += ["-e", item]
        if port:
            args += ["-p", f"127.0.0.1:{port}:7739"]
        container = docker(*args, image).stdout.strip()
        self.containers.append(container)
        return container


@pytest.fixture
def run() -> Iterator[Run]:
    r = Run()
    yield r
    for container in r.containers:
        docker("rm", "-f", container, check=False)
    for volume in r.volumes:
        docker("volume", "rm", "-f", volume, check=False)


def wait_healthy(container: str, budget_s: float = 60) -> None:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        state = docker("inspect", "-f", "{{.State.Health.Status}}", container).stdout.strip()
        if state == "healthy":
            return
        time.sleep(1)
    raise AssertionError(f"{container} not healthy within {budget_s}s")


def read_key(container: str) -> str:
    return docker("exec", container, "cat", "/data/secret_key").stdout.strip()


ENGINE = "CONSOLE_ENGINE_URL=http://127.0.0.1:9"


def test_runs_healthy_with_static_and_a_persistent_key(image: str, run: Run) -> None:
    volume = run.volume()
    port = free_port()
    container = run.start(image, volume, ENGINE, port=port)
    wait_healthy(container)
    health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=10)
    assert health.status_code == 200
    assert health.json()["engine"]["state"] == "unreachable"
    css = httpx.get(f"http://127.0.0.1:{port}/static/admin/css/base.css", timeout=10)
    assert css.status_code == 200
    assert len(css.content) > 1000
    key = read_key(container)
    assert len(key) >= 50
    docker("restart", container)
    wait_healthy(container)
    assert read_key(container) == key
    other = run.start(image, run.volume(), ENGINE)
    wait_healthy(other)
    assert read_key(other) != key


def test_refuses_without_engine_url(image: str, run: Run) -> None:
    container = run.start(image, run.volume())
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = json.loads(docker("inspect", container).stdout)[0]["State"]
        if status["Status"] == "exited":
            break
        time.sleep(1)
    assert status["Status"] == "exited"
    assert status["ExitCode"] != 0
    logs = docker("logs", container).stderr + docker("logs", container).stdout
    assert "error: CONSOLE_ENGINE_URL: is required" in logs


def test_image_contents(image: str) -> None:
    assert docker("run", "--rm", image, "id", "-u").stdout.strip() != "0"
    no_pytest = docker("run", "--rm", image, "python", "-c", "import pytest", check=False)
    assert no_pytest.returncode != 0
    # No secret key is ever baked into the image.
    assert docker("run", "--rm", image, "test", "-e", "/data/secret_key", check=False).returncode
    history = docker("history", "--no-trunc", image).stdout
    assert "secret_key" not in history

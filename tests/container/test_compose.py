"""The compose stack: published ports, no restarts, and a live run (marker ``docker``)."""

import json
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.helpers import REPO

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available"),
]
PROJECT = "epistrel-console-test"
TEST_PORT = 7740
BASE = f"http://127.0.0.1:{TEST_PORT}"


def compose(
    *args: str, file: Path | None = None, check: bool = True, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose"]
    if file is not None:
        cmd += ["-p", PROJECT, "-f", str(file)]
    return subprocess.run(  # noqa: S603
        [*cmd, *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def real_config() -> dict[str, Any]:
    config: dict[str, Any] = json.loads(compose("config", "--format", "json").stdout)
    return config


def test_only_console_publishes_a_port_on_localhost() -> None:
    services = real_config()["services"]
    published = [
        (name, port) for name, service in services.items() for port in service.get("ports", [])
    ]
    assert len(published) == 1
    name, port = published[0]
    assert name == "console"
    assert port["host_ip"] == "127.0.0.1"
    assert str(port["published"]) == "7739"
    assert int(port["target"]) == 7739
    assert "ports" not in services["engine"]
    assert "ports" not in services["db"]


def test_no_restart_policy_and_no_console_secrets() -> None:
    services = real_config()["services"]
    assert all("restart" not in service for service in services.values())
    console_env = services["console"]["environment"]
    assert "CONSOLE_ENGINE_SERVICE_KEY" not in console_env
    assert "DJANGO_SECRET_KEY" not in console_env
    assert services["console"]["pull_policy"] == "build"


def test_no_interpolation_in_the_file() -> None:
    assert "${" not in (REPO / "docker-compose.yml").read_text()


def test_env_file_changes_nothing_but_compose_project_name(tmp_path: Path) -> None:
    # The README's claim: a .env cannot change any setting; only COMPOSE_PROJECT_NAME renames the
    # stack (Compose's own override of `name:`).
    settings = tmp_path / "settings.env"
    settings.write_text(
        "CONSOLE_ENGINE_URL=http://elsewhere\nDJANGO_DEBUG=1\nPOSTGRES_PASSWORD=x\n"
    )
    renamed = tmp_path / "renamed.env"
    renamed.write_text("COMPOSE_PROJECT_NAME=renamed\n")
    config = real_config()
    assert config["name"] == "epistrel-console"
    with_settings = json.loads(
        compose("--env-file", str(settings), "config", "--format", "json").stdout
    )
    assert with_settings == config
    with_name = json.loads(compose("--env-file", str(renamed), "config", "--format", "json").stdout)
    assert with_name["name"] == "renamed"


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture(scope="module")
def stack_file(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    if not port_free(TEST_PORT):
        pytest.skip(f"port {TEST_PORT} is in use")
    if subprocess.run(["docker", "info"], capture_output=True, check=False).returncode:  # noqa: S607
        pytest.skip("docker daemon is not answering")
    config = real_config()
    console = config["services"]["console"]
    console["ports"] = [
        {"target": 7739, "published": str(TEST_PORT), "host_ip": "127.0.0.1", "protocol": "tcp"}
    ]
    console["image"] = "epistrel-console:test"
    console["environment"]["DJANGO_CSRF_TRUSTED_ORIGINS"] = BASE
    config["name"] = PROJECT
    path = tmp_path_factory.mktemp("compose") / "compose.json"
    path.write_text(json.dumps(config))
    pull = compose("pull", "engine", file=path, check=False)
    if pull.returncode:
        pytest.skip(f"cannot pull the Engine edge image: {pull.stderr.strip()}")
    try:
        yield path
    finally:
        compose("down", "-v", file=path, check=False)


def up(path: Path) -> None:
    compose("up", "--build", "-d", "--wait", "--wait-timeout", "180", file=path, timeout=600)


def wait_state(state: str, budget_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + budget_s
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{BASE}/health", timeout=10)
            body = response.json()
            if response.status_code == 200 and body["engine"]["state"] == state:
                return body
        except httpx.TransportError:
            pass
        time.sleep(1)
    raise AssertionError(f"engine never {state} within {budget_s}s: {body}")


def console_healthy(path: Path) -> bool:
    ps = compose("ps", "--format", "json", "console", file=path).stdout
    rows = [json.loads(line) for line in ps.splitlines() if line.strip()]
    return bool(rows) and rows[0].get("Health") == "healthy"


def login(username: str, password: str) -> bool:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        page = client.get("/admin/login/")
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text)
        assert match, "no CSRF token on the login page"
        response = client.post(
            "/admin/login/?next=/admin/",
            data={
                "csrfmiddlewaretoken": match.group(1),
                "username": username,
                "password": password,
                "next": "/admin/",
            },
            headers={"Referer": f"{BASE}/admin/login/"},
        )
        if response.status_code != 302:
            return False
        return client.get("/admin/").status_code == 200


def key(path: Path) -> str:
    return compose("exec", "-T", "console", "cat", "/data/secret_key", file=path).stdout.strip()


def test_stack_lifecycle(stack_file: Path) -> None:
    up(stack_file)
    body = wait_state("healthy", 60)
    assert body["engine"]["checks"]["database"] == "ok"
    page = httpx.get(f"{BASE}/", timeout=10)
    assert page.status_code == 200
    assert "Healthy" in page.text

    compose("stop", "engine", file=stack_file)
    wait_state("unreachable", 30)
    # Complement: the Console stays healthy while the Engine is down.
    assert console_healthy(stack_file)
    compose("start", "engine", file=stack_file)
    wait_state("healthy", 60)

    password = "console-test-password-0000"  # noqa: S105 - throwaway test account
    compose(
        "exec",
        "-T",
        "-e",
        "DJANGO_SUPERUSER_USERNAME=console-test",
        "-e",
        f"DJANGO_SUPERUSER_PASSWORD={password}",
        "-e",
        "DJANGO_SUPERUSER_EMAIL=t@example.invalid",
        "console",
        "python",
        "manage.py",
        "createsuperuser",
        "--noinput",
        file=stack_file,
    )
    before = key(stack_file)
    compose("down", file=stack_file)
    up(stack_file)
    assert login("console-test", password)
    assert key(stack_file) == before

    compose("down", "-v", file=stack_file)
    up(stack_file)
    assert not login("console-test", password)
    assert key(stack_file) != before


def test_pull_matches_the_registry(stack_file: Path) -> None:
    pull = compose("pull", file=stack_file)
    # Compose lists the local console tag as "Skipped"; it must never try to pull it.
    lines = (pull.stdout + pull.stderr).splitlines()
    console_lines = [line for line in lines if "epistrel-console:" in line]
    assert console_lines
    assert all("Skipped" in line for line in console_lines)
    assert any("ghcr.io/nathanpond/epistrel:edge" in line and "Pulled" in line for line in lines)
    remote = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "ghcr.io/nathanpond/epistrel:edge"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    digest = re.search(r"Digest:\s+(sha256:[0-9a-f]+)", remote)
    assert digest
    local = subprocess.run(
        ["docker", "image", "inspect", "ghcr.io/nathanpond/epistrel:edge"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    image = json.loads(local.stdout)[0]
    assert any(d.endswith(digest.group(1)) for d in image["RepoDigests"])
    up(stack_file)
    container = compose("ps", "-q", "engine", file=stack_file).stdout.strip()
    running = subprocess.run(  # noqa: S603
        ["docker", "inspect", "-f", "{{.Image}}", container],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert running == image["Id"]

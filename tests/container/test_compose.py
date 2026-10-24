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
DEV_CREDENTIAL = "dev-only-credential-change-me-00000000"
DEV_CONFIG_KEY = "ZXBpc3RyZWwtZGV2LW9ubHktY29uZmlnLWtleS0wMDA="
DEV_ONLY_COMMENT = "# dev-only default — override for anything but local use"
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


def real_config(*overrides: str) -> dict[str, Any]:
    files = ["-f", "docker-compose.yml"] if overrides else []
    for override in overrides:
        files += ["-f", override]
    config: dict[str, Any] = json.loads(compose(*files, "config", "--format", "json").stdout)
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


def test_no_restart_policy_and_no_console_secret_key() -> None:
    services = real_config()["services"]
    assert all("restart" not in service for service in services.values())
    assert "DJANGO_SECRET_KEY" not in services["console"]["environment"]


def test_engine_and_console_share_the_dev_only_credential() -> None:
    services = real_config()["services"]
    engine_env = services["engine"]["environment"]
    console_env = services["console"]["environment"]
    assert engine_env["EPISTREL_SERVICE_CREDENTIALS"] == DEV_CREDENTIAL
    assert console_env["CONSOLE_ENGINE_SERVICE_KEY"] == DEV_CREDENTIAL
    # Copied from the Engine compose at the `edge` commit (3d4c7f4), never invented.
    assert engine_env["EPISTREL_CONFIG_KEY"] == DEV_CONFIG_KEY
    text = (REPO / "docker-compose.yml").read_text()
    for literal in (DEV_CREDENTIAL, DEV_CONFIG_KEY):
        lines = [line for line in text.splitlines() if literal in line]
        assert lines
        assert all(line.endswith(f"  {DEV_ONLY_COMMENT}") for line in lines), lines


def test_engine_mounts_media_and_erasure_ledger_volumes() -> None:
    config = real_config()
    mounts = {
        (m["type"], m["source"], m["target"]) for m in config["services"]["engine"]["volumes"]
    }
    assert mounts == {
        ("volume", "engine-media", "/var/lib/epistrel/media"),
        ("volume", "engine-erasure-ledger", "/var/lib/epistrel/erasure-ledger"),
    }
    assert {"engine-media", "engine-erasure-ledger"} <= set(config["volumes"])


def test_console_runs_the_published_edge_image() -> None:
    console = real_config()["services"]["console"]
    assert console["image"] == "ghcr.io/nathanpond/epistrel-console:edge"
    assert "build" not in console
    assert "pull_policy" not in console


def test_build_override_restores_the_local_build() -> None:
    console = real_config("compose.build.yml")["services"]["console"]
    assert console["image"] == "epistrel-console:dev"
    assert console["pull_policy"] == "build"
    assert Path(console["build"]["context"]) == REPO


def test_whole_stack_pull_names_all_three_images() -> None:
    # `docker compose pull` pulls exactly the images `config --images` lists (no build-only tag).
    images = set(compose("config", "--images").stdout.split())
    assert images == {
        "ghcr.io/nathanpond/epistrel-console:edge",
        "ghcr.io/nathanpond/epistrel:edge",
        "pgvector/pgvector:pg16",
    }


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
    # The PR's own code, not the last published edge: the local-build override.
    config = real_config("compose.build.yml")
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


def create_operator(path: Path, password: str) -> None:
    """``createsuperuser --noinput`` inside the console container: the operator ``console-test``."""
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
        file=path,
    )


def operator_page(username: str, password: str, path: str) -> str:
    """``path`` as a signed-in operator, through the operator login form (#43)."""
    with httpx.Client(base_url=BASE, timeout=10) as client:
        page = client.get("/accounts/login/")
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text)
        assert match, "no CSRF token on the login page"
        response = client.post(
            "/accounts/login/",
            data={
                "csrfmiddlewaretoken": match.group(1),
                "username": username,
                "password": password,
            },
            headers={"Referer": f"{BASE}/accounts/login/"},
        )
        assert response.status_code == 302, response.text
        result = client.get(path)
        assert result.status_code == 200
        return result.text


# Run inside the console container: no Engine port is published. Prints [status, error code].
ENGINE_CALL = """
import json
from console.engine.client import EngineClient
response = EngineClient("http://engine:8000", {key!r})._request("GET", "/stories")
try:
    code = response.json().get("error", {{}}).get("code")
except ValueError:
    code = None
print(json.dumps([response.status_code, code]))
"""


def engine_call(path: Path, service_key: str) -> list[Any]:
    out = compose(
        "exec", "-T", "console", "python", "manage.py", "shell", "-c",
        ENGINE_CALL.format(key=service_key), file=path,
    ).stdout  # fmt: skip
    result: list[Any] = json.loads(out.strip().splitlines()[-1])
    return result


def key(path: Path) -> str:
    return compose("exec", "-T", "console", "cat", "/data/secret_key", file=path).stdout.strip()


@pytest.mark.engine_edge
def test_stack_lifecycle(stack_file: Path) -> None:
    up(stack_file)
    body = wait_state("healthy", 60)
    assert body["engine"]["checks"]["database"] == "ok"
    # Signed out, / is behind the operator login (#43); /health (above) stays public.
    anonymous = httpx.get(f"{BASE}/", timeout=10)
    assert anonymous.status_code == 302
    assert anonymous.headers["location"] == "/accounts/login/?next=/"
    password = "console-test-password-0000"  # noqa: S105 - throwaway test account
    create_operator(stack_file, password)
    page = operator_page("console-test", password, "/")
    assert "Healthy" in page
    assert "Service key: accepted" in page
    # The Engine enforces the credential (#42): the Console's key is accepted, a wrong one is not.
    assert engine_call(stack_file, DEV_CREDENTIAL) == [200, None]
    assert engine_call(stack_file, "wrong-key-" + "0" * 32) == [401, "unauthenticated"]

    compose("stop", "engine", file=stack_file)
    wait_state("unreachable", 30)
    # Complement: the Console stays healthy while the Engine is down.
    assert console_healthy(stack_file)
    compose("start", "engine", file=stack_file)
    wait_state("healthy", 60)

    before = key(stack_file)
    compose("down", file=stack_file)
    up(stack_file)
    assert login("console-test", password)
    assert key(stack_file) == before

    compose("down", "-v", file=stack_file)
    up(stack_file)
    assert not login("console-test", password)
    assert key(stack_file) != before


@pytest.mark.engine_edge
def test_pull_matches_the_registry(stack_file: Path) -> None:
    pull = compose("pull", file=stack_file)
    # Compose must never try to pull the local console tag. Some Compose versions list it as
    # "Skipped", others (GitHub's runners) print nothing per image; either way, no other verb.
    # The proof that the Engine was pulled is the digest comparison below, not Compose's output.
    lines = (pull.stdout + pull.stderr).splitlines()
    console_lines = [line for line in lines if "epistrel-console:" in line]
    assert all("Skipped" in line for line in console_lines), console_lines
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

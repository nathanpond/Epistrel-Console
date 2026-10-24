"""Dev environment end to end: runserver against a stub Engine, then with the Engine gone."""

import re
import subprocess
import time
from pathlib import Path

import httpx

from tests.helpers import PYTHON, SERVICE_KEY, free_port, scrubbed_env, working_copy
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


# A throwaway operator created in the subprocess's own database (#43).
OPERATOR = "console-test"
PASSWORD = "console-test-password-0000"  # noqa: S105 - throwaway test account


def create_operator(copy_dir: Path, env: dict[str, str]) -> None:
    manage = [PYTHON, "manage.py"]
    subprocess.run([*manage, "migrate", "--noinput"], cwd=copy_dir, env=env, check=True,  # noqa: S603
                   capture_output=True)  # fmt: skip
    subprocess.run(  # noqa: S603
        [*manage, "createsuperuser", "--noinput"],
        cwd=copy_dir,
        env={
            **env,
            "DJANGO_SUPERUSER_USERNAME": OPERATOR,
            "DJANGO_SUPERUSER_PASSWORD": PASSWORD,
            "DJANGO_SUPERUSER_EMAIL": "t@example.invalid",
        },
        check=True,
        capture_output=True,
    )


def sign_in(base: str) -> httpx.Client:
    """A client signed in through the login form (CSRF cookie and token)."""
    client = httpx.Client(base_url=base, timeout=10)
    page = client.get("/accounts/login/")
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text)
    assert match, "no CSRF token on the login page"
    response = client.post(
        "/accounts/login/",
        data={"csrfmiddlewaretoken": match.group(1), "username": OPERATOR, "password": PASSWORD},
    )
    assert response.status_code == 302, response.text
    assert response.headers["location"] == "/"
    return client


def test_status_follows_the_engine(tmp_path: Path) -> None:
    copy_dir = working_copy(tmp_path)
    port = free_port()
    with stub_engine() as engine:
        env = scrubbed_env(
            CONSOLE_ENGINE_URL=base_url(engine), CONSOLE_ENGINE_SERVICE_KEY=SERVICE_KEY
        )
        create_operator(copy_dir, env)
        proc = subprocess.Popen(  # noqa: S603
            [PYTHON, "manage.py", "runserver", "--noreload", f"127.0.0.1:{port}"],
            cwd=copy_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            anonymous = wait_for(f"{base}/")
            # Signed out, / is the login page (httpx follows no redirects by default).
            assert anonymous.status_code == 302
            assert anonymous.headers["location"] == "/accounts/login/?next=/"
            operator = sign_in(base)
            page = operator.get("/")
            assert "Healthy" in page.text
            assert "Service key: accepted" in page.text
            assert OPERATOR in page.text
            assert httpx.get(f"http://127.0.0.1:{port}/health").json()["engine"]["state"] == (
                "healthy"
            )
            engine.shutdown()
            engine.server_close()
            page = operator.get("/")
            assert "Unreachable" in page.text
            assert "connection failed" in page.text
            health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=10)
            assert health.status_code == 200
            assert health.json()["engine"]["state"] == "unreachable"
            operator.close()
        finally:
            proc.terminate()
            proc.wait(timeout=10)

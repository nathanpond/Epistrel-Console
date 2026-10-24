"""`manage.py engine_schema` against a real HTTP server on a thread."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command

from console.engine import schema
from tests.fakes.engine import recorded_schema
from tests.helpers import free_port


@contextmanager
def serving(doc: object, status: int = 200) -> Iterator[str]:
    payload = json.dumps(doc).encode() if not isinstance(doc, bytes) else doc

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/openapi.json":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def run(*args: str) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with pytest.raises(SystemExit) as exited:
        call_command("engine_schema", *args, stdout=out, stderr=err)
    code = exited.value.code
    assert isinstance(code, int)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def recorded(tmp_path: Path) -> Path:
    path = tmp_path / "openapi.json"
    path.write_text(schema.RECORDED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_check_matches_recorded(recorded: Path) -> None:
    with serving(recorded_schema()) as url:
        code, out, _ = run("--check", "--url", url, "--schema-path", str(recorded))
    assert code == 0
    assert out.strip() == "Engine API matches the recorded schema."


def test_check_ignores_info_version(recorded: Path) -> None:
    live = recorded_schema()
    live["info"]["version"] = "m99"
    with serving(live) as url:
        assert run("--check", "--url", url, "--schema-path", str(recorded))[0] == 0


def test_check_reports_extra_path(recorded: Path) -> None:
    live: dict[str, Any] = recorded_schema()
    live["paths"]["/brand-new"] = {"get": {"responses": {}}}
    with serving(live) as url:
        code, out, _ = run("--check", "--url", url, "--schema-path", str(recorded))
    assert code == 1
    assert "Operations added:\n  GET /brand-new\n" in out


def test_check_engine_down_exits_2(recorded: Path) -> None:
    url = f"http://127.0.0.1:{free_port()}"
    code, out, err = run("--check", "--url", url, "--schema-path", str(recorded))
    assert code == 2
    assert "connection failed" in err
    assert out == ""


@pytest.mark.parametrize(
    ("doc", "status", "why"), [({"x": 1}, 404, "HTTP 404"), (b"<html>", 200, "bad response")]
)
def test_check_bad_answers_exit_2(recorded: Path, doc: object, status: int, why: str) -> None:
    with serving(doc, status) as url:
        code, _, err = run("--check", "--url", url, "--schema-path", str(recorded))
    assert code == 2
    assert why in err


def test_no_url_exits_3(settings: Any, recorded: Path) -> None:
    settings.ENGINE_URL = None
    code, _, err = run("--check", "--schema-path", str(recorded))
    assert code == 3
    assert "no Engine URL: pass --url or set CONSOLE_ENGINE_URL" in err


def test_bad_url_exits_3(recorded: Path) -> None:
    assert run("--check", "--url", "ftp://x", "--schema-path", str(recorded))[0] == 3


def test_missing_recorded_file_exits_3(tmp_path: Path) -> None:
    with serving(recorded_schema()) as url:
        code, _, err = run("--check", "--url", url, "--schema-path", str(tmp_path / "none.json"))
    assert code == 3
    assert "recorded schema" in err


def test_url_defaults_to_settings(settings: Any, recorded: Path) -> None:
    with serving(recorded_schema()) as url:
        settings.ENGINE_URL = url
        assert run("--check", "--schema-path", str(recorded))[0] == 0


def test_write_is_byte_identical_across_runs(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    live = recorded_schema()
    with serving(live) as url:
        code, out, _ = run("--write", "--url", url, "--schema-path", str(target))
        first = target.read_bytes()
        run("--write", "--url", url, "--schema-path", str(target))
    assert code == 0
    assert out.startswith("Recorded ")
    assert f"operations, {len(schema.components(live))} components to {target}" in out
    assert target.read_bytes() == first
    assert first.endswith(b"}\n")
    assert first == schema.RECORDED_PATH.read_bytes()


def test_mode_is_required() -> None:
    with pytest.raises(Exception, match="one of the arguments"):
        call_command("engine_schema")


def test_never_calls_require(monkeypatch: pytest.MonkeyPatch, recorded: Path) -> None:
    from epistrel_console import engine_config

    def boom() -> None:
        raise AssertionError("require() called")

    monkeypatch.setattr(engine_config, "require", boom)
    with serving(recorded_schema()) as url:
        assert run("--check", "--url", url, "--schema-path", str(recorded))[0] == 0

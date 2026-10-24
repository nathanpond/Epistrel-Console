import pytest

from epistrel_console import engine_config
from epistrel_console.engine_config import BAD_URL, REQUIRED, parse, parse_url

KEY = "k"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("https://engine.example", "https://engine.example"),
        ("HTTP://Engine:8000", "http://Engine:8000"),
        ("  http://h:8000/  ", "http://h:8000"),
        ("http://h//", "http://h"),
        ("http://h/api/", "http://h/api"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_valid_urls_normalise(raw: str, expected: str) -> None:
    assert parse_url(raw) == (expected, None)


@pytest.mark.parametrize(
    "raw",
    [
        "localhost:8000",
        "ftp://x",
        "http://",
        "http://:8000",
        "http://x:abc",
        "http://x:70000",
        "http://x:0",
        "http://x:",
        "http://u:p@x",
        "http://@x",
        "http://x?q=1",
        "http://x?",
        "http://x#f",
        "http://x#",
        "http://[::1",
    ],
)
def test_invalid_urls_rejected(raw: str) -> None:
    assert parse_url(raw) == (None, BAD_URL)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_url_is_required(raw: str | None) -> None:
    assert parse_url(raw) == (None, REQUIRED)


@pytest.mark.parametrize("env", [{}, {"CONSOLE_ENGINE_SERVICE_KEY": ""}])
def test_key_absent_or_empty_is_none_and_required(env: dict[str, str]) -> None:
    config = parse({"CONSOLE_ENGINE_URL": "http://h", **env})
    assert config.service_key is None
    assert config.errors == (("CONSOLE_ENGINE_SERVICE_KEY", REQUIRED),)


def test_whitespace_key_counts_as_set() -> None:
    # Never trimmed (#29): a whitespace-only key is sent as is and the Engine answers 401.
    config = parse({"CONSOLE_ENGINE_URL": "http://h", "CONSOLE_ENGINE_SERVICE_KEY": " "})
    assert config.service_key == " "
    assert config.errors == ()


def test_key_kept_verbatim_and_hidden() -> None:
    secret = " sekrit-123 "  # noqa: S105 - a test value, not a credential
    config = parse({"CONSOLE_ENGINE_URL": "ftp://bad", "CONSOLE_ENGINE_SERVICE_KEY": secret})
    assert config.service_key == secret
    assert "sekrit" not in repr(config)
    assert all("sekrit" not in reason for _, reason in config.errors)


def test_unknown_console_variable_reported_with_url_error() -> None:
    config = parse({"CONSOLE_ENGINE_ULR": "x", "CONSOLE_ENGINE_SERVICE_KEY": KEY})
    assert config.errors == (
        ("CONSOLE_ENGINE_URL", REQUIRED),
        ("CONSOLE_ENGINE_ULR", engine_config.UNKNOWN),
    )


def test_valid_config_has_no_errors() -> None:
    config = parse(
        {
            "CONSOLE_ENGINE_URL": "http://h",
            "CONSOLE_ENGINE_SERVICE_KEY": KEY,
            "CONSOLE_DATA_DIR": "/data",
        }
    )
    assert config.url == "http://h"
    assert config.service_key == KEY
    assert config.errors == ()


def test_require_exits_with_one_line_per_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CONSOLE_ENGINE_ULR", "x")
    with pytest.raises(SystemExit) as exc:
        engine_config.require()
    assert exc.value.code == 1
    assert capsys.readouterr().err.splitlines() == [
        "error: CONSOLE_ENGINE_URL: is required",
        "error: CONSOLE_ENGINE_SERVICE_KEY: is required",
        "error: CONSOLE_ENGINE_ULR: unknown setting",
    ]


@pytest.mark.parametrize("key", [None, ""])
def test_require_refuses_without_a_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], key: str | None
) -> None:
    monkeypatch.setenv("CONSOLE_ENGINE_URL", "http://h")
    if key is not None:
        monkeypatch.setenv("CONSOLE_ENGINE_SERVICE_KEY", key)
    with pytest.raises(SystemExit) as exc:
        engine_config.require()
    assert exc.value.code == 1
    assert capsys.readouterr().err.splitlines() == [
        "error: CONSOLE_ENGINE_SERVICE_KEY: is required"
    ]


def test_require_passes_with_valid_url_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONSOLE_ENGINE_URL", "http://h")
    monkeypatch.setenv("CONSOLE_ENGINE_SERVICE_KEY", KEY)
    engine_config.require()

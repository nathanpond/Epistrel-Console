"""An Engine error a view lets escape renders the shared panel with 200, never a 500 (#43)."""

import logging

import pytest
from django.test import Client, override_settings

pytestmark = pytest.mark.urls("tests.urls_error_panel")


def test_engine_error_renders_code_message_and_details(operator_client: Client) -> None:
    response = operator_client.get("/_test/conflict/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "409" in html
    assert "<code>revision_conflict</code>" in html
    assert "The timeline moved on" in html
    assert "<dt>expected_revision</dt>" in html
    assert "<dd>3</dd>" in html
    flat = " ".join(html.split())
    assert "<dt>missing</dt> <dd>—</dd>" in flat
    # A list or dict detail is indented JSON in <pre>.
    assert "<pre>[\n  {\n    &quot;loc&quot;: [" in html
    # Secrets in details are masked.
    assert "sk-should-not-show" not in html
    assert "<dt>api_key</dt> <dd>***</dd>" in flat
    # A full page in the shell.
    assert "Log out" in html


def test_empty_details_show_a_dash(operator_client: Client) -> None:
    html = " ".join(operator_client.get("/_test/empty/").content.decode().split())
    assert "<code>not_found</code>" in html
    assert "<dt>Details</dt> <dd> — </dd>" in html


def test_post_also_renders_the_panel_with_200(operator_client: Client) -> None:
    response = operator_client.post("/_test/conflict/")
    assert response.status_code == 200
    assert "revision_conflict" in response.content.decode()


def test_unreachable_renders_the_unreachable_line(operator_client: Client) -> None:
    response = operator_client.get("/_test/unreachable/")
    # Complement: no 500.
    assert response.status_code == 200
    assert "Engine unreachable — timeout" in response.content.decode()


def test_not_configured_renders_its_line(operator_client: Client) -> None:
    response = operator_client.get("/_test/not-configured/")
    assert response.status_code == 200
    assert "Engine not configured — CONSOLE_ENGINE_URL is unset" in response.content.decode()


def test_htmx_request_gets_only_the_fragment(operator_client: Client) -> None:
    response = operator_client.get("/_test/conflict/", headers={"HX-Request": "true"})
    assert response.status_code == 200
    html = response.content.decode()
    assert html.lstrip().startswith('<div class="engine-error"')
    assert "revision_conflict" in html
    # Complement: no shell.
    assert "<html" not in html
    assert "Log out" not in html


def test_each_error_is_logged_once_without_details(
    operator_client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="console.engine"):
        operator_client.post("/_test/conflict/")
        operator_client.get("/_test/unreachable/")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("engine error")]
    assert lines == [
        "engine error 409 revision_conflict POST /_test/conflict/",
        "engine error - unreachable (timeout) GET /_test/unreachable/",
    ]
    assert all("sk-should-not-show" not in r.getMessage() for r in caplog.records)


@override_settings(DEBUG_PROPAGATE_EXCEPTIONS=False)
def test_other_exceptions_are_not_swallowed(operator_client: Client) -> None:
    # Complement: only Engine client errors become the panel.
    with pytest.raises(RuntimeError):
        operator_client.get("/_test/bug/")

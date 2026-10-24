"""The Console against the live Engine (``ENGINE_LIVE_URL``, ``ENGINE_LIVE_SERVICE_KEY``), through
its real client and pages.

Run by ci.yml's engine-smoke job against the Engine ``edge`` image; locally, see the README.
"""

import re

import pytest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_api_check import check_engine_api
from console.engine_probe import probe
from console.ids import new_ulid
from tests.plugins.engine_live import live_key, live_url

pytestmark = pytest.mark.engine_live


@pytest.fixture
def url() -> str:
    return live_url()


@pytest.fixture
def key() -> str:
    return live_key()


def test_client_health(url: str, key: str) -> None:
    result = EngineClient(url, key).health()
    assert result.http_status == 200
    assert result.body is not None, f"/health did not parse as HealthResponse: {result.raw}"
    assert result.body.status == "ok"
    assert result.body.checks.database == "ok"


def test_probe_says_healthy(url: str, key: str) -> None:
    result = probe(EngineClient(url, key))
    assert result.state == "healthy", result.reason


def test_status_page_and_health(url: str, key: str, operator_client: Client) -> None:
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        page = operator_client.get("/").content.decode()
        health = operator_client.get("/health")
    assert "Healthy" in page
    assert "Engine: Healthy" in page  # the shell's badge
    assert "Unhealthy" not in page
    assert "Unreachable" not in page
    assert health.status_code == 200
    engine = health.json()["engine"]
    assert engine["state"] == "healthy"
    assert engine["checks"]["database"] == "ok"


@pytest.mark.engine_auth
def test_status_page_service_key_accepted(url: str, key: str, operator_client: Client) -> None:
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        page = operator_client.get("/").content.decode()
    assert "Service key: accepted" in page


@pytest.mark.engine_auth
def test_list_stories_live(url: str, key: str) -> None:
    stories = EngineClient(url, key).list_stories()
    assert isinstance(stories, list)


@pytest.mark.engine_auth
def test_wrong_key_is_rejected_on_the_status_page(url: str, operator_client: Client) -> None:
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY="wrong-key-" + "0" * 32):
        page = operator_client.get("/").content.decode()
    assert "Service key: rejected (401)" in page


@pytest.mark.xfail(
    strict=False,
    reason="Engine edge API drift is reported by the daily drift check (#40), not the PR gate",
)
def test_live_engine_api_matches_the_recording(url: str, key: str) -> None:
    assert check_engine_api(EngineClient(url, key)).verdict == "matches"


@pytest.mark.engine_auth
def test_keyed_call_is_accepted(url: str, key: str) -> None:
    # The Engine enforces service auth on every route but the exempt ones (Engine #44).
    response = EngineClient(url, key)._request("GET", "/stories")
    assert response.status_code == 200, response.text


@pytest.mark.engine_auth
def test_story_create_archive_list_delete_live(
    url: str, key: str, operator_client: Client, operator: object
) -> None:
    """#45: create a story in the browser, archive it, see it listed, delete it."""
    ratings = EngineClient(url, key).list_content_ratings()
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        form = operator_client.get("/stories/new/").content.decode()
        signed = re.search(r'name="rating_choices" value="([^"]+)"', form)
        assert signed is not None, form
        response = operator_client.post(
            "/stories/new/",
            {
                "name": "Console smoke harbour",
                "content_rating_id": ratings[0].id,
                "viewpoint": "",
                "idempotency_key": new_ulid(),
                "rating_choices": signed.group(1),
                "characters-TOTAL_FORMS": "2",
                "characters-INITIAL_FORMS": "0",
                "characters-0-name": "Mara",
                "characters-0-definition": "A harbour pilot.",
                "characters-0-true_age": "34",
                "characters-0-is_player": "on",
                "characters-1-name": "Tobin",
                "characters-1-definition": "Her brother.",
                "characters-1-true_age": "41",
            },
        )
        assert response.status_code == 302, response.content.decode()
        story_id = response["Location"].strip("/").rsplit("/", 1)[-1]
        try:
            archived = operator_client.post(
                f"/stories/{story_id}/archive/", {"idempotency_key": new_ulid()}
            )
            assert archived.status_code == 302, archived.content.decode()
            listing = operator_client.get("/stories/").content.decode()
            assert f"<code>{story_id}</code>" in listing  # archived stories are listed
            row = listing[listing.index(f"<code>{story_id}</code>") :]
            assert '<td class="status">archived</td>' in row[: row.index("</tr>")]
            assert "<td>2</td>" in row[: row.index("</tr>")]  # the cast
        finally:
            deleted = operator_client.post(
                f"/stories/{story_id}/delete/", {"idempotency_key": new_ulid()}
            )
        assert deleted.status_code == 302, deleted.content.decode()
        assert f"<code>{story_id}</code>" not in operator_client.get("/stories/").content.decode()


@pytest.mark.engine_auth
def test_story_refusal_is_placed_beside_the_row_live(
    url: str, key: str, operator_client: Client
) -> None:
    """#45 demo step 3: two characters named "Mara" — the Engine's refusal sits on row 2."""
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        form = operator_client.get("/stories/new/").content.decode()
        signed = re.search(r'name="rating_choices" value="([^"]+)"', form)
        default = re.search(r'<option value="([^"]+)" selected>', form)
        assert signed is not None, form
        assert default is not None, form
        page = operator_client.post(
            "/stories/new/",
            {
                "content_rating_id": default.group(1),
                "idempotency_key": new_ulid(),
                "rating_choices": signed.group(1),
                "characters-TOTAL_FORMS": "2",
                "characters-INITIAL_FORMS": "0",
                "characters-0-name": "Mara",
                "characters-0-definition": "A harbour pilot.",
                "characters-0-true_age": "34",
                "characters-1-name": "mara",
                "characters-1-definition": "Another Mara.",
                "characters-1-true_age": "30",
            },
        ).content.decode()
    second = page[page.index('id="character-1"') :]
    assert "same name" in second[: second.index("</fieldset>")], page
    assert "The Engine rejected the story:" in page


def _model_row(html: str, purpose: str) -> str:
    match = re.search(rf'<tr id="purpose-{purpose}">(.*?)</tr>', html, re.S)
    assert match is not None, html
    return match.group(1)


@pytest.mark.engine_auth
def test_model_purpose_edit_key_and_drop_live(
    url: str, key: str, operator_client: Client, operator: object
) -> None:
    """#46: edit a purpose, give it a key (never shown again), drop its override."""
    secret = f"sk-console-smoke-{new_ulid()}"
    purpose = "quality_judge"
    blank = dict.fromkeys(("base_url", "temperature", "top_p", "max_tokens", "timeout", "stop"), "")
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        try:
            saved = operator_client.post(
                f"/models/{purpose}/edit/",
                {**blank, "model": "console-smoke", "seed": "7", "idempotency_key": new_ulid()},
            )
            assert saved.status_code == 302, saved.content.decode()
            keyed = operator_client.post(
                f"/models/{purpose}/api-key/", {"api_key": secret, "idempotency_key": new_ulid()}
            )
            assert keyed.status_code == 302, keyed.content.decode()
            listing = operator_client.get("/models/").content.decode()
            row = _model_row(listing, purpose)
            assert "console-smoke" in row
            assert "own" in row
            assert "set (fingerprint" in row
            assert secret not in listing
            assert "Configured in M4 (#59)" in _model_row(listing, "embedding")
        finally:
            dropped = operator_client.post(
                f"/models/{purpose}/drop/", {"idempotency_key": new_ulid()}
            )
        assert dropped.status_code == 302, dropped.content.decode()
        row = _model_row(operator_client.get("/models/").content.decode(), purpose)
        assert "console-smoke" not in row
        assert "not set" in row

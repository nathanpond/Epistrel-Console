"""#50 against the live Engine: rebuild a story, change a deployment setting and see the change
in the history with who made it. #323: the strongest model tier and the re-certification
interval's Clear."""

import re

import pytest
from django.test import Client, override_settings

from console.engine.client import EngineClient, EngineError
from console.ids import new_ulid
from console.views.engine_panel import listed_models
from tests.engine_panel.helpers import form_data
from tests.plugins.engine_live import live_key, live_url

pytestmark = [pytest.mark.engine_live, pytest.mark.engine_auth]


def test_rebuild_from_the_panel_live(operator_client: Client) -> None:
    url, key = live_url(), live_key()
    client = EngineClient(url, key, principal="console:smoke")
    ratings = client.list_content_ratings()
    created = client.create_story(
        {
            "name": "Console smoke rebuild",
            "content_rating_id": ratings[0].id,
            "characters": [{"name": "Mara", "definition": "A harbour pilot.", "true_age": 34}],
        },
        idempotency_key=new_ulid(),
    )
    story_id = created.story_id
    assert story_id is not None
    try:
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            home = operator_client.get("/engine/").content.decode()
            assert f'<option value="{story_id}">Console smoke rebuild</option>' in home
            confirm = operator_client.get(f"/engine/rebuild/{story_id}/", {"name": "Smoke"})
            key_field = re.search(
                r'name="idempotency_key" value="([^"]+)"', confirm.content.decode()
            )
            assert key_field is not None
            done = operator_client.post(
                f"/engine/rebuild/{story_id}/",
                {"idempotency_key": key_field.group(1), "name": "Smoke"},
            )
            assert done.status_code == 302, done.content.decode()
            result = operator_client.get(done["Location"]).content.decode()
        assert "<code>transcript_messages</code>" in result
        assert "<code>story_heads</code>" in result
        assert "Engine operation id" in result
    finally:
        client.delete_story(story_id, idempotency_key=new_ulid())


def test_change_a_setting_and_see_it_in_config_changes_live(
    operator_client: Client, operator: object
) -> None:
    url, key = live_url(), live_key()
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        page = operator_client.get("/engine/settings/").content.decode()
        data = form_data(page)
        before = int(data["minor_age_threshold"])
        after = 21 if before != 21 else 22
        data["minor_age_threshold"] = str(after)
        saved = operator_client.post("/engine/settings/", data)
        assert saved.status_code == 302, saved.content.decode()
        flash = operator_client.get("/engine/settings/").content.decode()
        assert "Saved — config revision" in flash
        changes = operator_client.get(
            "/engine/config-changes/", {"setting": "minor_age_threshold"}
        ).content.decode()
        # Put it back, through the same form.
        restore = form_data(flash)
        restore["minor_age_threshold"] = str(before)
        assert operator_client.post("/engine/settings/", restore).status_code == 302
    assert re.search(rf"<code>{before}</code>\s*→\s*<code>{after}</code>", changes), changes
    assert '<a href="/operations/?operator=nate">console:nate</a>' in changes


def test_strongest_model_tier_live() -> None:
    """#323: ``model_tiers.strongest`` takes a model the deployment lists and refuses a made-up
    one at ``body.model_tiers.strongest``; the original value is restored."""
    client = EngineClient(live_url(), live_key(), principal="console:smoke")
    before = client.get_deployment_settings().model_tiers.strongest
    with pytest.raises(EngineError) as refused:
        client.update_admin_settings(
            {"model_tiers": {"strongest": f"made-up-{new_ulid()}"}}, idempotency_key=new_ulid()
        )
    assert refused.value.http_status == 422
    assert refused.value.details.get("field") == "body.model_tiers.strongest"
    assert refused.value.details.get("reason") == "unknown_model"
    listed = listed_models(client.list_model_config())
    if not listed:
        pytest.skip("the live model configuration lists no model")
    try:
        saved = client.update_admin_settings(
            {"model_tiers": {"strongest": listed[0]}}, idempotency_key=new_ulid()
        )
        assert saved.model_tiers.strongest == listed[0]
        assert client.get_deployment_settings().model_tiers.strongest == listed[0]
    finally:
        if client.get_deployment_settings().model_tiers.strongest != before:
            client.update_admin_settings(
                {"model_tiers": {"strongest": before}}, idempotency_key=new_ulid()
            )
    assert client.get_deployment_settings().model_tiers.strongest == before


def test_recertify_interval_set_and_clear_live(operator_client: Client, operator: object) -> None:
    """#323: the re-certification interval is set, then Cleared to ``null`` through the form
    (the Engine accepts ``null`` for it); the original value is restored."""
    url, key = live_url(), live_key()
    client = EngineClient(url, key, principal="console:smoke")
    before = client.get_deployment_settings().certification_recertify_interval_s
    name = "certification_recertify_interval_s"
    try:
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            data = form_data(operator_client.get("/engine/settings/").content.decode())
            data[name] = "172800" if before != 172800 else "259200"
            data.pop(f"clear_{name}", None)
            saved = operator_client.post("/engine/settings/", data)
            assert saved.status_code == 302, saved.content.decode()
            assert client.get_deployment_settings().certification_recertify_interval_s == int(
                data[name]
            )
            cleared = form_data(operator_client.get("/engine/settings/").content.decode())
            cleared[f"clear_{name}"] = "on"
            done = operator_client.post("/engine/settings/", cleared)
            assert done.status_code == 302, done.content.decode()
        assert client.get_deployment_settings().certification_recertify_interval_s is None
    finally:
        if client.get_deployment_settings().certification_recertify_interval_s != before:
            client.update_admin_settings({name: before}, idempotency_key=new_ulid())
    assert client.get_deployment_settings().certification_recertify_interval_s == before

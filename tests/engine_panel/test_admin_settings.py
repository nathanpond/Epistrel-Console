"""Deployment settings (#50): a merge-patch form over ``PUT /admin/settings``."""

import copy
import re
from typing import Any

import pytest
from django.test import Client

from console.engine_api_check import RECORDED
from console.forms.deployment import (
    CLEAR_PREFIX,
    DEFAULTS,
    NULLABLE,
    SECTIONS,
    DeploymentSettingsForm,
)
from console.models import EngineOperation
from tests.engine_panel.helpers import (
    RATINGS,
    SETTINGS,
    answer_settings,
    body,
    calls,
    error,
    form_data,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

URL = "/engine/settings/"
SCHEMAS = RECORDED["components"]["schemas"]
# Engine M5 deployment settings no Console form edits yet (#168 adds the field and removes this).
M5_NOT_YET_EDITABLE = {"minor_hot_budget_fraction"}


def _load(client: Client, engine: FakeEngine, **changes: Any) -> dict[str, str]:
    """GET the page (settings patched by ``changes``) and return its unchanged form data."""
    answer_settings(engine, **changes)
    html = client.get(URL).content.decode()
    engine.reset()
    return form_data(html)


def _saved(engine: FakeEngine, **changes: Any) -> None:
    saved = copy.deepcopy(engine_fixture("update_admin_settings"))
    saved.update(changes)
    engine.respond("PUT", SETTINGS, 200, json=saved)


def _puts(engine: FakeEngine) -> list[Any]:
    return calls(engine, "PUT", SETTINGS)


def _field_html(html: str, name: str) -> str:
    match = re.search(
        rf'<div class="field field-{name}">(.*?)</div>\s*(?=<div class="field|</fieldset)',
        html,
        re.S,
    )
    assert match is not None, name
    return match.group(1)


# --- the form ---------------------------------------------------------------------------------


def test_the_form_mirrors_the_settings_patch_schema() -> None:
    """Guard: one field per ``SettingsPatch`` property except ``profile_defaults`` (and the M5
    settings no form edits yet)."""
    patch = set(SCHEMAS["SettingsPatch"]["properties"]) - {"profile_defaults"}
    patch -= M5_NOT_YET_EDITABLE
    fields = set(DeploymentSettingsForm.base_fields)
    settings = {name for name in fields if not name.startswith(CLEAR_PREFIX)}
    assert settings == patch
    # Clear checkboxes exactly on the settings ``SettingsBody`` lets be null.
    body_props = SCHEMAS["SettingsBody"]["properties"]
    nullable = {
        name
        for name in patch
        if any(o.get("type") == "null" for o in body_props[name].get("anyOf", []))
    }
    assert set(NULLABLE) == nullable
    assert {n.removeprefix(CLEAR_PREFIX) for n in fields - settings} == nullable
    # Every setting is in exactly one section.
    listed = [name for _, names in SECTIONS for name in names]
    assert sorted(listed) == sorted(settings)


def test_help_texts_show_the_schema_defaults() -> None:
    body_props = SCHEMAS["SettingsBody"]["properties"]
    stated = {name: p["default"] for name, p in body_props.items() if "default" in p}
    for name, default in stated.items():
        if name in DEFAULTS:
            assert DEFAULTS[name] == default, name
    assert set(stated) - {"profile_defaults"} - M5_NOT_YET_EDITABLE <= set(DEFAULTS)


def test_the_page_shows_every_setting_in_sections(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_settings(engine, media_moderation_webhook_url="https://hooks.example/m")
    html = operator_client.get(URL).content.decode()

    for title, _ in SECTIONS:
        assert f"<legend>{title}</legend>" in html
    assert '<option value="00000000000000000000RAT001" selected>Everyone</option>' in html
    assert "Default: 18." in _field_html(html, "minor_age_threshold")
    assert '<span class="unit">years</span>' in _field_html(html, "minor_age_threshold")
    assert "{name} is replaced by the character&#x27;s name." in _field_html(
        html, "default_safe_fallback"
    )
    assert '<span class="unit">hours</span>' in _field_html(html, "idempotency_result_window")
    # Lists are one per line.
    assert "image/png\nimage/jpeg" in _field_html(html, "media_allowed_types")
    # Enums are selects, booleans checkboxes.
    assert '<select name="erasure_shared_media"' in html
    assert 'type="checkbox" name="feedback_export"' in html
    # Clear exists only on the three nullable settings.
    assert html.count('name="clear_') == 3
    assert "https://hooks.example/m" in _field_html(html, "media_moderation_webhook_url")
    # The M2 engine dials are not offered.
    for dial in ("lite_window_messages", "lite_summary_max_tokens", "max_model_calls_per_turn"):
        assert f'name="{dial}"' not in html


def test_a_retired_default_rating_is_labelled(operator_client: Client, engine: FakeEngine) -> None:
    ratings = copy.deepcopy(engine_fixture("list_content_ratings_all"))
    ratings["items"][1]["retired_at"] = "2026-10-24T22:00:00Z"
    answer_settings(engine, default_content_rating_id="00000000000000000000RAT002")
    engine.respond("GET", RATINGS, 200, json=ratings)

    html = operator_client.get(URL).content.decode()

    assert '<option value="00000000000000000000RAT002" selected>Teen (retired)</option>' in html
    assert calls(engine, "GET", RATINGS)[0].url.params["include_retired"] == "1"


def test_a_failed_ratings_read_shows_the_current_id_read_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_settings(engine)
    engine.respond("GET", RATINGS, 503, json=error("database_unavailable"))

    html = operator_client.get(URL).content.decode()

    field = _field_html(html, "default_content_rating_id")
    assert "disabled" in field
    assert "00000000000000000000RAT001" in field
    assert "The ratings could not be read" in field


def test_a_failed_settings_read_shows_the_panel_and_no_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SETTINGS, 503, json=error("database_unavailable", "down"))
    html = operator_client.get(URL).content.decode()
    assert "Engine error 503 database_unavailable" in html
    assert 'action="/engine/settings/"' not in html


# --- saving -----------------------------------------------------------------------------------


def test_save_sends_only_the_changed_settings(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    _saved(engine)
    data["minor_age_threshold"] = "21"

    response = operator_client.post(URL, data)

    assert response.status_code == 302
    assert response["Location"] == URL
    [put] = _puts(engine)
    assert body(put) == {"minor_age_threshold": 21}
    assert put.headers["Content-Type"] == "application/json"
    assert put.headers["Idempotency-Key"] == data["idempotency_key"]
    row = EngineOperation.objects.get()
    assert (row.kind, row.outcome, row.config_revision) == ("update_admin_settings", "ok", 1)
    engine.reset()
    answer_settings(engine)
    html = operator_client.get(URL).content.decode()
    assert "Saved — config revision 1 (minor_age_threshold)" in html
    assert "changed elsewhere" not in html


def test_no_change_sends_no_write(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)

    response = operator_client.post(URL, data)

    assert response.status_code == 302
    assert engine.requests == []  # no write, and no read either (the snapshot is signed)
    answer_settings(engine)
    assert "No changes — nothing was sent." in operator_client.get(URL).content.decode()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("media_moderation_webhook_url", "https://hooks.example/m"),
        ("public_base_url", "https://console.example"),
        ("max_characters_at_start_ceiling", 40),
    ],
)
def test_clear_sends_null(
    operator_client: Client, engine: FakeEngine, name: str, value: Any
) -> None:
    data = _load(operator_client, engine, **{name: value})
    _saved(engine)
    data[CLEAR_PREFIX + name] = "on"

    operator_client.post(URL, data)

    [put] = _puts(engine)
    assert body(put) == {name: None}


def test_a_blank_nullable_setting_needs_clear(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine, public_base_url="https://console.example")
    data["public_base_url"] = ""

    html = operator_client.post(URL, data).content.decode()

    assert "Enter a value, or tick Clear to unset it." in _field_html(html, "public_base_url")
    assert _puts(engine) == []


@pytest.mark.parametrize(
    "name",
    ["minor_age_threshold", "default_engine_profile_id", "turn_queue_max", "timer_lag_alert"],
)
def test_no_other_setting_can_send_null(
    operator_client: Client, engine: FakeEngine, name: str
) -> None:
    data = _load(operator_client, engine)
    data[name] = ""
    data[CLEAR_PREFIX + name] = "on"  # a forged Clear is ignored

    html = operator_client.post(URL, data).content.decode()

    assert "This field is required." in _field_html(html, name)
    assert engine.requests == []


def test_a_list_is_sent_whole(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    tags = data["feedback_tags"].splitlines()
    data["feedback_tags"] = "\n".join(tags[:-1]) + "\n\n"
    _saved(engine)

    operator_client.post(URL, data)

    [put] = _puts(engine)
    assert body(put) == {"feedback_tags": tags[:-1]}


def test_a_bad_list_entry_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    data["feedback_tags"] = "Too Long Tag"

    html = operator_client.post(URL, data).content.decode()

    assert "Not 1 to 40 of a-z 0-9 _: Too Long Tag" in _field_html(html, "feedback_tags")
    assert engine.requests == []


def test_feedback_retention_days(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    assert data["feedback_retention_0"] == "story_lifetime"
    data["feedback_retention_0"] = "days"
    data["feedback_retention_1"] = "30"
    _saved(engine)

    operator_client.post(URL, data)

    assert body(_puts(engine)[0]) == {"feedback_retention": 30}


def test_feedback_retention_back_to_story_lifetime(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = _load(operator_client, engine, feedback_retention=30)
    assert (data["feedback_retention_0"], data["feedback_retention_1"]) == ("days", "30")
    data["feedback_retention_0"] = "story_lifetime"
    _saved(engine)

    operator_client.post(URL, data)

    assert body(_puts(engine)[0]) == {"feedback_retention": "story_lifetime"}


def test_feedback_retention_days_need_a_number(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    data["feedback_retention_0"] = "days"
    data["feedback_retention_1"] = ""

    html = operator_client.post(URL, data).content.decode()

    assert "Enter the number of days" in _field_html(html, "feedback_retention")
    assert engine.requests == []


def test_out_of_range_values_make_no_call(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    data["minor_age_threshold"] = "121"

    html = operator_client.post(URL, data).content.decode()

    assert "less than or equal to 120" in _field_html(html, "minor_age_threshold")
    assert engine.requests == []


def test_a_concurrent_change_is_mentioned(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)  # read at config revision 0
    _saved(engine, config_revision=3)
    data["minor_age_threshold"] = "21"

    operator_client.post(URL, data)

    answer_settings(engine)
    html = operator_client.get(URL).content.decode()
    assert "Saved — config revision 3" in html
    assert "Settings were also changed elsewhere since you loaded this page" in html


# --- the Engine's refusals --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "answer", "field", "text"),
    [
        (
            422,
            error(
                "rating_retired",
                "content rating X cannot be assigned (retired)",
                field="default_content_rating_id",
                rating_id="X",
                reason="retired",
            ),
            "default_content_rating_id",
            "cannot be assigned (retired)",
        ),
        (
            422,
            error(
                "profile_deleted",
                "engine profile P cannot be assigned (unknown)",
                field="body.default_engine_profile_id",
                profile_id="P",
                reason="unknown",
            ),
            "default_engine_profile_id",
            "cannot be assigned (unknown)",
        ),
        (
            422,
            error(
                "validation_error",
                "Input should be greater than or equal to 1",
                field="body.turn_queue_max",
                errors=[
                    {
                        "field": "body.turn_queue_max",
                        "message": "Input should be greater than or equal to 1",
                        "type": "greater_than_equal",
                    }
                ],
            ),
            "turn_queue_max",
            "Input should be greater than or equal to 1",
        ),
        (
            409,
            error("listing_status_in_use", "in use", value="in_review", count=3),
            "listing_status_values",
            "“in_review” is still held by 3 catalog entries",
        ),
        (
            409,
            error(
                "feedback_tag_in_use",
                "in use",
                in_use=[{"tag": "too_long", "current": 2, "history": 5}],
            ),
            "feedback_tags",
            "Still in use: too_long (current 2, history 5)",
        ),
    ],
)
def test_refusals_are_placed_at_their_fields(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    answer: dict[str, Any],
    field: str,
    text: str,
) -> None:
    data = _load(operator_client, engine)
    data["minor_age_threshold"] = "21"
    engine.respond("PUT", SETTINGS, status, json=answer)

    response = operator_client.post(URL, data)

    html = response.content.decode()
    assert response.status_code == 200
    assert text in _field_html(html, field)
    assert 'class="engine-error"' not in html
    # Re-rendered with a fresh idempotency key (#43) and the operator's input kept.
    assert data["idempotency_key"] not in html
    assert 'name="minor_age_threshold" value="21"' in html


def test_a_superseded_setting_names_its_replacement(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = _load(operator_client, engine)
    data["minor_age_threshold"] = "21"
    replacement = "profile_defaults.memory.rolling_summary.window_messages"
    engine.respond(
        "PUT",
        SETTINGS,
        422,
        json=error(
            "validation_error",
            "lite_window_messages moved",
            field="body.lite_window_messages",
            reason="superseded",
            replacement=replacement,
        ),
    )

    html = operator_client.post(URL, data).content.decode()

    assert f"Moved to profile defaults: {replacement}" in html


def test_other_errors_show_the_panel_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = _load(operator_client, engine)
    data["minor_age_threshold"] = "21"
    engine.respond("PUT", SETTINGS, 409, json=error("idempotency_conflict", "reused key"))

    html = operator_client.post(URL, data).content.decode()

    assert "Engine error 409 idempotency_conflict" in html
    assert 'name="minor_age_threshold" value="21"' in html


def test_a_read_only_rating_still_lets_other_settings_save(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_settings(engine)
    engine.respond("GET", RATINGS, 503, json=error("database_unavailable"))
    data = form_data(operator_client.get(URL).content.decode())
    engine.reset()
    data["default_content_rating_id"] = "00000000000000000000RAT004"  # forged: disabled field
    data["minor_age_threshold"] = "21"
    _saved(engine)

    operator_client.post(URL, data)

    assert body(_puts(engine)[0]) == {"minor_age_threshold": 21}

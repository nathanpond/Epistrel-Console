"""Deployment settings (#50): a merge-patch form over ``PUT /admin/settings``."""

import copy
import re
from typing import Any

import httpx
import pytest
from django import forms
from django.test import Client

from console.engine_api_check import RECORDED
from console.forms.deployment import (
    CLEAR_PREFIX,
    DEFAULTS,
    LIST_MAX,
    NULLABLE,
    PROVIDER_HOST,
    SECTIONS,
    DeploymentSettingsForm,
)
from console.models import EngineOperation
from console.world import ENGINE_NOTHING_CHANGED
from tests.engine_panel.helpers import (
    RATINGS,
    SETTINGS,
    answer_settings,
    body,
    calls,
    error,
    form_data,
    settings_body,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

URL = "/engine/settings/"
SCHEMAS = RECORDED["components"]["schemas"]
HOSTS = "provider_allowed_hosts"
HOST_ERROR = "Not a host, IPv4/IPv6 address or host:port: "


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


# #291: M7's deployment settings recorded but not yet on the form (test-only). #322 took the 13
# rewrite, direction and Lite reach settings; #323 empties it.
M7_NOT_YET_EDITABLE = {
    "bundle_export_ttl_hours",
    "bundle_max_entries",
    "bundle_max_entry_bytes",
    "bundle_max_uncompressed_bytes",
    "certification_canary_enabled",
    "certification_canary_interval_s",
    "certification_canary_sample_size",
    "certification_canary_seeds",
    "certification_recertify_interval_s",
    "model_tiers",
}


def test_the_m7_deployment_settings_are_recorded_and_not_yet_on_the_form() -> None:
    """**Complement:** every exempt name is a recorded ``SettingsPatch`` property (a typo can't
    silently exempt nothing) and none has a form field yet."""
    assert len(M7_NOT_YET_EDITABLE) == 10
    assert set(SCHEMAS["SettingsPatch"]["properties"]) >= M7_NOT_YET_EDITABLE
    assert not M7_NOT_YET_EDITABLE & set(DeploymentSettingsForm.base_fields)


def test_the_form_mirrors_the_settings_patch_schema() -> None:
    """Guard: one field per ``SettingsPatch`` property except ``profile_defaults`` (and, until
    #322/#323, :data:`M7_NOT_YET_EDITABLE`)."""
    patch = set(SCHEMAS["SettingsPatch"]["properties"]) - {"profile_defaults"} - M7_NOT_YET_EDITABLE
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
    stated = {
        name: p["default"]
        for name, p in body_props.items()
        if "default" in p and name not in M7_NOT_YET_EDITABLE
    }
    for name, default in stated.items():
        if name in DEFAULTS:
            assert DEFAULTS[name] == default, name
    assert set(stated) - {"profile_defaults"} <= set(DEFAULTS)


# #322: the two Engine M7 sections, their 13 settings in table order.
M7_REWRITE_SECTIONS = ("History rewrites and directions", "Lite rewrites and author facts")
M7_REWRITE_SETTINGS = tuple(
    name for title, names in SECTIONS if title in M7_REWRITE_SECTIONS for name in names
)
M7_UNITS = {
    "rewrite_ambiguity_margin": "",
    "rewrite_max_open_sessions": "sessions",
    "rewrite_session_ttl_minutes": "minutes",
    "rewrite_stale_retries": "retries",
    "directive_steer_ttl_minutes": "minutes",
    "dependency_search_max_depth": "levels",
    "dependency_search_max_results": "results",
    "lite_author_facts_max": "facts",
    "lite_reach_chunk_messages": "lines",
    "lite_reach_chunk_tokens": "tokens",
    "lite_reach_max_calls": "calls",
    "lite_reach_parallelism": "chunks",
    "lite_reach_deadline_seconds": "s",
}


def test_m7_rewrite_settings_bounds_match_the_schema() -> None:
    body_props = SCHEMAS["SettingsBody"]["properties"]
    assert len(M7_REWRITE_SETTINGS) == 13
    for name in M7_REWRITE_SETTINGS:
        field = DeploymentSettingsForm.base_fields[name]
        assert isinstance(field, forms.IntegerField | forms.FloatField), name
        prop = body_props[name]
        assert (field.min_value, field.max_value) == (prop["minimum"], prop["maximum"]), name
        assert DEFAULTS[name] == prop["default"], name
        assert field.required, name  # none of the 13 is nullable: no Clear
        assert name not in NULLABLE


def test_existing_help_texts_are_unchanged() -> None:
    """``extra`` defaults to "", so the fields before #322 keep main's help byte for byte."""
    expected = {
        "minor_age_threshold": "Default: 18.",
        "turn_queue_max": "Default: 5.",
        "max_characters_at_start_ceiling": "Default: unset.",
        "uncertain_below": "Default: 0.5.",
    }
    for name, text in expected.items():
        assert DeploymentSettingsForm.base_fields[name].help_text == text, name


def test_provider_host_pattern_matches_the_schema() -> None:
    item = SCHEMAS["SettingsBody"]["properties"][HOSTS]["items"]
    assert PROVIDER_HOST.pattern == item["pattern"]
    assert item["maxLength"] == 260
    assert SCHEMAS["SettingsBody"]["properties"][HOSTS]["maxItems"] == 100


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
    # External providers (#241): its own anchored section, the hosts one per line, no Clear.
    assert "bank.example\n127.0.0.1:8099" in _field_html(html, HOSTS)
    assert (
        '<fieldset class="settings-section" id="settings-external-providers">\n'
        "    <legend>External providers</legend>"
    ) in html
    assert 'id="settings-memory-and-retrieval"' in html
    assert 'Registered providers: <a href="/engine/providers/">Providers</a>' in html
    assert "clear_provider_allowed_hosts" not in html
    assert "Empty refuses every http provider" in _field_html(html, HOSTS)
    titles = [title for title, _ in SECTIONS]
    assert titles[titles.index("Catalog") + 1] == "External providers"
    assert "https://hooks.example/m" in _field_html(html, "media_moderation_webhook_url")
    # The M2 engine dials are not offered.
    for dial in ("lite_window_messages", "lite_summary_max_tokens", "max_model_calls_per_turn"):
        assert f'name="{dial}"' not in html


def test_m7_rewrite_sections_render(operator_client: Client, engine: FakeEngine) -> None:
    answer_settings(engine)
    html = operator_client.get(URL).content.decode()

    titles = [title for title, _ in SECTIONS]
    at = titles.index("Verification and gates")
    assert titles[at + 1 : at + 4] == [*M7_REWRITE_SECTIONS, "Retention"]
    assert 'id="settings-history-rewrites-and-directions"' in html
    assert 'id="settings-lite-rewrites-and-author-facts"' in html
    legends = [html.index(f"<legend>{title}</legend>") for title in titles[at : at + 4]]
    assert legends == sorted(legends)
    positions = [html.index(f'<div class="field field-{name}">') for name in M7_REWRITE_SETTINGS]
    assert positions == sorted(positions)
    assert legends[1] < positions[0]
    assert positions[-1] < legends[3]
    assert legends[2] < html.index('<div class="field field-lite_author_facts_max">')
    for name, unit in M7_UNITS.items():
        field = _field_html(html, name)
        assert f"Default: {DEFAULTS[name]}." in field, name
        if unit:
            assert f'<span class="unit">{unit}</span>' in field, name
        else:
            assert '<span class="unit">' not in field, name
        assert f'name="{CLEAR_PREFIX}{name}"' not in html
    assert (
        "Open rewrite sessions one story may hold (refused with rewrite_session_limit past it). "
        "Default: 5."
    ) in _field_html(html, "rewrite_max_open_sessions")
    assert "Default: 0.15." in _field_html(html, "rewrite_ambiguity_margin")
    assert 'max="1"' in _field_html(html, "rewrite_ambiguity_margin")


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


def test_changing_one_rewrite_setting_sends_only_it(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = _load(operator_client, engine)
    assert data["rewrite_max_open_sessions"] == "5"
    _saved(engine, rewrite_max_open_sessions=3)
    data["rewrite_max_open_sessions"] = "3"

    response = operator_client.post(URL, data)

    assert response.status_code == 302
    [put] = _puts(engine)
    assert body(put) == {"rewrite_max_open_sessions": 3}


def test_the_margin_compares_by_value(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    assert data["rewrite_ambiguity_margin"] == "0.15"
    data["rewrite_ambiguity_margin"] = "0.150"

    response = operator_client.post(URL, data)

    assert response.status_code == 302
    assert engine.requests == []


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


def test_media_types_keep_the_default_list_cap(operator_client: Client, engine: FakeEngine) -> None:
    """Complement (#241): ``_lines`` keeps ``LIST_MAX`` (50) for the other list settings."""
    data = _load(operator_client, engine)
    data["media_allowed_types"] = "\n".join(f"image/x{i}" for i in range(LIST_MAX + 1))

    html = operator_client.post(URL, data).content.decode()

    assert "List at most 50 entries." in _field_html(html, "media_allowed_types")
    assert engine.requests == []


# --- provider_allowed_hosts (#241) ----------------------------------------------------------


def test_hosts_reordered_or_recased_send_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = _load(operator_client, engine)
    first, second = data[HOSTS].splitlines()
    data[HOSTS] = f"{second}\n  {first.upper()}  \n\n"

    response = operator_client.post(URL, data)

    assert response.status_code == 302
    assert _puts(engine) == []
    assert engine.requests == []
    answer_settings(engine)
    assert "No changes — nothing was sent." in operator_client.get(URL).content.decode()


def test_hosts_change_sends_whole_list_in_stored_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = _load(operator_client, engine)
    data[HOSTS] = "127.0.0.1:8099\nShop.Example:8443\nbank.example"
    _saved(engine, provider_allowed_hosts=["bank.example", "127.0.0.1:8099", "shop.example:8443"])

    response = operator_client.post(URL, data)

    assert response.status_code == 302
    [put] = _puts(engine)
    assert body(put) == {HOSTS: ["bank.example", "127.0.0.1:8099", "shop.example:8443"]}
    answer_settings(engine)
    assert "Saved — config revision 1 (provider_allowed_hosts)" in (
        operator_client.get(URL).content.decode()
    )


def test_hosts_removed_and_added_keep_the_stored_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = _load(operator_client, engine)
    data[HOSTS] = "[::1]:9000\n127.0.0.1:8099\n[::1]:9000"  # a duplicate is dropped
    _saved(engine)

    operator_client.post(URL, data)

    assert body(_puts(engine)[0]) == {HOSTS: ["127.0.0.1:8099", "[::1]:9000"]}


def test_hosts_emptied_sends_empty_list(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    data[HOSTS] = "\n  \n"
    data[CLEAR_PREFIX + HOSTS] = "on"  # a forged Clear is ignored: never null
    _saved(engine, provider_allowed_hosts=[])

    operator_client.post(URL, data)

    [put] = _puts(engine)
    assert body(put) == {HOSTS: []}
    assert body(put)[HOSTS] is not None


@pytest.mark.parametrize("missing", ["absent", None])
def test_a_missing_original_reads_as_empty(
    operator_client: Client, engine: FakeEngine, missing: str | None
) -> None:
    """A missing or null original is ``[]``: an empty field sends nothing, an entry is sent."""
    data = _load(operator_client, engine)
    original = settings_body()
    if missing is None:
        original[HOSTS] = None
    else:
        del original[HOSTS]
    for typed, expected in (("", {}), ("Bank.Example", {HOSTS: ["bank.example"]})):
        posted = {**data, HOSTS: typed}
        form = DeploymentSettingsForm(posted, original=original, ratings=None)
        assert form.is_valid(), form.errors
        assert form.patch() == expected


@pytest.mark.parametrize("entry", ["https://bad/", "bad host", "host:port:extra", "a" * 261])
def test_bad_host_makes_no_call(operator_client: Client, engine: FakeEngine, entry: str) -> None:
    data = _load(operator_client, engine)
    data[HOSTS] = f"bank.example\n{entry}"

    html = operator_client.post(URL, data).content.decode()

    assert HOST_ERROR + entry in _field_html(html, HOSTS)
    assert engine.requests == []
    # D8: nothing was sent, so the page keeps the posted idempotency key.
    assert f'name="idempotency_key" value="{data["idempotency_key"]}"' in html


def test_more_than_100_hosts_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    data[HOSTS] = "\n".join(f"h{i}.example" for i in range(101))

    html = operator_client.post(URL, data).content.decode()

    assert "List at most 100 entries." in _field_html(html, HOSTS)
    assert engine.requests == []


def test_100_hosts_after_dropping_duplicates_are_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = _load(operator_client, engine)
    hosts = ["bank.example", "127.0.0.1:8099"] + [f"h{i}.example" for i in range(98)]
    data[HOSTS] = "\n".join([*hosts, "BANK.example"])  # 101 lines, 100 entries
    _saved(engine)

    operator_client.post(URL, data)

    assert body(_puts(engine)[0]) == {HOSTS: hosts}


def test_host_refusal_lands_on_its_line(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    data[HOSTS] += "\nshop.example:8443"
    message = "String should match pattern"
    engine.respond(
        "PUT",
        SETTINGS,
        422,
        json=error(
            "validation_error",
            message,
            field="body.provider_allowed_hosts[1]",
            errors=[{"field": "body.provider_allowed_hosts[1]", "message": message}],
        ),
    )

    response = operator_client.post(URL, data)

    html = response.content.decode()
    assert response.status_code == 200
    assert f"Line 2 (`127.0.0.1:8099`): {message}" in _field_html(html, HOSTS)
    assert 'class="engine-error"' not in html
    assert "provider_allowed_hosts[1]:" not in html  # not a non-field error too
    assert data["idempotency_key"] not in html  # D8: an answered refusal gets a fresh key


def test_host_refusal_without_an_index_shows_the_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = _load(operator_client, engine)
    data[HOSTS] = "bank.example"
    data["minor_age_threshold"] = "21"
    engine.respond(
        "PUT",
        SETTINGS,
        422,
        json=error(
            "validation_error",
            "too many",
            errors=[
                {"field": "body.provider_allowed_hosts", "message": "List should have at most"},
                {"field": "body.provider_allowed_hosts[7]", "message": "past the list"},
                {"field": "body.minor_age_threshold", "message": "bad age"},
            ],
        ),
    )

    html = operator_client.post(URL, data).content.decode()

    field = _field_html(html, HOSTS)
    assert "List should have at most" in field
    assert "past the list" in field
    assert "Line " not in field
    assert "bad age" in _field_html(html, "minor_age_threshold")  # others keep their placement


@pytest.mark.parametrize(
    ("name", "value"),
    [("minor_age_threshold", "21"), ("rewrite_max_open_sessions", "3")],
    ids=["minor_age_threshold", "rewrite_max_open_sessions"],
)
def test_engine_accepts_with_no_change(
    operator_client: Client, engine: FakeEngine, name: str, value: str
) -> None:
    """D8: an answer at the page's own config revision changed nothing."""
    data = _load(operator_client, engine)  # read at config revision 0
    data[name] = value
    _saved(engine, config_revision=0)

    response = operator_client.post(URL, data)

    assert response.status_code == 302
    assert len(_puts(engine)) == 1
    answer_settings(engine)
    html = operator_client.get(URL).content.decode()
    assert ENGINE_NOTHING_CHANGED in html
    assert "Saved — config revision" not in html


def test_a_transport_error_keeps_the_posted_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    """D8: the outcome is unknown, so a resubmit replays under the same key."""
    data = _load(operator_client, engine)
    data["minor_age_threshold"] = "21"
    engine.fail("PUT", SETTINGS, httpx.ReadTimeout("slow"))

    html = operator_client.post(URL, data).content.decode()

    assert f'name="idempotency_key" value="{data["idempotency_key"]}"' in html


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


def _out_of_bounds() -> list[Any]:
    """Each #322 field's min - 1 and max + 1 (the margin: -0.01 and 1.01), with the Django
    message stem and the limit as the field helper received it."""
    cases = []
    for name in M7_REWRITE_SETTINGS:
        field = DeploymentSettingsForm.base_fields[name]
        assert isinstance(field, forms.IntegerField | forms.FloatField)
        lo, hi = field.min_value, field.max_value
        assert isinstance(lo, int), name
        assert isinstance(hi, int), name
        if isinstance(field, forms.FloatField):  # the margin
            below, above = "-0.01", "1.01"
        else:
            below, above = str(lo - 1), str(hi + 1)
        cases.append(pytest.param(name, below, f"greater than or equal to {lo}", id=f"{name}-low"))
        cases.append(pytest.param(name, above, f"less than or equal to {hi}", id=f"{name}-high"))
    return cases


@pytest.mark.parametrize(("name", "value", "message"), _out_of_bounds())
def test_out_of_bounds_is_refused_locally(
    operator_client: Client, engine: FakeEngine, name: str, value: str, message: str
) -> None:
    data = _load(operator_client, engine)
    data[name] = value

    html = operator_client.post(URL, data).content.decode()

    field = _field_html(html, name)
    assert f"Ensure this value is {message}." in field
    # C10: the browser knows the bounds too.
    assert re.search(r'\bmin="', field)
    assert re.search(r'\bmax="', field)
    assert engine.requests == []


@pytest.mark.parametrize("value", ["0", "-0.1", "1.5"])
def test_minor_fraction_zero_is_refused_without_engine_call(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    data = _load(operator_client, engine)
    data["minor_hot_budget_fraction"] = value

    html = operator_client.post(URL, data).content.decode()

    field = _field_html(html, "minor_hot_budget_fraction")
    assert "Must be above 0 and at most 1" in field
    assert html.count("Must be above 0 and at most 1") == 1
    assert _puts(engine) == []
    assert engine.requests == []


def test_minor_fraction_field_is_a_bare_float_input(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_settings(engine)
    html = operator_client.get(URL).content.decode()

    field = _field_html(html, "minor_hot_budget_fraction")
    assert 'step="any"' in field
    assert " min=" not in field
    assert " max=" not in field
    assert (
        "Share of the per-character hot-memory budget a minor character&#x27;s unpinned hot "
        "memories are held to (above 0, at most 1). Default: 0.2."
    ) in field
    assert '<span class="unit">' not in field
    assert 'name="clear_minor_hot_budget_fraction"' not in html
    names = dict(SECTIONS)["Memory and retrieval"]
    assert names[names.index("memory_importance_half_life_turns") + 1] == (
        "minor_hot_budget_fraction"
    )


def test_minor_fraction_change_is_sent_alone(operator_client: Client, engine: FakeEngine) -> None:
    data = _load(operator_client, engine)
    _saved(engine, minor_hot_budget_fraction=0.5)
    data["minor_hot_budget_fraction"] = "0.5"

    response = operator_client.post(URL, data)

    assert response.status_code == 302
    [put] = _puts(engine)
    assert body(put) == {"minor_hot_budget_fraction": 0.5}


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
            422,
            error(
                "validation_error",
                "Input should be less than or equal to 500",
                field="body.lite_reach_max_calls",
                errors=[
                    {
                        "field": "body.lite_reach_max_calls",
                        "message": "Input should be less than or equal to 500",
                        "type": "less_than_equal",
                    }
                ],
            ),
            "lite_reach_max_calls",
            "Input should be less than or equal to 500",
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

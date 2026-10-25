"""A story's settings (#54): markers, the merge-patch form, resets, errors and the budget."""

import re
from pathlib import Path
from typing import Any

from django.test import Client

from console.models import EngineOperation
from console.story_settings import (
    CONSTANT_DEFAULTS,
    DEPLOYMENT_DEFAULTS,
    NULLABLE,
    POLICIES,
    SETTABLE,
    SUPERSEDED,
)
from tests.fakes.engine import FakeEngine, recorded_schema
from tests.settings.helpers import (
    ADMIN_SETTINGS,
    MARA,
    SETTINGS,
    STORY,
    URL,
    answer_page,
    body,
    error,
    form_data,
    section,
    settings_json,
    story_json,
    writes,
    written_json,
)


def _page(client: Client, engine: FakeEngine, **answers: Any) -> str:
    answer_page(engine, **answers)
    html: str = client.get(URL).content.decode()
    return html


def _marker(html: str, name: str) -> str:
    match = re.search(
        rf'<tr class="setting setting-{name}">.*?<td class="marker marker-\w+">([^<]*)</td>',
        html,
        re.S,
    )
    assert match is not None, name
    return match.group(1)


def _post(client: Client, html: str, **changes: Any) -> Any:
    data: dict[str, Any] = form_data(html, URL)
    for name, value in changes.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
    return client.post(URL, data)


# --- markers --------------------------------------------------------------------------------------


def test_defaults_and_story_values_are_marked(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)

    # Null nullable settings read "default (N)", N the deployment's own setting.
    assert _marker(html, "turn_queue_max") == "default (5)"
    assert _marker(html, "lite_summary_chunk") == "default (10)"
    assert _marker(html, "audit_retention_days") == "default (90)"
    # The fixture's viewpoint is set: the story's own.
    assert _marker(html, "default_output_viewpoint") == "story"
    # Constant-default fields compare against the table.
    assert _marker(html, "turn_concurrency") == "default"
    assert _marker(html, "regenerate_retry_budget") == "default"
    assert _marker(html, "allow_new_arrivals") == "default"
    # Reset shows only on fields marked "story".
    assert html.count('name="reset_') == 1
    assert 'name="reset_default_output_viewpoint"' in html


def test_a_value_other_than_the_default_is_the_storys(
    operator_client: Client, engine: FakeEngine
) -> None:
    settings = settings_json(
        turn_concurrency="queue",
        turn_queue_max=3,
        regenerate_retry_budget=4,
        allow_new_arrivals=False,
        default_output_viewpoint=None,
        audit_retention_days=30,
    )
    html = _page(operator_client, engine, settings=settings)

    for name in ("turn_concurrency", "turn_queue_max", "regenerate_retry_budget"):
        assert _marker(html, name) == "story"
    assert _marker(html, "allow_new_arrivals") == "story"
    assert _marker(html, "audit_retention_days") == "story"
    assert _marker(html, "default_output_viewpoint") == "default"
    assert 'name="reset_turn_concurrency"' in html
    assert 'name="reset_default_output_viewpoint"' not in html


def test_a_failed_deployment_read_marks_unknown_and_the_page_renders(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    engine.respond("GET", ADMIN_SETTINGS, 503, json=error("database_unavailable"))

    html = operator_client.get(URL).content.decode()

    assert _marker(html, "turn_queue_max") == "unknown"
    assert _marker(html, "turn_concurrency") == "default"
    settings = section(html, "settings")
    assert "The deployment's defaults could not be read" in settings
    assert "database_unavailable" in settings
    # The caps fall back to 50 pinned memories and 4,000 characters.
    assert "Up to 50 items" in section(html, "pinned")
    assert "Up to 4,000 characters" in section(html, "instructions")


def test_the_default_table_matches_the_recorded_settings_patch() -> None:
    patch = recorded_schema()["components"]["schemas"]["StorySettingsPatch"]
    properties = set(patch["properties"])
    assert properties == {*SETTABLE, POLICIES}
    assert set(CONSTANT_DEFAULTS) | set(NULLABLE) == set(SETTABLE)
    assert not set(CONSTANT_DEFAULTS) & set(NULLABLE)
    assert not set(SUPERSEDED) & properties
    for name in SETTABLE:  # every settable field accepts null (a reset)
        assert {"type": "null"} in patch["properties"][name]["anyOf"], name
    # Each deployment default exists in the deployment's settings.
    deployment = recorded_schema()["components"]["schemas"]["SettingsBody"]["properties"]
    assert set(DEPLOYMENT_DEFAULTS) <= set(deployment)
    # The enum's default is in its options.
    enum = patch["properties"]["turn_concurrency"]["anyOf"][0]["enum"]
    assert CONSTANT_DEFAULTS["turn_concurrency"] in enum


# --- the form -------------------------------------------------------------------------------------


def test_an_unchanged_form_sends_no_patch(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)

    response = _post(operator_client, html)

    assert response.status_code == 302
    assert response["Location"] == f"{URL}#settings"
    assert writes(engine) == []
    page = operator_client.get(URL).content.decode()
    assert "No changes — nothing was sent." in page


def test_one_change_sends_only_that_field(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond("PATCH", SETTINGS, 200, json=written_json(regenerate_retry_budget=4))

    response = _post(operator_client, html, regenerate_retry_budget="4")

    assert response.status_code == 302
    [patch] = writes(engine)
    assert body(patch) == {"regenerate_retry_budget": 4}
    assert "X-Epistrel-Expected-Revision" not in patch.headers
    flashed = operator_client.get(URL).content.decode()
    assert "Saved — config revision 4 (regenerate_retry_budget=4)" in flashed


def test_queue_with_a_size(operator_client: Client, engine: FakeEngine) -> None:
    """The demo: turn concurrency queue with queue size 3."""
    html = _page(operator_client, engine)
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    _post(operator_client, html, turn_concurrency="queue", turn_queue_max="3")

    [patch] = writes(engine)
    assert body(patch) == {"turn_concurrency": "queue", "turn_queue_max": 3}
    row = EngineOperation.objects.get()
    assert row.kind == "patch_story_settings"
    assert row.story_id == STORY


def test_reset_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    settings = settings_json(turn_concurrency="queue", turn_queue_max=3)
    html = _page(operator_client, engine, settings=settings)
    engine.respond("PATCH", SETTINGS, 200, json=written_json(turn_concurrency="reject"))

    _post(operator_client, html, reset_turn_concurrency="on", reset_turn_queue_max="on")

    [patch] = writes(engine)
    assert body(patch) == {"turn_concurrency": None, "turn_queue_max": None}


def test_a_blank_nullable_field_resets_it(operator_client: Client, engine: FakeEngine) -> None:
    settings = settings_json(lite_summary_chunk=20)
    html = _page(operator_client, engine, settings=settings)
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    _post(operator_client, html, lite_summary_chunk="", default_output_viewpoint="")

    [patch] = writes(engine)
    assert body(patch) == {"default_output_viewpoint": None, "lite_summary_chunk": None}


def test_the_viewpoint_takes_a_character_of_the_cast(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine, settings=settings_json(default_output_viewpoint=None))
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    _post(operator_client, html, default_output_viewpoint=f"character:{MARA}")

    [patch] = writes(engine)
    assert body(patch) == {"default_output_viewpoint": f"character:{MARA}"}


def test_allow_new_arrivals_sends_true_false_and_null(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    assert "Allow new arrivals — used by structured-pipeline stories" in html
    engine.respond("PATCH", SETTINGS, 200, json=written_json(allow_new_arrivals=False))
    _post(operator_client, html, allow_new_arrivals=None)  # unticked

    html = _page(operator_client, engine, settings=settings_json(allow_new_arrivals=False))
    _post(operator_client, html, allow_new_arrivals="on")
    _post(operator_client, html, reset_allow_new_arrivals="on")

    assert [body(r) for r in writes(engine)] == [
        {"allow_new_arrivals": False},
        {"allow_new_arrivals": True},
        {"allow_new_arrivals": None},
    ]


def test_out_of_range_input_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.requests.clear()

    response = _post(operator_client, html, turn_queue_max="51", regenerate_retry_budget="11")

    page = response.content.decode()
    assert response.status_code == 200
    assert engine.requests == []
    assert "Ensure this value is less than or equal to 50." in page
    assert "Ensure this value is less than or equal to 10." in page
    assert 'value="51"' in page  # input kept


def test_a_422_is_placed_beside_its_field(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PATCH",
        SETTINGS,
        422,
        json=error(
            "viewpoint_unresolved",
            "the default output viewpoint names no character of the cast",
            field="body.default_output_viewpoint",
            reason="unknown_character",
        ),
    )

    page = _post(operator_client, html, default_output_viewpoint="").content.decode()

    row = re.search(r'<tr class="setting setting-default_output_viewpoint">.*?</tr>', page, re.S)
    assert row is not None
    assert "names no character of the cast" in row.group(0)


def test_a_superseded_key_names_its_replacement(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PATCH",
        SETTINGS,
        422,
        json=error(
            "validation_error",
            "'turn_deadline' is an engine dial of the story's profile now",
            field="body.turn_deadline",
            reason="superseded",
            replacement="budgets.deadline_ms",
        ),
    )

    page = _post(operator_client, html, regenerate_retry_budget="3").content.decode()

    assert "Moved to engine profile overrides (budgets.deadline_ms)" in page
    assert re.search(r'class="errorlist nonfield">\s*<li>Moved to engine profile', page)


def test_superseded_dials_are_never_fields_and_old_keys_are_read_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    old = settings_json(turn_deadline=30, guardrail_retry_budget=1)
    html = _page(operator_client, engine, settings=old)
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    form = re.search(rf'<form method="post" action="{URL}".*?</form>', html, re.S)
    assert form is not None
    for name in SUPERSEDED:
        assert f'name="{name}"' not in form.group(0)
    others = html.split("<h3>Other settings</h3>", 1)[1]
    assert "<code>turn_deadline</code></th><td><code>30</code>" in others
    assert "<code>guardrail_retry_budget</code>" in others

    _post(operator_client, html, regenerate_retry_budget="5")

    [patch] = writes(engine)
    assert body(patch) == {"regenerate_retry_budget": 5}


def test_the_see_config_note_links_to_the_storys_config(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)

    note = re.search(r'<p class="note superseded-note">.*?</p>', html, re.S)
    assert note is not None
    assert "Moved to engine profile overrides — see" in note.group(0)
    assert f'<a href="/stories/{STORY}/config/">Config</a>' in note.group(0)


def test_the_effective_budget_is_shown(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine, settings=settings_json("get_story_settings_policies"))

    budget = section(html, "budget")
    assert '<dd class="budget-calls">11 <small class="source">source: 9 + G formula</small>' in (
        budget
    )
    assert '<dd class="budget-deadline">45.0 s</dd>' in budget
    assert "guardrail_input 1, guardrail_output 1" in budget
    assert '<dd class="budget-minimum">3</dd>' in budget
    assert "input 1, output 1" in budget
    assert f'<a href="/stories/{STORY}/config/">Change in Config</a>' in budget


def test_a_budget_from_the_profile_names_its_source(
    operator_client: Client, engine: FakeEngine
) -> None:
    settings = settings_json()
    settings["effective_budget"]["source"] = "profile"
    html = _page(operator_client, engine, settings=settings)

    assert "source: profile" in section(html, "budget")


def test_busy_shows_the_busy_line_with_input_kept(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PATCH",
        SETTINGS,
        423,
        json=error(
            "story_busy",
            "another turn is running",
            reason="in_flight",
            operation_id="01M5R8954SQXFAN5N439FEG4ZZ",
        ),
        headers={"Retry-After": "2"},
    )

    page = _post(operator_client, html, regenerate_retry_budget="7").content.decode()

    assert "Story busy — another turn is running" in page
    assert "<code>01M5R8954SQXFAN5N439FEG4ZZ</code>" in page
    assert "Retry after" in page
    assert 'name="regenerate_retry_budget" value="7"' in page


def test_a_failed_settings_read_degrades_only_its_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    engine.respond("GET", SETTINGS, 503, json=error("database_unavailable"))

    html = operator_client.get(URL).content.decode()

    assert "database_unavailable" in section(html, "settings")
    assert "Tobin owes Mara money." in section(html, "pinned")
    assert "Keep replies short." in section(html, "instructions")


def test_an_archived_story_disables_the_forms(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine, story=story_json(status="archived"))

    assert "Archived — unarchive to change the settings" in html
    assert html.count("<fieldset disabled>") == 4


def test_the_play_page_links_here() -> None:
    template = Path("templates/console/play/play.html").read_text(encoding="utf-8")
    assert "{% url 'stories:settings' story.id %}" in template

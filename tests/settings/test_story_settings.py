"""A story's settings (#54, #197): markers, the merge-patch form, resets, errors and the budget,
and Engine M5's player authority, player view and multi-character settings."""

import html as markup
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
    assert "Saved — config revision 4 (Regenerate retry budget: 4)" in flashed


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


# --- Engine M5: player authority, player view and the multi-character settings (#197) -----------

M5_KEYS = (
    "player_authority",
    "player_view",
    "interaction_mode",
    "sequence_order",
    "simultaneous_tie_break",
    "reactive_interjections",
)


def _row(html: str, name: str) -> str:
    match = re.search(rf'<tr class="setting setting-{name}">.*?</tr>', html, re.S)
    assert match is not None, name
    return match.group(0)


def _options(html: str, name: str) -> list[tuple[str, str]]:
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert select is not None, name
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', select.group(1))


def _selected(html: str, name: str) -> str:
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert select is not None, name
    chosen = re.search(r'<option value="([^"]*)"[^>]*selected', select.group(1))
    return chosen.group(1) if chosen else ""


def _flash(client: Client) -> str:
    page: str = client.get(URL).content.decode()
    match = re.search(r"Saved — config revision[^<]*", page)
    assert match is not None
    return markup.unescape(match.group(0))


def test_new_settings_rows_render_with_default_markers(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)

    rows = re.findall(r'<tr class="setting setting-(\w+)">', html)
    assert rows == list(SETTABLE)
    assert rows[-6:] == list(M5_KEYS)
    assert _marker(html, "player_authority") == "default"
    assert _marker(html, "player_view") == "default"
    assert _marker(html, "interaction_mode") == "default (sequential)"
    assert _marker(html, "sequence_order") == "default (request order)"
    assert _marker(html, "simultaneous_tie_break") == "default (request order)"
    assert _marker(html, "reactive_interjections") == "default"
    assert _options(html, "player_authority") == [
        ("own_character", "Own character only"),
        ("co_author", "Co-author (may narrate others&#x27; observable actions)"),
    ]
    assert "Lite stories get this as a prompt instruction only." in _row(html, "player_authority")
    assert _options(html, "player_view") == [
        ("character", "Character (audience-filtered)"),
        ("story", "Story (every beat in full)"),
    ]
    assert _options(html, "interaction_mode") == [
        ("", "Engine default (sequential)"),
        ("sequential", "Sequential"),
        ("simultaneous", "Simultaneous"),
    ]
    assert _options(html, "sequence_order") == [
        ("", "Engine default (request order)"),
        ("request", "Request order"),
        ("director", "Director"),
    ]
    assert _options(html, "simultaneous_tie_break") == [
        ("", "Engine default (request order)"),
        ("request_order", "Request order"),
        ("director", "Director"),
    ]
    assert _options(html, "reactive_interjections") == [("false", "Off"), ("true", "On")]
    assert _selected(html, "player_authority") == "own_character"
    assert _selected(html, "interaction_mode") == ""
    assert _selected(html, "reactive_interjections") == "false"
    for label in (
        "Player authority",
        "Player view (where perception is on)",
        "Interaction mode",
        "Sequence order (sequential turns)",
        "Tie-break (simultaneous turns)",
        "Reactive interjections",
    ):
        assert f">{label}</label>" in html
    # Complement: a null multi-character setting never reads "story" or "unknown", nor resets.
    for name in M5_KEYS:
        assert f'name="reset_{name}"' not in html
    # The M5 keys are rows, not "Other settings".
    others = html.split("<h3>Other settings</h3>", 1)
    assert len(others) == 1 or "<code>player_authority</code>" not in others[1]


def test_interaction_mode_default_names_the_scenario_value(
    operator_client: Client, engine: FakeEngine
) -> None:
    inactive = {"interaction_mode": {"value": "simultaneous", "capability": "multi_character"}}
    html = _page(operator_client, engine, settings=settings_json(inactive=inactive))

    assert _marker(html, "interaction_mode") == "default (scenario: simultaneous)"
    assert _options(html, "interaction_mode")[0] == ("", "Engine default (scenario: simultaneous)")
    assert _marker(html, "sequence_order") == "default (request order)"

    # Complement: a list-shaped or garbage ``inactive`` falls back to sequential.
    for garbage in (
        [{"interaction_mode": {"value": "simultaneous"}}],
        {"interaction_mode": "simultaneous"},
        {"interaction_mode": {"value": "all_at_once"}},
        "simultaneous",
    ):
        engine.requests.clear()
        html = _page(operator_client, engine, settings=settings_json(inactive=garbage))
        assert _marker(html, "interaction_mode") == "default (sequential)", garbage
        assert _options(html, "interaction_mode")[0] == ("", "Engine default (sequential)")


def test_a_stored_multi_setting_reads_story_with_a_reset(
    operator_client: Client, engine: FakeEngine
) -> None:
    settings = settings_json(
        interaction_mode="sequential", sequence_order="director", player_view="story"
    )
    html = _page(operator_client, engine, settings=settings)

    assert _marker(html, "interaction_mode") == "story"
    assert _marker(html, "sequence_order") == "story"
    assert _marker(html, "player_view") == "story"
    assert 'name="reset_interaction_mode"' in html
    assert _options(html, "interaction_mode")[0] == ("", "Engine default (sequential)")


def test_changed_multi_settings_patch_only_those_keys(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    _post(
        operator_client,
        html,
        player_authority="co_author",
        interaction_mode="simultaneous",
        simultaneous_tie_break="director",
        reactive_interjections="true",
    )

    [patch] = writes(engine)
    assert body(patch) == {
        "player_authority": "co_author",
        "interaction_mode": "simultaneous",
        "simultaneous_tie_break": "director",
        "reactive_interjections": True,
    }
    assert _flash(operator_client).startswith(
        "Saved — config revision 4 (Player authority: Co-author (may narrate others' observable "
        "actions), Interaction mode: Simultaneous, Tie-break: Director, Reactive interjections: "
        "On), idempotency key "
    )


def test_unchanged_form_sends_no_patch(operator_client: Client, engine: FakeEngine) -> None:
    settings = settings_json(
        player_authority="co_author",
        interaction_mode="simultaneous",
        sequence_order="request",
        simultaneous_tie_break="director",
        reactive_interjections=True,
    )
    html = _page(operator_client, engine, settings=settings)

    response = _post(operator_client, html)

    assert response.status_code == 302
    # Complement: zero PATCH calls in the fake log.
    assert [r for r in engine.requests if r.method == "PATCH"] == []


def test_explicit_default_choice_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond("PATCH", SETTINGS, 200, json=written_json(interaction_mode="sequential"))

    _post(operator_client, html, interaction_mode="sequential", sequence_order="request")

    [patch] = writes(engine)
    assert body(patch) == {"interaction_mode": "sequential", "sequence_order": "request"}


def test_engine_default_choice_over_a_stored_value_sends_null(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine, settings=settings_json(sequence_order="director"))
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    _post(operator_client, html, sequence_order="")

    [patch] = writes(engine)
    assert body(patch) == {"sequence_order": None}


def test_reset_sends_null_for_the_new_keys(operator_client: Client, engine: FakeEngine) -> None:
    settings = settings_json(
        player_authority="co_author",
        player_view="story",
        interaction_mode="simultaneous",
        sequence_order="director",
        simultaneous_tie_break="director",
        reactive_interjections=True,
    )
    html = _page(operator_client, engine, settings=settings)
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    _post(operator_client, html, **{f"reset_{name}": "on" for name in M5_KEYS})

    [patch] = writes(engine)
    assert body(patch) == dict.fromkeys(M5_KEYS)
    assert _flash(operator_client).startswith(
        "Saved — config revision 4 (Player authority: default, Player view: default, "
        "Interaction mode: default, Sequence order: default, Tie-break: default, "
        "Reactive interjections: default)"
    )


def test_reactive_interjections_off_is_not_sent_when_unchanged(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    engine.respond("PATCH", SETTINGS, 200, json=written_json())
    assert _selected(html, "reactive_interjections") == "false"

    _post(operator_client, html, regenerate_retry_budget="3")

    # Complement: a default-false read plus an untouched select sends no reactive_interjections.
    [patch] = writes(engine)
    assert body(patch) == {"regenerate_retry_budget": 3}

    html = _page(operator_client, engine, settings=settings_json(reactive_interjections=True))
    _post(operator_client, html, reactive_interjections="false")
    assert body(writes(engine)[-1]) == {"reactive_interjections": False}


def test_stored_state_tie_break_is_kept_and_not_resent(
    operator_client: Client, engine: FakeEngine
) -> None:
    settings = settings_json(simultaneous_tie_break="state:initiative")
    html = _page(operator_client, engine, settings=settings)
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    assert _options(html, "simultaneous_tie_break") == [
        ("", "Engine default (request order)"),
        ("request_order", "Request order"),
        ("director", "Director"),
        ("state:initiative", "state:initiative (M6: request order applies)"),
    ]
    assert _selected(html, "simultaneous_tie_break") == "state:initiative"
    assert _marker(html, "simultaneous_tie_break") == "story"

    _post(operator_client, html, player_view="story")

    # Complement: a save that changes another field doesn't carry the tie-break.
    [patch] = writes(engine)
    assert body(patch) == {"player_view": "story"}


def test_unknown_stored_value_is_kept_as_a_choice(
    operator_client: Client, engine: FakeEngine
) -> None:
    settings = settings_json(player_authority="full_author", interaction_mode="round_robin")
    html = _page(operator_client, engine, settings=settings)
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    assert _options(html, "player_authority")[-1] == ("full_author", "Full author")
    assert _selected(html, "player_authority") == "full_author"
    assert _marker(html, "player_authority") == "story"
    assert _options(html, "interaction_mode") == [
        ("", "Engine default (sequential)"),
        ("sequential", "Sequential"),
        ("simultaneous", "Simultaneous"),
        ("round_robin", "Round robin"),
    ]
    assert "(unknown)" not in _row(html, "interaction_mode")

    _post(operator_client, html, regenerate_retry_budget="1")

    [patch] = writes(engine)
    assert body(patch) == {"regenerate_retry_budget": 1}


def test_not_yet_422_lands_beside_tie_break(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PATCH",
        SETTINGS,
        422,
        json={
            "error": {
                "code": "validation_error",
                "message": "state tie-breaks are not available yet",
                "details": {"field": "body.interaction_mode", "reason": "not_yet"},
                "errors": [
                    {"field": "body.interaction_mode", "message": "not available yet"},
                ],
            }
        },
    )

    page = _post(operator_client, html, interaction_mode="simultaneous").content.decode()

    assert "State tie-breaks arrive in Engine M6" in _row(page, "simultaneous_tie_break")
    assert page.count("State tie-breaks arrive in Engine M6") == 1
    assert "not available yet" not in _row(page, "interaction_mode")


def test_a_422_on_a_new_key_lands_beside_its_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PATCH",
        SETTINGS,
        422,
        json=error("validation_error", "sequence order is refused", field="body.sequence_order"),
    )

    page = _post(operator_client, html, sequence_order="director").content.decode()

    assert "sequence order is refused" in _row(page, "sequence_order")


def test_flash_names_changes_with_labels(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine, settings=settings_json(interaction_mode="simultaneous"))
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    _post(
        operator_client,
        html,
        turn_concurrency="queue",
        interaction_mode="",
        allow_new_arrivals=None,
        regenerate_retry_budget="5",
        default_output_viewpoint="",
    )

    assert _flash(operator_client).startswith(
        "Saved — config revision 4 (Turn concurrency: queue, Default output viewpoint: default, "
        "Regenerate retry budget: 5, Allow new arrivals: Off, Interaction mode: default)"
    )


def test_blank_required_new_select_is_a_field_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    engine.requests.clear()

    response = _post(operator_client, html, player_authority="", reactive_interjections="")

    page = response.content.decode()
    assert response.status_code == 200
    assert "This field is required." in _row(page, "player_authority")
    assert "This field is required." in _row(page, "reactive_interjections")
    # Complement: zero PATCH calls.
    assert engine.requests == []


def test_note_links_config_without_an_effective_config_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)

    form = re.search(rf'<form method="post" action="{URL}".*?</form>', html, re.S)
    assert form is not None
    note = re.search(r'<p class="note settings-note">(.*?)</p>', form.group(0), re.S)
    assert note is not None
    assert note.group(1) == (
        "Interaction mode, sequence order, tie-break and reactive interjections apply only when "
        "the story's profile renders characters one by one "
        "(<code>multi_character.renders: per_character</code>); player view only where "
        "perception is on. A turn can override each. "
        f'See <a href="/stories/{STORY}/config/">Config</a>.'
    )
    # Above Save, inside the fieldset; no setting is greyed.
    assert form.group(0).index("settings-note") < form.group(0).index("Save settings")
    assert form.group(0).index("<fieldset>") < form.group(0).index("settings-note")
    for name in M5_KEYS:
        assert "disabled" not in _row(html, name)
    # Complement: no effective-config read.
    assert not any(r.url.path.endswith("/effective-config") for r in engine.requests)

"""/stories/new/ (#45): a Lite story with its cast, created through ``POST /stories``."""

import json
import re
from typing import Any

import pytest
from django.test import Client

from console.models import EngineOperation
from console.views import stories as views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.stories.helpers import calls

pytestmark = pytest.mark.django_db

URL = "/stories/new/"
NEW_ID = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
EVERYONE, TEEN = "00000000000000000000RAT001", "00000000000000000000RAT002"
RATINGS = [(EVERYONE, "Everyone"), (TEEN, "Teen"), ("00000000000000000000RAT003", "Mature")]
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KEY"


def character(**fields: Any) -> dict[str, Any]:
    return {"name": "", "definition": "", "true_age": "", "safe_fallback_text": "",
            "deflection_style": "", **fields}  # fmt: skip


MARA = character(name="Mara", definition="A harbour pilot.", true_age="34", is_player="on")
TOBIN = character(name="Tobin", definition="Her brother.", true_age="41")


def form_data(*rows: dict[str, Any], **story: Any) -> dict[str, Any]:
    """A posted new-story form, as the page renders it (signed rating options included)."""
    data: dict[str, Any] = {
        "name": "Harbour",
        "content_rating_id": TEEN,
        "viewpoint": "",
        "idempotency_key": KEY,
        "rating_choices": views._signed_ratings(RATINGS),
        "characters-TOTAL_FORMS": str(len(rows)),
        "characters-INITIAL_FORMS": "0",
        "characters-MIN_NUM_FORMS": "1",
        "characters-MAX_NUM_FORMS": "20",
        **story,
    }
    for index, row in enumerate(rows):
        for name, value in row.items():
            data[f"characters-{index}-{name}"] = value
    return data


def sent_body(engine: FakeEngine) -> Any:
    (request,) = calls(engine, "POST", "/stories")
    return json.loads(request.content)


def error_422(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def fieldset(html: str, index: int) -> str:
    start = html.index(f'id="character-{index}"')
    return html[start : html.index("</fieldset>", start)]


def field_block(html: str, name: str) -> str:
    """The ``<p>`` holding the form field ``name`` (its widget and its errors)."""
    start = html.index(f'id="id_{name}"')
    return html[html.rfind("<p", 0, start) : html.index("</p>", start)]


@pytest.fixture
def ratings(engine: FakeEngine) -> FakeEngine:
    engine.respond(
        "GET", "/admin/content-ratings", 200, json=engine_fixture("list_content_ratings")
    )
    engine.respond("GET", "/admin/settings", 200, json=engine_fixture("get_deployment_settings"))
    return engine


def test_form_preselects_the_default_rating_and_opens_with_one_row(
    operator_client: Client, ratings: FakeEngine
) -> None:
    html = operator_client.get(URL).content.decode()
    assert f'<option value="{EVERYONE}" selected>Everyone</option>' in html
    assert f'<option value="{TEEN}">Teen</option>' in html
    assert 'id="character-0"' in html
    assert 'id="character-1"' not in html
    assert "Add character" in html
    assert "Remove character" not in html  # never the last row
    assert re.search(r'name="idempotency_key" value="[0-9A-Z]{26}"', html)
    (listing,) = calls(ratings, "GET", "/admin/content-ratings")
    assert "include_retired" not in listing.url.params  # non-retired only


def test_valid_cast_posts_the_exact_engine_body_and_redirects(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    data = form_data(
        MARA,
        {**TOBIN, "safe_fallback_text": "Tobin shrugs."},
        name="Harbour",
        viewpoint="Mara",
    )
    response = operator_client.post(URL, data)
    assert response.status_code == 302
    assert response["Location"] == f"/stories/{NEW_ID}/"
    assert sent_body(engine) == {
        "name": "Harbour",
        "content_rating_id": TEEN,
        "characters": [
            {"name": "Mara", "definition": "A harbour pilot.", "true_age": 34, "is_player": True},
            {
                "name": "Tobin",
                "definition": "Her brother.",
                "true_age": 41,
                "is_player": False,
                "safe_fallback_text": "Tobin shrugs.",
            },
        ],
        "settings": {"default_output_viewpoint": "Mara"},
    }
    (request,) = calls(engine, "POST", "/stories")
    assert request.headers["Idempotency-Key"] == KEY
    assert request.headers["X-Epistrel-Principal"] == "console:nate"
    assert EngineOperation.objects.get().kind == "create_story"  # recorded (#44)
    flash = operator_client.get("/operations/").content.decode()
    assert "Created Harbour — revision 1" in flash


def test_blank_name_viewpoint_and_extra_rows_are_omitted(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    operator_client.post(URL, form_data(MARA, character(), name=""))
    body = sent_body(engine)
    assert body == {
        "content_rating_id": TEEN,
        "characters": [
            {"name": "Mara", "definition": "A harbour pilot.", "true_age": 34, "is_player": True}
        ],
    }


def test_engine_422_on_a_row_sits_on_that_row_and_keeps_the_input(
    operator_client: Client, engine: FakeEngine
) -> None:
    message = "Input should be less than or equal to 150"
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422(
            "validation_error",
            message,
            field="body.characters[1].true_age",
            errors=[{"field": "body.characters[1].true_age", "message": message, "type": "x"}],
        ),
    )
    html = operator_client.post(URL, form_data(MARA, TOBIN)).content.decode()
    second = fieldset(html, 1)
    assert message in second
    assert message not in fieldset(html, 0)
    assert 'value="Tobin"' in second
    assert "Her brother." in second
    assert 'value="Harbour"' in html
    assert f"The Engine rejected the story: {message}" in html
    assert 'role="alert"' not in html  # inline, not the error panel
    assert f'name="idempotency_key" value="{KEY}"' not in html  # a fresh key per render


def test_duplicate_name_refusal_appears_beside_the_second_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    message = "characters 0 and 1 have the same name"
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422(
            "validation_error", message, field="body.characters[1].name", reason="duplicate"
        ),
    )
    html = operator_client.post(URL, form_data(MARA, {**TOBIN, "name": "mara"})).content.decode()
    second = fieldset(html, 1)
    name_block = second[second.index("field-name") : second.index("field-definition")]
    assert message in name_block


def test_unresolved_viewpoint_sits_beside_the_viewpoint_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    message = "the default output viewpoint names no character of the cast"
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422(
            "viewpoint_unresolved",
            message,
            field="body.settings.default_output_viewpoint",
            reason="unknown_character",
        ),
    )
    html = operator_client.post(URL, form_data(MARA, viewpoint="Bob")).content.decode()
    assert message in field_block(html, "viewpoint")
    assert 'value="Bob"' in html
    assert '<datalist id="cast-names"><option value="Mara"></datalist>' in html


def test_add_character_works_without_js(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.post(URL, {**form_data(MARA), "add": "1"}).content.decode()
    assert 'id="character-1"' in html
    assert 'id="character-2"' not in html
    assert 'value="Mara"' in fieldset(html, 0)
    assert 'name="characters-TOTAL_FORMS" value="2"' in html
    assert "Remove character 1" in html
    assert "Remove character 2" in html
    assert '<ul class="errorlist' not in html  # the blank new row is not validated yet
    assert engine.requests == []


def test_remove_character_works_without_js(operator_client: Client, engine: FakeEngine) -> None:
    data = {**form_data(MARA, TOBIN), "remove": "0"}
    html = operator_client.post(URL, data).content.decode()
    assert 'id="character-1"' not in html
    assert 'value="Tobin"' in fieldset(html, 0)
    assert "Mara" not in fieldset(html, 0)
    assert "Remove character" not in html  # one row left
    assert engine.requests == []


def test_add_is_not_offered_at_twenty_rows(operator_client: Client, engine: FakeEngine) -> None:
    rows = [character(name=f"C{i}", definition="d", true_age="30") for i in range(19)]
    html = operator_client.post(URL, {**form_data(*rows), "add": "1"}).content.decode()
    assert 'name="characters-TOTAL_FORMS" value="20"' in html
    assert "Add character" not in html
    again = {**form_data(*rows, character()), "add": "1"}
    html = operator_client.post(URL, again).content.decode()
    assert 'name="characters-TOTAL_FORMS" value="20"' in html  # a crafted add does nothing
    assert engine.requests == []


def test_twenty_one_characters_are_refused_before_any_engine_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    rows = [character(name=f"C{i}", definition="d", true_age="30") for i in range(21)]
    html = operator_client.post(URL, form_data(*rows)).content.decode()
    assert "Please submit at most 20 forms." in html
    assert engine.requests == []


def test_age_over_150_is_refused_by_the_console(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.post(URL, form_data(MARA, {**TOBIN, "true_age": "200"})).content
    text = html.decode()
    assert "Ensure this value is less than or equal to 150." in fieldset(text, 1)
    assert 'value="Mara"' in fieldset(text, 0)
    assert engine.requests == []


def test_guardrail_rejection_shows_the_policy_and_keeps_the_input(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422(
            "guardrail_violation",
            "Input violates 1 policy.",
            violations=[
                {
                    "policy_id": "minors_no_sexual_content",
                    "name": "No sexual content involving minors",
                    "message": "A character under 18 is described sexually.",
                }
            ],
        ),
    )
    html = operator_client.post(URL, form_data(MARA)).content.decode()
    assert 'role="alert"' in html
    assert "guardrail_violation" in html
    assert "minors_no_sexual_content" in html
    assert "A character under 18 is described sexually." in html
    assert 'value="Mara"' in html
    assert "A harbour pilot." in html


def test_other_engine_errors_show_the_panel_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        "/stories",
        409,
        json=error_422("idempotency_conflict", "key reused with another body"),
    )
    html = operator_client.post(URL, form_data(MARA)).content.decode()
    assert "Engine error 409 idempotency_conflict" in html
    assert 'value="Mara"' in html


def test_non_blank_deflection_style_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    operator_client.post(URL, form_data({**MARA, "deflection_style": "Mara laughs it off."}))
    assert sent_body(engine)["characters"][0]["deflection_style"] == "Mara laughs it off."


def test_blank_deflection_style_is_omitted(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    operator_client.post(URL, form_data({**MARA, "deflection_style": "   "}))
    assert "deflection_style" not in sent_body(engine)["characters"][0]


def test_deflection_style_over_2000_is_refused_before_any_engine_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.post(URL, form_data({**MARA, "deflection_style": "x" * 2001}))
    assert "Ensure this value has at most 2000 characters (it has 2001)." in html.content.decode()
    assert engine.requests == []


def test_deflection_style_of_2000_is_accepted(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    response = operator_client.post(URL, form_data({**MARA, "deflection_style": "x" * 2000}))
    assert response.status_code == 302
    assert len(sent_body(engine)["characters"][0]["deflection_style"]) == 2000


def test_tampered_rating_options_are_fetched_again(
    operator_client: Client, ratings: FakeEngine
) -> None:
    ratings.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    response = operator_client.post(URL, form_data(MARA, rating_choices="forged"))
    assert response.status_code == 302
    assert len(calls(ratings, "GET", "/admin/content-ratings")) == 1


def test_rating_list_failure_shows_the_panel_in_place_of_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        "/admin/content-ratings",
        503,
        json=error_422("unavailable", "database unavailable"),
    )
    html = operator_client.get(URL).content.decode()
    assert "Engine error 503 unavailable" in html
    assert "Create story" not in html


# --- #198: creation settings and participation roles -----------------------------------------

SIX = (
    "player_authority",
    "player_view",
    "interaction_mode",
    "sequence_order",
    "simultaneous_tie_break",
    "reactive_interjections",
)
ENGINE_DEFAULTS = {
    "player_authority": "Engine default (own character)",
    "player_view": "Engine default (character)",
    "interaction_mode": "Engine default (sequential)",
    "sequence_order": "Engine default (request order)",
    "simultaneous_tie_break": "Engine default (request order)",
    "reactive_interjections": "Engine default (off)",
}


def created(engine: FakeEngine) -> None:
    engine.respond("POST", "/stories", 201, json=engine_fixture("create_story"))


def test_the_form_offers_the_six_settings_after_the_viewpoint(
    operator_client: Client, ratings: FakeEngine
) -> None:
    html = operator_client.get(URL).content.decode()
    positions = [html.index('id="id_viewpoint"')] + [html.index(f'id="id_{n}"') for n in SIX]
    assert positions == sorted(positions)
    for name, first in ENGINE_DEFAULTS.items():
        block = field_block(html, name)
        options = re.findall(r"<option value=\"([^\"]*)\"[^>]*>([^<]*)</option>", block)
        assert options[0] == ("", first), name
    assert "Lite stories get this as a prompt instruction only." in field_block(
        html, "player_authority"
    )
    assert "<small>" not in field_block(html, "player_view")
    row = fieldset(html, 0)
    assert row.index("field-deflection_style") < row.index("field-participation_role")
    assert row.index("field-participation_role") < row.index("field-participation_fixed")
    assert "Engine default (reactive)" in row
    assert "Leave on Engine default for the player&#x27;s character." in row
    assert "The engine never escalates or lowers a fixed role" in row


def test_new_story_sends_chosen_settings_only(operator_client: Client, engine: FakeEngine) -> None:
    created(engine)
    operator_client.post(
        URL,
        form_data(
            MARA,
            viewpoint="Mara",
            interaction_mode="simultaneous",
            player_authority="co_author",
            simultaneous_tie_break="director",
            reactive_interjections="true",
            player_view="",
            sequence_order="",
        ),
    )
    assert sent_body(engine)["settings"] == {
        "default_output_viewpoint": "Mara",
        "player_authority": "co_author",
        "interaction_mode": "simultaneous",
        "simultaneous_tie_break": "director",
        "reactive_interjections": True,
    }
    # Complement: untouched selects add no keys; an all-default form sends only the viewpoint.
    engine.reset()
    created(engine)
    operator_client.post(URL, form_data(MARA, viewpoint="Mara", **dict.fromkeys(SIX, "")))
    assert sent_body(engine)["settings"] == {"default_output_viewpoint": "Mara"}
    engine.reset()
    created(engine)
    operator_client.post(URL, form_data(MARA, **dict.fromkeys(SIX, "")))
    assert "settings" not in sent_body(engine)


def test_explicit_default_setting_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    created(engine)
    operator_client.post(
        URL,
        form_data(
            MARA,
            player_authority="own_character",
            player_view="character",
            interaction_mode="sequential",
            sequence_order="request",
            simultaneous_tie_break="request_order",
            reactive_interjections="false",
        ),
    )
    assert sent_body(engine)["settings"] == {
        "player_authority": "own_character",
        "player_view": "character",
        "interaction_mode": "sequential",
        "sequence_order": "request",
        "simultaneous_tie_break": "request_order",
        "reactive_interjections": False,
    }


def test_new_story_sends_participation_for_a_chosen_role(
    operator_client: Client, engine: FakeEngine
) -> None:
    created(engine)
    jenni = character(name="Jenni", definition="A neighbour.", true_age="29")
    operator_client.post(
        URL,
        form_data(
            MARA,
            {**TOBIN, "participation_role": "primary", "participation_fixed": "on"},
            {**jenni, "participation_role": "silent"},
            character(name="Ola", definition="A dog.", true_age="3", participation_role=""),
        ),
    )
    characters = sent_body(engine)["characters"]
    assert "participation" not in characters[0]
    assert characters[1]["participation"] == {"role": "primary", "fixed": True}
    assert characters[2]["participation"] == {"role": "silent", "fixed": False}
    assert "participation" not in characters[3]


def role_errors(html: str, index: int) -> str:
    row = fieldset(html, index)
    return row[row.index("field-participation_role") : row.index("field-participation_fixed")]


@pytest.mark.parametrize(
    "posted",
    [{"participation_role": "primary"}, {"participation_fixed": "on"}],
    ids=["role", "fixed-alone"],
)
def test_player_row_with_a_role_is_a_local_error(
    operator_client: Client, engine: FakeEngine, posted: dict[str, str]
) -> None:
    html = operator_client.post(URL, form_data({**MARA, **posted}, TOBIN)).content.decode()
    errors = role_errors(html, 0)
    assert errors.count("The player&#x27;s character has no role.") == 1
    assert "Choose a role to fix it." not in errors
    assert calls(engine, "POST", "/stories") == []


def test_fixed_without_role_is_a_local_error(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.post(
        URL, form_data(MARA, {**TOBIN, "participation_fixed": "on"})
    ).content.decode()
    assert "Choose a role to fix it." in role_errors(html, 1)
    assert "Choose a role to fix it." not in fieldset(html, 0)
    assert calls(engine, "POST", "/stories") == []
    # A blank extra row with only "Fixed" ticked counts as changed: required errors and the C8 one.
    engine.reset()
    html = operator_client.post(
        URL, form_data(MARA, character(participation_fixed="on"))
    ).content.decode()
    second = fieldset(html, 1)
    assert "This field is required." in second
    assert "Choose a role to fix it." in role_errors(html, 1)
    assert calls(engine, "POST", "/stories") == []


@pytest.mark.parametrize("name", SIX)
def test_settings_422_lands_beside_the_field(
    operator_client: Client, engine: FakeEngine, name: str
) -> None:
    message = f"{name} is not allowed here"
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422("validation_error", message, field=f"body.settings.{name}"),
    )
    html = operator_client.post(URL, form_data(MARA)).content.decode()
    assert message in field_block(html, name)
    assert f"The Engine rejected the story: {message}" in html


@pytest.mark.parametrize(
    "path",
    [
        "body.characters[1]",
        "body.characters[1].participation",
        "body.characters[1].participation.role",
        "body.characters[1].participation.fixed",
    ],
)
def test_participation_422_lands_on_its_row(
    operator_client: Client, engine: FakeEngine, path: str
) -> None:
    message = "the player's character has no participation role"
    engine.respond("POST", "/stories", 422, json=error_422("validation_error", message, field=path))
    html = operator_client.post(
        URL, form_data(TOBIN, {**MARA, "participation_role": ""})
    ).content.decode()
    assert message.replace("'", "&#x27;") in role_errors(html, 1)
    assert message.replace("'", "&#x27;") not in fieldset(html, 0)


def test_bare_settings_422_is_a_form_error(operator_client: Client, engine: FakeEngine) -> None:
    message = "Extra inputs are not permitted"
    engine.respond(
        "POST", "/stories", 422, json=error_422("validation_error", message, field="body.settings")
    )
    html = operator_client.post(URL, form_data(MARA, viewpoint="Mara")).content.decode()
    assert f"settings: {message}" in html
    assert message not in field_block(html, "viewpoint")
    # A creation setting the form doesn't show falls back the same way.
    engine.reset()
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422("validation_error", message, field="body.settings.turn_queue_max"),
    )
    html = operator_client.post(URL, form_data(MARA)).content.decode()
    assert f"settings.turn_queue_max: {message}" in html


def test_creation_error_paths_enumerate_the_fixed_keys() -> None:
    from console.forms.stories import CREATION_ERROR_PATHS

    assert set(CREATION_ERROR_PATHS) == {
        *(f"settings.{name}" for name in SIX),
        "settings.default_output_viewpoint",
        "viewpoint_unresolved",
    }
    assert len(CREATION_ERROR_PATHS) == 8
    assert CREATION_ERROR_PATHS["characters[3]"] == "characters[3].participation_role"
    assert "settings" not in CREATION_ERROR_PATHS
    assert "characters[3].true_age" not in CREATION_ERROR_PATHS

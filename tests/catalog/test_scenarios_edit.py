"""Create and edit a scenario's draft in the editor (#68): every section but the cast, the world
seeds and media."""

import json
from typing import Any

import pytest
from django.http import QueryDict
from django.test import Client

from console.engine.client import EngineError
from console.forms.policy import RULE_TEMPLATES, retemplate, rule_template_text
from console.models import EngineOperation
from tests.catalog.helpers import TEEN, answer_ratings, body_of, calls, error, text
from tests.catalog.scenario_helpers import (
    EDIT,
    NEW_SCN,
    NEW_URL,
    PATH,
    PROFILES,
    RATINGS_PATH,
    S_ITEM,
    S_LIST,
    SCN,
    answer_edit_reads,
    answer_profiles,
    as_post,
    draft,
    fixture,
    posted,
    writes,
)
from tests.fakes.engine import FakeEngine

pytestmark = pytest.mark.django_db


def edit_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    answer_edit_reads(engine, body)
    response = client.get(EDIT)
    assert response.status_code == 200
    engine.reset()
    return response


def form(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> QueryDict:
    """The edit page's state as it would post back unchanged."""
    return posted(edit_page(client, engine, body))


def save(client: Client, engine: FakeEngine, data: QueryDict, **answer: Any) -> Any:
    engine.respond(
        "PATCH", S_ITEM, answer.get("status", 200), json=answer.get("json", fixture("patch"))
    )
    return client.post(EDIT, as_post(data))


def patch_sent(engine: FakeEngine) -> dict[str, Any]:
    (sent,) = calls(engine, "PATCH", PATH)
    body: dict[str, Any] = body_of(sent)
    return body


def changes(engine: FakeEngine) -> dict[str, Any]:
    """The patch without its ``draft_revision``."""
    body = patch_sent(engine)
    assert body.pop("draft_revision") == 5
    return body


def flashes(response: Any) -> list[str]:
    return [str(m) for m in response.wsgi_request._messages]


# --- an untouched form ----------------------------------------------------------------------------


def test_an_untouched_form_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 302
    assert response["Location"] == EDIT
    assert engine.requests == []
    assert flashes(response) == ["No changes"]


def test_the_edit_page_header_and_sections(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine).content.decode()
    page = text(html)
    assert "Status: published · Draft revision 5 · latest v2" in page
    for legend in (
        "Card",
        "Metadata",
        "World",
        "Locations",
        "Entities",
        "Opening",
        "Story-setting defaults",
        "Story policies",
        "Recommendation",
        "Visual style",
        "Creation rules",
        "Drivers",
        "Plot hooks",
        "Deferred",
    ):
        assert f"<legend>{legend}</legend>" in html
    assert "Stored by Lite but not used until a later Engine milestone" in page
    assert 'name="op" value="refresh"' in html
    # The role selects offer the draft's cast; the location select the world's locations.
    assert '<option value="son" selected>son</option>' in html
    assert '<option value="farmhouse" selected>farmhouse</option>' in html
    assert 'value="mother" id="f-starting_state-opening-roles_present_0" checked' in html
    # World seeds, the cast and media are not in the form.
    assert "seeded_memories" not in html
    assert "The harvest is late this year." not in html


# --- each section round-trips into the patch ------------------------------------------------------


def test_each_section_round_trips_into_the_patch(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data["public_card__name"] = "Harvest Weekend II"
    data["public_card__public_description"] = "Everyone is home.\r\nFor once."
    data["metadata__labels"] = "family, Autumn"
    data["metadata__content_rating"] = "00000000000000000000RAT003"
    data["world__description"] = "A farmhouse."
    data["world__event_types"] = "harvest\r\nsupper\r\nbarn dance"
    data["loc-1-name"] = "The red barn"
    data["ent-1-kind"] = "place"
    data["ent-1-aliases"] = "the town\nRB"
    data["starting_state__opening__narration"] = "Dusk."
    data["starting_state__opening__starting_location"] = "barn"
    data.setlist("starting_state__opening__roles_present", ["mother", "son"])
    data["story_settings_defaults__allow_new_arrivals"] = "false"
    data["story_settings_defaults__player_authority"] = "co_author"
    data["story_settings_defaults__audit_retention_days"] = ""
    data["story_settings_defaults__default_output_viewpoint"] = "mother"
    data["story_settings_defaults__required_providers"] = "openai\nanthropic"
    data["recommendation__engine_profile_id"] = "structured"
    data["recommendation__capabilities"] = ""
    data["visual_style__prompt"] = "Oil painting"
    data["visual_style__negative_prompt"] = "blurry"
    data["creation_rules__overridable"] = "drivers.endings"
    data["creation_rules__bounds"] = '{"audit_retention_days": {"max": 30}}'
    data["drivers__director_guidance"] = ""
    data["hook-0-text"] = "A letter arrives.\nIt is unsigned."
    data["drivers__planned_reveals"] = "[]"
    data["starting_state__states"] = '[{"name": "weather", "value": "sun"}]'
    response = save(operator_client, engine, data)
    assert response.status_code == 302, response.content.decode()[:2000]
    assert flashes(response) == ["Saved — draft revision 6."]
    assert changes(engine) == {
        "public_card": {
            "name": "Harvest Weekend II",
            "public_description": "Everyone is home.\nFor once.",
        },
        "metadata": {
            "labels": ["family", "autumn"],
            "content_rating_id": "00000000000000000000RAT003",
        },
        "world": {
            "description": "A farmhouse.",
            "event_types": ["harvest", "supper", "barn dance"],
            "locations": [
                {
                    "id": "farmhouse",
                    "name": "The farmhouse",
                    "description": "Where everyone sleeps.",
                },
                {"id": "barn", "name": "The red barn"},
            ],
            "entities": [
                {
                    "id": "tractor",
                    "name": "Old Red",
                    "kind": "item",
                    "aliases": ["the tractor"],
                    "description": "The tractor that never starts.",
                },
                {"id": "town", "name": "Riverbend", "kind": "place", "aliases": ["the town", "RB"]},
            ],
        },
        "starting_state": {
            "opening": {
                "narration": "Dusk.",
                "starting_location": "barn",
                "roles_present": ["mother", "son"],
            },
            "states": [{"name": "weather", "value": "sun"}],
        },
        "story_settings_defaults": {
            "allow_new_arrivals": False,
            "player_authority": "co_author",
            "audit_retention_days": None,
            "default_output_viewpoint": "mother",
            "required_providers": ["openai", "anthropic"],
        },
        "recommendation": {"engine_profile_id": "structured", "capabilities": []},
        "visual_style": {"prompt": "Oil painting", "negative_prompt": "blurry"},
        "creation_rules": {
            "overridable": ["drivers.endings"],
            "bounds": {"audit_retention_days": {"max": 30}},
        },
        "drivers": {
            "director_guidance": None,
            "plot_hooks": ["A letter arrives.\nIt is unsigned."],
            "planned_reveals": [],
        },
    }


def test_only_changed_groups_are_sent(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["visual_style__negative_prompt"] = "blurry"
    save(operator_client, engine, data)
    sent = changes(engine)
    assert sent == {"visual_style": {"negative_prompt": "blurry"}}
    # Complement: every untouched group is absent.
    for group in ("public_card", "metadata", "world", "starting_state", "drivers", "cast"):
        assert group not in sent


def test_the_patch_never_carries_media_or_cast(operator_client: Client, engine: FakeEngine) -> None:
    body = draft()
    body["public_card"]["media"] = {"cover": "01JC000000000000000000MED1", "gallery": []}
    data = form(operator_client, engine, body)
    data["public_card__name"] = "Renamed"
    data["public_card__public_description"] = ""
    save(operator_client, engine, data)
    sent = changes(engine)
    assert sent == {"public_card": {"name": "Renamed", "public_description": None}}
    assert "cast" not in sent
    assert "seeded_memories" not in json.dumps(sent)


def test_event_types_cleared_send_null(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["world__event_types"] = "  \r\n"
    save(operator_client, engine, data)
    assert changes(engine) == {"world": {"event_types": None}}


# --- JSON boxes -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("starting_state__clock", "{oops", "Enter valid JSON"),
        ("starting_state__clock", "[1, 2]", "Enter a JSON object"),
        ("starting_state__states", '{"a": 1}', "Enter a JSON array"),
        ("starting_state__rules", "[NaN]", "NaN is not valid JSON"),
        ("creation_rules__bounds", '{"a": 1, "a": 2}', "duplicate key"),
        ("drivers__planned_reveals", "{}", "Enter a JSON array"),
    ],
)
def test_json_boxes_are_checked_before_any_call(
    operator_client: Client, engine: FakeEngine, name: str, value: str, message: str
) -> None:
    data = form(operator_client, engine)
    data["public_card__name"] = "Changed too"
    data[name] = value
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 200
    assert engine.requests == []
    errors = response.context["form"].errors[name]
    assert any(message in e for e in errors), errors


def test_clearing_the_clock_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["starting_state__clock"] = ""
    save(operator_client, engine, data)
    assert changes(engine) == {"starting_state": {"clock": None}}


def test_reformatting_a_json_box_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["starting_state__states"] = '[{"value":"rain",  "name":"weather"}]'
    data["creation_rules__bounds"] = '{"audit_retention_days":{"max":90}}'
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 302
    assert engine.requests == []
    assert flashes(response) == ["No changes"]


# --- rows -----------------------------------------------------------------------------------------


def test_rows_add_and_remove_without_js(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["op"] = "add:loc"
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 200
    assert engine.requests == []
    html = response.content.decode()
    assert 'name="loc-2-id"' in html
    assert 'name="loc-TOTAL_FORMS" value="3"' in html
    assert 'class="errorlist' not in html  # a row button re-renders without validating
    after = posted(response)
    after["op"] = "remove:loc-0"
    response = operator_client.post(EDIT, as_post(after))
    html = response.content.decode()
    assert engine.requests == []
    assert 'name="loc-TOTAL_FORMS" value="2"' in html
    assert (
        'value="farmhouse"' not in html.split('name="loc-TOTAL_FORMS"')[1].split("</fieldset>")[0]
    )
    # The starting location select follows the rows: farmhouse is kept, marked missing.
    assert '<option value="farmhouse" selected>farmhouse (missing)</option>' in html
    # Saving the edited rows sends the locations array whole (the new blank row is dropped).
    final = posted(response)
    final["starting_state__opening__starting_location"] = "barn"
    save(operator_client, engine, final)
    sent = changes(engine)
    assert sent["world"] == {"locations": [{"id": "barn", "name": "The barn"}]}
    assert sent["starting_state"] == {"opening": {"starting_location": "barn"}}


def test_a_new_policy_row_starts_from_the_template(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data["op"] = "add:pol"
    response = operator_client.post(EDIT, as_post(data))
    after = posted(response)
    assert json.loads(str(after["pol-1-rule"])) == RULE_TEMPLATES["wordlist"]
    # Update form re-templates an untouched box to the chosen type.
    after["pol-1-rule_type"] = "model_assisted"
    after["op"] = "refresh"
    response = operator_client.post(EDIT, as_post(after))
    assert engine.requests == []
    refreshed = posted(response)
    assert json.loads(str(refreshed["pol-1-rule"])) == RULE_TEMPLATES["model_assisted"]
    # An untouched new row is blank: dropped on save (nothing changed).
    response = operator_client.post(EDIT, as_post(refreshed))
    assert response.status_code == 302
    assert writes(engine) == []


def test_an_edited_rule_box_is_kept_on_refresh() -> None:
    edited = '{"type": "wordlist", "words": ["x"]}'
    assert retemplate(edited, "regex") == edited
    assert retemplate("", "regex") == rule_template_text("regex")
    assert retemplate("{bad", "regex") == "{bad"


def test_viewpoint_and_a_new_policy_are_saved(operator_client: Client, engine: FakeEngine) -> None:
    """Demo 2: set the default viewpoint and add a story policy."""
    data = form(operator_client, engine)
    data["story_settings_defaults__default_output_viewpoint"] = "mother"
    data["op"] = "add:pol"
    data = posted(operator_client.post(EDIT, as_post(data)))
    data["pol-1-name"] = "No spoilers"
    data["pol-1-rule_type"] = "regex"
    data["pol-1-rule"] = '{"type": "wordlist", "patterns": ["ending"]}'
    data["pol-1-message"] = "Not yet."
    save(operator_client, engine, data)
    sent = changes(engine)
    assert sent == {
        "story_settings_defaults": {
            "default_output_viewpoint": "mother",
            "policies": [
                {
                    "name": "No swearing",
                    "applies_to": ["text_output"],
                    "rule": {"type": "wordlist", "words": ["darn"]},
                    "age_condition": "minor_involved_or_present",
                    "message": "Keep it clean.",
                },
                # The select's type wins over the box's; no target ticked: applies_to omitted.
                {
                    "name": "No spoilers",
                    "rule": {"type": "regex", "patterns": ["ending"]},
                    "message": "Not yet.",
                },
            ],
        }
    }


def test_removing_the_last_policy_sends_an_empty_array(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data["op"] = "remove:pol-0"
    data = posted(operator_client.post(EDIT, as_post(data)))
    save(operator_client, engine, data)
    assert changes(engine) == {"story_settings_defaults": {"policies": []}}


def test_an_incomplete_row_is_refused_locally(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["op"] = "add:pol"
    data = posted(operator_client.post(EDIT, as_post(data)))
    data["pol-1-name"] = "Half done"
    data["op"] = "add:ent"
    data = posted(operator_client.post(EDIT, as_post(data)))
    data["ent-2-name"] = "No id"
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 200
    assert engine.requests == []
    policy = response.context["editor"].formsets["pol"].forms[1]
    assert "Enter the refusal message." in policy.errors["message"]
    entity = response.context["editor"].formsets["ent"].forms[2]
    assert entity.errors["id"]


def test_a_crafted_post_over_a_cap_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["loc-TOTAL_FORMS"] = "101"
    for index in range(101):
        data[f"loc-{index}-id"] = f"l{index}"
        data[f"loc-{index}-name"] = f"Place {index}"
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 200
    assert engine.requests == []
    assert "Please submit at most 100 forms." in response.content.decode()


def test_driver_rows_keep_multi_line_items_and_drop_blank_ones(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data["op"] = "add:mile"
    data = posted(operator_client.post(EDIT, as_post(data)))
    data["op"] = "add:mile"
    data = posted(operator_client.post(EDIT, as_post(data)))
    data["mile-0-text"] = "First:\r\nthe harvest."
    save(operator_client, engine, data)
    assert changes(engine) == {"drivers": {"milestones": ["First:\nthe harvest."]}}


# --- Engine refusals ------------------------------------------------------------------------------


def test_a_validation_error_lands_on_its_row(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["loc-1-id"] = "Barn!"
    response = save(operator_client, engine, data, status=422, json=fixture("422_validation"))
    assert response.status_code == 200
    row = response.context["editor"].formsets["loc"].forms[1]
    assert row.errors["id"] == ["String should match pattern '^[a-z0-9_]{1,40}$'"]
    html = response.content.decode()
    assert 'value="Barn!"' in html  # the input is kept


def test_a_validation_error_on_a_line_list(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["recommendation__capabilities"] = "memory\nBad Cap"
    body = error(
        "validation_error",
        "invalid",
        errors=[{"field": "body.recommendation.capabilities[1]", "message": "bad pattern"}],
    )
    response = save(operator_client, engine, data, status=422, json=body)
    errors = response.context["form"].errors["recommendation__capabilities"]
    assert errors == ["Line 2: bad pattern"]


def test_an_error_inside_a_box_lands_on_the_box(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data["drivers__planned_reveals"] = '[{"truth": {}}]'
    body = error(
        "validation_error",
        "invalid",
        errors=[{"field": "drivers.planned_reveals[0].truth.text", "message": "Field required"}],
    )
    response = save(operator_client, engine, data, status=422, json=body)
    errors = response.context["form"].errors["drivers__planned_reveals"]
    assert errors == ["drivers.planned_reveals[0].truth.text: Field required"]


def test_a_stale_draft_keeps_the_input(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["public_card__name"] = "Mine"
    response = save(operator_client, engine, data, status=409, json=fixture("409_stale"))
    html = response.content.decode()
    assert response.status_code == 200
    assert "This draft was saved elsewhere (now revision 7)" in html
    assert 'value="Mine"' in html


def test_a_draft_too_large(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["public_card__name"] = "Big"
    response = save(operator_client, engine, data, status=413, json=fixture("413"))
    assert "Draft exceeds the Engine&#x27;s 1 MiB limit" in response.content.decode()


def test_an_archived_scenario_renders_the_engine_refusal(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine, draft(status="archived"))
    html = response.content.decode()
    assert "Archived — unarchive to edit" in html
    assert 'name="op" value="save" disabled' in html
    # A forced POST still goes to the Engine, whose refusal is shown.
    data = posted(response)
    data["public_card__name"] = "Forced"
    refusal = error("entry_archived", "The scenario is archived")
    response = save(operator_client, engine, data, status=422, json=refusal)
    assert response.status_code == 200
    assert "entry_archived" in response.content.decode()
    assert len(calls(engine, "PATCH", PATH)) == 1


def test_a_tampered_snapshot_reads_again(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["snapshot"] = "tampered"
    data["public_card__name"] = "Changed"
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 302
    assert engine.requests == []


def test_saves_are_recorded(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["public_card__name"] = "Recorded"
    save(operator_client, engine, data)
    row = EngineOperation.objects.get(kind="patch_scenario")
    assert (row.entity_type, row.entity_id) == ("scenario", SCN)


# --- lookups --------------------------------------------------------------------------------------


def test_failed_lookups_degrade_their_fields(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", S_ITEM, 200, json=draft())
    engine.respond("GET", RATINGS_PATH, 503, json=error("unavailable"))
    engine.respond("GET", PROFILES, 503, json=error("unavailable"))
    response = operator_client.get(EDIT)
    html = response.content.decode()
    assert "Ratings unavailable: 503 unavailable" in html
    assert 'name="metadata__content_rating"' not in html
    assert "Engine profiles unavailable: 503 unavailable — enter the id" in html
    assert 'name="recommendation__engine_profile_id" value="lite-default"' in html
    engine.reset()
    data = posted(response)
    data["recommendation__engine_profile_id"] = "custom-1"
    save(operator_client, engine, data)
    # The rating is kept as it was; the typed profile id is sent.
    assert changes(engine) == {"recommendation": {"engine_profile_id": "custom-1"}}


# --- create ---------------------------------------------------------------------------------------


def new_page(client: Client, engine: FakeEngine) -> Any:
    answer_ratings(engine)
    answer_profiles(engine)
    response = client.get(NEW_URL)
    assert response.status_code == 200
    engine.reset()
    return response


def test_create_sends_the_filled_groups_and_lands_on_the_edit_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Demo 1: a name, a world description, two locations and the opening narration."""
    response = new_page(operator_client, engine)
    assert 'value="console:nate"' in response.content.decode()
    data = posted(response)
    data["op"] = "add:loc"
    data = posted(operator_client.post(NEW_URL, as_post(data)))
    data["op"] = "add:loc"
    data = posted(operator_client.post(NEW_URL, as_post(data)))
    assert engine.requests == []
    data.update(
        {
            "owner": " alice ",
            "public_card__name": "New one",
            "world__description": "A lighthouse.",
            "loc-0-id": "lamp",
            "loc-0-name": "The lamp room",
            "loc-1-id": "shore",
            "loc-1-name": "The shore",
            "starting_state__opening__narration": "Fog rolls in.",
        }
    )
    engine.respond("POST", S_LIST, 201, json=fixture("post"))
    response = operator_client.post(NEW_URL, as_post(data))
    assert response.status_code == 302
    assert response["Location"] == f"/catalog/scenarios/{NEW_SCN}/edit/"
    assert flashes(response) == ["Created at draft revision 1"]
    (sent,) = calls(engine, "POST", S_LIST)
    assert body_of(sent) == {
        "owner": "alice",
        "public_card": {"name": "New one"},
        "world": {
            "description": "A lighthouse.",
            "locations": [
                {"id": "lamp", "name": "The lamp room"},
                {"id": "shore", "name": "The shore"},
            ],
        },
        "starting_state": {"opening": {"narration": "Fog rolls in."}},
    }
    row = EngineOperation.objects.get(kind="create_scenario")
    assert (row.entity_type, row.entity_id) == ("scenario", NEW_SCN)


def test_an_empty_create_sends_only_the_owner(operator_client: Client, engine: FakeEngine) -> None:
    data = posted(new_page(operator_client, engine))
    engine.respond("POST", S_LIST, 201, json=fixture("post"))
    operator_client.post(NEW_URL, as_post(data))
    (sent,) = calls(engine, "POST", S_LIST)
    assert body_of(sent) == {"owner": "console:nate"}


def test_create_refusals_are_placed(operator_client: Client, engine: FakeEngine) -> None:
    data = posted(new_page(operator_client, engine))
    data["metadata__content_rating"] = TEEN
    engine.respond("POST", S_LIST, 422, json=error("rating_retired", "Teen is retired"))
    response = operator_client.post(NEW_URL, as_post(data))
    assert response.context["form"].errors["metadata__content_rating"] == ["Teen is retired"]


def test_an_invalid_create_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    data = posted(new_page(operator_client, engine))
    data["owner"] = "system"
    data["starting_state__clock"] = "[]"
    response = operator_client.post(NEW_URL, as_post(data))
    assert response.status_code == 200
    assert engine.requests == []
    assert response.context["form"].errors["owner"]


def test_place_error_without_a_path_is_a_form_error() -> None:
    from console.engine.choices import ProfileChoices, RatingChoices
    from console.forms.scenario import ScenarioEditor, editor_data

    editor = ScenarioEditor(
        editor_data({}),
        ratings=RatingChoices([]),
        profiles=ProfileChoices([]),
        roles=[],
        create=False,
    )
    assert editor.is_valid()
    placed = editor.place_error(EngineError("media_retired", "A media asset is retired", {}, 422))
    assert placed == 0
    assert editor.form.non_field_errors() == ["media_retired: A media asset is retired"]


# --- shared helpers (#68) -------------------------------------------------------------------------


def test_parse_op() -> None:
    from console.formset_rows import Op, parse_op

    assert parse_op(None) == Op("save")
    assert parse_op("add:loc") == Op("add", "loc")
    assert parse_op("remove:pol-12") == Op("remove", "pol", 12)
    assert parse_op("remove:pol-x") == Op("refresh")  # malformed: changes nothing
    assert parse_op("refresh").edits_rows
    assert not parse_op("save").edits_rows


def test_apply_op_edits_one_formset() -> None:
    from console.formset_rows import Op, apply_op

    data = QueryDict(mutable=True)
    data.update({"op": "x", "a-TOTAL_FORMS": "2", "a-0-v": "one", "a-1-v": "two", "b-0-v": "k"})
    added = apply_op(data, Op("add", "a"), max_rows={"a": 3}, new_rows={"a": {"v": ["new"]}})
    assert added["a-TOTAL_FORMS"] == "3"
    assert added["a-2-v"] == "new"
    assert added["b-0-v"] == "k"
    assert "op" not in added
    removed = apply_op(data, Op("remove", "a", 0), max_rows={"a": 3})
    assert (removed["a-TOTAL_FORMS"], removed["a-0-v"]) == ("1", "two")
    capped = apply_op(added, Op("add", "a"), max_rows={"a": 3})
    assert capped["a-TOTAL_FORMS"] == "3"
    unknown = apply_op(data, Op("add", "zz"), max_rows={"a": 3})
    assert unknown["a-TOTAL_FORMS"] == "2"


def test_engine_profile_choices_follows_cursors_and_keeps_the_current(engine: FakeEngine) -> None:
    from console.engine.choices import engine_profile_choices
    from console.engine.client import EngineClient
    from tests.catalog.scenario_helpers import profile

    pages = [
        {
            "items": [
                profile("a", "Alpha"),
                profile("old", "Old", deleted_at="2026-10-02T00:00:00Z"),
            ]
        },
        {"items": [profile("b", "Beta")]},
    ]
    engine.respond_sequence(
        "GET",
        PROFILES,
        [
            {"status": 200, "json": {**pages[0], "next_cursor": "c2"}},
            {"status": 200, "json": {**pages[1], "next_cursor": None}},
        ],
    )
    client = EngineClient("http://engine.test", "", transport=engine.transport)
    choices = engine_profile_choices(client, current="old")
    assert choices.ok
    assert choices.choices == [("a", "Alpha"), ("b", "Beta"), ("old", "Old (deleted)")]
    cursors = [dict(r.url.params).get("cursor") for r in calls(engine, "GET", PROFILES)]
    assert cursors == [None, "c2"]
    assert dict(calls(engine, "GET", PROFILES)[0].url.params)["include_deleted"] == "true"


def test_engine_profile_choices_too_many_and_failed(engine: FakeEngine) -> None:
    from console.engine.choices import PROFILES_MAX, TOO_MANY, engine_profile_choices
    from console.engine.client import EngineClient
    from tests.catalog.scenario_helpers import profile

    many = [profile(f"p{i}", f"P{i}") for i in range(PROFILES_MAX + 1)]
    engine.respond("GET", PROFILES, 200, json={"items": many, "next_cursor": None})
    client = EngineClient("http://engine.test", "", transport=engine.transport)
    choices = engine_profile_choices(client, current="zz")
    assert not choices.ok
    assert choices.unavailable_text == TOO_MANY
    engine.respond("GET", PROFILES, 503, json=error("unavailable"))
    failed = engine_profile_choices(client)
    assert failed.unavailable_text == "Engine profiles unavailable: 503 unavailable — enter the id"


def test_the_scenario_filter_form_adds_include_companions() -> None:
    from console.engine.choices import RatingChoices
    from console.forms.catalog import ScenarioFilterForm

    form = ScenarioFilterForm(
        {"include_companions": "on"}, ratings=RatingChoices([]), listing_values=None
    )
    assert form.is_valid()
    assert form.query()["include_companions"] is True
    plain = ScenarioFilterForm({}, ratings=RatingChoices([]), listing_values=None)
    assert plain.is_valid()
    assert plain.query()["include_companions"] is False

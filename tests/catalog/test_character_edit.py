"""Create and edit a catalog character in the structured form (#66)."""

import json
from typing import Any, get_args

import pytest
from django.test import Client

from console.engine.choices import RatingChoices
from console.engine.models import CatalogCharacter
from console.forms import catalog_character as cc
from console.forms.catalog_character import CharacterForm, draft_to_write, flatten
from console.models import ActingPrincipal, EngineOperation, KnownPrincipal
from tests.catalog.helpers import (
    CHAR,
    COMPANION,
    EDIT,
    ITEM,
    LIST,
    NEW_CHAR,
    NEW_URL,
    RATINGS,
    TEEN,
    answer_edit_reads,
    answer_ratings,
    body_of,
    calls,
    draft,
    error,
    form_data,
    patched,
    text,
    writes,
)
from tests.fakes.engine import FakeEngine, recorded_schema
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

EVERYONE = "00000000000000000000RAT001"
WARMTH_NUANCE = "definition__dispositions__warmth__nuance"


def edit_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    answer_edit_reads(engine, body)
    response = client.get(EDIT)
    assert response.status_code == 200
    engine.reset()
    return response


def patch_sent(engine: FakeEngine) -> dict[str, Any]:
    (sent,) = calls(engine, "PATCH", ITEM.format(character_id=CHAR))
    body: dict[str, Any] = body_of(sent)
    return body


# --- create ---------------------------------------------------------------------------------------


def test_new_page_prefills_the_owner_and_shows_every_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    response = operator_client.get(NEW_URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'value="console:nate"' in html  # the acting principal
    for legend in ("Card", "Definition", "Interaction style", "Metadata", "Visual identity"):
        assert f"<legend>{legend}</legend>" in html
    assert 'id="f-definition-true_age"' in html
    assert 'id="f-metadata-content_rating_id"' in html
    assert 'id="f-definition-dispositions-warmth-setting"' in html
    assert "required to publish" in html
    assert "Save &amp; publish" not in html  # create offers Create only
    assert "clear_visual_identity" not in html


def filled() -> dict[str, str]:
    return {
        "owner": " alice ",
        "public_card__name": "Mira",
        "public_card__public_description": "A wandering healer.\r\nKind.",
        "definition__true_age": "29",
        "definition__appearance": "Short, freckled.",
        "definition__personality": "Warm.",
        "definition__values_motivations": "Heal everyone.",
        "definition__background_flavor": "Raised by monks.",
        "definition__likes_dislikes__likes": "tea\r\n\r\n herbs \r\ntea",
        "definition__likes_dislikes__dislikes": "cruelty",
        "definition__dispositions__warmth__setting": "very_warm",
        "definition__dispositions__warmth__nuance": "even to strangers",
        "definition__dispositions__caution__setting": "careful",
        "definition__interaction_style__speech_style": "Gentle.",
        "definition__interaction_style__vocabulary": "Plain.",
        "definition__interaction_style__mannerisms": "hums",
        "definition__interaction_style__sample_dialogue": "Sit, rest.",
        "definition__deflection_style": "Offers tea.",
        "definition__safe_fallback_text": "Let's talk of other things.",
        "metadata__aliases": "The Healer",
        "metadata__labels": "Fantasy, healer, fantasy",
        "metadata__content_rating": TEEN,
        "visual_identity__preferred_image_model": "img-1",
        "visual_identity__base_appearance_prompt": "a healer",
        "visual_identity__negative_prompt": "cartoon",
        "visual_identity__seed": "7",
        "visual_identity__adapter_params": '{"lora": "healer", "weight": 0.5}',
    }


def test_create_sends_every_filled_section(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    engine.respond("POST", LIST, 201, json=engine_fixture("catalog_characters_post"))
    response = operator_client.post(NEW_URL, filled())
    assert response.status_code == 302
    assert response["Location"] == f"/catalog/characters/{NEW_CHAR}/edit/"
    (sent,) = calls(engine, "POST", LIST)
    assert body_of(sent) == {
        "owner": "alice",
        "public_card": {"name": "Mira", "public_description": "A wandering healer.\nKind."},
        "definition": {
            "true_age": 29,
            "appearance": "Short, freckled.",
            "personality": "Warm.",
            "values_motivations": "Heal everyone.",
            "background_flavor": "Raised by monks.",
            "likes_dislikes": {"likes": ["tea", "herbs"], "dislikes": ["cruelty"]},
            "dispositions": {
                "warmth": {"setting": "very_warm", "nuance": "even to strangers"},
                "caution": {"setting": "careful"},
            },
            "interaction_style": {
                "speech_style": "Gentle.",
                "vocabulary": "Plain.",
                "mannerisms": ["hums"],
                "sample_dialogue": ["Sit, rest."],
            },
            "deflection_style": "Offers tea.",
            "safe_fallback_text": "Let's talk of other things.",
        },
        "metadata": {
            "aliases": ["The Healer"],
            "labels": ["fantasy", "healer"],
            "content_rating_id": TEEN,
        },
        "visual_identity": {
            "preferred_image_model": "img-1",
            "base_appearance_prompt": "a healer",
            "negative_prompt": "cartoon",
            "seed": 7,
            "adapter_params": {"lora": "healer", "weight": 0.5},
        },
    }
    assert sent.headers["X-Epistrel-Principal"] == "console:nate"
    assert sent.headers["Idempotency-Key"]
    assert KnownPrincipal.objects.filter(principal="alice").exists()
    page = operator_client.get(NEW_URL)  # the flash
    assert "Created Mira — draft revision 1." in text(page.content.decode())


def test_create_with_a_name_only_omits_blank_optionals(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    engine.respond("POST", LIST, 201, json=engine_fixture("catalog_characters_post"))
    response = operator_client.post(NEW_URL, {"owner": "console:nate", "public_card__name": "Mira"})
    assert response.status_code == 302
    (sent,) = calls(engine, "POST", LIST)
    assert body_of(sent) == {
        "owner": "console:nate",
        "public_card": {"name": "Mira"},
        "definition": {
            "likes_dislikes": {"likes": [], "dislikes": []},
            "interaction_style": {"mannerisms": [], "sample_dialogue": []},
        },
    }


def test_create_allows_an_entirely_empty_draft(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    engine.respond("POST", LIST, 201, json=engine_fixture("catalog_characters_post"))
    assert operator_client.post(NEW_URL, {"owner": "console:nate"}).status_code == 302
    (sent,) = calls(engine, "POST", LIST)
    assert set(body_of(sent)) == {"owner", "definition"}


def test_create_needs_an_owner(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    response = operator_client.get(NEW_URL)
    data = {**form_data(response), "owner": "  ", "public_card__name": "Mira"}
    engine.reset()
    response = operator_client.post(NEW_URL, data)
    assert response.status_code == 200
    assert "Principal can&#x27;t be blank." in response.content.decode()
    assert engine.requests == []  # zero calls: the rating options travel in the snapshot


def test_create_422_lands_on_the_field(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    refusal = error(
        "validation_error",
        "invalid",
        field="body.metadata.aliases[0]",
        errors=[{"field": "body.metadata.aliases[0]", "message": "an alias can't be the name"}],
    )
    engine.respond("POST", LIST, 422, json=refusal)
    data = {"owner": "console:nate", "public_card__name": "Mira", "metadata__aliases": "mira"}
    response = operator_client.post(NEW_URL, data)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Line 1: an alias can&#x27;t be the name" in html
    assert "mira" in html  # input kept


# --- edit: reading the draft ----------------------------------------------------------------------


def test_the_draft_round_trips_through_the_form_with_no_diff() -> None:
    entry = CatalogCharacter.from_json(draft())
    assert entry is not None
    original = draft_to_write(entry)
    ratings = RatingChoices([(TEEN, "Teen")])
    shown = CharacterForm(ratings=ratings, create=False, initial=flatten(original))
    data = {
        name: shown[name].value()
        for name in shown.fields
        if shown[name].value() not in (None, False)
    }
    form = CharacterForm(data, ratings=ratings, create=False, current_rating=TEEN)
    assert form.is_valid(), form.errors
    assert form.edit_patch(original) == {}
    assert original["metadata"]["content_rating_id"] == TEEN
    assert original["public_card"]["name"] == "Sir Aldric"


def test_the_edit_page_reads_the_draft_and_the_companion(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_edit_reads(engine)
    response = operator_client.get(EDIT)
    html = response.content.decode()
    assert response.status_code == 200
    (read,) = calls(engine, "GET", ITEM.format(character_id=CHAR))
    assert dict(read.url.params) == {"projection": "full", "draft": "true"}
    (comp,) = calls(engine, "GET", COMPANION.format(character_id=CHAR))
    assert dict(comp.url.params) == {"draft": "true", "projection": "full"}
    page = text(html)
    assert "Draft revision 5" in page
    assert "latest v2" in page
    assert 'name="draft_revision" value="5"' in html
    assert 'name="companion_draft_revision" value="3"' in html
    assert 'name="with_companion"' in html
    assert "Save &amp; publish" in html


def test_the_rating_field_maps_from_metadata_content_rating_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    assert response.context["form"]["metadata__content_rating"].value() == TEEN
    html = response.content.decode()
    assert f'<option value="{TEEN}" selected>Teen</option>' in html
    assert '<option value="">— none —</option>' in html


# --- edit: what is sent ---------------------------------------------------------------------------


def test_no_change_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    saved = operator_client.post(EDIT, {**form_data(response), "action": "save"})
    assert saved.status_code == 302
    assert saved["Location"] == EDIT
    assert engine.requests == []
    answer_edit_reads(engine)
    assert "No changes" in text(operator_client.get(EDIT).content.decode())


def test_edit_sends_only_the_changed_field_with_the_draft_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = {**form_data(response), WARMTH_NUANCE: "to friends", "action": "save"}
    saved = operator_client.post(EDIT, data)
    assert saved.status_code == 302
    assert saved["Location"] == EDIT
    body = patch_sent(engine)
    assert body == {
        "definition": {"dispositions": {"warmth": {"nuance": "to friends"}}},
        "draft_revision": 5,
    }
    # complement: the unchanged fields are absent from the patch body
    assert "public_card" not in body
    assert "metadata" not in body
    assert "visual_identity" not in body
    assert set(body["definition"]) == {"dispositions"}
    assert [r.method for r in engine.requests] == ["PATCH"]
    answer_edit_reads(engine, draft(draft_revision=6))
    assert "Saved — draft revision 6." in text(operator_client.get(EDIT).content.decode())


def test_cleared_fields_send_null_and_emptied_lists_send_empty(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = {
        **form_data(response),
        "definition__background_flavor": "",
        "metadata__aliases": "",
        "definition__likes_dislikes__likes": "",
        "definition__dispositions__dominance__setting": "",
        "definition__dispositions__dominance__nuance": "",
        "metadata__content_rating": "",
        "action": "save",
    }
    operator_client.post(EDIT, data)
    assert patch_sent(engine) == {
        "definition": {
            "background_flavor": None,
            "likes_dislikes": {"likes": []},
            "dispositions": {"dominance": None},
        },
        "metadata": {"aliases": [], "content_rating_id": None},
        "draft_revision": 5,
    }


def test_a_changed_list_is_sent_whole(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = {**form_data(response), "metadata__labels": "fantasy, knight, Hero", "action": "save"}
    operator_client.post(EDIT, data)
    assert patch_sent(engine)["metadata"] == {"labels": ["fantasy", "knight", "hero"]}


def test_normalisation_is_not_a_change(operator_client: Client, engine: FakeEngine) -> None:
    body = draft(public_description="  A knight errant.\r\nSworn to no one.  ", labels=["Fantasy"])
    response = edit_page(operator_client, engine, body)
    data = {
        **form_data(response),
        "public_card__public_description": "A knight errant.\r\nSworn to no one.",
        "metadata__labels": " FANTASY , fantasy",
        "action": "save",
    }
    saved = operator_client.post(EDIT, data)
    assert saved.status_code == 302
    assert engine.requests == []


def test_a_nuance_without_a_setting_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    data = {
        **form_data(response),
        "definition__dispositions__caution__nuance": "but brave",
        "action": "save",
    }
    refused = operator_client.post(EDIT, data)
    assert refused.status_code == 200
    assert "Pick a setting for this nuance." in refused.content.decode()
    assert engine.requests == []


def test_clear_visual_identity_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = {
        **form_data(response),
        "clear_visual_identity": "on",
        "visual_identity__seed": "99",  # ignored: the clear wins
        "action": "save",
    }
    operator_client.post(EDIT, data)
    assert patch_sent(engine) == {"visual_identity": None, "draft_revision": 5}


def test_a_cleared_visual_identity_field_sends_null(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = {**form_data(response), "visual_identity__seed": "", "action": "save"}
    operator_client.post(EDIT, data)
    assert patch_sent(engine) == {"visual_identity": {"seed": None}, "draft_revision": 5}


# --- adapter_params -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("{not json", "Enter valid JSON"),
        ("[1, 2]", cc.ADAPTER_PARAMS_OBJECT),
        ('"text"', cc.ADAPTER_PARAMS_OBJECT),
        ('{"x": NaN}', cc.ADAPTER_PARAMS_OBJECT),
        ('{"x": Infinity}', cc.ADAPTER_PARAMS_OBJECT),
        ('{"x": 1e999}', cc.ADAPTER_PARAMS_OBJECT),
        ('{"x": {"y": null}}', cc.ADAPTER_PARAMS_NULL),
        (json.dumps({"x": "a" * 8200}), cc.ADAPTER_PARAMS_SIZE),
    ],
)
def test_invalid_adapter_params_make_zero_calls(
    operator_client: Client, engine: FakeEngine, value: str, message: str
) -> None:
    response = edit_page(operator_client, engine)
    data = {**form_data(response), "visual_identity__adapter_params": value, "action": "save"}
    refused = operator_client.post(EDIT, data)
    assert refused.status_code == 200
    assert message.replace("'", "&#x27;") in refused.content.decode()
    assert engine.requests == []


def test_adapter_params_size_is_the_compact_utf8_json() -> None:
    value = {"k": "é" * 10}
    assert cc.compact_size(value) == len(json.dumps(value, separators=(",", ":")).encode())


def test_a_removed_adapter_params_key_is_sent_as_null(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    shown = response.context["form"]["visual_identity__adapter_params"].value()
    assert shown == json.dumps({"lora": "knight-v2", "weight": 0.7}, indent=2, sort_keys=True)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = {
        **form_data(response),
        "visual_identity__adapter_params": '{"lora": "knight-v3"}',
        "action": "save",
    }
    operator_client.post(EDIT, data)
    assert patch_sent(engine) == {
        "visual_identity": {"adapter_params": {"lora": "knight-v3", "weight": None}},
        "draft_revision": 5,
    }


def test_emptied_adapter_params_send_null(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = {**form_data(response), "visual_identity__adapter_params": "", "action": "save"}
    operator_client.post(EDIT, data)
    assert patch_sent(engine) == {
        "visual_identity": {"adapter_params": None},
        "draft_revision": 5,
    }


# --- refusals -------------------------------------------------------------------------------------


def test_draft_stale_keeps_the_input_and_shows_the_banner(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    stale = error(
        "draft_stale", "stale", character_id=CHAR, draft_revision=5, current_draft_revision=8
    )
    engine.respond("PATCH", ITEM, 409, json=stale)
    data = {**form_data(response), WARMTH_NUANCE: "only to friends", "action": "save"}
    refused = operator_client.post(EDIT, data)
    assert refused.status_code == 200
    html = refused.content.decode()
    assert "This draft was saved elsewhere (now revision 8)" in html
    assert 'value="only to friends"' in html
    assert len(writes(engine)) == 1


def test_a_422_on_save_places_each_error_at_its_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    refusal = error(
        "validation_error",
        "invalid",
        field="body.metadata.aliases[1]",
        errors=[
            {"field": "body.metadata.aliases[1]", "message": "duplicate alias"},
            {"field": "body.definition.dispositions.warmth.nuance", "message": "too long"},
            {"field": "body.public_card.images.primary", "message": "not an image"},
        ],
    )
    engine.respond("PATCH", ITEM, 422, json=refusal)
    data = {**form_data(response), WARMTH_NUANCE: "x", "action": "save"}
    refused = operator_client.post(EDIT, data)
    form = refused.context["form"]
    assert form.errors["metadata__aliases"] == ["Line 2: duplicate alias"]
    assert form.errors[WARMTH_NUANCE] == ["too long"]
    assert form.non_field_errors() == ["public_card.images.primary: not an image"]


def test_rating_retired_lands_on_the_rating(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    refusal = error("rating_retired", "retired", field="body.metadata.content_rating_id")
    engine.respond("PATCH", ITEM, 422, json=refusal)
    data = {**form_data(response), "metadata__content_rating": EVERYONE, "action": "save"}
    refused = operator_client.post(EDIT, data)
    assert refused.context["form"].errors["metadata__content_rating"] == ["retired"]


def test_draft_too_large_is_a_form_error(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 413, json=error("draft_too_large", "big", size=1, limit=2))
    data = {**form_data(response), WARMTH_NUANCE: "x", "action": "save"}
    refused = operator_client.post(EDIT, data)
    assert refused.context["form"].non_field_errors() == ["The draft is over 256 KiB."]


def test_another_error_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 422, json=error("entry_archived", "archived", character_id=CHAR))
    data = {**form_data(response), WARMTH_NUANCE: "x", "action": "save"}
    refused = operator_client.post(EDIT, data)
    html = refused.content.decode()
    assert "Engine error 422 entry_archived" in html
    assert 'value="x"' in html


# --- ratings --------------------------------------------------------------------------------------


def test_a_retired_current_rating_stays_selected(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_edit_reads(engine)
    answer_ratings(engine, Teen={"retired_at": "2026-10-23T00:00:00Z"})
    response = operator_client.get(EDIT)
    html = response.content.decode()
    assert f'<option value="{TEEN}" selected>Teen (retired)</option>' in html
    engine.reset()
    saved = operator_client.post(EDIT, {**form_data(response), "action": "save"})
    assert saved.status_code == 302
    assert engine.requests == []  # unchanged unless another is picked


def test_when_ratings_are_unavailable_the_rating_is_kept(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_edit_reads(engine)
    engine.respond("GET", RATINGS, 503, json=error("database_unavailable", "down"))
    response = operator_client.get(EDIT)
    html = response.content.decode()
    assert "Ratings unavailable: 503 database_unavailable" in html
    assert 'name="metadata__content_rating"' not in html
    engine.reset()
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = {**form_data(response), WARMTH_NUANCE: "x", "action": "save"}
    assert operator_client.post(EDIT, data).status_code == 302
    assert "metadata" not in patch_sent(engine)


# --- archived, recording, schema ------------------------------------------------------------------


def test_an_archived_entry_is_read_only(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine, draft(status="archived"))
    html = response.content.decode()
    assert "Archived — unarchive to save or publish." in html
    assert 'value="save" disabled' in html
    assert 'value="publish" disabled' in html
    data = {**form_data(response), WARMTH_NUANCE: "x", "action": "save"}
    refused = operator_client.post(EDIT, data)
    assert refused.status_code == 302
    assert engine.requests == []


def test_every_write_is_recorded_as_the_acting_principal(
    operator_client: Client, engine: FakeEngine, operator: Any
) -> None:
    ActingPrincipal.objects.create(user=operator, principal="alice")
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    operator_client.post(EDIT, {**form_data(response), WARMTH_NUANCE: "x", "action": "save"})
    (sent,) = writes(engine)
    assert sent.headers["X-Epistrel-Principal"] == "alice"
    row = EngineOperation.objects.get(kind="patch_catalog_character")
    assert (row.principal, row.operator, row.entity_type, row.entity_id) == (
        "alice",
        operator,
        "character",
        CHAR,
    )


def test_create_is_recorded_with_the_new_id(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    engine.respond("POST", LIST, 201, json=engine_fixture("catalog_characters_post"))
    operator_client.post(NEW_URL, {"owner": "console:nate"})
    row = EngineOperation.objects.get(kind="create_catalog_character")
    assert (row.entity_type, row.entity_id) == ("character", NEW_CHAR)


def test_disposition_settings_equal_the_schema_enums() -> None:
    schemas = recorded_schema()["components"]["schemas"]
    for axis, name in (
        ("threat_response", "ThreatResponse"),
        ("dominance", "Dominance"),
        ("honesty", "Honesty"),
        ("warmth", "Warmth"),
        ("caution", "Caution"),
    ):
        assert list(cc.DISPOSITION_SETTINGS[axis]) == schemas[name]["enum"]
    assert get_args(cc.Warmth) == cc.DISPOSITION_SETTINGS["warmth"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("body.name", ("public_card__name", None)),
        ("public_card.name", ("public_card__name", None)),
        ("public_description", ("public_card__public_description", None)),
        ("content_rating", ("metadata__content_rating", None)),
        ("body.content_rating_id", ("metadata__content_rating", None)),
        ("metadata.content_rating_id", ("metadata__content_rating", None)),
        ("aliases[2]", ("metadata__aliases", 2)),
        ("body.metadata.labels[0]", ("metadata__labels", 0)),
        ("definition.dispositions.warmth", ("definition__dispositions__warmth__setting", None)),
        (
            "definition.interaction_style.mannerisms[3]",
            (
                "definition__interaction_style__mannerisms",
                3,
            ),
        ),
        ("visual_identity.seed", ("visual_identity__seed", None)),
        ("visual_identity.reference_images[0].audience", None),
        ("public_card.images.primary", None),
    ],
)
def test_engine_paths_map_to_fields(path: str, expected: tuple[str, int | None] | None) -> None:
    form = CharacterForm(ratings=RatingChoices([(TEEN, "Teen")]), create=False)
    assert cc.field_for_path(path, form.fields) == expected


def test_the_list_offers_new_character(operator_client: Client, engine: FakeEngine) -> None:
    from tests.catalog.helpers import answer_lookups, listing

    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing())
    html = operator_client.get("/catalog/characters/").content.decode()
    assert 'href="/catalog/characters/new/"' in html

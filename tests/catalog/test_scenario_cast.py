"""Edit a scenario's cast roles and seeded memories (#73): the editor's Cast and World seeds,
the whole-cast PATCH, seed ids kept across saves, Engine errors placed at their rows, and the
detail page's cast tables."""

import copy
import json
import re
from typing import Any

import httpx
import pytest
from django.http import QueryDict
from django.test import Client

from console import merge_patch
from console.engine.choices import CharacterChoices, ProfileChoices, RatingChoices
from console.engine.models import Scenario
from console.forms.scenario import ScenarioEditor, draft_to_write, editor_data
from console.forms.scenario_cast import CastChoices, normalize_cast
from tests.catalog.helpers import CHAR, TEEN, body_of, calls, error, text
from tests.catalog.scenario_helpers import (
    DETAIL,
    EDIT,
    NEW_SCN,
    NEW_URL,
    PATH,
    RATINGS_PATH,
    S_ITEM,
    S_LIST,
    answer_edit_reads,
    answer_profiles,
    answer_ratings,
    as_post,
    fixture,
    posted,
    writes,
)
from tests.fakes.engine import FakeEngine

pytestmark = pytest.mark.django_db

CHAR_LIST = "/catalog/characters"
SEED1, SEED2, SEED3, SEED4 = (f"01JC0000000000000000SEED0{n}" for n in range(1, 5))
MINTED = "01JC00000000000000000MNT{:02d}"


def cast_draft(**changes: Any) -> dict[str, Any]:
    return fixture("draft_cast", **changes)


def edit_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    answer_edit_reads(engine, body if body is not None else cast_draft())
    response = client.get(EDIT)
    assert response.status_code == 200
    engine.reset()
    return response


def form(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> QueryDict:
    return posted(edit_page(client, engine, body))


def save(client: Client, engine: FakeEngine, data: QueryDict, **answer: Any) -> Any:
    engine.respond(
        "PATCH", S_ITEM, answer.get("status", 200), json=answer.get("json", fixture("patch"))
    )
    data["op"] = "save"
    return client.post(EDIT, as_post(data))


def changes(engine: FakeEngine) -> dict[str, Any]:
    (sent,) = calls(engine, "PATCH", PATH)
    body: dict[str, Any] = body_of(sent)
    body.pop("draft_revision")
    return body


def op(client: Client, data: QueryDict, value: str) -> QueryDict:
    """Press a row button (or "Update form"): the re-rendered state, no Engine call."""
    data["op"] = value
    response = client.post(EDIT, as_post(data))
    assert response.status_code == 200
    return posted(response)


def the_cast(body: dict[str, Any]) -> dict[str, Any]:
    """``body``'s cast as the editor sends it (every key present)."""
    roles = []
    for role in body["cast"]["roles"]:
        roles.append(
            {
                "id": role["id"],
                "kind": role["kind"],
                "public_label": role["public_label"],
                "setup_description": role["setup_description"],
                "required": role.get("required", True),
                "fill": role.get("fill", "open"),
                "character": role.get("character"),
                "eligibility": role.get("eligibility"),
                "starting_location": role.get("starting_location"),
                "participation": role.get("participation"),
                "seeded_memories": role.get("seeded_memories", []),
                "relationships": role.get("relationships", []),
                "name_overrides": role.get("name_overrides"),
                "state_initial_values": role.get("state_initial_values"),
            }
        )
    return {
        "max_characters_at_start": body["cast"]["max_characters_at_start"],
        "roles": roles,
        "groups": body["cast"].get("groups") or None,  # #206: always sent, null for none
    }


# --- the whole cast in the patch ------------------------------------------------------------------


def test_an_untouched_cast_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 302
    assert writes(engine) == []


def test_the_patch_carries_the_whole_cast(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["role-1-public_label"] = "The son"
    save(operator_client, engine, data)
    sent = changes(engine)
    assert set(sent) == {"cast"}
    expected = the_cast(cast_draft())
    expected["roles"][1]["public_label"] = "The son"
    assert sent["cast"] == expected
    # The whole object: the untouched role and the limit are sent too, seeds with their ids.
    assert sent["cast"]["roles"][0]["seeded_memories"][1]["id"] == SEED2
    assert sent["cast"]["roles"][0]["character"] == {"catalog_id": CHAR, "version": 2}


def test_the_cast_limit_cleared_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["cast__max_characters_at_start"] = ""
    save(operator_client, engine, data)
    assert changes(engine)["cast"]["max_characters_at_start"] is None


def test_world_seed_rows_land_in_starting_state(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = op(operator_client, form(operator_client, engine), "add:wseed")
    # A new world seed is held by and known to the world.
    assert data["wseed-1-holder"] == "world"
    assert data.getlist("wseed-1-audience") == ["world"]
    data["wseed-1-text"] = "The river floods every spring."
    data["wseed-1-disclosure"] = "open"
    save(operator_client, engine, data)
    sent = changes(engine)
    assert "cast" not in sent
    assert sent == {
        "starting_state": {
            "seeded_memories": [
                {
                    "id": SEED4,
                    "text": "The harvest is late this year.",
                    "holder": "world",
                    "audience": ["world"],
                    "disclosure": "open",
                    "kind": "canon",
                    "valid_time": None,
                },
                {
                    "text": "The river floods every spring.",
                    "holder": "world",
                    "audience": ["world"],
                    "disclosure": "open",
                    "kind": None,
                    "valid_time": None,
                },
            ]
        }
    }


def test_fill_open_sends_character_null(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["role-0-fill"] = "open"  # the select still posts its saved value: open wins
    save(operator_client, engine, data)
    role = changes(engine)["cast"]["roles"][0]
    assert role["fill"] == "open"
    assert role["character"] is None


def test_a_blank_version_is_left_out(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["role-0-character__version"] = ""
    save(operator_client, engine, data)
    assert changes(engine)["cast"]["roles"][0]["character"] == {"catalog_id": CHAR}


def test_eligibility_and_optionals(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["role-1-eligibility__labels"] = "Farmer, young"
    data.setlist("role-1-eligibility__content_ratings", [TEEN])
    data["role-1-eligibility__age_range_max"] = "30"
    data["role-1-participation"] = "reactive"
    data["role-1-starting_location"] = "barn"
    data["role-1-name_overrides"] = '{"canonical": "Tom"}'
    data["role-1-required"] = ""
    data.pop("role-1-required")
    # Everything of role 0's eligibility cleared → null.
    data["role-0-eligibility__labels"] = ""
    data.setlist("role-0-eligibility__content_ratings", [])
    data["role-0-eligibility__age_range_min"] = ""
    data["role-0-relationships"] = ""
    save(operator_client, engine, data)
    roles = changes(engine)["cast"]["roles"]
    assert roles[1]["eligibility"] == {
        "labels": ["farmer", "young"],
        "content_ratings": [TEEN],
        "age_range": [None, 30],
    }
    assert roles[1]["participation"] == "reactive"
    assert roles[1]["starting_location"] == "barn"
    assert roles[1]["name_overrides"] == {"canonical": "Tom"}
    assert roles[1]["required"] is False
    assert roles[0]["eligibility"] is None
    assert roles[0]["relationships"] == []


# --- seed ids -------------------------------------------------------------------------------------


class Stored:
    """A stateful fake draft: the PATCH merges the patch, mints an id for each seed without one
    and echoes the draft; the GET answers the stored draft."""

    def __init__(self, engine: FakeEngine, body: dict[str, Any]) -> None:
        self.body = copy.deepcopy(body)
        self.minted = 0
        engine.side_effect("GET", S_ITEM, self.read)
        engine.side_effect("PATCH", S_ITEM, self.patch)

    def read(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.body)

    def _mint(self, seeds: list[dict[str, Any]]) -> None:
        for seed in seeds:
            if not seed.get("id"):
                self.minted += 1
                seed["id"] = MINTED.format(self.minted)

    def patch(self, request: httpx.Request) -> httpx.Response:
        patch = json.loads(request.content)
        patch.pop("draft_revision")
        self.body = merge_patch.apply(self.body, patch)
        for role in self.body["cast"]["roles"]:
            self._mint(role.get("seeded_memories") or [])
        self._mint(self.body["starting_state"].get("seeded_memories") or [])
        self.body["draft_revision"] += 1
        return httpx.Response(200, json={**self.body, "config_revision": 4})


def seeds_of(body: dict[str, Any]) -> list[tuple[str, str]]:
    found = [(s["id"], s["text"]) for r in body["cast"]["roles"] for s in r["seeded_memories"]]
    return [*found, *((s["id"], s["text"]) for s in body["starting_state"]["seeded_memories"])]


def reopen(client: Client) -> QueryDict:
    response = client.get(EDIT)
    assert response.status_code == 200
    return posted(response)


def test_an_edited_seed_keeps_its_id(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    answer_profiles(engine)
    store = Stored(engine, cast_draft())
    data = reopen(operator_client)
    # Add a secret seed held by and known to the mother; save; it gets an id.
    data = op(operator_client, data, "add:role-0-seed")
    assert data["role-0-seed-2-holder"] == "mother"
    assert data.getlist("role-0-seed-2-audience") == ["mother"]
    data["role-0-seed-2-text"] = "She means to sell."
    data["role-0-seed-2-disclosure"] = "secret"
    data["op"] = "save"
    assert operator_client.post(EDIT, as_post(data)).status_code == 302
    new_id = MINTED.format(1)
    assert (new_id, "She means to sell.") in seeds_of(store.body)
    # Edit it, and an old one, and save again: the same ids, no extra seed.
    data = reopen(operator_client)
    assert data["role-0-seed-2-id"] == new_id
    data["role-0-seed-2-text"] = "She means to sell the farm."
    data["role-0-seed-0-text"] = "She hid the deed under the hay."
    data["op"] = "save"
    assert operator_client.post(EDIT, as_post(data)).status_code == 302
    assert seeds_of(store.body) == [
        (SEED1, "She hid the deed under the hay."),
        (SEED2, "Her son left after a quarrel."),
        (new_id, "She means to sell the farm."),
        (SEED3, "The tractor never starts."),
        (SEED4, "The harvest is late this year."),
    ]
    assert store.minted == 1
    # The detail page shows the same id.
    engine.respond("GET", "/catalog/characters/{character_id}", 200, json=character_card())
    html = operator_client.get(f"{DETAIL}?view=draft").content.decode()
    assert f"<code>{new_id}</code>" in html


def test_a_posted_seed_id_not_in_the_original_is_stripped(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = op(operator_client, form(operator_client, engine), "add:role-1-seed")
    data["role-1-seed-1-text"] = "A forged one."
    data["role-1-seed-1-id"] = "01JC0000000000000000FAKE01"
    # A copy of an original id on a second row is stripped too (kept on its first occurrence).
    data["role-1-seed-0-id"] = SEED1
    save(operator_client, engine, data)
    seeds = changes(engine)["cast"]["roles"]
    mother, son = seeds[0]["seeded_memories"], seeds[1]["seeded_memories"]
    assert [s.get("id") for s in mother] == [SEED1, SEED2]
    assert "id" not in son[0]  # SEED1 again: stripped
    assert "id" not in son[1]  # never the Engine's
    assert son[1]["text"] == "A forged one."


# --- the form -------------------------------------------------------------------------------------


def test_the_character_select_shows_only_for_default_and_locked(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    html = response.content.decode()
    assert 'name="role-0-character__catalog_id"' in html  # locked
    assert f'<option value="{CHAR}" selected>Sir Aldric</option>' in html
    assert 'name="role-0-character__version" value="2"' in html
    assert 'name="role-1-character__catalog_id"' not in html  # open
    # "Update form" with the fill set to default shows the select; no Engine call.
    data = posted(response)
    data["role-1-fill"] = "default"
    data["op"] = "refresh"
    page = operator_client.post(EDIT, as_post(data)).content.decode()
    assert 'name="role-1-character__catalog_id"' in page
    assert 'name="role-1-character__version"' in page
    assert engine.requests == []


def test_the_character_read_asks_for_published_characters(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_edit_reads(engine, cast_draft())
    operator_client.get(EDIT)
    (read,) = calls(engine, "GET", CHAR_LIST)
    assert read.url.params["status"] == "published"


def test_a_failed_character_read_is_a_raw_id_input(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_edit_reads(engine, cast_draft())
    engine.respond("GET", CHAR_LIST, 503, json=error("engine_unavailable", "down"))
    html = operator_client.get(EDIT).content.decode()
    assert f'name="role-0-character__catalog_id" value="{CHAR}"' in html
    assert "Catalog characters unavailable: 503 engine_unavailable — enter the id" in html


def test_a_saved_character_not_listed_is_kept_as_missing(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = cast_draft()
    gone = "01JC00000000000000000CHAR7"
    body["cast"]["roles"][0]["character"] = {"catalog_id": gone}
    html = edit_page(operator_client, engine, body).content.decode()
    assert f'<option value="{gone}" selected>{gone} (missing)</option>' in html


def test_an_archived_scenario_disables_the_cast(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, cast_draft(status="archived")).content.decode()
    assert 'id="f-cast-roles-0--public_label" disabled>' in html
    assert 'id="f-starting_state-seeded_memories-0--text" disabled' in html
    assert 'value="add:role" formnovalidate disabled' in html
    assert 'value="add:role-0-seed" formnovalidate disabled' in html


def test_a_new_role_starts_npc_required_open(operator_client: Client, engine: FakeEngine) -> None:
    data = op(operator_client, form(operator_client, engine), "add:role")
    assert data["role-2-kind"] == "npc"
    assert data["role-2-required"] == "on"
    assert data["role-2-fill"] == "open"
    assert data["role-2-seed-TOTAL_FORMS"] == "0"
    # A blank new role is dropped: nothing changed.
    data["op"] = "save"
    assert operator_client.post(EDIT, as_post(data)).status_code == 302
    assert writes(engine) == []


def test_a_new_seed_without_a_valid_role_id_stays_unset(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = op(operator_client, form(operator_client, engine), "add:role")
    data["role-2-id"] = "Bad Id"
    data = op(operator_client, data, "add:role-2-seed")
    assert data.get("role-2-seed-0-holder") in (None, "")
    assert data.getlist("role-2-seed-0-audience") == []


def test_removing_a_role_renumbers_its_seeds(operator_client: Client, engine: FakeEngine) -> None:
    data = op(operator_client, form(operator_client, engine), "remove:role-0")
    assert data["role-TOTAL_FORMS"] == "1"
    assert data["role-0-id"] == "son"
    assert data["role-0-seed-0-id"] == SEED3
    assert data["role-0-seed-TOTAL_FORMS"] == "1"


def test_audience_order_and_other_tokens(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    # The saved "self" sits in "Other audience"; the role ids and world are boxes.
    assert data.getlist("role-0-seed-1-audience") == ["mother", "son"]
    assert data["role-0-seed-1-audience_other"] == "self"
    data.setlist("role-0-seed-1-audience", ["world", "son", "mother"])
    data["role-0-seed-1-audience_other"] = " Self , self, mother "
    save(operator_client, engine, data)
    seed = changes(engine)["cast"]["roles"][0]["seeded_memories"][1]
    # #206 (C9): the saved entries in their stored order, then the new ones in the form's.
    assert seed["audience"] == ["mother", "son", "self", "world", "Self"]


def test_an_empty_audience_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data.setlist("role-0-seed-0-audience", [])
    data["role-0-seed-0-holder"] = ""
    save(operator_client, engine, data)
    seed = changes(engine)["cast"]["roles"][0]["seeded_memories"][0]
    assert seed["audience"] is None
    assert seed["holder"] is None


def test_a_renamed_role_moves_its_audience_to_other(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data["role-1-id"] = "boy"
    data = op(operator_client, data, "refresh")
    assert data.getlist("role-0-seed-1-audience") == ["mother"]
    assert data["role-0-seed-1-audience_other"] == "son, self"


def test_a_blank_seed_row_is_dropped(operator_client: Client, engine: FakeEngine) -> None:
    data = op(operator_client, form(operator_client, engine), "add:role-1-seed")
    data["role-1-public_label"] = "The son"
    save(operator_client, engine, data)
    assert len(changes(engine)["cast"]["roles"][1]["seeded_memories"]) == 1


def test_a_non_blank_role_needs_its_text(operator_client: Client, engine: FakeEngine) -> None:
    data = op(operator_client, form(operator_client, engine), "add:role")
    data["role-2-id"] = "guard"
    data["op"] = "save"
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 200
    role = response.context["editor"].cast.roles.forms[2]
    assert role.errors["public_label"] == ["This field is required."]
    assert role.errors["setup_description"] == ["This field is required."]
    assert writes(engine) == []


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("role-0-relationships", "[{", "Enter valid JSON"),
        ("role-0-relationships", '{"to_role": "son"}', "Enter a JSON array"),
        ("role-1-state_initial_values", "[]", "Enter a JSON object"),
        ("role-0-name_overrides", "NaN", "Enter valid JSON"),
        ("role-0-seed-0-valid_time", "[1]", "Enter a JSON object"),
        ("wseed-0-valid_time", "{", "Enter valid JSON"),
        ("cast__max_characters_at_start", "0", "greater than or equal to 1"),
        ("role-0-eligibility__age_range_min", "151", "less than or equal to 150"),
        ("role-0-character__version", "0", "greater than or equal to 1"),
    ],
)
def test_bad_input_makes_no_call(
    operator_client: Client, engine: FakeEngine, name: str, value: str, message: str
) -> None:
    data = form(operator_client, engine)
    data[name] = value
    data["op"] = "save"
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 200
    assert engine.requests == []
    assert message in text(response.content.decode())


def test_too_many_audience_entries_make_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data["role-0-seed-0-audience_other"] = ", ".join(f"t{n}" for n in range(40))
    data["op"] = "save"
    response = operator_client.post(EDIT, as_post(data))
    assert engine.requests == []
    seed = response.context["editor"].cast.seed_sets[0].forms[0]
    assert seed.errors["audience"] == ["At most 40 audience entries."]


# --- Engine errors at their rows ------------------------------------------------------------------


def refused(*failures: tuple[str, str]) -> dict[str, Any]:
    errors = [{"field": f"body.{path}", "message": msg, "type": "x"} for path, msg in failures]
    return error("validation_error", "invalid scenario", errors=errors)


def test_each_save_rule_error_lands_on_its_row(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    # A blank role between the two: the Engine's index 1 is the son's row, now form row 2.
    data = op(operator_client, data, "add:role")
    moved = {k: v for k, v in data.lists() if k.startswith("role-1-")}
    for key, values in moved.items():
        data.setlist(key.replace("role-1-", "role-2-", 1), values)
        data.pop(key)
    data["role-1-kind"], data["role-1-fill"] = "npc", "open"
    data["role-1-seed-TOTAL_FORMS"] = "0"
    data["role-2-fill"] = "default"
    response = save(
        operator_client,
        engine,
        data,
        status=422,
        json=refused(
            ("cast.roles[1].fill", "the player role is filled by the player's persona"),
            ("cast.roles[1].character", "a `default` role names the catalog character"),
            ("cast.roles[0].id", "another role is `mother`"),
            ("cast.roles[0].relationships[0].to_role", "no role of this scenario is `x`"),
            ("cast.roles[1]", "row-level"),
            ("cast.roles[7].id", "no such row"),
        ),
    )
    assert response.status_code == 200
    roles = response.context["editor"].cast.roles.forms
    assert roles[2].errors["fill"] == ["the player role is filled by the player's persona"]
    assert roles[2].errors["character__catalog_id"] == [
        "a `default` role names the catalog character"
    ]
    assert roles[2].non_field_errors() == ["row-level"]
    assert roles[0].errors["id"] == ["another role is `mother`"]
    assert roles[0].errors["relationships"] == [
        "cast.roles[0].relationships[0].to_role: no role of this scenario is `x`"
    ]
    assert roles[1].errors == {}
    assert response.context["editor"].cast.cast_errors == ["cast.roles[7].id: no such row"]
    html = response.content.decode()
    assert "the player role is filled by the player&#x27;s persona" in html


def test_nested_seed_errors_are_placed(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["role-0-public_label"] = "Ma"
    response = save(
        operator_client,
        engine,
        data,
        status=422,
        json=refused(
            ("cast.roles[0].seeded_memories[0].audience", "audience refused"),
            ("cast.roles[0].seeded_memories[1].audience[2]", "bad token"),
            ("cast.roles[0].seeded_memories[1].holder", "no role of this scenario is `x`"),
            ("cast.roles[0].seeded_memories[0].valid_time.from", "bad time"),
            ("cast.roles[0].seeded_memories[0].confidence", "bad confidence"),
            ("cast.roles[0].seeded_memories[1].nested", "seed_nested_missing"),
            ("cast.roles[0].seeded_memories[1].nested.proposition", "too long"),
            ("starting_state.seeded_memories[0].kind", "bad kind"),
            ("starting_state.seeded_memories[0].nested.attributed_confidence", "bad level"),
            ("starting_state.seeded_memories[3].kind", "no such seed"),
        ),
    )
    cast = response.context["editor"].cast
    first, second = cast.seed_sets[0].forms
    assert first.errors["audience"] == ["audience refused"]
    assert first.errors["valid_time"] == [
        "cast.roles[0].seeded_memories[0].valid_time.from: bad time"
    ]
    assert second.errors["audience"] == ["self: bad token"]
    assert second.errors["holder"] == ["no role of this scenario is `x`"]
    # #206: the confidence and nested content at their fields; ``nested`` itself on its role.
    assert first.errors["confidence"] == ["bad confidence"]
    assert second.errors["nested__attributed_holder"] == ["seed_nested_missing"]
    assert second.errors["nested__proposition"] == ["too long"]
    assert cast.world.forms[0].errors["kind"] == ["bad kind"]
    assert cast.world.forms[0].errors["nested__attributed_confidence"] == ["bad level"]
    assert cast.world_errors == ["starting_state.seeded_memories[3].kind: no such seed"]


def test_the_cast_limit_error_lands_on_the_limit(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data["cast__max_characters_at_start"] = "900"
    body = error(
        "cast_limit_exceeded",
        "above the ceiling of 8",
        field="cast.max_characters_at_start",
        max_characters_at_start=900,
        ceiling=8,
    )
    response = save(operator_client, engine, data, status=422, json=body)
    limit = response.context["editor"].cast.limit
    assert limit.errors["cast__max_characters_at_start"] == ["above the ceiling of 8"]


def test_the_cast_limit_error_without_a_field(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data["cast__max_characters_at_start"] = "900"
    body = error("cast_limit_exceeded", "above the ceiling of 8", ceiling=8)
    response = save(operator_client, engine, data, status=422, json=body)
    limit = response.context["editor"].cast.limit
    assert limit.errors["cast__max_characters_at_start"] == ["above the ceiling of 8"]


# --- create ---------------------------------------------------------------------------------------


def test_create_sends_the_cast(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    answer_profiles(engine)
    response = operator_client.get(NEW_URL)
    engine.reset()
    data = posted(response)
    assert data["role-TOTAL_FORMS"] == "0"
    assert data["wseed-TOTAL_FORMS"] == "0"
    data["op"] = "add:role"
    data = posted(operator_client.post(NEW_URL, as_post(data)))
    data.update(
        {
            "role-0-id": "guard",
            "role-0-public_label": "Guard",
            "role-0-setup_description": "Watches the gate.",
        }
    )
    data["op"] = "save"
    engine.respond("POST", S_LIST, 201, json=fixture("post"))
    created = operator_client.post(NEW_URL, as_post(data))
    assert created.status_code == 302
    (sent,) = calls(engine, "POST", S_LIST)
    assert body_of(sent)["cast"] == {
        "roles": [
            {
                "id": "guard",
                "kind": "npc",
                "public_label": "Guard",
                "setup_description": "Watches the gate.",
                "required": True,
                "fill": "open",
                "character": None,
                "eligibility": None,
                "starting_location": None,
                "participation": None,
                "seeded_memories": [],
                "relationships": [],
                "name_overrides": None,
                "state_initial_values": None,
            }
        ]
    }
    assert created["Location"] == f"/catalog/scenarios/{NEW_SCN}/edit/"


# --- the detail page ------------------------------------------------------------------------------


def detail_html(client: Client, engine: FakeEngine, body: dict[str, Any], query: str = "") -> str:
    answer_ratings(engine)
    answer_profiles(engine)
    engine.respond("GET", S_ITEM, 200, json=body)
    response = client.get(f"{DETAIL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def cast_detail() -> dict[str, Any]:
    body = cast_draft()
    for key in ("draft_revision",):
        body.pop(key)
    return body


def test_the_detail_page_shows_the_cast_tables(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/catalog/characters/{character_id}", 200, json=character_card())
    html = detail_html(operator_client, engine, cast_detail(), "?view=draft")
    page = text(html)
    assert '<table class="cast-roles">' in html
    assert "Most characters at start: 4" in page
    assert f'<a href="/catalog/characters/{CHAR}/">Sir Aldric</a>' in html
    assert "@2" in page
    assert "The farmhouse ( farmhouse )" in page
    assert "labels: farmer · ratings: Teen · ages 40\u2013any" in page
    assert "anyone" in page  # the son's eligibility
    for seed_id in (SEED1, SEED2, SEED3):
        assert f'<tr id="seed-{seed_id}">' in html
        assert f"<code>{seed_id}</code>" in html
    world = html[html.index('<section id="world-seeds">') :]
    assert f"<code>{SEED4}</code>" in world
    assert "The harvest is late this year." in world
    assert "&quot;from&quot;: &quot;last year&quot;" in html  # a seed's valid_time
    assert "&quot;canonical&quot;: &quot;Martha&quot;" in html
    # One card read per distinct character; one ratings read for the page.
    reads = calls(engine, "GET", f"/catalog/characters/{CHAR}")
    assert [dict(r.url.params) for r in reads] == [{"projection": "card"}]
    assert len(calls(engine, "GET", RATINGS_PATH)) == 1


def character_card(**changes: Any) -> dict[str, Any]:
    from tests.catalog.helpers import card

    return card(**changes)


def test_an_archived_or_unread_character_is_marked(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", "/catalog/characters/{character_id}", 200, json=character_card(status="archived")
    )
    html = detail_html(operator_client, engine, cast_detail(), "?view=draft")
    assert "Sir Aldric</a> (archived)" in html
    engine.respond("GET", "/catalog/characters/{character_id}", 404, json=error("not_found"))
    html = detail_html(operator_client, engine, cast_detail(), "?view=draft")
    assert "(name unavailable: 404 not_found)" in html


def test_an_undeclared_location_and_latest_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = cast_detail()
    body["cast"]["roles"][0]["starting_location"] = "attic"
    body["cast"]["roles"][0]["character"] = {"catalog_id": CHAR}
    engine.respond("GET", "/catalog/characters/{character_id}", 200, json=character_card())
    page = text(detail_html(operator_client, engine, body, "?view=draft"))
    assert "attic (undeclared)" in page
    assert "Sir Aldric latest" in page


def test_the_public_card_view_has_no_cast_table(
    operator_client: Client, engine: FakeEngine
) -> None:
    from tests.catalog.scenario_helpers import card_of

    html = detail_html(operator_client, engine, card_of(cast_detail()), "?projection=card")
    assert '<table class="cast-roles">' not in html
    assert '<table class="card-roles">' in html


# --- units ----------------------------------------------------------------------------------------


def parsed(body: dict[str, Any]) -> Scenario:
    scenario = Scenario.from_json(body)
    assert scenario is not None
    return scenario


def test_normalize_cast_fills_defaults_and_drops_empties() -> None:
    sparse = {"roles": [{"id": "a", "kind": "npc", "public_label": "A", "setup_description": "s"}]}
    full: dict[str, Any] = {
        "max_characters_at_start": None,
        "roles": [
            {
                "id": "a",
                "kind": "npc",
                "public_label": "A",
                "setup_description": "s",
                "required": True,
                "fill": "open",
                "character": None,
                "eligibility": {},
                "seeded_memories": [],
                "relationships": [],
                "name_overrides": None,
            }
        ],
    }
    assert normalize_cast(sparse) == normalize_cast(full)
    changed = copy.deepcopy(full)
    changed["roles"][0]["required"] = False
    assert normalize_cast(changed) != normalize_cast(sparse)
    assert normalize_cast(None) == {}
    # #206: groups ({} and null-valued entries read as none), relationships' and seeds' M5 keys.
    assert normalize_cast({**full, "groups": {}}) == normalize_cast(sparse)
    assert normalize_cast({**full, "groups": {"gone": None}}) == normalize_cast(sparse)
    grouped = {**sparse, "groups": {"g": ["b", "a"], "h": []}}
    reordered = {**full, "groups": {"h": [], "g": ["b", "a"], "old": None}}
    assert normalize_cast(grouped) == normalize_cast(reordered)
    m5: dict[str, Any] = json.loads(json.dumps(sparse))
    m5["roles"][0]["relationships"] = [{"to_role": "b", "name": "n", "trust": 0.5}]
    m5["roles"][0]["seeded_memories"] = [
        {
            "text": "t",
            "confidence": "known",
            "nested": {"attributed_holder": "b", "proposition": "p"},
        }
    ]
    m5_full = copy.deepcopy(full)
    m5_full["roles"][0]["relationships"] = [
        {"name": "n", "to_role": "b", "audience": None, "trust": 0.5}
    ]
    m5_full["roles"][0]["seeded_memories"] = [
        {
            "text": "t",
            "holder": None,
            "confidence": "known",
            "nested": {"attributed_holder": "b", "proposition": "p", "attributed_confidence": None},
        }
    ]
    assert normalize_cast(m5) == normalize_cast(m5_full)
    assert normalize_cast(m5) != normalize_cast(sparse)


def editor_for(body: dict[str, Any], **changes: str) -> ScenarioEditor:
    original = draft_to_write(parsed(body))
    data = editor_data(original)
    for name, value in changes.items():
        data[name] = value
    return ScenarioEditor(
        data,
        ratings=RatingChoices([(TEEN, "Teen")]),
        profiles=ProfileChoices([("lite-default", "Lite default")]),
        create=False,
        original=original,
        cast_choices=CastChoices(CharacterChoices([(CHAR, "Sir Aldric")]), [(TEEN, "Teen")]),
    )


@pytest.mark.parametrize("draft", ["draft_cast", "draft_cast_m5"])
def test_the_draft_round_trips_through_the_editor(draft: str) -> None:
    body = fixture(draft)  # #206: the M5 draft's groups, trust/audience, confidence and nested
    editor = editor_for(body)
    assert editor.is_valid(), editor.cast.roles.errors
    assert editor.edit_patch(draft_to_write(parsed(body))) == {}
    assert editor.cast.cast_body() == the_cast(body)


def test_the_role_selects_offer_the_role_rows() -> None:
    editor = editor_for(cast_draft(), **{"role-1-id": "boy"})

    def options(bound: object) -> list[str]:
        return re.findall(r'<option value="([^"]*)"', str(bound))

    assert "boy" in options(editor.form["story_settings_defaults__default_output_viewpoint"])
    holder = editor.cast.seed_sets[0].forms[0]["holder"]
    assert options(holder) == ["", "mother", "boy", "world"]


def test_a_companion_keeps_its_null_pin() -> None:
    body = cast_draft(companion_of=CHAR)
    body["cast"]["roles"][0]["character"] = {"catalog_id": CHAR, "version": None}
    assert draft_to_write(parsed(body))["cast"]["roles"][0]["character"] == {
        "catalog_id": CHAR,
        "version": None,
    }


def test_the_cast_enums_match_the_schema() -> None:
    from console.forms import scenario_cast
    from tests.fakes.engine import recorded_schema

    schemas = recorded_schema()["components"]["schemas"]

    def enum(node: dict[str, Any]) -> list[str]:
        options = node.get("anyOf") or [node]
        (values,) = [o["enum"] for o in options if "enum" in o]
        return list(values)

    role = schemas["ScenarioRole"]["properties"]
    seed = schemas["Seed"]["properties"]
    nested = schemas["NestedSeed"]["properties"]
    checks: list[tuple[tuple[str, ...], dict[str, Any]]] = [
        (scenario_cast.ROLE_KINDS, role["kind"]),
        (scenario_cast.FILLS, role["fill"]),
        (scenario_cast.PARTICIPATION, role["participation"]),
        (scenario_cast.DISCLOSURES, seed["disclosure"]),
        (scenario_cast.SEED_KINDS, seed["kind"]),
        (scenario_cast.SEED_CONFIDENCES, seed["confidence"]),
        (scenario_cast.SEED_CONFIDENCES, nested["attributed_confidence"]),
    ]
    for ours, node in checks:
        assert sorted(ours) == sorted(enum(node))


# --- #206: groups, relationship trust and audience, seed confidence and nested content -----------

M5 = "01JC0000000000000000M5SD0{}"
FAMILY = {"family": ["bob", "alice"], "staff": ["carol"]}
GROUP_ERRORS = ("name", "members")


def m5_draft(*, certain: bool = True, **changes: Any) -> dict[str, Any]:
    """The M5 draft (roles alice, bob, carol; groups ``family`` stored as [bob, alice] and
    ``staff``; alice's seeds: a canon secret known to ``family``, a belief at ``suspected``, one
    at the unknown ``certain``, a nested seed; a world seed with confidence and nested content).
    The schema refuses ``certain``: ``certain=False`` reads ``known`` (a schema-valid write)."""
    body = fixture("draft_cast_m5", **changes)
    if not certain:
        body["cast"]["roles"][0]["seeded_memories"][2]["confidence"] = "known"
    return body


def m5_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    """The edit page of ``body`` (default :func:`m5_draft`), served unvalidated (C2's open enum
    ``certain``)."""
    served = body if body is not None else m5_draft()
    answer_ratings(engine)
    answer_profiles(engine)
    engine.side_effect("GET", S_ITEM, lambda request: httpx.Response(200, json=served))
    response = client.get(EDIT)
    assert response.status_code == 200
    engine.reset()
    return response


def m5_form(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> QueryDict:
    return posted(m5_page(client, engine, body))


def valid_changes(engine: FakeEngine) -> dict[str, Any]:
    """The PATCH sent, checked against the request schema (no form-only key leaks)."""
    from tests.catalog.test_scenarios_edit import schema_valid

    (sent,) = calls(engine, "PATCH", PATH)
    schema_valid(engine, body_of(sent), "ScenarioPatch")
    return changes(engine)


def saved(client: Client, data: QueryDict) -> Any:
    """Press Save expecting a local refusal: the page again, no Engine call."""
    data["op"] = "save"
    response = client.post(EDIT, as_post(data))
    assert response.status_code == 200
    return response


def group_errors(response: Any, index: int) -> dict[str, list[str]]:
    return dict(response.context["editor"].cast.groups.forms[index].errors)


def seed_errors(response: Any, role: int, index: int) -> dict[str, list[str]]:
    return dict(response.context["editor"].cast.seed_sets[role].forms[index].errors)


def test_m5_cast_fields_untouched_send_nothing(operator_client: Client, engine: FakeEngine) -> None:
    data = m5_form(operator_client, engine)
    assert data["grp-TOTAL_FORMS"] == "2"
    assert data.getlist("grp-0-members") == ["bob", "alice"]
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 302
    assert writes(engine) == []
    body = m5_draft()
    editor = editor_for(body)
    assert editor.is_valid()
    assert editor.edit_patch(draft_to_write(parsed(body))) == {}


def test_editing_a_role_keeps_groups_relationships_and_seed_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = m5_draft(certain=False)
    data = m5_form(operator_client, engine, body)
    data["role-1-public_label"] = "Robert"
    save(operator_client, engine, data)
    cast = valid_changes(engine)["cast"]
    assert cast["groups"] == FAMILY
    assert list(cast["groups"]["family"]) == ["bob", "alice"]  # C9: the stored order
    for sent, stored in zip(cast["roles"], body["cast"]["roles"], strict=True):
        assert sent["relationships"] == stored["relationships"]
        assert sent["seeded_memories"] == stored["seeded_memories"]
    assert cast["roles"][0]["seeded_memories"][0]["audience"] == ["family", "alice"]
    assert cast["roles"][1]["public_label"] == "Robert"
    # A stored confidence outside the three is kept and sent back (C7).
    engine.reset()
    data = m5_form(operator_client, engine)
    data["role-1-public_label"] = "Robert"
    save(operator_client, engine, data)
    assert changes(engine)["cast"]["roles"][0]["seeded_memories"][2]["confidence"] == "certain"


def test_editing_a_world_seed_keeps_confidence_and_nested(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = m5_draft(certain=False)
    data = m5_form(operator_client, engine, body)
    data["wseed-0-text"] = "The house is very old."
    save(operator_client, engine, data)
    sent = valid_changes(engine)
    assert "cast" not in sent
    world = sent["starting_state"]["seeded_memories"]
    assert world[0]["text"] == "The house is very old."
    assert world[1] == body["starting_state"]["seeded_memories"][1]
    assert world[1]["nested"] == {"attributed_holder": "carol", "proposition": "Nobody saw her."}


def test_groups_are_sent_in_row_order_and_cleared_to_null(
    operator_client: Client, engine: FakeEngine
) -> None:
    # Swapping the two rows (members ticked in the form's order) sends nothing.
    data = m5_form(operator_client, engine)
    data["grp-0-name"], data["grp-1-name"] = "staff", "family"
    data.setlist("grp-0-members", ["carol"])
    data.setlist("grp-1-members", ["alice", "bob"])
    assert operator_client.post(EDIT, as_post(data)).status_code == 302
    assert writes(engine) == []
    # A stored ``{}`` with no rows sends nothing either.
    body = m5_draft(certain=False)
    body["cast"]["groups"] = {}
    data = m5_form(operator_client, engine, body)
    assert data["grp-TOTAL_FORMS"] == "0"
    assert operator_client.post(EDIT, as_post(data)).status_code == 302
    assert writes(engine) == []
    # A member added is appended; a new group comes after the rows before it.
    data = m5_form(operator_client, engine, m5_draft(certain=False))
    data.setlist("grp-0-members", ["alice", "bob", "carol"])
    with_rows(data, "grp", [{"name": " kin ", "members": ["carol", "alice"]}])
    save(operator_client, engine, data)
    groups = valid_changes(engine)["cast"]["groups"]
    # A new group's members in the form's (role-row) order.
    assert groups == {
        "family": ["bob", "alice", "carol"],
        "staff": ["carol"],
        "kin": ["alice", "carol"],
    }
    assert list(groups) == ["family", "staff", "kin"]
    # Every row removed: ``groups: null`` clears them all.
    engine.reset()
    data = m5_form(operator_client, engine, m5_draft(certain=False))
    data = op(operator_client, data, "remove:grp-0")
    data = op(operator_client, data, "remove:grp-0")
    assert data["grp-TOTAL_FORMS"] == "0"
    save(operator_client, engine, data)
    assert valid_changes(engine)["cast"]["groups"] is None


def test_a_removed_or_renamed_group_is_sent_as_null(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = op(
        operator_client, m5_form(operator_client, engine, m5_draft(certain=False)), "remove:grp-1"
    )
    save(operator_client, engine, data)
    assert valid_changes(engine)["cast"]["groups"] == {"family": ["bob", "alice"], "staff": None}
    engine.reset()
    data = m5_form(operator_client, engine, m5_draft(certain=False))
    data["grp-0-name"] = "kin"
    save(operator_client, engine, data)
    groups = valid_changes(engine)["cast"]["groups"]
    assert groups == {"kin": ["alice", "bob"], "staff": ["carol"], "family": None}
    assert list(groups) == ["kin", "staff", "family"]


def create_with_groups(
    client: Client, engine: FakeEngine, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Create a scenario with one role (``guard``) and ``rows`` of groups: the body sent."""
    from tests.catalog.test_scenarios_edit import schema_valid

    answer_ratings(engine)
    answer_profiles(engine)
    data = posted(client.get(NEW_URL))
    engine.reset()
    data["op"] = "add:role"
    data = posted(client.post(NEW_URL, as_post(data)))
    data.update(
        {
            "role-0-id": "guard",
            "role-0-public_label": "Guard",
            "role-0-setup_description": "Watches the gate.",
        }
    )
    with_rows(data, "grp", rows)
    data["op"] = "save"
    engine.respond("POST", S_LIST, 201, json=fixture("post"))
    assert client.post(NEW_URL, as_post(data)).status_code == 302
    (sent,) = calls(engine, "POST", S_LIST)
    body: dict[str, Any] = body_of(sent)
    schema_valid(engine, body, "ScenarioCreate")
    return body


def test_create_sends_groups_with_empty_members(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = create_with_groups(operator_client, engine, [{"name": "g"}])
    assert body["cast"]["groups"] == {"g": []}
    engine.reset()
    body = create_with_groups(operator_client, engine, [])
    assert "groups" not in body["cast"]


def with_rows(data: QueryDict, prefix: str, rows: list[dict[str, Any]]) -> QueryDict:
    from tests.catalog.test_scenarios_edit import with_rows as append

    return append(data, prefix, rows)


@pytest.mark.parametrize(
    ("rows", "index", "message"),
    [
        ([{"name": "  ", "members": ["alice"]}], 2, "A group needs its name"),
        ([{"name": "family"}], 2, "Already used by row 1"),
        ([{"name": " staff "}, {"name": "staff", "members": ["bob"]}], 3, "Already used by row 2"),
        ([{"name": "self"}], 2, "self is an audience level"),
        ([{"name": "public"}], 2, "public is an audience level"),
        ([{"name": "world"}], 2, "world is an audience level"),
        (
            [{"name": "carol", "members": ["bob"]}],
            2,
            "A role has this id: the group would hide the role in audiences",
        ),
    ],
    ids=["blank", "repeat", "third-repeat", "self", "public", "world", "role-id"],
)
def test_group_name_rules_refused_locally(
    operator_client: Client,
    engine: FakeEngine,
    rows: list[dict[str, Any]],
    index: int,
    message: str,
) -> None:
    data = with_rows(m5_form(operator_client, engine), "grp", rows)
    response = saved(operator_client, data)
    assert engine.requests == []
    assert group_errors(response, index) == {"name": [message]}
    # A colliding name is offered once (its first row) or not at all.
    audience = response.context["editor"].cast.seed_sets[0].forms[0].fields["audience"]
    assert [v for v, _ in audience.choices] == [
        "alice",
        "bob",
        "carol",
        "family",
        "staff",
        "world",
    ]


def test_a_fully_blank_group_row_is_dropped(operator_client: Client, engine: FakeEngine) -> None:
    """#222: a group row with a whitespace-only name and no members sends nothing."""
    data = with_rows(m5_form(operator_client, engine), "grp", [{"name": "   "}])
    assert data["grp-TOTAL_FORMS"] == "3"
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 302
    assert writes(engine) == []


@pytest.mark.parametrize("button", ["refresh", "add:grp"])
def test_group_and_nested_checks_stay_quiet_until_save(
    operator_client: Client, engine: FakeEngine, button: str
) -> None:
    """#220: a reserved group name and a half-filled nested seed raise no error on "Update
    form" or a row button; Save refuses both, all without an Engine call."""
    data = with_rows(m5_form(operator_client, engine), "grp", [{"name": "self"}])
    data["role-1-seed-0-nested__attributed_holder"] = "alice"  # no proposition
    data["op"] = button
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 200
    assert engine.requests == []
    cast = response.context["editor"].cast
    assert all(form.errors == {} for form in cast.groups.forms)
    assert all(form.errors == {} for seeds in cast.seed_sets for form in seeds.forms)
    assert "self is an audience level" not in response.content.decode()
    response = saved(operator_client, posted(response))
    assert engine.requests == []
    assert group_errors(response, 2) == {"name": ["self is an audience level"]}
    assert seed_errors(response, 1, 0) == {
        "nested__proposition": ["A nested belief needs both the role and what it believes"]
    }


def test_audience_offers_groups_and_rehomes_a_renamed_group(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = m5_page(operator_client, engine)
    html = response.content.decode()
    data = posted(response)
    # The saved audience [family, alice] is ticked, not in "Other audience".
    assert data.getlist("role-0-seed-0-audience") == ["family", "alice"]
    assert not data.get("role-0-seed-0-audience_other")
    seed = response.context["editor"].cast.seed_sets[0].forms[0]
    assert seed.fields["audience"].choices == [
        ("alice", "alice"),
        ("bob", "bob"),
        ("carol", "carol"),
        ("family", "family (group)"),
        ("staff", "staff (group)"),
        ("world", "world"),
    ]
    assert "family (group)</label>" in html
    # Renamed: "Update form" offers the new name and moves the ticked old one to "Other".
    data["grp-0-name"] = "kin"
    data = op(operator_client, data, "refresh")
    assert data.getlist("role-0-seed-0-audience") == ["alice"]
    assert data["role-0-seed-0-audience_other"] == "family"
    # C9: an untouched saved audience is sent unchanged.
    engine.reset()
    data = m5_form(operator_client, engine, m5_draft(certain=False))
    data["role-1-public_label"] = "Robert"
    data.setlist("role-0-seed-1-audience", ["family", "alice"])  # a group added to a saved one
    save(operator_client, engine, data)
    seeds = valid_changes(engine)["cast"]["roles"][0]["seeded_memories"]
    assert seeds[0]["audience"] == ["family", "alice"]
    assert seeds[1]["audience"] == ["alice", "family"]


def test_believes_that_offers_none_and_the_roles_never_world(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#221: the nested seed's "Believes that…" offers (none) plus the role ids, on a role
    seed and a world seed alike, never ``world``."""
    cast = m5_page(operator_client, engine).context["editor"].cast
    expected = [("", "(none)"), ("alice", "alice"), ("bob", "bob"), ("carol", "carol")]
    for form in (cast.seed_sets[0].forms[3], cast.seed_sets[1].forms[0], *cast.world.forms):
        choices = list(form.fields["nested__attributed_holder"].choices)
        assert choices == expected
        assert "world" not in [value for value, _ in choices]


def test_seed_confidence_unset_sends_no_key(operator_client: Client, engine: FakeEngine) -> None:
    response = m5_page(operator_client, engine)
    seeds = response.context["editor"].cast.seed_sets[0].forms
    assert seeds[1].fields["confidence"].choices == [
        ("", "Engine default (believed)"),
        ("suspected", "Suspected"),
        ("believed", "Believed"),
        ("known", "Known"),
    ]
    assert seeds[2].fields["confidence"].choices[-1] == ("certain", "Certain")  # kept (C7)
    assert seeds[3].fields["nested__attributed_confidence"].choices[0] == (
        "",
        "Engine default (known)",
    )
    assert "How sure the holder is of a belief seed." in response.content.decode()
    data = posted(response)
    data["role-1-public_label"] = "Robert"
    data["role-0-seed-1-confidence"] = ""  # suspected → the Engine default: no key
    data["role-0-seed-2-confidence"] = "believed"  # an explicit default is sent
    save(operator_client, engine, data)
    roles = changes(engine)["cast"]["roles"]
    alice, bob = roles[0]["seeded_memories"], roles[1]["seeded_memories"]
    assert "confidence" not in alice[1]
    assert alice[2]["confidence"] == "believed"
    assert "confidence" not in bob[0]
    assert "nested" not in bob[0]  # a fully blank nested block


@pytest.mark.parametrize(
    ("typed", "errors"),
    [
        (
            {"nested__attributed_holder": "alice"},
            {"nested__proposition": ["A nested belief needs both the role and what it believes"]},
        ),
        (
            {"nested__proposition": "Carol lied."},
            {
                "nested__attributed_holder": [
                    "A nested belief needs both the role and what it believes"
                ]
            },
        ),
        (
            {"nested__attributed_confidence": "known", "nested__proposition": "   "},
            {
                "nested__attributed_holder": [
                    "A nested belief needs both the role and what it believes"
                ],
                "nested__proposition": ["A nested belief needs both the role and what it believes"],
            },
        ),
        (
            {"nested__attributed_holder": "bob", "nested__proposition": "He knows."},
            {"nested__attributed_holder": ["A role can't be its own attributed holder"]},
        ),
        (
            {"text": "", "nested__attributed_holder": "alice", "nested__proposition": "Hm."},
            {"text": ["A seed needs its text."]},
        ),
        ({"text": "", "confidence": "known"}, {"text": ["A seed needs its text."]}),
    ],
    ids=["holder-only", "proposition-only", "confidence-only", "self", "no-text", "no-text-conf"],
)
def test_nested_seed_needs_both_fields(
    operator_client: Client, engine: FakeEngine, typed: dict[str, str], errors: dict[str, Any]
) -> None:
    data = m5_form(operator_client, engine)
    for name, value in typed.items():
        data[f"role-1-seed-0-{name}"] = value  # bob's seed, held by bob
    response = saved(operator_client, data)
    assert engine.requests == []
    assert seed_errors(response, 1, 0) == errors


def test_a_blank_role_kept_by_a_seed_confidence_is_refused(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = op(operator_client, m5_form(operator_client, engine), "add:role")
    data = op(operator_client, data, "add:role-3-seed")
    data["role-3-seed-0-confidence"] = "known"
    response = saved(operator_client, data)
    assert engine.requests == []
    role = response.context["editor"].cast.roles.forms[3]
    assert role.errors["id"] == ["This field is required."]
    assert seed_errors(response, 3, 0) == {"text": ["A seed needs its text."]}


def test_relationships_are_written_without_null_keys(
    operator_client: Client, engine: FakeEngine
) -> None:
    from console.forms.scenario_cast import RELATIONSHIPS_HELP, relationship_to_write

    response = m5_page(operator_client, engine, m5_draft(certain=False))
    html = response.content.decode()
    assert json.dumps(RELATIONSHIPS_HELP)[1:-1].replace('\\"', "&quot;") in html
    assert "&quot;trust&quot;: 0.2" in html  # a saved trust shows in the box
    data = posted(response)
    data["role-1-relationships"] = (
        '[{"audience": ["alice"], "trust": null, "name": "brother_of", "to_role": "alice"}]'
    )
    save(operator_client, engine, data)
    (sent,) = valid_changes(engine)["cast"]["roles"][1]["relationships"]
    assert list(sent.items()) == [
        ("to_role", "alice"),
        ("name", "brother_of"),
        ("audience", ["alice"]),
    ]
    typed = {"x": 1, "audience": None, "name": "n", "y": None, "trust": 0.5, "to_role": "a"}
    written = relationship_to_write(typed)
    assert isinstance(written, dict)
    assert list(written) == ["to_role", "name", "trust", "x"]
    assert relationship_to_write("oops") == "oops"


def nested_details(html: str, seed_id: str) -> str:
    """Seed ``seed_id``'s editor row, up to its Remove button."""
    start = html.index(f'id="seed-{seed_id}"')
    return html[start : html.index('value="remove:', start)]


def test_nested_details_open_for_kind_nested_or_values(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = m5_page(operator_client, engine)
    html = response.content.decode()
    note = (
        "Needs the nested_beliefs module; without it the seed stays deferred. The Console "
        "can&#x27;t read nested beliefs back yet (Engine #606)."
    )
    opened = '<details class="nested-belief" open><summary>Nested belief</summary>'
    closed = '<details class="nested-belief"><summary>Nested belief</summary>'
    nested = nested_details(html, M5.format(4))  # kind nested, with content
    assert opened in nested
    assert note in nested
    assert "Believes that…" in nested
    assert "…believes" in nested
    assert "At confidence" in nested
    assert nested.index("Confidence") < nested.index("Nested belief") < nested.index("Advanced")
    assert closed in nested_details(html, M5.format(2))  # a belief, nothing nested
    # Kind nested (after Update form), or a value typed, opens it; so does an error.
    data = posted(response)
    data["role-0-seed-1-kind"] = "nested"
    data["role-1-seed-0-nested__attributed_confidence"] = "known"
    data["op"] = "refresh"
    html = operator_client.post(EDIT, as_post(data)).content.decode()
    assert opened in nested_details(html, M5.format(2))
    assert opened in nested_details(html, M5.format(5))
    assert closed in nested_details(html, M5.format(1))
    data = m5_form(operator_client, engine)
    data["role-0-public_label"] = "Al"
    response = save(
        operator_client,
        engine,
        data,
        status=422,
        json=refused(("cast.roles[0].seeded_memories[0].nested", "seed_nested_missing")),
    )
    assert opened in nested_details(response.content.decode(), M5.format(1))


def test_group_and_nested_errors_are_placed(operator_client: Client, engine: FakeEngine) -> None:
    data = m5_form(operator_client, engine)
    data["role-0-public_label"] = "Al"
    response = save(
        operator_client,
        engine,
        data,
        status=422,
        json=refused(
            ("cast.groups.family.[key]", "string does not match the pattern"),
            ("cast.groups.staff", "not a list"),
            ("cast.groups.family[1]", "no role of this scenario"),
            ("cast.groups.nope[0]", "no such group"),
            ("cast.roles[0].seeded_memories[1].confidence", "bad level"),
            ("cast.roles[0].seeded_memories[3].nested", "seed_nested_missing"),
            ("cast.roles[0].seeded_memories[3].nested.attributed_holder", "unknown role"),
            ("cast.roles[0].seeded_memories[0].holder", "seed_holder_required"),
            ("starting_state.seeded_memories[1].nested.proposition", "blank"),
        ),
    )
    assert response.status_code == 200
    assert group_errors(response, 0) == {
        "name": ["string does not match the pattern"],
        "members": ["alice: no role of this scenario"],
    }
    assert group_errors(response, 1) == {"members": ["not a list"]}
    cast = response.context["editor"].cast
    assert cast.cast_errors == ["cast.groups.nope[0]: no such group"]
    assert seed_errors(response, 0, 1) == {"confidence": ["bad level"]}
    assert seed_errors(response, 0, 3) == {
        "nested__attributed_holder": ["seed_nested_missing", "unknown role"]
    }
    assert seed_errors(response, 0, 0) == {"holder": ["seed_holder_required"]}
    assert cast.world.forms[1].errors == {"nested__proposition": ["blank"]}
    # No group error lands on a role row.
    assert all(form.errors == {} for form in cast.roles.forms)
    assert "alice: no role of this scenario" in response.content.decode()


def test_group_and_nested_report_errors_are_placed(
    operator_client: Client, engine: FakeEngine
) -> None:
    from tests.catalog.scenario_helpers import SCN

    def entry(code: str, field: str) -> dict[str, Any]:
        return {
            "code": code,
            "location": {
                "entry": SCN,
                "version": "draft",
                "field": field,
                "role": None,
                "seed_id": None,
            },
            "outcome": "rejected",
            "rule": "refused",
            "source": None,
            "text": None,
        }

    body = m5_draft(certain=False)
    data = m5_form(operator_client, engine, body)
    data["role-0-public_label"] = "Al"
    data["action"] = "publish"
    report = fixture("publish_invalid")
    report["error"]["details"]["report"]["errors"] = [
        entry("seed_reference_unknown", "cast.groups.staff[0]"),
        entry("seed_nested_missing", "cast.roles[0].seeded_memories[3].nested"),
        entry(
            "seed_reference_unknown", "cast.roles[0].seeded_memories[3].nested.attributed_holder"
        ),
        entry("seed_holder_required", "starting_state.seeded_memories[1].holder"),
        entry("seed_audience_unknown", "cast.roles[0].seeded_memories[0].audience[0]"),
    ]
    engine.respond("PATCH", S_ITEM, 200, json=fixture("patch"))
    engine.respond("POST", "/catalog/scenarios/{scenario_id}/publish", 422, json=report)
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 302
    page = operator_client.get(response["Location"])
    assert group_errors(page, 1) == {"members": ["carol: seed_reference_unknown: refused"]}
    assert seed_errors(page, 0, 3) == {
        "nested__attributed_holder": [
            "seed_nested_missing: refused",
            "seed_reference_unknown: refused",
        ]
    }
    world = page.context["editor"].cast.world.forms[1]
    assert dict(world.errors) == {"holder": ["seed_holder_required: refused"]}
    assert seed_errors(page, 0, 0) == {"audience": ["family: seed_audience_unknown: refused"]}


def test_an_archived_scenario_disables_groups_and_seed_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = m5_page(operator_client, engine, m5_draft(status="archived")).content.decode()
    assert re.search(r'<input type="text" name="grp-0-name"[^>]* disabled', html)
    assert re.search(r'<input type="checkbox" name="grp-0-members" value="bob"[^>]* disabled', html)
    assert re.search(r'<select name="role-0-seed-0-confidence"[^>]* disabled', html)
    for name in ("nested__attributed_holder", "nested__attributed_confidence"):
        assert re.search(rf'<select name="role-0-seed-3-{name}"[^>]* disabled', html)
    assert re.search(r'<textarea name="role-0-seed-3-nested__proposition"[^>]* disabled', html)
    assert 'value="add:grp" formnovalidate disabled' in html


def test_the_groups_rows_follow_the_roles(operator_client: Client, engine: FakeEngine) -> None:
    html = m5_page(operator_client, engine).content.decode()
    cast = html[html.index('id="f-cast"') :]
    groups = cast.index('<fieldset class="rows rows-grp" id="f-cast-groups">')
    assert cast.index('id="f-cast-roles"') < groups
    assert "<legend>Groups</legend>" in cast[groups:]
    assert (
        "A seed&#x27;s audience can name a group to mean its members. Use Update form to offer "
        "a new or renamed group in the audiences."
    ) in cast[groups:]
    assert 'id="grp-1"' in cast
    # Members offer the role rows' ids, then a missing ticked one, kept as "(missing)".
    data = m5_form(operator_client, engine)
    data["role-2-id"] = "cook"
    data["op"] = "refresh"
    response = operator_client.post(EDIT, as_post(data))
    page = response.content.decode()
    assert posted(response).getlist("grp-1-members") == ["carol"]
    assert "carol (missing)" in page


def m5_detail(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> str:
    """The M5 draft's detail page, served unvalidated (C2's ``certain``)."""
    served = body if body is not None else m5_draft()
    served.pop("draft_revision", None)
    answer_ratings(engine)
    answer_profiles(engine)
    engine.side_effect("GET", S_ITEM, lambda request: httpx.Response(200, json=served))
    response = client.get(f"{DETAIL}?view=draft")
    assert response.status_code == 200
    return response.content.decode()


def test_the_detail_page_shows_groups_relationships_and_seed_confidence(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = m5_detail(operator_client, engine)
    section = html[html.index('<section id="cast">') :]
    assert (
        '<dl class="cast-groups"><dt>Groups</dt><dd><code>family</code>: <code>bob</code>, '
        "<code>alice</code><br><code>staff</code>: <code>carol</code></dd></dl>"
    ) in section
    assert section.index("Most characters at start") < section.index("cast-groups")
    assert section.index("cast-groups") < section.index('<table class="cast-roles">')
    alice = section[section.index('id="role-alice-detail"') : section.index('id="role-bob"')]
    table = alice[
        alice.index('<table class="relationships">') : alice.index(
            "</table>", alice.index('class="relationships"')
        )
    ]
    page = text(table)
    assert "To role Name Trust Known by" in page
    assert "carol rival_of 0.20 alice , carol , bob" in page
    assert "bob sister_of — (by name) its two roles (default)" in page
    assert "None" not in page
    assert "null" not in page
    assert "&quot;to_role&quot;" not in alice  # the table replaces the JSON
    carol = section[section.index('id="role-carol-detail"') :]
    assert "1.00" in text(carol[: carol.index("</table>")])
    # The Confidence column, right after Kind.
    assert "<th>Kind</th><th>Confidence</th><th>Text</th>" in html

    def confidence(seed: int) -> str:
        row = html[html.index(f'<tr id="seed-{M5.format(seed)}">') :]
        cell = re.search(r'<td class="seed-confidence">([^<]*)</td>', row)
        assert cell is not None
        return cell[1]

    assert [confidence(n) for n in range(1, 8)] == [
        "—",  # canon
        "Suspected",
        "Certain",  # unknown, humanised (C3)
        "Believed (default)",  # nested, unset
        "—",
        "—",
        "Known",
    ]
    assert (
        "Nested: believes <code>bob</code> believes “The will is in the safe.” at Suspected"
    ) in html
    assert (
        "Nested: believes <code>carol</code> believes “Nobody saw her.” at Known (default)" in html
    )


@pytest.mark.parametrize(("stored", "shown"), [(True, "true"), ("high", "&quot;high&quot;")])
def test_the_detail_page_shows_a_non_number_trust_as_raw_json(
    operator_client: Client, engine: FakeEngine, stored: Any, shown: str
) -> None:
    """#225: a stored non-number trust (a boolean included) is the Trust cell's raw JSON, not
    a page-level bad_response."""
    body = m5_draft()
    body["cast"]["roles"][0]["relationships"][0]["trust"] = stored
    html = m5_detail(operator_client, engine, body)
    assert "bad_response" not in html
    alice = html[html.index('id="role-alice-detail"') : html.index('id="role-bob"')]
    assert f"<td><code>{shown}</code></td>" in alice


def test_the_detail_page_groups_defaults(operator_client: Client, engine: FakeEngine) -> None:
    body = m5_draft()
    body["cast"]["groups"] = {"empty": []}
    body["cast"]["roles"][1]["name_overrides"] = {"canonical": "Robert"}
    body["cast"]["roles"][1]["seeded_memories"][0]["kind"] = "belief"
    html = m5_detail(operator_client, engine, body)
    assert "<dd><code>empty</code>: no members</dd>" in html
    bob = html[html.index('id="role-bob-detail"') : html.index('id="role-carol"')]
    assert "<dt>Relationships</dt><dd>—</dd>" in bob
    assert '<td class="seed-confidence">Believed (default)</td>' in bob  # an unset belief
    body["cast"]["groups"] = None
    assert "<dt>Groups</dt><dd>—</dd>" in m5_detail(operator_client, engine, body)

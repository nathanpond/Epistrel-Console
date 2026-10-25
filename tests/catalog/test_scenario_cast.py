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
    return {"max_characters_at_start": body["cast"]["max_characters_at_start"], "roles": roles}


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
    assert seed["audience"] == ["mother", "son", "world", "Self", "self"]


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
            ("starting_state.seeded_memories[0].kind", "bad kind"),
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
    assert cast.world.forms[0].errors["kind"] == ["bad kind"]
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


def test_the_draft_round_trips_through_the_editor() -> None:
    body = cast_draft()
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
    checks: list[tuple[tuple[str, ...], dict[str, Any]]] = [
        (scenario_cast.ROLE_KINDS, role["kind"]),
        (scenario_cast.FILLS, role["fill"]),
        (scenario_cast.PARTICIPATION, role["participation"]),
        (scenario_cast.DISCLOSURES, seed["disclosure"]),
        (scenario_cast.SEED_KINDS, seed["kind"]),
    ]
    for ours, node in checks:
        assert sorted(ours) == sorted(enum(node))

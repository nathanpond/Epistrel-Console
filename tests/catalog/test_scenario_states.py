"""A scenario's starting States and each role's initial values in the scenario editor (#267)."""

import copy
import json
from html import unescape
from typing import Any

import pytest
from django.http import QueryDict
from django.test import Client
from jsonschema import Draft202012Validator

from console.engine.choices import ProfileChoices, RatingChoices
from console.engine.models import Scenario
from console.forms import scenario_states, states
from console.forms.scenario import DEFERRED_BOXES, SECTIONS, ScenarioEditor, draft_to_write
from console.forms.scenario import editor_data as scenario_editor_data
from console.forms.scenario_cast import INITIAL_VALUE_INVALID, INITIAL_VALUES_HELP
from console.forms.scenario_states import (
    NAME_NEEDED,
    NAME_USED,
    SNAPSHOT_FIELDS,
    STATES_HELP,
    WORLD_CLOCK_RESERVED,
    ScenarioStateForm,
    number_text,
    state_to_write,
)
from console.forms.world import NUMBER_INVALID
from console.models import EngineOperation
from tests.catalog.helpers import body_of, calls, error, text
from tests.catalog.scenario_helpers import (
    EDIT,
    NEW_URL,
    PATH,
    S_ITEM,
    S_LIST,
    answer_edit_reads,
    answer_profiles,
    answer_ratings,
    as_post,
    fixture,
    posted,
)
from tests.fakes.engine import DOC_URI, FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

PUBLISH = "/catalog/scenarios/{scenario_id}/publish"
GOLD, DOOR, BANK = 0, 1, 2
ALL_MEMBERS = {
    "name": "trust",
    "type": "number",
    "scope": "character",
    "constraints": {
        "min": 0,
        "max": 10,
        "non_negative": True,
        "tolerance": 0.5,
    },
    "initial_value": 5,
    "aliases": ["faith"],
    "unit": "points",
    "entity_id": "bob",
    "visibility": "hidden",
    "owner": "external",
    "provider_id": "crm",
    "external_value_name": "trust_score",
    "silent": False,
    "rules": {"derived": False},
    "authorization": {"sources": ["role:bob"]},
    "transfer": {"mode": "none"},
    "reservation_expiry_minutes": 15,
}
"""A ``ScenarioState`` with every one of its 17 members set (schema-valid shapes)."""


def states_draft(**changes: Any) -> dict[str, Any]:
    return fixture("draft_states", **changes)


def stored_states() -> list[dict[str, Any]]:
    return copy.deepcopy(states_draft()["starting_state"]["states"])


def edit_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    answer_edit_reads(engine, body if body is not None else states_draft())
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


def op(client: Client, data: QueryDict, value: str) -> QueryDict:
    data["op"] = value
    response = client.post(EDIT, as_post(data))
    assert response.status_code == 200
    return posted(response)


def schema_valid(engine: FakeEngine, body: Any, component: str) -> None:
    """The Engine's request schema accepts ``body`` (``additionalProperties: false``): no
    Console-only key (a snapshot, a flattened constraint) leaks into a write."""
    ref = {"$ref": f"{DOC_URI}#/components/schemas/{component}"}
    Draft202012Validator(ref, registry=engine._registry).validate(body)


def changes(engine: FakeEngine) -> dict[str, Any]:
    (sent,) = calls(engine, "PATCH", PATH)
    body: dict[str, Any] = body_of(sent)
    schema_valid(engine, body, "ScenarioPatch")
    # The patch's groups are open objects: each sent State is checked against its component.
    for state in (body.get("starting_state") or {}).get("states") or []:
        schema_valid(engine, state, "ScenarioState")
    body.pop("draft_revision")
    return body


def row_errors(response: Any, index: int) -> dict[str, list[str]]:
    return dict(response.context["editor"].formsets["state"].forms[index].errors)


def added(client: Client, data: QueryDict, **values: str) -> tuple[QueryDict, int]:
    """``data`` with one more States row, filled with ``values``; and its index."""
    data = op(client, data, "add:state")
    index = int(str(data["state-TOTAL_FORMS"])) - 1
    for name, value in values.items():
        data[f"state-{index}-{name}"] = value
    return data, index


def flashes(response: Any) -> list[str]:
    return [str(m) for m in response.wsgi_request._messages]


def editor_for(body: dict[str, Any], data: QueryDict | None = None) -> ScenarioEditor:
    """An editor over ``body`` (the draft read), bound to ``data`` or the draft's own state."""
    body = {**body, "metadata": {**body["metadata"], "content_rating_id": None}}  # no lookups
    draft = Scenario.from_json(body)
    assert draft is not None
    original = draft_to_write(draft)
    return ScenarioEditor(
        data if data is not None else scenario_editor_data(original),
        ratings=RatingChoices([]),
        profiles=ProfileChoices([]),
        create=False,
        original=original,
    )


# --- the fixture ----------------------------------------------------------------------------------


def test_the_fixture_is_schema_valid(engine: FakeEngine) -> None:
    engine.respond("GET", S_ITEM, 200, json=states_draft())  # validated against the schema
    names = [s["name"] for s in stored_states()]
    assert names == ["gold", "door_open", "bank_balance"]
    alice = states_draft()["cast"]["roles"][0]
    assert (alice["id"], alice["state_initial_values"]) == ("alice", {"gold": 250})


# --- nothing lost, nothing spurious (C9) ----------------------------------------------------------


@pytest.mark.parametrize("extra", [None, ALL_MEMBERS])
def test_untouched_states_send_nothing(
    operator_client: Client, engine: FakeEngine, extra: dict[str, Any] | None
) -> None:
    body = states_draft()
    if extra is not None:
        body["starting_state"]["states"].append(copy.deepcopy(extra))
    response = edit_page(operator_client, engine, body)
    html = response.content.decode()
    section = html[html.index('id="f-starting_state-states"') :]
    section = section[: section.index("</fieldset>")]
    assert 'class="errorlist' not in section  # the GET-bound editor shows no local error
    data = posted(response)
    saved = save(operator_client, engine, data.copy())
    assert saved.status_code == 302
    assert flashes(saved) == ["No changes"]
    assert engine.requests == []
    # Complement: none after an Add/Remove re-render either.
    data = op(operator_client, data, "add:state")
    data = op(operator_client, data, "add:state")
    last = int(str(data["state-TOTAL_FORMS"])) - 1
    data = op(operator_client, data, f"remove:state-{last}")
    assert engine.requests == []
    saved = save(operator_client, engine, data)
    assert saved.status_code == 302
    assert calls(engine, "PATCH", PATH) == []


def test_editing_one_state_sends_the_array_and_nothing_else(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data[f"state-{GOLD}-unit"] = "coins"
    save(operator_client, engine, data)
    sent = changes(engine)
    expected = stored_states()
    expected[GOLD]["unit"] = "coins"
    assert sent == {"starting_state": {"states": expected}}
    assert sent["starting_state"]["states"][DOOR] == stored_states()[DOOR]
    assert sent["starting_state"]["states"][BANK] == stored_states()[BANK]
    for key in ("clock", "scheduled_events"):
        assert key not in sent["starting_state"]
    assert "cast" not in sent
    # The row is sent with its keys in schema order.
    assert list(sent["starting_state"]["states"][GOLD]) == [
        "name",
        "type",
        "scope",
        "constraints",
        "initial_value",
        "unit",
    ]


def test_c7_selects_send_nothing_unless_chosen(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine).content.decode()
    assert "Engine default (story)" in html
    assert "Engine default (player visible)" in html
    assert "Engine default (false)" in html
    data = form(operator_client, engine)
    assert data.get(f"state-{DOOR}-scope", "") == ""  # Engine default
    data[f"state-{DOOR}-unit"] = "hinge"
    save(operator_client, engine, data.copy())
    door = changes(engine)["starting_state"]["states"][DOOR]
    assert "scope" not in door
    engine.reset()
    data[f"state-{DOOR}-scope"] = "story"  # an explicit "Story" is sent
    save(operator_client, engine, data)
    assert changes(engine)["starting_state"]["states"][DOOR]["scope"] == "story"


def test_a_stored_value_outside_the_choices_is_kept() -> None:
    body = states_draft()
    # Deliberately off-schema: a stored scope the schema doesn't list, and a kept clock type.
    body["starting_state"]["states"][DOOR]["scope"] = "region"
    body["starting_state"]["states"][DOOR]["type"] = "clock"
    editor = editor_for(body)
    row = editor.formsets["state"].forms[DOOR]
    assert ("region", "Region") in row.fields["scope"].choices
    assert ("clock", "Clock") in row.fields["type"].choices
    assert editor.data[f"state-{DOOR}-initial_value"] == "false"  # compact JSON
    data = editor.data.copy()
    data[f"state-{DOOR}-unit"] = "minutes"
    edited = editor_for(body, data)
    assert edited.is_valid(), [f.errors for f in edited.formsets["state"].forms]
    door = edited.edit_patch(edited.original)["starting_state"]["states"][DOOR]
    assert door["scope"] == "region"  # sent unchanged
    assert door["type"] == "clock"
    assert door["initial_value"] is False  # the stored value, not re-parsed
    # Edited, a kept type's value is read as a strict JSON scalar.
    data[f"state-{DOOR}-initial_value"] = "ajar"
    refused = editor_for(body, data)
    assert not refused.is_valid()
    assert refused.formsets["state"].forms[DOOR].errors["initial_value"] == [
        scenario_states.SCALAR_INVALID
    ]


def test_removing_the_last_state_sends_an_empty_array(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = states_draft()
    body["starting_state"]["states"] = body["starting_state"]["states"][:1]
    data = form(operator_client, engine, body)
    data = op(operator_client, data, "remove:state-0")
    save(operator_client, engine, data)
    assert changes(engine) == {"starting_state": {"states": []}}


def test_a_draft_without_states_sends_no_states_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: a draft without States, saved with no rows, sends no ``states`` key."""
    body = states_draft()
    body["starting_state"]["states"] = []
    data = form(operator_client, engine, body)
    assert data["state-TOTAL_FORMS"] == "0"
    data["public_card__name"] = "Renamed"
    save(operator_client, engine, data)
    sent = changes(engine)
    assert sent == {"public_card": {"name": "Renamed"}}


def test_reordering_sends_the_new_order_and_wakes_no_check(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = states_draft()
    body["starting_state"]["states"][DOOR]["name"] = "gold"  # a stored duplicate
    data = form(operator_client, engine, body)
    data = op(operator_client, data, f"down:state-{GOLD}")
    assert data["state-0-name"] == "gold"
    assert data["state-0-type"] == "boolean"
    response = save(operator_client, engine, data)
    assert response.status_code == 302  # the Engine's report is left to say "duplicate"
    order = [s["type"] for s in changes(engine)["starting_state"]["states"]]
    assert order == ["boolean", "number", "number"]


def test_floats_round_trip() -> None:
    assert number_text(1.0) == "1.0"
    assert number_text(1e20) == "100000000000000000000"
    assert number_text(1e-7) == "0.0000001"
    for value in (1.0, 1e20, 1e-7, 1):
        body = states_draft()
        body["starting_state"]["states"][GOLD]["initial_value"] = value
        editor = editor_for(body)
        data = editor.data.copy()
        data[f"state-{GOLD}-unit"] = "coins"  # the row changes; its value is untouched
        edited = editor_for(body, data)
        assert edited.is_valid(), [f.errors for f in edited.formsets["state"].forms]
        sent = edited.edit_patch(edited.original)["starting_state"]["states"][GOLD]
        assert sent["initial_value"] == value
        assert type(sent["initial_value"]) is type(value)


def test_state_to_write_normalises_as_the_form_does() -> None:
    entry: dict[str, Any] = {
        "unit": " gold ",
        "type": "enum",
        "name": " weather",
        "constraints": {"allowed_values": ["rain", " ", "rain"], "min": None},
        "aliases": [],
        "rules": None,
        "initial_value": 1.0,
    }
    assert state_to_write(entry) == {
        "name": "weather",
        "type": "enum",
        "constraints": {"allowed_values": ["rain", "rain"]},  # duplicates kept
        "initial_value": 1.0,
        "unit": "gold",
    }
    assert state_to_write({"name": "x", "type": "text", "constraints": {}}) == {
        "name": "x",
        "type": "text",
    }


# --- local checks ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "field", "message"),
    [
        ({"type": "number", "initial_value": "12,5"}, "initial_value", NUMBER_INVALID),
        ({"type": "boolean", "initial_value": "maybe"}, "initial_value", states.BOOLEAN_INVALID),
        (
            {"type": "enum", "constraints_allowed_values": "a\nb", "initial_value": "c"},
            "initial_value",
            states.NOT_ALLOWED,
        ),
        (
            {"type": "number", "constraints_min": "5", "constraints_max": "1"},
            "constraints_min",
            states.MIN_ABOVE_MAX,
        ),
        ({"type": "number", "owner": "external"}, "provider_id", states.PROVIDER_REQUIRED),
        (
            {
                "type": "number",
                "owner": "external",
                "provider_id": "bank",
                "rules": '{"derived": true}',
            },
            "rules",
            states.EXTERNAL_RULES,
        ),
        (
            {"type": "number", "external_value_name": "balance"},
            "external_value_name",
            states.PROVIDER_STORY_OWNED,
        ),
        (
            {"type": "number", "owner": "external", "provider_id": "b", "external_value_name": "9"},
            "external_value_name",
            states.STATE_NAME_INVALID,
        ),
    ],
)
def test_the_row_reuses_the_declaration_checks(
    operator_client: Client, engine: FakeEngine, values: dict[str, str], field: str, message: str
) -> None:
    data, index = added(operator_client, form(operator_client, engine), name="fresh", **values)
    response = save(operator_client, engine, data)
    assert response.status_code == 200
    assert row_errors(response, index)[field] == [message]
    assert engine.requests == []  # zero Engine calls


@pytest.mark.parametrize(
    ("text_", "expected"), [("12", 12), ("-3.5", -3.5), ("", None), ("  ", None)]
)
def test_initial_values_parse_by_type(
    operator_client: Client, engine: FakeEngine, text_: str, expected: Any
) -> None:
    data, _ = added(operator_client, form(operator_client, engine), name="score", type="number")
    data["state-3-initial_value"] = text_
    save(operator_client, engine, data)
    (_, _, _, score) = changes(engine)["starting_state"]["states"]
    if expected is None:
        assert "initial_value" not in score  # blank sends none
    else:
        assert score["initial_value"] == expected
        assert type(score["initial_value"]) is type(expected)


def test_state_names_are_checked_locally(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data, nameless = added(operator_client, data, type="number")
    data, clock = added(operator_client, data, name=" world_clock ", type="number")
    data, twice = added(operator_client, data, name="gold", type="number")
    data, other_case = added(operator_client, data, name="Gold", type="number")
    response = save(operator_client, engine, data)
    assert response.status_code == 200
    assert engine.requests == []
    assert row_errors(response, nameless)["name"] == [NAME_NEEDED]
    assert row_errors(response, clock)["name"] == [WORLD_CLOCK_RESERVED]
    assert row_errors(response, twice)["name"] == [NAME_USED.format(row=GOLD + 1)]
    assert row_errors(response, other_case) == {}  # "Gold" and "gold" are not duplicates
    assert WORLD_CLOCK_RESERVED == (
        "`world_clock` is the world clock — set the starting clock in Clock"
    )


def test_a_changed_earlier_row_names_the_untouched_later_one(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data[f"state-{GOLD}-name"] = "bank_balance"
    response = save(operator_client, engine, data)
    assert row_errors(response, GOLD)["name"] == [NAME_USED.format(row=BANK + 1)]
    assert row_errors(response, BANK) == {}
    assert engine.requests == []


def test_an_untouched_bad_entry_never_blocks_another_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = states_draft()
    body["starting_state"]["states"][GOLD]["name"] = "world_clock"  # the Engine reports it
    body["starting_state"]["states"][DOOR]["initial_value"] = "ajar"  # mismatched, kept
    data = form(operator_client, engine, body)
    data["public_card__name"] = "Renamed"
    response = save(operator_client, engine, data)
    assert response.status_code == 302
    assert changes(engine) == {"public_card": {"name": "Renamed"}}


def test_initial_values_box_refuses_nested_values(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = unescape(edit_page(operator_client, engine).content.decode())
    assert INITIAL_VALUES_HELP in html
    for box, name in (('{"gold": {"x": 1}}', "gold"), ('{"mood": "calm", "gold": null}', "gold")):
        data = form(operator_client, engine)
        data["role-0-state_initial_values"] = box
        response = save(operator_client, engine, data)
        assert response.status_code == 200
        role = response.context["editor"].cast.roles.forms[0]
        assert role.errors["state_initial_values"] == [
            INITIAL_VALUE_INVALID.replace("{name}", name)
        ]
        assert engine.requests == []
    data = form(operator_client, engine)
    data["role-0-state_initial_values"] = '{"gold": 250, "flag": true, "mood": "calm"}'
    save(operator_client, engine, data)
    sent = changes(engine)
    assert sent["cast"]["roles"][0]["state_initial_values"] == {
        "gold": 250,
        "flag": True,
        "mood": "calm",
    }


def test_an_untouched_role_box_is_left_to_the_engine(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = states_draft()
    body["cast"]["roles"][1]["state_initial_values"] = {"door_open": True}
    data = form(operator_client, engine, body)
    data["public_card__name"] = "Renamed"
    save(operator_client, engine, data)
    assert changes(engine) == {"public_card": {"name": "Renamed"}}


# --- create ---------------------------------------------------------------------------------------


def new_page(client: Client, engine: FakeEngine) -> Any:
    answer_ratings(engine)
    answer_profiles(engine)
    response = client.get(NEW_URL)
    assert response.status_code == 200
    engine.reset()
    return response


def test_create_sends_states_only_when_filled(operator_client: Client, engine: FakeEngine) -> None:
    data = posted(new_page(operator_client, engine))
    data["public_card__name"] = "New one"
    engine.respond("POST", S_LIST, 201, json=fixture("post"))
    operator_client.post(NEW_URL, as_post(data))
    (sent,) = calls(engine, "POST", S_LIST)
    assert "starting_state" not in body_of(sent)  # no rows: omitted (drop_empty)
    engine.reset()
    data = posted(new_page(operator_client, engine))
    data["op"] = "add:state"
    data = posted(operator_client.post(NEW_URL, as_post(data)))
    data["public_card__name"] = "New one"
    data["state-0-name"] = "gold"
    data["state-0-type"] = "number"
    data["state-0-initial_value"] = "5"
    engine.respond("POST", S_LIST, 201, json=fixture("post"))
    operator_client.post(NEW_URL, as_post(data))
    (sent,) = calls(engine, "POST", S_LIST)
    body = body_of(sent)
    schema_valid(engine, body, "ScenarioCreate")
    assert body["starting_state"] == {
        "states": [{"name": "gold", "type": "number", "initial_value": 5}]
    }


# --- errors land on their rows --------------------------------------------------------------------


def test_state_422_paths_land_on_their_fields(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data[f"state-{GOLD}-unit"] = "coins"
    failures = [
        {"field": "body.starting_state.states[1].constraints.min", "message": "bad min"},
        {"field": "body.starting_state.states[2].authorization.sources[0]", "message": "bad src"},
        {"field": "body.starting_state.states[0]", "message": "bad entry"},
        {"field": "body.starting_state.states[2].aliases[0]", "message": "too long"},
        {"field": "body.starting_state.states[0].unit", "message": "bad unit"},
        {"field": "body.starting_state.states[9].name", "message": "no such row"},
    ]
    body = error("validation_error", "invalid", errors=failures)
    response = save(operator_client, engine, data, status=422, json=body)
    assert response.status_code == 200
    assert row_errors(response, DOOR)["constraints_min"] == ["bad min"]
    assert row_errors(response, BANK)["authorization"] == [
        "starting_state.states[2].authorization.sources[0]: bad src"
    ]
    assert row_errors(response, BANK)["aliases"] == ["Line 1: too long"]
    assert row_errors(response, GOLD)["__all__"] == ["bad entry"]
    assert row_errors(response, GOLD)["unit"] == ["bad unit"]
    # An unmappable index stays a non-field error naming the path.
    assert (
        "starting_state.states[9].name: no such row" in response.context["form"].errors["__all__"]
    )
    html = response.content.decode()
    assert '<details class="state-boxes" open>' in html  # the box with an error opens


def test_state_schema_report_is_placed(operator_client: Client, engine: FakeEngine) -> None:
    refused = fixture("publish_invalid")
    report = refused["error"]["details"]["report"]
    location = dict(report["errors"][0]["location"])
    report["errors"] = [
        {
            **report["errors"][0],
            "code": "state_schema_invalid",
            "location": {**location, "field": "starting_state.states[0].name"},
            "rule": "a State's name appears once",
            "source": "duplicate",
        },
        {
            **report["errors"][0],
            "code": "state_schema_invalid",
            "location": {**location, "field": "cast.roles[1].state_initial_values.door_open"},
            "rule": "only a character-scoped State has per-role values",
            "source": "not_character_scoped",
        },
    ]
    report["unavailable"] = []
    data = form(operator_client, engine)
    data["action"] = "publish"
    engine.respond("POST", PUBLISH, 422, json=refused)
    response = operator_client.post(EDIT, as_post(data))
    row = EngineOperation.objects.get(kind="publish_scenario")
    assert response["Location"] == f"{EDIT}?report={row.pk}"
    page = operator_client.get(response["Location"])
    editor = page.context["editor"]
    assert editor.formsets["state"].forms[0].errors["name"] == [
        "state_schema_invalid: a State's name appears once"
    ]
    role = editor.cast.roles.forms[1]
    assert role.errors["state_initial_values"] == [
        "cast.roles[1].state_initial_values.door_open: state_schema_invalid: only a "
        "character-scoped State has per-role values"
    ]


# --- the section ----------------------------------------------------------------------------------


def test_the_deferred_section_no_longer_has_a_states_box(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    html = response.content.decode()
    assert 'name="starting_state__states"' not in html
    assert STATES_HELP in unescape(html)
    world_seeds = html.index('id="f-starting_state-seeded_memories"')
    section = html.index('id="f-starting_state-states"')
    settings = html.index('id="f-story_settings_defaults"')
    assert world_seeds < section < settings
    (deferred,) = [names for title, _, names in SECTIONS if title == "Rules and declarations"]
    assert list(deferred) == list(DEFERRED_BOXES)
    assert list(DEFERRED_BOXES) == [  # #268 moved the clock and the events out
        "starting_state__predicates",
        "starting_state__closed_sets",
        "starting_state__rules",
    ]
    # Complement: a posted ``starting_state__states`` is ignored.
    data = posted(response)
    data["starting_state__states"] = json.dumps([{"name": "x", "type": "text"}])
    saved = save(operator_client, engine, data)
    assert saved.status_code == 302
    assert engine.requests == []


def test_the_state_enums_match_the_schema() -> None:
    schemas = engine_doc()["components"]["schemas"]

    def values(choices: list[tuple[str, str]]) -> list[str]:
        return [v for v, _ in choices if v]

    assert values(states.TYPE_CHOICES) == [t for t in schemas["StateType"]["enum"] if t != "clock"]
    assert values(states.SCOPE_CHOICES) == schemas["StateScope"]["enum"]
    assert values(states.VISIBILITY_CHOICES) == schemas["StateVisibility"]["enum"]
    assert values(states.OWNER_CHOICES) == schemas["StateOwner"]["enum"]
    form_ = ScenarioStateForm()
    fields = set(form_.fields) - set(SNAPSHOT_FIELDS)
    members = set(schemas["ScenarioState"]["properties"]) - {"constraints"}
    members |= {f"constraints_{m}" for m in schemas["StateConstraints"]["properties"]}
    assert fields == members
    assert list(scenario_states.STATE_MEMBERS) == sorted(
        schemas["ScenarioState"]["properties"], key=list(scenario_states.STATE_MEMBERS).index
    )
    assert set(scenario_states.STATE_MEMBERS) == set(schemas["ScenarioState"]["properties"])


def engine_doc() -> dict[str, Any]:
    from pathlib import Path

    import console.engine

    path = Path(console.engine.__file__).parent / "openapi.json"
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def test_an_archived_scenario_disables_the_states(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, states_draft(status="archived")).content.decode()
    assert 'name="state-0-name" value="gold"' in html
    start = html.index('name="state-0-name"')
    assert "disabled" in html[start : html.index(">", start)]
    assert 'value="add:state" formnovalidate disabled' in html


def test_the_entity_select_offers_the_forms_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = states_draft()
    body["starting_state"]["states"][GOLD]["entity_id"] = "ghost"
    html = edit_page(operator_client, engine, body).content.decode()
    start = html.index('name="state-0-entity_id"')
    select = html[start : html.index("</select>", start)]
    assert '<optgroup label="Roles">' in select
    assert '<optgroup label="Locations">' in select
    assert '<optgroup label="Entities">' in select
    assert "ghost (missing)" in text(select)


def test_no_console_only_key_leaks(operator_client: Client, engine: FakeEngine) -> None:
    """Complement: the request schema refuses any key outside ``ScenarioState``/
    ``StateConstraints``, and an edited row of every member passes it."""
    body = states_draft()
    body["starting_state"]["states"].append(copy.deepcopy(ALL_MEMBERS))
    data = form(operator_client, engine, body)
    data[f"state-{GOLD}-unit"] = "coins"
    data[f"state-{BANK}-constraints_tolerance"] = "0.5"
    save(operator_client, engine, data)
    sent = changes(engine)  # validated against ScenarioPatch
    assert sent["starting_state"]["states"][3] == ALL_MEMBERS  # untouched: as stored
    assert sent["starting_state"]["states"][BANK]["constraints"] == {"tolerance": 0.5}
    leaked = copy.deepcopy(sent["starting_state"]["states"][GOLD])
    leaked["initial_value_raw"] = "100"
    with pytest.raises(Exception, match="Additional properties"):
        schema_valid(engine, leaked, "ScenarioState")
    assert engine_fixture("catalog_scenarios_draft_states")

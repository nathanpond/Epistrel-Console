"""A scenario's starting clock and pre-scheduled events in the scenario editor (#268)."""

import copy
import json
from html import unescape
from pathlib import Path
from typing import Any

import pytest
from django.http import QueryDict
from django.test import Client
from jsonschema import Draft202012Validator

import console.engine
from console.forms import scenario_clock
from console.forms.clock import CELL_INVALID, TIME_INVALID
from console.forms.scenario import DEFERRED_BOXES, SECTIONS, main_field_names
from console.forms.scenario_clock import (
    AT_LEAST_ONE,
    CLOCK_HELP,
    EVENT_NEEDS_NAME,
    EVENTS_HELP,
    PLANNED_REQUIRED,
    ROW_WIDTH_NOTE,
    TIME_OF_DAY_INVALID,
    TOO_MANY_PARTICIPANTS,
    EventForm,
    clock_to_write,
    event_to_write,
)
from console.forms.world import PLANNED_INVALID
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

pytestmark = pytest.mark.django_db

PUBLISH = "/catalog/scenarios/{scenario_id}/publish"
CLOCK = "starting_state__clock"
ANCHOR = f"{CLOCK}__anchor"
DINNER, LETTER, HEIST = 0, 1, 2


def clock_draft(**changes: Any) -> dict[str, Any]:
    return fixture("draft_clock", **changes)


def stored_clock() -> dict[str, Any]:
    return copy.deepcopy(clock_draft()["starting_state"]["clock"])


def stored_events() -> list[dict[str, Any]]:
    return copy.deepcopy(clock_draft()["starting_state"]["scheduled_events"])


def edit_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    answer_edit_reads(engine, body if body is not None else clock_draft())
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
    """The Engine's schema accepts ``body`` (``additionalProperties: false``)."""
    ref = {"$ref": f"{DOC_URI}#/components/schemas/{component}"}
    Draft202012Validator(ref, registry=engine._registry).validate(body)


def check_starting_state(engine: FakeEngine, state: dict[str, Any]) -> None:
    """Complement (C15): what is sent for the clock and the events is valid against
    ``ScenarioClock`` (a merge patch's ``null`` members included) and
    ``ScenarioScheduledEvent``: no Console-only key leaks into a write."""
    if state.get("clock") is not None:
        schema_valid(engine, state["clock"], "ScenarioClock")
    for event in state.get("scheduled_events") or []:
        schema_valid(engine, event, "ScenarioScheduledEvent")


def changes(engine: FakeEngine) -> dict[str, Any]:
    (sent,) = calls(engine, "PATCH", PATH)
    body: dict[str, Any] = body_of(sent)
    schema_valid(engine, body, "ScenarioPatch")
    check_starting_state(engine, body.get("starting_state") or {})
    body.pop("draft_revision")
    return body


def row_errors(response: Any, prefix: str, index: int) -> dict[str, list[str]]:
    return dict(response.context["editor"].formsets[prefix].forms[index].errors)


def form_errors(response: Any, name: str) -> list[str]:
    return list(response.context["form"].errors.get(name, []))


def added(client: Client, data: QueryDict, prefix: str, **values: Any) -> tuple[QueryDict, int]:
    """``data`` with one more ``prefix`` row, filled with ``values``; and its index."""
    data = op(client, data, f"add:{prefix}")
    index = int(str(data[f"{prefix}-TOTAL_FORMS"])) - 1
    for name, value in values.items():
        key = f"{prefix}-{index}-{name}"
        if isinstance(value, list):
            data.setlist(key, value)
        else:
            data[key] = value
    return data, index


def flashes(response: Any) -> list[str]:
    return [str(m) for m in response.wsgi_request._messages]


def clock_section(html: str) -> str:
    start = html.index('id="f-starting_state-clock"')
    return html[start : html.index('id="f-deferred"')]


def engine_doc() -> dict[str, Any]:
    path = Path(console.engine.__file__).parent / "openapi.json"
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


# --- the fixture ----------------------------------------------------------------------------------


def test_the_fixture_is_schema_valid(engine: FakeEngine) -> None:
    engine.respond("GET", S_ITEM, 200, json=clock_draft())  # validated against the schema
    clock = stored_clock()
    assert set(clock) == set(scenario_clock.CLOCK_MEMBERS)
    assert set(clock["anchor"]) == set(scenario_clock.ANCHOR_MEMBERS)
    assert [e["planned"] for e in stored_events()] == ["18:00", 130, "02:10"]
    assert stored_events()[DINNER]["participants"] == ["bob", "alice"]  # unlike the role rows


# --- nothing lost, nothing spurious (C9) ----------------------------------------------------------


def test_untouched_clock_and_events_send_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    assert 'class="errorlist' not in clock_section(response.content.decode())
    data = posted(response)
    saved = save(operator_client, engine, data.copy())
    assert saved.status_code == 302
    assert flashes(saved) == ["No changes"]
    assert engine.requests == []
    # Complement: none after an Add/Remove on each row set either.
    for prefix in ("pacing", "bands", "evt"):
        data = op(operator_client, data, f"add:{prefix}")
        last = int(str(data[f"{prefix}-TOTAL_FORMS"])) - 1
        data = op(operator_client, data, f"remove:{prefix}-{last}")
    data = op(operator_client, data, "add:evt")  # a blank row is dropped
    assert engine.requests == []
    saved = save(operator_client, engine, data)
    assert saved.status_code == 302
    assert calls(engine, "PATCH", PATH) == []


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        ({f"{CLOCK}__mode": "paced"}, {"mode": "paced"}),
        ({f"{CLOCK}__time_awareness": "exact"}, {"time_awareness": "exact"}),
        ({f"{ANCHOR}__time": ""}, {"anchor": {"time": None}}),
        ("remove:pacing-0", {"pacing_table": [[50, None, 15]]}),
        (["remove:bands-1", "remove:bands-0"], {"bands": None}),
        ({f"{CLOCK}__calendar": ""}, {"calendar": None}),
        ({f"{CLOCK}__max_unprompted_skip_minutes": "2000"}, {"max_unprompted_skip_minutes": 2000}),
        ({f"{CLOCK}__stop_at_due_events": ""}, {"stop_at_due_events": None}),
    ],
)
def test_clock_edits_send_member_level_merge_patches(
    operator_client: Client, engine: FakeEngine, edit: Any, expected: dict[str, Any]
) -> None:
    data = form(operator_client, engine)
    if isinstance(edit, dict):
        data.update(edit)
    else:
        for value in [edit] if isinstance(edit, str) else edit:
            data = op(operator_client, data, value)
    save(operator_client, engine, data)
    # Complement: only that member; no states, scheduled events or other clock member.
    assert changes(engine) == {"starting_state": {"clock": expected}}


def test_clearing_the_anchor_sends_anchor_null(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    for part in scenario_clock.ANCHOR_MEMBERS:
        data[f"{ANCHOR}__{part}"] = ""
    save(operator_client, engine, data)
    assert changes(engine) == {"starting_state": {"clock": {"anchor": None}}}
    # Complement: there is no "No anchor" control, and a draft with no anchor sends nothing.
    assert not any("unanchored" in name for name in main_field_names())
    engine.reset()
    body = clock_draft()
    del body["starting_state"]["clock"]["anchor"]
    data = form(operator_client, engine, body)
    assert all(not data.get(f"{ANCHOR}__{part}") for part in scenario_clock.ANCHOR_MEMBERS)
    saved = save(operator_client, engine, data)
    assert flashes(saved) == ["No changes"]
    assert calls(engine, "PATCH", PATH) == []


def test_an_empty_clock_sends_clock_null_only_when_the_draft_had_one(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = clock_draft()
    body["starting_state"]["clock"] = {"mode": "manual"}
    data = form(operator_client, engine, body)
    data[f"{CLOCK}__mode"] = ""
    save(operator_client, engine, data)
    assert changes(engine) == {"starting_state": {"clock": None}}
    engine.reset()
    body["starting_state"]["clock"] = {}  # a stored empty clock reads as none: nothing sent
    data = form(operator_client, engine, body)
    saved = save(operator_client, engine, data)
    assert flashes(saved) == ["No changes"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (f"{ANCHOR}__time", "24:00", TIME_OF_DAY_INVALID),
        (f"{ANCHOR}__time", "7:3", TIME_OF_DAY_INVALID),
        (f"{ANCHOR}__time", "6:00", TIME_OF_DAY_INVALID),  # never padded
        (f"{ANCHOR}__month", "0", AT_LEAST_ONE),
        (f"{ANCHOR}__day", "x", scenario_clock.WHOLE_NUMBER),
        (f"{ANCHOR}__year", "2147483648", "At most 2,147,483,647."),
        (f"{ANCHOR}__weekday", "x" * 61, "At most 60 characters"),
        ("pacing-0-minutes", "x", CELL_INVALID),
        ("bands-0-to", "25:00", TIME_INVALID),
    ],
)
def test_anchor_and_row_values_are_checked_locally(
    operator_client: Client, engine: FakeEngine, field: str, value: str, message: str
) -> None:
    data = form(operator_client, engine)
    data[field] = value
    response = save(operator_client, engine, data)
    assert response.status_code == 200
    if field.startswith(("pacing", "bands")):
        prefix, index, name = field.split("-")
        assert row_errors(response, prefix, int(index))[name] == [message]
    else:
        assert form_errors(response, field) == [message]
    assert engine.requests == []  # zero Engine calls


def test_a_negative_year_is_accepted(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data[f"{ANCHOR}__year"] = "-44"
    save(operator_client, engine, data)
    assert changes(engine) == {"starting_state": {"clock": {"anchor": {"year": -44}}}}


def test_a_row_not_three_cells_wide_is_noted_and_never_resent(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = clock_draft()
    body["starting_state"]["clock"]["pacing_table"][0] = [0, 50, 5, 9]
    response = edit_page(operator_client, engine, body)
    assert ROW_WIDTH_NOTE.format(n=4) in response.content.decode()
    data = posted(response)
    assert data["pacing-0-cells"] == "4"
    data[f"{CLOCK}__mode"] = "paced"
    save(operator_client, engine, data.copy())
    assert changes(engine) == {"starting_state": {"clock": {"mode": "paced"}}}
    # Edited, the row is sent 3 cells wide.
    engine.reset()
    data["pacing-0-minutes"] = "6"
    save(operator_client, engine, data)
    sent = changes(engine)["starting_state"]["clock"]
    assert sent["pacing_table"] == [[0, 50, 6], [50, None, 15]]


# --- the scheduled events -------------------------------------------------------------------------


def test_editing_an_event_sends_only_the_events(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data[f"evt-{DINNER}-place"] = "barn"
    save(operator_client, engine, data)
    expected = stored_events()
    expected[DINNER]["place"] = "barn"
    sent = changes(engine)
    assert sent == {"starting_state": {"scheduled_events": expected}}
    assert list(sent["starting_state"]["scheduled_events"][DINNER]) == [
        "proposition",
        "planned",
        "participants",
        "place",
        "owner",
    ]


def test_planned_keeps_its_json_type(operator_client: Client, engine: FakeEngine) -> None:
    data = form(operator_client, engine)
    data, minutes = added(operator_client, data, "evt", proposition="harvest", planned="130")
    data, timed = added(operator_client, data, "evt", proposition="supper", planned="18:00")
    save(operator_client, engine, data)
    events = changes(engine)["starting_state"]["scheduled_events"]
    assert events[minutes]["planned"] == 130
    assert type(events[minutes]["planned"]) is int
    assert events[timed]["planned"] == "18:00"
    assert events[HEIST] == stored_events()[HEIST]  # a stored "02:10" untouched is unchanged
    assert events[HEIST]["planned"] == "02:10"


@pytest.mark.parametrize(
    ("values", "field", "message"),
    [
        ({"proposition": "the dance", "planned": "7:3"}, "planned", PLANNED_INVALID),
        ({"proposition": "the dance"}, "planned", PLANNED_REQUIRED),
        ({"planned": "130"}, "proposition", EVENT_NEEDS_NAME),
        ({"owner": "world"}, "proposition", EVENT_NEEDS_NAME),
        ({"proposition": "x" * 201, "planned": "1"}, "proposition", "At most 200 characters"),
        (
            {"proposition": "the dance", "planned": "1", "audience_other": "self"},
            None,
            None,
        ),
    ],
)
def test_event_rows_are_checked_locally(
    operator_client: Client,
    engine: FakeEngine,
    values: dict[str, str],
    field: str | None,
    message: str | None,
) -> None:
    data, index = added(operator_client, form(operator_client, engine), "evt", **values)
    response = save(operator_client, engine, data)
    if field is None:
        assert response.status_code == 302
        event = changes(engine)["starting_state"]["scheduled_events"][index]
        assert event == {"proposition": "the dance", "planned": 1, "audience": ["self"]}
        return
    assert response.status_code == 200
    assert row_errors(response, "evt", index)[field] == [message]
    assert engine.requests == []


def test_too_many_participants_is_refused() -> None:
    roles = [f"r{n}" for n in range(33)]
    data = QueryDict(mutable=True)
    data["evt-0-proposition"] = "the crowd"
    data["evt-0-planned"] = "5"
    data.setlist("evt-0-participants", roles)
    row = EventForm(data, prefix="evt-0", roles=roles)
    assert not row.is_valid()
    assert row.errors["participants"] == [TOO_MANY_PARTICIPANTS]


def test_participants_and_audience_keep_the_stored_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    assert data.getlist(f"evt-{DINNER}-participants") == ["bob", "alice"]
    data.setlist(f"evt-{DINNER}-participants", ["alice", "bob"])  # posted in role-row order
    data.setlist(f"evt-{LETTER}-audience", ["family", "alice"])
    saved = save(operator_client, engine, data.copy())
    assert flashes(saved) == ["No changes"]
    assert calls(engine, "PATCH", PATH) == []
    data.setlist(f"evt-{DINNER}-participants", ["alice", "bob", "carol"])
    save(operator_client, engine, data)
    events = changes(engine)["starting_state"]["scheduled_events"]
    assert events[DINNER]["participants"] == ["bob", "alice", "carol"]
    assert events[LETTER]["audience"] == ["alice", "family"]


def test_audience_offers_roles_groups_and_world(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine).content.decode()
    start = html.index(f'id="evt-{LETTER}"')
    row = text(html[start : html.index("</fieldset>", start)])
    for label in ("alice", "bob", "carol", "family (group)", "world"):
        assert label in row
    assert "Engine default (world)" in row


def test_event_choices_keep_missing_ids(operator_client: Client, engine: FakeEngine) -> None:
    body = clock_draft()
    body["starting_state"]["scheduled_events"][DINNER]["place"] = "cellar"
    body["starting_state"]["scheduled_events"][HEIST]["subject"] = "dave"
    response = edit_page(operator_client, engine, body)
    page = text(response.content.decode())
    assert "cellar (missing)" in page
    assert "dave (missing)" in page
    data = posted(response)
    data[f"evt-{LETTER}-disclosure"] = "guarded"
    save(operator_client, engine, data)
    events = changes(engine)["starting_state"]["scheduled_events"]
    assert events[DINNER]["place"] == "cellar"  # sent unchanged
    assert events[HEIST]["subject"] == "dave"


def test_a_removed_role_stays_ticked_as_missing(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data = op(operator_client, data, "remove:role-2")  # carol's row
    data.setlist(f"evt-{DINNER}-participants", ["bob", "alice", "carol"])
    html = operator_client.post(EDIT, as_post({**dict(data.lists()), "op": "refresh"}))
    assert "carol (missing)" in text(html.content.decode())


def test_removing_the_last_event_sends_an_empty_array(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    for index in (2, 1, 0):
        data = op(operator_client, data, f"remove:evt-{index}")
    save(operator_client, engine, data)
    assert changes(engine) == {"starting_state": {"scheduled_events": []}}
    # Complement: a draft without events, saved with no rows, sends no scheduled_events.
    engine.reset()
    body = clock_draft()
    body["starting_state"]["scheduled_events"] = []
    data = form(operator_client, engine, body)
    assert data["evt-TOTAL_FORMS"] == "0"
    data[f"{CLOCK}__mode"] = "paced"
    save(operator_client, engine, data)
    assert "scheduled_events" not in changes(engine)["starting_state"]


def test_normalising_is_the_same_on_both_sides() -> None:
    assert clock_to_write({}) is None
    assert clock_to_write({"anchor": {}, "bands": []}) is None
    assert clock_to_write({"anchor": {"weekday": " Sat "}, "pacing_table": [[0, None, 5]]}) == {
        "anchor": {"weekday": "Sat"},
        "pacing_table": [[0, None, 5]],
    }
    assert event_to_write(
        {"planned": 5, "proposition": " x ", "participants": [], "audience": None}
    ) == {"proposition": "x", "planned": 5}


# --- create ---------------------------------------------------------------------------------------


def new_page(client: Client, engine: FakeEngine) -> Any:
    answer_ratings(engine)
    answer_profiles(engine)
    response = client.get(NEW_URL)
    assert response.status_code == 200
    engine.reset()
    return response


def test_create_sends_clock_and_events_only_when_filled(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(new_page(operator_client, engine))
    data["public_card__name"] = "New one"
    engine.respond("POST", S_LIST, 201, json=fixture("post"))
    operator_client.post(NEW_URL, as_post(data))
    (sent,) = calls(engine, "POST", S_LIST)
    assert "starting_state" not in body_of(sent)  # Complement: an empty Clock sends no clock
    engine.reset()
    data = posted(new_page(operator_client, engine))
    data["op"] = "add:evt"
    data = posted(operator_client.post(NEW_URL, as_post(data)))
    data["op"] = "add:pacing"
    data = posted(operator_client.post(NEW_URL, as_post(data)))
    data["public_card__name"] = "New one"
    data[f"{CLOCK}__mode"] = "manual"
    data[f"{ANCHOR}__time"] = "06:00"
    data.update({"pacing-0-from_words": "0", "pacing-0-minutes": "5"})
    data.update({"evt-0-proposition": "the dinner", "evt-0-planned": "18:00"})
    engine.respond("POST", S_LIST, 201, json=fixture("post"))
    operator_client.post(NEW_URL, as_post(data))
    (sent,) = calls(engine, "POST", S_LIST)
    body = body_of(sent)
    schema_valid(engine, body, "ScenarioCreate")
    check_starting_state(engine, body["starting_state"])
    assert body["starting_state"] == {
        "clock": {"anchor": {"time": "06:00"}, "mode": "manual", "pacing_table": [[0, None, 5]]},
        "scheduled_events": [{"proposition": "the dinner", "planned": "18:00"}],
    }


# --- errors land where they belong ----------------------------------------------------------------


def test_clock_and_event_422_paths_land_on_their_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(operator_client, engine)
    data[f"{CLOCK}__mode"] = "paced"
    failures = [
        {"field": "body.starting_state.clock.anchor.month", "message": "bad month"},
        {"field": "body.starting_state.clock.anchor", "message": "bad anchor"},
        {"field": "body.starting_state.clock.pacing_table[1]", "message": "bad row"},
        {"field": "body.starting_state.clock.pacing_table[0][2]", "message": "bad cell"},
        {"field": "body.starting_state.clock.bands", "message": "bad bands"},
        {"field": "body.starting_state.clock.calendar.months[0]", "message": "bad month name"},
        {"field": "body.starting_state.clock.time_awareness", "message": "bad awareness"},
        {"field": "body.starting_state.clock", "message": "bad clock"},
        {"field": "body.starting_state.scheduled_events[1].participants[0]", "message": "x"},
        {"field": "body.starting_state.scheduled_events[0].participants[1]", "message": "y"},
        {"field": "body.starting_state.scheduled_events[2].planned.int", "message": "bad int"},
        {"field": "body.starting_state.scheduled_events[0].place", "message": "bad place"},
        {"field": "body.starting_state.scheduled_events", "message": "too many"},
        {"field": "body.starting_state.scheduled_events[7].place", "message": "no such row"},
    ]
    body = error("validation_error", "invalid", errors=failures)
    response = save(operator_client, engine, data, status=422, json=body)
    assert response.status_code == 200
    editor = response.context["editor"]
    assert form_errors(response, f"{ANCHOR}__month") == ["bad month"]
    assert list(editor.section_errors["starting_state.clock.anchor"]) == ["bad anchor"]
    assert list(editor.section_errors["starting_state.clock"]) == [
        "starting_state.clock: bad clock"
    ]
    assert row_errors(response, "pacing", 1)["__all__"] == ["bad row"]
    assert row_errors(response, "pacing", 0)["minutes"] == ["bad cell"]
    assert list(editor.formsets["bands"].non_form_errors()) == ["bad bands"]
    assert form_errors(response, f"{CLOCK}__calendar") == [
        "starting_state.clock.calendar.months[0]: bad month name"
    ]
    assert form_errors(response, f"{CLOCK}__time_awareness") == ["bad awareness"]
    # LETTER has no participants: the path itself is named.
    assert row_errors(response, "evt", LETTER)["participants"] == [
        "starting_state.scheduled_events[1].participants[0]: x"
    ]
    assert row_errors(response, "evt", DINNER)["participants"] == ["alice: y"]  # prefixed
    assert row_errors(response, "evt", DINNER)["place"] == ["bad place"]
    assert row_errors(response, "evt", HEIST)["planned"] == [
        "starting_state.scheduled_events[2].planned.int: bad int"
    ]
    assert list(editor.formsets["evt"].non_form_errors()) == [
        "starting_state.scheduled_events: too many"
    ]
    # An unmappable index stays a non-field error naming the path.
    assert "starting_state.scheduled_events[7].place: no such row" in form_errors(
        response, "__all__"
    )
    section = clock_section(response.content.decode())
    assert "bad anchor" in section
    assert "bad clock" in section


def test_state_schema_report_places_clock_and_event_entries(
    operator_client: Client, engine: FakeEngine
) -> None:
    refused = fixture("publish_invalid")
    report = refused["error"]["details"]["report"]
    location = dict(report["errors"][0]["location"])
    entry = {**report["errors"][0], "code": "state_schema_invalid"}
    report["errors"] = [
        {
            **entry,
            "location": {**location, "field": "starting_state.scheduled_events[2].place"},
            "rule": "the place is a world location",
            "source": "unknown_place",
        },
        {
            **entry,
            "location": {**location, "field": "starting_state.clock.anchor"},
            "rule": "the anchor's calendar has that month",
            "source": "anchor",
        },
        {
            **entry,
            "location": {**location, "field": "starting_state.scheduled_events[0].owner"},
            "rule": "a role of the cast",
            "source": "unknown_role",
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
    events = editor.formsets["evt"].forms
    assert events[HEIST].errors["place"] == ["state_schema_invalid: the place is a world location"]
    assert events[DINNER].errors["owner"] == ["state_schema_invalid: a role of the cast"]
    assert list(editor.section_errors["starting_state.clock.anchor"]) == [
        "state_schema_invalid: the anchor's calendar has that month"
    ]
    assert "the anchor&#x27;s calendar has that month" in clock_section(page.content.decode())


# --- the section ----------------------------------------------------------------------------------


def test_the_clock_enums_match_the_schema() -> None:
    schemas = engine_doc()["components"]["schemas"]

    def values(choices: list[tuple[str, str]]) -> list[str]:
        return [v for v, _ in choices if v]

    def enum(component: str, member: str) -> list[str]:
        (option,) = [o for o in schemas[component]["properties"][member]["anyOf"] if "enum" in o]
        return list(option["enum"])

    assert values(scenario_clock.MODE_CHOICES) == enum("ScenarioClock", "mode")
    assert (
        values(scenario_clock.C7_CHOICES[f"{CLOCK}__time_awareness"])
        == (schemas["TimeAwareness"]["enum"])
    )
    assert values(scenario_clock.DISCLOSURE_CHOICES) == enum("ScenarioScheduledEvent", "disclosure")
    clock_fields = {n.removeprefix(f"{CLOCK}__") for n in scenario_clock.CLOCK_FIELDS}
    anchor = {n.removeprefix("anchor__") for n in clock_fields if n.startswith("anchor__")}
    assert anchor == set(schemas["ScenarioAnchor"]["properties"])
    tables = {"pacing_table", "bands"}  # the row sets
    members = {n for n in clock_fields if not n.startswith("anchor__")} | tables | {"anchor"}
    assert members == set(schemas["ScenarioClock"]["properties"])
    assert set(scenario_clock.CLOCK_MEMBERS) == set(schemas["ScenarioClock"]["properties"])
    event = set(EventForm().fields) - {"audience_other"}
    assert event == set(schemas["ScenarioScheduledEvent"]["properties"])
    assert set(scenario_clock.EVENT_MEMBERS) == event


def test_deferred_is_now_rules_and_declarations(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    html = unescape(response.content.decode())
    assert list(DEFERRED_BOXES) == [
        "starting_state__predicates",
        "starting_state__closed_sets",
        "starting_state__rules",
    ]
    (names,) = [n for title, _, n in SECTIONS if title == "Rules and declarations"]
    assert list(names) == list(DEFERRED_BOXES)
    deferred = html[html.index('id="f-deferred"') :]
    deferred = deferred[: deferred.index("</fieldset>")]
    assert "<legend>Rules and declarations</legend>" in deferred
    assert "Each box must be valid JSON before anything is sent." in deferred
    assert "later Engine milestone" not in html
    assert deferred.count("<textarea") == 3
    # The Clock section sits after the States.
    assert html.index('id="f-starting_state-states"') < html.index('id="f-starting_state-clock"')
    assert CLOCK_HELP in html
    assert EVENTS_HELP in html
    assert scenario_clock.ANCHOR_HELP in html
    # Complement: posted keys with the old box names send nothing.
    data = posted(response)
    data["starting_state__clock"] = json.dumps({"mode": "paced"})
    data["starting_state__scheduled_events"] = json.dumps([{"proposition": "x", "planned": 1}])
    saved = save(operator_client, engine, data)
    assert saved.status_code == 302
    assert engine.requests == []


def test_an_archived_scenario_disables_the_clock_and_events(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, clock_draft(status="archived")).content.decode()
    section = clock_section(html)
    for name in (f"{ANCHOR}__year", f"{CLOCK}__mode", "pacing-0-minutes", "evt-0-proposition"):
        start = section.index(f'name="{name}"')
        assert "disabled" in section[start : section.index(">", start)], name
    for prefix in ("pacing", "bands", "evt"):
        assert f'value="add:{prefix}" formnovalidate disabled' in section


def test_the_schema_refuses_a_console_only_key(engine: FakeEngine) -> None:
    """Complement: the schema-validating fake rejects a key outside the three components."""
    for component, body in (
        ("ScenarioClock", {**stored_clock(), "cells": 4}),
        ("ScenarioAnchor", {"time": "06:00", "unanchored": True}),
        ("ScenarioScheduledEvent", {**stored_events()[DINNER], "audience_other": "self"}),
    ):
        with pytest.raises(Exception, match="Additional properties"):
            schema_valid(engine, body, component)

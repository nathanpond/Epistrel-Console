"""#235: D4\u2013D9 and D18 through ``console/world.py``: State values, clock values, positions and
boundaries, the viewpoint line, slot-write results and the refusal table."""

from dataclasses import dataclass
from typing import Any

import pytest
from django.test import override_settings
from django.urls import reverse

from console.engine.client import EngineError, EngineUnreachable
from console.engine.models import (
    ClockValue,
    NarrativePosition,
    ScheduledEventWritten,
    SettledNow,
    StateWritten,
)
from console.inspect import history_turn_url, unavailable_for
from console.positions import knowledge_position_text
from console.wording import UNKNOWN_CHARACTER, write_refusal
from console.world import (
    CONFIG_REVISION_TEXT,
    ENGINE_NOTHING_CHANGED,
    EXPECTED_REVISION_FIELD,
    PLACE_UNKNOWN,
    READONLY_EXTERNAL_TEXT,
    SLOTS_NOT_LISTED,
    STATE_NOT_FOUND,
    STATES_NOTE,
    WorldRefusal,
    boundary_text,
    change_text,
    clock_change_text,
    clock_value_html,
    config_revision_text,
    expected_revision,
    five_part,
    nothing_changed,
    offset_text,
    settle_reason_text,
    signed_delta_text,
    slot_result,
    slot_turn_text,
    state_value_text,
    state_values,
    viewpoint_line,
    world_refusal,
)
from tests.fixtures import engine_fixture

STORY = "01JM6ST0000000000000000001"
MARA, ALICE, BOB = (f"01JM6CH000000000000000000{n}" for n in (1, 2, 3))
NAMES = {MARA: "Mara (player)", ALICE: "Alice", BOB: "Bob"}
CAST = [MARA, ALICE, BOB]
EVENTS_OMNISCIENT = "every canon event"
EVENTS_CHARACTER = "only events {label} takes part in, and {label}'s own belief-only schedules"


# --- D5 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "kind", "unit", "text"),
    [
        (120, "number", "gold", "120 gold"),
        (1200.5, "number", None, "1,200.5"),
        (1200, "number", "gold", "1,200 gold"),
        (True, "boolean", None, "true"),
        (False, "boolean", None, "false"),
        ("open", "enum", None, "open"),
        ("Harbour gate_open", "text", None, "Harbour gate_open"),
        (None, "number", "gold", "undefined"),
        # Complement: a value that doesn't fit its type, or an unknown type, is compact JSON.
        (True, "number", "gold", "true"),
        ({"a": 1}, "text", None, '{"a": 1}'),
        (130, "clock", None, "130"),
        (["é"], "enum", None, '["é"]'),
    ],
)
def test_state_value_text(value: Any, kind: str, unit: str | None, text: str) -> None:
    assert state_value_text(value, kind, unit) == text


def test_state_value_text_infers_a_missing_type() -> None:
    assert state_value_text(False, None) == "false"
    assert state_value_text(5, None, "gold") == "5 gold"
    assert state_value_text("shut", None) == "shut"
    assert state_value_text([1, 2], None) == "[1, 2]"


def test_state_values_cast_order_then_unknown_ids() -> None:
    stranger = "01JM6CH0000000000000000099"
    values = {stranger: 1, BOB: 5, MARA: None}
    rows = state_values(values, NAMES, CAST, type="number", unit="trust")
    assert rows == [
        ("Mara (player)", "undefined"),
        ("Bob", "5 trust"),
        ("…00000099", "1 trust"),  # C4's short id, last
    ]


def test_state_values_null_and_non_object() -> None:
    assert state_values(None, NAMES, CAST) == [("—", "undefined")]
    assert state_values([1], NAMES, CAST) == [("—", "[1]")]


def test_signed_delta_text() -> None:
    minus = signed_delta_text(-3, "gold")
    assert minus == "\u22123 gold"
    assert minus[0] == "\u2212"
    assert ord(minus[0]) == 0x2212
    assert signed_delta_text(5) == "+5"
    assert signed_delta_text(0) == "+0"
    assert signed_delta_text(0.0) == "+0.0"
    assert signed_delta_text(1200.5, "gold") == "+1,200.5 gold"
    assert signed_delta_text("x") == '"x"'


def test_change_text() -> None:
    assert change_text(120, 125, "number", "gold") == "120 gold → 125 gold"
    assert change_text(None, "open", None) == "undefined → open"


# --- D6 ---------------------------------------------------------------------------------------


def _engine_display(offset_minutes: int) -> str:
    """The Engine's ``world_clock.display`` at origin/main, copied as the reference."""
    hours, minutes = divmod(int(offset_minutes), 60)
    return f"+{hours:02d}h {minutes:02d}m"


@pytest.mark.parametrize(("minutes", "text"), [(0, "+00h 00m"), (130, "+02h 10m"),
                                               (3000, "+50h 00m")])  # fmt: skip
def test_offset_text(minutes: int, text: str) -> None:
    assert offset_text(minutes) == text


@pytest.mark.parametrize("minutes", [0, 59, 60, 1439, 1440, 3000, 100_000, -5])
def test_offset_text_is_the_engine_formatter(minutes: int) -> None:
    # Complement: hours are never wrapped into days; byte for byte the Engine's rule.
    assert offset_text(minutes) == _engine_display(minutes)


def test_offset_text_of_a_non_number() -> None:
    assert offset_text(None) == "—"
    assert offset_text(True) == "—"


def test_clock_change_text() -> None:
    before = ClockValue(offset=130, display="+02h 10m")
    after = ClockValue(offset=160, display="+02h 40m")
    assert clock_change_text(before, after) == "+02h 10m → +02h 40m"
    # "(unchanged)" only when the offsets are equal, even if the displays differ.
    same = ClockValue(offset=130, display="+02h 10m (morning)")
    assert clock_change_text(before, same) == "+02h 10m (morning) (unchanged)"
    differ = ClockValue(offset=131, display="+02h 10m")
    assert "(unchanged)" not in clock_change_text(before, differ)
    assert clock_change_text(None, after) == "— → +02h 40m"


def test_clock_value_html() -> None:
    html = clock_value_html(ClockValue(offset=3000, display="+50h 00m <b>"))
    assert html == '<span class="clock" title="offset 3000 min">+50h 00m &lt;b&gt;</span>'
    assert clock_value_html(None) == "—"


# --- D7 ---------------------------------------------------------------------------------------


def test_five_part() -> None:
    position = five_part({"turn": 3, "variant": 0, "slot_k": 1, "beat": 0, "step": 0})
    assert position == NarrativePosition(turn=3, variant=0, slot_k=1, beat=0, step=0)
    assert knowledge_position_text(position) == "turn 3⁺¹ · start"
    # An anchor's four-part ``anchored_at``: the same position, step 0.
    assert five_part({"turn": 3, "variant": 0, "slot_k": 1, "beat": 0}) == position


@pytest.mark.parametrize(
    "mapping",
    [
        {"turn": 3, "variant": 0, "slot_k": 1, "step": 0},  # Complement: no beat
        {"turn": 3, "variant": 0, "slot_k": 1, "beat": True},
        {"turn": "3", "variant": 0, "slot_k": 1, "beat": 0},
        None,
        [3, 0, 1, 0, 0],
    ],
)
def test_five_part_malformed(mapping: Any) -> None:
    assert five_part(mapping) is None


def test_five_part_ignores_extra_keys() -> None:
    assert five_part({"turn": 1, "variant": 0, "slot_k": 0, "beat": 2, "x": 9}) is not None


@pytest.mark.parametrize(
    ("as_of", "text"),
    [
        (None, "as the story stands"),
        ("12", "through turn 12"),
        ("12+1", "through turn 12⁺¹"),
        ("12+0", "through turn 12"),
        ("soon", "through soon"),
    ],
)
def test_boundary_text(as_of: str | None, text: str) -> None:
    assert boundary_text(as_of) == text


def test_slot_turn_text() -> None:
    assert slot_turn_text(3, 2) == "turn 3⁺²"
    assert slot_turn_text(12, 13) == "turn 12⁺¹³"
    # Complement: k 0 and None are the turn itself.
    assert slot_turn_text(3, 0) == "turn 3"
    assert slot_turn_text(3, None) == "turn 3"


# --- D4 and D18 ------------------------------------------------------------------------------


def test_viewpoint_line() -> None:
    assert viewpoint_line("omniscient", NAMES) == "Viewpoint: Omniscient"
    alice = viewpoint_line(f"character:{ALICE}", NAMES)
    assert alice == "Viewpoint: Alice — hidden States are left out"
    assert STATES_NOTE == "hidden States are left out"
    omniscient = viewpoint_line("omniscient", NAMES, omniscient_note=EVENTS_OMNISCIENT)
    assert omniscient == "Viewpoint: Omniscient — every canon event"
    events = viewpoint_line(f"character:{ALICE}", NAMES, note=EVENTS_CHARACTER)
    assert events == (
        "Viewpoint: Alice — only events Alice takes part in, and Alice's own belief-only schedules"
    )
    player = viewpoint_line(f"character:{MARA}", NAMES)
    assert player == "Viewpoint: Mara (player) — hidden States are left out"
    assert viewpoint_line("story", NAMES) == "Viewpoint: story — hidden States are left out"


def test_slots_not_listed_sentence() -> None:
    assert SLOTS_NOT_LISTED == (
        "History doesn't list inter-turn slots; a State's \"since\" position or a write's "
        "result names one"
    )


# --- D8 ---------------------------------------------------------------------------------------


def test_revision_texts_and_nothing_changed() -> None:
    assert config_revision_text(13) == "Configuration revision now 13"
    assert config_revision_text(1200) == "Configuration revision now 1200"
    assert CONFIG_REVISION_TEXT == "Configuration revision now {n}"
    assert ENGINE_NOTHING_CHANGED == "The Engine found nothing to change"
    assert nothing_changed(12, 12) is True
    # Complement.
    assert nothing_changed(13, 12) is False
    assert nothing_changed(12, None) is False
    assert nothing_changed(True, 1) is False


@pytest.mark.parametrize(
    ("posted", "revision"),
    [("12", 12), (" 012 ", 12), ("", None), ("x", None), ("-1", None), ("١٢", None)],
)
def test_expected_revision(posted: str, revision: int | None) -> None:
    assert EXPECTED_REVISION_FIELD == "expected_revision"
    assert expected_revision({EXPECTED_REVISION_FIELD: posted}) == revision


def test_expected_revision_missing() -> None:
    assert expected_revision({}) is None


@dataclass(frozen=True)
class Row:
    pk: Any
    engine_operation_id: str


def test_slot_result_over_a_state_write() -> None:
    answer = StateWritten.from_json(engine_fixture("m6/state_written"))
    assert answer is not None
    result = slot_result(STORY, answer, Row(7, "01JM6QP0000000000000000011"), "/back/")
    assert result.slot_text == "turn 3⁺² · start"
    assert result.slot_url == history_turn_url(STORY, 3)
    assert result.revision_text == f"Story revision now {answer.timeline_revision}"
    assert result.operation_id == answer.operation_id
    assert result.operation_pk == 7
    assert result.engine_position == answer.position
    assert result.back_url == "/back/"


def test_slot_result_without_a_slot() -> None:
    answer = ScheduledEventWritten.from_json(engine_fixture("m6/scheduled_event_written"))
    assert answer is not None
    result = slot_result(STORY, answer, None, "/back/")
    assert result.slot_text is None
    assert result.slot_url is None
    assert result.operation_pk is None
    assert result.engine_position is None


def test_slot_result_with_a_null_slot() -> None:
    answer = SettledNow.from_json(engine_fixture("m6/settled_now_queued"))
    assert answer is not None
    result = slot_result(STORY, answer, None, "/back/")
    assert result.slot_text == "—"
    assert result.slot_url is None
    assert result.operation_id is None  # null in the answer, and no row
    assert result.revision_text == "Story revision now 12"
    with_row = slot_result(STORY, answer, Row(3, "01JM6QP0000000000000000077"), "/b/")
    assert with_row.operation_id == "01JM6QP0000000000000000077"


# --- D9 ---------------------------------------------------------------------------------------


def err(
    code: str, details: dict[str, Any] | None = None, status: int = 409, **kw: Any
) -> EngineError:
    return EngineError(code, f"{code} message", details, status, **kw)


EFFECTIVE: dict[str, Any] = {"pipeline": "structured"}


def refusal(exc: Any, verb: str = "set a State", **kw: Any) -> WorldRefusal | None:
    return world_refusal(exc, STORY, verb, EFFECTIVE, **kw)


def test_story_busy_with_retry_after() -> None:
    exc = err("story_busy", {"reason": "in_flight"}, 423, retry_after="12")
    assert refusal(exc) == WorldRefusal("Story busy — another turn is running · retry after 12 s")
    assert refusal(err("story_busy", {"reason": "lock_timeout"}, 423)) == WorldRefusal("Story busy")


def test_module_off_reads_the_gate() -> None:
    result = refusal(err("module_off", {"module": "states"}))
    assert result == WorldRefusal(
        unavailable_for("states", EFFECTIVE).reason, reverse("stories:config", args=[STORY])
    )


def test_module_off_without_a_module() -> None:
    result = refusal(err("module_off", {}))
    assert result is not None
    assert result.text == "Needs a module that is off under this story's configuration"
    assert result.url == reverse("stories:config", args=[STORY])


def test_settle_refused_reasons() -> None:
    exc = err("settle_refused", {"reasons": ["precondition:ownership", "bounds:max", "later_code"]})
    assert refusal(exc, "settle an effect now") == WorldRefusal(
        "Settling was refused: the seller no longer owns it; it would break the `max` bound; "
        "`later_code`"
    )
    assert refusal(err("settle_refused", {"reasons": []})) == WorldRefusal("Settling was refused")


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("precondition:availability", "the item is no longer available"),
        ("precondition:ownership", "the seller no longer owns it"),
        ("bounds:max", "it would break the `max` bound"),
        ("settlement_conflict", "it conflicts with another settlement"),
        ("not_confirmed", "the effect isn't confirmed"),
        ("later_code", "`later_code`"),
        ("odd`code", "`oddcode`"),
        (5, "`5`"),
    ],
)
def test_settle_reason_text(code: Any, text: str) -> None:
    assert settle_reason_text(code) == text


def test_state_unpermitted_two_contexts() -> None:
    exc = err("state_unpermitted", {"state": "gold", "reason": "source"}, 422)
    assert refusal(exc, "compensate an effect") == WorldRefusal(
        "Only a State an external provider owns can be compensated"
    )
    assert refusal(exc, "set a State") == WorldRefusal(
        "This State's authorization doesn't admit the consumer as a source"
    )


def test_state_constraint_violation() -> None:
    details = {"state": "gold", "constraint": "max", "attempted": 125, "current": 125}
    exc = err("state_constraint_violation", details, 422)
    definition = refusal(exc, "change a State's definition", value_type="number", unit="gold")
    assert definition == WorldRefusal("The current value 125 gold breaks the new `max` constraint")
    asked = err("state_constraint_violation", {**details, "attempted": 130}, 422)
    assert refusal(asked, value_type="number", unit="gold") == WorldRefusal(
        "That breaks the State's `max` constraint (asked 130 gold, now 125 gold)"
    )
    # Complement: without the State's type and unit the values are inferred.
    assert refusal(asked) == WorldRefusal(
        "That breaks the State's `max` constraint (asked 130, now 125)"
    )
    degraded = err("state_constraint_violation", {"current": 1}, 422)
    assert refusal(degraded) == WorldRefusal(
        "That breaks one of the State's constraints (asked —, now 1)"
    )


def test_validation_error_members() -> None:
    character = err(
        "validation_error", {"reason": "unknown_character", "field": "body.participants"}, 422
    )
    assert refusal(character) == WorldRefusal(UNKNOWN_CHARACTER, field="participants")
    place = err("validation_error", {"reason": "unknown_place", "field": "body.place"}, 422)
    assert refusal(place) == WorldRefusal(PLACE_UNKNOWN, field="place")
    assert PLACE_UNKNOWN == "That isn't a place in this story"


def test_validation_error_other_reasons() -> None:
    reserved = err("validation_error", {"reason": "reserved_state", "field": "body.name"}, 422)
    assert refusal(reserved) == WorldRefusal(
        "`world_clock` is the world clock — use the Clock section", field="name"
    )
    immutable = err("validation_error", {"reason": "immutable_field", "field": "body.scope"}, 422)
    assert refusal(immutable) == WorldRefusal(
        "Scope can't change once a State is declared", field="scope"
    )
    settled = err("validation_error", {"reason": "event_settled", "status": "cancelled"}, 422)
    assert refusal(settled) == WorldRefusal("This event is already cancelled — it can't be changed")
    other = err("validation_error", {"reason": "shape", "field": "body.effect.state"}, 422)
    assert refusal(other) == WorldRefusal("validation_error message", field="effect.state")
    several = err("validation_error", {"errors": [{"field": "body.a"}]}, 422)
    assert refusal(several) is None


def test_field_map_and_placement() -> None:
    unanchored = err("clock_unanchored", {"to": "07:30"}, 422)
    mapped = refusal(unanchored, "schedule an event", field_map={"to": "planned"})
    assert mapped is not None
    assert mapped.field == "planned"
    assert mapped.text == (
        "The clock's anchor doesn't fix the time of day, so \"07:30\" can't be placed — "
        "enter minutes from the story's start"
    )
    plain = refusal(unanchored, "advance the clock")
    assert plain is not None
    assert plain.field == "to"
    backward = err("clock_backward", {"current": 130, "attempted": 60}, 422)
    above = refusal(backward, "advance the clock")
    assert above == WorldRefusal(
        "That would move the clock backward (it is at +02h 10m, you asked for +01h 00m) "
        "— earlier story-time is reached by rewinding"
    )
    assert above.field is None
    placed = err("clock_backward", {"current": 130, "attempted": "x", "field": "body.minutes"})
    beside = refusal(placed, "advance the clock")
    assert beside is not None
    assert beside.field == "minutes"
    assert "you asked for —" in beside.text


def test_not_found_variants() -> None:
    missing = refusal(err("not_found", {"state": "silver"}, 404), "compensate an effect")
    assert missing == WorldRefusal("This story has no State “silver”", field="state")
    assert STATE_NOT_FOUND.replace("{state}", "silver") == missing.text
    listing = refusal(err("not_found", {"reason": "no_listing"}, 404))
    assert listing == WorldRefusal(
        "This effect is no longer listed for reconciliation — reload the list"
    )
    proposal = refusal(err("not_found", {"reason": "no_pending_proposal"}, 404))
    assert proposal == WorldRefusal("This proposal is no longer pending — reload the list")
    assert refusal(err("not_found", {}, 404)) is None
    assert refusal(err("not_found", {"state": 5}, 404)) is None


def test_state_sentences() -> None:
    assert refusal(err("state_readonly_external", {"state": "gold"}, 422)) == WorldRefusal(
        READONLY_EXTERNAL_TEXT
    )
    assert READONLY_EXTERNAL_TEXT == (
        "This State's value belongs to its provider — it can't be set here"
    )
    assert refusal(err("state_derived", {"state": "worth"}, 422)) == WorldRefusal(
        "This State is derived from other States — it can't be set directly"
    )
    taken = refusal(err("state_name_taken", {"state": "gold"}, 409), "declare a State")
    assert taken == WorldRefusal("A State named “gold” already exists", field="name")
    assert refusal(err("state_name_taken", {}, 409)) == WorldRefusal(
        "A State with that name already exists", field="name"
    )
    assert refusal(err("viewpoint_unresolved", {"field": "body.shared_owners"}, 422)) == (
        WorldRefusal(UNKNOWN_CHARACTER, field="shared_owners")
    )
    assert refusal(err("clock_external", {"provider_id": "bank"}, 409)) == WorldRefusal(
        "This clock follows its provider's value (external mode) — it can't be advanced here"
    )


def test_provider_codes() -> None:
    read_only = refusal(err("provider_read_only", {"provider": "bank", "state": "gold"}, 422))
    assert read_only == WorldRefusal("bank is read-only — it can't take a compensating effect")
    assert refusal(err("provider_read_only", {}, 422)) == WorldRefusal(
        "This provider is read-only — it can't take a compensating effect"
    )
    # Complement: while ``stories:providers`` doesn't resolve, the link is "".
    unbound = refusal(err("provider_unbound", {"providers": ["bank", "shop"]}, 409))
    assert unbound == WorldRefusal("This story has no binding to bank, shop — bind it first")
    assert refusal(err("provider_unbound", {"providers": [1]}, 409)) == WorldRefusal(
        "This story has no binding to its providers — bind it first"
    )


def test_provider_unbound_links_the_bindings_page() -> None:
    with override_settings(ROOT_URLCONF="tests.urls_with_engine_operation"):
        unbound = refusal(err("provider_unbound", {"providers": ["bank"]}, 409))
        assert unbound is not None
        assert unbound.url == reverse("stories:providers", args=[STORY])
        assert unbound.url == f"/stories/{STORY}/providers/"


@pytest.mark.parametrize(
    ("code", "details"),
    [
        ("story_archived", {}),
        ("story_paused", {"paused_at": "2026-10-30T09:12:44Z"}),
        ("revision_conflict", {"current_revision": 14}),
    ],
)
def test_c5_codes_fall_through(code: str, details: dict[str, Any]) -> None:
    exc = err(code, details)
    expected = write_refusal(exc, STORY, "set a State")
    assert expected is not None
    assert refusal(exc) == WorldRefusal(expected.text, expected.url, None)


def test_none_for_what_c5_returns_none_for() -> None:
    assert refusal(err("idempotency_conflict", {})) is None
    assert refusal(err("later_code", {})) is None
    assert refusal(EngineUnreachable("timeout")) is None

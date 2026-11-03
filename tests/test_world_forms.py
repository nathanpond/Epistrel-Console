"""#235: D4\u2013D7's inputs through ``console/forms/world.py``: the World viewpoint picker,
story-time and boundary fields, and the number, delta and scalar value inputs."""

from typing import Any

import pytest
from django import forms

from console.engine.client import EngineError
from console.engine.models import Story
from console.forms.inspect import VIEWPOINT_NOT_IN_CAST, viewpoint_choices
from console.forms.world import (
    BOUNDARY_INVALID,
    DELTA_INVALID,
    PLANNED_INVALID,
    VALUE_HELP,
    BoundaryField,
    DeltaField,
    PlannedField,
    ScalarValueField,
    WorldViewpointForm,
    parse_number,
)
from tests.fixtures import engine_fixture

MARA, ALICE, BOB = (f"01JM6CH000000000000000000{n}" for n in (1, 2, 3))


def cast() -> Any:
    story = Story.from_json(engine_fixture("m6/story"))
    assert story is not None
    return story.cast


def test_viewpoint_choices_and_initial() -> None:
    story = engine_fixture("m6/story")
    assert story["settings"]["default_output_viewpoint"] == f"character:{MARA}"
    form = WorldViewpointForm({}, cast=cast())
    assert form.choices == [
        ("omniscient", "Omniscient"),
        (f"character:{MARA}", "Mara (player)"),
        (f"character:{ALICE}", "Alice"),
        (f"character:{BOB}", "Bob"),
    ]
    assert form.initial_viewpoint == "omniscient"
    assert form.is_valid()
    assert form.query() == {"viewpoint": "omniscient"}


def test_viewpoint_initial_is_omniscient_even_with_a_default_alice() -> None:
    story = engine_fixture("m6/story")
    story["settings"]["default_output_viewpoint"] = f"character:{ALICE}"
    parsed = Story.from_json(story)
    assert parsed is not None
    form = WorldViewpointForm({}, cast=parsed.cast)
    assert form.initial_viewpoint == "omniscient"
    assert form.is_valid()
    assert form.query() == {"viewpoint": "omniscient"}


def test_viewpoint_outside_the_cast() -> None:
    form = WorldViewpointForm({"viewpoint": "character:01JM6CH0000000000000000099"}, cast=cast())
    assert not form.is_valid()
    assert form.errors["viewpoint"] == [VIEWPOINT_NOT_IN_CAST]


def test_viewpoint_without_the_story_read() -> None:
    form = WorldViewpointForm({"viewpoint": "character:X"}, cast=None)
    assert form.choices == [("omniscient", "Omniscient"), ("character:X", "character:X")]
    assert form.is_valid()


def test_viewpoint_choices_is_scene_forms() -> None:
    assert viewpoint_choices(None, "") == [("omniscient", "Omniscient")]
    assert WorldViewpointForm({}, cast=cast()).choices == viewpoint_choices(cast(), "")


def test_viewpoint_engine_errors() -> None:
    form = WorldViewpointForm({"viewpoint": f"character:{ALICE}"}, cast=cast())
    assert form.is_valid()
    form.apply_engine_error(EngineError("viewpoint_unresolved", "no", {"field": "x"}, 422))
    assert form.errors["viewpoint"] == [VIEWPOINT_NOT_IN_CAST]
    other = WorldViewpointForm({"viewpoint": "omniscient"}, cast=cast())
    assert other.is_valid()
    other.apply_engine_error(EngineError("story_busy", "Busy now", {}, 423))
    assert other.non_field_errors() == ["Busy now"]


def clean(field: forms.Field, value: Any) -> Any:
    return field.clean(value)


def error_of(field: forms.Field, value: Any) -> list[str]:
    with pytest.raises(forms.ValidationError) as caught:
        field.clean(value)
    return list(caught.value.messages)


@pytest.mark.parametrize(("text", "value"), [("130", 130), ("0130", 130), ("07:30", "07:30"),
                                             (" 23:59 ", "23:59")])  # fmt: skip
def test_planned_field(text: str, value: Any) -> None:
    assert clean(PlannedField(), text) == value


@pytest.mark.parametrize("text", ["24:00", "7:3", "7:30", "+5", "٣", "", "12:60", "-5"])
def test_planned_field_invalid(text: str) -> None:
    assert error_of(PlannedField(), text) == [PLANNED_INVALID]


def test_planned_field_optional_blank() -> None:
    assert PlannedField(required=False).clean("") is None


@pytest.mark.parametrize(
    ("text", "value"), [("12", "12"), ("12+1", "12+1"), ("12⁺¹", "12+1"),
                        ("012", "12"), ("12+0", "12+0")]
)  # fmt: skip
def test_boundary_field(text: str, value: str) -> None:
    assert clean(BoundaryField(), text) == value


@pytest.mark.parametrize("text", ["12+", "+1", "12.1", "x", "1 2"])
def test_boundary_field_invalid(text: str) -> None:
    assert error_of(BoundaryField(), text) == [BOUNDARY_INVALID]


def test_boundary_field_optional_blank() -> None:
    assert BoundaryField(required=False).clean("") is None


@pytest.mark.parametrize(
    ("text", "value"),
    [("0.5", 0.5), ("+5", 5), ("\u22123", -3), ("12", 12), ("-3", -3), ("-0", 0), ("007", 7),
     ("123456789012345678901234567890", 123456789012345678901234567890)],
)  # fmt: skip
def test_parse_number(text: str, value: float) -> None:
    parsed = parse_number(text)
    assert parsed == value
    assert type(parsed) is type(value)


@pytest.mark.parametrize("text", ["5e3", "1,000", "NaN", "١٢", "-", "", ".5", "5.", "inf", None])
def test_parse_number_refuses(text: Any) -> None:
    assert parse_number(text) is None


@pytest.mark.parametrize(("text", "value"), [("-500", -500), ("+5", 5), ("2.5", 2.5),
                                             ("\u22123", -3), ("", None)])  # fmt: skip
def test_delta_field(text: str, value: Any) -> None:
    assert clean(DeltaField(), text) == value


@pytest.mark.parametrize("text", ["5e3", "1,000", "٣", "-"])
def test_delta_field_invalid(text: str) -> None:
    assert error_of(DeltaField(), text) == [DELTA_INVALID]


@pytest.mark.parametrize(
    ("text", "value"),
    [("true", True), ("false", False), ("12", 12), ("-1.5", -1.5), ('"12"', "12"),
     ("sword", "sword"), ("  open  ", "open"), ("", None), ("True", "True"), ("null", "null"),
     ('"abc', '"abc')],
)  # fmt: skip
def test_scalar_value_field(text: str, value: Any) -> None:
    cleaned = clean(ScalarValueField(), text)
    assert cleaned == value
    assert type(cleaned) is type(value)


def test_scalar_value_help() -> None:
    assert ScalarValueField().help_text == VALUE_HELP
    assert VALUE_HELP == (
        'Numbers and true/false are sent as such; put text in double quotes to send "12" or '
        '"true" as text.'
    )


@pytest.mark.parametrize("field", [PlannedField, DeltaField, ScalarValueField, BoundaryField])
def test_widgets_carry_no_bounds(field: type[forms.Field]) -> None:
    html = field().widget.render("v", "")
    assert 'type="text"' in html
    assert "min" not in html
    assert "max" not in html

"""``apply_engine_errors`` (#43's 422 placement, built in #45): Engine field paths onto a form
and its formset rows."""

from typing import Any

import pytest
from django import forms

from console.engine.client import EngineError
from console.engine_form_errors import apply_engine_errors, engine_field_errors


class Row(forms.Form):
    name = forms.CharField(required=False)
    true_age = forms.IntegerField(required=False)


class Story(forms.Form):
    name = forms.CharField(required=False)
    viewpoint = forms.CharField(required=False)


RowSet = forms.formset_factory(Row, extra=0, min_num=1)


def make(rows: int = 2) -> tuple[Story, Any]:
    story = Story({"name": "Harbour", "viewpoint": ""})
    data: dict[str, Any] = {"c-TOTAL_FORMS": str(rows), "c-INITIAL_FORMS": "0"}
    for i in range(rows):
        data[f"c-{i}-name"] = f"C{i}"
    formset = RowSet(data, prefix="c")
    assert story.is_valid()
    assert formset.is_valid()
    return story, formset


def error(code: str = "validation_error", message: str = "bad", **details: Any) -> EngineError:
    return EngineError(code, message, details, 422)


def test_errors_list_wins_over_field() -> None:
    exc = error(
        field="body.name",
        errors=[
            {"field": "body.name", "message": "too long", "type": "x"},
            {"field": "body.c[1].true_age", "message": "too old", "type": "y"},
        ],
    )
    story, rows = make()
    assert apply_engine_errors(exc, story, {"c": rows}) == 2
    assert story.errors["name"] == ["too long"]
    assert rows.forms[1].errors["true_age"] == ["too old"]
    assert not rows.forms[0].errors


def test_field_alone_uses_the_error_message() -> None:
    story, rows = make()
    apply_engine_errors(error(message="dup", field="body.c[0].name"), story, {"c": rows})
    assert rows.forms[0].errors["name"] == ["dup"]


def test_row_index_follows_the_sent_list_not_the_formset() -> None:
    story, rows = make(3)
    sent = [rows.forms[0], rows.forms[2]]  # row 1 was blank and dropped before sending
    apply_engine_errors(error(message="m", field="body.c[1].name"), story, {"c": sent})
    assert rows.forms[2].errors["name"] == ["m"]
    assert not rows.forms[1].errors


def test_unknown_field_on_a_row_goes_to_that_row() -> None:
    story, rows = make()
    apply_engine_errors(error(message="m", field="body.c[1].colour"), story, {"c": rows})
    assert rows.forms[1].non_field_errors() == ["colour: m"]


@pytest.mark.parametrize(
    "path",
    ["body.c[5].name", "body.unknown", "query.status", "body.c", "body.settings.x"],
)
def test_unmatched_paths_go_to_non_field_errors_with_the_path(path: str) -> None:
    story, rows = make()
    apply_engine_errors(error(message="m", field=path), story, {"c": rows})
    shown = path.removeprefix("body.")
    assert story.non_field_errors() == [f"{shown}: m"]


def test_alias_by_path() -> None:
    story, _ = make()
    exc = error(message="no such character", field="body.settings.default_output_viewpoint")
    apply_engine_errors(exc, story, aliases={"settings.default_output_viewpoint": "viewpoint"})
    assert story.errors["viewpoint"] == ["no such character"]


def test_alias_by_detail_key_and_by_code() -> None:
    story, _ = make()
    exc = error("unknown_actor", "who?", unknown_actor="Bob")
    assert apply_engine_errors(exc, story, aliases={"unknown_actor": "viewpoint"}) == 1
    assert story.errors["viewpoint"] == ["who?"]
    story, _ = make()
    exc = error("viewpoint_unresolved", "none named")
    apply_engine_errors(exc, story, aliases={"viewpoint_unresolved": "viewpoint"})
    assert story.errors["viewpoint"] == ["none named"]


def test_nothing_to_place_places_nothing() -> None:
    story, _ = make()
    assert apply_engine_errors(error(message="m"), story) == 0
    assert not story.errors
    assert engine_field_errors(error(errors="not a list")) == []

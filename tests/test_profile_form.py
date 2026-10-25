"""The JSON-Schema → Django form generator for engine profiles (#84)."""

from collections.abc import Iterator
from typing import Any, cast

import pytest
from django import forms
from django.test import Client

from console.engine.client import EngineError
from console.engine.models import (
    MissingDependency,
    ProfileSchema,
    ProfileValidation,
    ProfileValidationWarning,
    RequiredModule,
    SettingTags,
    UndeliveredSetting,
)
from console.forms.profile_schema_form import (
    OTHER,
    Group,
    Leaf,
    SchemaSettingsForm,
    ShownGroup,
    build_tree,
    leaves,
    missing_text,
    schema_digest,
    validation_banner,
    warning_text,
)
from tests.fakes.engine import FakeEngine
from tests.profile_helpers import (
    ITEM,
    fake_engine,
    field,
    fresh_cache,
    null,
    other_profile,
    posted,
    schema_model,
)
from tests.profile_helpers import (
    OTHER as OTHER_ID,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    yield from fresh_cache()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    yield from fake_engine(monkeypatch)


def synthetic(properties: dict[str, Any], **extra: Any) -> ProfileSchema:
    """A small profile schema holding ``properties`` (and ``$defs`` etc. via ``extra``)."""
    tags = {"mod.flag": SettingTags("behavioral", "prospective", "relevant")}
    return ProfileSchema(
        {"type": "object", "properties": properties, **extra}, tags, {"mod": ["on"]}, ["generation"]
    )


SYNTHETIC = {
    "mod": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "flag": {"type": "boolean", "default": True, "description": "Turns it on."},
            "mode": {"type": "string", "enum": ["a", "b"], "default": "a"},
            "count": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            "ratio": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
            "free": {"type": "number"},
            "label": {"type": "string", "minLength": 2, "maxLength": 5},
            "cap": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 50}, {"type": "null"}]},
            "length": {
                "anyOf": [
                    {"type": "string", "enum": ["short", "long"]},
                    {"type": "integer", "minimum": 1, "maximum": 4000},
                ],
                "default": "short",
            },
            "picks": {"type": "array", "items": {"type": "string", "enum": ["x", "y", "z"]}},
            "words": {"type": "array", "items": {"type": "integer"}},
            "rows": {"type": "array", "items": {"type": "object", "properties": {"k": {}}}},
            "either": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
            "map": {"type": "object", "patternProperties": {"^[a-z]+$": {"type": "integer"}}},
            "bag": {"type": "object", "additionalProperties": {"type": "integer"}},
            "loose": {"type": "object"},
            "ref": {"$ref": "#/$defs/Shared"},
            "merged": {
                "allOf": [
                    {"type": "object", "properties": {"one": {"type": "integer"}}},
                    {"properties": {"two": {"type": "boolean"}}},
                ]
            },
        },
    }
}
DEFS = {"$defs": {"Shared": {"type": "object", "properties": {"depth": {"type": "integer"}}}}}


def kinds(schema: ProfileSchema) -> dict[str, str]:
    return {leaf.dotted: leaf.kind for leaf in leaves(build_tree(schema))}


def form(data: Any = None, **kw: Any) -> SchemaSettingsForm:
    return SchemaSettingsForm(data, schema=synthetic(SYNTHETIC, **DEFS), **kw)


# --- mapping ------------------------------------------------------------------------------------


def test_each_schema_type_maps_to_its_widget() -> None:
    built = form()
    widgets = {name: type(f.widget) for name, f in built.fields.items()}
    assert kinds(synthetic(SYNTHETIC, **DEFS)) == {
        "mod.flag": "boolean",
        "mod.mode": "enum",
        "mod.count": "integer",
        "mod.ratio": "number",
        "mod.free": "number",
        "mod.label": "string",
        "mod.cap": "integer",
        "mod.length": "choice",
        "mod.picks": "enum_list",
        "mod.words": "lines",
        "mod.rows": "json",
        "mod.either": "json",
        "mod.map": "json",
        "mod.bag": "json",
        "mod.loose": "json",
        "mod.ref.depth": "integer",
        "mod.merged.one": "integer",
        "mod.merged.two": "boolean",
    }
    assert widgets[field("mod.flag")] is forms.Select
    assert widgets[field("mod.mode")] is forms.Select
    assert widgets[field("mod.count")] is forms.NumberInput
    assert widgets[field("mod.free")] is forms.NumberInput  # unbounded stays a number input
    assert widgets[field("mod.label")] is forms.TextInput
    assert widgets[field("mod.length")] is forms.TextInput
    assert widgets[field("mod.picks")] is forms.SelectMultiple
    assert widgets[field("mod.words")] is forms.Textarea
    assert widgets[field("mod.rows")] is forms.Textarea
    assert widgets[field("mod.either")] is forms.Textarea
    # The boolean select's options: unset first, then Yes/No.
    flag = built.fields[field("mod.flag")]
    assert isinstance(flag, forms.ChoiceField)
    assert list(cast(list[tuple[str, str]], flag.choices)) == [
        ("", "(schema default: true)"),
        ("true", "Yes"),
        ("false", "No"),
    ]
    # Numbers: step and bounds as attributes.
    count = built.fields[field("mod.count")].widget.attrs
    assert (count["step"], count["min"], count["max"]) == ("1", "1", "10")
    assert built.fields[field("mod.ratio")].widget.attrs["step"] == "any"
    # Only nullable fields get a "null" checkbox.
    assert null("mod.cap") in built.fields
    assert null("mod.count") not in built.fields


def test_fieldsets_nest_and_deep_objects_become_json() -> None:
    tree = build_tree(synthetic(SYNTHETIC, **DEFS))
    (mod,) = tree
    assert isinstance(mod, Group)
    assert [type(c).__name__ for c in mod.children if c.path[-1] in ("ref", "merged")] == [
        "Group",
        "Group",
    ]
    deep: dict[str, Any] = {"type": "integer"}
    for _ in range(10):
        deep = {"type": "object", "properties": {"n": deep}}
    (leaf,) = leaves(build_tree(synthetic({"top": deep})))
    assert leaf.kind == "json"
    assert len(leaf.path) == 9  # depth 8 is the last fieldset level


def test_the_real_schema_maps_without_json_except_purposes() -> None:
    found = kinds(schema_model())
    assert found["models.purposes"] == "json"
    assert found["models.generation.response_length"] == "choice"
    assert found["budgets.model_calls"] == "integer"
    assert [p for p, k in found.items() if k == "json"] == ["models.purposes"]
    built = SchemaSettingsForm(schema=schema_model())
    purposes = str(built.fields[field("models.purposes")].help_text)
    assert "Purposes: belief_adjudication" in purposes
    assert "short / medium / long, or 1\u20134000 (integer)" in str(
        built.fields[field("models.generation.response_length")].help_text
    )


def test_tag_badges_are_on_leaves_and_unioned_on_json_subtrees() -> None:
    tree = build_tree(schema_model())
    by_path = {leaf.dotted: leaf for leaf in leaves(tree)}
    pipeline = by_path["pipeline"]
    assert [b.text for b in pipeline.badges] == [
        "data-producing",
        "prospective",
        "quality-relevant",
    ]
    purposes = by_path["models.purposes"]
    assert purposes.badges  # the union of every tagged path under it
    assert isinstance(tree[1], Group)
    assert not hasattr(tree[1], "badges")


# --- values -------------------------------------------------------------------------------------


def test_create_starts_unset_and_sends_nothing() -> None:
    built = form({})
    assert built.is_valid(), built.errors
    assert built.settings() == {}


def test_parsed_values_go_into_a_nested_sparse_document() -> None:
    built = form(
        {
            field("mod.flag"): "false",
            field("mod.mode"): "b",
            field("mod.count"): "7",
            field("mod.ratio"): "0.5",
            field("mod.length"): "120",
            field("mod.picks"): ["z", "x"],
            field("mod.words"): "3\n\n 4 \n3",
            field("mod.rows"): '[{"k": 1}]',
            field("mod.merged.two"): "true",
        }
    )
    assert built.is_valid(), built.errors
    assert built.settings() == {
        "mod": {
            "flag": False,
            "mode": "b",
            "count": 7,
            "ratio": 0.5,
            "length": 120,
            "picks": ["z", "x"],
            "words": [3, 4],
            "rows": [{"k": 1}],
            "merged": {"two": True},
        }
    }


def test_choice_tries_each_branch_in_order() -> None:
    built = form({field("mod.length"): "long"})
    assert built.is_valid()
    assert built.settings() == {"mod": {"length": "long"}}
    bad = form({field("mod.length"): "huge"})
    assert not bad.is_valid()
    assert "short / long, or 1\u20134000 (integer)" in bad.errors[field("mod.length")][0]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("mod.count", "0", "Must be within 1\u201310."),
        ("mod.count", "11", "Must be within 1\u201310."),
        ("mod.count", "2.5", "Enter a whole number."),
        ("mod.ratio", "0", "Must be within > 0, < 1."),
        ("mod.ratio", "1", "Must be within > 0, < 1."),
        ("mod.ratio", "nan", "Enter a finite number."),
        ("mod.label", "a", "Must be length 2\u20135 characters."),
        ("mod.length", "5000", "Must be within 1\u20134000."),
        ("mod.cap", "51", "Must be within 1\u201350."),
        ("mod.rows", "[1,", "Enter valid JSON"),
    ],
)
def test_bounds_become_validators(path: str, value: str, message: str) -> None:
    built = form({field(path): value})
    assert not built.is_valid()
    assert str(built.errors[field(path)][0]).startswith(message)


def test_integers_accept_a_whole_float_and_compare_numerically() -> None:
    built = form({field("mod.count"): "10.0"}, stored={"mod": {"count": 10}})
    assert built.is_valid()
    assert built.settings() == {"mod": {"count": 10}}
    assert isinstance(built.settings()["mod"]["count"], int)


def test_the_null_checkbox_sends_null_and_ignores_the_input() -> None:
    built = form({null("mod.cap"): "on", field("mod.cap"): "999"})
    assert built.is_valid(), built.errors
    assert built.settings() == {"mod": {"cap": None}}
    stored = form(stored={"mod": {"cap": None}})
    assert stored[null("mod.cap")].value() is True


def test_an_unmappable_subtree_is_json_sent_whole() -> None:
    built = form({field("mod.map"): '{"abc": 1, "def": 2}', field("mod.either"): '"text"'})
    assert built.is_valid(), built.errors
    assert built.settings() == {"mod": {"map": {"abc": 1, "def": 2}, "either": "text"}}
    dup = form({field("mod.map"): '{"a": 1, "a": 2}'})
    assert not dup.is_valid()
    assert "duplicate key" in dup.errors[field("mod.map")][0]


def test_edit_prefills_stored_keys_and_drops_a_cleared_one() -> None:
    stored = {"mod": {"count": 3, "mode": "b", "flag": False}}
    built = form(stored=stored)
    assert built[field("mod.count")].value() == "3"
    assert built[field("mod.mode")].value() == "b"
    assert built[field("mod.flag")].value() == "false"
    assert built[field("mod.ratio")].value() is None
    cleared = form({field("mod.count"): "3", field("mod.mode"): "", field("mod.flag"): "false"})
    assert cleared.is_valid()
    assert cleared.settings() == {"mod": {"count": 3, "flag": False}}


def test_an_unchanged_stored_value_outside_the_bounds_warns_and_is_sent() -> None:
    built = form({field("mod.count"): "40"}, stored={"mod": {"count": 40}})
    assert built.is_valid(), built.errors
    assert built.field_warnings[field("mod.count")] == [
        "Outside this deployment's bounds (1\u201310)"
    ]
    assert built.settings() == {"mod": {"count": 40}}
    changed = form({field("mod.count"): "41"}, stored={"mod": {"count": 40}})
    assert not changed.is_valid()


def test_default_labels_prefer_the_deployment_default() -> None:
    built = form(defaults={"mod": {"count": 9}})
    count = built.fields[field("mod.count")].widget.attrs
    assert count["placeholder"] == "default: 9"
    assert built.fields[field("mod.ratio")].widget.attrs.get("placeholder") is None
    mode = built.fields[field("mod.mode")]
    assert isinstance(mode, forms.ChoiceField)
    assert cast(list[tuple[str, str]], mode.choices)[0] == ("", "(schema default: a)")
    unread = form(defaults=None)
    assert unread.fields[field("mod.count")].widget.attrs["placeholder"] == "schema default: 5"


# --- the "not in schema" textarea -----------------------------------------------------------------


def test_undescribed_stored_keys_prefill_the_other_textarea_and_are_sent() -> None:
    stored = {"mod": {"count": 2, "gone": 1}, "retired": {"level": "x"}}
    built = form(stored=stored)
    assert '"retired"' in built[OTHER].value()
    assert '"gone"' in built[OTHER].value()
    data = {field("mod.count"): "2", OTHER: built[OTHER].value()}
    again = form(data, stored=stored)
    assert again.is_valid(), again.errors
    assert again.settings() == stored


def test_a_form_field_named_in_the_other_textarea_is_refused() -> None:
    built = form({OTHER: '{"mod": {"count": 4}}'})
    assert not built.is_valid()
    assert built.errors[OTHER] == ["`mod.count` is a form field — set it there"]
    not_object = form({OTHER: "[1]"})
    assert not not_object.is_valid()


# --- placement ------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["body.settings.mod.count", "settings.mod.count", "mod.count"])
def test_engine_paths_resolve_to_their_field(path: str) -> None:
    built = form({})
    built.is_valid()
    built.place(path, "too big")
    assert built.errors[field("mod.count")] == ["too big"]


def test_a_path_inside_a_json_subtree_lands_on_its_textarea() -> None:
    built = form({})
    built.is_valid()
    built.place("body.settings.mod.map.abc", "not allowed")
    assert built.errors[field("mod.map")] == ["abc: not allowed"]
    built.place("settings.unknown.level", "undelivered")
    assert built.errors[OTHER] == ["unknown.level: undelivered"]


def test_validation_places_dependencies_undelivered_and_warnings() -> None:
    built = SchemaSettingsForm({}, schema=schema_model())
    built.is_valid()
    result = ProfileValidation(
        False,
        [
            MissingDependency(
                "episodic",
                "on",
                [RequiredModule("rolling_summary", []), RequiredModule("pins", ["a", "b"])],
            ),
            MissingDependency("nowhere", "on", [RequiredModule("pins", ["on"])]),
        ],
        [UndeliveredSetting("perception", "full", "perception.level")],
        [ProfileValidationWarning("below_safety_minimum", "budgets.model_calls", 1, 3, {"g": 3})],
    )
    built.place_validation(result)
    assert built.group_errors["memory.episodic"] == [
        "`episodic` at `on` needs `rolling_summary` at any level and `pins` at `a` or `b`"
    ]
    assert built.errors["__all__"] == ["`nowhere` at `on` needs `pins` at `on`"]
    assert built.errors[field("perception.level")] == [
        "Not delivered on this deployment: `perception` at `full`"
    ]
    assert built.field_warnings[field("budgets.model_calls")] == [
        "Below safety minimum: `model_calls` 1; a turn needs at least 3 (reservation: g=3)"
    ]
    shown = built.shown()
    memory = next(n for n in shown if isinstance(n, ShownGroup) and n.group.dotted == "memory")
    assert memory.open  # an error inside opens the fieldset
    assert validation_banner(result, built.problem_count) == "Not valid — 3 problems"


def test_banner_texts() -> None:
    assert validation_banner(ProfileValidation(True, [], []), 0) == "Valid"
    warned = ProfileValidation(
        True, [], [], [ProfileValidationWarning("below_safety_minimum", "x", 1, 2, {})] * 2
    )
    assert validation_banner(warned, 0) == "Valid, with 2 warnings"
    assert missing_text(MissingDependency("a", "on", [RequiredModule("b", ["x"])])) == (
        "`a` at `on` needs `b` at `x`"
    )
    assert (
        warning_text(ProfileValidationWarning("other_code", "x.y", 1, 2, {})) == "other_code: x.y"
    )


def test_invalid_profile_details_are_placed() -> None:
    built = SchemaSettingsForm({}, schema=schema_model())
    built.is_valid()
    exc = EngineError(
        "invalid_profile",
        "the profile cannot run",
        {
            "undelivered": [
                {"module": "perception", "level": "full", "setting": "perception.level"}
            ],
            "missing_dependencies": [],
            "stories": ["s1", "s2"],
            "count": 4,
            "kind": "x",
        },
        422,
    )
    placed = built.place_invalid_profile(exc)
    assert field("perception.level") in built.errors
    assert (placed.stories, placed.count, placed.extra) == (["s1", "s2"], 4, [("kind", "x")])


def test_schema_digest_changes_with_the_schema() -> None:
    one = schema_model()
    other = synthetic(SYNTHETIC)
    assert schema_digest(one) == schema_digest(schema_model())
    assert schema_digest(one) != schema_digest(other)


# --- defaults mode --------------------------------------------------------------------------------


def test_defaults_mode_sends_only_changed_leaves_as_nested_objects() -> None:
    stored = {"mod": {"count": 5, "cap": 3, "map": {"abc": 1}}}
    built = SchemaSettingsForm(
        schema=synthetic(SYNTHETIC, **DEFS), root="profile_defaults", stored=stored, mode="defaults"
    )
    assert OTHER not in built.fields
    assert field("mod.count", "profile_defaults") in built.fields
    data = {
        field("mod.count", "profile_defaults"): "6",
        field("mod.cap", "profile_defaults"): "3",
        field("mod.map", "profile_defaults"): '{"abc": 1, "xyz": 2}',
    }
    again = SchemaSettingsForm(
        data,
        schema=synthetic(SYNTHETIC, **DEFS),
        root="profile_defaults",
        stored=stored,
        mode="defaults",
    )
    assert again.is_valid(), again.errors
    assert again.defaults_patch() == {"mod": {"count": 6, "map": {"xyz": 2}}}


def test_defaults_mode_refuses_clearing_a_stored_value() -> None:
    stored = {"mod": {"count": 5, "cap": 3}}
    built = SchemaSettingsForm(
        {field("mod.count", "profile_defaults"): "", field("mod.cap", "profile_defaults"): ""},
        schema=synthetic(SYNTHETIC, **DEFS),
        root="profile_defaults",
        stored=stored,
        mode="defaults",
    )
    assert not built.is_valid()
    assert built.errors[field("mod.cap", "profile_defaults")] == ["Tick “null” to set null"]
    assert "can't be left unset" in built.errors[field("mod.count", "profile_defaults")][0]


# --- zero calls -----------------------------------------------------------------------------------


def test_a_value_outside_the_schema_bounds_makes_zero_engine_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=other_profile())
    page = operator_client.get(f"/profiles/{OTHER_ID}/edit/")
    engine.reset()
    data = posted(
        page, **{field("memory.rolling_summary.window_messages"): "501", "action": "save"}
    )
    response = operator_client.post(f"/profiles/{OTHER_ID}/edit/", data)
    assert response.status_code == 200
    assert engine.requests == []
    assert "Must be within 1\u2013500." in response.content.decode()


def test_leaf_and_group_types() -> None:
    leaf = Leaf(("a",), "integer", minimum=1)
    assert leaf.has_bounds
    assert leaf.dotted == "a"
    assert Group(("a", "b")).name == "b"

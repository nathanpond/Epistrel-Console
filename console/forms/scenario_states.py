"""A scenario's starting States in the editor (#267): the ``state`` row set, one
:class:`ScenarioStateForm` per ``ScenarioState`` in ``starting_state.states``.

The row is #261's :class:`~console.forms.states.StateDefinitionFields` (its fields, parsing,
checks and texts, never redefined here) plus what only a scenario has: ``external_value_name``,
an ``entity_id`` that is a slug select of the form's roles, locations and entities, kept choices
for a stored type, scope, visibility or owner outside the offered ones (C7, so a stored
``clock`` type is never rewritten), and the row-set checks of :class:`StateFormSet`.

Both sides of the editor's comparison (C9) normalise the same way, :func:`state_to_write`: keys
in ``ScenarioState``'s schema order, ``null``/``[]``/``{}`` optionals and an empty
``constraints`` left out, strings stripped and blank lines dropped as the form does, every value
kept as its JSON type. The row is rendered by :func:`state_editor_row`; its hidden
:data:`SNAPSHOT_FIELDS` keep the stored ``initial_value`` (compact JSON) and ``type``, so a value
the row renders but could not parse back the same (a type-mismatched value, ``1e20``) is sent as
stored while its text and the type are untouched.

Local checks run on save only, and only on rows that are new or changed: a row whose posted
rendering equals a draft entry's (a multiset match, so reordering wakes nothing) is that entry,
sent as stored, its problems left to the Engine's validation report.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any, cast

from django import forms
from django.forms import BaseFormSet
from django.forms.utils import ErrorDict

from console.forms.states import (
    BOXES,
    CONSTRAINT_MEMBERS,
    EXTERNAL,
    OWNER_CHOICES,
    PROVIDER_STORY_OWNED,
    SCOPE_CHOICES,
    STATE_NAME,
    STATE_NAME_INVALID,
    STATE_TYPES,
    TYPE_CHOICES,
    VISIBILITY_CHOICES,
    StateDefinitionFields,
)
from console.story_settings import kept_choices
from console.world import BOOLEAN, ENUM, NUMBER, TEXT

STATE = "state"
STATES_PATH = "starting_state.states"
STATES_MAX = 200
WORLD_CLOCK = "world_clock"

# ``ScenarioState``'s members in schema order (the Engine's ``scenario_models.ScenarioState``).
STATE_MEMBERS = (
    "name",
    "type",
    "scope",
    "constraints",
    "initial_value",
    "aliases",
    "unit",
    "entity_id",
    "visibility",
    "owner",
    "provider_id",
    "external_value_name",
    "silent",
    "rules",
    "authorization",
    "transfer",
    "reservation_expiry_minutes",
)
TEXT_MEMBERS = ("name", "unit", "entity_id", "provider_id", "external_value_name")
SNAPSHOT_FIELDS = ("initial_value_raw", "type_raw")

STATES_HELP = (
    "A story created from this scenario tracks these States from its start. Authorization "
    "sources name roles as `role:<id>`."
)
NAME_NEEDED = "A State needs its name"
WORLD_CLOCK_RESERVED = "`world_clock` is the world clock — set the starting clock in Clock"
NAME_USED = "Already used by row {row}"
SCALAR_INVALID = "Enter a JSON value: a number, true/false or text in double quotes"
EXTERNAL_VALUE_HELP = "The provider's value name (default: the State's name)"
ENTITY_SLUG_HELP = "A role, location or entity of this scenario the State tracks"
NONE_LABEL = "— none —"
MISSING_SUFFIX = " (missing)"
DETAILS_SUMMARY = "Rules, authorization and transfer"

# The partial's groups, in page order (``_states.html``; the boxes are the <details>).
ROW_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Identity", ("name", "type", "scope", "visibility", "unit")),
    (
        "Value",
        (
            "initial_value",
            "constraints_min",
            "constraints_max",
            "constraints_tolerance",
            "constraints_non_negative",
            "constraints_allowed_values",
        ),
    ),
    (
        "Ownership",
        (
            "owner",
            "provider_id",
            "external_value_name",
            "entity_id",
            "aliases",
            "silent",
            "reservation_expiry_minutes",
        ),
    ),
)
# Every posted field of a row but the snapshots, in the order the rendering compares them.
ROW_FIELDS: tuple[str, ...] = (
    *(name for _, names in ROW_GROUPS for name in names),
    *BOXES,
)
NEW_STATE_ROW: dict[str, list[str]] = {}  # all blank, C7 selects on their default


# --- normalising and rendering --------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _box_text(value: Any) -> str:
    """A JSON box's text, as the editor's other boxes render it (indented, key order kept)."""
    return json.dumps(value, indent=2, ensure_ascii=False)


def _lines(values: Any) -> list[Any]:
    if not isinstance(values, list):
        return values  # type: ignore[no-any-return]
    out: list[Any] = []
    for value in values:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        out.append(value)
    return out


def number_text(value: Any) -> str:
    """A stored number as the row's input shows it, so ``parse_number`` reads it back: an int
    as is, a float by ``repr`` (``1.0``), or fixed-point when ``repr`` has an exponent
    (``1e-07`` → ``0.0000001``)."""
    if isinstance(value, int):
        return str(value)
    text = repr(value)
    if "e" in text or "E" in text:
        return format(Decimal(text), "f")
    return text


def value_text(value: Any, state_type: Any) -> str:
    """A stored ``initial_value`` as the row's input shows it: by its type when it fits,
    otherwise (or for an unknown type) compact JSON."""
    if state_type == NUMBER and _is_number(value):
        return number_text(value)
    if state_type == BOOLEAN and isinstance(value, bool):
        return "true" if value else "false"
    if state_type in (ENUM, TEXT) and isinstance(value, str):
        return value
    return _json(value)


def state_to_write(entry: Any) -> Any:
    """One ``ScenarioState`` normalised as the form normalises its row (see the module
    docstring); a non-object entry is kept as is. Duplicates are kept, so ``ALLOWED_DUPLICATE``
    reports them rather than the editor fixing them silently."""
    if not isinstance(entry, Mapping):
        return entry
    out: dict[str, Any] = {}
    order = [*STATE_MEMBERS, *(k for k in entry if k not in STATE_MEMBERS)]
    for key in order:
        if key not in entry:
            continue
        value = entry[key]
        if key in TEXT_MEMBERS and isinstance(value, str):
            value = value.strip()
        elif key == "aliases":
            value = _lines(value)
        elif key == "constraints" and isinstance(value, Mapping):
            members = [*CONSTRAINT_MEMBERS, *(k for k in value if k not in CONSTRAINT_MEMBERS)]
            constraints: dict[str, Any] = {}
            for member in members:
                item = value.get(member)
                if member == "allowed_values":
                    item = _lines(item)
                if item is not None and item != [] and item != {}:
                    constraints[member] = item
            value = constraints
        if value is None or value == "" or value == [] or value == {}:
            continue
        out[key] = value
    return out


def state_editor_row(entry: Any) -> dict[str, Any]:
    """A normalised entry as its row's posted values (``None``: no value)."""
    if not isinstance(entry, Mapping):
        return {}
    row: dict[str, Any] = {}
    for key in (*TEXT_MEMBERS, "type", "scope", "visibility", "owner"):
        value = entry.get(key)
        if value is not None:
            row[key] = value if isinstance(value, str) else _json(value)
    aliases = entry.get("aliases")
    if isinstance(aliases, list):
        row["aliases"] = "\n".join(str(a) for a in aliases)
    constraints = entry.get("constraints")
    if isinstance(constraints, Mapping):
        for member in ("min", "max", "tolerance"):
            value = constraints.get(member)
            if value is not None:
                row[f"constraints_{member}"] = (
                    number_text(value) if _is_number(value) else _json(value)
                )
        non_negative = constraints.get("non_negative")
        if isinstance(non_negative, bool):
            row["constraints_non_negative"] = "true" if non_negative else "false"
        allowed = constraints.get("allowed_values")
        if isinstance(allowed, list):
            row["constraints_allowed_values"] = "\n".join(str(v) for v in allowed)
    if "initial_value" in entry and entry["initial_value"] is not None:
        row["initial_value"] = value_text(entry["initial_value"], entry.get("type"))
        row["initial_value_raw"] = _json(entry["initial_value"])
    if isinstance(entry.get("type"), str):
        row["type_raw"] = entry["type"]
    silent = entry.get("silent")
    if isinstance(silent, bool):
        row["silent"] = "true" if silent else "false"
    for box in BOXES:
        if entry.get(box) is not None:
            row[box] = _box_text(entry[box])
    minutes = entry.get("reservation_expiry_minutes")
    if minutes is not None:
        row["reservation_expiry_minutes"] = str(minutes) if _is_number(minutes) else _json(minutes)
    return row


def _compared(name: str, value: Any) -> str:
    """A posted field value as the untouched-row match compares it: text with CRLF → LF and
    surrounding whitespace stripped, a JSON box as its parsed object when it parses."""
    text = value.replace("\r\n", "\n").strip() if isinstance(value, str) else ""
    if name in BOXES and text:
        try:
            return json.dumps(json.loads(text), sort_keys=True)
        except ValueError:
            return text
    return text


def rendering(values: Mapping[str, Any]) -> tuple[str, ...]:
    """A row's comparable rendering (:data:`ROW_FIELDS` order) from its posted values."""
    return tuple(_compared(name, values.get(name)) for name in ROW_FIELDS)


# --- the row --------------------------------------------------------------------------------------


def _entity_choices(
    roles: Sequence[str], locations: Sequence[str], entities: Sequence[str], keep: str
) -> list[Any]:
    """``NONE_LABEL``, then the "Roles", "Locations" and "Entities" groups (each id labelled
    by itself; an id shared by two kinds is in each); a posted id none has is kept as
    "<id> (missing)"."""
    choices: list[Any] = [("", NONE_LABEL)]
    known: set[str] = set()
    for label, ids in (("Roles", roles), ("Locations", locations), ("Entities", entities)):
        if ids:
            choices.append((label, [(v, v) for v in ids]))
            known.update(ids)
    if keep and keep not in known:
        choices.append((keep, f"{keep}{MISSING_SUFFIX}"))
    return choices


class ScenarioStateForm(StateDefinitionFields):
    """One ``state`` row (see the module docstring). ``roles``, ``locations`` and ``entities``
    are the form's own ids in the current form state, which the Entity select offers."""

    def __init__(
        self,
        *args: Any,
        roles: Sequence[str] = (),
        locations: Sequence[str] = (),
        entities: Sequence[str] = (),
        stored: Sequence[Any] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.untouched: Any = None  # the draft entry this row is, when it is untouched
        self.fields["name"].required = False  # "A State needs its name" is the row set's
        for name, choices in (
            ("type", TYPE_CHOICES),
            ("scope", SCOPE_CHOICES),
            ("visibility", VISIBILITY_CHOICES),
            ("owner", OWNER_CHOICES),
        ):
            field = cast("forms.ChoiceField", self.fields[name])
            field.choices = kept_choices(choices, self.raw(name) or None)
        self.fields["entity_id"] = forms.ChoiceField(
            label="Entity",
            required=False,
            choices=_entity_choices(roles, locations, entities, self.raw("entity_id")),
            help_text=ENTITY_SLUG_HELP,
        )
        self.fields["external_value_name"] = forms.CharField(
            label="Provider value name",
            required=False,
            strip=True,
            help_text=EXTERNAL_VALUE_HELP,
        )
        for name in SNAPSHOT_FIELDS:
            self.fields[name] = forms.CharField(
                required=False, strip=False, widget=forms.HiddenInput
            )

    def raw(self, name: str) -> str:
        """A field's posted value, stripped (``""`` when absent)."""
        value = self.data.get(self.add_prefix(name)) if self.is_bound else None
        return value.strip() if isinstance(value, str) else ""

    def posted(self) -> dict[str, Any]:
        return {
            name: self.data.get(self.add_prefix(name)) if self.is_bound else None
            for name in ROW_FIELDS
        }

    @property
    def blank(self) -> bool:
        """Every field blank (the snapshots aside): dropped."""
        return not any(_compared(n, v) for n, v in self.posted().items())

    def rendering(self) -> tuple[str, ...]:
        return rendering(self.posted())

    # --- the groups the partial renders -----------------------------------------------------

    def row_groups(self) -> list[tuple[str, list[forms.BoundField]]]:
        return [(legend, [self[n] for n in names]) for legend, names in ROW_GROUPS]

    def boxes(self) -> list[forms.BoundField]:
        return [self[name] for name in BOXES]

    def snapshots(self) -> list[forms.BoundField]:
        return [self[name] for name in SNAPSHOT_FIELDS]

    @property
    def details_open(self) -> bool:
        """The "Rules, authorization and transfer" block opens when a box is set or has an
        error."""
        return any(self.raw(name) or name in self.errors for name in BOXES)

    # --- cleaning ---------------------------------------------------------------------------

    def clean_name(self) -> str:
        name = str(self.cleaned_data.get("name") or "").strip()
        if name and not STATE_NAME.fullmatch(name):
            raise forms.ValidationError(STATE_NAME_INVALID, code="invalid")
        return name

    def clean_entity_id(self) -> str | None:
        return str(self.cleaned_data.get("entity_id") or "").strip() or None

    def clean_external_value_name(self) -> str | None:
        value = str(self.cleaned_data.get("external_value_name") or "").strip()
        if not value:
            return None
        if not STATE_NAME.fullmatch(value):
            raise forms.ValidationError(STATE_NAME_INVALID, code="invalid")
        return value

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        story_owned = cleaned.get("owner") != EXTERNAL
        named = self._present("owner", "external_value_name") and cleaned["external_value_name"]
        if named and story_owned:
            self.add_error("external_value_name", PROVIDER_STORY_OWNED)
        return cleaned

    def _snapshot(self) -> tuple[bool, Any]:
        """``(True, stored value)`` when the value's text and the type are both the rendered
        snapshot's: the stored ``initial_value`` is kept without re-parsing."""
        raw = self.data.get(self.add_prefix("initial_value_raw")) if self.is_bound else None
        if not isinstance(raw, str) or not raw:
            return False, None
        if self.raw("type") != self.raw("type_raw"):
            return False, None
        try:
            stored = json.loads(raw)
        except ValueError:
            return False, None
        posted = self.data.get(self.add_prefix("initial_value"))
        if posted != value_text(stored, self.raw("type_raw")):
            return False, None
        return True, stored

    def _initial(self, cleaned: dict[str, Any], state_type: str) -> None:
        """#261's rule by the chosen type, but the stored value kept when untouched, and a kept
        unknown type's text read as a strict JSON scalar."""
        if not self._present("initial_value"):
            return
        kept, stored = self._snapshot()
        if kept:
            cleaned["initial_value"] = stored
            return
        if state_type in STATE_TYPES:
            super()._initial(cleaned, state_type)
            return
        text = str(cleaned.get("initial_value") or "").strip()
        if not text:
            cleaned["initial_value"] = None
            return
        try:
            value = json.loads(text)
        except ValueError:
            value = None
        if not isinstance(value, bool | int | float | str):
            self.add_error("initial_value", SCALAR_INVALID)
            return
        cleaned["initial_value"] = value

    def state(self) -> Any:
        """The ``ScenarioState`` sent (normalised by :func:`state_to_write`): the draft entry
        itself when the row is untouched."""
        if self.untouched is not None:
            return self.untouched
        cleaned = self.cleaned_data
        constraints = {
            member: cleaned.get(f"constraints_{member}") for member in CONSTRAINT_MEMBERS
        }
        entry = {
            member: constraints if member == "constraints" else cleaned.get(member)
            for member in STATE_MEMBERS
        }
        return state_to_write(entry)


class StateFormSet(BaseFormSet):  # type: ignore[type-arg]
    """The ``state`` rows' checks, on save only (see the module docstring): the untouched rows
    found and their errors dropped, then the three name checks on every other row, each on
    ``name`` and only when it has no error yet."""

    def clean(self) -> None:
        stored: list[Any] = list(self.form_kwargs.get("stored") or ())
        pool = [(rendering(state_editor_row(entry)), entry) for entry in stored]
        rows = cast("list[ScenarioStateForm]", self.forms)
        for form in rows:
            if form.blank:
                continue
            key = form.rendering()
            for k, (shown, entry) in enumerate(pool):
                if shown == key:
                    form.untouched = entry
                    del pool[k]
                    errors = getattr(form, "_errors", None)
                    if isinstance(errors, ErrorDict):
                        errors.clear()
                    break
        names: dict[str, list[int]] = {}
        for index, form in enumerate(rows):
            name = form.raw("name")
            if name and not form.blank:
                names.setdefault(name, []).append(index)
        for index, form in enumerate(rows):
            if form.blank or form.untouched is not None or "name" in form.errors:
                continue
            name = form.raw("name")
            if not name:
                form.add_error("name", NAME_NEEDED)
            elif name == WORLD_CLOCK:
                form.add_error("name", WORLD_CLOCK_RESERVED)
            else:
                other = _other_row(names[name], index, rows)
                if other is not None:
                    form.add_error("name", NAME_USED.format(row=other + 1))


def _other_row(indices: Iterable[int], index: int, rows: Sequence[ScenarioStateForm]) -> int | None:
    """The row a changed row ``index``'s name repeats: the first earlier row with it, else the
    first later untouched one (a later changed row reports the repeat itself)."""
    others = [j for j in indices if j != index]
    earlier = [j for j in others if j < index]
    if earlier:
        return earlier[0]
    later = [j for j in others if rows[j].untouched is not None]
    return later[0] if later else None

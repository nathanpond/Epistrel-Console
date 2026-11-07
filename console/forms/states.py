"""The one-State page's GET form (#242): D4's viewpoint picker plus D7's "As of" boundary; the
Set form (#251), :class:`StateSetForm`; and the State definition fields with the Declare form
(#261), :class:`StateDefinitionFields`, :func:`definition_body` and
:class:`StateDeclarationForm` (#266 and #267 import them); and the definition change (#265),
:class:`StateDefinitionForm`."""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from django import forms
from django.core import signing
from django.http import QueryDict

from console.canon import cast_names
from console.cast import Character
from console.confirm import KEY_FIELD
from console.engine.client import EngineError
from console.engine.models import StoryState
from console.forms.fields import JsonBoxField
from console.forms.inspect import SUBJECT_INVALID
from console.forms.world import NUMBER_INVALID, BoundaryField, WorldViewpointForm, parse_number
from console.ids import is_ulid, new_ulid
from console.inspect import short_id
from console.merge_patch import diff
from console.paging import CHARACTER_INVALID
from console.states import CHARACTER_SCOPE, STATE_NAME_PATTERN, STORY_SCOPE
from console.wording import character_label, humanised
from console.world import BOOLEAN, ENUM, EXPECTED_REVISION_FIELD, NUMBER, TEXT

AS_OF_HELP = "A turn (12), or a turn and slot (12+1); blank for now"
AS_OF_FIELD = "query.as_of"
HEAD_TEXT = "The story's head is turn {head}"


class StateAsOfForm(WorldViewpointForm):
    """``viewpoint`` (D4) and ``as_of`` (D7's ``BoundaryField``, optional: blank sends no
    ``as_of``, ``12⁺¹`` is sent as ``12+1``). No HTML ``min``/``max`` or ``maxlength`` (C10)."""

    as_of = BoundaryField(
        label="As of",
        required=False,
        help_text=AS_OF_HELP,
        widget=forms.TextInput(),
    )

    def query(self) -> dict[str, Any]:
        """The read's ``viewpoint`` and ``as_of`` keyword arguments (valid form only)."""
        return {**super().query(), "as_of": self.cleaned_data.get("as_of") or None}

    def apply_engine_error(self, exc: EngineError) -> None:
        """A ``validation_error`` on ``query.as_of`` beside As of ("The story's head is turn
        <head>" when ``details.head`` is an int, else the Engine's message); anything else as
        D4's form places it."""
        details = exc.details if isinstance(exc.details, dict) else {}
        if exc.code == "validation_error" and details.get("field") == AS_OF_FIELD:
            head = details.get("head")
            if isinstance(head, int) and not isinstance(head, bool):
                self.add_error("as_of", HEAD_TEXT.replace("{head}", str(head)))
            else:
                self.add_error("as_of", exc.message)
            return
        super().apply_engine_error(exc)


# --- set a State (#251) ---------------------------------------------------------------------------
#
# ``StateSetForm`` is built from the State the page read (omniscient ``get_state``: its ``type``,
# ``scope`` and ``owner``) and the story's cast. Every check happens before any write: the Engine
# is left only what the read doesn't expose (derived rules, ``authorization.sources``,
# constraints, an enum's allowed values), worded by D9's ``world_refusal``. Numbers go through
# #235's ``parse_number`` (D5), never redefined here; no HTML ``min``/``max``/``maxlength`` (C10).

CHOOSE_CHARACTER = "Choose a character — this State is per character"
CHOOSE_CHARACTER_LABEL = "Choose a character"
ENTER_AMOUNT = "Enter an amount, e.g. 5 or -3"
ENTER_VALUE = "Enter a value."
GIVE_REASON = "Give a reason."
NO_VALUE_YET = "This State has no value yet — set a value before changing it by an amount"
TEXT_TOO_LONG = "At most 200 characters."
NOT_IN_CAST = "{short} (not in the cast)"
CHANGE_HELP = "Set to uses Value; Change by uses Amount — the other box is ignored."
TEXT_MAX = 200  # the Engine's value ``too_long`` and ``REASON_MAX`` (code points)
SET_VALUE, CHANGE_BY = "value", "delta"
SETTABLE_TYPES = (NUMBER, BOOLEAN, ENUM, TEXT)


def normalised_id(value: Any) -> str:
    """C13: an id from the URL or a free-text field, trimmed and upper-cased."""
    return value.strip().upper() if isinstance(value, str) else ""


def _decimal_input() -> forms.TextInput:
    return forms.TextInput(attrs={"inputmode": "decimal"})


class StateSetForm(forms.Form):
    """The Set form for one read State (see the block comment above). Fields, by the State:

    - ``change`` (number only): "Set to" (``value``) / "Change by" (``delta``), a required
      choice; other types always send ``value``;
    - ``value``: number (text, ``parse_number``), boolean (a "true"/"false" select), enum
      (stripped, required, sent verbatim), text (a textarea, sent as typed but for ``\\r\\n`` →
      ``\\n``, at most 200 code points);
    - ``delta`` ("Amount", number only): ``parse_number``, required when changing;
    - ``scope_key`` (per-character only): the cast in the Engine's order after a blank "Choose a
      character", plus a kept "<short id> (not in the cast)" option (C13); a free-text id
      (``is_ulid``) when the story read failed (``cast`` ``None``);
    - ``reason``: stripped, required, at most 200 code points.

    :attr:`body` is the ``StateSetRequest`` (valid form only)."""

    def __init__(
        self,
        data: QueryDict | None,
        *,
        state: StoryState,
        cast: Sequence[Character] | None,
        scope_key: str = "",
    ) -> None:
        self.state = state
        self.cast = cast
        self.state_type = state.type
        self.per_character = state.scope == CHARACTER_SCOPE
        initial: dict[str, Any] = {"change": SET_VALUE}
        if self.state_type == BOOLEAN:
            current = state.value if not self.per_character else None
            initial["value"] = "false" if current is False else "true"
        kept = normalised_id(scope_key)
        if data is not None:
            kept = normalised_id(data.get("scope_key")) or kept
        if kept:
            initial["scope_key"] = kept
        super().__init__(data, initial=initial)
        if self.state_type == NUMBER:
            self.fields["change"] = forms.ChoiceField(
                label="Change",
                choices=[(SET_VALUE, "Set to"), (CHANGE_BY, "Change by")],
                widget=forms.RadioSelect,
                help_text=CHANGE_HELP,
            )
            self.fields["value"] = forms.CharField(
                label="Value", required=False, widget=_decimal_input()
            )
            self.fields["delta"] = forms.CharField(
                label="Amount", required=False, widget=_decimal_input()
            )
        elif self.state_type == BOOLEAN:
            self.fields["value"] = forms.ChoiceField(
                label="Value", choices=[("true", "true"), ("false", "false")]
            )
        elif self.state_type == ENUM:
            self.fields["value"] = forms.CharField(label="Value", required=False)
        else:
            self.fields["value"] = forms.CharField(
                label="Value", required=False, strip=False, widget=forms.Textarea
            )
        if self.per_character:
            if cast is None:
                self.fields["scope_key"] = forms.CharField(label="Character id", required=False)
            else:
                # A select whose posted value is normalised and checked by ``clean_scope_key``
                # (C13), so a kept or forged id is an id error, never Django's choice error.
                self.fields["scope_key"] = forms.CharField(
                    label="Character",
                    required=False,
                    widget=forms.Select(choices=self._character_choices(kept)),
                )
        self.fields["reason"] = forms.CharField(label="Reason", required=False)

    def _character_choices(self, kept: str) -> list[tuple[str, str]]:
        names = cast_names(self.cast)
        choices = [("", CHOOSE_CHARACTER_LABEL)]
        choices += [(c.id, character_label(c.id, names)) for c in self.cast or ()]
        if kept and kept not in names:
            choices.append((kept, NOT_IN_CAST.replace("{short}", short_id(kept))))
        return choices

    @property
    def changing(self) -> bool:
        """Whether this is a "Change by" (a number State whose ``change`` is ``delta``)."""
        return self.state_type == NUMBER and self.cleaned_data.get("change") == CHANGE_BY

    def clean_scope_key(self) -> str:
        value = normalised_id(self.cleaned_data.get("scope_key"))
        if not value:
            raise forms.ValidationError(CHOOSE_CHARACTER, code="required")
        if not is_ulid(value):
            raise forms.ValidationError(CHARACTER_INVALID, code="invalid")
        return value

    def clean_reason(self) -> str:
        reason = str(self.cleaned_data.get("reason") or "")
        if not reason:
            raise forms.ValidationError(GIVE_REASON, code="required")
        if len(reason) > TEXT_MAX:
            raise forms.ValidationError(TEXT_TOO_LONG, code="max_length")
        return reason

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if self.state_type == NUMBER:
            if "change" not in cleaned:
                return cleaned  # Django's required error: no write
            name = CHANGE_BY if cleaned["change"] == CHANGE_BY else "value"
            text = str(cleaned.get(name) or "")
            if name == CHANGE_BY and not text:
                self.add_error(name, ENTER_AMOUNT)
                return cleaned
            number = parse_number(text)
            if number is None:
                self.add_error(name, NUMBER_INVALID)
            else:
                cleaned[name] = number
        elif self.state_type == BOOLEAN:
            if "value" in cleaned:
                cleaned["value"] = cleaned["value"] == "true"
        elif self.state_type == ENUM:
            if not cleaned.get("value"):
                self.add_error("value", ENTER_VALUE)
        else:
            text = str(cleaned.get("value") or "").replace("\r\n", "\n")
            if not text:
                self.add_error("value", ENTER_VALUE)
            elif len(text) > TEXT_MAX:
                self.add_error("value", TEXT_TOO_LONG)
            else:
                cleaned["value"] = text
        return cleaned

    @property
    def body(self) -> dict[str, Any]:
        """``{"value": v, "reason": r}`` or ``{"delta": d, "reason": r}``, plus ``scope_key`` for
        a per-character State (the other mode's input is never sent)."""
        cleaned = self.cleaned_data
        body: dict[str, Any] = (
            {"delta": cleaned[CHANGE_BY]} if self.changing else {"value": cleaned["value"]}
        )
        if self.per_character:
            body["scope_key"] = cleaned["scope_key"]
        body["reason"] = cleaned["reason"]
        return body


# --- declare a State (#261) -----------------------------------------------------------------------
#
# ``StateDefinitionFields`` holds every member of the Engine's ``StateDeclaration`` (core/states/
# api.py, ``extra="forbid"``) as a field named after it, the constraints flattened as
# ``constraints_<member>``. Its checks mirror ``validate_definition`` (core/states/model.py) one for
# one, so the common mistakes make no call; anything else is the Engine's 422, placed by
# ``apply_engine_errors``. ``clean()`` skips a check whose fields are absent, so #266 (approve with
# changes) and #267 (a scenario's starting States) can reuse the field set with fields removed or
# added. There is no ``external_value_name`` field: the Engine takes an external State's provider
# value of the State's own name. No HTML ``min``/``max``/``maxlength`` (C10).

STATE_NAME_INVALID = "Start with a letter, then letters, digits and underscores (at most 80)"
CHOOSE_TYPE = "Choose a type"
PROVIDER_REQUIRED = "Name the provider that owns this State"
PROVIDER_STORY_OWNED = "Only an external State names a provider"
EXTERNAL_RULES = "An external State has no rules"
NUMBER_ONLY = "Only a number State has bounds"
MIN_ABOVE_MAX = "Min is above Max"
ALLOWED_REQUIRED = "List the allowed values, one per line"
ALLOWED_DUPLICATE = "Each allowed value appears once"
ENUM_ONLY = "Only an enum State lists allowed values"
BOOLEAN_INVALID = "Enter true or false"
NOT_ALLOWED = "That isn't one of the allowed values"
MINUTES_INVALID = "Enter a whole number of minutes"

PROVIDER_HELP = "A registered provider's name"
NUMBER_ONLY_HELP = "Number States only"
ALLOWED_HELP = "Enum States only — one per line"
INITIAL_HELP = "Blank: undefined until set. Number, true/false, one of the allowed values, or text"
ENTITY_HELP = "An entity id (26 characters)"
ALIASES_HELP = "One per line"
BOX_HELP = "A JSON object (blank sends none)"
MINUTES_HELP = "Minutes before a reservation lapses"

STATE_NAME = re.compile(STATE_NAME_PATTERN)
STATE_TYPES = (NUMBER, BOOLEAN, ENUM, TEXT)
EXTERNAL = "external"
BOXES = ("rules", "authorization", "transfer")
# The flattened constraint fields, in the Engine's ``StateConstraintsBody`` member order.
CONSTRAINT_MEMBERS = ("min", "max", "non_negative", "allowed_values", "tolerance")
BOUNDS = ("constraints_min", "constraints_max", "constraints_tolerance")

TYPE_CHOICES = [("", CHOOSE_TYPE)] + [(t, humanised(t)) for t in STATE_TYPES]
SCOPE_CHOICES = [
    ("", "Engine default (story)"),
    (STORY_SCOPE, "Story"),
    (CHARACTER_SCOPE, "Per character"),
]
VISIBILITY_CHOICES = [
    ("", "Engine default (player visible)"),
    ("player_visible", "Player visible"),
    ("hidden", "Hidden"),
]
OWNER_CHOICES = [("", "Engine default (story)"), ("story", "Story"), (EXTERNAL, "External")]
BOOLEAN_CHOICES = [("", "Engine default (false)"), ("true", "true"), ("false", "false")]


def _lines(text: Any) -> list[str]:
    """A one-per-line textarea: each line stripped, blank lines dropped, in order."""
    if not isinstance(text, str):
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _choice_bool(value: Any) -> bool | None:
    """A C7 boolean select: "" → ``None`` (not sent), "true"/"false" → the bool."""
    return {"true": True, "false": False}.get(value) if isinstance(value, str) else None


def _number_field(label: str) -> forms.CharField:
    return forms.CharField(
        label=label, required=False, help_text=NUMBER_ONLY_HELP, widget=_decimal_input()
    )


class StateDefinitionFields(forms.Form):
    """Every State definition field (see the block comment above); :func:`definition_body`
    builds the body from its ``cleaned_data``."""

    # Identity
    name = forms.CharField(label="Name", strip=True)
    type = forms.ChoiceField(
        label="Type", choices=TYPE_CHOICES, error_messages={"required": CHOOSE_TYPE}
    )
    scope = forms.ChoiceField(label="Scope", required=False, choices=SCOPE_CHOICES)
    entity_id = forms.CharField(label="Entity", required=False, help_text=ENTITY_HELP)
    # Shown as
    unit = forms.CharField(label="Unit", required=False, strip=True)
    aliases = forms.CharField(
        label="Aliases",
        required=False,
        help_text=ALIASES_HELP,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    visibility = forms.ChoiceField(label="Visibility", required=False, choices=VISIBILITY_CHOICES)
    silent = forms.ChoiceField(label="Silent", required=False, choices=BOOLEAN_CHOICES)
    # Owner
    owner = forms.ChoiceField(label="Owner", required=False, choices=OWNER_CHOICES)
    provider_id = forms.CharField(
        label="Provider", required=False, strip=True, help_text=PROVIDER_HELP
    )
    # Constraints
    constraints_min = _number_field("Min")
    constraints_max = _number_field("Max")
    constraints_tolerance = _number_field("Tolerance")
    constraints_non_negative = forms.ChoiceField(
        label="Non-negative", required=False, choices=BOOLEAN_CHOICES, help_text=NUMBER_ONLY_HELP
    )
    constraints_allowed_values = forms.CharField(
        label="Allowed values",
        required=False,
        help_text=ALLOWED_HELP,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    # Initial value
    initial_value = forms.CharField(
        label="Initial value", required=False, strip=False, help_text=INITIAL_HELP
    )
    # Effect rules
    rules = JsonBoxField(dict, empty=None, required=False, label="Rules", help_text=BOX_HELP)
    authorization = JsonBoxField(
        dict, empty=None, required=False, label="Authorization", help_text=BOX_HELP
    )
    transfer = JsonBoxField(dict, empty=None, required=False, label="Transfer", help_text=BOX_HELP)
    reservation_expiry_minutes = forms.CharField(
        label="Reservation expiry (minutes)", required=False, strip=True, help_text=MINUTES_HELP
    )

    # Field groups, in page order (the template renders each as a <fieldset>).
    GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Identity", ("name", "type", "scope", "entity_id")),
        ("Shown as", ("unit", "aliases", "visibility", "silent")),
        ("Owner", ("owner", "provider_id")),
        (
            "Constraints",
            (
                "constraints_min",
                "constraints_max",
                "constraints_tolerance",
                "constraints_non_negative",
                "constraints_allowed_values",
            ),
        ),
        ("Initial value", ("initial_value",)),
        ("Effect rules", ("rules", "authorization", "transfer", "reservation_expiry_minutes")),
    )

    def groups(self) -> list[tuple[str, list[forms.BoundField]]]:
        """``(legend, bound fields)`` for each group with a field present."""
        found = []
        for legend, names in self.GROUPS:
            fields = [self[name] for name in names if name in self.fields]
            if fields:
                found.append((legend, fields))
        return found

    def clean_name(self) -> str:
        name = str(self.cleaned_data.get("name") or "")
        if not STATE_NAME.fullmatch(name):
            raise forms.ValidationError(STATE_NAME_INVALID, code="invalid")
        return name

    def clean_entity_id(self) -> str | None:
        value = normalised_id(self.cleaned_data.get("entity_id"))
        if not value:
            return None
        if not is_ulid(value):
            raise forms.ValidationError(SUBJECT_INVALID, code="invalid")
        return value

    def clean_aliases(self) -> list[str]:
        return _lines(self.cleaned_data.get("aliases"))

    def clean_constraints_allowed_values(self) -> list[str]:
        return _lines(self.cleaned_data.get("constraints_allowed_values"))

    def _number(self, name: str) -> int | float | None:
        text = str(self.cleaned_data.get(name) or "").strip()
        if not text:
            return None
        number = parse_number(text)
        if number is None:
            raise forms.ValidationError(NUMBER_INVALID, code="invalid")
        return number

    def clean_constraints_min(self) -> int | float | None:
        return self._number("constraints_min")

    def clean_constraints_max(self) -> int | float | None:
        return self._number("constraints_max")

    def clean_constraints_tolerance(self) -> int | float | None:
        return self._number("constraints_tolerance")

    def clean_constraints_non_negative(self) -> bool | None:
        return _choice_bool(self.cleaned_data.get("constraints_non_negative"))

    def clean_silent(self) -> bool | None:
        return _choice_bool(self.cleaned_data.get("silent"))

    def clean_reservation_expiry_minutes(self) -> int | None:
        text = str(self.cleaned_data.get("reservation_expiry_minutes") or "")
        if not text:
            return None
        if not (text.isascii() and text.isdigit()):
            raise forms.ValidationError(MINUTES_INVALID, code="invalid")
        return int(text)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        self._check_owner(cleaned)
        state_type = cleaned.get("type") if "type" in self.fields else None
        number_only = self._check_bounds(cleaned, state_type)
        if not number_only and self._present("constraints_min", "constraints_max"):
            low, high = cleaned.get("constraints_min"), cleaned.get("constraints_max")
            if low is not None and high is not None and low > high:
                self.add_error("constraints_min", MIN_ABOVE_MAX)
        if state_type:
            self._check_allowed(cleaned, state_type)
            self._initial(cleaned, state_type)
        return cleaned

    def _present(self, *names: str) -> bool:
        """Every one of ``names`` is a field of this form and cleaned without error."""
        return all(n in self.fields and n in self.cleaned_data for n in names)

    def _check_owner(self, cleaned: dict[str, Any]) -> None:
        if not self._present("owner", "provider_id"):
            return
        external = cleaned.get("owner") == EXTERNAL
        provider = cleaned.get("provider_id")
        if external and not provider:
            self.add_error("provider_id", PROVIDER_REQUIRED)
        elif provider and not external:
            self.add_error("provider_id", PROVIDER_STORY_OWNED)
        if external and self._present("rules") and cleaned.get("rules"):
            self.add_error("rules", EXTERNAL_RULES)  # ``validate_definition`` checks a truthy one

    def _check_bounds(self, cleaned: dict[str, Any], state_type: Any) -> bool:
        """``NUMBER_ONLY`` on each bound filled for a known non-number type; whether any fired."""
        if not state_type or state_type == NUMBER:
            return False
        fired = False
        for name in BOUNDS:
            if self._present(name) and cleaned.get(name) is not None:
                self.add_error(name, NUMBER_ONLY)
                fired = True
        if self._present("constraints_non_negative") and cleaned.get("constraints_non_negative"):
            self.add_error("constraints_non_negative", NUMBER_ONLY)
            fired = True
        return fired

    def _check_allowed(self, cleaned: dict[str, Any], state_type: str) -> None:
        if not self._present("constraints_allowed_values"):
            return
        values = cleaned.get("constraints_allowed_values") or []
        if state_type != ENUM:
            if values:
                self.add_error("constraints_allowed_values", ENUM_ONLY)
        elif not values:
            self.add_error("constraints_allowed_values", ALLOWED_REQUIRED)
        elif len(set(values)) != len(values):
            self.add_error("constraints_allowed_values", ALLOWED_DUPLICATE)

    def _initial(self, cleaned: dict[str, Any], state_type: str) -> None:
        """The initial value parsed by the chosen type (blank, or only whitespace: not sent)."""
        if not self._present("initial_value"):
            return
        raw = cleaned.get("initial_value")
        text = raw if isinstance(raw, str) else ""
        if not text.strip():
            cleaned["initial_value"] = None
            return
        value: Any
        if state_type == NUMBER:
            value = parse_number(text.strip())
            if value is None:
                self.add_error("initial_value", NUMBER_INVALID)
                return
        elif state_type == BOOLEAN:
            value = _choice_bool(text.strip())
            if value is None:
                self.add_error("initial_value", BOOLEAN_INVALID)
                return
        elif state_type == ENUM:
            value = text.strip()
            allowed = self.cleaned_data.get("constraints_allowed_values") or []
            if allowed and value not in allowed:
                self.add_error("initial_value", NOT_ALLOWED)
                return
        else:
            value = text
            if len(value) > TEXT_MAX:
                self.add_error("initial_value", TEXT_TOO_LONG)
                return
        cleaned["initial_value"] = value


def definition_body(cleaned: Mapping[str, Any]) -> dict[str, Any]:
    """A ``StateDeclaration`` from cleaned definition fields: ``name`` and ``type``, plus each
    optional member only when given (convention 5), in ``StateDeclaration``'s member order. A C7
    "Engine default" choice is never sent; an explicit choice equal to the default is."""
    body: dict[str, Any] = {"name": cleaned["name"], "type": cleaned["type"]}
    if cleaned.get("scope"):
        body["scope"] = cleaned["scope"]
    constraints: dict[str, Any] = {}
    for member in CONSTRAINT_MEMBERS:
        value = cleaned.get(f"constraints_{member}")
        if value is not None and value != []:
            constraints[member] = value
    if constraints:
        body["constraints"] = constraints
    if cleaned.get("initial_value") is not None:
        body["initial_value"] = cleaned["initial_value"]
    if cleaned.get("aliases"):
        body["aliases"] = list(cleaned["aliases"])
    for member in ("unit", "entity_id", "visibility", "owner", "provider_id"):
        if cleaned.get(member):
            body[member] = cleaned[member]
    if cleaned.get("silent") is not None:
        body["silent"] = cleaned["silent"]
    for member in BOXES:
        if cleaned.get(member) is not None:
            body[member] = cleaned[member]
    if cleaned.get("reservation_expiry_minutes") is not None:
        body["reservation_expiry_minutes"] = cleaned["reservation_expiry_minutes"]
    return body


class StateDeclarationForm(StateDefinitionFields):
    """The Declare a State form (#261): the definition fields plus the hidden
    ``idempotency_key`` (a fresh ULID per unbound render, #43) and D8's ``expected_revision``
    (``initial_revision``: the shell's ``Story.timeline_revision``; blank when unknown)."""

    idempotency_key = forms.CharField(required=False, widget=forms.HiddenInput)
    expected_revision = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(
        self,
        data: Any = None,
        *,
        initial_revision: int | None,
        idempotency_key: str | None = None,
        initial: Mapping[str, Any] | None = None,
    ) -> None:
        kept = dict(initial or {})
        kept[KEY_FIELD] = idempotency_key or new_ulid()
        kept[EXPECTED_REVISION_FIELD] = "" if initial_revision is None else str(initial_revision)
        super().__init__(data, initial=kept)

    @property
    def definition(self) -> dict[str, Any]:
        """The ``StateDeclaration`` body (valid form only)."""
        return definition_body(self.cleaned_data)


# --- approve a proposed State (#266) --------------------------------------------------------------
#
# #261's form without Owner and Provider (the Engine's ``StateDefinitionOverrides`` has neither),
# starting from the proposal's suggestion. ``overrides()`` diffs the five suggested members
# against the suggestion and adds every other member given (#261's body rules); untouched it is
# ``None`` and the approval sends ``{}`` (C9). A blank Unit or Entity keeps the suggestion: there
# is no control that clears one, and ``null`` is never sent (an explicit ``null`` override would
# clear it at the Engine).

KEEPS_SUGGESTION_HELP = "Blank keeps the suggestion"
SCOPE_SUGGESTION_HELP = "Engine default keeps the suggestion"
ENTITY_SUGGESTED_HELP = "Blank keeps the suggestion. Suggested: {label}"
SUGGESTED_MEMBERS = ("name", "type", "scope", "unit", "entity_id")
OVERRIDES_DROPPED = ("owner", "provider_id")


def _kept_choice(choices: Sequence[tuple[str, str]], value: Any) -> list[tuple[str, str]]:
    """C7's ``_kept`` pattern: a suggested value outside the choices is one more choice."""
    kept = list(choices)
    if isinstance(value, str) and value and value not in {v for v, _ in kept}:
        kept.append((value, humanised(value)))
    return kept


class StateOverridesForm(StateDeclarationForm):
    """Approve a proposed State (#266): :class:`StateDeclarationForm` without ``owner`` and
    ``provider_id``, its Name, Type, Scope, Unit and Entity starting from ``suggestion``."""

    def __init__(
        self,
        data: Any = None,
        *,
        suggestion: Any,
        initial_revision: int | None,
        idempotency_key: str | None = None,
        initial: Mapping[str, Any] | None = None,
        names: Mapping[str, str] | None = None,
    ) -> None:
        self.suggestion = suggestion
        started: dict[str, Any] = {
            "name": suggestion.name,
            "type": suggestion.suggested_type,
            "scope": suggestion.suggested_scope,
            "unit": suggestion.suggested_unit or "",
            "entity_id": suggestion.suggested_entity_id or "",
        }
        started.update(initial or {})
        super().__init__(
            data,
            initial_revision=initial_revision,
            idempotency_key=idempotency_key,
            initial=started,
        )
        for name in OVERRIDES_DROPPED:
            self.fields.pop(name, None)
        type_field = cast("forms.ChoiceField", self.fields["type"])
        scope_field = cast("forms.ChoiceField", self.fields["scope"])
        type_field.choices = _kept_choice(TYPE_CHOICES, suggestion.suggested_type)
        scope_field.choices = _kept_choice(SCOPE_CHOICES, suggestion.suggested_scope)
        scope_field.help_text = SCOPE_SUGGESTION_HELP
        self.fields["unit"].help_text = KEEPS_SUGGESTION_HELP
        entity = suggestion.suggested_entity_id
        if entity:
            label = (names or {}).get(entity) or short_id(entity)
            self.fields["entity_id"].help_text = ENTITY_SUGGESTED_HELP.replace("{label}", label)
        else:
            self.fields["entity_id"].help_text = f"{KEEPS_SUGGESTION_HELP}. {ENTITY_HELP}"

    def _suggested(self, member: str) -> Any:
        s = self.suggestion
        return {
            "name": s.name,
            "type": s.suggested_type,
            "scope": s.suggested_scope,
            "unit": s.suggested_unit,
            "entity_id": s.suggested_entity_id,
        }[member]

    def overrides(self) -> dict[str, Any] | None:
        """The ``StateDefinitionOverrides`` (valid form only): each suggested member that
        differs from the suggestion (a blank Scope, Unit or Entity counts as unchanged), plus
        every other member given; ``None`` when nothing is."""
        cleaned = self.cleaned_data
        body = {k: v for k, v in definition_body(cleaned).items() if k not in SUGGESTED_MEMBERS}
        changed: dict[str, Any] = {}
        for member in SUGGESTED_MEMBERS:
            value = cleaned.get(member)
            if value is None or value == "":
                continue  # Name and Type are required; a blank optional one keeps the suggestion
            if value != self._suggested(member):
                changed[member] = value
        merged = {**changed, **body}
        return merged or None


# --- change a State's definition (#265, #329) ----------------------------------------------------
#
# The form starts from the State's stored definition (#329: ``GET …/states/{name}/definition``).
# Every mutable member is pre-filled from the *normalised* members (:func:`definition_members`),
# which are also signed into the snapshot with the read's ``config_revision`` (D8's base); the
# patch is ``console.merge_patch.diff`` of the snapshot's members against the cleaned form,
# normalised the same way (M3 convention 5, D14). So clearing a member resets it, editing part
# of an object sends only that part, and an untouched form sends nothing. No HTML
# ``min``/``max``/``maxlength`` (C10).

# The members ``PATCH …/definition`` may change, in the form's (and the flash's) order: #261's
# fieldsets Shown as | Constraints | Effect rules.
MUTABLE_MEMBERS = (
    "visibility",
    "unit",
    "aliases",
    "silent",
    "constraints",
    "rules",
    "authorization",
    "transfer",
    "reservation_expiry_minutes",
)
DEFINITION_BOXES = ("constraints", "rules", "authorization", "transfer")
EXPIRY = "reservation_expiry_minutes"
# v2 (#329): ``{name, members, config_revision}``; a #265-era (v1) form fails to verify.
DEFINITION_SNAPSHOT_SALT = "console.states.definition.snapshot.v2"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_KEYS = frozenset({"name", "members", "config_revision"})

NOTHING_CHANGED = "Nothing changed — nothing was sent"
RULES_KIND = "A State can't become, or stop being, derived — declare a new State instead"
SNAPSHOT_INVALID = "This form was incomplete — reload it and edit again"
INTERPRETIVE_NOTE = (
    "Constraints, Rules, Authorization, Transfer and Visibility are interpretive: turns checked "
    "under the old ones are re-checked"
)
PATCH_BOX_HELP = "Edit the stored value; clear the box to reset it to the default (`{}`)"
PATCH_ALIASES_HELP = "One per line; clear to remove them all"
PATCH_MINUTES_HELP = "Clear to remove it"

DEFINITION_VISIBILITY_CHOICES = [("player_visible", "Player visible"), ("hidden", "Hidden")]
SILENT_CHOICES = [("true", "Yes"), ("false", "No")]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _without_nulls(value: dict[str, Any]) -> dict[str, Any]:
    """``value`` with every ``null``-valued key removed, recursively through nested objects (a
    merge patch can't carry a ``null`` value: it would read as a removal)."""
    kept = {k: v for k, v in value.items() if v is not None}
    return {k: _without_nulls(v) if isinstance(v, dict) else v for k, v in kept.items()}


def definition_members(definition: Mapping[str, Any]) -> dict[str, Any]:
    """The nine mutable members of ``definition`` (a stored definition, or the cleaned form's
    values), normalised: a member at its default is left out (``{}`` for the four objects,
    ``[]`` for aliases, ``null``/``""`` for unit, ``null`` for the expiry), and ``visibility``
    and ``silent`` are always present. Odd stored values normalise so an untouched form always
    matches: a non-string visibility is ``None``; a non-string unit and an expiry that isn't a
    non-negative int (not a bool) are absent; aliases keep only their non-blank strings,
    stripped; a non-bool ``silent`` is ``False``; an object member has its ``null`` keys
    stripped, and one that isn't an object is kept as is (it pre-fills raw)."""
    visibility = definition.get("visibility")
    silent = definition.get("silent")
    members: dict[str, Any] = {
        "visibility": visibility if isinstance(visibility, str) else None,
        "silent": silent if isinstance(silent, bool) else False,
    }
    unit = definition.get("unit")
    if isinstance(unit, str) and unit:
        members["unit"] = unit
    aliases = definition.get("aliases")
    kept = (
        [a.strip() for a in aliases if isinstance(a, str) and a.strip()]
        if isinstance(aliases, list)
        else []
    )
    if kept:
        members["aliases"] = kept
    for member in DEFINITION_BOXES:
        value = definition.get(member)
        if isinstance(value, dict):
            value = _without_nulls(value)
            if value:
                members[member] = value
        elif value is not None:
            members[member] = value
    expiry = definition.get(EXPIRY)
    if isinstance(expiry, int) and not isinstance(expiry, bool) and expiry >= 0:
        members[EXPIRY] = expiry
    return {member: members[member] for member in MUTABLE_MEMBERS if member in members}


def _member_valid(member: str, value: Any) -> bool:
    """A snapshot member's JSON type (#329): an object box member is any non-null value;
    ``visibility`` a string (or ``None``, a stored definition without one); ``unit`` a string;
    ``aliases`` a list of strings; ``silent`` a bool; the expiry an int, not a bool."""
    if member in DEFINITION_BOXES:
        return value is not None
    if member == "visibility":
        return value is None or isinstance(value, str)
    if member == "unit":
        return isinstance(value, str)
    if member == "aliases":
        return isinstance(value, list) and all(isinstance(a, str) for a in value)
    if member == "silent":
        return isinstance(value, bool)
    return _is_int(value)


def sign_definition_snapshot(name: str, definition: Mapping[str, Any], config_revision: Any) -> str:
    """The signed ``{"name", "members", "config_revision"}`` the page rendered from: the
    normalised mutable members (:func:`definition_members`) and the read's ``config_revision``
    (an int, else ``None``: unknown)."""
    return signing.dumps(
        {
            "name": name,
            "members": definition_members(definition),
            "config_revision": config_revision if _is_int(config_revision) else None,
        },
        salt=DEFINITION_SNAPSHOT_SALT,
        compress=True,
    )


def read_definition_snapshot(value: Any, name: str) -> dict[str, Any] | None:
    """The snapshot ``value`` signs, or ``None`` when it doesn't verify (a #265-era one
    included), isn't exactly ``{name, members, config_revision}`` with valid members, or names
    another State."""
    try:
        raw = signing.loads(value if isinstance(value, str) else "", salt=DEFINITION_SNAPSHOT_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(raw, dict) or set(raw) != SNAPSHOT_KEYS or raw["name"] != name:
        return None
    members = raw["members"]
    if not isinstance(members, dict) or not set(members) <= set(MUTABLE_MEMBERS):
        return None
    if not all(_member_valid(member, value) for member, value in members.items()):
        return None
    revision = raw["config_revision"]
    if revision is not None and not _is_int(revision):
        return None
    return raw


def _box_initial(value: Any) -> Any:
    """An object box's pre-fill: a stored object as is (``JsonBoxField.prepare_value`` indents
    it), any other stored value as its raw JSON."""
    if value is None or isinstance(value, dict):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def _initial(members: Mapping[str, Any]) -> dict[str, Any]:
    """The form's initial values from the snapshot's members (never the raw definition, so
    pre-fill and diff base can't drift)."""
    initial: dict[str, Any] = {
        "unit": members.get("unit", ""),
        "aliases": "\n".join(members.get("aliases", [])),
        "silent": "true" if members.get("silent") is True else "false",
        EXPIRY: str(members[EXPIRY]) if EXPIRY in members else "",
    }
    if members.get("visibility") is not None:
        initial["visibility"] = members["visibility"]
    for member in DEFINITION_BOXES:
        initial[member] = _box_initial(members.get(member))
    return initial


def _box(label: str) -> JsonBoxField:
    return JsonBoxField(dict, empty=None, required=False, label=label, help_text=PATCH_BOX_HELP)


class StateDefinitionForm(forms.Form):
    """The Change definition form (#265, #329): one field per mutable member, named after it,
    pre-filled from the snapshot's members, plus the hidden ``snapshot`` and
    ``idempotency_key``. :attr:`patch` is the merge patch (valid form only); its keys are always
    a subset of :data:`MUTABLE_MEMBERS`."""

    visibility = forms.ChoiceField(label="Visibility", choices=DEFINITION_VISIBILITY_CHOICES)
    unit = forms.CharField(label="Unit", required=False, strip=True, help_text="Blank: no unit")
    aliases = forms.CharField(
        label="Aliases",
        required=False,
        help_text=PATCH_ALIASES_HELP,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    silent = forms.ChoiceField(label="Silent", choices=SILENT_CHOICES)
    constraints = _box("Constraints")
    rules = _box("Rules")
    authorization = _box("Authorization")
    transfer = _box("Transfer")
    reservation_expiry_minutes = forms.CharField(
        label="Reservation expiry (minutes)",
        required=False,
        strip=True,
        help_text=PATCH_MINUTES_HELP,
    )
    snapshot = forms.CharField(required=False, widget=forms.HiddenInput)
    idempotency_key = forms.CharField(required=False, widget=forms.HiddenInput)

    GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Shown as", ("visibility", "unit", "aliases", "silent")),
        ("Constraints", ("constraints",)),
        ("Effect rules", ("rules", "authorization", "transfer", EXPIRY)),
    )

    def __init__(
        self,
        data: Any = None,
        *,
        snapshot: Mapping[str, Any] | None,
        signed_snapshot: str,
        idempotency_key: str | None = None,
        initial: Mapping[str, Any] | None = None,
    ) -> None:
        members = (snapshot or {}).get("members")
        self.members: dict[str, Any] = dict(members) if isinstance(members, Mapping) else {}
        kept = dict(initial) if initial is not None else _initial(self.members)
        kept[SNAPSHOT_FIELD] = signed_snapshot
        kept[KEY_FIELD] = idempotency_key or new_ulid()
        super().__init__(data, initial=kept)
        # A read visibility outside the two choices stays selectable (humanised), so an
        # untouched form is still an empty patch.
        visibility = self.members.get("visibility")
        known = {value for value, _ in DEFINITION_VISIBILITY_CHOICES}
        if isinstance(visibility, str) and visibility and visibility not in known:
            field = self.fields["visibility"]
            if isinstance(field, forms.ChoiceField):
                field.choices = [
                    *DEFINITION_VISIBILITY_CHOICES,
                    (visibility, humanised(visibility)),
                ]

    def groups(self) -> list[tuple[str, list[forms.BoundField]]]:
        """``(legend, bound fields)`` per fieldset, in page order."""
        return [(legend, [self[name] for name in names]) for legend, names in self.GROUPS]

    def clean_aliases(self) -> list[str]:
        return _lines(self.cleaned_data.get("aliases"))

    def clean_silent(self) -> bool:
        return self.cleaned_data.get("silent") == "true"

    def clean_reservation_expiry_minutes(self) -> int | None:
        text = str(self.cleaned_data.get(EXPIRY) or "")
        if not text:
            return None
        if not (text.isascii() and text.isdigit()):
            raise forms.ValidationError(MINUTES_INVALID, code="invalid")
        return int(text)

    @property
    def patch(self) -> dict[str, Any]:
        """The merge patch: ``console.merge_patch.diff`` of the snapshot's members against the
        cleaned form, normalised the same way (:func:`definition_members`), in
        :data:`MUTABLE_MEMBERS` order. A cleared member is ``null`` (reset to its default), an
        edited object sends only its changed keys (a removed key ``null``), aliases are
        replaced whole, and an untouched or restored form is ``{}``."""
        cleaned = self.cleaned_data
        typed = definition_members({member: cleaned.get(member) for member in MUTABLE_MEMBERS})
        changes = diff(self.members, typed)
        return {member: changes[member] for member in MUTABLE_MEMBERS if member in changes}

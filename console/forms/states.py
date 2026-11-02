"""The one-State page's GET form (#242): D4's viewpoint picker plus D7's "As of" boundary; the
Set form (#251), :class:`StateSetForm`; and the State definition fields with the Declare form
(#261), :class:`StateDefinitionFields`, :func:`definition_body` and
:class:`StateDeclarationForm` (#266 and #267 import them)."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from django import forms
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

"""Direct a belief (#194): :class:`DirectBeliefForm` builds ``BeliefRequest`` for
``POST /admin/stories/{id}/beliefs`` (``console/views/inspect_beliefs.py``'s ``direct``).

Every check runs before any Engine call and all its errors show together; a refused form makes
no write. Field names are the Engine's (``holder``, ``proposition``, ``polarity``,
``confidence_level``, ``confidence_scalar``, ``disclosure``, ``attributed_confidence_level``),
except the nested chain, which is ``depth - 1`` ordered selects ``attributed_to_1`` …
``attributed_to_<n>`` (outermost first) sent as the list ``attributed_to``. A blank optional
field is left out of the body.

**Holder** is a select of the non-player cast (C4 labels), "Choose a character" first; an id
outside it (the player, an off-cast id, from the URL or the POST) is kept as an extra option,
last, so the Engine's refusal shows (C13). Without the story read it is an id field.
**Proposition** is a JSON object holding ``subject_id``, ``predicate_id`` and ``valid_time``
(non-null); deeper checks are the Engine's. **Scalar** is ``^\\d*\\.?\\d+$``, 0 to 1, at most 3
decimals after normalising, sent as a float of the quantised ``Decimal``; with a level too, they
must agree by the Engine's bands (:func:`band`). **Nested**: blank selects are skipped, a filled
one after a blank one is out of order, the outermost can't be the holder (the Engine checks
``attributed_to[0]`` only), and an attributed confidence needs an attribution. With the
``nested_beliefs`` gate closed (``nested_gate``) the nested fields are disabled and never read.
"""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from django import forms
from django.http import QueryDict

from console.cast import Character
from console.engine.client import EngineError
from console.engine_form_errors import engine_field_errors
from console.forms.fields import JsonBoxField
from console.inspect import NESTED_BELIEFS, Unavailable, setting, short_id
from console.paging import cast_id
from console.wording import UNKNOWN_CHARACTER, humanised

LEVELS = ("unknown", "suspected", "believed", "known")  # weakest first
POLARITIES = ("affirmed", "negated")
DISCLOSURES = ("open", "guarded", "secret")
REQUIRED_KEYS = ("subject_id", "predicate_id", "valid_time")
ATTRIBUTED_PREFIX = "attributed_to_"
ATTRIBUTED_FIELD = re.compile(r"^attributed_to_\d+$")
DEPTH_SETTING = "character_knowledge.nested_beliefs.depth"
DEPTH_DEFAULT, DEPTH_MIN, DEPTH_MAX = 2, 2, 5
THOUSANDTH = Decimal("0.001")


CHOOSE_CHARACTER = "Choose a character"
AS_IN_PROPOSITION = "(as in the proposition)"
NONE_CHOICE = "(none)"
PLAYER_SUFFIX = " (player)"
NOT_IN_CAST_SUFFIX = " (not in the cast)"
MISSING_KEYS = "The proposition needs subject_id, predicate_id and valid_time"
SCALAR_INVALID = "Enter a number from 0 to 1 with at most 3 decimals"
OUT_OF_ORDER = "Fill the attributions in order, outermost first"
NOT_THE_HOLDER = "Someone other than the holder"
CONFIDENCE_ALONE = "Attributed confidence needs someone in Attributed to"
HOLDER_NO_PARTITION = "That character has no partition (the player's character, or not a character)"
UNKNOWN_PREDICATE_TEXT = "That predicate isn't in this story's registry"
POLARITY_HELP = "A chosen polarity is sent and overrides the proposition's"
SCALAR_HELP = "Neither level nor scalar: the belief is known"
NOT_READABLE = (
    "Nested beliefs can't be read back yet (nathanpond/Epistrel#606): the result page is the "
    "only place this one will show."
)
NESTED_LEGEND = "Nested: the holder thinks someone believes it"
NESTED_LINE = "The holder thinks … believes it"

HOLDER_HAS_NO_PARTITION = "holder_has_no_partition"
NESTING_TOO_DEEP = "nesting_too_deep"
CONFIDENCE_INCONSISTENT = "confidence_inconsistent"
UNKNOWN_PREDICATE = "unknown_predicate"
UNKNOWN_CHARACTER_REASON = "unknown_character"
ATTRIBUTED_SELF = "attributed_self"
BODY_PREFIX = "body."

# An Engine field path (``body.`` stripped, index dropped) → the form field it lands on;
# :data:`NESTED_FIELDSET` is the Nested fieldset as a whole (the Engine names no select).
NESTED_FIELDSET = "attributed_to"
ENGINE_FIELDS: Mapping[str, str] = {
    "holder": "holder",
    "proposition": "proposition",
    "polarity": "polarity",
    "confidence_level": "confidence_level",
    "confidence_scalar": "confidence_scalar",
    "disclosure": "disclosure",
    "attributed_to": NESTED_FIELDSET,
    "attributed_holder": NESTED_FIELDSET,
    "attributed_confidence_level": "attributed_confidence_level",
}


def scalar_syntax(text: str) -> bool:
    """Whether ``text`` is ``digits``, ``.digits`` or ``digits.digits`` (no sign, exponent,
    comma or trailing dot), checked in one linear pass (no backtracking regex on typed input)."""
    whole, dot, fraction = text.partition(".")
    if not dot:
        return whole.isdecimal()
    return (not whole or whole.isdecimal()) and fraction.isdecimal()


def nested_depth(effective: Mapping[str, Any] | None) -> int:
    """``character_knowledge.nested_beliefs.depth``: an ``int`` (not a bool or float) clamped to
    2 to 5, else 2."""
    value = setting(effective, DEPTH_SETTING) if effective is not None else None
    if type(value) is not int:
        return DEPTH_DEFAULT
    return min(DEPTH_MAX, max(DEPTH_MIN, value))


def band(scalar: Decimal) -> str:
    """The level a scalar projects onto (the Engine's bands)."""
    if scalar < Decimal("0.2"):
        return "unknown"
    if scalar < Decimal("0.5"):
        return "suspected"
    if scalar < Decimal("0.9"):
        return "believed"
    return "known"


def band_text(scalar: Decimal | float, level: str) -> str:
    """ "The scalar 0.300 is Suspected, not Known"."""
    value = Decimal(str(scalar))
    return f"The scalar {value:.3f} is {humanised(band(value))}, not {humanised(level)}"


def too_deep_text(limit: object, depth: object) -> str:
    return (
        f"Too deep: this story allows depth {limit} (a first-order belief is 1; this was {depth})"
    )


def attributed_names(count: int) -> list[str]:
    return [f"{ATTRIBUTED_PREFIX}{n}" for n in range(1, count + 1)]


def _choices(values: Sequence[str], first: str) -> list[tuple[str, str]]:
    return [("", first), *((v, humanised(v)) for v in values)]


def _kept_label(character_id: str, cast: Sequence[Character], names: Mapping[str, str]) -> str:
    if any(c.id == character_id for c in cast):
        label = names.get(character_id) or short_id(character_id)
        return label if label.endswith(PLAYER_SUFFIX) else f"{label}{PLAYER_SUFFIX}"
    return f"{short_id(character_id)}{NOT_IN_CAST_SUFFIX}"


def _normalised(value: object) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


class DirectBeliefForm(forms.Form):
    holder = forms.CharField(
        label="Holder", error_messages={"required": CHOOSE_CHARACTER}, required=True
    )
    proposition = JsonBoxField(dict, label="Proposition")
    polarity = forms.ChoiceField(
        label="Polarity",
        required=False,
        choices=_choices(POLARITIES, AS_IN_PROPOSITION),
        help_text=POLARITY_HELP,
    )
    confidence_level = forms.ChoiceField(
        label="Confidence level", required=False, choices=_choices(LEVELS, NONE_CHOICE)
    )
    confidence_scalar = forms.CharField(
        label="Scalar",
        required=False,
        help_text=SCALAR_HELP,
        widget=forms.TextInput(attrs={"size": 8, "inputmode": "decimal"}),
    )
    disclosure = forms.ChoiceField(
        label="Disclosure", required=False, choices=_choices(DISCLOSURES, NONE_CHOICE)
    )
    attributed_confidence_level = forms.ChoiceField(
        label="Attributed confidence", required=False, choices=_choices(LEVELS, NONE_CHOICE)
    )

    def __init__(
        self,
        data: QueryDict | Mapping[str, Any] | None = None,
        *,
        cast: Sequence[Character] | None,
        names: Mapping[str, str],
        depth: int,
        nested_gate: Unavailable | None,
        initial: Mapping[str, Any] | None = None,
    ) -> None:
        bound = None
        self.depth = depth
        self.nested_gate = nested_gate
        self.attributed = attributed_names(depth - 1)
        self.nested_errors: list[str] = []
        if data is not None:
            bound = data.copy() if isinstance(data, QueryDict) else dict(data)
            for name in ("holder", *self.attributed):
                if name in bound:
                    bound[name] = _normalised(bound.get(name))
        super().__init__(bound, initial=dict(initial or {}))
        self.cast = cast
        self.names = names
        source = bound if bound is not None else self.initial
        requested = _normalised(source.get("holder"))
        for name in self.attributed:
            self.fields[name] = forms.CharField(
                required=False, label=f"Attributed to ({name.removeprefix(ATTRIBUTED_PREFIX)})"
            )
        self.holder_ids: tuple[str, ...] = ()
        if cast is not None:
            holders = [(c.id, names.get(c.id) or short_id(c.id)) for c in cast if not c.is_player]
            self.holder_ids = tuple(cid for cid, _ in holders)
            if cast_id(requested, self.holder_ids).kept:
                holders.append((requested, _kept_label(requested, cast, names)))
            self.fields["holder"].widget = forms.Select(choices=[("", CHOOSE_CHARACTER), *holders])
            everyone = [(c.id, names.get(c.id) or short_id(c.id)) for c in cast]
            cast_ids = tuple(cid for cid, _ in everyone)
            for name in self.attributed:
                options = [("", "—"), *everyone]
                typed = _normalised(source.get(name))
                if cast_id(typed, cast_ids).kept:
                    options.append((typed, _kept_label(typed, cast, names)))
                self.fields[name].widget = forms.Select(choices=options)
        else:
            self.fields["holder"].widget = forms.TextInput(attrs={"size": 30})
            for name in self.attributed:
                self.fields[name].widget = forms.TextInput(attrs={"size": 30})
        if nested_gate is not None:
            for name in self.nested_names:
                self.fields[name].disabled = True

    @property
    def nested_names(self) -> list[str]:
        return [*self.attributed, "attributed_confidence_level"]

    @property
    def attributed_fields(self) -> list[forms.BoundField]:
        return [self[name] for name in self.attributed]

    # --- cleaning ---

    def clean_holder(self) -> str:
        typed = cast_id(str(self.cleaned_data.get("holder") or ""), self.holder_ids)
        if typed.error:
            raise forms.ValidationError(typed.error)
        return typed.value

    def clean_proposition(self) -> dict[str, Any]:
        proposition: dict[str, Any] = self.cleaned_data["proposition"]
        if any(proposition.get(key) is None for key in REQUIRED_KEYS):
            raise forms.ValidationError(MISSING_KEYS)
        return proposition

    def clean_confidence_scalar(self) -> Decimal | None:
        typed = str(self.cleaned_data.get("confidence_scalar") or "").strip()
        if not typed:
            return None
        if not scalar_syntax(typed):
            raise forms.ValidationError(SCALAR_INVALID)
        try:
            value = Decimal(typed)
        except InvalidOperation as exc:
            raise forms.ValidationError(SCALAR_INVALID) from exc
        exponent = value.normalize().as_tuple().exponent
        if not (0 <= value <= 1) or (isinstance(exponent, int) and exponent < -3):
            raise forms.ValidationError(SCALAR_INVALID)
        return value.quantize(THOUSANDTH)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or self.cleaned_data
        level, scalar = data.get("confidence_level"), data.get("confidence_scalar")
        if level and scalar is not None and band(scalar) != level:
            self.add_error("confidence_scalar", band_text(scalar, level))
        if self.nested_gate is None:
            self._clean_nested(data)
        return data

    def _clean_nested(self, data: dict[str, Any]) -> None:
        cast_ids = tuple(c.id for c in self.cast or ())
        chain: list[str] = []
        gap = False
        for name in self.attributed:
            typed = cast_id(str(data.get(name) or ""), cast_ids)
            if typed.error:
                self.add_error(name, typed.error)
                continue
            data[name] = typed.value
            if not typed.value:
                gap = True
                continue
            if gap:
                self.add_error(name, OUT_OF_ORDER)
            chain.append(typed.value)
        outermost = data.get(self.attributed[0]) if self.attributed else ""
        holder = data.get("holder")
        if holder and outermost and outermost == holder and "holder" not in self.errors:
            self.add_error(self.attributed[0], NOT_THE_HOLDER)
        if data.get("attributed_confidence_level") and not chain:
            self.add_error("attributed_confidence_level", CONFIDENCE_ALONE)

    # --- the request ---

    @property
    def attributed_to(self) -> list[str]:
        if self.nested_gate is not None:
            return []
        return [v for name in self.attributed if (v := self.cleaned_data.get(name))]

    @property
    def body(self) -> dict[str, Any]:
        """The exact ``BeliefRequest`` (valid form only)."""
        data = self.cleaned_data
        body: dict[str, Any] = {"holder": data["holder"], "proposition": data["proposition"]}
        for name in ("polarity", "confidence_level"):
            if data.get(name):
                body[name] = data[name]
        if data.get("confidence_scalar") is not None:
            body["confidence_scalar"] = float(data["confidence_scalar"])
        if data.get("disclosure"):
            body["disclosure"] = data["disclosure"]
        chain = self.attributed_to
        if chain:
            body["attributed_to"] = chain
        if self.nested_gate is None and data.get("attributed_confidence_level"):
            body["attributed_confidence_level"] = data["attributed_confidence_level"]
        return body

    # --- Engine refusals (422) ---

    def _place(self, field: str | None, text: str) -> None:
        if field == NESTED_FIELDSET:
            self.nested_errors.append(text)
        else:
            self.add_error(field, text)

    def apply_engine_error(self, exc: EngineError) -> None:
        """A 422 placed beside the field it concerns (see the module docstring and #194's
        refusal list); one the form can't place goes above it."""
        details = exc.details
        if exc.code == HOLDER_HAS_NO_PARTITION:
            self.add_error("holder", HOLDER_NO_PARTITION)
        elif exc.code == NESTING_TOO_DEEP:
            self.nested_errors.append(too_deep_text(details.get("limit"), details.get("depth")))
        elif exc.code == CONFIDENCE_INCONSISTENT:
            level = self.cleaned_data.get("confidence_level")
            scalar = self.cleaned_data.get("confidence_scalar")
            text = band_text(scalar, level) if level and scalar is not None else exc.message
            self.add_error("confidence_scalar", text)
        elif exc.code == UNKNOWN_PREDICATE:
            self.add_error("proposition", UNKNOWN_PREDICATE_TEXT)
        elif details.get("reason") == UNKNOWN_CHARACTER_REASON:
            self._place_named(details.get("field"), UNKNOWN_CHARACTER)
        elif details.get("reason") == ATTRIBUTED_SELF:
            self.nested_errors.append(NOT_THE_HOLDER)
        else:
            failures = engine_field_errors(exc)
            if not failures:
                self.add_error(None, exc.message)
            for failure in failures:
                self._place_named(failure.path, failure.message or exc.message)

    def _place_named(self, path: object, text: str) -> None:
        self._place(engine_field(path), text)


def engine_field(path: object) -> str | None:
    """The form field an Engine path names (:data:`NESTED_FIELDSET`: the Nested fieldset), or
    ``None`` (above the form)."""
    if not isinstance(path, str) or not path:
        return None
    name = path.removeprefix(BODY_PREFIX)
    root = re.split(r"[.\[]", name, maxsplit=1)[0]
    if root in ENGINE_FIELDS:
        return ENGINE_FIELDS[root]
    if root.startswith("proposition"):
        return "proposition"
    return None


def gated_fields(data: QueryDict, depth: int) -> dict[str, tuple[str, str | None]]:
    """#188's ``gate_refusal`` map for a POST: every ``attributed_to_<n>`` it carries (and the
    form's own), and ``attributed_confidence_level``."""
    names = set(attributed_names(depth - 1)) | {k for k in data if ATTRIBUTED_FIELD.match(k)}
    ordered = sorted(names, key=lambda n: int(n.removeprefix(ATTRIBUTED_PREFIX)))
    gated: dict[str, tuple[str, str | None]] = {name: (NESTED_BELIEFS, None) for name in ordered}
    gated["attributed_confidence_level"] = (NESTED_BELIEFS, None)
    return gated

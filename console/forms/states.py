"""The one-State page's GET form (#242): D4's viewpoint picker plus D7's "As of" boundary; and
the Set form (#251), :class:`StateSetForm`."""

from collections.abc import Sequence
from typing import Any

from django import forms
from django.http import QueryDict

from console.canon import cast_names
from console.cast import Character
from console.engine.client import EngineError
from console.engine.models import StoryState
from console.forms.world import NUMBER_INVALID, BoundaryField, WorldViewpointForm, parse_number
from console.ids import is_ulid
from console.inspect import short_id
from console.paging import CHARACTER_INVALID
from console.states import CHARACTER_SCOPE
from console.wording import character_label
from console.world import BOOLEAN, ENUM, NUMBER, TEXT

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

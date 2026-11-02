"""The Reconciliation section's compensate form (#264; D5, D8, D9, C4, C13).

:class:`CompensateForm` is ``inspect:compensate``'s form
(``console/views/inspect_reconciliation.py``). Its field names are the Engine's
``CompensatingEffect`` members (Engine ``core/outbox.py`` at origin/main b8fd182), so a 422
``validation_error`` on ``body.effect.<member>`` lands on the field through
:data:`COMPENSATE_ERRORS` (keyed without ``body.``: ``apply_engine_errors`` strips it before the
alias lookup, D9):

- ``state``: required, at most 64 characters, initial from the link's ``state`` (#249's
  ``action_query``);
- ``scope_key`` ("Character"): with the shell's story read, a select whose first choice
  (:data:`STORY_STATE_CHOICE`) sends nothing, then the cast in the Engine's order labelled by C4
  (" (player)" marked); a posted id outside the cast is kept as an extra option (C13) and sent
  when it is 1-64 characters. Without the story read a free-text id (trimmed, upper-cased, at most
  64; blank sends nothing). Per-character versus story scope is the Engine's refusal
  (``required``/``story_scoped``), placed here;
- ``delta``: D5's ``DeltaField``; ``value``: D5's ``ScalarValueField`` (both imported from
  ``console/forms/world.py``, never redefined);
- ``reason``: required, stripped, at most 200 characters.

:meth:`CompensateForm.clean` wants exactly one of delta and value (:data:`BOTH_GIVEN`,
:data:`NONE_GIVEN`, non-field errors); :meth:`CompensateForm.effect` is the request's ``effect``.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from django import forms

from console.canon import cast_names
from console.cast import Character
from console.forms.scheduled_events import NOT_IN_CAST
from console.forms.states import normalised_id
from console.forms.world import DeltaField, ScalarValueField

STATE_MAX = 64  # CompensatingEffect.state / scope_key: 1-64
SCOPE_MAX = 64
REASON_MAX = 200  # CompensatingEffect.reason: 1-200
BOTH_GIVEN = "Give a delta or a value, not both"
NONE_GIVEN = "Give a delta (for a number State) or a value"
STORY_STATE_CHOICE = ("", "— (a story State)")
DELTA_HELP = "A signed change for a number State, e.g. -500"
SCOPE_TOO_LONG = f"A character id is at most {SCOPE_MAX} characters"
CAST_UNREAD = (
    "The cast couldn't be read — enter the character id, or leave it blank for a story State"
)
# The form's fields, in the order the Engine names them (``effect.<member>``).
EFFECT_FIELDS: tuple[str, ...] = ("state", "scope_key", "delta", "value", "reason")
# A 422 ``validation_error``'s ``details.field`` (``body.`` stripped) → the form's field.
COMPENSATE_ERRORS: Mapping[str, str] = {f"effect.{name}": name for name in EFFECT_FIELDS}


class CompensateForm(forms.Form):
    """The compensate form (see the module docstring). ``cast`` is the shell's story read's cast,
    ``None`` when that read failed (Character is then free text)."""

    state = forms.CharField(label="State", max_length=STATE_MAX)
    delta = DeltaField(label="Delta", help_text=DELTA_HELP)
    value = ScalarValueField(label="Value")
    reason = forms.CharField(label="Reason", max_length=REASON_MAX)

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        cast: Sequence[Character] | None,
        state: str = "",
    ) -> None:
        initial = {"state": state.strip()} if state.strip() else {}
        kept = ""
        if data is not None:
            # C13: the posted id trimmed and upper-cased before it meets the choices.
            kept = normalised_id(data.get("scope_key"))
            bound = data.copy() if hasattr(data, "copy") else dict(data)
            bound["scope_key"] = kept
            data = bound
        super().__init__(data, initial=initial)
        self.cast = cast
        if cast is None:
            self.fields["scope_key"] = forms.CharField(
                label="Character", required=False, max_length=SCOPE_MAX
            )
        else:
            self.fields["scope_key"] = forms.ChoiceField(
                label="Character", required=False, choices=self._choices(kept)
            )
        self.order_fields(EFFECT_FIELDS)

    def _choices(self, kept: str) -> list[tuple[str, str]]:
        names = cast_names(self.cast)
        choices = [STORY_STATE_CHOICE, *((c.id, names[c.id]) for c in self.cast or ())]
        if kept and kept not in names:
            choices.append((kept, NOT_IN_CAST.replace("{id}", kept)))
        return choices

    @property
    def names(self) -> dict[str, str]:
        """C4's cast labels (``{}`` without the story read)."""
        return cast_names(self.cast)

    def clean_scope_key(self) -> str:
        value = normalised_id(self.cleaned_data.get("scope_key"))
        if len(value) > SCOPE_MAX:
            raise forms.ValidationError(SCOPE_TOO_LONG, code="max_length")
        return value

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if "delta" in cleaned and "value" in cleaned:
            delta, value = cleaned["delta"], cleaned["value"]
            if delta is not None and value is not None:
                raise forms.ValidationError(BOTH_GIVEN, code="both_given")
            if delta is None and value is None:
                raise forms.ValidationError(NONE_GIVEN, code="none_given")
        return cleaned

    def effect(self) -> dict[str, Any]:
        """``{state, reason}`` plus ``delta`` or ``value``, plus ``scope_key`` only when non-blank
        (valid form only)."""
        cleaned = self.cleaned_data
        effect: dict[str, Any] = {"state": cleaned["state"]}
        if cleaned.get("scope_key"):
            effect["scope_key"] = cleaned["scope_key"]
        if cleaned.get("delta") is not None:
            effect["delta"] = cleaned["delta"]
        else:
            effect["value"] = cleaned["value"]
        effect["reason"] = cleaned["reason"]
        return effect

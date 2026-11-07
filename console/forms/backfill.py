"""The Backfill page's write forms (#309; FR-PROF-9/10, E4's body rule, E21, E23).

:class:`ConflictResolveForm` settles one open backfill conflict. It renders one radio per
statement (``choose=<statement_id>``, the recommendation's statement pre-selected), an "Or state
the value" box (``value``, at most :data:`VALUE_MAX` characters) and two submits: "Resolve" and
"Accept the recommendation" (``accept=1``, shown only when the conflict has a recommendation).
:meth:`ConflictResolveForm.body` gives exactly one member, E4's rule:

- ``{"accept_recommendation": true}`` for the accept submit;
- ``{"value": <stripped text>}`` when the box is non-empty (it wins over the radios, and is sent
  as typed: the Engine normalises it, #708);
- else ``{"choose": <statement_id>}``.

A choice not among the conflict's statements, or none at all, is :data:`NO_CHOICE` above the
form with no Engine call. When the conflict isn't known (the view's read missed it) the choice
is sent as posted and the Engine decides. Each form's ids are ``conflict-<id>-<field>`` so
several on one page never collide; posted names stay bare (``choose``/``value``).

A radio's label is "Turn <n> — <speaker or source>: <value>", then the quote in quotes when it
isn't null. The value is E23's: a string as is, else compact JSON cut at
:data:`~console.raw.CELL_LIMIT` with the full text in ``title``.

:class:`GapFillForm` is the "Fill coverage gaps…" form: one checkbox per capability of the
effective configuration (in its order, labelled with the raw id; none ticked means "every
capability that has a gap") and the hidden ``mode`` (``start`` | ``resume``) that
``stories:backfill-start`` branches on. The checkbox values are not checked against the list
here: the Engine refuses a capability the story doesn't have on (422 at
``body.capabilities``), which lands on the checkboxes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from django import forms

from console.backfill import enum_text, speaker_text
from console.engine.models import BackfillConflict, ConflictStatement
from console.raw import CELL_LIMIT, compact_json

VALUE_MAX = 2000
VALUE_LABEL = "Or state the value"
VALUE_HELP = "Overrides the choice above; the value is stored as you type it"
NO_CHOICE = "Pick a statement, type a value, or accept the recommendation"
RESOLVE_LABEL = "Resolve"
ACCEPT_LABEL = "Accept the recommendation"
ACCEPT: Final = "accept"
CHOOSE: Final = "choose"
VALUE: Final = "value"
MODE: Final = "mode"
START: Final = "start"
RESUME: Final = "resume"
MODES = (START, RESUME)
CAPABILITIES: Final = "capabilities"


@dataclass(frozen=True)
class RadioRow:
    """One statement's radio: its element id, value (the statement id), label, the label's
    ``title`` (the full value when the label cuts it) and whether it is checked."""

    id: str
    value: str
    label: str
    title: str
    checked: bool


def value_label(value: object) -> tuple[str, str]:
    """E23: ``(text, title)``: a string as is, else compact JSON cut at ``CELL_LIMIT`` with the
    full text as ``title`` ("" when nothing was cut)."""
    if isinstance(value, str):
        return value, ""
    full = compact_json(value)
    shown = compact_json(value, CELL_LIMIT)
    return shown, full if shown != full else ""


def statement_label(statement: ConflictStatement, names: Mapping[str, str]) -> tuple[str, str]:
    """ "Turn <n> — <speaker or source>: <value>" (plus “<quote>”), and its title."""
    if statement.speaker:
        who = speaker_text(statement.speaker, names)
    else:
        who, _ = enum_text(statement.source)
    value, title = value_label(statement.value)
    quote = f" “{statement.quote}”" if statement.quote is not None else ""
    return f"Turn {statement.turn:,} — {who}: {value}{quote}", title


class ConflictResolveForm(forms.Form):
    """One open conflict's resolve form (module docstring)."""

    choose = forms.CharField(required=False, strip=True)
    value = forms.CharField(
        required=False, strip=True, max_length=VALUE_MAX, label=VALUE_LABEL, help_text=VALUE_HELP
    )

    def __init__(
        self,
        conflict_id: str,
        conflict: BackfillConflict | None = None,
        data: Mapping[str, Any] | None = None,
        *,
        names: Mapping[str, str] | None = None,
    ) -> None:
        recommended = conflict.recommended if conflict is not None else None
        super().__init__(
            data,
            auto_id=f"conflict-{conflict_id}-%s",
            initial={CHOOSE: recommended.choose} if recommended is not None else {},
        )
        self.conflict_id = conflict_id
        self.conflict = conflict
        self.names = names or {}

    @property
    def accept(self) -> bool:
        return self.is_bound and self.data.get(ACCEPT) == "1"

    @property
    def has_recommendation(self) -> bool:
        return self.conflict is not None and self.conflict.recommended is not None

    @property
    def statement_ids(self) -> tuple[str, ...]:
        return tuple(s.statement_id for s in self.conflict.statements) if self.conflict else ()

    @property
    def radio_rows(self) -> tuple[RadioRow, ...]:
        if self.conflict is None:
            return ()
        checked = self[CHOOSE].value()
        rows = []
        for n, statement in enumerate(self.conflict.statements):
            label, title = statement_label(statement, self.names)
            rows.append(
                RadioRow(
                    id=f"conflict-{self.conflict_id}-choose_{n}",
                    value=statement.statement_id,
                    label=label,
                    title=title,
                    checked=checked == statement.statement_id,
                )
            )
        return tuple(rows)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if self.accept or cleaned.get(VALUE):
            return cleaned
        chosen = cleaned.get(CHOOSE) or ""
        if not chosen or (self.conflict is not None and chosen not in self.statement_ids):
            raise forms.ValidationError(NO_CHOICE)
        return cleaned

    def body(self) -> dict[str, Any]:
        """Exactly one of ``accept_recommendation`` | ``value`` | ``choose`` (call after
        ``is_valid()``)."""
        if self.accept:
            return {"accept_recommendation": True}
        value = self.cleaned_data.get(VALUE)
        if value:
            return {VALUE: value}
        return {CHOOSE: self.cleaned_data[CHOOSE]}

    def choice_error_field(self) -> str | None:
        """Where an Engine ``invalid_resolution`` goes: ``value`` when one was typed, else the
        radios' slot (``choose``)."""
        typed = self.data.get(VALUE, "") if self.is_bound else ""
        return VALUE if isinstance(typed, str) and typed.strip() and not self.accept else CHOOSE


class GapFillForm(forms.Form):
    """The "Fill coverage gaps…" form (module docstring): ``capabilities`` checkboxes over
    ``choices`` and the hidden ``mode``."""

    capabilities = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple, label="Capabilities"
    )
    mode = forms.ChoiceField(
        choices=[(m, m) for m in MODES], initial=START, widget=forms.HiddenInput
    )

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        choices: Sequence[str] = (),
        mode: str = START,
    ) -> None:
        super().__init__(data, auto_id="gap-fill-%s", initial={MODE: mode})
        self.choice_ids = tuple(choices)
        self._set_choices(self.choice_ids)

    def full_clean(self) -> None:
        # The posted capabilities are the Engine's to check (module docstring): widen the
        # choices to what was posted, so a stale or tampered box reaches the Engine's 422.
        if self.is_bound and hasattr(self.data, "getlist"):
            posted = [v for v in self.data.getlist(CAPABILITIES) if isinstance(v, str)]
            known = self.choice_ids
            self._set_choices((*known, *(p for p in posted if p not in known)))
        super().full_clean()

    def _set_choices(self, values: Sequence[str]) -> None:
        field = self.fields[CAPABILITIES]
        if isinstance(field, forms.MultipleChoiceField):
            field.choices = [(c, c) for c in values]

    def chosen_mode(self) -> str:
        found = self.cleaned_data.get(MODE) if hasattr(self, "cleaned_data") else None
        return found if found in MODES else START

    def chosen(self) -> list[str] | None:
        """The ticked capabilities in the order shown, de-duplicated; ``None`` when none is
        ticked (every capability with a gap)."""
        ticked = [c.strip() for c in self.cleaned_data.get(CAPABILITIES) or [] if c.strip()]
        found = list(dict.fromkeys(ticked))
        return found or None

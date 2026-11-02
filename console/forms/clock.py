"""The clock settings form (#243; D8, D14, founder decision 8): ``PATCH /stories/{id}/clock``.

:class:`ClockSettingsForm` holds the scalar settings and owns the two row tables
(:class:`PacingRowForm`, prefix ``pacing``; :class:`BandRowForm`, prefix ``bands``), edited
without JavaScript through ``console.formset_rows``' ``op`` buttons (``op=add:pacing``,
``op=remove:bands-2``). A wholly blank row is dropped before validation and patch-building; a
partly filled row is validated as required. The calendar is one ``JsonBoxField(dict)`` holding
the whole object, ``{"kind": "gregorian"}`` included.

:meth:`ClockSettingsForm.patch` holds only the members whose cleaned value differs from the
snapshot the page rendered from: ``console.merge_patch.diff`` over the six non-object members
(rows replaced whole), the calendar compared as JSON and sent whole when it differs. Emptying a
stored table sends ``null`` (D14, #268's rule: the Engine's default applies); ``[]`` is never
sent, and a table with no rows and no stored value sends nothing. ``provider_value``
(``"<provider>.<value>"``) is sent only under External, when the snapshot wasn't external or the
value changed; a changed value typed under another mode is a field error (no call), the
snapshot's own value being ignored there. ``ClockPatch`` has no ``anchor`` member, so there is
no anchor control.

The Engine's own rules (contiguous pacing rows from 0 words, only the last open-ended; bands
tiling the day; a custom calendar's year equal to its months' days) are not re-checked: its 422
lands above the table. Row caps are the Console's (no-JavaScript editor), never below the
snapshot's own row count. Digit inputs are ``type="text" inputmode="numeric"``; no
``min``/``max``/``pattern`` (C10).
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.forms.utils import ErrorDict, ErrorList

from console.forms.fields import JsonBoxField
from console.merge_patch import diff
from console.wording import humanised

PACING_PREFIX = "pacing"
BANDS_PREFIX = "bands"
PACING_CAP = 20
BANDS_CAP = 24
INT_MAX = 2147483647  # the schema's bound
EXTERNAL = "external"

MODE_CHOICES = [("paced", "Paced"), ("manual", "Manual"), (EXTERNAL, "External")]
TIME_AWARENESS_CHOICES = [("exact", "Exact"), ("approximate", "Approximate"), ("none", "None")]
YES_NO = [("true", "Yes"), ("false", "No")]

SKIP_INVALID = "Enter a whole number of minutes, 0 or more."
INT_TOO_BIG = "At most 2,147,483,647."
CELL_INVALID = "Enter a whole number, 0 or more."
TIME_INVALID = "Enter a time as HH:MM (24:00 for the end of the day)"
CALENDAR_REQUIRED = 'Enter the calendar as JSON, e.g. {"kind": "gregorian"}'
PROVIDER_VALUE_HELP = "Only for an external clock: <provider>.<value>, e.g. bank.story_minutes"
PROVIDER_VALUE_NOT_EXTERNAL = (
    "Only an external clock reads a provider value — choose External or clear it"
)
CALENDAR_HELP = (
    '{"kind": "gregorian"}, or {"kind": "custom", "day_minutes", "months": [{"name", "days"}], '
    '"weekdays", "year_days"}'
)
TABLE_HELP = "No rows: the Engine's default"

# The patchable members, in ``ClockSettingsBody`` order (``provider_value`` after ``mode``), with
# the settings table's labels (the saved flash's names).
MEMBER_LABELS: Mapping[str, str] = {
    "mode": "Mode",
    "provider_value": "Provider value",
    "pacing_table": "Pacing table",
    "max_unprompted_skip_minutes": "Max unprompted skip",
    "stop_at_due_events": "Stop at due events",
    "time_awareness": "Time awareness",
    "bands": "Bands",
    "calendar": "Calendar",
}
SCALARS = ("mode", "max_unprompted_skip_minutes", "stop_at_due_events", "time_awareness")
TABLES = ("pacing_table", "bands")

_TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_NUMERIC = {"inputmode": "numeric"}


def _digits(text: str, invalid: str) -> int:
    """ASCII digits as an int, at most :data:`INT_MAX`."""
    if not (text.isascii() and text.isdigit()):
        raise forms.ValidationError(invalid, code="invalid")
    value = int(text)
    if value > INT_MAX:
        raise forms.ValidationError(INT_TOO_BIG, code="max_value")
    return value


class WholeNumberField(forms.CharField):
    """ASCII digits → ``int`` (0 … 2,147,483,647); blank → ``None`` when not required."""

    def __init__(self, *, invalid: str = CELL_INVALID, **kwargs: Any) -> None:
        self.invalid = invalid
        kwargs.setdefault("widget", forms.TextInput(attrs=_NUMERIC))
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> int | None:  # type: ignore[override]
        text = super().to_python(value)
        if text is None or text in self.empty_values:
            return None
        return _digits(text, self.invalid)


class TimeField(forms.CharField):
    """``HH:MM`` (00:00 to 23:59, or 24:00 when ``end``), kept as the string."""

    def __init__(self, *, end: bool = False, **kwargs: Any) -> None:
        self.end = end
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> str:
        text = super().to_python(value)
        if text is None or text in self.empty_values:
            return ""
        if _TIME.match(text) or (self.end and text == "24:00"):
            return text
        raise forms.ValidationError(TIME_INVALID, code="invalid")


class RowForm(forms.Form):
    """A row of a table the clock sends as ``[a, b, c]``."""

    def row(self) -> list[Any]:
        raise NotImplementedError


class PacingRowForm(RowForm):
    """One pacing row: ``[from_words, to_words | None, minutes]``."""

    from_words = WholeNumberField(label="From words")
    to_words = WholeNumberField(label="To words (exclusive)", required=False)
    minutes = WholeNumberField(label="Minutes")

    def row(self) -> list[int | None]:
        data = self.cleaned_data
        return [data["from_words"], data.get("to_words"), data["minutes"]]


class _BandRow(RowForm):
    def row(self) -> list[str]:
        data = self.cleaned_data
        return [data["name"], data["from"], data["to"]]


class BandRowForm(_BandRow):
    """One time-of-day band: ``[name, from, to]``. Its time fields are named after the Engine's
    columns; ``from`` is a Python keyword, so both are added after the class is built."""

    name = forms.CharField(label="Name", strip=True)


for _name, _field in (("from", TimeField(label="From")), ("to", TimeField(label="To", end=True))):
    BandRowForm.base_fields[_name] = _field
    BandRowForm.declared_fields[_name] = _field


class RowFormSet(BaseFormSet):  # type: ignore[type-arg]
    """Every bound row is an extra row (``empty_permitted``), so a wholly blank one is skipped
    and dropped; the rendered management form says nothing the POST relies on."""

    def initial_form_count(self) -> int:
        return 0 if self.is_bound else super().initial_form_count()

    def filled(self) -> list[RowForm]:
        """The rows that hold anything, in posted order (the order they are sent in)."""
        return [form for form in self.forms if form.has_changed()]


# formset_factory builds a subclass of ``formset=``; the stubs type it as the generic base.
PacingFormSet = cast(
    type[RowFormSet], formset_factory(PacingRowForm, formset=RowFormSet, extra=0, max_num=1000)
)
BandFormSet = cast(
    type[RowFormSet], formset_factory(BandRowForm, formset=RowFormSet, extra=0, max_num=1000)
)

BAND_FIELDS = ("name", "from", "to")
PACING_FIELDS = ("from_words", "to_words", "minutes")


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def _initial_rows(table: Any, names: Sequence[str]) -> list[dict[str, str]]:
    """A stored table as row initials, cell by cell (``str(value)``, blank for ``null``)."""
    rows: list[dict[str, str]] = []
    for row in table if isinstance(table, list) else []:
        cells = row if isinstance(row, list) else [row]
        rows.append(
            {name: _cell(cells[i]) if i < len(cells) else "" for i, name in enumerate(names)}
        )
    return rows


def row_caps(original: Mapping[str, Any]) -> dict[str, int]:
    """The ``op`` row caps: 20 pacing rows and 24 bands, or the snapshot's own count when it is
    longer (a longer Engine-side table is never truncated)."""

    def count(key: str) -> int:
        value = original.get(key)
        return len(value) if isinstance(value, list) else 0

    return {
        PACING_PREFIX: max(PACING_CAP, count("pacing_table")),
        BANDS_PREFIX: max(BANDS_CAP, count("bands")),
    }


def provider_value_of(original: Mapping[str, Any]) -> str:
    """The snapshot's ``"<provider_id>.<external_value_name>"`` when both are set, else ""."""
    provider, name = original.get("provider_id"), original.get("external_value_name")
    if isinstance(provider, str) and provider and isinstance(name, str) and name:
        return f"{provider}.{name}"
    return ""


def _kept(choices: list[tuple[str, str]], value: Any) -> list[tuple[str, str]]:
    """C7's ``_kept`` pattern: a current value outside the choices is one more choice."""
    if isinstance(value, str) and value and value not in {v for v, _ in choices}:
        return [*choices, (value, humanised(value))]
    return choices


def _yes_no(value: str) -> bool:
    return value == "true"


class ClockSettingsForm(forms.Form):
    """The seven patchable settings (``original``: the snapshot's settings, the initial values
    and the kept choices). ``data`` ``None``: unbound, initialised from ``original``."""

    mode = forms.ChoiceField(label="Mode", choices=MODE_CHOICES)
    provider_value = forms.CharField(
        label="Provider value", required=False, strip=True, help_text=PROVIDER_VALUE_HELP
    )
    max_unprompted_skip_minutes = WholeNumberField(
        label="Max unprompted skip (minutes)",
        invalid=SKIP_INVALID,
        error_messages={"required": SKIP_INVALID},
    )
    stop_at_due_events = forms.TypedChoiceField(
        label="Stop at due events", choices=YES_NO, coerce=_yes_no
    )
    time_awareness = forms.ChoiceField(label="Time awareness", choices=TIME_AWARENESS_CHOICES)
    calendar = JsonBoxField(
        dict,
        label="Calendar",
        help_text=CALENDAR_HELP,
        error_messages={"required": CALENDAR_REQUIRED},
    )

    def __init__(self, data: Any = None, *, original: Mapping[str, Any]) -> None:
        self.original = dict(original)
        initial: dict[str, Any] | None = None
        if data is None:
            stop = original.get("stop_at_due_events")
            skip = original.get("max_unprompted_skip_minutes")
            initial = {
                "mode": original.get("mode"),
                "provider_value": provider_value_of(original),
                "max_unprompted_skip_minutes": "" if skip is None else str(skip),
                "stop_at_due_events": "true" if stop is True else "false",
                "time_awareness": original.get("time_awareness"),
                "calendar": original.get("calendar"),
            }
        super().__init__(data, initial=initial)
        mode_field: Any = self.fields["mode"]
        mode_field.choices = _kept(MODE_CHOICES, original.get("mode"))
        aware_field: Any = self.fields["time_awareness"]
        aware_field.choices = _kept(TIME_AWARENESS_CHOICES, original.get("time_awareness"))
        if data is None:
            self.pacing = PacingFormSet(
                prefix=PACING_PREFIX,
                initial=_initial_rows(original.get("pacing_table"), PACING_FIELDS),
            )
            self.bands = BandFormSet(
                prefix=BANDS_PREFIX, initial=_initial_rows(original.get("bands"), BAND_FIELDS)
            )
        else:
            self.pacing = PacingFormSet(data, prefix=PACING_PREFIX)
            self.bands = BandFormSet(data, prefix=BANDS_PREFIX)
        caps = row_caps(original)
        self.can_add_pacing = len(self.pacing.forms) < caps[PACING_PREFIX]
        self.can_add_bands = len(self.bands.forms) < caps[BANDS_PREFIX]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        value = cleaned.get("provider_value") or ""
        mode = cleaned.get("mode")
        # Under another mode, the snapshot's own value is ignored; a typed one is refused.
        typed = bool(value) and value != provider_value_of(self.original)
        if mode is not None and mode != EXTERNAL and typed:
            self.add_error("provider_value", PROVIDER_VALUE_NOT_EXTERNAL)
        return cleaned

    def is_valid(self) -> bool:
        results = [super().is_valid(), self.pacing.is_valid(), self.bands.is_valid()]
        return all(results)

    def quiet(self) -> None:
        """Show the bound state without validating it (a re-render after a row button)."""
        # Django validates lazily on ``errors``: setting them empty renders the state as is.
        quieted: Any = self
        quieted._errors = ErrorDict()
        for formset in (self.pacing, self.bands):
            quieted = formset
            quieted._errors = []
            quieted._non_form_errors = ErrorList()
            for form in formset.forms:
                quieted = form
                quieted._errors = ErrorDict()

    def table_rows(self) -> dict[str, Sequence[forms.BaseForm]]:
        """The forms that built each table, in the order sent (the 422 placement map)."""
        return {"pacing_table": self.pacing.filled(), "bands": self.bands.filled()}

    def table_error(self, member: str, message: str) -> None:
        """An Engine error about a whole table, above it."""
        formset: Any = self.pacing if member == "pacing_table" else self.bands
        formset.non_form_errors()  # clean first, so the added error isn't overwritten
        formset._non_form_errors.append(message)

    def patch(self, original: Mapping[str, Any]) -> dict[str, Any]:
        """The ``ClockPatch``: only the changed members, in :data:`MEMBER_LABELS` order (call
        after :meth:`is_valid`)."""
        data = self.cleaned_data
        before: dict[str, Any] = {key: original.get(key) for key in (*SCALARS, *TABLES)}
        after: dict[str, Any] = {key: data.get(key) for key in SCALARS}
        tables = {
            "pacing_table": [form.row() for form in self.pacing.filled()],
            "bands": [form.row() for form in self.bands.filled()],
        }
        for key, rows in tables.items():
            stored = original.get(key)
            if rows:
                after[key] = rows
            elif stored:  # D14: emptying a stored table sends null (the Engine's default)
                after[key] = None
            else:  # no rows and nothing stored: nothing to send
                after[key] = stored
        changed = diff(before, after)
        calendar = data.get("calendar")
        stored_calendar = original.get("calendar")
        if not isinstance(stored_calendar, Mapping) or diff(stored_calendar, calendar or {}):
            changed["calendar"] = calendar
        if data.get("mode") == EXTERNAL:
            value = data.get("provider_value") or ""
            if original.get("mode") != EXTERNAL or value != provider_value_of(original):
                changed["provider_value"] = value
        return {key: changed[key] for key in MEMBER_LABELS if key in changed}


def saved_text(patch: Mapping[str, Any]) -> str:
    """ "Pacing table, Bands" (the saved flash's members, in :data:`MEMBER_LABELS` order)."""
    return ", ".join(label for key, label in MEMBER_LABELS.items() if key in patch)

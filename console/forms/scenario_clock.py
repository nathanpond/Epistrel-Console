"""A scenario's starting clock and pre-scheduled events in the editor (#268; D6, D14).

The Clock section (``fieldset#f-starting_state-clock``) holds main-form fields named after the
Engine path (``starting_state__clock__…``, #64 convention 6), each blank one sent as nothing:

- the anchor's six parts (``anchor__year`` … ``anchor__band``). Every part blank means
  unanchored: the Engine stores ``anchor: null`` exactly as an absent anchor (D14), so there is
  no "No anchor" control;
- ``mode``, ``stop_at_due_events`` and ``time_awareness`` as C7 selects ("Engine default (…)"
  first; a stored value outside the choices kept, humanised), ``max_unprompted_skip_minutes``
  with #243's digits check, and the ``calendar`` as a D14 JSON box (the editor's
  ``OTHER_BOXES``);
- #243's pacing and band row tables (:class:`~console.forms.clock.PacingRowForm`,
  :class:`~console.forms.clock.BandRowForm`, prefixes ``pacing`` and ``bands``, their fields,
  texts and caps), each row a fixed-width list mapped positionally. A stored row that is not 3
  cells wide is shown positionally with a note of its width (:data:`ROW_WIDTH_NOTE`) and sent
  as stored until it is edited;
- the ``evt`` row set (:class:`EventForm`, cap :data:`EVENTS_MAX`): one
  ``ScenarioScheduledEvent`` per row, ``planned`` through D6's
  :class:`~console.forms.world.PlannedField`, participants and audience written through #205's
  ``keep_order`` against the stored event of the same index (C9).

Both sides of the editor's comparison normalise alike: :func:`clock_to_write` (keys in
``ScenarioClock``'s schema order, unset members left out, an empty clock or anchor ``None``, a
table with no rows ``None``; a row that reads back as the stored one is sent as stored,
:func:`kept_rows`) and :func:`event_to_write` (``ScenarioScheduledEvent``'s order,
``planned`` kept as its JSON type, an empty list left out). The Engine's own clock rules
(contiguous pacing, bands tiling the day, the calendar's shape, a time of day the anchor can't
place) are not re-checked: they come back in its 422 or report.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.forms.boundfield import BoundField

from console.forms.catalog_character import norm_text
from console.forms.clock import (
    BAND_FIELDS,
    BANDS_CAP,
    BANDS_PREFIX,
    INT_MAX,
    INT_TOO_BIG,
    PACING_CAP,
    PACING_FIELDS,
    PACING_PREFIX,
    SKIP_INVALID,
    TABLE_HELP,
    TIME_AWARENESS_CHOICES,
    BandRowForm,
    PacingRowForm,
    RowFormSet,
    WholeNumberField,
)
from console.forms.fields import JsonBoxField
from console.forms.scenario_cast import (
    AUDIENCE_LEVELS,
    AUDIENCE_MAX,
    GROUP_SUFFIX,
    WORLD,
    split_audience,
)
from console.forms.world import PlannedField
from console.story_settings import engine_default_choice, kept_choices

CLOCK_PATH = "starting_state.clock"
ANCHOR_PATH = f"{CLOCK_PATH}.anchor"
EVENTS_PATH = "starting_state.scheduled_events"
CLOCK = "starting_state__clock"
ANCHOR = f"{CLOCK}__anchor"
CALENDAR_FIELD = f"{CLOCK}__calendar"
STOP_FIELD = f"{CLOCK}__stop_at_due_events"
EVENTS = "evt"
EVENTS_MAX = 200
PARTICIPANTS_MAX = 32
TEXT_MAX = 60  # a weekday, band or band name (the Engine's ``ALIAS_MAX``)
PROPOSITION_MAX = 200
INT_MIN = -2147483648
CELLS = "cells"
WIDTH = 3

# ``ScenarioClock``'s, ``ScenarioAnchor``'s and ``ScenarioScheduledEvent``'s members in the
# Engine's schema order (``scenario_models``).
CLOCK_MEMBERS = (
    "anchor",
    "mode",
    "pacing_table",
    "max_unprompted_skip_minutes",
    "stop_at_due_events",
    "time_awareness",
    "bands",
    "calendar",
)
ANCHOR_MEMBERS = ("year", "month", "day", "weekday", "time", "band")
EVENT_MEMBERS = (
    "proposition",
    "subject",
    "planned",
    "participants",
    "place",
    "owner",
    "audience",
    "disclosure",
)
TABLES = {"pacing_table": PACING_FIELDS, "bands": BAND_FIELDS}
TABLE_PREFIXES = {"pacing_table": PACING_PREFIX, "bands": BANDS_PREFIX}

# The choices (#243's labels; ``external`` isn't a scenario clock's mode).
MODE_CHOICES = [("paced", "Paced"), ("manual", "Manual")]
YES_NO = [("true", "Yes"), ("false", "No")]
DISCLOSURE_CHOICES = [("open", "Open"), ("guarded", "Guarded"), ("secret", "Secret")]
C7_DEFAULTS = {"mode": "paced", "stop_at_due_events": "yes", "time_awareness": "exact"}

# Texts.
CLOCK_HELP = (
    "The clock a story created from this scenario starts with. Blank fields keep the Engine's "
    "defaults."
)
ANCHOR_HELP = "Leave every part blank for no anchor: the story's time of day is unknown"
WHOLE_NUMBER = "Enter a whole number"
AT_LEAST_ONE = "At least 1"
INT_TOO_SMALL = "At least -2,147,483,648."
TIME_OF_DAY_INVALID = "Enter a time of day (HH:MM)"
TOO_LONG = "At most {n} characters"
WEEKDAY_HELP = "A weekday name of the calendar (e.g. Saturday)"
BAND_HELP = "A band name (e.g. morning)"
SKIP_HELP = "Minutes a render may skip on its own (Engine default 1,440)"
CALENDAR_HELP = "A custom calendar as JSON; blank for the Gregorian calendar"
PACING_HELP = f"Story-minutes a turn of that many words moves the clock. {TABLE_HELP}"
ROW_WIDTH_NOTE = "This row has {n} cells; the Engine expects 3"
EVENTS_HELP = (
    "Events planned from the story's start. Use Update form to offer new roles, groups or "
    "locations."
)
EVENT_NEEDS_NAME = "An event needs its name"
PLANNED_REQUIRED = "When is it planned?"
PLANNED_HELP = (
    "Minutes from the story's start (e.g. 130) or a time of day the anchor places (HH:MM)"
)
SUBJECT_HELP = "Engine default: the owning role, else the first participant"
OWNER_HELP = "world: the world's event; a role: that role's character owns it"
OTHER_AUDIENCE_HELP = f"Comma-separated, for the levels {', '.join(AUDIENCE_LEVELS)}"
TOO_MANY_PARTICIPANTS = f"At most {PARTICIPANTS_MAX} participants"
TOO_MANY_AUDIENCE = f"At most {AUDIENCE_MAX} audience entries."  # SeedForm's text
ENGINE_DEFAULT_LABEL = "Engine default"
MISSING_SUFFIX = " (missing)"

_TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_SIGNED = re.compile(r"^-?[0-9]+$")
_NUMERIC = {"inputmode": "numeric"}


# --- normalising (both sides of the comparison) ---------------------------------------------------


def _unset(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _trimmed(value: Any) -> Any:
    return norm_text(value) if isinstance(value, str) else value


def anchor_to_write(anchor: Any) -> Any:
    """A stored or cleaned anchor normalised: parts in schema order, text trimmed, unset parts
    left out; no part set → ``None``. A non-object is kept as is."""
    if not isinstance(anchor, Mapping):
        return anchor
    out: dict[str, Any] = {}
    for key in ANCHOR_MEMBERS:
        value = anchor.get(key)
        if key in ("weekday", "band"):
            value = _trimmed(value)
        if not _unset(value):
            out[key] = value
    return out or None


def _row(row: Any, names: Sequence[str]) -> list[Any]:
    """A table row as exactly :data:`WIDTH` cells, positionally (a band's text trimmed): how
    the row's form reads it back."""
    cells = list(row) if isinstance(row, list) else [row]
    cells = [*cells[:WIDTH], *([None] * (WIDTH - len(cells)))]
    if names is BAND_FIELDS:
        cells = [c.strip() if isinstance(c, str) else c for c in cells]
    return cells


def table_to_write(table: Any) -> Any:
    """A pacing table or band list as stored; no rows → ``None`` (the Engine's default)."""
    if not isinstance(table, list):
        return table
    return list(table) or None


def kept_rows(rows: Sequence[list[Any]], stored: Any, member: str) -> list[Any]:
    """The rows sent for a table: a row that reads back as the stored row at its index
    (:func:`_row`) is that stored row as is, so a stored row not 3 cells wide is never resent
    unless it is edited."""
    old = stored if isinstance(stored, list) else []
    return [
        old[i] if i < len(old) and _row(old[i], TABLES[member]) == row else row
        for i, row in enumerate(rows)
    ]


def clock_to_write(clock: Any) -> Any:
    """``starting_state.clock`` normalised (module docstring); ``None`` when no member is set
    (a stored ``{}`` included). A non-object is kept as is."""
    if not isinstance(clock, Mapping):
        return clock
    out: dict[str, Any] = {}
    for key in CLOCK_MEMBERS:
        value = clock.get(key)
        if key == "anchor":
            value = anchor_to_write(value)
        elif key in TABLES:
            value = table_to_write(value)
        if not _unset(value):
            out[key] = value
    return out or None


def event_to_write(entry: Any) -> Any:
    """One ``ScenarioScheduledEvent`` normalised: members in schema order, the proposition
    trimmed, unset ones and empty lists left out, ``planned`` kept as its JSON type (``130`` and
    ``"02:10"`` differ). A non-object entry is kept as is."""
    if not isinstance(entry, Mapping):
        return entry
    out: dict[str, Any] = {}
    for key in (*EVENT_MEMBERS, *(k for k in entry if k not in EVENT_MEMBERS)):
        value = entry.get(key)
        if key == "proposition":
            value = _trimmed(value)
        if not _unset(value):
            out[key] = value
    return out


# --- the editor's POST state from a document ------------------------------------------------------


def _cell(value: Any) -> str | None:
    return None if value is None else str(value)


def table_rows(table: Any, names: Sequence[str]) -> list[dict[str, Any]]:
    """A stored table as its rows' posted values, positionally (:func:`_row`); a row not 3
    cells wide carries its width (the hidden ``cells`` field, the row's note)."""
    rows: list[dict[str, Any]] = []
    for row in table if isinstance(table, list) else []:
        cells = _row(row, names)
        values: dict[str, Any] = {name: _cell(cells[i]) for i, name in enumerate(names)}
        width = len(row) if isinstance(row, list) else 1
        if width != WIDTH:
            values[CELLS] = str(width)
        rows.append(values)
    return rows


def event_offered(role_ids: Sequence[str], groups: Sequence[str]) -> list[str]:
    """An event's audience checkboxes: the role ids, the offered groups, then ``world``."""
    return list(dict.fromkeys([*role_ids, *groups, WORLD]))


def event_editor_row(entry: Any, offered: Iterable[str]) -> dict[str, Any]:
    """A normalised event as its ``evt`` row's posted values (``None``: no value): the stored
    audience tokens the checkboxes offer ticked, the rest in "Other audience"."""
    if not isinstance(entry, Mapping):
        return {}
    known = set(offered)
    audience = [a for a in entry.get("audience") or [] if isinstance(a, str)]
    ticked = [a for a in audience if a in known]
    other = [a for a in audience if a not in known]
    planned = entry.get("planned")
    return {
        "proposition": entry.get("proposition"),
        "planned": None if planned is None else str(planned),
        "subject": entry.get("subject"),
        "owner": entry.get("owner"),
        "place": entry.get("place"),
        "disclosure": entry.get("disclosure"),
        "participants": list(entry.get("participants") or []) or None,
        "audience": ticked or None,
        "audience_other": ", ".join(other) if other else None,
    }


# --- the clock's main-form fields -----------------------------------------------------------------


class AnchorNumberField(forms.CharField):
    """An anchor's year, month or day: ASCII digits (``signed``: a leading ``-``), as an
    ``int`` within ``minimum`` … :data:`INT_MAX`; blank → ``None``. No HTML min/max (C10)."""

    def __init__(self, *, minimum: int, signed: bool = False, **kwargs: Any) -> None:
        self.minimum = minimum
        self.signed = signed
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.TextInput(attrs={**_NUMERIC, "size": 8}))
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> int | None:  # type: ignore[override]
        text = super().to_python(value)
        if not text:
            return None
        ok = _SIGNED.match(text) if self.signed else (text.isascii() and text.isdigit())
        if not ok:
            raise forms.ValidationError(WHOLE_NUMBER, code="invalid")
        number = int(text)
        if number > INT_MAX:
            raise forms.ValidationError(INT_TOO_BIG, code="max_value")
        if number < self.minimum:
            message = AT_LEAST_ONE if self.minimum == 1 else INT_TOO_SMALL
            raise forms.ValidationError(message, code="min_value")
        return number


class ShortTextField(forms.CharField):
    """A trimmed name of 1 to ``limit`` characters; blank → ``None``. The length is checked here
    (no ``maxlength`` attribute), with "At most <n> characters"."""

    def __init__(self, *, limit: int = TEXT_MAX, **kwargs: Any) -> None:
        self.limit = limit
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> str | None:
        text = norm_text(super().to_python(value))
        if text is not None and len(text) > self.limit:
            raise forms.ValidationError(TOO_LONG.format(n=self.limit), code="max_length")
        return text


class TimeOfDayField(forms.CharField):
    """``HH:MM`` exactly (00 to 23, 00 to 59: the Engine's ``TimeOfDay``), kept as the string; blank
    → ``None``. "6:00" is refused, never padded."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.TextInput(attrs={"size": 5}))
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> str | None:
        text = super().to_python(value)
        if not text:
            return None
        if not _TIME.match(text):
            raise forms.ValidationError(TIME_OF_DAY_INVALID, code="invalid")
        return text


def _c7(label: str, member: str, choices: list[tuple[str, str]], **kwargs: Any) -> forms.Field:
    return forms.ChoiceField(
        label=label,
        required=False,
        choices=[engine_default_choice(C7_DEFAULTS[member]), *choices],
        **kwargs,
    )


# The C7 selects' explicit choices (the stored value outside them is added per form).
C7_CHOICES = {
    f"{CLOCK}__mode": MODE_CHOICES,
    STOP_FIELD: YES_NO,
    f"{CLOCK}__time_awareness": TIME_AWARENESS_CHOICES,
}


def clock_fields() -> dict[str, forms.Field]:
    """The Clock section's main-form fields, in ``ScenarioClock``'s schema order (the anchor's
    parts first; the two tables are the editor's row sets)."""
    return {
        f"{ANCHOR}__year": AnchorNumberField(label="Year", minimum=INT_MIN, signed=True),
        f"{ANCHOR}__month": AnchorNumberField(label="Month", minimum=1),
        f"{ANCHOR}__day": AnchorNumberField(label="Day", minimum=1),
        f"{ANCHOR}__weekday": ShortTextField(label="Weekday", help_text=WEEKDAY_HELP),
        f"{ANCHOR}__time": TimeOfDayField(label="Time of day"),
        f"{ANCHOR}__band": ShortTextField(label="Band", help_text=BAND_HELP),
        f"{CLOCK}__mode": _c7("Mode", "mode", MODE_CHOICES),
        f"{CLOCK}__max_unprompted_skip_minutes": WholeNumberField(
            label="Max unprompted skip (minutes)",
            required=False,
            invalid=SKIP_INVALID,
            help_text=SKIP_HELP,
        ),
        STOP_FIELD: _c7("Stop at due events", "stop_at_due_events", YES_NO),
        f"{CLOCK}__time_awareness": _c7("Time awareness", "time_awareness", TIME_AWARENESS_CHOICES),
        CALENDAR_FIELD: JsonBoxField(
            dict,
            empty=None,
            required=False,
            label="Calendar",
            help_text=CALENDAR_HELP,
            widget=forms.Textarea(
                attrs={"rows": 6, "cols": 80, "spellcheck": "false", "class": "monospace"}
            ),
        ),
    }


CLOCK_FIELDS = tuple(clock_fields())
ANCHOR_FIELDS = tuple(name for name in CLOCK_FIELDS if name.startswith(f"{ANCHOR}__"))
SETTING_FIELDS = (
    f"{CLOCK}__max_unprompted_skip_minutes",
    STOP_FIELD,
    f"{CLOCK}__time_awareness",
)


def keep_clock_choices(form: forms.Form, data: Any) -> None:
    """C7: a posted (stored) mode, stop or awareness value outside the choices is kept as one
    more choice, humanised."""
    for name, choices in C7_CHOICES.items():
        field = form.fields.get(name)
        if isinstance(field, forms.ChoiceField):
            first = cast("list[tuple[str, str]]", field.choices)[0]
            field.choices = [first, *kept_choices(choices, data.get(name) or None)]


# --- the row tables (#243's rows) -----------------------------------------------------------------


class _WidthMixin(forms.Form):
    """A hidden ``cells`` field: a stored row's width when it isn't 3 (the row's note); it never
    makes a row filled."""

    def note(self) -> str:
        raw = self.data.get(self.add_prefix(CELLS)) if self.is_bound else None
        return ROW_WIDTH_NOTE.format(n=raw) if isinstance(raw, str) and raw.isdigit() else ""

    def has_changed(self) -> bool:
        return any(name != CELLS for name in self.changed_data)


def _cells_field() -> forms.CharField:
    return forms.CharField(required=False, widget=forms.HiddenInput)


class ScenarioPacingRowForm(_WidthMixin, PacingRowForm):
    """#243's pacing row (``[from_words, to_words | null, minutes]``) with the width note."""

    cells = _cells_field()


class ScenarioBandRowForm(_WidthMixin, BandRowForm):
    """#243's band row (``[name, from, to]``) with the width note."""

    cells = _cells_field()


# formset_factory builds a subclass of ``formset=``; the stubs type it as the generic base.
PacingRows = cast(
    type[RowFormSet],
    formset_factory(ScenarioPacingRowForm, formset=RowFormSet, extra=0, max_num=1000),
)
BandRows = cast(
    type[RowFormSet],
    formset_factory(ScenarioBandRowForm, formset=RowFormSet, extra=0, max_num=1000),
)
ROW_CAPS = {PACING_PREFIX: PACING_CAP, BANDS_PREFIX: BANDS_CAP, EVENTS: EVENTS_MAX}


# --- the scheduled events -------------------------------------------------------------------------


def _kept(choices: list[tuple[str, str]], keep: Iterable[str]) -> list[tuple[str, str]]:
    """``choices`` plus each of ``keep`` it lacks, labelled "<id> (missing)"."""
    known = {value for value, _ in choices}
    extra = [(v, f"{v}{MISSING_SUFFIX}") for v in dict.fromkeys(keep) if v and v not in known]
    return [*choices, *extra]


class EventForm(forms.Form):
    """One ``evt`` row: a ``ScenarioScheduledEvent`` (module docstring). ``roles``,
    ``locations`` and ``groups`` are the form's own ids and offered group names in the current
    form state; ``stored`` the draft's event at this row's index (C9's order)."""

    proposition = ShortTextField(label="Event", limit=PROPOSITION_MAX)
    planned = PlannedField(
        label="Planned",
        required=False,
        help_text=PLANNED_HELP,
        widget=forms.TextInput(attrs={"size": 6}),
    )
    subject = forms.ChoiceField(label="Subject", required=False, help_text=SUBJECT_HELP)
    owner = forms.ChoiceField(label="Owner", required=False, help_text=OWNER_HELP)
    place = forms.ChoiceField(label="Place", required=False)
    disclosure = forms.ChoiceField(label="Disclosure", required=False)
    participants = forms.MultipleChoiceField(
        label="Participants", required=False, widget=forms.CheckboxSelectMultiple
    )
    audience = forms.MultipleChoiceField(
        label="Audience", required=False, widget=forms.CheckboxSelectMultiple
    )
    audience_other = forms.CharField(
        label="Other audience",
        required=False,
        help_text=OTHER_AUDIENCE_HELP,
        widget=forms.TextInput(attrs={"size": 20}),
    )

    LINE_ONE = ("proposition", "planned", "subject", "owner", "place", "disclosure")
    LINE_TWO = ("participants", "audience", "audience_other")

    def __init__(
        self,
        *args: Any,
        roles: Sequence[str] = (),
        locations: Sequence[str] = (),
        groups: Sequence[str] = (),
        stored: Any = None,
        **kwargs: Any,
    ) -> None:
        from console.forms.scenario import NO_LOCATIONS, NO_ROLES  # the editor imports this

        super().__init__(*args, **kwargs)
        self.stored: Mapping[str, Any] = stored if isinstance(stored, Mapping) else {}
        self.sent_event: dict[str, Any] | None = None
        role_choices = [(v, v) for v in roles]
        selects: dict[str, list[tuple[str, str]]] = {
            "subject": [("", ENGINE_DEFAULT_LABEL), *role_choices],
            "owner": [engine_default_choice(WORLD), (WORLD, WORLD), *role_choices],
            "place": [("", ENGINE_DEFAULT_LABEL), *((v, v) for v in locations)],
        }
        for name, choices in selects.items():
            field = cast("forms.ChoiceField", self.fields[name])
            field.choices = _kept(choices, [self.raw(name)])
        disclosure = cast("forms.ChoiceField", self.fields["disclosure"])
        disclosure.choices = [
            ("", ENGINE_DEFAULT_LABEL),
            *kept_choices(DISCLOSURE_CHOICES, self.raw("disclosure") or None),
        ]
        participants = cast("forms.MultipleChoiceField", self.fields["participants"])
        participants.choices = _kept(role_choices, self.ticked("participants"))
        audience = cast("forms.MultipleChoiceField", self.fields["audience"])
        audience.choices = _kept(
            [
                *role_choices,
                *((g, f"{g}{GROUP_SUFFIX}") for g in groups),
                (WORLD, WORLD),
            ],
            self.ticked("audience"),
        )
        if not roles:
            for name in ("subject", "participants"):
                self.fields[name].help_text = NO_ROLES
        if not locations:
            self.fields["place"].help_text = NO_LOCATIONS

    def raw(self, name: str) -> str:
        """A field's posted value, stripped (``""`` when absent)."""
        value = self.data.get(self.add_prefix(name)) if self.is_bound else None
        return value.strip() if isinstance(value, str) else ""

    def ticked(self, name: str) -> list[str]:
        if not self.is_bound or not hasattr(self.data, "getlist"):
            return []
        return [v for v in self.data.getlist(self.add_prefix(name)) if v]

    @property
    def blank(self) -> bool:
        """Every field blank (nothing typed, chosen or ticked): dropped."""
        texts = ("proposition", "planned", "subject", "owner", "place", "disclosure")
        lists = ("participants", "audience")
        return not any(self.raw(n) for n in (*texts, "audience_other")) and not any(
            self.ticked(n) for n in lists
        )

    def line_one(self) -> list[BoundField]:
        return [self[name] for name in self.LINE_ONE]

    def line_two(self) -> list[BoundField]:
        return [self[name] for name in self.LINE_TWO]

    def _chosen(self, name: str, data: Mapping[str, Any]) -> list[str]:
        """The ticked ``name`` values in choice order, through ``keep_order`` against the stored
        event's list (C9: compared as sets, stored order kept, new ones appended)."""
        from console.forms.scenario import keep_order  # the editor imports this module

        field = cast("forms.MultipleChoiceField", self.fields[name])
        ticked = set(data.get(name) or [])
        choices = cast("list[tuple[str, str]]", field.choices)
        chosen = [str(v) for v, _ in choices if v in ticked]
        if name == "audience":
            chosen = list(dict.fromkeys([*chosen, *(data.get("audience_other") or [])]))
        stored = self.stored.get(name)
        if isinstance(stored, list) and stored:
            return keep_order([s for s in stored if isinstance(s, str)], chosen)
        return chosen

    def clean_audience_other(self) -> list[str]:
        return split_audience(self.cleaned_data.get("audience_other") or "")

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if self.blank:
            return data
        if not data.get("proposition") and "proposition" not in self.errors:
            self.add_error("proposition", EVENT_NEEDS_NAME)
        if data.get("planned") is None and "planned" not in self.errors:
            self.add_error("planned", PLANNED_REQUIRED)
        if len(set(data.get("participants") or [])) > PARTICIPANTS_MAX:
            self.add_error("participants", TOO_MANY_PARTICIPANTS)
        if "audience_other" not in self.errors:
            audience = self._chosen("audience", data)
            if len(audience) > AUDIENCE_MAX:
                self.add_error("audience", TOO_MANY_AUDIENCE)
        self.sent_event = self._event(data)
        return data

    def _event(self, data: Mapping[str, Any]) -> dict[str, Any]:
        audience = self._chosen("audience", data) if "audience_other" not in self.errors else []
        return cast(
            "dict[str, Any]",
            event_to_write(
                {
                    "proposition": data.get("proposition"),
                    "subject": data.get("subject") or None,
                    "planned": data.get("planned"),
                    "participants": self._chosen("participants", data),
                    "place": data.get("place") or None,
                    "owner": data.get("owner") or None,
                    "audience": audience,
                    "disclosure": data.get("disclosure") or None,
                }
            ),
        )

    def event(self) -> dict[str, Any]:
        """The ``ScenarioScheduledEvent`` sent (call after validation)."""
        if self.sent_event is None:
            self.sent_event = self._event(self.cleaned_data)
        return self.sent_event


class EventFormSet(BaseFormSet):  # type: ignore[type-arg]
    """The ``evt`` rows; each gets the draft's event at its index as ``stored``."""

    def get_form_kwargs(self, index: int | None) -> dict[str, Any]:
        kwargs = dict(self.form_kwargs)
        events = kwargs.pop("events", None) or []
        if index is not None and index < len(events):
            kwargs["stored"] = events[index]
        return kwargs


# --- the section as the template shows it ---------------------------------------------------------


@dataclass(frozen=True)
class FieldGroup:
    """A run of the Clock section's fields (``_fields.html``'s ``section``)."""

    fields: list[BoundField]


def clock_groups(form: forms.Form) -> dict[str, FieldGroup]:
    """The Clock section's fields in schema order, around the two tables."""

    def group(names: Iterable[str]) -> FieldGroup:
        return FieldGroup([form[name] for name in names if name in form.fields])

    return {
        "anchor": group(ANCHOR_FIELDS),
        "mode": group([f"{CLOCK}__mode"]),
        "settings": group(SETTING_FIELDS),
        "calendar": group([CALENDAR_FIELD]),
    }

"""The World tab's Clock section, read side (#243; D1, D6, D7, D18): the anchor view, the settings
table, and the clock-off and scheduled-stops notices.

**The anchor.** ``ClockBody.anchor`` is ``null`` (unanchored) or ``Anchor.document()``: the year,
month, day, weekday, band, hour and minute **at the anchoring offset** (``anchor.offset``), not
now (D18: the Engine serves no "now" components; ``ClockBody.display`` is the "now" line). Each
component is a value, shown verbatim (the story's own words: the Console never computes a date,
D6), a ``[lo, hi]`` range ("lo … hi", inclusive; " (wraps)" only for an int range whose
``lo > hi``: a wrapped name range needs the calendar's order to detect), ``null`` ("unknown",
muted) or anything else as compact JSON. ``anchored_at`` is four-part (no ``step``) and goes
straight to D7's :func:`console.world.five_part`; ``null`` is the author's starting anchor. A
non-object anchor, or one missing a component key, is shown raw with "This anchor couldn't be
read" (C2: the page never fails); a missing ``display``/``offset`` alone reads "—".

**The settings table** follows ``ClockSettingsBody``'s order. The three read-only settings
(grace, time-statement tolerance, elapsed tolerance factor) are marked "(set at creation)":
``PATCH …/clock`` refuses them as unknown settings. A malformed pacing or band row is shown as
compact JSON in ``<code>``; a custom calendar's missing parts read "—"; an unknown calendar
``kind`` is shown raw.

Notices (backtick spans are code, rendered through ``code_spans``): :func:`clock_off_notice` when
``m6_gate(CLOCK, effective)`` is closed, and :func:`stops_note` when
``m6_gate(SCHEDULED_STOPS, effective)`` is.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse

from console.engine_errors import EMPTY
from console.inspect import CLOCK, SCHEDULED_STOPS, SETTINGS, TIME_CLAIMS, m6_gate, setting
from console.positions import knowledge_position_text, knowledge_position_url
from console.wording import humanised
from console.world import five_part, offset_text

UNANCHORED = "Unanchored — the story hasn't established a date or time of day yet"
ANCHOR_UNREADABLE = "This anchor couldn't be read"
UNKNOWN = "unknown"
ELLIPSIS = "…"
WRAPS = " (wraps)"
AT_START = "Established at the story's start"
SET_AT_CREATION = "(set at creation)"
NEVER_SKIPS = " (0: a render never skips on its own)"
NO_END = "no end"
CLOCK_OFF = (
    "The clock is off under this story's configuration: {reason} — its settings can still be "
    "changed, but nothing advances it"
)
STOPS_NOTE = "Stops also need the scheduled-stops module: {reason}"

COMPONENTS: tuple[tuple[str, str], ...] = (
    ("year", "Year"),
    ("month", "Month"),
    ("day", "Day"),
    ("weekday", "Weekday"),
    ("band", "Band"),
    ("hour", "Hour"),
    ("minute", "Minute"),
)
FLAGS: tuple[tuple[str, str], ...] = (
    (TIME_CLAIMS, "Time-claim verification"),
    (SCHEDULED_STOPS, "Scheduled-event stops"),
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def minutes_text(value: Any) -> str:
    """ "1,440 min"; "—" for a non-int."""
    return f"{value:,} min" if _is_int(value) else EMPTY


# --- the anchor ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentCell:
    """One anchor component: its label, its text, and ``kind`` (``value``, ``range``,
    ``unknown`` or ``json``: the last rendered in ``<code>``)."""

    key: str
    label: str
    text: str
    kind: str


def component_cell(key: str, label: str, value: Any) -> ComponentCell:
    """A component's cell: a value verbatim, a ``[lo, hi]`` range "lo … hi" (" (wraps)" for an
    int range with ``lo > hi``), ``None`` "unknown", anything else compact JSON."""
    if value is None:
        return ComponentCell(key, label, UNKNOWN, "unknown")
    if isinstance(value, str) or _is_int(value):
        return ComponentCell(key, label, str(value), "value")
    if isinstance(value, list) and len(value) == 2:
        lo, hi = value
        if _is_int(lo) and _is_int(hi):
            wraps = WRAPS if lo > hi else ""
            return ComponentCell(key, label, f"{lo} {ELLIPSIS} {hi}{wraps}", "range")
        if isinstance(lo, str) and isinstance(hi, str):
            return ComponentCell(key, label, f"{lo} {ELLIPSIS} {hi}", "range")
    return ComponentCell(key, label, _json(value), "json")


@dataclass(frozen=True)
class AnchorView:
    """An anchor as the Clock section shows it. ``readable`` false: only ``raw`` (the whole
    anchor) is shown, with :data:`ANCHOR_UNREADABLE`."""

    readable: bool
    raw: Any
    components: tuple[ComponentCell, ...] = ()
    display: str = EMPTY
    offset: str = EMPTY
    established: str = EMPTY
    established_url: str | None = None
    more: tuple[tuple[str, Any], ...] = ()


def _established(story_id: str, anchored_at: Any) -> tuple[str, str | None]:
    if anchored_at is None:
        return AT_START, None
    position = five_part(anchored_at)  # D7: a missing ``step`` defaults to 0
    if position is None:
        return f"Established at {EMPTY}", None
    return (
        f"Established at {knowledge_position_text(position)}",
        knowledge_position_url(story_id, position),
    )


def anchor_view(anchor: Any, story_id: str = "") -> AnchorView | None:
    """``None`` for an unanchored clock (``anchor`` ``null``); else the component table, the
    "As established" line, the offset it was established at, where it was established and the
    More members, or (a non-object anchor, a missing component key) the raw anchor alone."""
    if anchor is None:
        return None
    if not isinstance(anchor, Mapping) or any(key not in anchor for key, _ in COMPONENTS):
        return AnchorView(readable=False, raw=anchor)
    display = anchor.get("display")
    established, url = _established(story_id, anchor.get("anchored_at"))
    return AnchorView(
        readable=True,
        raw=anchor,
        components=tuple(component_cell(key, label, anchor[key]) for key, label in COMPONENTS),
        display=display if isinstance(display, str) else EMPTY,
        offset=offset_text(anchor["offset"]) if _is_int(anchor.get("offset")) else EMPTY,
        established=established,
        established_url=url,
        more=tuple((name, anchor.get(name)) for name in ("revision", "reference", "segments")),
    )


# --- the settings table -------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """A nested-table cell; ``code``: compact JSON in ``<code>``; ``muted``: a placeholder."""

    text: str
    code: bool = False
    muted: bool = False


@dataclass(frozen=True)
class SettingRow:
    """One row of the settings table: its label and text (``title``: the raw enum), an optional
    note, nested table (``headers``/``rows``; a malformed row is one ``code`` cell), extra lines
    (a custom calendar's) and More JSON."""

    key: str
    label: str
    text: str = ""
    title: str = ""
    note: str = ""
    code: bool = False
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[Cell, ...], ...] = ()
    lines: tuple[str, ...] = ()
    more: Any = None
    has_more: bool = False
    read_only: bool = False


def _enum_row(key: str, label: str, value: Any) -> SettingRow:
    if isinstance(value, str):
        return SettingRow(key, label, humanised(value), title=value)
    return SettingRow(key, label, _json(value), code=True)


def _pacing_rows(table: Any) -> tuple[tuple[Cell, ...], ...]:
    rows: list[tuple[Cell, ...]] = []
    for row in table if isinstance(table, list) else []:
        if (
            isinstance(row, list)
            and len(row) == 3
            and _is_int(row[0])
            and (row[1] is None or _is_int(row[1]))
            and _is_int(row[2])
        ):
            to = Cell(NO_END, muted=True) if row[1] is None else Cell(f"{row[1]:,}")
            rows.append((Cell(f"{row[0]:,}"), to, Cell(f"{row[2]:,}")))
        else:
            rows.append((Cell(_json(row), code=True),))
    return tuple(rows)


def _band_rows(bands: Any) -> tuple[tuple[Cell, ...], ...]:
    rows: list[tuple[Cell, ...]] = []
    for row in bands if isinstance(bands, list) else []:
        if isinstance(row, list) and len(row) == 3 and all(isinstance(c, str) for c in row):
            rows.append(tuple(Cell(c) for c in row))
        else:
            rows.append((Cell(_json(row), code=True),))
    return tuple(rows)


def _calendar_row(calendar: Any) -> SettingRow:
    """ "Gregorian"; "Custom" with day minutes, the months table, weekdays and year days ("—"
    for a missing part); an unknown ``kind`` raw. The full object is always in More."""
    label = "Calendar"
    if not isinstance(calendar, Mapping):
        return SettingRow(
            "calendar", label, _json(calendar), code=True, more=calendar, has_more=True
        )
    kind = calendar.get("kind")
    if kind == "gregorian":
        return SettingRow("calendar", label, "Gregorian", more=calendar, has_more=True)
    if kind != "custom":
        return SettingRow("calendar", label, _json(kind), code=True, more=calendar, has_more=True)
    day = calendar.get("day_minutes")
    year = calendar.get("year_days")
    weekdays = calendar.get("weekdays")
    named = (
        ", ".join(str(w) for w in weekdays) if isinstance(weekdays, list) and weekdays else EMPTY
    )
    months: list[tuple[Cell, ...]] = []
    listed = calendar.get("months")
    for month in listed if isinstance(listed, list) else []:
        if isinstance(month, Mapping):
            name, days = month.get("name"), month.get("days")
            months.append(
                (
                    Cell(name if isinstance(name, str) else EMPTY),
                    Cell(f"{days:,}" if _is_int(days) else EMPTY),
                )
            )
        else:
            months.append((Cell(_json(month), code=True),))
    return SettingRow(
        "calendar",
        label,
        "Custom",
        headers=("Name", "Days") if months else (),
        rows=tuple(months),
        lines=(
            f"Day minutes: {f'{day:,}' if _is_int(day) else EMPTY}",
            f"Weekdays: {named}",
            f"Year days: {f'{year:,}' if _is_int(year) else EMPTY}",
        ),
        more=calendar,
        has_more=True,
    )


def _read_only(key: str, label: str, text: str) -> SettingRow:
    return SettingRow(key, label, text, note=SET_AT_CREATION, read_only=True)


def settings_rows(settings: Any, *, stops_closed: Any = None) -> list[SettingRow]:
    """The settings table, in ``ClockSettingsBody``'s order. ``settings`` is a
    ``ClockSettings`` (or any object with its attributes); ``stops_closed`` is the closed
    ``m6_gate(SCHEDULED_STOPS, …)`` (its reason goes under "Stop at due events")."""

    def get(name: str) -> Any:
        if isinstance(settings, Mapping):
            return settings.get(name)
        return getattr(settings, name, None)

    skip = get("max_unprompted_skip_minutes")
    skip_text = minutes_text(skip) + (NEVER_SKIPS if skip == 0 and _is_int(skip) else "")
    stop = get("stop_at_due_events")
    factor = get("elapsed_tolerance_factor")
    return [
        _enum_row("mode", "Mode", get("mode")),
        SettingRow(
            "pacing_table",
            "Pacing table",
            headers=("From words", "To words (exclusive)", "Minutes"),
            rows=_pacing_rows(get("pacing_table")),
        ),
        SettingRow("max_unprompted_skip_minutes", "Max unprompted skip", skip_text),
        SettingRow(
            "stop_at_due_events",
            "Stop at due events",
            "Yes" if stop is True else "No" if stop is False else _json(stop),
            note=stops_note(stops_closed) if stops_closed is not None else "",
        ),
        _read_only("grace_minutes", "Grace", minutes_text(get("grace_minutes"))),
        _read_only(
            "time_statement_tolerance_minutes",
            "Time-statement tolerance",
            minutes_text(get("time_statement_tolerance_minutes")),
        ),
        _read_only(
            "elapsed_tolerance_factor",
            "Elapsed tolerance factor",
            str(factor)
            if isinstance(factor, int | float) and not isinstance(factor, bool)
            else EMPTY,
        ),
        _enum_row("time_awareness", "Time awareness", get("time_awareness")),
        SettingRow("bands", "Bands", headers=("Name", "From", "To"), rows=_band_rows(get("bands"))),
        _calendar_row(get("calendar")),
    ]


# --- the section's other lines ------------------------------------------------------------------


def clock_off_notice(effective: Mapping[str, Any] | None) -> str | None:
    """The clock-off notice when ``m6_gate(CLOCK, effective)`` is closed (D1's reason)."""
    closed = m6_gate(CLOCK, effective)
    return None if closed is None else CLOCK_OFF.replace("{reason}", closed.reason)


def stops_note(closed: Any) -> str:
    """ "Stops also need the scheduled-stops module: <reason>"."""
    return STOPS_NOTE.replace("{reason}", str(getattr(closed, "reason", closed)))


def flag_texts(effective: Mapping[str, Any] | None) -> list[str]:
    """ "Time-claim verification on", "Scheduled-event stops off" (D2's labels) for each flag
    whose switch is in ``effective``; a non-boolean value raw."""
    found: list[str] = []
    for module, label in FLAGS:
        path = SETTINGS[module]
        parent, _, leaf = path.rpartition(".")
        holder = setting(effective, parent) if effective is not None else None
        if not isinstance(holder, Mapping) or leaf not in holder:
            continue
        value = holder[leaf]
        word = "on" if value is True else "off" if value is False else _json(value)
        found.append(f"{label} {word}")
    return found


def external_names(settings: Any) -> tuple[str, str]:
    """``(provider_id, external_value_name)`` of an external clock, "—" for a missing one."""

    def text(name: str) -> str:
        value = getattr(settings, name, None)
        return value if isinstance(value, str) and value else EMPTY

    return text("provider_id"), text("external_value_name")


def clock_url(story_id: str) -> str:
    return reverse("inspect:clock", args=[story_id])


def settings_url(story_id: str) -> str:
    return reverse("inspect:clock-settings", args=[story_id])


def advance_url(story_id: str) -> str | None:
    """``inspect:clock-advance`` (#252), ``None`` while it isn't registered (D19)."""
    try:
        return reverse("inspect:clock-advance", args=[story_id])
    except NoReverseMatch:
        return None


@dataclass(frozen=True)
class ReadOnlyLine:
    """A read-only setting under the settings form."""

    label: str
    text: str


def read_only_lines(settings: Mapping[str, Any]) -> list[ReadOnlyLine]:
    """The three read-only settings as text (the settings form's footer)."""
    return [ReadOnlyLine(row.label, row.text) for row in settings_rows(settings) if row.read_only]

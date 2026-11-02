"""The World tab's Scheduled events (#244): the rows, the header lines and their wording.

``GET /stories/{id}/scheduled-events`` (FR-CLOCK-9) lists, for one viewpoint (D4), the canon
events it holds (omniscient: every one; a character: the ones it takes part in) and, for a
character under per-character partitions, its own belief-only schedules. The Engine sends them
by planned time, canon first; rows keep that order.

One event reads through :func:`event_row` → :class:`EventRow`:

- ``label`` (C3, raw in ``title``): "Canon" / "Belief"; anything whose label isn't exactly
  ``belief`` follows the canon rules, and its row class is ``other`` (never the raw value).
- ``status``, ``confidence`` humanised (C3/D10, raw in ``title``); ``None`` reads "—".
- ``planned`` and ``stopped_clock_at`` through D6's ``offset_text`` with ``offset <n> min`` in
  ``title``.
- ``owner``: an id in the shell's ``names`` → its C4 label; ``author``/``world`` humanised; any
  other string raw in ``<code>`` (no ULID sniffing); ``None`` "—".
- ``shared_owners``/``participants``: C4's ``character_label``, comma-joined, the Engine's order.
- ``place_id``: convention 2's short id linked to ``inspect:entity`` (no entity-name read), plain
  on ``NoReverseMatch``.
- **A belief never stops the clock.** :attr:`EventRow.stopped_text` decides on
  ``label == "belief"``, not on ``stopped_clock_at`` being ``None``: a belief always reads
  :data:`BELIEF_NEVER_STOPS`, whatever the row carries.

Header lines (:func:`off_line`, :func:`stops_line`, :func:`count_text`, :func:`empty_text`,
:func:`partitions_note`) come from the effective configuration (D1's gates) and the section's
one ``get_clock`` read. The scheduled-events list itself is never gated (D1).

**Writes (#253).** :data:`WRITE_KINDS` names the #44 kinds of the three event writes (create
#253; reschedule and cancel #262) with their result-page verb; :func:`written_row` reads one
back for a ``?report=<pk>`` result page: only an ``ok`` row of one of those kinds, of this story,
whose stored response parses as ``ScheduledEventWritten``.

The viewpoint line is D4's ``console.world.viewpoint_line`` with this section's two notes
(:data:`EVENTS_NOTE`, :data:`EVENTS_OMNISCIENT_NOTE`); it is never redefined here (D17).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse

from console.cast import OMNISCIENT
from console.engine.models import ScheduledEvent, ScheduledEventWritten, WorldClock
from console.engine_errors import EMPTY
from console.inspect import CLOCK, PARTITIONS, SCHEDULED_STOPS, m5_gate, m6_gate
from console.models import EngineOperation
from console.states import viewpoint_label
from console.wording import character_label, humanised
from console.world import offset_text

CANON = "canon"
BELIEF = "belief"
OTHER = "other"
OWNER_KEYWORDS = frozenset({"author", "world"})

EVENTS_OMNISCIENT_NOTE = "every canon event"
EVENTS_NOTE = "only events {label} takes part in, and {label}'s own belief-only schedules"

UNNAMED = "(unnamed)"
BELIEF_NEVER_STOPS = "Never — a belief doesn't stop the clock"
STOPPED_AT = "at {offset}"

STOPS_ON = "Due events stop the clock"
STOPS_OFF = "Due events don't stop the clock: {reason}"
STOPS_SETTING_OFF_HEAD = "Due events don't stop the clock: the clock's "
STOPS_SETTING_LINK = "Stop at due events setting"
STOPS_SETTING_OFF_TAIL = " is off"
CLOCK_OFF = (
    "The clock is off under this story's configuration: {reason} — events are listed, but none "
    "comes due"
)
CLOCK_UNREAD = "Couldn't read the clock"
PARTITIONS_NOTE = "Belief-only schedules aren't listed: {reason}"

# #253: the #44 kinds of the event writes, and each one's result-page verb.
WRITE_KINDS: Mapping[str, str] = {
    "create_scheduled_event": "Scheduled",
    "reschedule_scheduled_event": "Rescheduled",
    "cancel_scheduled_event": "Cancelled",
}

EVENTS_EMPTY = "No scheduled events"
EVENTS_EMPTY_FOR = "{label} takes part in no scheduled event and holds no belief-only schedule"


def count_text(n: int, revision: int) -> str:
    """C11's count line: "3 events · revision 12" / "1 event · revision 12"."""
    noun = "event" if n == 1 else "events"
    return f"{n:,} {noun} · revision {revision}"


def unparsed_text(n: int) -> str:
    """D15's notice for the rejected entries: "1 event couldn't be read" / "N events…"."""
    return "1 event couldn't be read" if n == 1 else f"{n:,} events couldn't be read"


def empty_text(viewpoint: str | None, names: Mapping[str, str]) -> str:
    """ "No scheduled events" (omniscient), or "Alice takes part in no scheduled event and holds
    no belief-only schedule"."""
    label = viewpoint_label(viewpoint, names)
    return EVENTS_EMPTY if label is None else EVENTS_EMPTY_FOR.replace("{label}", label)


def off_line(effective: Mapping[str, Any] | None) -> str | None:
    """The clock-off line when ``m6_gate(CLOCK)`` is closed (backtick spans are code), else
    ``None``. It follows the configuration only, never the clock read's ``level``."""
    closed = m6_gate(CLOCK, effective)
    return None if closed is None else CLOCK_OFF.replace("{reason}", closed.reason)


def partitions_note(viewpoint: str | None, effective: Mapping[str, Any] | None) -> str | None:
    """For a character viewpoint whose partitions gate is closed, why its belief-only schedules
    aren't listed (the Engine lists them only under per-character partitions); else ``None``."""
    if not viewpoint or viewpoint == OMNISCIENT:
        return None
    closed = m5_gate(PARTITIONS, effective)
    return None if closed is None else PARTITIONS_NOTE.replace("{reason}", closed.reason)


@dataclass(frozen=True)
class StopsLine:
    """The stops line: ``text`` (backtick spans are code), then, for the setting-off case,
    ``link_text`` (linked to ``url`` when it resolves, D19) and ``tail``."""

    text: str
    on: bool = False
    link_text: str = ""
    url: str | None = None
    tail: str = ""


def clock_settings_url(story_id: str) -> str | None:
    """``inspect:clock-settings`` (#243), ``None`` while it doesn't resolve (D19)."""
    try:
        return reverse("inspect:clock-settings", args=[story_id])
    except NoReverseMatch:
        return None


def stops_line(
    effective: Mapping[str, Any] | None, clock: WorldClock, story_id: str
) -> StopsLine | None:
    """The stops line (``None`` when the clock gate is closed: the off line says it once).
    Stops are on only when ``m6_gate(SCHEDULED_STOPS)`` is open and
    ``settings.stop_at_due_events is True``; a closed gate gives its reason alone."""
    if m6_gate(CLOCK, effective) is not None:
        return None
    closed = m6_gate(SCHEDULED_STOPS, effective)
    if closed is not None:
        return StopsLine(STOPS_OFF.replace("{reason}", closed.reason))
    if clock.settings.stop_at_due_events is True:
        return StopsLine(STOPS_ON, on=True)
    return StopsLine(
        STOPS_SETTING_OFF_HEAD,
        link_text=STOPS_SETTING_LINK,
        url=clock_settings_url(story_id),
        tail=STOPS_SETTING_OFF_TAIL,
    )


@dataclass(frozen=True)
class Offset:
    """A bare minutes value (D6): ``offset_text`` and the raw minutes for ``title``."""

    text: str
    minutes: int | None


def _offset(minutes: int | None) -> Offset:
    number = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else None
    return Offset(offset_text(number), number)


def _enum(raw: str | None) -> tuple[str, str]:
    """(humanised, raw) for an Engine string; ``None`` or blank → ("—", "")."""
    if not isinstance(raw, str) or not raw:
        return EMPTY, ""
    return humanised(raw), raw


@dataclass(frozen=True)
class OwnerCell:
    """The Owner cell: ``text``, shown in ``<code>`` when ``code`` (an owner the Console can't
    name), and the raw value for More."""

    text: str
    code: bool
    raw: str | None


def owner_cell(owner: str | None, names: Mapping[str, str]) -> OwnerCell:
    """A cast id → C4 label; ``author``/``world`` → "Author"/"World"; ``None``/blank → "—";
    anything else raw in ``<code>``."""
    if not isinstance(owner, str) or not owner:
        return OwnerCell(EMPTY, False, None)
    if owner in names:
        return OwnerCell(character_label(owner, names), False, owner)
    if owner in OWNER_KEYWORDS:
        return OwnerCell(humanised(owner), False, owner)
    return OwnerCell(owner, True, owner)


def labels_text(ids: Iterable[str], names: Mapping[str, str]) -> str:
    """C4 labels, comma-joined in the order given, or "—"."""
    labels = [character_label(cid, names) for cid in ids]
    return ", ".join(labels) if labels else EMPTY


def ids_text(ids: Sequence[str]) -> str:
    """The raw ids for More, comma-joined, or "—"."""
    return ", ".join(ids) if ids else EMPTY


def place_url(story_id: str, place_id: str | None) -> str | None:
    """``inspect:entity`` for the place (convention 2: no name read), ``None`` when it can't be a
    page URL (D19's plain rendering)."""
    if not place_id:
        return None
    try:
        return reverse("inspect:entity", args=[story_id, place_id])
    except NoReverseMatch:
        return None


@dataclass(frozen=True)
class EventRow:
    """``_scheduled_event_row.html``'s context for one event. ``label`` and ``stopped_clock_at``
    stay raw; the belief rule lives in :attr:`stopped_text`. Enum cells are (label, raw)."""

    event_id: str
    name: str | None
    label: str
    row_class: str
    kind: tuple[str, str]
    status: tuple[str, str]
    planned: Offset
    owner: OwnerCell
    shared_text: str
    participants_text: str
    place_id: str | None
    place_url: str | None
    grace_text: str
    stopped_clock_at: int | None
    confidence: tuple[str, str]
    more_participants: str
    more_shared: str

    @property
    def is_belief(self) -> bool:
        return self.label == BELIEF

    @property
    def stopped(self) -> Offset | None:
        """Where a canon event stopped the clock, ``None`` for a belief or when it didn't."""
        if self.is_belief or self.stopped_clock_at is None:
            return None
        return _offset(self.stopped_clock_at)

    @property
    def stopped_text(self) -> str:
        """ "Never — a belief doesn't stop the clock" for any belief; "at +19h 00m" for a canon
        event that stopped the clock; else "—"."""
        if self.is_belief:
            return BELIEF_NEVER_STOPS
        stopped = self.stopped
        return EMPTY if stopped is None else STOPPED_AT.replace("{offset}", stopped.text)


def _grace(minutes: int | None) -> str:
    if not isinstance(minutes, int) or isinstance(minutes, bool):
        return EMPTY
    return f"{minutes:,} min"


def event_row(event: ScheduledEvent, names: Mapping[str, str], story_id: str) -> EventRow:
    """One event as a row (see the module docstring)."""
    label = event.label if isinstance(event.label, str) else str(event.label)
    row_class = label if label in (CANON, BELIEF) else OTHER
    belief = label == BELIEF
    return EventRow(
        event_id=event.event_id,
        name=event.name or None,
        label=label,
        row_class=row_class,
        kind=_enum(label),
        status=_enum(event.status),
        planned=_offset(event.planned),
        owner=owner_cell(event.owner, names),
        shared_text=labels_text(event.shared_owners, names),
        participants_text=labels_text(event.participants, names),
        place_id=event.place_id or None,
        place_url=place_url(story_id, event.place_id),
        grace_text=_grace(event.grace_minutes),
        stopped_clock_at=event.stopped_clock_at,
        confidence=_enum(event.confidence) if belief else (EMPTY, ""),
        more_participants=ids_text(event.participants),
        more_shared=ids_text(event.shared_owners),
    )


def event_rows(
    events: Iterable[ScheduledEvent], names: Mapping[str, str], story_id: str
) -> list[EventRow]:
    """The rows, in the Engine's order (by planned time, canon first)."""
    return [event_row(event, names, story_id) for event in events]


def written_row(pk: object, story_id: str) -> tuple[EngineOperation, ScheduledEventWritten] | None:
    """The #44 row ``pk`` and its parsed answer when it is an ``ok`` row of one of
    :data:`WRITE_KINDS`, of ``story_id``, whose stored response parses; else ``None`` (a missing,
    blank or non-integer ``pk`` included)."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    if (
        row.kind not in WRITE_KINDS
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    parsed = ScheduledEventWritten.from_json(row.response)
    if parsed is None:
        return None
    return row, parsed

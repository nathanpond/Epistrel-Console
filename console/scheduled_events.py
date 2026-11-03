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

**Changes (#262).** :func:`row_actions` gives a row its "Reschedule" and "Cancel" links
(:class:`RowActions`) only for a ``canon`` event that isn't ``cancelled``/``completed``, on an
omniscient read; every other status (``unknown``, ``possibly_due``, ``missed`` …) gets both and the
Engine decides. The links carry the omniscient read's ``timeline_revision``; Cancel's query is
``name``/``expected_revision``/``next`` (display data the confirm page never acts on). Both are
greyed by D1's clock gate (``gate``). :func:`find_event` finds the URL's event in a list (belief
rows skipped: a belief isn't an event of the story). :class:`EventSnapshot` is the reschedule page's
signed ``{story_id, event_id, name, shared_owners}`` (salt :data:`SNAPSHOT_SALT`, no max age).
:func:`cancel_confirm` builds the cancel confirm page (``console/confirm.py``'s confirmed mode).

The viewpoint line is D4's ``console.world.viewpoint_line`` with this section's two notes
(:data:`EVENTS_NOTE`, :data:`EVENTS_OMNISCIENT_NOTE`); it is never redefined here (D17).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.core import signing
from django.urls import NoReverseMatch, reverse

from console.cast import OMNISCIENT
from console.confirm import ConfirmPage
from console.engine.models import ScheduledEvent, ScheduledEventWritten, WorldClock
from console.engine_errors import EMPTY
from console.inspect import CLOCK, PARTITIONS, SCHEDULED_STOPS, Unavailable, m5_gate, m6_gate
from console.models import EngineOperation
from console.states import viewpoint_label
from console.wording import character_label, humanised
from console.world import EXPECTED_REVISION_FIELD, offset_text

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


# --- reschedule and cancel (#262) ----------------------------------------------------------------

SETTLED_STATUSES = frozenset({"cancelled", "completed"})
SNAPSHOT_SALT = "console.inspect.scheduled_event"
NOT_LISTED = "This event is no longer listed — reload the list"
NO_CHANGES = "No changes — nothing was sent."
CANCEL_TITLE = "Cancel a scheduled event"
CANCEL_QUESTION = "Cancel the scheduled event {name}?"
CANCEL_UNNAMED = "this event"
CANCEL_LINES: tuple[str, ...] = (
    "It becomes cancelled and never stops the clock.",
    "A cancelled event can't be rescheduled; rewinding the story to before this slot undoes it.",
)
CANCEL_LABEL = "Cancel the event"
CANCELLED_FLASH = "Cancelled “{name}” — operation {operation}, story revision now {revision}"


@dataclass(frozen=True)
class RowActions:
    """One changeable row's links, and D1's clock gate (``None``: open) that greys both."""

    reschedule_url: str
    cancel_url: str
    gate: Unavailable | None


def reschedule_url(story_id: str, event_id: str) -> str:
    return reverse("inspect:scheduled-event-reschedule", args=[story_id, event_id])


def cancel_url(story_id: str, event_id: str) -> str:
    return reverse("inspect:scheduled-event-cancel", args=[story_id, event_id])


def changeable(event: ScheduledEvent) -> bool:
    """A canon event that isn't settled (``cancelled``/``completed``)."""
    return event.label == CANON and event.status not in SETTLED_STATUSES


def row_actions(
    event: ScheduledEvent,
    story_id: str,
    revision: int,
    gate: Unavailable | None,
    omniscient: bool,
    *,
    next_url: str,
) -> RowActions | None:
    """The row's Reschedule and Cancel links, or ``None`` (a character's view, a belief, a
    settled event). Cancel carries ``?name=&expected_revision=&next=`` (``next_url``: the
    section's own URL)."""
    if not omniscient or not changeable(event):
        return None
    query = urlencode(
        {"name": event.name or "", EXPECTED_REVISION_FIELD: revision, "next": next_url}
    )
    return RowActions(
        reschedule_url=reschedule_url(story_id, event.event_id),
        cancel_url=f"{cancel_url(story_id, event.event_id)}?{query}",
        gate=gate,
    )


def find_event(events: Iterable[ScheduledEvent], event_id: str) -> ScheduledEvent | None:
    """The canon event ``event_id`` in an (omniscient) list; belief rows never match."""
    for event in events:
        if event.event_id == event_id and event.label != BELIEF:
            return event
    return None


@dataclass(frozen=True)
class EventSnapshot:
    """What the reschedule form rendered from, signed into the page."""

    story_id: str
    event_id: str
    name: str | None
    shared_owners: tuple[str, ...]

    @classmethod
    def of(cls, story_id: str, event: ScheduledEvent) -> EventSnapshot:
        return cls(story_id, event.event_id, event.name or None, tuple(event.shared_owners))

    def signed(self) -> str:
        raw = {
            "story_id": self.story_id,
            "event_id": self.event_id,
            "name": self.name,
            "shared_owners": list(self.shared_owners),
        }
        return signing.dumps(raw, salt=SNAPSHOT_SALT, compress=True)

    @classmethod
    def unsigned(cls, value: str | None, story_id: str, event_id: str) -> EventSnapshot | None:
        """The posted snapshot, or ``None`` when it is missing, tampered with, malformed or of
        another story or event."""
        try:
            raw = signing.loads(value or "", salt=SNAPSHOT_SALT)
        except signing.BadSignature:
            return None
        if not isinstance(raw, dict):
            return None
        if raw.get("story_id") != story_id or raw.get("event_id") != event_id:
            return None
        name = raw.get("name")
        owners = raw.get("shared_owners")
        if name is not None and not isinstance(name, str):
            return None
        if not isinstance(owners, list) or not all(isinstance(o, str) for o in owners):
            return None
        return cls(story_id, event_id, name or None, tuple(owners))


def cancel_confirm(
    story_id: str, event_id: str, name: str, data: Mapping[str, Any], cancel: str
) -> ConfirmPage:
    """The ``inspect:scheduled-event-cancel`` confirm page: ``name`` is display-only
    (``confirm.shown_name``), ``data`` the GET or POST carrying ``expected_revision``."""
    hidden = {"name": name}
    revision = data.get(EXPECTED_REVISION_FIELD)
    if isinstance(revision, str) and revision:
        hidden[EXPECTED_REVISION_FIELD] = revision  # carried as given (no local check)
    return ConfirmPage(
        title=CANCEL_TITLE,
        question=CANCEL_QUESTION,
        emphasis=name or CANCEL_UNNAMED,
        confirm_label=CANCEL_LABEL,
        cancel_url=cancel,
        lines=CANCEL_LINES,
        facts=(("Event id", event_id), ("Story id", story_id)),
        hidden=hidden,
        danger=True,
    )


def cancelled_flash(name: str | None, answer: ScheduledEventWritten) -> str:
    """The no-#44-row fallback flash after a cancel."""
    return (
        CANCELLED_FLASH.replace("{name}", name or UNNAMED)
        .replace("{operation}", answer.operation_id or EMPTY)
        .replace("{revision}", str(answer.timeline_revision))
    )

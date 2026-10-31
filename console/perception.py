"""A turn's perception trace (#195): which characters perceived each beat, and at what fidelity.

The Engine stores no per-beat perception result (docs/02 §4.4; Engine request
nathanpond/Epistrel#607), so the page reconstructs it: one history read per cast member at that
character's viewpoint with ``player_view=story`` (every beat in full, each carrying the
``fidelity`` the character perceived it at). :func:`grid` turns those reads into rows (beats) by
columns (characters); the view (``console/views/perception.py``) makes the reads.

**Rows** are the items of the turn with a non-null ``beat`` (any ``kind``), merged across reads by
:data:`MergeKey` ``(beat, channel, kind, actor_character_id, n)``, ``n`` being the item's
occurrence of that key within its read. Ordered by beat, then by first appearance (reads in cast
order). A row's header text comes from the first read that returned it.

**Cells** reuse #62's fidelity wording and classes (convention 20): ``content`` → "perceived",
``envelope`` → "envelope", ``none`` → "not perceived"; a null fidelity, an unknown value (C3) and
an item absent from that read each have their own cell. A failed read's column reads "?".

Building a link (:func:`perception_url`) makes no Engine call.
"""

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlencode

from django.urls import reverse

from console.cast import Character
from console.engine.models import History, HistoryItem
from console.history import (
    ENVELOPE,
    FIDELITY_BADGES,
    NOT_PERCEIVED,
    narrated_label,
)
from console.inspect import short_id
from console.wording import humanised

READS_MAX = 20  # cast members one page reads
STORY_VIEW: Final = "story"
TEXT_MAX = 80  # code points of a row header's text
NO_TEXT = "(no text)"
PLAYER_SUFFIX = " (player)"
CONTENT = "content"

ENGINE_REQUEST = "nathanpond/Epistrel#607"
SETUP_NOTICE = "Turn 0 is the story's setup: it has no beats"
NO_CAST = "This story has no characters to read"
ALL_TIMED_OUT = "Every read timed out — reload to try again"
RACE = "The story changed while this page loaded — reload"
FOOTNOTE = "`think` and `latent` beats never reach a character viewpoint and aren't in this grid."
NO_FIDELITY_TITLE = "No fidelity on this item"
ABSENT_TITLE = "Not in this character's history"
UNAVAILABLE_TEXT = "?"

MergeKey = tuple[int, str, str, str | None, int]


def caveat(reads: int) -> str:
    return (
        f"Reconstructed: one history read per character with player view story ({reads} reads). "
        "This shows today's audience filter, not a record of what was applied when the turn ran "
        f"(Engine request {ENGINE_REQUEST})."
    )


def cap_note(more: int) -> str:
    return f"Showing the first {READS_MAX} characters; {more} more not read"


def no_beats(turn: int) -> str:
    return (
        f"Turn {turn} has no beats readable at a character viewpoint (it may be past the head, "
        "truncated, or ingested without beats)"
    )


def player_view_not_applied(value: str) -> str:
    return f"player view not applied: {value}"


def perception_url(story_id: str, turn: int, variant: str | None = None) -> str:
    """The perception page for ``turn``, with the history chain ``variant`` (``T:V``) when
    given. No Engine call."""
    base = reverse("stories:perception", args=[story_id, turn])
    return f"{base}?{urlencode({'variant': variant})}" if variant else base


def has_structured_item(items: Sequence[HistoryItem]) -> bool:
    """An item that marks its turn structured for the Perception link: ``propositions`` or
    ``fidelity`` set."""
    return any(i.propositions is not None or i.fidelity is not None for i in items)


def turn_links_perception(
    items: Sequence[HistoryItem], turn: int, story_structured: Callable[[], bool] | None
) -> bool:
    """Whether a turn ≥ 1 links its perception trace: a shown structured item, else (when
    ``story_structured`` is given, active-chain pages only) the story runs the structured
    pipeline. ``story_structured`` is called only when the items don't say."""
    if turn < 1:
        return False
    if has_structured_item(items):
        return True
    return story_structured is not None and story_structured()


def column_labels(cast: Sequence[Character]) -> dict[str, str]:
    """Convention 17's labels for the grid's columns: the name (the short id when nameless),
    " (<short id>)" when two members share it, " (player)" for a player character."""
    counts = Counter(c.name for c in cast if c.name)
    labels = {}
    for c in cast:
        label = c.name or short_id(c.id)
        if c.name and counts[c.name] > 1:
            label = f"{label} ({short_id(c.id)})"
        labels[c.id] = f"{label}{PLAYER_SUFFIX}" if c.is_player else label
    return labels


@dataclass(frozen=True)
class Cell:
    text: str
    css_class: str = ""
    title: str = ""


UNAVAILABLE_CELL = Cell(UNAVAILABLE_TEXT, "fidelity-unavailable")
ABSENT_CELL = Cell("—", "", ABSENT_TITLE)


def cell(fidelity: str | None) -> Cell:
    """An item's cell for the fidelity one character perceived it at."""
    if fidelity is None:
        return Cell("—", "", NO_FIDELITY_TITLE)
    if fidelity == CONTENT:
        return Cell("perceived", "fidelity-content")
    if fidelity == ENVELOPE:
        badge = FIDELITY_BADGES[ENVELOPE]
        return Cell(badge.text, badge.row_class, badge.title)
    if fidelity == NOT_PERCEIVED:
        badge = FIDELITY_BADGES[NOT_PERCEIVED]
        return Cell(badge.text, badge.row_class, badge.title)
    return Cell(humanised(fidelity), "fidelity-unknown", fidelity)


@dataclass(frozen=True)
class Column:
    character_id: str
    label: str
    failure: str | None = None  # why the read failed

    @property
    def header(self) -> str:
        if self.failure is None:
            return self.label
        return f"{self.label} — unavailable ({self.failure})"


@dataclass(frozen=True)
class GridRow:
    head: str  # "beat N · <channel> · <actor>"
    text: str  # whitespace collapsed, cut to 80 code points
    full_text: str  # for ``title``
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class PerceptionGrid:
    columns: tuple[Column, ...]
    rows: tuple[GridRow, ...] = ()
    failures: str = ""  # "Perception unavailable for: …", "" when none
    all_failed: bool = False
    race: bool = False  # successful reads disagree on the variant or the revision
    variant: int | None = None  # the turn's variant, from the first successful read
    succeeded: int = 0


def _collapsed(text: str | None) -> tuple[str, str]:
    full = re.sub(r"\s+", " ", text or "").strip()
    if not full:
        return NO_TEXT, NO_TEXT
    cut = full[:TEXT_MAX] + "…" if len(full) > TEXT_MAX else full
    return cut, full


def _turn_variant(history: History, turn: int, items: Sequence[HistoryItem]) -> int | None:
    for summary in history.turns:
        if summary.turn == turn:
            return summary.variant
    return items[0].variant if items else None


def _cell(column: Column, found: Mapping[str, HistoryItem]) -> Cell:
    if column.failure is not None:
        return UNAVAILABLE_CELL
    item = found.get(column.character_id)
    return cell(item.fidelity) if item is not None else ABSENT_CELL


@dataclass
class _Row:
    beat: int
    order: int
    item: HistoryItem
    found: dict[str, HistoryItem] = field(default_factory=dict)


def grid(
    cast: Sequence[Character], reads: Mapping[str, History | str], turn: int
) -> PerceptionGrid:
    """The grid for ``turn`` from ``reads`` (character id → its history read, or why it failed;
    in cast order). ``cast`` labels the columns and the actors."""
    labels = column_labels(cast)
    columns: list[Column] = []
    histories: dict[str, History] = {}
    for character_id, read in reads.items():
        label = labels.get(character_id) or short_id(character_id)
        if isinstance(read, str):
            columns.append(Column(character_id, label, read))
        elif read.player_view != STORY_VIEW:
            columns.append(Column(character_id, label, player_view_not_applied(read.player_view)))
        else:
            columns.append(Column(character_id, label))
            histories[character_id] = read
    failed = [f"{c.label} ({c.failure})" for c in columns if c.failure is not None]
    failures = f"Perception unavailable for: {', '.join(failed)}" if failed else ""
    if not histories:
        return PerceptionGrid(tuple(columns), failures=failures, all_failed=bool(columns))
    rows: dict[MergeKey, _Row] = {}
    variants: set[int] = set()
    revisions: set[int] = set()
    first_variant: int | None = None
    for character_id, history in histories.items():
        items = [i for i in history.entries if i.turn == turn and i.beat is not None]
        variant = _turn_variant(history, turn, items)
        if variant is not None:
            variants.add(variant)
            if first_variant is None:
                first_variant = variant
        revisions.add(history.timeline_revision)
        seen: Counter[tuple[int, str, str, str | None]] = Counter()
        for item, beat in ((i, i.beat) for i in items if i.beat is not None):
            base = (beat, item.channel, item.kind, item.actor_character_id)
            key: MergeKey = (*base, seen[base])
            seen[base] += 1
            if key not in rows:
                rows[key] = _Row(beat, len(rows), item)
            rows[key].found[character_id] = item
    race = len(variants) > 1 or len(revisions) > 1
    actors = dict(labels)
    built = []
    for row in sorted(rows.values(), key=lambda r: (r.beat, r.order)):
        item = row.item
        if item.actor_character_id and item.actor_character_id not in actors:
            actors[item.actor_character_id] = short_id(item.actor_character_id)
        text, full = _collapsed(item.text)
        cells = tuple(_cell(c, row.found) for c in columns)
        head = f"beat {row.beat} · {item.channel} · {narrated_label(item, actors)}"
        built.append(GridRow(head, text, full, cells))
    return PerceptionGrid(
        tuple(columns),
        tuple(built),
        failures=failures,
        race=race,
        variant=first_variant,
        succeeded=len(histories),
    )

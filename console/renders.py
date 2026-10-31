"""A multi-character turn's per-character renders (#201): the turn inspector's "Renders" section.

:func:`turn_renders` groups a turn's parsed beats (``HistoryItem``s, in the Engine's ``beats[]``
order) by rendering character, in the turn's ``order``:

1. "Input — <actor>": the turn actor's beats before the first beat of a character in ``order``
   (only when the actor is not in ``order`` itself);
2. one group per distinct id in ``order``: reactions before the character's first own beat at
   the top, then its own beats, each later reaction it narrated nested under its nearest
   preceding own beat (one level only);
3. "Other beats": everything left, a reaction whose narrator isn't in ``order`` included.

A beat whose ``narrated_by`` is ``"player"`` (co_author) is a player-narrated beat, never a
reaction, and never by itself shows the section (C16, owned here).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from console.engine.models import HistoryItem
from console.history import (
    PLAYER_NARRATOR,
    FidelityBadge,
    fidelity_badge,
    narrated_label,
)
from console.wording import character_label

NO_BEATS = "No beats (see warnings)"
INPUT_HEADING = "Input — {}"
OTHER_HEADING = "Other beats"
INPUT_ANCHOR = "render-input"
OTHER_ANCHOR = "render-other"
REPLY_CHANNEL = "message"


def is_reaction(item: HistoryItem) -> bool:
    """A reactive character's beat narrated by a primary: ``narrated_by`` set and not the
    player (C16)."""
    return bool(item.narrated_by) and item.narrated_by != PLAYER_NARRATOR


def is_player_narrated(item: HistoryItem) -> bool:
    return item.narrated_by == PLAYER_NARRATOR


def speaker(item: HistoryItem, labels: Mapping[str, str]) -> str:
    """The play transcript's speaker rule (``play._speaker``): #62's narrated label for a
    player-narrated beat, else the actor's label, else a reply's render characters' labels
    joined with ", ", else ``""``."""
    if is_player_narrated(item):
        return narrated_label(item, labels)
    if item.actor_character_id:
        return character_label(item.actor_character_id, labels)
    if item.channel == REPLY_CHANNEL and item.render_character_ids:
        return ", ".join(character_label(c, labels) for c in item.render_character_ids)
    return ""


@dataclass(frozen=True)
class RenderBeat:
    """One beat of a group. ``label`` is ``""`` for an own or Input beat (the heading names
    it), the reactor's label for a reaction, ``narrated_label`` for a player-narrated beat and
    the speaker for an Other beat. ``anchor`` is ``beat-<N>`` on the first item with that beat
    number only. ``position`` is the item's 1-based place among the parsed beats ("item i" when
    it has no ``beat``)."""

    item: HistoryItem
    label: str
    anchor: str
    reactions: tuple[RenderBeat, ...] = ()
    position: int = 0

    @property
    def reaction(self) -> bool:
        return is_reaction(self.item)

    @property
    def fidelity(self) -> FidelityBadge | None:
        return fidelity_badge(self.item.fidelity)

    @property
    def css_class(self) -> str:
        """``reaction`` and/or #62's fidelity row class, space-separated."""
        found = ["reaction"] if self.reaction else []
        badge = self.fidelity
        if badge is not None and badge.row_class:
            found.append(badge.row_class)
        return " ".join(found)


@dataclass(frozen=True)
class RenderGroup:
    """One ``<h5>`` and its ``<ol class="render-beats">``: ``leading`` reactions (before the
    narrator's first own beat), then ``beats``; ``empty_note`` when a primary has neither."""

    heading: str
    anchor: str
    leading: tuple[RenderBeat, ...]
    beats: tuple[RenderBeat, ...]
    empty_note: str = ""


type _Beat = Callable[..., RenderBeat]  # turn_renders' beat builder (marks the item used)


def shows_renders(entries: Sequence[HistoryItem], order: Sequence[str]) -> bool:
    """The section shows when ``order`` has two or more distinct ids, or any beat is a
    reaction (a player-narrated beat doesn't count, C16)."""
    return len(set(order)) >= 2 or any(is_reaction(item) for item in entries)


def turn_renders(
    entries: Sequence[HistoryItem],
    order: Sequence[str],
    labels: Mapping[str, str],
    actor: str = "",
) -> tuple[RenderGroup, ...]:
    """The Renders section's groups, or ``()`` when it isn't shown (:func:`shows_renders`).
    ``actor`` is the turn's actor (``""``: unknown, or a regeneration: no Input group)."""
    if not shows_renders(entries, order):
        return ()
    items = list(entries)
    ids = list(dict.fromkeys(order))
    in_order = set(ids)
    anchors = _anchors(items)
    used: set[int] = set()

    def beat(index: int, label: str, reactions: tuple[RenderBeat, ...] = ()) -> RenderBeat:
        used.add(index)
        return RenderBeat(items[index], label, anchors.get(index, ""), reactions, index + 1)

    groups: list[RenderGroup] = []
    if actor and actor not in in_order:
        cut = next(
            (i for i, item in enumerate(items) if item.actor_character_id in in_order),
            len(items),
        )
        inputs = tuple(beat(i, "") for i in range(cut) if items[i].actor_character_id == actor)
        if inputs:
            heading = INPUT_HEADING.format(character_label(actor, labels))
            groups.append(RenderGroup(heading, INPUT_ANCHOR, (), inputs))
    for position, cid in enumerate(ids, start=1):
        groups.append(_group(items, cid, position, labels, beat))
    rest = tuple(beat(i, speaker(item, labels)) for i, item in enumerate(items) if i not in used)
    if rest:
        groups.append(RenderGroup(OTHER_HEADING, OTHER_ANCHOR, (), rest))
    return tuple(groups)


def _group(
    items: list[HistoryItem],
    cid: str,
    position: int,
    labels: Mapping[str, str],
    beat: _Beat,
) -> RenderGroup:
    own = [
        i
        for i, item in enumerate(items)
        if item.actor_character_id == cid and not is_reaction(item)
    ]
    reactions = [i for i, item in enumerate(items) if is_reaction(item) and item.narrated_by == cid]
    first = own[0] if own else len(items)
    leading = tuple(
        beat(i, character_label(items[i].actor_character_id, labels))
        for i in reactions
        if i < first
    )
    nested: dict[int, list[int]] = {}
    for i in reactions:
        if i > first:
            narrator_beat = max(o for o in own if o < i)
            nested.setdefault(narrator_beat, []).append(i)
    beats = tuple(
        beat(
            o,
            narrated_label(items[o], labels) if is_player_narrated(items[o]) else "",
            tuple(
                beat(r, character_label(items[r].actor_character_id, labels))
                for r in nested.get(o, ())
            ),
        )
        for o in own
    )
    note = NO_BEATS if not own and not leading else ""
    return RenderGroup(character_label(cid, labels), f"render-{position}", leading, beats, note)


def _anchors(items: list[HistoryItem]) -> dict[int, str]:
    """``beat-<N>`` on the first item (in ``beats[]`` order) with that beat number only; an item
    without a ``beat`` gets none."""
    seen: set[int] = set()
    found: dict[int, str] = {}
    for index, item in enumerate(items):
        if item.beat is None or item.beat in seen:
            continue
        seen.add(item.beat)
        found[index] = f"beat-{item.beat}"
    return found

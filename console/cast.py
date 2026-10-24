"""A story's cast as Console selects show it (#43: "character selects come from ``GET
/stories/{id}`` ``characters``, labelled by name, a duplicate name gets its last 6 id
characters"), and output viewpoints (``character:<id>``). First used by #48's turn form;
#51's ingest, #53's regenerate and #54's settings reuse it.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

VIEWPOINT_PREFIX = "character:"
OMNISCIENT = "omniscient"


class Character(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def is_player(self) -> bool: ...


@dataclass(frozen=True)
class CastMember:
    """A cast member as the Console carries it between requests (signed snapshots)."""

    id: str
    name: str
    is_player: bool


def members(cast: Iterable[Character]) -> list[CastMember]:
    return [CastMember(c.id, c.name, c.is_player) for c in cast]


def character_labels(cast: Sequence[Character]) -> dict[str, str]:
    """Each character's select label: its name, plus `` (…<last 6 id chars>)`` when another
    cast member has the same name."""
    counts = Counter(c.name for c in cast)
    return {c.id: f"{c.name} (…{c.id[-6:]})" if counts[c.name] > 1 else c.name for c in cast}


def character_choices(cast: Sequence[Character]) -> list[tuple[str, str]]:
    """``(character id, label)`` for a select, in the Engine's cast order."""
    labels = character_labels(cast)
    return [(c.id, labels[c.id]) for c in cast]


def default_actor(cast: Sequence[Character]) -> str | None:
    """The first player character, else the first cast member (``None`` for no cast)."""
    for c in cast:
        if c.is_player:
            return c.id
    return cast[0].id if cast else None


def viewpoint_for(character_id: str) -> str:
    return f"{VIEWPOINT_PREFIX}{character_id}"


def character_of(viewpoint: str | None) -> str | None:
    """The character id a ``character:<id>`` viewpoint names, else ``None``."""
    if viewpoint and viewpoint.startswith(VIEWPOINT_PREFIX):
        return viewpoint[len(VIEWPOINT_PREFIX) :] or None
    return None


def viewpoint_label(viewpoint: str | None, cast: Sequence[Character]) -> str:
    """A viewpoint for display: the character's label, ``omniscient``, or the raw value."""
    if not viewpoint:
        return "—"
    character = character_of(viewpoint)
    if character is not None:
        return character_labels(cast).get(character, viewpoint)
    return viewpoint

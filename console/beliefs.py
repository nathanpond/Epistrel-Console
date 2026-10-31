"""The Knowledge tab's Beliefs section (#190): one character's partition as the Engine projects it.

View models for ``templates/console/inspect/beliefs.html`` and ``_belief_row.html``:
:func:`belief_rows` (one :class:`BeliefRow` per ``BeliefItem``, in the Engine's order),
:func:`identity_rows` (the ``identities`` list), and the section's wording: :func:`header_text`,
:func:`count_text`, :func:`empty_text` and the notices.

Positions are C1's (``console.positions``: ``knowledge_position_text`` and its link, the step
only in "More"); enum values are C3's ``humanised`` with the raw value in ``title``; character
labels are C4's (``console.wording.character_label``). Counts follow C11 (``console.ledger``'s
"on this page" / "matching filters" qualifiers).

``provenance`` is the id of the belief transition that produced the row (the Engine's design;
its belief read carries no acquisition kind), shown as an id (convention 2's ``engine_id``), #227.

Shown as sent (C15): level and scalar aren't reconciled, ``held_to`` isn't checked against the
as-of turn, identities always read the current record. A proposition that doesn't parse (C2)
shows "—" in its column and raw in "More".
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from console.canon import EntityRef, entity_ref, lineage_url, proposition_time_text, utc_text
from console.engine.models import BeliefItem, IdentityItem, OmniscientStoryCharacter, Proposition
from console.engine_errors import EMPTY
from console.ledger import MATCHING, ON_PAGE
from console.positions import (
    knowledge_position_text,
    knowledge_position_url,
    position_step,
    valid_minutes_text,
)
from console.wording import humanised, minor_ids

FIRST_ORDER_NOTICE = "Only first-order beliefs held at suspected or above are listed."
PLAYER_NOTICE = "The player's character has no partition — the Engine keeps no beliefs for it"
NO_CHARACTERS = "This story has no modeled characters"
ENTER_CHARACTER = "Enter a character id to read its beliefs"
EMPTY_PAGE = "No beliefs on this page — the partition may have changed since the last page."
IDENTITIES = "Identities"
IDENTITIES_FILTERED = "Identities (held now — not filtered by As of turn or Proposition id)"
IDENTITIES_FIRST_PAGE = "Listed on the first page"
CANON_SOURCE, BELIEF_SOURCE = "canon", "belief"
SOURCE_TEXT = {CANON_SOURCE: "Canon fact", BELIEF_SOURCE: "Own belief"}
NOW = "now"


def story_minor_ids(characters: Sequence[Any] | None) -> frozenset[str]:
    """C4's minor ids from the shell's ``get_story`` items that parse as an omniscient cast member
    (no read is added for them: a non-omniscient read lists none)."""
    if characters is None:
        return frozenset()
    parsed = (OmniscientStoryCharacter.from_json(item) for item in characters)
    return minor_ids(c for c in parsed if c is not None)


@dataclass(frozen=True)
class Linked:
    """A position's text and its history link."""

    text: str
    url: str | None


@dataclass(frozen=True)
class MoreItem:
    """One "More" entry: ``kind`` ``text`` (``title`` the raw value when set), ``code`` (an id)
    or ``time`` (``title``/``datetime`` the raw value)."""

    term: str
    text: str
    kind: str = "text"
    title: str = ""


@dataclass(frozen=True)
class BeliefRow:
    """``_belief_row.html``'s context for one belief. ``until`` is ``None`` while it holds now;
    ``raw_proposition`` is the proposition's JSON when it doesn't parse (else ""); ``mark`` is
    #191's Canon cell (shown only when the page sets ``marks``)."""

    belief: BeliefItem
    proposition: Proposition | None
    level_text: str
    scalar_text: str | None
    since: Linked
    until: Linked | None
    about: str
    more: tuple[MoreItem, ...]
    raw_proposition: str
    mark: Any = None


def _linked(story_id: str, position: Any) -> Linked:
    return Linked(knowledge_position_text(position), knowledge_position_url(story_id, position))


def _more(belief: BeliefItem, proposition: Proposition | None) -> tuple[MoreItem, ...]:
    step = position_step(belief.since)
    return (
        MoreItem("Belief id", belief.belief_id, "code"),
        MoreItem("Proposition id", belief.proposition_id, "code"),
        MoreItem("Kind", humanised(belief.kind), title=belief.kind),
        MoreItem("Held from", knowledge_position_text(belief.held_from)),
        MoreItem(step[0], step[1]),
        MoreItem("Tx from", utc_text(belief.tx_from), "time", belief.tx_from),
        MoreItem(
            "Proposition valid-time",
            EMPTY if proposition is None else proposition_time_text(proposition.valid_time),
        ),
        MoreItem("Polarity", humanised(belief.polarity), title=belief.polarity),
    )


def belief_row(
    story_id: str, belief: BeliefItem, marks: Mapping[str, Any] | None = None
) -> BeliefRow:
    proposition = belief.proposition_view
    scalar = belief.current_scalar
    return BeliefRow(
        belief=belief,
        proposition=proposition,
        level_text=humanised(belief.current_level),
        scalar_text=None if scalar is None else f"{scalar:.3f}",
        since=_linked(story_id, belief.since),
        until=None if belief.held_to is None else _linked(story_id, belief.held_to),
        about=valid_minutes_text(belief.valid_from, belief.valid_to),
        more=_more(belief, proposition),
        raw_proposition=""
        if proposition is not None
        else json.dumps(belief.proposition, indent=2, ensure_ascii=False),
        mark=None if marks is None else marks.get(belief.belief_id),
    )


def belief_rows(
    story_id: str, beliefs: Sequence[BeliefItem], marks: Mapping[str, Any] | None = None
) -> list[BeliefRow]:
    """The rows in the Engine's order."""
    return [belief_row(story_id, b, marks) for b in beliefs]


# --- identities ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityRow:
    """One ``identities`` entry: its entities (cast labels, else ids), the source's text, the
    record id and its link (a canon fact's lineage view; an own belief's row when it is on the
    page; else ``None``)."""

    entities: tuple[EntityRef, ...]
    source: str
    source_text: str
    record_id: str
    url: str | None


def identity_rows(
    story_id: str,
    identities: Sequence[IdentityItem],
    names: Mapping[str, str],
    belief_ids: frozenset[str],
) -> list[IdentityRow]:
    rows = []
    for item in identities:
        url = None
        if item.source == CANON_SOURCE:
            url = lineage_url(story_id, item.record_id)
        elif item.source == BELIEF_SOURCE and item.record_id in belief_ids:
            url = f"#b-{item.record_id}"
        rows.append(
            IdentityRow(
                entities=tuple(entity_ref(e, names) for e in item.entities),
                source=item.source,
                source_text=SOURCE_TEXT.get(item.source) or humanised(item.source),
                record_id=item.record_id,
                url=url,
            )
        )
    return rows


# --- the section's wording ----------------------------------------------------------------------


def time_text(as_of: int | None) -> str:
    """ "now", or "as of turn N"."""
    return NOW if as_of is None else f"as of turn {as_of}"


def header_text(label: str, time: str | None, revision: int | None) -> str:
    """ "What Alice believes · now · revision 7"; the time part dropped when As of turn is
    invalid (``None``), the revision when no read answered."""
    parts = [f"What {label} believes"]
    if time is not None:
        parts.append(time)
    if revision is not None:
        parts.append(f"revision {revision}")
    return " · ".join(parts)


def count_text(n: int, *, partial: bool, filtered: bool) -> str:
    """C11: "12 beliefs" / "1 belief", then "on this page" and/or "matching filters"."""
    noun = "belief" if n == 1 else "beliefs"
    qualifier = ", ".join(text for text, on in ((ON_PAGE, partial), (MATCHING, filtered)) if on)
    return f"{n:,} {noun} {qualifier}" if qualifier else f"{n:,} {noun}"


def empty_text(label: str, *, paged: bool, proposition: bool, as_of: int | None) -> str:
    """The empty page's line (the player notice, checked first by the view, aside): a cursor
    page's, then a proposition filter's, then the plain one."""
    if paged:
        return EMPTY_PAGE
    if proposition:
        if as_of is None:
            return f"{label} holds no current belief in that proposition"
        return f"{label} held no belief in that proposition as of turn {as_of}"
    plain = f"{label} holds no beliefs at suspected or above"
    return plain if as_of is None else f"{plain} as of turn {as_of}"

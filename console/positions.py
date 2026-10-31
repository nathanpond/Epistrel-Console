"""Five-part narrative positions as every M5 page shows them (#187).

**Convention 15 (C1): five-part positions.** ``NarrativePosition`` and the belief write's
``AuthorPosition`` render only through this module:

- :func:`knowledge_position_text`: "turn N"; "turn N⁺k" when ``slot_k`` > 0 (superscript plus and
  superscript digits, each digit mapped); " · vV" only when ``variant`` ≠ 0; then " · start" for
  beat 0, " · after the turn" for beat 32767 (:data:`POSITION_MAX`, the Engine's, where author
  writes sit), else " · beat B" — the beat part applies to slot positions too. Example:
  "turn 12 · v2 · beat 3".
- :func:`position_step` → ("Step", "<step>", plain ``str``, no separators): shown only in the
  row's "More", never in the text.
- :func:`knowledge_position_url` = ``history_turn_url(story_id, p.turn, p.variant or None)``; a
  slot position links turn N, variant kept.
- Story-minute values (``valid_from``/``valid_to``) read :func:`valid_minutes_text` =
  "[a, b) min", built on ``console.canon.bound_text`` (outer bounds only; "…" for open).
- Three-part ``InspectPosition``\\ s keep #135 convention 4's ``position_text``.

Every function takes any object with ``turn``, ``variant``, ``slot_k``, ``beat`` and ``step``
(:class:`FivePart`) and answers ``None`` for a ``None`` position. Values render as given
(``str()``), no clamping: the Engine never sends a negative or out-of-range part (C15).
"""

from typing import Protocol, overload

from console.canon import bound_text
from console.inspect import history_turn_url

POSITION_MAX = 32767  # the Engine's last beat of a turn: where author writes sit
SUPERSCRIPT = str.maketrans("0123456789+", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺")


class FivePart(Protocol):
    """A five-part position: #186's ``NarrativePosition`` and ``AuthorPosition`` both fit."""

    @property
    def turn(self) -> int: ...
    @property
    def variant(self) -> int: ...
    @property
    def slot_k(self) -> int: ...
    @property
    def beat(self) -> int: ...
    @property
    def step(self) -> int: ...


@overload
def knowledge_position_text(position: None) -> None: ...
@overload
def knowledge_position_text(position: FivePart) -> str: ...
@overload
def knowledge_position_text(position: FivePart | None) -> str | None: ...
def knowledge_position_text(position: FivePart | None) -> str | None:
    """ "turn 12 · beat 3", "turn 12⁺¹ · v2 · start", "turn 12 · after the turn"."""
    if position is None:
        return None
    text = f"turn {position.turn}"
    if position.slot_k > 0:
        text += f"+{position.slot_k}".translate(SUPERSCRIPT)
    if position.variant != 0:
        text += f" · v{position.variant}"
    if position.beat == 0:
        return f"{text} · start"
    if position.beat == POSITION_MAX:
        return f"{text} · after the turn"
    return f"{text} · beat {position.beat}"


@overload
def knowledge_position_url(story_id: str, position: None) -> None: ...
@overload
def knowledge_position_url(story_id: str, position: FivePart) -> str: ...
@overload
def knowledge_position_url(story_id: str, position: FivePart | None) -> str | None: ...
def knowledge_position_url(story_id: str, position: FivePart | None) -> str | None:
    """The story's history at the position's turn (a slot position links turn N), with its
    variant only when ≠ 0."""
    if position is None:
        return None
    return history_turn_url(story_id, position.turn, position.variant or None)


@overload
def position_step(position: None) -> None: ...
@overload
def position_step(position: FivePart) -> tuple[str, str]: ...
@overload
def position_step(position: FivePart | None) -> tuple[str, str] | None: ...
def position_step(position: FivePart | None) -> tuple[str, str] | None:
    """The row's "More" fact ``("Step", "40000")``; never part of the position text."""
    if position is None:
        return None
    return ("Step", str(position.step))


def valid_minutes_text(valid_from: int | None, valid_to: int | None) -> str:
    """Story-minute validity: "[540, …) min" ("…" for an open end)."""
    return f"[{bound_text(valid_from, None)}, {bound_text(valid_to, None)}) min"

"""The Retrieval explorer (#141) as the Inspect Retrieval tab shows it: pure functions from the
Engine's ``GET /stories/{id}/retrieve`` answer (:class:`RetrievalBody`), the question and the
story's cast to the tab's rows. The template only prints them.

**Highlighting** (:func:`mention_rows`) works over Python code-point offsets, as the Engine's
spans are: the question becomes a list of :class:`Segment`\\ s, each plain text or one mention's
span, rendered with autoescape (never ``mark_safe`` on the question). Mentions are taken by
start; a span that overlaps an earlier one, falls outside the question, isn't two numbers or is
empty is not highlighted, and its row says so.

**Names** come only from the page's one story read (#135 convention 2): a character id (the
actor, a participant, a mention resolving to a character) shows its cast label; every other id
stays a short id. ``cast`` is ``None`` when the story read failed.

**Display filters** (``kind``, ``tier``, ``memory``; convention 11) narrow the three candidate
tables and nothing else; they combine with AND. ``memory`` matches case-insensitively on the full
id or a suffix of at least 8 characters (the shown "…last 8", its "…" optional). An unknown kind
or tier never matches a set filter.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from console.cast import Character, character_labels
from console.engine.models import MentionBody, ResolutionBody, RetrievalBody, RetrievedMemoryBody
from console.inspect import (
    SHORT_ID,
    history_turn_url,
    position_text,
    retrieval_url,
    setting,
)

EMPTY = "—"
OPEN_BOUND = "…"
NOT_HIGHLIGHTED = "not highlighted"
EPISODIC_SKIPPED = "episodic"

KINDS: tuple[str, ...] = ("episodic", "semantic", "reflection")
TIERS: tuple[str, ...] = ("hot", "retired")
TIER_LABELS: Mapping[str, str] = {"hot": "hot", "retired": "retired · rehydrated"}

# The Engine's arms (``ARM_*`` in core/retrieval/pipeline.py) with the legend's words.
ARMS: Mapping[str, str] = {
    "vector": "nearest by embedding",
    "lexical": "full-text match",
    "alias": "names an entity the question names",
    "occasion": "an episode of the resolved time or event",
    "assessment": "a semantic fact or reflection about the occasion's participants",
}

# The score's weights and the recency half-life, in the story's effective configuration.
WEIGHTS: Mapping[str, str] = {
    "similarity": "memory.episodic.weight_similarity",
    "recency": "memory.episodic.weight_recency",
    "importance": "memory.episodic.weight_importance",
}
HALF_LIFE = "memory.episodic.recency_half_life_turns"

NOT_RETRIEVED = "That memory isn't among what this question retrieves."
HIDDEN_BY_FILTER = "That memory is retrieved, but the kind/tier filter hides it."

# The answer's fields #141 doesn't show as sections (#152 replaces them), for "More".
MORE_FIELDS: tuple[str, ...] = (
    "canon",
    "profiles",
    "context",
    "timings",
    "degradation",
    "rehydrated",
    "model_calls",
    "embedding_model_version",
    "position",
)


def humanised(value: str) -> str:
    """Convention 5: ``unresolved_entity`` → "Unresolved entity" (unknown values the same)."""
    return value.replace("_", " ").capitalize()


def decimal3(value: object) -> str:
    """Convention 5's 3 decimals for scores and their terms; "—" for anything not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return EMPTY
    return f"{value:.3f}"


# --- people ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    """A character id with its cast label (``None``: not in the cast, shown as a short id), for
    ``console/inspect/_character.html``."""

    id: str
    label: str | None
    is_player: bool


class Names:
    """The cast's labels (``character_labels``) and its player, from the page's story read."""

    def __init__(self, cast: Sequence[Character] | None) -> None:
        self.labels = character_labels(cast) if cast is not None else {}
        self.players = {c.id for c in cast or () if c.is_player}

    def person(self, character_id: str) -> Person:
        return Person(character_id, self.labels.get(character_id), character_id in self.players)

    def label(self, character_id: str) -> str | None:
        """The cast label with " (player)" for the player; ``None`` outside the cast."""
        label = self.labels.get(character_id)
        if label is None:
            return None
        return f"{label} (player)" if character_id in self.players else label


# --- resolution -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """A run of the question: plain text (``number`` ``None``) or mention ``number``'s span."""

    text: str
    number: int | None = None
    title: str = ""


@dataclass(frozen=True)
class MentionRow:
    """One mentions-table row: its 1-based ``number`` (the ``<mark>``'s), the span's text
    (``None`` for a span that isn't one), whether it is highlighted, and what it resolved to."""

    number: int
    span_text: str | None
    highlighted: bool
    kind: str
    entity: Person | None
    reason: str | None

    @property
    def kind_label(self) -> str:
        return humanised(self.kind)

    @property
    def reason_label(self) -> str | None:
        return humanised(self.reason) if self.reason else None

    @property
    def title(self) -> str:
        """The ``<mark>``'s ``title``: kind, then the resolved name or id, else the reason."""
        if self.entity is not None:
            shown = self.entity.label or self.entity.id
            if self.entity.is_player and self.entity.label:
                shown = f"{shown} (player)"
        else:
            shown = self.reason_label or "unresolved"
        return f"{self.kind_label} · {shown}"


def _span(mention: MentionBody, length: int) -> tuple[int, int] | None:
    """The mention's ``[start, end)`` when it is two numbers inside the question, else ``None``."""
    if len(mention.span) != 2:
        return None
    start, end = mention.span
    if not 0 <= start < end <= length:
        return None
    return start, end


def mention_rows(
    q: str, mentions: Sequence[MentionBody], names: Names
) -> tuple[list[Segment], list[MentionRow]]:
    """The question as highlighted segments, and the mentions table in the Engine's order."""
    spans = [_span(m, len(q)) for m in mentions]
    highlighted: dict[int, tuple[int, int]] = {}
    reached = 0
    valid = sorted((span, index) for index, span in enumerate(spans) if span is not None)
    for (start, end), index in valid:
        if start < reached:
            continue  # overlaps an earlier span
        highlighted[index] = (start, end)
        reached = end
    rows = [
        MentionRow(
            number=index + 1,
            span_text=q[span[0] : span[1]] if span else None,
            highlighted=index in highlighted,
            kind=m.kind,
            entity=names.person(m.entity_id) if m.entity_id else None,
            reason=m.reason,
        )
        for index, (m, span) in enumerate(zip(mentions, spans, strict=True))
    ]
    segments: list[Segment] = []
    at = 0
    for index, (start, end) in sorted(highlighted.items(), key=lambda item: item[1]):
        if start > at:
            segments.append(Segment(q[at:start]))
        segments.append(Segment(q[start:end], index + 1, rows[index].title))
        at = end
    if at < len(q):
        segments.append(Segment(q[at:]))
    return segments, rows


@dataclass(frozen=True)
class Bounds:
    """A ``[start, end)`` pair as text, or (``raw``) the value as JSON when it isn't a pair."""

    text: str
    raw: bool = False


def _bounds(value: Sequence[int | None] | None, pattern: str) -> Bounds | None:
    if value is None:
        return None
    if len(value) != 2:
        return Bounds(json.dumps(list(value)), raw=True)
    start, end = (OPEN_BOUND if v is None else str(v) for v in value)
    return Bounds(pattern.format(start=start, end=end))


@dataclass(frozen=True)
class TimeView:
    """The resolution's time: the story-minute ``window`` and its ``turns`` (each ``None`` when
    absent), else the ``reason`` it didn't resolve (``None``: no time expression)."""

    window: Bounds | None
    turns: Bounds | None
    reason: str | None

    @property
    def reason_label(self) -> str | None:
        return humanised(self.reason) if self.reason else None


def time_view(resolution: ResolutionBody) -> TimeView:
    return TimeView(
        _bounds(resolution.window, "[{start}, {end}) min"),
        _bounds(resolution.turns, "{start}\u2013{end}"),
        resolution.time_reason,
    )


# --- candidates -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Filters:
    """The display filters' cleaned values ("" = any)."""

    kind: str = ""
    tier: str = ""
    memory: str = ""

    @property
    def active(self) -> bool:
        return bool(self.kind or self.tier or self.memory)


def memory_matches(memory_id: str, wanted: str) -> bool:
    """``wanted`` is the full id or a suffix of at least 8 characters, any casing (a leading
    "…", as the short id shows it, is ignored)."""
    wanted = wanted.strip().removeprefix("…").lower()
    if not wanted:
        return False
    found = memory_id.lower()
    return found == wanted or (len(wanted) >= SHORT_ID and found.endswith(wanted))


def _kind_tier(memory: RetrievedMemoryBody, filters: Filters) -> bool:
    return (not filters.kind or memory.kind == filters.kind) and (
        not filters.tier or memory.retrieval_tier == filters.tier
    )


def passes(memory: RetrievedMemoryBody, filters: Filters) -> bool:
    return _kind_tier(memory, filters) and (
        not filters.memory or memory_matches(memory.id, filters.memory)
    )


@dataclass(frozen=True)
class Arm:
    name: str

    @property
    def known(self) -> bool:
        return self.name in ARMS


@dataclass(frozen=True)
class CandidateRow:
    """One candidate as ``console/inspect/_memory_row.html`` prints it: the Engine's memory and
    what the Console derives (labels, cast names, the position and its history link, and the id's
    link that filters this result to it)."""

    memory: RetrievedMemoryBody
    actor: Person | None
    participants: list[Person]
    position: str | None
    position_url: str | None
    filter_url: str | None
    arms: list[Arm]

    @property
    def kind_label(self) -> str | None:
        """Known kinds humanised ("Episodic"); ``None`` for an unknown one (shown as code)."""
        return humanised(self.memory.kind) if self.memory.kind in KINDS else None

    @property
    def tier_label(self) -> str | None:
        return TIER_LABELS.get(self.memory.retrieval_tier)

    @property
    def event_type_label(self) -> str | None:
        return humanised(self.memory.event_type) if self.memory.event_type else None


def candidate_row(
    story_id: str, memory: RetrievedMemoryBody, names: Names, keep: Mapping[str, str]
) -> CandidateRow:
    """``keep``: the question's parameters the id link keeps (``memory`` is added)."""
    turn = memory.narrative_turn
    return CandidateRow(
        memory=memory,
        actor=names.person(memory.character_id) if memory.character_id else None,
        participants=[names.person(p) for p in memory.participants],
        position=position_text(turn, memory.beat),
        position_url=history_turn_url(story_id, turn) if turn is not None else None,
        filter_url=retrieval_url(story_id, {**keep, "memory": memory.id}),
        arms=[Arm(name) for name in memory.arms],
    )


@dataclass(frozen=True)
class CandidateTable:
    """One of the three tables: the rows the filters keep, out of ``total``."""

    key: str
    title: str
    rows: list[CandidateRow]
    total: int

    @property
    def hidden(self) -> bool:
        return len(self.rows) < self.total


@dataclass(frozen=True)
class Candidates:
    """The Candidates section: the Memories, Episodes and Assessments tables, how many rows
    the filters show of how many, and the ``memory`` filter's note (``None``: no note)."""

    tables: list[CandidateTable]
    shown: int
    total: int
    memory_note: str | None
    filters: Filters = field(default_factory=Filters)

    @property
    def hidden(self) -> bool:
        return self.shown < self.total


def candidates(
    story_id: str,
    body: RetrievalBody,
    names: Names,
    filters: Filters,
    keep: Mapping[str, str],
) -> Candidates:
    """The three tables in the Engine's order (memories by score), filtered."""
    groups = (
        ("memories", "Memories", body.memories),
        ("episodes", "Episodes", body.episodes),
        ("assessments", "Assessments", body.assessments),
    )
    tables = [
        CandidateTable(
            key,
            title,
            [candidate_row(story_id, m, names, keep) for m in memories if passes(m, filters)],
            len(memories),
        )
        for key, title, memories in groups
    ]
    every = [m for _, _, memories in groups for m in memories]
    note = None
    if filters.memory:
        named = [m for m in every if memory_matches(m.id, filters.memory)]
        if not named:
            note = NOT_RETRIEVED
        elif not any(_kind_tier(m, filters) for m in named):
            note = HIDDEN_BY_FILTER
    return Candidates(
        tables,
        sum(len(t.rows) for t in tables),
        len(every),
        note,
        filters,
    )


# --- the legend, the header and "More" ------------------------------------------------------------


@dataclass(frozen=True)
class Legend:
    """The score legend's weights (3 decimals, "—" when missing) and the recency half-life."""

    similarity: str
    recency: str
    importance: str
    half_life: str
    arms: Mapping[str, str] = field(default_factory=lambda: ARMS)


def legend(effective: Mapping[str, Any]) -> Legend:
    half_life = setting(effective, HALF_LIFE)
    if isinstance(half_life, int) and not isinstance(half_life, bool):
        half_life_text = f"{half_life:,}"
    else:
        half_life_text = decimal3(half_life)
    similarity, recency, importance = (decimal3(setting(effective, p)) for p in WEIGHTS.values())
    return Legend(similarity, recency, importance, half_life_text)


def episodic_skipped(body: RetrievalBody) -> bool:
    return EPISODIC_SKIPPED in body.skipped


def more(body: RetrievalBody) -> dict[str, Any]:
    """Convention 13's "More": the fields #152 will show as sections, and any ``skipped``
    stage other than ``episodic``."""
    found = {name: getattr(body, name) for name in MORE_FIELDS}
    skipped = [s for s in body.skipped if s != EPISODIC_SKIPPED]
    if skipped:
        found["skipped"] = skipped
    return found

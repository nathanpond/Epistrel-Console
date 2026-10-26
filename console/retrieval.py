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

**What the read placed** (#152): Canon, Profiles, Context and Run facts come from the same
answer, with no further Engine read, and the display filters never touch them. Entity names come
from the response's profiles (the profiled entity, a relationship's other end, a placement's
places and container, an inventory item), then the cast; a predicate's name from a profile
relationship with the same id (no registry read on this page). Every entity links #58's entity
page at the retrieval's turn (``position[0] - 1``) and ``omniscient``. A context id of a memory
kind and a rehydrated id link back to this explorer with the same question and ``memory=<id>``
(``kind``/``tier`` dropped).
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.urls import NoReverseMatch

from console.cast import OMNISCIENT, Character, character_labels
from console.engine.models import (
    CanonBody,
    ContextBody,
    EntityBody,
    EntityProfileBody,
    MentionBody,
    ResolutionBody,
    RetrievalBody,
    RetrievedMemoryBody,
)
from console.engine_errors import degradation_message
from console.inspect import (
    SHORT_ID,
    history_turn_url,
    position_text,
    retrieval_url,
    setting,
)
from console.ledger import FactLink, fact_link
from console.predicates import row_url
from console.scene import entity_url

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

# The answer's fields no section shows, for "More" (#152 turned the rest into sections).
MORE_FIELDS: tuple[str, ...] = ("position",)


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
    """Convention 13's "More": the fields no section shows (``position``)."""
    return {name: getattr(body, name) for name in MORE_FIELDS}


# --- what the read placed (#152) ------------------------------------------------------------------

# The explorer parameters an id link back keeps ("the same query"); ``memory`` is then added.
KEEP_FIELDS: tuple[str, ...] = (
    "q",
    "as_of",
    "actor",
    "include_quarantined",
    "explain",
    "explain_variant",
)


@dataclass(frozen=True)
class Ref:
    """An id as ``console/inspect/_predicate_ref.html`` shows it: the name (``None``: the short
    id) linked to ``url`` when there is one, the full id in ``title``."""

    id: str
    name: str | None
    url: str | None


def retrieval_turn(body: RetrievalBody) -> int | None:
    """The turn the read retrieved at: ``position[0] - 1`` (``None`` when it isn't one)."""
    if not body.position or body.position[0] < 1:
        return None
    return body.position[0] - 1


def _profile_entities(profile: EntityProfileBody) -> list[EntityBody]:
    found = [profile.entity, *(r.other for r in profile.relationships)]
    placement = profile.placement
    if placement is not None:
        found += [e for e in (placement.location, placement.container) if e is not None]
        found += placement.within
    found += [i.item for i in profile.inventory]
    return found


class ResponseNames:
    """Names for the ids the canon table shows, from the response alone (convention 2): entity
    names from the profiles (a non-empty ``EntityBody.name``), else the cast's labels; predicate
    names from the profiles' relationships."""

    def __init__(self, body: RetrievalBody, names: Names) -> None:
        self.names = names
        self.entities: dict[str, str] = {}
        self.predicates: dict[str, str] = {}
        for profile in body.profiles:
            for entity in _profile_entities(profile):
                if entity.name:
                    self.entities.setdefault(entity.entity_id, entity.name)
            for r in profile.relationships:
                if r.predicate:
                    self.predicates.setdefault(r.predicate_id, r.predicate)

    def entity(self, entity_id: str) -> str | None:
        return self.entities.get(entity_id) or self.names.label(entity_id)

    def predicate(self, predicate_id: str) -> str | None:
        return self.predicates.get(predicate_id)


@dataclass(frozen=True)
class LiteralText:
    """A canon literal as shown: ``text``, with the literal's kind in ``title`` (``None`` for a
    value shown as JSON)."""

    text: str
    title: str | None = None


def _value_text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def literal_text(value: Any) -> LiteralText:
    """A dict with ``kind`` and ``value`` reads "value unit" (kind in ``title``); anything else
    compact JSON, untruncated."""
    if isinstance(value, dict) and "kind" in value and "value" in value:
        unit = value.get("unit")
        text = _value_text(value["value"])
        if unit not in (None, ""):
            text = f"{text} {_value_text(unit)}"
        return LiteralText(text, _value_text(value["kind"]))
    return LiteralText(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")))


@dataclass(frozen=True)
class CanonRow:
    """One canon table row. The object is ``entity``, else ``entities`` (a set), else
    ``literal``; all three ``None`` reads "—"."""

    fact: FactLink
    subject: Ref
    predicate: Ref
    entity: Ref | None
    entities: list[Ref] | None
    literal: LiteralText | None
    polarity: str

    @property
    def polarity_label(self) -> str:
        return humanised(self.polarity)


def _entity_ref(story_id: str, entity_id: str, turn: int | None, names: ResponseNames) -> Ref:
    url = entity_url(story_id, entity_id, turn, OMNISCIENT) if turn is not None else None
    return Ref(entity_id, names.entity(entity_id), url)


def _predicate_link(story_id: str, predicate_id: str) -> str | None:
    try:
        return row_url(story_id, predicate_id)
    except NoReverseMatch:
        return None


def canon_rows(story_id: str, body: RetrievalBody, names: ResponseNames) -> list[CanonRow]:
    """The canon table in the Engine's order."""
    turn = retrieval_turn(body)

    def row(fact: CanonBody) -> CanonRow:
        literal = None
        if fact.object_id is None and fact.object_set is None and fact.literal is not None:
            literal = literal_text(fact.literal)
        return CanonRow(
            fact=fact_link(story_id, fact.fact_id, fact.subject_id, fact.predicate_id),
            subject=_entity_ref(story_id, fact.subject_id, turn, names),
            predicate=Ref(
                fact.predicate_id,
                names.predicate(fact.predicate_id),
                _predicate_link(story_id, fact.predicate_id),
            ),
            entity=(
                _entity_ref(story_id, fact.object_id, turn, names)
                if fact.object_id is not None
                else None
            ),
            entities=(
                [_entity_ref(story_id, e, turn, names) for e in fact.object_set]
                if fact.object_id is None and fact.object_set is not None
                else None
            ),
            literal=literal,
            polarity=fact.polarity,
        )

    return [row(fact) for fact in body.canon]


# --- context --------------------------------------------------------------------------------------

TIER_TITLES: Mapping[int, str] = {
    1: "1 · Ledger and pins",
    2: "2 · Occasions",
    3: "3 · Profiles and canon",
    4: "4 · Ranked memories",
}
MEMORY_KINDS: frozenset[str] = frozenset({"memory", "episode", "assessment", "pin"})
DROP_REASONS: Mapping[str, str] = {
    "budget": "over the remaining budget",
    "pin_cap": "pins over their 40% share",
    "duplicate": "already placed at a higher tier",
}
SUMMARY_MAX = 80


@dataclass(frozen=True)
class ContextRow:
    """A placed item (``text`` set) or a dropped one (``reason`` set) as the tables show it.
    ``url`` is the id's link (``None``: plain ``<code>``)."""

    tier: int
    kind: str
    id: str
    url: str | None
    tokens: int
    text: str | None = None
    reason: str | None = None

    @property
    def kind_label(self) -> str:
        return humanised(self.kind)

    @property
    def summary(self) -> str:
        """The text's first 80 characters, "…" when cut (the closed ``<details>``'s summary)."""
        text = self.text or ""
        return text if len(text) <= SUMMARY_MAX else f"{text[:SUMMARY_MAX]}…"

    @property
    def reason_text(self) -> str | None:
        """A known drop reason's explanation; ``None`` for an unknown one (shown as code)."""
        return DROP_REASONS.get(self.reason or "")


@dataclass(frozen=True)
class TierGroup:
    tier: int
    title: str
    rows: list[ContextRow]


@dataclass(frozen=True)
class ContextView:
    """The Context section: used/budget, the placed items by tier (the four known tiers
    always, then any other tier ascending) and the dropped items, each in the Engine's order."""

    used: int
    budget: int
    tiers: list[TierGroup]
    dropped: list[ContextRow]

    @property
    def over_budget(self) -> bool:
        return self.used > self.budget


def id_url(
    story_id: str,
    kind: str,
    item_id: str,
    keep: Mapping[str, str],
    canon: Mapping[str, CanonBody],
) -> str | None:
    """A context id's link: memory kinds → this explorer with ``memory=<id>``; ``canon`` → #138's
    lineage view (subject and predicate when this answer's canon carries the fact); anything
    else (``ledger``, ``profile``, unknown kinds) none."""
    if kind in MEMORY_KINDS:
        return retrieval_url(story_id, {**keep, "memory": item_id})
    if kind == "canon":
        fact = canon.get(item_id)
        if fact is not None:
            return fact_link(story_id, item_id, fact.subject_id, fact.predicate_id).url
        return fact_link(story_id, item_id).url
    return None


def context_view(story_id: str, body: RetrievalBody, keep: Mapping[str, str]) -> ContextView:
    context: ContextBody = body.context
    canon = {fact.fact_id: fact for fact in body.canon}

    def url(kind: str, item_id: str) -> str | None:
        return id_url(story_id, kind, item_id, keep, canon)

    placed: dict[int, list[ContextRow]] = {tier: [] for tier in TIER_TITLES}
    for item in context.items:
        row = ContextRow(
            item.tier, item.kind, item.item_id, url(item.kind, item.item_id), item.tokens, item.text
        )
        placed.setdefault(item.tier, []).append(row)
    tiers = [TierGroup(tier, TIER_TITLES[tier], placed[tier]) for tier in TIER_TITLES]
    tiers += [
        TierGroup(tier, f"Other tier {tier}", placed[tier])
        for tier in sorted(placed)
        if tier not in TIER_TITLES
    ]
    dropped = [
        ContextRow(d.tier, d.kind, d.item_id, url(d.kind, d.item_id), d.tokens, reason=d.reason)
        for d in context.dropped
    ]
    return ContextView(context.used, context.budget, tiers, dropped)


# --- run facts ------------------------------------------------------------------------------------

STAGE_ORDER: tuple[str, ...] = (
    "resolve",
    "occasion",
    "vector",
    "lexical",
    "fuse",
    "profiles",
    "assemble",
)
TOTAL = "total"
DEGRADATIONS: Mapping[str, str] = {
    "embedding_unavailable": "Embedding unavailable — vector arm skipped",
}
SKIP_NOTES: Mapping[str, str] = {
    "occasion": "no time window or event type in the question",
    "vector": "arm did not run",
    "lexical": "arm did not run",
    "alias": "arm did not run",
}
REHYDRATED_NOTE = "Rehydrated for this read only; nothing was stored."


@dataclass(frozen=True)
class DegradationLine:
    """A degradation cause's text (#150's for ``embedding_pending``, with its run page link)."""

    cause: str
    text: str
    url: str | None = None


def degradation_line(cause: str) -> DegradationLine:
    found = degradation_message(cause)
    if found is not None:
        return DegradationLine(cause, found.text, found.url)
    return DegradationLine(cause, DEGRADATIONS.get(cause, humanised(cause)))


@dataclass(frozen=True)
class Skipped:
    stage: str
    note: str | None

    @property
    def label(self) -> str:
        return humanised(self.stage)


@dataclass(frozen=True)
class TimingRow:
    stage: str
    ms: str  # 3 decimals with thousands separators, "—" when missing

    @property
    def total(self) -> bool:
        return self.stage == TOTAL


def ms_text(value: float | None) -> str:
    return EMPTY if value is None else f"{value:,.3f}"


def timing_rows(timings: Mapping[str, float]) -> list[TimingRow]:
    """The Engine's stages in order (a missing one "—"), then unknown stages in the response's
    order, then ``total``."""
    rows = [TimingRow(stage, ms_text(timings.get(stage))) for stage in STAGE_ORDER]
    rows += [
        TimingRow(stage, ms_text(value))
        for stage, value in timings.items()
        if stage not in STAGE_ORDER and stage != TOTAL
    ]
    rows.append(TimingRow(TOTAL, ms_text(timings.get(TOTAL))))
    return rows


@dataclass(frozen=True)
class RunFacts:
    degradations: list[DegradationLine]
    skipped: list[Skipped]
    rehydrated: list[Ref]
    model_calls: int
    timings: list[TimingRow]
    embedding_model_version: str | None
    rehydrated_note: str = REHYDRATED_NOTE


def run_facts(story_id: str, body: RetrievalBody, keep: Mapping[str, str]) -> RunFacts:
    return RunFacts(
        degradations=[degradation_line(cause) for cause in body.degradation],
        skipped=[
            Skipped(stage, SKIP_NOTES.get(stage))
            for stage in body.skipped
            if stage != EPISODIC_SKIPPED
        ],
        rehydrated=[
            Ref(memory_id, None, retrieval_url(story_id, {**keep, "memory": memory_id}))
            for memory_id in body.rehydrated
        ],
        model_calls=body.model_calls,
        timings=timing_rows(body.timings),
        embedding_model_version=body.embedding_model_version,
    )


@dataclass(frozen=True)
class Placed:
    """The four #152 sections' rows, and ``unparsed``: the raw lists of entries that failed to
    parse, by dotted name (each section's "More")."""

    canon: list[CanonRow]
    profiles: list[EntityProfileBody]
    context: ContextView
    run: RunFacts
    unparsed: Mapping[str, list[Any]]

    def unparsed_of(self, *names: str) -> dict[str, list[Any]]:
        return {name: self.unparsed[name] for name in names if name in self.unparsed}

    @property
    def canon_more(self) -> dict[str, list[Any]]:
        return self.unparsed_of("canon")

    @property
    def profiles_more(self) -> dict[str, list[Any]]:
        return self.unparsed_of("profiles")

    @property
    def context_more(self) -> dict[str, list[Any]]:
        return self.unparsed_of("context.items", "context.dropped")


def placed(story_id: str, body: RetrievalBody, names: Names, keep: Mapping[str, str]) -> Placed:
    """The Canon, Profiles, Context and Run facts sections from the one read. ``keep``: the
    question's parameters (:data:`KEEP_FIELDS`) an id link back keeps."""
    return Placed(
        canon=canon_rows(story_id, body, ResponseNames(body, names)),
        profiles=list(body.profiles),
        context=context_view(story_id, body, keep),
        run=run_facts(story_id, body, keep),
        unparsed=body.unparsed,
    )

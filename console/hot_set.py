r"""A story's hot set as the Inspect Memory tab (#140) shows it: pure functions from the Engine's
``GET /admin/stories/{id}/hot-set`` answer (:class:`HotSetBody`) and the story's cast to the
tab's rows, with every marker computed here (the template only prints them).

**Character rows** (Counts and Pins) are the union of the cast and the ids the answer's maps
name, in cast order, then the other ids sorted; an id absent from a map counts 0. ``cast`` is
``None`` when the page's story read failed: every row is then an id (convention 2), and links
carry no ``actor``.

**Markers** (text, not colour): a recount above its budget is "over budget" and its headroom
"over by N" (else "N left"); a pin count at its ceiling "at ceiling", above it "over ceiling"; a
stored count unlike the recount "differs (+N)" / "differs (\u2212N)" (stored minus recount). With
``stored_counters`` ``None`` no row has a stored count (the template says "not stored yet").

**Episodes** are due when ``deadline_turn`` ≤ ``head_turn``; **retirements** keep the Engine's
order (newest first). Both link per conventions 3 and 11 (:mod:`console.inspect`).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from console.cast import Character, character_labels
from console.engine.models import EpisodeBody, HotSetBody, HotSetBudgets, RetirementBody
from console.inspect import history_turn_url, retrieval_url

OVER_BUDGET = "over budget"
AT_CEILING = "at ceiling"
OVER_CEILING = "over ceiling"
STORY_VECTORS = "Story vectors"
MINUS = "\u2212"  # the minus sign

CHARACTER_SCOPE = "character"
STORY_SCOPE = "story"
SCOPE_LABELS: Mapping[str, str] = {CHARACTER_SCOPE: "Character", STORY_SCOPE: "Story"}

# The Engine's ``RETIRED_REASONS`` (``core/tables/memories.py``), with the tab's legend.
REASON_LEGEND: Mapping[str, str] = {
    "covered": "a consolidation output covers it",
    "floor": "decayed importance fell below the importance floor",
    "uncovered": (
        "retired at the deadline before a consolidation output covered it "
        "(becomes covered when one does)"
    ),
}

# The Browse grid (convention 11): kind rows x tier columns.
KINDS: tuple[str, ...] = ("episodic", "semantic", "reflection")
TIERS: tuple[str, ...] = ("hot", "retired")


@dataclass(frozen=True)
class BudgetRow:
    """One ``HotSetBudgets`` field: its label, formatted value (``title`` the raw value for a
    yes/no), and its source ("story dial" with its ``setting`` path, or "deployment ceiling")."""

    field: str
    label: str
    value: str
    title: str | None
    source: str
    setting: str | None


@dataclass(frozen=True)
class CountRow:
    """A Counts row: a character's unpinned hot memories (``character_id`` empty for the Story
    vectors row), its budget and headroom text, and the stored count (``None``: not stored yet)
    with ``stored_delta`` = stored - recount."""

    character_id: str
    label: str | None
    is_player: bool
    hot: int
    budget: int
    headroom: str
    stored: int | None
    stored_delta: int | None
    marker: str | None

    @property
    def differs(self) -> str | None:
        """ "differs (+N)" / "differs (\u2212N)" when the stored count isn't the recount."""
        if not self.stored_delta:
            return None
        sign = "+" if self.stored_delta > 0 else MINUS
        return f"differs ({sign}{abs(self.stored_delta):,})"


@dataclass(frozen=True)
class PinRow:
    """A Pins row: the memory pins a character holds against the per-character ceiling."""

    character_id: str
    label: str | None
    is_player: bool
    pins: int
    ceiling: int
    marker: str | None


@dataclass(frozen=True)
class StoryPins:
    """The story's pins: memory and text pins and their total against the per-story ceiling
    (the marker is the total's), and ``holderless`` memory pins no character holds."""

    memory_pins: int
    text_pins: int
    total: int
    ceiling: int
    marker: str | None
    holderless: int


@dataclass(frozen=True)
class HotSetRows:
    """What :func:`hot_set_rows` returns."""

    counts: list[CountRow]
    story_vectors: CountRow
    pins: list[PinRow]
    story_pins: StoryPins


@dataclass(frozen=True)
class EpisodeRow:
    """An open overflow episode. ``scope_label`` is ``None`` for a scope the Console doesn't
    know (rendered raw); ``label`` is the character's name for a cast character (else ``None``:
    the id renders); ``turns`` is how many turns remain, or (``due``) how many are past."""

    id: str
    scope: str
    scope_label: str | None
    scope_id: str
    label: str | None
    is_player: bool
    opened_turn: int
    deadline_turn: int
    due: bool
    turns: int
    excess_at_open: int
    excess: int

    @property
    def is_story(self) -> bool:
        return self.scope == STORY_SCOPE


@dataclass(frozen=True)
class RetirementRow:
    """A recent retirement: its memory id and Retrieval link (``None`` while #141 isn't
    registered), its holder (``None``: story-level), the reason (``reason_label`` ``None`` for
    an unknown or missing reason), and the retired turn's history link."""

    memory_id: str
    memory_url: str | None
    holder_id: str | None
    holder_label: str | None
    holder_is_player: bool
    reason: str | None
    reason_label: str | None
    retired_turn: int | None
    turn_url: str | None
    retired_at: str | None


@dataclass(frozen=True)
class BrowseLink:
    label: str
    url: str | None


@dataclass(frozen=True)
class BrowseRow:
    kind: str
    label: str
    links: list[BrowseLink]


def _count(n: int) -> str:
    return f"{n:,}"


def _turns(n: int) -> str:
    return f"{n:,} turn{'' if n == 1 else 's'}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def budget_rows(budgets: HotSetBudgets) -> list[BudgetRow]:
    """Every ``HotSetBudgets`` field, labelled, formatted and sourced, in the schema's order."""

    def dial(field: str, label: str, value: str, title: str | None = None) -> BudgetRow:
        return BudgetRow(field, label, value, title, "story dial", f"memory.hot_set.{field}")

    def ceiling(field: str, label: str, value: int) -> BudgetRow:
        return BudgetRow(field, label, _count(value), None, "deployment ceiling", None)

    b = budgets
    return [
        dial(
            "memories_per_character", "Hot memories per character", _count(b.memories_per_character)
        ),
        dial("vectors_per_story", "Hot vectors per story", _count(b.vectors_per_story)),
        dial("importance_floor", "Importance floor (retirable below)", f"{b.importance_floor:.3f}"),
        dial(
            "retirement_max_lag",
            "Retirement deadline after an overflow opens",
            _turns(b.retirement_max_lag),
        ),
        dial(
            "retain_retired_vectors",
            "Keep vectors of retired memories",
            _yes_no(b.retain_retired_vectors),
            str(b.retain_retired_vectors).lower(),
        ),
        dial(
            "retain_dormant_vectors",
            "Keep vectors of memories off the active timeline",
            _yes_no(b.retain_dormant_vectors),
            str(b.retain_dormant_vectors).lower(),
        ),
        ceiling(
            "max_pinned_per_character", "Pin ceiling per character", b.max_pinned_per_character
        ),
        ceiling(
            "max_pinned_per_story",
            "Pin ceiling per story (memory and text pins)",
            b.max_pinned_per_story,
        ),
    ]


def character_ids(cast: Sequence[Character] | None, *maps: Iterable[str]) -> list[str]:
    """The cast's ids in cast order, then every other id in ``maps``, sorted."""
    ordered = [c.id for c in cast or ()]
    known = set(ordered)
    others = sorted({key for keys in maps for key in keys} - known)
    return ordered + others


def _headroom(count: int, budget: int) -> tuple[str, str | None]:
    if count > budget:
        return f"over by {_count(count - budget)}", OVER_BUDGET
    return f"{_count(budget - count)} left", None


def _ceiling_marker(count: int, ceiling: int) -> str | None:
    if count > ceiling:
        return OVER_CEILING
    return AT_CEILING if count == ceiling else None


def _count_row(
    character_id: str,
    label: str | None,
    is_player: bool,
    hot: int,
    budget: int,
    stored: int | None,
) -> CountRow:
    headroom, marker = _headroom(hot, budget)
    delta = None if stored is None else stored - hot
    return CountRow(character_id, label, is_player, hot, budget, headroom, stored, delta, marker)


def hot_set_rows(body: HotSetBody, cast: Sequence[Character] | None) -> HotSetRows:
    """The Counts and Pins rows (module docstring: union, order, zero-fill, markers)."""
    labels = character_labels(cast) if cast is not None else {}
    players = {c.id for c in cast or () if c.is_player}
    budgets = body.budgets
    recount = body.counters
    stored = body.stored_counters
    stored_characters: Mapping[str, int] = stored.characters if stored is not None else {}

    counts = [
        _count_row(
            cid,
            labels.get(cid),
            cid in players,
            recount.characters.get(cid, 0),
            budgets.memories_per_character,
            None if stored is None else stored_characters.get(cid, 0),
        )
        for cid in character_ids(cast, recount.characters, stored_characters)
    ]
    story_vectors = _count_row(
        "",
        STORY_VECTORS,
        False,
        recount.story_vectors,
        budgets.vectors_per_story,
        None if stored is None else stored.story_vectors,
    )

    by_character = body.pins.by_character
    ceiling = budgets.max_pinned_per_character
    pins = []
    for cid in character_ids(cast, by_character):
        held = by_character.get(cid, 0)
        pins.append(
            PinRow(
                cid, labels.get(cid), cid in players, held, ceiling, _ceiling_marker(held, ceiling)
            )
        )
    memory_pins, text_pins = body.pins.memory_pins, body.pins.text_pins
    total = memory_pins + text_pins
    story_ceiling = budgets.max_pinned_per_story
    story_pins = StoryPins(
        memory_pins,
        text_pins,
        total,
        story_ceiling,
        _ceiling_marker(total, story_ceiling),
        max(0, memory_pins - sum(by_character.values())),
    )
    return HotSetRows(counts, story_vectors, pins, story_pins)


def episode_rows(
    episodes: Sequence[EpisodeBody], head_turn: int, cast: Sequence[Character] | None
) -> list[EpisodeRow]:
    """The open overflow episodes in the Engine's order, each due or with turns remaining."""
    labels = character_labels(cast) if cast is not None else {}
    players = {c.id for c in cast or () if c.is_player}
    rows = []
    for e in episodes:
        character = e.scope == CHARACTER_SCOPE
        due = e.deadline_turn <= head_turn
        rows.append(
            EpisodeRow(
                id=e.id,
                scope=e.scope,
                scope_label=SCOPE_LABELS.get(e.scope),
                scope_id=e.scope_id,
                label=labels.get(e.scope_id) if character else None,
                is_player=character and e.scope_id in players,
                opened_turn=e.opened_turn,
                deadline_turn=e.deadline_turn,
                due=due,
                turns=head_turn - e.deadline_turn if due else e.deadline_turn - head_turn,
                excess_at_open=e.excess_at_open,
                excess=e.excess,
            )
        )
    return rows


def retirement_link(
    story_id: str, memory_id: str, holder: str | None, cast: Sequence[Character] | None
) -> str | None:
    """A retired memory's Retrieval link: ``memory``, ``tier=retired``,
    ``include_quarantined=1``, and ``actor`` only for a holder in the cast (#141 refuses a
    non-cast actor; with the cast unknown, never)."""
    params = {"memory": memory_id, "tier": "retired", "include_quarantined": "1"}
    if holder is not None and any(c.id == holder for c in cast or ()):
        params["actor"] = holder
    return retrieval_url(story_id, params)


def _reason_label(reason: str | None) -> str | None:
    """A known reason humanised ("Covered"); ``None`` for an unknown or missing one."""
    if reason is None or reason not in REASON_LEGEND:
        return None
    return reason.replace("_", " ").capitalize()


def retirement_rows(
    story_id: str, retirements: Sequence[RetirementBody], cast: Sequence[Character] | None
) -> list[RetirementRow]:
    """The recent retirements in the Engine's order (newest first)."""
    labels = character_labels(cast) if cast is not None else {}
    players = {c.id for c in cast or () if c.is_player}
    return [
        RetirementRow(
            memory_id=r.memory_id,
            memory_url=retirement_link(story_id, r.memory_id, r.holder_character_id, cast),
            holder_id=r.holder_character_id,
            holder_label=labels.get(r.holder_character_id) if r.holder_character_id else None,
            holder_is_player=r.holder_character_id in players,
            reason=r.reason,
            reason_label=_reason_label(r.reason),
            retired_turn=r.retired_turn,
            turn_url=(
                history_turn_url(story_id, r.retired_turn) if r.retired_turn is not None else None
            ),
            retired_at=r.retired_at,
        )
        for r in retirements
    ]


def browse_rows(story_id: str) -> list[BrowseRow]:
    """The Browse grid: a Retrieval link per kind x tier (``url`` ``None`` while #141 isn't
    registered)."""
    return [
        BrowseRow(
            kind,
            kind.capitalize(),
            [
                BrowseLink(
                    f"{kind.capitalize()} · {tier}",
                    retrieval_url(story_id, {"kind": kind, "tier": tier}),
                )
                for tier in TIERS
            ],
        )
        for kind in KINDS
    ]


def more(body: HotSetBody) -> dict[str, object]:
    """Convention 13's "More": the response's ``story_id`` and each episode's ``id`` keyed by
    its scope id."""
    return {"story_id": body.story_id, "episodes": {e.scope_id: e.id for e in body.open_episodes}}

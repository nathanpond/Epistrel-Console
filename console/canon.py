"""A story's canon facts as the Canon tab shows them (#137; #138's lineage view and #146's fact
writes reuse the rows).

**The state rule** (:func:`fact_states`). The Engine records a version's *first* closure in
``tx_to`` and ``superseded_by`` and never rewrites them (``coalesce`` on close, in
``core/canon/facts.py``). A version is current when no **active** row names it in
``supersedes``; when the closing operation is truncated the version is current again and still
carries its old ``tx_to``. So a row's state never follows ``tx_to`` alone:

- without "include superseded" every row the Engine returned is ``current`` (at R under "as
  recorded at"; the header names R);
- with it: ``tx_to`` null → ``current``; a row on the page naming it in ``supersedes`` →
  ``superseded``; else the Console reads the current set once (:func:`read_current_set`: same
  subject and predicate, no time filters, up to 2,000 versions): in it → ``current (closure
  undone)``; missing from a complete set → ``superseded``; a capped or failed read, or one
  whose ``timeline_revision`` differs from the page's → ``closed · not confirmed``.

"Superseded by" lists the active rows **on the page** that name the version; with none, the
stored ``superseded_by`` shows as "first closed by" (it may name a truncated closer).

**Cells.** Valid-time ``[from, to)`` comes from the version's own fields, the inner bound shown
only where it differs from the outer (``[10 (inner 12), 40 (inner 38))``; an open bound "…").
Literals: int as is, float ``{:g}``, str double-quoted and cut to 80 characters, bool
``true``/``false``, date ISO, duration "N min", quantity "value unit". Qualifiers sort by key; a
list renders ``[a, b]``; a string matching the Engine's id pattern is an entity id (convention
2). Entity ids are named only when they are cast characters (``cast_names``).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.http import HttpRequest

from console.cast import Character, character_labels
from console.engine.client import EngineClient, EngineClientError
from console.engine.models import FactBody, Instant, Interval, Proposition, TypedLiteral
from console.engine_errors import log_engine_error
from console.ids import is_ulid
from console.inspect import history_turn_url
from console.predicates import PredicateRef, Refs

EMPTY = "—"
OPEN = "…"
STRING_SHOWN = 80
PLAYER_SUFFIX = " (player)"

CURRENT = "current"
SUPERSEDED = "superseded"
UNDONE = "closure_undone"
UNCONFIRMED = "unconfirmed"

# The current-set read (the state rule's last step): pages of 500, at most 4 of them.
CURRENT_PAGE = 500
CURRENT_PAGES = 4
CAPPED = "capped"
FAILED = "failed"
RACE = "race"
PROBLEM_TITLES: Mapping[str, str] = {
    CAPPED: "More than 2,000 current versions match; couldn't check whether this closure still "
    "stands",
    FAILED: "The check of current versions failed; couldn't tell whether this closure still stands",
    RACE: "Canon changed while this page loaded — reload",
}
PROBLEM_REASONS: Mapping[str, str] = {
    CAPPED: "more than 2,000 current versions match",
    FAILED: "the check of current versions failed",
    RACE: "canon changed while this page loaded — reload",
}
UNDONE_NOTE = "its closing operation left the timeline"
PERMANENT_NOTE = (
    "Facts of permanent predicates are listed here only when their valid-time covers minute T — "
    "untick 'include superseded' to see every fact true at T."
)


def utc_text(raw: str) -> str:
    """Convention 4's datetime to the second, ``2026-10-24 14:03:05 UTC`` (raw when unparsable)."""
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def cast_names(cast: Sequence[Character] | None) -> dict[str, str]:
    """Convention 2's cast names from the page's one ``get_story`` read: character id → label,
    the player marked " (player)"; ``{}`` when the read failed (ids show as ids)."""
    if cast is None:
        return {}
    labels = character_labels(cast)
    return {c.id: f"{labels[c.id]}{PLAYER_SUFFIX}" if c.is_player else labels[c.id] for c in cast}


# --- the state rule ------------------------------------------------------------------------------


@dataclass(frozen=True)
class FactState:
    """One row's state. ``superseded_by`` are the active rows on the page that name it;
    ``first_closed_by`` the stored ``superseded_by`` when none does (superseded rows only)."""

    key: str
    label: str
    title: str = ""
    note: str = ""
    superseded_by: tuple[str, ...] = ()
    first_closed_by: str | None = None

    @property
    def superseded(self) -> bool:
        return self.key == SUPERSEDED


CURRENT_STATE = FactState(CURRENT, "current")


@dataclass(frozen=True)
class CurrentSet:
    """The current-set read: the ids of every current version matching the page's subject and
    predicate, or ``problem`` (capped, failed, race) when the read can't settle the rule."""

    ids: frozenset[str] = frozenset()
    problem: str | None = None


def read_current_set(
    request: HttpRequest,
    client: EngineClient,
    story_id: str,
    *,
    subject: str | None,
    predicate: str | None,
    revision: int,
) -> CurrentSet:
    """``list_facts`` without ``include_superseded`` or time filters, ``limit=500``, following
    cursors for at most 4 pages. A ``next_cursor`` after the 4th page is ``capped``; any failure
    ``failed`` (logged); a page whose ``timeline_revision`` isn't ``revision`` is ``race``."""
    ids: set[str] = set()
    cursor: str | None = None
    for _ in range(CURRENT_PAGES):
        try:
            page = client.list_facts(
                story_id, subject=subject, predicate=predicate, limit=CURRENT_PAGE, cursor=cursor
            )
        except EngineClientError as exc:
            log_engine_error(request, exc)
            return CurrentSet(problem=FAILED)
        if page.timeline_revision != revision:
            return CurrentSet(problem=RACE)
        ids.update(f.id for f in page.facts)
        cursor = page.next_cursor
        if cursor is None:
            return CurrentSet(frozenset(ids))
    return CurrentSet(problem=CAPPED)


@dataclass(frozen=True)
class States:
    """:func:`fact_states`' answer: each row's state by id, and (when some closure couldn't be
    checked) how many and why, for the notice above the table."""

    by_id: dict[str, FactState]
    unconfirmed: int = 0
    problem: str | None = None
    current_read: bool = False  # whether the current-set read was made

    @property
    def notice(self) -> str | None:
        if not self.unconfirmed or self.problem is None:
            return None
        versions = "version is" if self.unconfirmed == 1 else "versions are"
        return (
            f"Couldn't confirm whether {self.unconfirmed} closed {versions} still closed: "
            f"{PROBLEM_REASONS[self.problem]}"
        )


def _superseded(fact: FactBody, closers: Sequence[str]) -> FactState:
    return FactState(
        SUPERSEDED,
        "superseded",
        superseded_by=tuple(closers),
        first_closed_by=None if closers else fact.superseded_by,
    )


def fact_states(
    facts: Sequence[FactBody],
    *,
    include_superseded: bool,
    current: Callable[[], CurrentSet],
) -> States:
    """The state of each row of one page (see the module docstring). ``current`` makes the
    current-set read; it is called at most once, and only when some row needs it."""
    if not include_superseded:
        return States({f.id: CURRENT_STATE for f in facts})
    closers: dict[str, list[str]] = {}
    for f in facts:
        for closed in f.supersedes:
            closers.setdefault(closed, []).append(f.id)
    by_id: dict[str, FactState] = {}
    pending: list[FactBody] = []
    for f in facts:
        if f.tx_to is None:
            by_id[f.id] = CURRENT_STATE
        elif f.id in closers:
            by_id[f.id] = _superseded(f, closers[f.id])
        else:
            pending.append(f)
    if not pending:
        return States(by_id)
    found = current()
    unconfirmed = 0
    for f in pending:
        if found.problem is not None:
            unconfirmed += 1
            by_id[f.id] = FactState(
                UNCONFIRMED, "closed · not confirmed", PROBLEM_TITLES[found.problem]
            )
        elif f.id in found.ids:
            closed_at = utc_text(f.tx_to or "")
            by_id[f.id] = FactState(
                UNDONE,
                "current (closure undone)",
                f"Closed at {closed_at} by an operation that has since left the timeline",
                UNDONE_NOTE,
            )
        else:
            by_id[f.id] = _superseded(f, ())
    return States(by_id, unconfirmed, found.problem if unconfirmed else None, current_read=True)


# --- the header ----------------------------------------------------------------------------------


def question_text(
    as_of_story_time: int | None, as_of_record_time: datetime | None, include_superseded: bool
) -> str:
    """The header naming the question asked (the story's Discretion wording)."""
    if include_superseded:
        if as_of_story_time is None:
            return "Every version in the record (current and superseded)"
        return f"Every version whose valid-time may cover minute {as_of_story_time}"
    true_at = "Current canon" if as_of_story_time is None else f"True at minute {as_of_story_time}"
    if as_of_record_time is None:
        recorded = "as the record stands now" if as_of_story_time is None else "as recorded now"
    else:
        recorded = f"as recorded at {as_of_record_time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    return f"{true_at} · {recorded}"


# --- cells ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityRef:
    """An entity id as shown: the cast label when it is a cast character, else the id."""

    id: str
    label: str | None


def entity_ref(entity_id: str, names: Mapping[str, str]) -> EntityRef:
    return EntityRef(entity_id, names.get(entity_id))


def number_text(value: object) -> str:
    """An int as is, a float ``{:g}``, a bool ``true``/``false``; anything else as ``str``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


@dataclass(frozen=True)
class LiteralView:
    """A literal's text, the full text when ``text`` was cut (for ``title``), and its kind."""

    text: str
    kind: str
    full: str = ""


def literal_view(literal: TypedLiteral) -> LiteralView:
    value, kind = literal.value, literal.kind
    if kind == "str" and isinstance(value, str):
        quoted = f'"{value}"'
        if len(value) > STRING_SHOWN:
            return LiteralView(f'"{value[:STRING_SHOWN]}…"', kind, quoted)
        return LiteralView(quoted, kind)
    if kind == "duration":
        return LiteralView(f"{number_text(value)} min", kind)
    if kind == "quantity":
        unit = f" {literal.unit}" if literal.unit else ""
        return LiteralView(f"{number_text(value)}{unit}", kind)
    return LiteralView(number_text(value), kind)


@dataclass(frozen=True)
class QualifierItem:
    """``key=value``: ``entity`` set when the value is an entity id (convention 2)."""

    key: str
    text: str
    entity: EntityRef | None = None


def qualifier_items(qualifiers: Mapping[str, Any], names: Mapping[str, str]) -> list[QualifierItem]:
    items = []
    for key in sorted(qualifiers):
        value = qualifiers[key]
        if isinstance(value, str) and is_ulid(value):
            items.append(QualifierItem(key, value, entity_ref(value, names)))
        elif isinstance(value, list):
            items.append(QualifierItem(key, f"[{', '.join(str(v) for v in value)}]"))
        else:
            items.append(QualifierItem(key, number_text(value)))
    return items


@dataclass(frozen=True)
class PropositionView:
    """``_proposition.html``'s parts. ``object_kind`` is ``entity``, ``literal``, ``set`` or
    ``none`` (an object-less predicate: "—"). ``polarity`` is set only when it is neither
    ``affirmed`` nor ``negated`` (shown raw)."""

    subject: EntityRef
    negated: bool
    polarity: str | None
    predicate: PredicateRef
    object_kind: str
    entity: EntityRef | None = None
    literal: LiteralView | None = None
    members: tuple[EntityRef, ...] = ()
    qualifiers: tuple[QualifierItem, ...] = ()


def proposition_view(
    proposition: Proposition,
    predicate_names: Mapping[str, str],
    names: Mapping[str, str],
    story_id: str | None,
) -> PropositionView:
    """The parts of ``proposition``. Predicate names link the Predicates tab (#136's ``Refs``,
    other-tab mode) when ``story_id`` is known."""
    if story_id:
        predicate = Refs(story_id, predicate_names).ref(proposition.predicate_id)
    else:
        pid = proposition.predicate_id
        predicate = PredicateRef(pid, predicate_names.get(pid), None)
    polarity = proposition.polarity
    common: dict[str, Any] = {
        "subject": entity_ref(proposition.subject_id, names),
        "negated": polarity == "negated",
        "polarity": None if polarity in ("affirmed", "negated") else polarity,
        "predicate": predicate,
        "qualifiers": tuple(qualifier_items(proposition.qualifiers, names)),
    }
    if proposition.object_id is not None:
        return PropositionView(
            object_kind="entity", entity=entity_ref(proposition.object_id, names), **common
        )
    if proposition.literal is not None:
        return PropositionView(
            object_kind="literal", literal=literal_view(proposition.literal), **common
        )
    if proposition.object_set is not None:
        members = tuple(entity_ref(m, names) for m in proposition.object_set)
        return PropositionView(object_kind="set", members=members, **common)
    return PropositionView(object_kind="none", **common)


def bound_text(outer: int | None, inner: int | None) -> str:
    """One end of a valid-time: the outer bound ("…" when open), with "(inner N)" only where
    the inner bound differs."""
    shown = OPEN if outer is None else str(outer)
    if inner is not None and inner != outer:
        return f"{shown} (inner {inner})"
    return shown


def valid_time_text(fact: FactBody) -> str:
    """``[from, to)`` from the version's own fields."""
    start = bound_text(fact.valid_from, fact.valid_from_inner)
    end = bound_text(fact.valid_to, fact.valid_to_inner)
    return f"[{start}, {end})"


def proposition_time_text(valid_time: Interval | Instant) -> str:
    """The proposition's own valid-time (in "More"): an interval ``[a, b)`` in minutes, or
    "instant at N min (granularity G, until M min)"."""
    if isinstance(valid_time, Instant):
        return (
            f"instant at {valid_time.at} min (granularity {valid_time.granularity}, "
            f"until {valid_time.until} min)"
        )
    start = valid_time.start
    end = valid_time.end
    start_text = OPEN if start is None else bound_text(start.outer, start.inner)
    end_text = OPEN if end is None else bound_text(end.outer, end.inner)
    return f"interval [{start_text}, {end_text}) min"


@dataclass(frozen=True)
class Moment:
    """A datetime cell: the raw ISO value (``title``/``datetime``) and convention 4's text."""

    raw: str
    shown: str


@dataclass(frozen=True)
class FactView:
    """``_fact_row.html``'s computed cells for one version."""

    valid_time: str
    tx_from: Moment
    tx_to: Moment | None
    scope: str
    characters: tuple[EntityRef, ...] | None  # ``None`` unless the scope is ``characters``
    turn_url: str | None
    proposition_time: str


def fact_view(fact: FactBody, names: Mapping[str, str], story_id: str | None) -> FactView:
    visibility = fact.visibility
    characters = None
    if visibility.scope == "characters":
        characters = tuple(entity_ref(c, names) for c in visibility.characters or ())
    turn_url = None
    if fact.narrative_turn is not None and story_id:
        turn_url = history_turn_url(story_id, fact.narrative_turn)
    return FactView(
        valid_time=valid_time_text(fact),
        tx_from=Moment(fact.tx_from, utc_text(fact.tx_from)),
        tx_to=None if fact.tx_to is None else Moment(fact.tx_to, utc_text(fact.tx_to)),
        scope=visibility.scope,
        characters=characters,
        turn_url=turn_url,
        proposition_time=proposition_time_text(fact.proposition.valid_time),
    )

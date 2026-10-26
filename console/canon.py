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

**Lineage** (#138): :func:`read_lineage` reads every version of one subject and predicate,
:func:`lineage_component` keeps those connected to a fact through supersede links (not
``lineage_id``: an automatic supersede and a remainder start new ones), :func:`lineage_states`
applies the state rule without the current-set read, and :func:`bar_spans` lays out the bars.
:func:`find_fact` resolves an id alone (until nathanpond/Epistrel#535's single-fact read).
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest
from django.urls import reverse

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
LINEAGE_CAPPED = "lineage_capped"  # #138: a lineage read past its 4 pages
PROBLEM_TITLES: Mapping[str, str] = {
    CAPPED: "More than 2,000 current versions match; couldn't check whether this closure still "
    "stands",
    FAILED: "The check of current versions failed; couldn't tell whether this closure still stands",
    RACE: "Canon changed while this page loaded — reload",
    LINEAGE_CAPPED: "More than 2,000 versions of this subject and predicate; couldn't check "
    "whether this closure still stands",
}
PROBLEM_REASONS: Mapping[str, str] = {
    CAPPED: "more than 2,000 current versions match",
    FAILED: "the check of current versions failed",
    RACE: "canon changed while this page loaded — reload",
    LINEAGE_CAPPED: "more than 2,000 versions of this subject and predicate",
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


# --- a fact's lineage (#138) ---------------------------------------------------------------------

# The lineage read: every version of one subject and predicate, pages of 500, at most 4 of them.
LINEAGE_PAGE = 500
LINEAGE_PAGES = 4
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def lineage_url(
    story_id: str, fact_id: str, subject: str | None = None, predicate: str | None = None
) -> str:
    """Convention 3: a fact id's link, ``inspect:canon-lineage`` with ``?subject=&predicate=``
    when both are known (an id-only link costs the view one extra read)."""
    url = reverse("inspect:canon-lineage", args=[story_id, fact_id])
    if subject and predicate:
        return f"{url}?{urlencode({'subject': subject, 'predicate': predicate})}"
    return url


def ulid_predecessor(value: str) -> str | None:
    """The id just before ``value`` in the Engine's id order: the last Crockford character
    decremented, with borrow. ``None`` for the all-zero id (or anything not an Engine id)."""
    if not is_ulid(value) or set(value) == {"0"}:
        return None
    chars = list(value)
    for i in range(len(chars) - 1, -1, -1):
        position = CROCKFORD.index(chars[i])
        if position:
            chars[i] = CROCKFORD[position - 1]
            break
        chars[i] = CROCKFORD[-1]
    return "".join(chars)


def find_fact(client: EngineClient, story_id: str, fact_id: str) -> FactBody | None:
    """One version by id, until the Engine has a single-fact read (nathanpond/Epistrel#535):
    ``include_superseded=true&limit=1`` after the id's predecessor (the list is ordered by id
    and the cursor means "after this id"). ``None`` when the version returned isn't
    ``fact_id`` (not in the active timeline). Raises :class:`EngineClientError` on failure."""
    page = client.list_facts(
        story_id, include_superseded=True, limit=1, cursor=ulid_predecessor(fact_id)
    )
    if page.facts and page.facts[0].id == fact_id:
        return page.facts[0]
    return None


def read_lineage(
    client: EngineClient, story_id: str, subject: str, predicate: str
) -> tuple[list[FactBody], bool]:
    """Every active version of ``subject`` and ``predicate`` (``include_superseded``, no time
    filters, ``limit=500``, at most 4 pages) and whether the read is complete (no
    ``next_cursor`` left). Raises :class:`EngineClientError` on failure: no partial lineage."""
    versions: list[FactBody] = []
    cursor: str | None = None
    for _ in range(LINEAGE_PAGES):
        page = client.list_facts(
            story_id,
            subject=subject,
            predicate=predicate,
            include_superseded=True,
            limit=LINEAGE_PAGE,
            cursor=cursor,
        )
        versions.extend(page.facts)
        cursor = page.next_cursor
        if cursor is None:
            return versions, True
    return versions, False


def _moment(raw: str | None) -> datetime | None:
    """An Engine datetime in UTC (a naive one read as UTC); ``None`` when absent or unparsable."""
    if raw is None:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _tx_order(fact: FactBody) -> tuple[datetime, str]:
    return (_moment(fact.tx_from) or datetime.max.replace(tzinfo=UTC), fact.id)


def lineage_component(versions: Sequence[FactBody], fact_id: str) -> list[FactBody]:
    """The versions connected to ``fact_id`` through supersede links, followed both ways: any
    row's ``supersedes``, and ``superseded_by`` where it names a row in ``versions`` (the whole
    transitive component, whatever lineage id, qualifiers or polarity). Ordered by ``tx_from``,
    then id. Empty when ``fact_id`` isn't among ``versions``."""
    by_id = {f.id: f for f in versions}
    if fact_id not in by_id:
        return []
    links: dict[str, set[str]] = {fid: set() for fid in by_id}
    for f in versions:
        named = [*f.supersedes, *([f.superseded_by] if f.superseded_by else [])]
        for other in named:
            if other in by_id and other != f.id:
                links[f.id].add(other)
                links[other].add(f.id)
    seen = {fact_id}
    pending = [fact_id]
    while pending:
        for other in links[pending.pop()]:
            if other not in seen:
                seen.add(other)
                pending.append(other)
    return sorted((by_id[fid] for fid in seen), key=_tx_order)


def lineage_states(component: Sequence[FactBody], complete: bool) -> States:
    """#137's state rule over a lineage read, without the current-set read: closers share the
    closed version's subject and predicate, so a **complete** read holds every active row that
    could close a version, and a closed row no row names is ``current (closure undone)``. With a
    capped read such a row is ``closed · not confirmed``."""

    def current() -> CurrentSet:
        if complete:
            return CurrentSet(frozenset(f.id for f in component))
        return CurrentSet(problem=LINEAGE_CAPPED)

    return fact_states(component, include_superseded=True, current=current)


# --- the lineage bars ----------------------------------------------------------------------------

BAR_MIN = 1.0  # percent


@dataclass(frozen=True)
class Span:
    """One bar in its 240 px track: ``left`` and ``width`` as percentages with 2 decimals,
    "…" at an open edge, and its bounds as text for ``title``."""

    left: str
    width: str
    title: str
    open_start: bool = False
    open_end: bool = False


@dataclass(frozen=True)
class Bars:
    """:func:`bar_spans`' answer: each axis's ends as header text, and each version's two bars
    by id (a version whose ``tx_from`` doesn't parse has no tx bar)."""

    valid_start: str
    valid_end: str
    always: bool  # no finite valid-time bound: every valid bar is the full width
    tx_start: str
    tx_end: str
    valid: dict[str, Span]
    tx: dict[str, Span]


def _span(
    start: float,
    end: float,
    low: float,
    length: float,
    title: str,
    *,
    open_start: bool = False,
    open_end: bool = False,
) -> Span:
    """Clamped to 0 to 100%, at least 1% wide; a minimum-width bar past 99% is shifted left to
    end at 100%."""
    left = min(max((start - low) / length * 100, 0.0), 100.0)
    right = min(max((end - low) / length * 100, 0.0), 100.0)
    width = max(right - left, BAR_MIN)
    left = min(left, 100.0 - width)
    return Span(f"{left:.2f}", f"{width:.2f}", title, open_start, open_end)


def _minute_text(value: int | None) -> str:
    return OPEN if value is None else str(value)


def _axis_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def bar_spans(
    versions: Sequence[FactBody], now: datetime, runs_to_now: Collection[str] = ()
) -> Bars:
    """The two bars of each version (the story's Discretion):

    - valid-time over the min..max of the lineage's finite outer bounds (that minute ± 1 when
      they are equal); an open start draws from 0% and an open end to 100%; with no finite
      bound every bar is the full width ("always");
    - tx-time over the earliest ``tx_from`` .. the latest of ``now``, every ``tx_from`` and every
      ``tx_to`` (1 second when that is zero long). An open ``tx_to``, and a version in
      ``runs_to_now`` (current, closure undone), is drawn to ``now`` (or its own ``tx_from``
      when the Engine's clock is ahead).
    """
    now = now.astimezone(UTC)
    bounds = [b for f in versions for b in (f.valid_from, f.valid_to) if b is not None]
    valid: dict[str, Span] = {}
    always = not bounds
    low = float(min(bounds)) if bounds else 0.0
    high = float(max(bounds)) if bounds else 0.0
    if bounds and low == high:
        low, high = low - 1, high + 1
    for f in versions:
        title = f"Valid-time [{_minute_text(f.valid_from)}, {_minute_text(f.valid_to)}) min"
        if always:
            valid[f.id] = Span("0.00", "100.00", f"{title}: always", True, True)
            continue
        start = low if f.valid_from is None else float(f.valid_from)
        end = high if f.valid_to is None else float(f.valid_to)
        valid[f.id] = _span(
            start,
            end,
            low,
            high - low,
            title,
            open_start=f.valid_from is None,
            open_end=f.valid_to is None,
        )

    starts = {f.id: m for f in versions if (m := _moment(f.tx_from)) is not None}
    ends = [m for f in versions if (m := _moment(f.tx_to)) is not None]
    tx: dict[str, Span] = {}
    tx_low = min(starts.values(), default=now)
    tx_high = max([now, *starts.values(), *ends])
    length = (tx_high - tx_low).total_seconds() or 1.0
    for f in versions:
        begin = starts.get(f.id)
        if begin is None:
            continue
        closed = _moment(f.tx_to)
        if closed is None or f.id in runs_to_now:
            finish, until = max(now, begin), "now"
        else:
            finish, until = closed, utc_text(f.tx_to or "")
        tx[f.id] = _span(
            (begin - tx_low).total_seconds(),
            (finish - tx_low).total_seconds(),
            0.0,
            length,
            f"Recorded {utc_text(f.tx_from)} → {until}",
        )
    return Bars(
        valid_start="" if always else f"{low:g} min",
        valid_end="" if always else f"{high:g} min",
        always=always,
        tx_start=_axis_time(tx_low),
        tx_end="now" if tx_high == now else _axis_time(tx_high),
        valid=valid,
        tx=tx,
    )

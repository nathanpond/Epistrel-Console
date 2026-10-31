"""The Ledger tab's rows (#148): a page of thunks and its promissory-ledger entries, as shown.

:func:`ledger_view` turns one ``LedgerBody`` (:class:`console.engine.models.Ledger`) into the
template's rows. Nothing here reads the Engine: the view hands in the page's predicate names (one
``list_predicates`` read), cast names (the shell's ``get_story``) and ``operation_links`` (one
call over the page's discharge ids).

- **A thunk row** (:class:`ThunkRow`) carries its typed sketch (:meth:`ThunkSketch.from_json`;
  ``None`` when it doesn't parse: the description alone, the raw sketch in "More"), the known
  arguments (``subject_id``, ``object_id`` and ``literal``, whichever aren't the open slot), the
  candidates (highest confidence first, at most :data:`CANDIDATES_SHOWN`), its introduced
  position and history link (convention 3, the active chain), the resolved fact's lineage link,
  the rejected bindings and the ledger entries owed on it.
- **Entries** keep the Engine's order. One whose ``thunk_id`` names a thunk on the page goes
  under it; one on a hidden fact's proposition goes in the "Owed on hidden facts" group, shown
  on the first page only; any other goes in "Entries for thunks not on this page".
- **Could-have-known flags** (#208, FR-COMMIT-11): each thunk row's typed flags as
  :class:`FlagLine` ("<name> · <severity> · gap <n> min", the severity humanised and titled by
  :data:`SEVERITY_TITLES`), in the Engine's order; a flag list that lost an item (C2) goes to
  the row's "More" as ``could_have_known_flags_as_sent``.
- **Hidden facts** (#208, FR-COMMIT-4): the first page's current canon facts scoped away from
  the shared view, one :class:`HiddenFactRow` each (fact link, proposition or its raw JSON, who
  knows it, valid bounds, committing turn); ``LedgerView.hidden_facts`` is ``None`` off the
  first page (not rendered). They don't follow the filters and aren't joined to the "Owed on
  hidden facts" entries (a hidden fact's proposition carries no id).
- **Counts** cover the whole page: thunks by state (the three states always, "other" only when
  an unknown state occurs), entries discharged / undischarged, hidden-fact ones included, and
  thunks carrying a could-have-known flag.
"""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.http import QueryDict
from django.urls import NoReverseMatch

from console.canon import EntityRef, LiteralView, entity_ref, lineage_url, literal_view
from console.engine.models import (
    CouldHaveKnownFlag,
    HiddenFact,
    InspectPosition,
    LedgerItem,
    Proposition,
    RejectedBinding,
    Thunk,
    ThunkSketch,
)
from console.inspect import history_turn_url, position_text
from console.predicates import PredicateRef, Refs
from console.wording import humanised

STATES = ("open", "resolved", "expired")
NEGATED = "negated"
CANDIDATES_SHOWN = 5
ON_PAGE = "on this page"
MATCHING = "matching filters"
FLAGS = "could_have_known_flags"
FLAGS_AS_SENT = "could_have_known_flags_as_sent"
SEVERITY_TITLES = {
    "mild": "under one story-day",
    "moderate": "under 30 story-days",
    "strong": "30 story-days or more",
}
CHARACTERS_SCOPE = "characters"
ENGINE_SCOPE = "engine"
ENGINE_ONLY = "Nobody (engine only)"
ENGINE_ONLY_TITLE = "Scoped to the engine: no character's partition holds it"


@dataclass(frozen=True)
class KnownArgument:
    """``subject=<entity>``, ``object=<entity>`` or ``literal=<value> (<kind>)``."""

    slot: str
    entity: EntityRef | None = None
    literal: LiteralView | None = None


@dataclass(frozen=True)
class CandidateView:
    """A candidate as "text (entity) 0.850": ``text`` may be empty, ``entity`` ``None``."""

    text: str
    entity: EntityRef | None
    confidence: float


@dataclass(frozen=True)
class FactLink:
    """A fact id and its lineage link (``None`` when the id can't be a lineage URL)."""

    id: str
    url: str | None


@dataclass(frozen=True)
class BindingRow:
    """A rejected binding: the claim id (unlinked), beat, bound entity and conflicting facts."""

    claim_id: str | None
    beat: int | None
    entity: EntityRef | None
    conflicting: tuple[FactLink, ...]


@dataclass(frozen=True)
class EntryRow:
    """One ledger entry: its introduced position (and history link), and the discharging
    operation's #44 row (``op_pk``, ``None`` when the Console never logged it)."""

    item: LedgerItem
    position: str | None
    position_url: str | None
    op_pk: int | None

    @property
    def discharged(self) -> bool:
        return self.item.discharged_by_operation is not None


@dataclass(frozen=True)
class FlagLine:
    """One could-have-known flag (#208): the bound character (cast name or short id, C4), the
    severity humanised (C3) with its scale as ``title`` and class ``flag-<severity>`` (a known
    severity) or ``flag-other``, and the gap in story-minutes as sent."""

    entity: EntityRef
    severity: str
    text: str
    title: str
    css_class: str
    gap: int


def flag_line(flag: CouldHaveKnownFlag, names: Mapping[str, str]) -> FlagLine:
    known = flag.severity in SEVERITY_TITLES
    return FlagLine(
        entity=entity_ref(flag.character_id, names),
        severity=flag.severity,
        text=humanised(flag.severity),
        title=SEVERITY_TITLES.get(flag.severity, flag.severity),
        css_class=f"flag-{flag.severity}" if known else "flag-other",
        gap=flag.gap_minutes,
    )


@dataclass(frozen=True)
class HiddenFactRow:
    """One hidden fact (#208): its lineage link, the typed proposition (``None`` when it
    doesn't parse: ``raw`` is shown instead), the scope (``kind`` ``characters``, ``engine`` or
    ``other``, with ``scope_text`` humanised for an other scope), the characters who know it
    (Engine order), the valid bounds' text and the committing turn and its history link."""

    fact: FactLink
    proposition: Proposition | None
    raw: Any
    scope: str
    kind: str
    scope_text: str
    known_to: tuple[EntityRef, ...]
    valid: str
    turn: int | None
    turn_url: str | None


def hidden_fact_row(story_id: str, fact: HiddenFact, names: Mapping[str, str]) -> HiddenFactRow:
    # console.scene imports this module's fact_link: imported here to break the cycle.
    from console.scene import valid_text

    proposition = fact.proposition_view
    if proposition is not None:
        link = fact_link(story_id, fact.fact_id, proposition.subject_id, proposition.predicate_id)
    else:
        link = fact_link(story_id, fact.fact_id)
    if fact.scope == CHARACTERS_SCOPE:
        kind = CHARACTERS_SCOPE
    elif fact.scope == ENGINE_SCOPE:
        kind = ENGINE_SCOPE
    else:
        kind = "other"
    known = () if kind == ENGINE_SCOPE else tuple(entity_ref(c, names) for c in fact.characters)
    turn = fact.narrative_turn
    return HiddenFactRow(
        fact=link,
        proposition=proposition,
        raw=fact.proposition,
        scope=fact.scope,
        kind=kind,
        scope_text=humanised(fact.scope),
        known_to=known,
        valid=valid_text(fact.valid_from, fact.valid_to),
        turn=turn,
        turn_url=history_turn_url(story_id, turn) if turn is not None else None,
    )


@dataclass(frozen=True)
class ThunkRow:
    """One thunk's row and the entries under it (``_ledger_entries.html``)."""

    thunk: Thunk
    sketch: ThunkSketch | None
    predicate: PredicateRef | None
    negated: bool
    known: tuple[KnownArgument, ...]
    candidates: tuple[CandidateView, ...]
    more_candidates: int
    position: str | None
    position_url: str | None
    resolved: FactLink | None
    parent: FactLink | None
    bindings: tuple[BindingRow, ...]
    entries: tuple[EntryRow, ...]
    more: dict[str, Any]
    flags: tuple[FlagLine, ...] = ()

    @property
    def state_class(self) -> str:
        """The badge's class: ``state-<state>`` for a known state, else the neutral badge."""
        state = self.thunk.state
        return f"state-{state}" if state in STATES else "state-other"


@dataclass(frozen=True)
class Counts:
    """The page's counts: ``states`` as ``(state, n)`` (the three, then any other states the
    page holds), entries discharged / undischarged, thunks with a could-have-known flag (typed,
    or unread in ``unparsed``), and the qualifier label ("on this page, matching filters";
    empty when neither applies)."""

    states: tuple[tuple[str, int], ...]
    other: int
    discharged: int
    undischarged: int
    label: str
    flagged: int = 0


@dataclass(frozen=True)
class LedgerView:
    """``hidden_facts`` is ``None`` off the first page (the section isn't rendered);
    ``hidden_facts_unread`` the raw list when an item didn't parse (C2)."""

    rows: tuple[ThunkRow, ...]
    hidden: tuple[EntryRow, ...]
    orphans: tuple[EntryRow, ...]
    counts: Counts
    revision: int
    hidden_facts: tuple[HiddenFactRow, ...] | None = None
    hidden_facts_unread: list[Any] | dict[str, Any] | None = None


def fact_link(
    story_id: str, fact_id: str, subject: str | None = None, predicate: str | None = None
) -> FactLink:
    """Convention 3's fact link (:func:`console.canon.lineage_url`), unlinked for an id the
    lineage route can't take."""
    try:
        return FactLink(fact_id, lineage_url(story_id, fact_id, subject, predicate))
    except NoReverseMatch:
        return FactLink(fact_id, None)


def _position(story_id: str, position: InspectPosition) -> tuple[str | None, str | None]:
    """Convention 4's text and convention 3's history link (``None`` for a null turn)."""
    text = position_text(position.turn, position.beat, position.variant)
    if position.turn is None:
        return text, None
    return text, history_turn_url(story_id, position.turn, position.variant)


def known_arguments(
    sketch: ThunkSketch, open_slot: str, names: Mapping[str, str]
) -> tuple[KnownArgument, ...]:
    """The sketch's known arguments in slot order, leaving out the open slot."""
    known: list[KnownArgument] = []
    if sketch.subject_id and open_slot != "subject":
        known.append(KnownArgument("subject", entity=entity_ref(sketch.subject_id, names)))
    if sketch.object_id and open_slot != "object":
        known.append(KnownArgument("object", entity=entity_ref(sketch.object_id, names)))
    if sketch.literal is not None and open_slot != "literal":
        known.append(KnownArgument("literal", literal=literal_view(sketch.literal)))
    return tuple(known)


def candidate_views(sketch: ThunkSketch, names: Mapping[str, str]) -> tuple[CandidateView, ...]:
    """Every candidate, highest confidence first (ties keep the Engine's order)."""
    ordered = sorted(sketch.candidates, key=lambda c: -c.confidence)
    return tuple(
        CandidateView(c.text, entity_ref(c.entity_id, names) if c.entity_id else None, c.confidence)
        for c in ordered
    )


def binding_row(story_id: str, binding: RejectedBinding, names: Mapping[str, str]) -> BindingRow:
    return BindingRow(
        binding.claim_id,
        binding.beat,
        entity_ref(binding.bound_entity, names) if binding.bound_entity else None,
        tuple(fact_link(story_id, f) for f in binding.conflicting_records),
    )


def entry_row(story_id: str, item: LedgerItem, links: Mapping[str, int]) -> EntryRow:
    position, url = _position(story_id, item.introduced_position)
    op = item.discharged_by_operation
    return EntryRow(item, position, url, links.get(op) if op else None)


def _more(
    thunk: Thunk, sketch: ThunkSketch | None, rest: Sequence[CandidateView]
) -> dict[str, Any]:
    """Convention 13's "More": the full id, and the sketch's texts, own open slot, span,
    parent fact, skipped and further candidates and unnamed keys; the raw sketch when it
    doesn't parse. Last, a flag list that lost an item (C2), as sent."""
    more = _sketch_more(thunk, sketch, rest)
    if FLAGS in thunk.unparsed:
        more[FLAGS_AS_SENT] = thunk.unparsed[FLAGS]
    return more


def _sketch_more(
    thunk: Thunk, sketch: ThunkSketch | None, rest: Sequence[CandidateView]
) -> dict[str, Any]:
    more: dict[str, Any] = {"id": thunk.id}
    if sketch is None:
        more["sketch"] = thunk.sketch
        return more
    for name in ("subject_text", "relation_text", "object_text", "time_text"):
        value = getattr(sketch, name)
        if value:
            more[name] = value
    more["sketch_open_slot"] = sketch.open_slot
    if sketch.span is not None:
        more["span"] = list(sketch.span)
    if rest:
        more["more_candidates"] = [
            {
                "text": c.text,
                "entity_id": c.entity.id if c.entity else None,
                "confidence": c.confidence,
            }
            for c in rest
        ]
    if sketch.skipped_candidates:
        more["unread_candidates"] = [raw for _, raw in sketch.skipped_candidates]
    more.update(sketch.extras)
    return more


def thunk_row(
    story_id: str,
    thunk: Thunk,
    entries: Sequence[EntryRow],
    refs: Refs,
    names: Mapping[str, str],
) -> ThunkRow:
    sketch = ThunkSketch.from_json(thunk.sketch)
    candidates = candidate_views(sketch, names) if sketch else ()
    position, position_url = _position(story_id, thunk.position)
    resolved = None
    if thunk.resolved_fact_id:
        subject = sketch.subject_id if sketch else None
        predicate = sketch.predicate_id if sketch else None
        resolved = fact_link(story_id, thunk.resolved_fact_id, subject, predicate)
    return ThunkRow(
        thunk=thunk,
        sketch=sketch,
        predicate=refs.ref(sketch.predicate_id) if sketch else None,
        negated=bool(sketch and sketch.polarity == NEGATED),
        known=known_arguments(sketch, thunk.open_slot, names) if sketch else (),
        candidates=candidates[:CANDIDATES_SHOWN],
        more_candidates=max(0, len(candidates) - CANDIDATES_SHOWN),
        position=position,
        position_url=position_url,
        resolved=resolved,
        parent=fact_link(story_id, sketch.parent_fact_id)
        if sketch and sketch.parent_fact_id
        else None,
        bindings=tuple(binding_row(story_id, b, names) for b in thunk.rejected_bindings),
        entries=tuple(entries),
        more=_more(thunk, sketch, candidates[CANDIDATES_SHOWN:]),
        flags=tuple(flag_line(f, names) for f in thunk.could_have_known_flags),
    )


def flagged(thunk: Thunk) -> bool:
    """A typed could-have-known flag, or a flag list that lost an item (C2)."""
    return bool(thunk.could_have_known_flags) or FLAGS in thunk.unparsed


def counts(
    thunks: Sequence[Thunk], entries: Sequence[LedgerItem], *, partial: bool, filtered: bool
) -> Counts:
    by_state = Counter(t.state for t in thunks)
    others = [(s, n) for s, n in by_state.items() if s not in STATES]
    discharged = sum(1 for e in entries if e.discharged_by_operation is not None)
    label = ", ".join(text for text, on in ((ON_PAGE, partial), (MATCHING, filtered)) if on)
    return Counts(
        states=tuple((s, by_state.get(s, 0)) for s in STATES),
        other=sum(n for _, n in others),
        discharged=discharged,
        undischarged=len(entries) - discharged,
        label=label,
        flagged=sum(1 for t in thunks if flagged(t)),
    )


def discharge_ids(entries: Sequence[LedgerItem]) -> list[str]:
    """The page's discharging operation ids, for the one ``operation_links`` call."""
    return [e.discharged_by_operation for e in entries if e.discharged_by_operation]


def ledger_view(
    story_id: str,
    thunks: Sequence[Thunk],
    entries: Sequence[LedgerItem],
    revision: int,
    *,
    refs: Refs,
    names: Mapping[str, str],
    links: Mapping[str, int],
    first_page: bool,
    partial: bool,
    filtered: bool,
    hidden_facts: Sequence[HiddenFact] = (),
    hidden_facts_unread: list[Any] | dict[str, Any] | None = None,
) -> LedgerView:
    """The page's rows. ``hidden_facts`` and ``hidden_facts_unread`` are dropped off the first
    page (the Engine sends ``[]`` there; whatever it holds is ignored)."""
    on_page: Collection[str] = {t.id for t in thunks}
    under: dict[str, list[EntryRow]] = {t.id: [] for t in thunks}
    hidden: list[EntryRow] = []
    orphans: list[EntryRow] = []
    for item in entries:
        row = entry_row(story_id, item, links)
        if item.thunk_id is not None and item.thunk_id in on_page:
            under[item.thunk_id].append(row)
        elif item.thunk_id is None and item.proposition_id is not None:
            if first_page:
                hidden.append(row)
        else:
            orphans.append(row)
    return LedgerView(
        rows=tuple(thunk_row(story_id, t, under[t.id], refs, names) for t in thunks),
        hidden=tuple(hidden),
        orphans=tuple(orphans),
        counts=counts(thunks, entries, partial=partial, filtered=filtered),
        revision=revision,
        hidden_facts=tuple(hidden_fact_row(story_id, f, names) for f in hidden_facts)
        if first_page
        else None,
        hidden_facts_unread=hidden_facts_unread if first_page else None,
    )


def paging(params: QueryDict, after_id: str | None, next_after_id: str | None) -> dict[str, Any]:
    """Convention 6's links: ``next_query`` keeps every filter and carries ``next_after_id``;
    ``first_query`` drops ``after_id``; ``paged`` when this page was reached by one."""
    first = params.copy()
    first.pop("after_id", None)
    following = ""
    if next_after_id:
        after = first.copy()
        after["after_id"] = next_after_id
        following = after.urlencode()
    return {"first_query": first.urlencode(), "next_query": following, "paged": bool(after_id)}


@dataclass(frozen=True)
class Empty:
    """The empty state's text and whether it offers "First page"."""

    text: str
    first_page: bool = False


NO_THUNKS = "No thunks yet: commitment records a reference the story leaves open."
NO_MATCH = "No thunks match these filters."
EMPTY_PAGE = "No thunks on this page — the ledger may have changed since the last page."


def empty_state(*, paged: bool, state_filter: bool) -> Empty:
    if paged:
        return Empty(EMPTY_PAGE, first_page=True)
    return Empty(NO_MATCH if state_filter else NO_THUNKS)

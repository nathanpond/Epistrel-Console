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
- **Counts** cover the whole page: thunks by state (the three states always, "other" only when
  an unknown state occurs) and entries discharged / undischarged, hidden-fact ones included.
"""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.http import QueryDict
from django.urls import NoReverseMatch

from console.canon import EntityRef, LiteralView, entity_ref, lineage_url, literal_view
from console.engine.models import InspectPosition, LedgerItem, RejectedBinding, Thunk, ThunkSketch
from console.inspect import history_turn_url, position_text
from console.predicates import PredicateRef, Refs

STATES = ("open", "resolved", "expired")
NEGATED = "negated"
CANDIDATES_SHOWN = 5
ON_PAGE = "on this page"
MATCHING = "matching filters"


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

    @property
    def state_class(self) -> str:
        """The badge's class: ``state-<state>`` for a known state, else the neutral badge."""
        state = self.thunk.state
        return f"state-{state}" if state in STATES else "state-other"


@dataclass(frozen=True)
class Counts:
    """The page's counts: ``states`` as ``(state, n)`` (the three, then any other states the
    page holds), entries discharged / undischarged, and the qualifier label ("on this page,
    matching filters"; empty when neither applies)."""

    states: tuple[tuple[str, int], ...]
    other: int
    discharged: int
    undischarged: int
    label: str


@dataclass(frozen=True)
class LedgerView:
    rows: tuple[ThunkRow, ...]
    hidden: tuple[EntryRow, ...]
    orphans: tuple[EntryRow, ...]
    counts: Counts
    revision: int


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
    doesn't parse."""
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
    )


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
) -> LedgerView:
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

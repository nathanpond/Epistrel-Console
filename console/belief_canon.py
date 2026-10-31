"""Beliefs compared with canon (#191): the Beliefs section's Canon column.

A character believing something false is the point of character knowledge (FR-EPI-3), so a
mark is information, not an error. The Engine has no route that compares a belief with canon,
so the Console reads the current canon once (:func:`read_current_canon`: the Canon tab's
``list_facts``, no subject, predicate, time or ``include_superseded`` filter, at most 4 pages
of 500, through :func:`console.canon.read_current_facts`) and applies **the Engine's own
contradiction rule for beliefs** to each belief and fact pair (:func:`canon_mark`).

**The rule** is ``core/epistemic/transitions.py::_conflicts`` (the rule that decides when a
new belief closes a held one; FR-PROP-4), with a current canon fact in place of the held
belief, over ``Proposition.normalized_key`` (``core/propositions/model.py``): subject,
predicate, object key and qualifier key. Object key: ``("entity", object_id)``;
``("set", sorted distinct members)``; ``("number", float(value))`` for a literal whose ``kind``
is ``int`` or ``float`` (the Engine's ``NUMERIC_KINDS``, by kind); ``(kind, (value, unit))``
for every other literal, unknown kinds included; ``("none", None)``. Qualifier key: items
sorted by key, list values as tuples in their given order. Plain Python equality on these
tuples mirrors the Engine's, quirks included (``True == 1``, ``3 == 3.0``).

A fact *matches* a belief when both have the same subject and predicate and their story-times
overlap: the belief's ``[valid_from, valid_to)`` as sent, the fact's outer ``[valid_from,
valid_to)``; ``None`` is open; a zero-width ``[a, a)`` is the point ``a``. Then:

- **contradicts**: the same normalized key with the other polarity; or the predicate is
  ``functional`` in the registry, both are affirmed, the qualifier keys are equal and the
  object keys differ;
- **agrees**: the same normalized key with the same polarity;
- **unsettled**: anything else. Any contradicting fact wins over agreeing ones.

Polarity is the parsed belief proposition's own (what ``_conflicts`` reads) and the fact
proposition's.

**Deviation.** The Engine closes an opposite-polarity belief whatever its time; here the
polarity case also needs overlapping time (a canon fact about another time says nothing about
the belief's time). Rules the Engine applies to claims but not to beliefs aren't used: closed
sets, max cardinality, exclusions, implications, the placement and containment rule, and
``same_as`` identities (a ``same_as`` row is compared like any other). Propositions are stored
in the predicate's canonical direction, so no inverse is flipped; proposition ids are minted
per write, so they are never matched.

Canon is the current record (no ``as_of_record_time``), every valid-time, whatever its
visibility. A past partition (``as_of`` set) isn't compared: canon is read by record time,
not by turn.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest

from console.canon import (
    CAPPED,
    FAILED,
    HELD,
    MODULE_OFF,
    RACE,
    CurrentFacts,
    read_current_facts,
)
from console.engine.client import EngineClient
from console.engine.models import BeliefItem, FactBody, Proposition

FIRST_ORDER = "first_order"
AFFIRMED = "affirmed"
FUNCTIONAL = "functional"
NUMERIC_KINDS = frozenset({"int", "float"})
SHOWN_FACTS = 3

CONTRADICTS = "contradicts"
AGREES = "agrees"
UNSETTLED = "unsettled"
NOT_COMPARED = "not_compared"

# ``CanonMark.reason`` for a ``not_compared`` mark (the facts-read problems reuse
# console.canon's ``CAPPED``/``FAILED``/``RACE``/``MODULE_OFF``).
NOT_FIRST_ORDER = "not_first_order"
PAST_TURN = "past_turn"
REGISTRY_UNAVAILABLE = "registry_unavailable"

NOT_FIRST_ORDER_TITLE = "Only first-order beliefs with a readable proposition are compared"
PAST_TURN_NOTICE = (
    "Canon is compared only for the current partition (canon is read by record time, not by "
    "turn) — clear 'As of turn' to compare"
)
REGISTRY_NOTICE = "Some beliefs weren't compared: the predicate registry couldn't be read"
PROBLEM_NOTICES: Mapping[str, str] = {
    CAPPED: "Not compared: this story has more than 2,000 current canon facts",
    FAILED: "Not compared: canon couldn't be read",
    RACE: "Not compared: the story changed while this page loaded — reload",
    MODULE_OFF: "Not compared: this story keeps no canon (`pipeline` is not structured)",
}
VERDICT_LABELS: Mapping[str, str] = {
    CONTRADICTS: "Contradicts canon",
    AGREES: "Canon agrees",
    UNSETTLED: "No canon record settles it",
}
VERDICT_TITLES: Mapping[str, str] = {
    CONTRADICTS: "Canon holds the other polarity, or another value of this functional predicate, "
    "over an overlapping story-time",
    AGREES: "Canon holds the same proposition with the same polarity over an overlapping "
    "story-time",
    UNSETTLED: "No current canon fact with this subject and predicate agrees or conflicts over "
    "an overlapping story-time",
}
NOT_COMPARED_LABELS: Mapping[str, str] = {
    PAST_TURN: "Not compared for past turns",
    REGISTRY_UNAVAILABLE: "Not compared (predicate registry unavailable)",
}
NOT_COMPARED_TITLES: Mapping[str, str] = {
    NOT_FIRST_ORDER: NOT_FIRST_ORDER_TITLE,
    PAST_TURN: PAST_TURN_NOTICE,
    REGISTRY_UNAVAILABLE: REGISTRY_NOTICE,
    **PROBLEM_NOTICES,
}
CSS_CLASSES: Mapping[str, str] = {
    CONTRADICTS: "canon-contradicts",
    AGREES: "canon-agrees",
    UNSETTLED: "muted canon-unsettled",
    NOT_COMPARED: "muted canon-not-compared",
}

Key = tuple[str, str]


@dataclass(frozen=True)
class CanonMark:
    """One belief's Canon cell: ``verdict`` (``contradicts``, ``agrees``, ``unsettled``,
    ``not_compared``), up to 3 fact ids of that verdict in ``list_facts`` order, how many more
    there are, and (``not_compared`` only) why."""

    verdict: str
    facts: tuple[str, ...] = ()
    more: int = 0
    reason: str | None = None

    @property
    def label(self) -> str:
        if self.verdict == NOT_COMPARED:
            return NOT_COMPARED_LABELS.get(self.reason or "", "Not compared")
        return VERDICT_LABELS[self.verdict]

    @property
    def title(self) -> str:
        """The rule in one sentence, or the sentence of the notice that explains the gap (shown
        in ``title`` without backticks)."""
        if self.verdict == NOT_COMPARED:
            return NOT_COMPARED_TITLES.get(self.reason or "", "").replace("`", "")
        return VERDICT_TITLES[self.verdict]

    @property
    def css_class(self) -> str:
        return CSS_CLASSES[self.verdict]


def not_compared(reason: str) -> CanonMark:
    return CanonMark(NOT_COMPARED, reason=reason)


# --- the rule ------------------------------------------------------------------------------------


def _freeze(value: Any) -> Any:
    return tuple(value) if isinstance(value, list | tuple) else value


def object_key(proposition: Proposition) -> tuple[str, Any]:
    """The Engine's ``Proposition.object_key`` (see the module docstring)."""
    if proposition.object_id is not None:
        return ("entity", proposition.object_id)
    if proposition.object_set is not None:
        return ("set", tuple(sorted(set(proposition.object_set))))
    literal = proposition.literal
    if literal is not None:
        value = literal.value
        if literal.kind in NUMERIC_KINDS and isinstance(value, int | float):
            return ("number", float(value))
        return (literal.kind, (value, literal.unit))
    return ("none", None)


def qualifier_key(proposition: Proposition) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted((k, _freeze(v)) for k, v in proposition.qualifiers.items()))


def normalized_key(proposition: Proposition) -> tuple[Any, ...]:
    return (
        proposition.subject_id,
        proposition.predicate_id,
        object_key(proposition),
        qualifier_key(proposition),
    )


def _contains(start: int | None, end: int | None, point: int) -> bool:
    return (start is None or start <= point) and (end is None or point < end)


def overlaps(
    belief_from: int | None, belief_to: int | None, fact_from: int | None, fact_to: int | None
) -> bool:
    """Whether ``[belief_from, belief_to)`` and ``[fact_from, fact_to)`` overlap (``None`` open).
    A zero-width ``[a, a)`` is the point ``a``: inside an interval holding it, and equal to
    another point only when it is the same."""
    belief_point = belief_from is not None and belief_from == belief_to
    fact_point = fact_from is not None and fact_from == fact_to
    if belief_point and fact_point:
        return belief_from == fact_from
    if belief_point and belief_from is not None:
        return _contains(fact_from, fact_to, belief_from)
    if fact_point and fact_from is not None:
        return _contains(belief_from, belief_to, fact_from)
    return (fact_to is None or belief_from is None or belief_from < fact_to) and (
        belief_to is None or fact_from is None or fact_from < belief_to
    )


def comparable(belief: BeliefItem) -> Proposition | None:
    """The belief's proposition when it can be compared (first order and parsed, C2)."""
    if belief.kind != FIRST_ORDER:
        return None
    return belief.proposition_view


def facts_by_key(facts: Sequence[FactBody]) -> dict[Key, list[FactBody]]:
    """The current facts indexed once by ``(subject_id, predicate_id)``, in read order."""
    index: dict[Key, list[FactBody]] = {}
    for fact in facts:
        key = (fact.proposition.subject_id, fact.proposition.predicate_id)
        index.setdefault(key, []).append(fact)
    return index


def _shown(verdict: str, ids: Sequence[str]) -> CanonMark:
    unique = list(dict.fromkeys(ids))
    return CanonMark(verdict, tuple(unique[:SHOWN_FACTS]), max(len(unique) - SHOWN_FACTS, 0))


def canon_mark(
    belief: BeliefItem,
    facts_by_key: Mapping[Key, Sequence[FactBody]],
    cardinality: Mapping[str, str] | None,
) -> CanonMark:
    """The belief's mark against every current fact with its subject and predicate.
    ``cardinality`` is predicate id → registry ``cardinality`` (a predicate it lacks gets
    exact-key verdicts only), or ``None`` when the registry read failed: a row whose only
    candidate verdict needs the functional rule is then ``not_compared``
    (``registry_unavailable``)."""
    held = comparable(belief)
    if held is None:
        return not_compared(NOT_FIRST_ORDER)
    candidates = facts_by_key.get((held.subject_id, held.predicate_id), ())
    key = normalized_key(held)
    functional = None if cardinality is None else cardinality.get(held.predicate_id) == FUNCTIONAL
    contradicting: list[str] = []
    agreeing: list[str] = []
    needs_registry = False
    for fact in candidates:
        if not overlaps(belief.valid_from, belief.valid_to, fact.valid_from, fact.valid_to):
            continue
        stored = fact.proposition
        if normalized_key(stored) == key:
            (agreeing if stored.polarity == held.polarity else contradicting).append(fact.id)
        elif (
            held.polarity == AFFIRMED
            and stored.polarity == AFFIRMED
            and qualifier_key(stored) == qualifier_key(held)
        ):
            if functional is None:
                needs_registry = True
            elif functional:
                contradicting.append(fact.id)
    if contradicting:
        return _shown(CONTRADICTS, contradicting)
    if agreeing:
        return _shown(AGREES, agreeing)
    if needs_registry:
        return not_compared(REGISTRY_UNAVAILABLE)
    return CanonMark(UNSETTLED)


def canon_marks(
    beliefs: Sequence[BeliefItem],
    facts_by_key: Mapping[Key, Sequence[FactBody]],
    cardinality: Mapping[str, str] | None,
) -> dict[str, CanonMark]:
    """Each belief's mark by ``belief_id`` (#190's ``marks``)."""
    return {b.belief_id: canon_mark(b, facts_by_key, cardinality) for b in beliefs}


# --- the read and the column ---------------------------------------------------------------------


def read_current_canon(
    request: HttpRequest, client: EngineClient, story_id: str, revision: int
) -> CurrentFacts:
    """The whole current canon in one read series (at most 4 ``list_facts`` calls, no filters),
    checked against the beliefs body's ``revision``. A held story (503 ``erasure_pending``)
    re-raises the Engine's refusal for #87's wrapper; every other problem is returned."""
    found = read_current_facts(
        request, client, story_id, subject=None, predicate=None, revision=revision
    )
    if found.problem == HELD and found.error is not None:
        raise found.error
    return found


def summary_text(contradicting: int, total: int) -> str:
    """C11: "2 of 5 beliefs on this page contradict canon" ("1 of 1 belief on this page
    contradicts canon"): the noun follows the page's count, the verb the contradicting one."""
    noun = "belief" if total == 1 else "beliefs"
    verb = "contradicts" if contradicting == 1 else "contradict"
    return f"{contradicting:,} of {total:,} {noun} on this page {verb} canon"


@dataclass(frozen=True)
class CanonColumn:
    """The Canon column's context: each row's mark, the notices (in order) and the summary
    line (``None`` when no row contradicts or the comparison wasn't made)."""

    marks: dict[str, CanonMark]
    notices: tuple[str, ...] = ()
    summary: str | None = None


def canon_column(
    request: HttpRequest,
    client: EngineClient,
    story_id: str,
    beliefs: Sequence[BeliefItem],
    revision: int,
    *,
    cardinality: Mapping[str, str] | None,
    as_of: int | None,
) -> CanonColumn:
    """The column for one page of beliefs: past turns aren't compared (no facts call); a page
    with no comparable row makes no facts call either; otherwise one read series, then
    :func:`canon_marks`. A facts problem marks every row ``not_compared`` with its notice; a
    failed registry read adds its notice after it."""
    if as_of is not None:
        return CanonColumn(
            {b.belief_id: not_compared(PAST_TURN) for b in beliefs}, (PAST_TURN_NOTICE,)
        )
    if not any(comparable(b) is not None for b in beliefs):
        return CanonColumn(canon_marks(beliefs, {}, cardinality))
    found = read_current_canon(request, client, story_id, revision)
    registry_notice = (REGISTRY_NOTICE,) if cardinality is None else ()
    if found.problem is not None:
        reason = found.problem if found.problem in PROBLEM_NOTICES else FAILED
        return CanonColumn(
            {b.belief_id: not_compared(reason) for b in beliefs},
            (PROBLEM_NOTICES[reason], *registry_notice),
        )
    marks = canon_marks(beliefs, facts_by_key(found.facts), cardinality)
    if not any(m.reason == REGISTRY_UNAVAILABLE for m in marks.values()):
        registry_notice = ()
    contradicting = sum(m.verdict == CONTRADICTS for m in marks.values())
    summary = summary_text(contradicting, len(beliefs)) if contradicting else None
    return CanonColumn(marks, registry_notice, summary)

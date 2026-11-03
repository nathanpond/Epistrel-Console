"""The World tab's Proposed States (#266; K2, Engine FR-STATE-3): the section's rows, its wording
and its links.

The Engine analyses a story's whole history on request (a 202 job with no status read, D18) and
proposes each unit or object that recurs in three or more turns that no State tracks. A proposal
(``StateProposal``) is listed oldest first; this module turns each into a :class:`ProposalRow`:

- **Suggests:** the suggested type and scope humanised (C3; a ``character`` scope reads "Per
  character", as the States section's scope does), "unit <unit>" when set, all joined by " · ";
  the suggested entity is a separate :class:`console.canon.EntityRef` (C4 label, else the short
  id), which the row's partial appends.
- **Evidence:** "Recurs in N turns" over the distinct turns (singular "1 turn"); with the Engine's
  ``EVIDENCE_MAX`` (10) entries, "Recurs in at least N turns (the Engine keeps the earliest 10
  quotes)". One :class:`EvidenceRow` per entry in the Engine's order, quoted in full and linked to
  ``console.inspect.history_turn_url(story_id, turn)``. The ``span`` (an offset into the beat's
  text, which the page doesn't have) goes in More only.
- **Status:** humanised with the raw value in ``title``; only ``proposed`` is actionable.
- **More:** "Proposal id", "Analysis request" (D19's ``engine_operation_url`` when it resolves),
  "Declared State" (the ``definition_id`` of an approved one: an id, since the declared name may
  differ from the proposal's) and one "Turn T evidence" line per entry.

:func:`count_text` is C11's line ("3 proposals · 1 pending", always with "· N pending"); the
section leaves it out when nothing is listed. The flash and notice wordings are here so the
views and the tests share them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.urls import reverse

from console.canon import EntityRef, entity_ref
from console.confirm import confirm_query
from console.effects import engine_operation_url
from console.engine.models import StateProposal
from console.inspect import history_turn_url
from console.states import SCOPE_LABELS
from console.wording import humanised

PROPOSED = "proposed"
# The Engine keeps at most the earliest 10 evidence entries (core/states/proposals.py).
EVIDENCE_MAX = 10

ANALYSIS_NOTICE = (
    "The analysis proposes each unit or object that recurs in three or more turns and that no "
    "State tracks. It never creates a State: approve or dismiss each proposal."
)
QUEUED_TEXT = (
    "Analysis queued (job {job_id}). Proposals it finds appear here when the worker finishes — "
    "reload this page to see them. The Engine reports no progress for the job, so the Console "
    "can't say when it's done."
)
REPLAYED_TEXT = "Already queued (repeated submit) — job {job_id}."
DISMISSED_TEXT = "Dismissed “{name}” — the analysis won't propose it again"
EMPTY_TEXT = (
    "No proposals yet — run an analysis to look for quantities and objects the story tracks no "
    "State for"
)
UNPARSED_TEXT = "Some proposals couldn't be read"
DISMISS_QUESTION = (
    "Dismiss the proposed State {name}? Nothing is tracked, and the analysis never proposes it "
    "again."
)
DISMISS_TITLE = "Dismiss a proposed State"
APPROVE_HEADING = "Approve “{name}”"
APPROVED_HEADING = "Approved “{name}”"
APPROVED_PROPOSAL_LINE = "Proposal {id} · {status}"
DECLARED_AS_LINE = "Declared as “{name}”"
UNCHANGED_NOTE = "Unchanged fields approve as suggested"
NULL_SPAN = "— (declared by the beat)"
EN_DASH = "\u2013"
NO_DECISION = "—"


def section_url(story_id: str) -> str:
    return reverse("inspect:state-proposals", args=[story_id])


def analyze_url(story_id: str) -> str:
    return reverse("inspect:state-proposals-analyze", args=[story_id])


def approve_url(story_id: str, proposal_id: str) -> str:
    return reverse("inspect:state-proposal-approve", args=[story_id, proposal_id])


def dismiss_url(story_id: str, proposal_id: str) -> str:
    return reverse("inspect:state-proposal-dismiss", args=[story_id, proposal_id])


def approved_url(story_id: str, pk: int) -> str:
    """``inspect:state-proposal-approved?report=<pk>``."""
    return f"{reverse('inspect:state-proposal-approved', args=[story_id])}?report={pk}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def count_text(proposals: Sequence[StateProposal]) -> str:
    """C11: "3 proposals · 1 pending" ("1 proposal · 0 pending"); parsed proposals only."""
    pending = sum(1 for p in proposals if p.status == PROPOSED)
    return f"{_plural(len(proposals), 'proposal', 'proposals')} · {pending:,} pending"


def scope_text(scope: str) -> str:
    return SCOPE_LABELS.get(scope) or humanised(scope)


def suggests_text(proposal: StateProposal) -> str:
    """The suggested type and scope (C3), then "unit <unit>" when set, joined by " · " (the
    entity, when suggested, is appended by the partial)."""
    parts = [humanised(proposal.suggested_type), scope_text(proposal.suggested_scope)]
    if proposal.suggested_unit:
        parts.append(f"unit {proposal.suggested_unit}")
    return " · ".join(parts)


def recurs_text(proposal: StateProposal) -> str:
    """ "Recurs in 2 turns" over the distinct turns; "at least" when the Engine's cap was hit."""
    turns = len({e.turn for e in proposal.evidence})
    counted = _plural(turns, "turn", "turns")
    if len(proposal.evidence) >= EVIDENCE_MAX:
        return f"Recurs in at least {counted} (the Engine keeps the earliest {EVIDENCE_MAX} quotes)"
    return f"Recurs in {counted}"


def span_text(span: Any) -> str:
    """ "12\u201330" (an en dash), or :data:`NULL_SPAN` for ``null`` (the beat declared the
    proposition)."""
    if span is None:
        return NULL_SPAN
    if isinstance(span, list) and len(span) == 2:
        return f"{span[0]}{EN_DASH}{span[1]}"
    return str(span)


@dataclass(frozen=True)
class EvidenceRow:
    """One quoted beat: "Turn T: “<text>”" linked to that turn in History."""

    turn: int
    text: str
    url: str
    event_id: str
    span_text: str

    @property
    def line(self) -> str:
        return f"Turn {self.turn}: “{self.text}”"


@dataclass(frozen=True)
class MoreLine:
    """A More ``<dt>``/``<dd>``: ``code`` renders the value in ``<code>``, ``url`` links it."""

    term: str
    value: str
    code: bool = False
    url: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class ProposalRow:
    """One ``<tr id="proposal-<id>">`` of ``_state_proposal_row.html``."""

    id: str
    name: str
    suggests_text: str
    entity_ref: EntityRef | None
    evidence: tuple[EvidenceRow, ...]
    turn_count: int
    recurs_text: str
    status_text: str
    status_raw: str
    created_at: str
    decided_at: str | None
    actionable: bool
    approve_url: str | None
    dismiss_url: str | None
    more: tuple[MoreLine, ...]


def evidence_rows(proposal: StateProposal, story_id: str) -> tuple[EvidenceRow, ...]:
    return tuple(
        EvidenceRow(
            turn=e.turn,
            text=e.text,
            url=history_turn_url(story_id, e.turn),
            event_id=e.event_id,
            span_text=span_text(e.span),
        )
        for e in proposal.evidence
    )


def _more(proposal: StateProposal, evidence: Sequence[EvidenceRow]) -> tuple[MoreLine, ...]:
    lines = [
        MoreLine("Proposal id", proposal.id, code=True),
        MoreLine(
            "Analysis request",
            proposal.created_by_operation_id,
            code=True,
            url=engine_operation_url(proposal.created_by_operation_id),
        ),
    ]
    if proposal.definition_id:
        lines.append(MoreLine("Declared State", proposal.definition_id))
    for e in evidence:
        lines.append(MoreLine(f"Turn {e.turn} evidence", e.event_id, code=True, detail=e.span_text))
    return tuple(lines)


def proposal_row(proposal: StateProposal, story_id: str, names: Mapping[str, str]) -> ProposalRow:
    evidence = evidence_rows(proposal, story_id)
    actionable = proposal.status == PROPOSED
    section = section_url(story_id)
    return ProposalRow(
        id=proposal.id,
        name=proposal.name,
        suggests_text=suggests_text(proposal),
        entity_ref=(
            entity_ref(proposal.suggested_entity_id, names)
            if proposal.suggested_entity_id
            else None
        ),
        evidence=evidence,
        turn_count=len({e.turn for e in evidence}),
        recurs_text=recurs_text(proposal),
        status_text=humanised(proposal.status) if proposal.status else NO_DECISION,
        status_raw=proposal.status,
        created_at=proposal.created_at,
        decided_at=proposal.decided_at,
        actionable=actionable,
        approve_url=approve_url(story_id, proposal.id) if actionable else None,
        dismiss_url=(
            f"{dismiss_url(story_id, proposal.id)}?{confirm_query(proposal.name, section)}"
            if actionable
            else None
        ),
        more=_more(proposal, evidence),
    )


def proposal_rows(
    proposals: Sequence[StateProposal], story_id: str, names: Mapping[str, str]
) -> list[ProposalRow]:
    """The section's rows, in the Engine's order."""
    return [proposal_row(p, story_id, names) for p in proposals]

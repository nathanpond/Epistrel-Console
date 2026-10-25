"""What the history browser (#52) shows, built from one ``GET /stories/{id}/history`` page.

- :func:`turn_groups`: the page's items under per-turn headers ("Turn N · variant V"), one row
  per item, with the turn's audit records under it and "no reply" marks;
- :func:`audit_record`: a ``guardrail_output_rejected`` (or other audit) item, read from its
  text, which the Engine fills with the event payload's canonical JSON;
- :func:`policy_names`: policy id → name from ``GET /stories/{id}/settings``;
- :func:`operation_links`: Engine operation id → the Console's operation-log row (#44).

#53 builds its timeline controls onto these groups.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from console.engine.models import (
    History,
    HistoryItem,
    StorySettings,
    SummarySegment,
    VariantChain,
)
from console.models import EngineOperation

if TYPE_CHECKING:
    from console.feedback import FeedbackWidget

INGEST_KIND = "ingest"  # the operation-log kind of an ingest batch (the client method's name)
REJECTED_KIND = "guardrail_output_rejected"
AUDIT_CHANNEL = "audit"
REPLY_CHANNELS = frozenset({"message", "narration"})
REPLY_KIND = "message_rendered"
NAME_WITHHELD = "(name withheld — erasure pending)"
INGEST_SCAN = 500  # newest ingest rows of the story looked through for a batch's operations


# --- Policies and audit records -----------------------------------------------------------------


def policy_names(settings: StorySettings) -> dict[str, str | None]:
    """Policy id → name, from the effective then the inactive policies. A duplicated id takes
    the first effective entry; a ``None`` name is a pending catalog erasure."""
    names: dict[str, str | None] = {}
    for policy in [*settings.effective, *settings.inactive]:
        names.setdefault(policy.id, policy.name)
    return names


@dataclass(frozen=True)
class AuditPolicy:
    """One policy an audit record names: its id, the label shown and the policy's message."""

    id: str
    label: str  # the name, NAME_WITHHELD, or the raw id when unresolved
    resolved: bool
    message: str


@dataclass(frozen=True)
class AuditRecord:
    """An audit item as shown under its turn. ``draft`` is the rejected text (``None`` when the
    record holds none); ``raw`` is the item's text when it is not a JSON object (an erasure
    marker)."""

    item: HistoryItem
    reason: str = ""
    attempt: int | None = None
    policies: tuple[AuditPolicy, ...] = ()
    draft: str | None = None
    raw: str = ""

    @property
    def policy_line(self) -> str:
        """``<kind> — <policy id>, …`` (the Demo's "guardrail_output_rejected —
        minors_no_sexual_content")."""
        ids = ", ".join(p.id for p in self.policies)
        return f"{self.item.kind} — {ids}" if ids else self.item.kind


def _payload(text: str) -> dict[str, Any] | None:
    try:
        found = json.loads(text)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _policy_label(policy_id: str, names: Mapping[str, str | None]) -> tuple[str, bool]:
    if policy_id not in names:
        return policy_id, False
    name = names[policy_id]
    return (name if name else NAME_WITHHELD), True


def audit_record(item: HistoryItem, names: Mapping[str, str | None]) -> AuditRecord:
    """``item`` (an audit-channel item) with its payload read: the policy ids (``policy_ids``,
    else the violations'), each violation's message and the rejected ``text``."""
    payload = _payload(item.text)
    if payload is None:
        return AuditRecord(item, raw=item.text)
    messages: dict[str, str] = {}
    violations = payload.get("violations")
    for violation in violations if isinstance(violations, list) else []:
        if isinstance(violation, dict) and isinstance(violation.get("policy_id"), str):
            message = violation.get("message")
            messages.setdefault(
                str(violation["policy_id"]), message if isinstance(message, str) else ""
            )
    raw_ids = payload.get("policy_ids")
    ids = [i for i in raw_ids if isinstance(i, str)] if isinstance(raw_ids, list) else []
    for policy_id in messages:
        if policy_id not in ids:
            ids.append(policy_id)
    policies = []
    for policy_id in ids:
        label, resolved = _policy_label(policy_id, names)
        policies.append(AuditPolicy(policy_id, label, resolved, messages.get(policy_id, "")))
    text = payload.get("text")
    attempt = payload.get("attempt")
    reason = payload.get("reason")
    return AuditRecord(
        item,
        reason=reason if isinstance(reason, str) else "",
        attempt=attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else None,
        policies=tuple(policies),
        draft=text if isinstance(text, str) else None,
        raw="" if isinstance(text, str) else item.text,
    )


def split_audit(history: History) -> tuple[list[HistoryItem], list[HistoryItem]]:
    """``(items, audit)``. The Engine sends audit records in ``audit`` beside ``items``; should
    an answer carry none there, audit-channel and ``guardrail_output_rejected`` items found in
    ``items`` are routed to the audit display instead (planner)."""
    entries = history.entries
    audit = history.audit_entries
    if audit is not None:
        return entries, audit

    def is_audit(item: HistoryItem) -> bool:
        return item.channel == AUDIT_CHANNEL or item.kind == REJECTED_KIND

    return [i for i in entries if not is_audit(i)], [i for i in entries if is_audit(i)]


# --- Operation links ----------------------------------------------------------------------------


def operation_links(story_id: str, operation_ids: Iterable[str]) -> dict[str, int]:
    """Engine operation id → the newest operation-log row sharing it; an ingest batch's row
    stands for every operation in its ``response.results``. Ids the Console never logged are
    absent (shown as plain text)."""
    wanted = {i for i in operation_ids if i}
    links: dict[str, int] = {}
    if not wanted:
        return links
    for op_id, pk in EngineOperation.objects.filter(engine_operation_id__in=wanted).values_list(
        "engine_operation_id", "pk"
    ):  # newest first (the model's ordering)
        links.setdefault(op_id, pk)
    missing = wanted - links.keys()
    if missing:
        rows = EngineOperation.objects.filter(
            story_id=story_id, kind__startswith=INGEST_KIND, response__isnull=False
        ).values_list("pk", "response")[:INGEST_SCAN]
        for pk, response in rows:
            results = response.get("results") if isinstance(response, dict) else None
            for result in results if isinstance(results, list) else []:
                found = result.get("operation_id") if isinstance(result, dict) else None
                if isinstance(found, str) and found in missing:
                    links.setdefault(found, pk)
    return links


# --- Turn groups --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One item's table row."""

    item: HistoryItem
    actor: str  # the actor's label, "—" when none
    render: tuple[str, ...]  # render characters' labels (raw id when not in the cast)
    log_pk: int | None  # the operation-log row its operation id links to
    same_message: bool = False  # shares message_id with the row before it (say + do)
    feedback: FeedbackWidget | None = None  # the "Rate" control (#83, set by the view)


@dataclass
class TurnGroup:
    turn: int
    variant: int | None
    rows: list[Row] = field(default_factory=list)
    audit: list[AuditRecord] = field(default_factory=list)
    no_reply: bool = False


def _label(character_id: str, labels: Mapping[str, str]) -> str:
    return labels.get(character_id, character_id)


def is_reply(item: HistoryItem) -> bool:
    return item.kind == REPLY_KIND or item.channel in REPLY_CHANNELS


def turn_groups(
    history: History,
    items: Sequence[HistoryItem],
    audit: Sequence[AuditRecord],
    *,
    labels: Mapping[str, str],
    links: Mapping[str, int],
    actor_filtered: bool,
) -> list[TurnGroup]:
    """The page's turns in ascending order. Audit records render under their turn only for
    turns on this page. A turn ≥ 1 with no reply item says "no reply" (not while an actor
    filter is set)."""
    groups: dict[tuple[int, int | None], TurnGroup] = {}

    def group(turn: int, variant: int | None) -> TurnGroup:
        key = (turn, variant)
        if key not in groups:
            groups[key] = TurnGroup(turn, variant)
        return groups[key]

    for summary in history.turns:
        group(summary.turn, summary.variant)
    previous: HistoryItem | None = None
    for item in items:
        same = (
            previous is not None
            and previous.message_id == item.message_id
            and previous.turn == item.turn
        )
        actor = _label(item.actor_character_id, labels) if item.actor_character_id else "—"
        render = tuple(_label(c, labels) for c in item.render_character_ids or ())
        row = Row(item, actor, render, links.get(item.operation_id), same)
        group(item.turn or 0, item.variant).rows.append(row)
        previous = item
    by_turn: dict[int, TurnGroup] = {}
    for g in groups.values():
        by_turn.setdefault(g.turn, g)
    for record in audit:
        turn = record.item.turn or 0
        target = groups.get((turn, record.item.variant)) or by_turn.get(turn)
        if target is not None:
            target.audit.append(record)
    ordered = sorted(groups.values(), key=lambda g: (g.turn, g.variant or 0))
    for g in ordered:
        g.no_reply = (
            not actor_filtered and g.turn >= 1 and not any(is_reply(r.item) for r in g.rows)
        )
    return ordered


def summary_rows(segments: Sequence[SummarySegment] | None) -> list[SummarySegment]:
    """Summary segments ordered by the first turn they cover."""
    return sorted(segments or (), key=lambda s: (s.covers_from, s.level))


# --- Variants -----------------------------------------------------------------------------------


def chain_key(chain: VariantChain) -> str:
    """A chain's ``variant`` query value, ``T:V``."""
    return f"{chain.turn}:{chain.variant}"


def chain_tip(chains: Sequence[VariantChain], variant: str) -> int | None:
    """The tip turn of the chain ``variant`` (``T:V``) names, if listed."""
    for chain in chains:
        if chain_key(chain) == variant:
            return chain.tip_turn
    return None

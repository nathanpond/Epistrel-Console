"""The Effects tab's Reconciliation rows (#249; D1, D9, D10, D11, D15, D19).

``GET /stories/{id}/reconciliation`` lists every outbox row the operator must reconcile, oldest
first (FR-EXT-13/14/21/22). This module words one listed row (:func:`listing_row`) for
``templates/console/inspect/_listing_row.html``: its full outbox id (D11, the anchor
``console.effects.outbox_url`` targets), the provider (linked through D19's
``console.effects.provider_url``), the provider value, the status and the listing reason (C3:
humanised, raw in ``title``; the reason then explained by :data:`REASON_TEXT`), since when, the
effect (:func:`effect_text`), the latest outcome, a late success's re-verification (D9's
``settle_reason_text``) and the compensating row.

**Actions.** "Settle now" (``inspect:settle-now``, #256) and "Compensate" (``inspect:compensate``,
#264) are offered only on a row whose shown ``reason`` is in :data:`SETTLEABLE_REASONS`, and only
while their URL names resolve (#64's nav rule; a ``NoReverseMatch`` for a row's args leaves that
row's link out too). With ``states`` below ``prompt_only`` (D1's ``module_gate``) each link is a
greyed ``<span class="disabled">``, its reason shown once above the table. The query each link
carries (:func:`action_query`) is display-only data the action never trusts (#45's rule).

A redacted row (Engine #211: ``value`` and everything after it omitted) shows its ids, status,
reason and since only; the value-bearing cells read "—" or :data:`REDACTED`.
"""

import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse
from django.utils.http import urlencode

from console.effects import provider_url
from console.engine.models import ListingOutcome, ReconciliationListing, Story
from console.engine_errors import EMPTY
from console.inspect import Unavailable
from console.wording import humanised
from console.world import settle_reason_text, signed_delta_text

SECTION = "inspect:reconciliation"
SETTLE_NOW = "inspect:settle-now"  # #256
COMPENSATE = "inspect:compensate"  # #264
SETTLE_NOW_LABEL = "Settle now"
COMPENSATE_LABEL = "Compensate"
GATE_ID = "reconciliation-actions-gate"

# The listings the Engine serves settle-now and compensate for: Engine
# ``orchestrator/jobs/settlement.py`` ``SETTLEABLE = (LATE, DISCARDED, CONFLICTED)`` at origin/main
# b8fd182 (#218).
SETTLEABLE_REASONS: tuple[str, ...] = (
    "late_success_after_expiry",
    "confirmed_discarded_origin",
    "settlement_conflict",
)

REASON_TEXT: dict[str, str] = {
    "unconfirmed": "Dispatched; the provider hasn't confirmed it yet",
    "failed": "The provider reported that it failed",
    "unknown": "The provider's answer is unknown — it may or may not have applied",
    "reconciliation_required": "A timeline edit took its turn out after the effect had gone out",
    "cancelled_on_reactivation": (
        "Cancelled while its turn was out; the turn is active again and the effect was not re-sent"
    ),
    "confirmed_unsettled": "Confirmed, but its settlement is no longer on the active timeline",
    "confirmed_discarded_origin": "Confirmed after the turn that requested it was discarded",
    "settlement_conflict": "Confirmed, but settling it breaks a State's rules",
    "late_success_after_expiry": "Confirmed after its reservation had expired",
    "outcome_conflict": "The provider reported two different outcomes",
    "reservation_expired": "The State hold waiting on it expired",
}

# The Engine's ``_listed`` docstring (core/outbox.py), not the AC's draft line (planner, pass 2).
SCOPE_LINE = (
    "Effects dispatched to write-capable providers that need attention, oldest first: "
    "unconfirmed, failed or unknown ones, and any the Engine marked for reconciliation. An "
    "effect that confirmed and settled isn't listed."
)
REDACTED = "Redacted by an erasure"
EMPTY_TEXT = "Nothing to reconcile — no dispatched effect is waiting."
NOT_FOUND = "Not found (the story may have been deleted)"
PASSED = "passed"
FAILED = "failed"


def count_text(n: int) -> str:
    """C11: "1 effect listed" / "1,234 effects listed"."""
    return "1 effect listed" if n == 1 else f"{n:,} effects listed"


def unreadable_text(n: int) -> str:
    """D15's notice: "1 listing couldn't be read — see More"."""
    noun = "listing" if n == 1 else "listings"
    return f"{n:,} {noun} couldn't be read — see More"


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def effect_text(effect: Any) -> str:
    """ "gold \u2212500 — “buy sword”": the State, then the change (a numeric ``delta`` through D5's
    ``signed_delta_text``, no unit; otherwise ``delta`` or ``value`` as compact JSON, ``delta``
    winning), then the effect's non-blank string ``reason``. "—" for an effect that isn't an
    object or names no string ``state``."""
    if not isinstance(effect, dict):
        return EMPTY
    state = effect.get("state")
    if not isinstance(state, str) or not state:
        return EMPTY
    delta = effect.get("delta")
    if delta is not None:
        change: str | None = signed_delta_text(delta) if _is_number(delta) else _compact(delta)
    elif "value" in effect:
        change = _compact(effect["value"])
    else:
        change = None
    text = state if change is None else f"{state} {change}"
    reason = effect.get("reason")
    if isinstance(reason, str) and reason.strip():
        text += f" — “{reason}”"
    return text


def is_redacted(listing: ReconciliationListing) -> bool:
    """A row an erasure redacted: the Engine leaves out ``value`` and everything after it."""
    return listing.value is None


def _effect_state(listing: ReconciliationListing) -> str | None:
    state = listing.effect.get("state") if isinstance(listing.effect, dict) else None
    return state if isinstance(state, str) and state else None


def section_url(story_id: str) -> str:
    return reverse(SECTION, args=[story_id])


def action_query(
    listing: ReconciliationListing,
    story: Story | None,
    next_url: str,
    *,
    with_state: bool = False,
) -> dict[str, str]:
    """An action link's display-only query: ``expected_revision`` (the shell's
    ``Story.timeline_revision``; D8), ``provider``, ``value``, ``listed_reason``, ``state``
    (Compensate only: ``with_state``) and ``next``. A key whose value is absent (no story read,
    a redacted row's ``value``, an effect without a string ``state``) is left out."""
    query: dict[str, str] = {}
    revision = story.timeline_revision if story is not None else None
    if isinstance(revision, int) and not isinstance(revision, bool):
        query["expected_revision"] = str(revision)
    if listing.provider:
        query["provider"] = listing.provider
    if listing.value:
        query["value"] = listing.value
    if listing.reason:
        query["listed_reason"] = listing.reason
    state = _effect_state(listing)
    if with_state and state is not None:
        query["state"] = state
    if next_url:
        query["next"] = next_url
    return query


@dataclass(frozen=True)
class ActionLink:
    """One action: its ``label``, ``url`` (with the query) and ``gated`` (render greyed)."""

    label: str
    url: str
    gated: bool


@dataclass(frozen=True)
class OutcomeCell:
    """The Latest outcome cell: "<kind> · via <via> · <received_at>", raw kind and via in
    ``title``."""

    kind: str
    via: str
    received_at: str
    title: str


@dataclass(frozen=True)
class ListingRow:
    """``_listing_row.html``'s context for one listed outbox row."""

    outbox_id: str
    provider: str
    provider_url: str | None
    value: str | None
    status: str
    status_text: str
    reason: str
    reason_text: str
    explanation: str
    since: str
    effect_text: str
    redacted: bool
    outcome: OutcomeCell | None
    reverification: str
    reverification_title: str
    compensated_by: str | None
    compensated_url: str | None
    actions: tuple[ActionLink, ...]
    effect_json: str


def _outcome(outcome: ListingOutcome | None) -> OutcomeCell | None:
    if outcome is None:
        return None
    return OutcomeCell(
        kind=humanised(outcome.kind) if outcome.kind else EMPTY,
        via=humanised(outcome.received_via) if outcome.received_via else EMPTY,
        received_at=outcome.received_at,
        title=" · ".join((outcome.kind, outcome.received_via)),
    )


def _reverification(listing: ReconciliationListing) -> tuple[str, str]:
    """(text with backtick spans, raw ``title``); ("", "") when absent."""
    check = listing.reverification
    if check is None:
        return "", ""
    if check.reverification == PASSED:
        return "Passed", PASSED
    if check.reverification == FAILED:
        if not check.reasons:
            return "Failed", FAILED
        return "Failed: " + "; ".join(settle_reason_text(r) for r in check.reasons), FAILED
    return humanised(check.reverification), check.reverification


def _link(name: str, story_id: str, outbox_id: str, query: dict[str, str]) -> str | None:
    """``name``'s URL with ``query``; ``None`` while it (or its args) don't resolve."""
    try:
        url = reverse(name, args=[story_id, outbox_id])
    except NoReverseMatch:
        return None
    return f"{url}?{urlencode(query)}" if query else url


def _actions(
    listing: ReconciliationListing,
    story_id: str,
    story: Story | None,
    gate: Unavailable | None,
) -> tuple[ActionLink, ...]:
    if listing.reason not in SETTLEABLE_REASONS:
        return ()
    next_url = f"{section_url(story_id)}#outbox-{listing.outbox_id}"
    links = []
    for name, label, with_state in (
        (SETTLE_NOW, SETTLE_NOW_LABEL, False),
        (COMPENSATE, COMPENSATE_LABEL, True),
    ):
        query = action_query(listing, story, next_url, with_state=with_state)
        url = _link(name, story_id, listing.outbox_id, query)
        if url is not None:
            links.append(ActionLink(label, url, gated=gate is not None))
    return tuple(links)


def listing_row(
    listing: ReconciliationListing,
    story_id: str,
    on_page: Collection[str],
    *,
    story: Story | None = None,
    gate: Unavailable | None = None,
) -> ListingRow:
    """One listed row's cells. ``on_page`` holds the page's outbox ids (Compensated by links
    ``#outbox-<id>`` only to one of them, exact match); ``story`` is the shell's story read (the
    actions' expected revision) and ``gate`` D1's ``module_gate(STATES, effective)``."""
    redacted = is_redacted(listing)
    reverification, reverification_title = _reverification(listing)
    compensated = listing.compensated_by or None
    return ListingRow(
        outbox_id=listing.outbox_id,
        provider=listing.provider,
        provider_url=provider_url(listing.provider),
        value=listing.value or None,
        status=listing.status,
        status_text=humanised(listing.status) if listing.status else EMPTY,
        reason=listing.reason,
        reason_text=humanised(listing.reason) if listing.reason else EMPTY,
        explanation=REASON_TEXT.get(listing.reason, ""),
        since=listing.since,
        effect_text=REDACTED if redacted else effect_text(listing.effect),
        redacted=redacted,
        outcome=_outcome(listing.outcome),
        reverification=reverification,
        reverification_title=reverification_title,
        compensated_by=compensated,
        compensated_url=(
            f"#outbox-{compensated}" if compensated is not None and compensated in on_page else None
        ),
        actions=_actions(listing, story_id, story, gate),
        effect_json=(
            ""
            if listing.effect is None or redacted
            else json.dumps(listing.effect, indent=2, ensure_ascii=False, default=str)
        ),
    )

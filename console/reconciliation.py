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

**Request-keyed rows** (M7, #291; Engine #661). A consumer-applied effect is listed by its
``request_id`` with no ``outbox_id``: it is never redacted, gets no Settle now or Compensate
(both name an outbox row), its id cell reads "Request <code>id</code>" with
:data:`REQUEST_NOTE` (#326) and its anchor is ``request-<request_id>`` (:func:`row_anchor`); a
raw "Request id" is the last entry under More. A row with neither id has no anchor.

**Resolve** (#326; FR-EXT-22, Engine #661 and #251). :func:`resolvable` names the rows the Engine
resolves (a request-keyed row by its ``request_id``, an ``imported_unsettled`` row by its
``outbox_id``); each gets "Applied elsewhere…" and "Abandoned…" to ``inspect:resolve-listing``
(only while it resolves; greyed like the others while D1's gate is closed), and no Settle now.
:func:`resolve_confirm` builds the confirm page, :func:`resolved_view` words the
``ListingResolved`` answer for the shared result template (``kind`` :data:`RESOLVE_KIND`) and
:func:`resolved_row` finds a ``?report=<pk>`` row.

**Settle now** (#256; D8, D9, D11, D19). :func:`settle_confirm` builds the ``inspect:settle-now``
confirm page (``console/confirm.py``'s confirmed mode) from the link's display-only query: the
facts (Story id, the full Outbox id, Provider linked through D19's ``provider_url``, Provider
value, "Listed as" = C3's humanise of the raw ``listed_reason``; "—" when absent) and the hidden
``expected_revision`` (D8) and display fields, so a re-render keeps them. :func:`settled_view`
words the ``SettledNow`` answer for ``templates/console/inspect/reconciliation_result.html``
(heading, D8's lines through ``console.world.slot_result``, the re-verification and reasons
through D9's ``settle_reason_text``, More), which #264's compensate result reuses;
:func:`settled_row` finds the #44 row a ``?report=<pk>`` permalink names (an ``ok``
:data:`SETTLE_KIND` of this story whose stored response parses).

**Compensate** (#264; D5, D8, D11, D19). :func:`compensate_facts` reads the row's display fields
(the link's ``value`` is carried on a POST as :data:`PROVIDER_VALUE_FIELD`, since the form's own
``value`` is the Engine's ``effect.value``); :func:`compensate_confirm` builds the confirm page,
restating the effect through :func:`effect_summary` ("gold · Alice · +500 — “refund”");
:func:`compensated_view` words the ``Compensated`` answer for the shared result template
(``kind`` :data:`COMPENSATE_KIND`: "Compensation sent: <new id> (<status>) compensates <id>", both
linked through ``outbox_url``) and :func:`compensated_row` finds a ``?report=<pk>`` row.
"""

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from django.urls import NoReverseMatch, reverse
from django.utils.http import urlencode

from console.confirm import ConfirmPage
from console.effects import outbox_url, provider_url
from console.engine.models import (
    Compensated,
    ListingResolved,
    ReconciliationListing,
    SettledNow,
    Story,
)
from console.engine_errors import EMPTY
from console.inspect import Unavailable
from console.models import EngineOperation
from console.wording import humanised
from console.world import (
    EXPECTED_REVISION_FIELD,
    OperationRow,
    settle_reason_text,
    signed_delta_text,
    slot_result,
)

SECTION = "inspect:reconciliation"
SETTLE_NOW = "inspect:settle-now"  # #256
COMPENSATE = "inspect:compensate"  # #264
RESOLVE_LISTING = "inspect:resolve-listing"  # #326
APPLIED_ELSEWHERE = "applied_elsewhere"
ABANDONED = "abandoned"
RESOLUTIONS: tuple[str, ...] = (APPLIED_ELSEWHERE, ABANDONED)  # the Engine's two (#326)
RESOLVE_LINK_LABELS: Mapping[str, str] = {
    APPLIED_ELSEWHERE: "Applied elsewhere…",
    ABANDONED: "Abandoned…",
}
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
    "imported_unsettled": (
        "Imported from a bundle with no final outcome — never sent; mark whether it was applied"
    ),
}
IMPORTED_UNSETTLED = "imported_unsettled"  # Engine #251: a parked imported outbox row
REQUEST_NOTE = "Applied by the consumer (no outbox row)"  # #326's request-id cell

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
    """A row an erasure redacted: the Engine leaves out ``value`` and everything after it. Only
    an outbox row is: a request-keyed row (no ``outbox_id``, M7) never is."""
    return listing.outbox_id is not None and listing.value is None


def row_anchor(listing: ReconciliationListing) -> str | None:
    """The row's ``<tr id>`` (no "#"): ``outbox-<outbox_id>``, else ``request-<request_id>`` for
    a request-keyed row (M7), else ``None`` (no anchor)."""
    if listing.outbox_id is not None:
        return f"outbox-{listing.outbox_id}"
    if listing.request_id is not None:
        return f"request-{listing.request_id}"
    return None


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
    """``_listing_row.html``'s context for one listed row. ``outbox_id`` is ``None`` for a
    request-keyed row (M7), whose ``request_id`` is set; ``anchor`` is :func:`row_anchor`."""

    outbox_id: str | None
    request_id: str | None
    anchor: str | None
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
    # #326: a request-keyed row's id cell reads "Request <code>request_id</code>" and this note.
    request_note: str = ""


class Outcome(Protocol):
    """An outcome the Latest outcome cell words: ``ListingOutcome`` (Reconciliation and the
    Outbox section's rows, #325) fits."""

    @property
    def kind(self) -> str: ...
    @property
    def received_via(self) -> str: ...
    @property
    def received_at(self) -> str: ...


def outcome_cell(outcome: Outcome | None) -> OutcomeCell | None:
    """The Latest outcome cell (#249), shared with the Outbox section (#325); ``None`` when
    absent."""
    if outcome is None:
        return None
    return OutcomeCell(
        kind=humanised(outcome.kind) if outcome.kind else EMPTY,
        via=humanised(outcome.received_via) if outcome.received_via else EMPTY,
        received_at=outcome.received_at,
        title=" · ".join((outcome.kind, outcome.received_via)),
    )


_outcome = outcome_cell


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


def _link(name: str, story_id: str, listing_id: str, query: Mapping[str, str]) -> str | None:
    """``name``'s URL for ``listing_id`` (an outbox id, or #326's listing id) with ``query``;
    ``None`` while it (or its args) don't resolve."""
    try:
        url = reverse(name, args=[story_id, listing_id])
    except NoReverseMatch:
        return None
    return f"{url}?{urlencode(query)}" if query else url


def resolvable(listing: ReconciliationListing) -> str | None:
    """The listing id ``resolve_listing`` takes (#326), or ``None`` for a row it doesn't resolve:
    a request-keyed row's non-blank ``request_id`` (whatever its reason: it has no outbox row,
    so Resolve is its only action), an ``imported_unsettled`` row's ``outbox_id``. A redacted
    row follows the same rule (the Engine decides)."""
    if listing.outbox_id is None:
        request_id = listing.request_id
        return request_id if isinstance(request_id, str) and request_id.strip() else None
    if listing.reason == IMPORTED_UNSETTLED and listing.outbox_id.strip():
        return listing.outbox_id
    return None


def _resolve_actions(
    listing: ReconciliationListing,
    listing_id: str,
    story_id: str,
    story: Story | None,
    gate: Unavailable | None,
) -> tuple[ActionLink, ...]:
    """#326's two links, each with its ``resolution`` and #249's query (``next`` the row's
    anchor); none while ``inspect:resolve-listing`` doesn't resolve."""
    anchor = f"request-{listing_id}" if listing.outbox_id is None else f"outbox-{listing.outbox_id}"
    query = action_query(listing, story, f"{section_url(story_id)}#{anchor}")
    links = []
    for resolution in RESOLUTIONS:
        url = _link(RESOLVE_LISTING, story_id, listing_id, {"resolution": resolution, **query})
        if url is None:
            return ()
        links.append(ActionLink(RESOLVE_LINK_LABELS[resolution], url, gated=gate is not None))
    return tuple(links)


def _actions(
    listing: ReconciliationListing,
    story_id: str,
    story: Story | None,
    gate: Unavailable | None,
) -> tuple[ActionLink, ...]:
    listing_id = resolvable(listing)
    if listing_id is not None:  # #326: before the settleable check (a request row has no outbox)
        return _resolve_actions(listing, listing_id, story_id, story, gate)
    outbox_id = listing.outbox_id
    if outbox_id is None or listing.reason not in SETTLEABLE_REASONS:
        return ()  # settle-now and compensate name an outbox row (M7: a request-keyed row has none)
    next_url = f"{section_url(story_id)}#{row_anchor(listing)}"
    links = []
    for name, label, with_state in (
        (SETTLE_NOW, SETTLE_NOW_LABEL, False),
        (COMPENSATE, COMPENSATE_LABEL, True),
    ):
        query = action_query(listing, story, next_url, with_state=with_state)
        url = _link(name, story_id, outbox_id, query)
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
        request_id=listing.request_id,
        anchor=row_anchor(listing),
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
        request_note=REQUEST_NOTE if listing.outbox_id is None and listing.request_id else "",
    )


# --- settle now (#256) ---------------------------------------------------------------------------

SETTLE_KIND = "settle_now"  # the #44 row's kind (the client method)
SETTLED_NOW = "inspect:settled-now"
SETTLE_TITLE = "Settle now"
SETTLE_QUESTION = "Settle effect {name} now?"
SETTLE_LINES: tuple[str, ...] = (
    "The Engine re-checks it against the story as it stands now (the item still the seller's, "
    "no other hold or owner, the State's bounds). If a check fails, nothing changes.",
    "If every check passes, it is applied at the next inter-turn slot and leaves the "
    "reconciliation list.",
)
PROVIDER_FACT = "Provider"
# The display fields a settle-now link carries (#249's ``action_query``), kept on a re-render.
DISPLAY_FIELDS: tuple[str, ...] = ("provider", "value", "listed_reason")
SETTLED = "settled"
QUEUED = "queued"
QUEUED_TEXT = (
    "Queued: a staged turn is still waiting on its outcome, so the effect is applied at the slot "
    "after that turn"
)
QUEUED_STAYS = "It stays on the Reconciliation list until it is applied — don't settle it again"
MALFORMED_OUTBOX_ID = "Not found (that isn't an outbox id)"
RESULT_UNAVAILABLE = "That result isn't available — see Reconciliation"
BACK_LABEL = "Back to Reconciliation"
SECTION_LABEL = "Reconciliation"
REVERIFIED_PASSED = "Re-verified: passed"


def _shown(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def settle_confirm(
    story_id: str, outbox_id: str, data: Mapping[str, Any], cancel_url: str
) -> ConfirmPage:
    """The ``inspect:settle-now`` confirm page for ``outbox_id`` from the GET or POST ``data``
    (display-only: ``provider``, ``value``, ``listed_reason``, ``expected_revision``);
    ``cancel_url`` is the request's safe ``next`` or the row's anchor."""
    provider = _shown(data, "provider")
    value = _shown(data, "value")
    listed = _shown(data, "listed_reason")
    hidden = {key: data[key] for key in DISPLAY_FIELDS if _shown(data, key) is not None}
    revision = data.get(EXPECTED_REVISION_FIELD)
    if isinstance(revision, str) and revision.strip():
        hidden[EXPECTED_REVISION_FIELD] = revision
    link = provider_url(provider) if provider is not None else None
    return ConfirmPage(
        title=SETTLE_TITLE,
        question=SETTLE_QUESTION,
        emphasis=outbox_id,
        confirm_label=SETTLE_NOW_LABEL,
        cancel_url=cancel_url,
        lines=SETTLE_LINES,
        facts=(
            ("Story id", story_id),
            ("Outbox id", outbox_id),
            (PROVIDER_FACT, provider or EMPTY),
            ("Provider value", value or EMPTY),
            ("Listed as", humanised(listed.strip()) if listed is not None else EMPTY),
        ),
        fact_urls={PROVIDER_FACT: link} if link is not None else {},
        hidden=hidden,
    )


@dataclass(frozen=True)
class ResultLine:
    """One result line: ``text`` (backtick spans become ``<code>``), linked when ``url``."""

    text: str
    url: str | None = None
    cls: str = ""


@dataclass(frozen=True)
class SettledView:
    """``reconciliation_result.html``'s context: the heading (``heading`` before and after the
    outbox id, which is ``None`` for a heading that names none), the lines, the reasons, the
    More items and the links."""

    heading: str
    heading_after: str
    heading_outbox_id: str | None
    heading_outbox_url: str | None
    notes: tuple[str, ...]
    lines: tuple[ResultLine, ...]
    reasons: tuple[str, ...]
    more: tuple[tuple[str, str], ...]
    replayed: bool
    back_url: str
    back_label: str
    report_url: str | None
    # #264: which action's result this is (the template tells the headings apart by it), and the
    # compensate heading's status and compensated row.
    kind: str = SETTLE_KIND
    status_text: str = ""
    compensates_id: str | None = None
    compensates_url: str | None = None


def settled_url(story_id: str, pk: int) -> str:
    """``inspect:settled-now?report=<pk>``."""
    return f"{reverse(SETTLED_NOW, args=[story_id])}?{urlencode({'report': pk})}"


def _row_pk(row: OperationRow | None) -> int | None:
    pk = row.pk if row is not None else None
    return pk if isinstance(pk, int) and not isinstance(pk, bool) else None


def settled_view(
    answer: SettledNow,
    row: EngineOperation | None,
    story_id: str,
    sent_key: str = "",
) -> SettledView:
    """The settle-now result (on the POST response and for ``?report=``): "Settled: effect <id>
    is applied" with D8's Slot, Operation and revision lines; :data:`QUEUED_TEXT` with "Slot: —"
    and no operation line; any other ``result`` "Result: <humanised>" with the slot line. Then the
    re-verification, the reasons and More (``outcome_id``, the Engine's ``position``, the
    answer's ``idempotency_key``, else ``sent_key``)."""
    back = section_url(story_id)
    slot = slot_result(story_id, answer, row, back)
    pk = _row_pk(row)
    lines: list[ResultLine] = []
    notes: tuple[str, ...] = ()
    outbox: str | None = None
    after = ""
    if answer.result == SETTLED:
        heading, outbox, after = "Settled: effect ", answer.outbox_id, " is applied"
    elif answer.result == QUEUED:
        heading, notes = QUEUED_TEXT, (QUEUED_STAYS,)
    else:
        heading = f"Result: {humanised(answer.result) if answer.result else EMPTY}"
    lines.append(ResultLine(f"Slot: {slot.slot_text or EMPTY}", slot.slot_url, "slot"))
    if answer.result != QUEUED and slot.operation_id is not None:
        url = reverse("operations:detail", args=[slot.operation_pk]) if slot.operation_pk else None
        lines.append(ResultLine(f"Operation: `{slot.operation_id}`", url, "operation"))
    lines.append(ResultLine(slot.revision_text, None, "revision"))
    if answer.reverification == PASSED:
        lines.append(ResultLine(REVERIFIED_PASSED, None, "reverification"))
    else:
        shown = humanised(answer.reverification) if answer.reverification else EMPTY
        lines.append(ResultLine(f"Re-verified: {shown}", None, "reverification"))
    key = answer.idempotency_key or sent_key
    more = (
        ("Outcome id", answer.outcome_id or EMPTY),
        ("Engine position", slot.engine_position or EMPTY),
        ("Idempotency key", key or EMPTY),
    )
    return SettledView(
        heading=heading,
        heading_after=after,
        heading_outbox_id=outbox,
        heading_outbox_url=outbox_url(story_id, outbox) if outbox is not None else None,
        notes=notes,
        lines=tuple(lines),
        reasons=tuple(settle_reason_text(r) for r in answer.reasons),
        more=more,
        replayed=row is not None and row.replayed,
        back_url=back,
        back_label=BACK_LABEL,
        report_url=settled_url(story_id, pk) if pk is not None else None,
    )


def settled_row(pk: object, story_id: str) -> tuple[EngineOperation, SettledNow] | None:
    """The #44 row ``pk`` and its answer when it is an ``ok`` :data:`SETTLE_KIND` of this story
    whose stored response parses as ``SettledNow``; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError, TypeError:
        return None
    if (
        row.kind != SETTLE_KIND
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    try:
        answer = SettledNow.from_json(row.response)
    except Exception:
        return None
    return None if answer is None else (row, answer)


# --- compensate (#264) ---------------------------------------------------------------------------

COMPENSATE_KIND = "compensate"  # the #44 row's kind (the client method)
COMPENSATED = "inspect:compensated"
COMPENSATE_TITLE = "Compensate an effect"
COMPENSATE_QUESTION = "Send a compensating effect for {name}?"
COMPENSATE_CONFIRM_LABEL = "Send compensation"
COMPENSATE_LINE = (
    "It becomes a new outbox row, dispatched to the provider like any other. The listed effect "
    "shows Compensated until that row is confirmed."
)
COMPENSATE_RESULT_TITLE = "Compensate"
STORY_STATE = "story State"
# The link's ``value`` (the provider value) is carried on a POST as ``provider_value``: the form's
# own ``value`` is the Engine's ``effect.value`` member.
PROVIDER_VALUE_FIELD = "provider_value"
COMPENSATE_DISPLAY_FIELDS: tuple[str, ...] = ("provider", PROVIDER_VALUE_FIELD, "listed_reason")


@dataclass(frozen=True)
class CompensateFacts:
    """What the compensate form and its confirm page show of the listed row (display-only, from
    #249's link query on GET and the hidden fields on POST; never trusted for the action)."""

    provider: str | None
    provider_url: str | None
    provider_value: str | None
    listed_reason: str | None
    hidden: Mapping[str, str]

    @property
    def listed_text(self) -> str:
        return humanised(self.listed_reason.strip()) if self.listed_reason else EMPTY


def compensate_facts(data: Mapping[str, Any], *, posted: bool) -> CompensateFacts:
    """The display fields from the GET query (``provider``, ``value``, ``listed_reason``) or the
    POST's hidden fields (``provider``, ``provider_value``, ``listed_reason``)."""
    provider = _shown(data, "provider")
    provider_value = _shown(data, PROVIDER_VALUE_FIELD if posted else "value")
    listed = _shown(data, "listed_reason")
    hidden: dict[str, str] = {}
    for key, shown in zip(
        COMPENSATE_DISPLAY_FIELDS, (provider, provider_value, listed), strict=True
    ):
        if shown is not None:
            hidden[key] = shown
    return CompensateFacts(
        provider=provider,
        provider_url=provider_url(provider) if provider is not None else None,
        provider_value=provider_value,
        listed_reason=listed,
        hidden=hidden,
    )


def compensate_url(story_id: str, outbox_id: str) -> str:
    return reverse(COMPENSATE, args=[story_id, outbox_id])


def row_url(story_id: str, outbox_id: str) -> str:
    """The listed row's anchor on the Reconciliation list."""
    return f"{section_url(story_id)}#outbox-{outbox_id}"


def effect_summary(effect: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """ "gold · Alice · +500 — “refund”": the State; the character's C4 label, else its raw id
    (cast unread, or a kept option), else "story State"; D5's signed delta or the value as
    compact JSON (text quoted); the reason."""
    scope = effect.get("scope_key")
    character = (names.get(scope) or scope) if isinstance(scope, str) and scope else STORY_STATE
    if effect.get("delta") is not None:
        change = signed_delta_text(effect["delta"])
    else:
        change = json.dumps(effect.get("value"), ensure_ascii=False, separators=(",", ":"))
    return f"{effect.get('state')} · {character} · {change} — “{effect.get('reason')}”"


def compensate_confirm(
    story_id: str,
    outbox_id: str,
    effect: Mapping[str, Any],
    names: Mapping[str, str],
    facts: CompensateFacts,
    typed: Mapping[str, str],
) -> ConfirmPage:
    """The compensate confirm page: the effect restated, the row's facts and, as hidden fields,
    the values **as typed** (``typed``; D8: a cleaned ``True`` or ``"12"`` doesn't round-trip),
    the display fields and ``expected_revision``. Its Edit button posts them back with
    ``edit=1``."""
    link = facts.provider_url
    return ConfirmPage(
        title=COMPENSATE_TITLE,
        question=COMPENSATE_QUESTION,
        emphasis=outbox_id,
        confirm_label=COMPENSATE_CONFIRM_LABEL,
        cancel_url=row_url(story_id, outbox_id),
        lines=(effect_summary(effect, names), COMPENSATE_LINE),
        facts=(
            ("Story id", story_id),
            ("Outbox id", outbox_id),
            (PROVIDER_FACT, facts.provider or EMPTY),
            ("Provider value", facts.provider_value or EMPTY),
        ),
        fact_urls={PROVIDER_FACT: link} if link is not None else {},
        hidden={**typed, **facts.hidden},
        buttons_template="console/inspect/_compensate_edit.html",
    )


def compensated_url(story_id: str, pk: int) -> str:
    """``inspect:compensated?report=<pk>``."""
    return f"{reverse(COMPENSATED, args=[story_id])}?{urlencode({'report': pk})}"


def compensated_view(
    answer: Compensated,
    row: EngineOperation | None,
    story_id: str,
    sent_key: str = "",
) -> SettledView:
    """The compensate result: "Compensation sent: <new id> (<status>) compensates <id>", each id
    linked to its Reconciliation anchor, D8's Slot, Operation and revision lines, and More (the
    Engine's ``position``, the #44 row's kept ``Idempotency-Key``, else ``sent_key``)."""
    back = section_url(story_id)
    slot = slot_result(story_id, answer, row, back)
    pk = _row_pk(row)
    lines = [ResultLine(f"Slot: {slot.slot_text or EMPTY}", slot.slot_url, "slot")]
    if slot.operation_id is not None:
        url = reverse("operations:detail", args=[slot.operation_pk]) if slot.operation_pk else None
        lines.append(ResultLine(f"Operation: `{slot.operation_id}`", url, "operation"))
    lines.append(ResultLine(slot.revision_text, None, "revision"))
    key = (row.idempotency_key if row is not None else "") or answer.idempotency_key or sent_key
    return SettledView(
        heading="Compensation sent: ",
        heading_after="",
        heading_outbox_id=answer.outbox_id,
        heading_outbox_url=outbox_url(story_id, answer.outbox_id),
        notes=(),
        lines=tuple(lines),
        reasons=(),
        more=(
            ("Engine position", slot.engine_position or EMPTY),
            ("Idempotency key", key or EMPTY),
        ),
        replayed=row is not None and row.replayed,
        back_url=back,
        back_label=BACK_LABEL,
        report_url=compensated_url(story_id, pk) if pk is not None else None,
        kind=COMPENSATE_KIND,
        status_text=humanised(answer.status) if answer.status else EMPTY,
        compensates_id=answer.compensates or None,
        compensates_url=outbox_url(story_id, answer.compensates),
    )


def compensated_row(pk: object, story_id: str) -> tuple[EngineOperation, Compensated] | None:
    """The #44 row ``pk`` and its answer when it is an ``ok`` :data:`COMPENSATE_KIND` of this
    story whose stored response parses as ``Compensated``; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError, TypeError:
        return None
    if (
        row.kind != COMPENSATE_KIND
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    try:
        answer = Compensated.from_json(row.response)
    except Exception:
        return None
    return None if answer is None else (row, answer)


# --- resolve (#326) ------------------------------------------------------------------------------

RESOLVE_KIND = "resolve_listing"  # the #44 row's kind (the client method)
RESOLVED = "inspect:resolved"
RESOLVE_VERB = "resolve an effect"
RESOLVE_TITLE = "Resolve effect"
RESOLVE_RESULT_TITLE = "Resolve effect"
RESOLUTION_FIELD = "resolution"
RESOLUTION_TEXT: Mapping[str, str] = {
    APPLIED_ELSEWHERE: "applied elsewhere",
    ABANDONED: "abandoned",
}
RESOLVE_QUESTION = "Mark {name} as %s?"
RESOLVE_LINES: Mapping[str, str] = {
    APPLIED_ELSEWHERE: (
        "The consumer applied it outside Epistrel. Nothing is sent and no outcome is recorded; "
        "the listing closes."
    ),
    ABANDONED: (
        "It will never be applied. Nothing is sent and no outcome is recorded; the listing closes."
    ),
}
RESOLVE_AUDIT_LINE = "Recorded as an audit event at the next inter-turn slot."
RESOLVE_CONFIRM_LABELS: Mapping[str, str] = {
    APPLIED_ELSEWHERE: "Mark applied elsewhere",
    ABANDONED: "Mark abandoned",
}
RESOLVE_LABEL = "Resolve"  # a blocked page's (disabled) submit
RESOLUTION_BLOCKED = "Choose applied elsewhere or abandoned"
MALFORMED_LISTING_ID = "Not found (that isn't a listing id)"
OUTBOX_ID_FACT = "Outbox id"
REQUEST_ID_FACT = "Request id"
LISTING_ID_FACT = "Listing id"


def chosen_resolution(data: Mapping[str, Any]) -> str | None:
    """The ``resolution`` in ``data`` (the link's query, or the POST's hidden field): stripped,
    and one of :data:`RESOLUTIONS` exactly; else ``None`` (the page is blocked)."""
    value = data.get(RESOLUTION_FIELD)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value in RESOLUTIONS else None


def _listed_kind(listed: str | None) -> tuple[str, str | None]:
    """(the id fact's label, the row's anchor prefix) from the display-only ``listed_reason``:
    ``imported_unsettled`` names an outbox row, any other reason a request; absent, neither."""
    if listed is None:
        return LISTING_ID_FACT, None
    if listed.strip() == IMPORTED_UNSETTLED:
        return OUTBOX_ID_FACT, "outbox"
    return REQUEST_ID_FACT, "request"


def resolve_cancel_url(story_id: str, listing_id: str, data: Mapping[str, Any]) -> str:
    """Cancel's fallback (after a safe ``next``): the row's anchor on Reconciliation
    (``#outbox-<id>`` for ``imported_unsettled``, ``#request-<id>`` for any other present
    ``listed_reason``), else the bare section."""
    _, prefix = _listed_kind(_shown(data, "listed_reason"))
    section = section_url(story_id)
    return section if prefix is None else f"{section}#{prefix}-{listing_id}"


def resolve_confirm(
    story_id: str, listing_id: str, data: Mapping[str, Any], cancel_url: str
) -> ConfirmPage:
    """The ``inspect:resolve-listing`` confirm page for ``listing_id`` from the GET or POST
    ``data``: the resolution (``?resolution=``, then the hidden field; anything but the two
    blocks the page), the display-only facts and the hidden ``resolution``,
    ``expected_revision`` (D8) and display fields."""
    resolution = chosen_resolution(data)
    provider = _shown(data, "provider")
    value = _shown(data, "value")
    listed = _shown(data, "listed_reason")
    id_label, _ = _listed_kind(listed)
    hidden = {key: data[key] for key in DISPLAY_FIELDS if _shown(data, key) is not None}
    revision = data.get(EXPECTED_REVISION_FIELD)
    if isinstance(revision, str) and revision.strip():
        hidden[EXPECTED_REVISION_FIELD] = revision
    if resolution is not None:
        hidden[RESOLUTION_FIELD] = resolution
    link = provider_url(provider) if provider is not None else None
    return ConfirmPage(
        title=RESOLVE_TITLE,
        question=RESOLVE_QUESTION
        % (RESOLUTION_TEXT[resolution] if resolution is not None else "resolved"),
        emphasis=listing_id,
        confirm_label=(
            RESOLVE_CONFIRM_LABELS[resolution] if resolution is not None else RESOLVE_LABEL
        ),
        cancel_url=cancel_url,
        lines=(
            (RESOLVE_LINES[resolution], RESOLVE_AUDIT_LINE)
            if resolution is not None
            else (RESOLVE_AUDIT_LINE,)
        ),
        facts=(
            ("Story id", story_id),
            (id_label, listing_id),
            (PROVIDER_FACT, provider or EMPTY),
            ("Provider value", value or EMPTY),
            ("Listed as", humanised(listed.strip()) if listed is not None else EMPTY),
        ),
        fact_urls={PROVIDER_FACT: link} if link is not None else {},
        hidden=hidden,
        blocked="" if resolution is not None else RESOLUTION_BLOCKED,
    )


def resolved_url(story_id: str, pk: int) -> str:
    """``inspect:resolved?report=<pk>``."""
    return f"{reverse(RESOLVED, args=[story_id])}?{urlencode({'report': pk})}"


def resolution_text(resolution: str) -> str:
    """ "applied elsewhere" / "abandoned"; an unknown value humanised (E23), "—" when blank."""
    if resolution in RESOLUTION_TEXT:
        return RESOLUTION_TEXT[resolution]
    return humanised(resolution) if resolution.strip() else EMPTY


def resolved_view(
    answer: ListingResolved,
    row: EngineOperation | None,
    story_id: str,
    sent_key: str = "",
) -> SettledView:
    """The resolve result: "Resolved: <listing id> marked <resolution>" (the id in full,
    unlinked: the listing is gone), "Listed as: <reasons, C3-humanised>", D8's Slot, Operation
    and revision lines, and More (the Engine's ``position``, the idempotency key)."""
    back = section_url(story_id)
    slot = slot_result(story_id, answer, row, back)
    pk = _row_pk(row)
    reasons = [humanised(r) for r in answer.reasons if isinstance(r, str) and r.strip()]
    lines = [
        ResultLine(f"Listed as: {', '.join(reasons) if reasons else EMPTY}", None, "listed"),
        ResultLine(f"Slot: {slot.slot_text or EMPTY}", slot.slot_url, "slot"),
    ]
    if slot.operation_id is not None:
        url = reverse("operations:detail", args=[slot.operation_pk]) if slot.operation_pk else None
        lines.append(ResultLine(f"Operation: `{slot.operation_id}`", url, "operation"))
    lines.append(ResultLine(slot.revision_text, None, "revision"))
    key = answer.idempotency_key or (row.idempotency_key if row is not None else "") or sent_key
    listing_id = answer.request_id if answer.request_id.strip() else None
    return SettledView(
        heading="Resolved: " if listing_id is not None else f"Resolved: {EMPTY}",
        heading_after=f" marked {resolution_text(answer.resolution)}",
        heading_outbox_id=listing_id,
        heading_outbox_url=None,
        notes=(),
        lines=tuple(lines),
        reasons=(),
        more=(
            ("Engine position", slot.engine_position or EMPTY),
            ("Idempotency key", key or EMPTY),
        ),
        replayed=row is not None and row.replayed,
        back_url=back,
        back_label=BACK_LABEL,
        report_url=resolved_url(story_id, pk) if pk is not None else None,
        kind=RESOLVE_KIND,
    )


def resolved_row(pk: object, story_id: str) -> tuple[EngineOperation, ListingResolved] | None:
    """The #44 row ``pk`` and its answer when it is an ``ok`` :data:`RESOLVE_KIND` of this story
    whose stored response parses as ``ListingResolved``; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError, TypeError:
        return None
    if (
        row.kind != RESOLVE_KIND
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    try:
        answer = ListingResolved.from_json(row.response)
    except Exception:
        return None
    return None if answer is None else (row, answer)

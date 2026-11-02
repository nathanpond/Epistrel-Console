"""The Effects tab's Reconciliation section (#249; D1, D3, D11, D15, D19).

``inspect:reconciliation`` (``/stories/<id>/inspect/effects/reconciliation/``) renders through
#135's :func:`console.views.inspect.inspect_page` with ``Tab.EFFECTS``. It is never greyed (D1:
the Engine serves the list under every configuration). Each row (:mod:`console.reconciliation`)
is one listed outbox row, ``<tr id="outbox-<outbox_id>">``, the anchor
``console.effects.outbox_url`` links to from every other page.

Reads per render, in order:

1. the shell's effective-config and story reads;
2. exactly one ``GET /stories/{id}/reconciliation``.

The "Settle now" / "Compensate" links are greyed while ``states`` is below ``prompt_only`` (D1's
``module_gate``); the gate's reason shows once above the table, only when a greyed link renders.
Errors stay inside the section (convention 10): 404 the not-found line; 503 ``erasure_pending``
#87's held wrapper (``render_held``); anything else (``module_off`` included: the section is never
greyed) #43's panel. Unreadable listings (D15) show a count notice and a page-level ``<details>``
holding only the rejected entries.

**Settle now** (#256; D8, D9, founder decision 6). ``inspect:settle-now``
(``…/effects/reconciliation/<outbox_id>/settle-now/``) is ``console/confirm.py``'s confirmed-mode
confirm page, outside the Inspect shell; its GET (and any POST without ``confirmed=1``) makes no
Engine call. The URL's outbox id is trimmed and upper-cased (C13); one that fails ``is_ulid`` is
the 404 page :data:`console.reconciliation.MALFORMED_OUTBOX_ID` with no call. The confirmed POST:

1. one ``get_effective_config`` read; a closed ``module_gate(STATES, …)`` (or a failed read:
   ``GATE_UNREAD``) re-renders the page with the reason and the posted key, no write;
2. ``settle_now`` with the posted ``expected_revision`` (D8; none when missing or non-digit),
   waiting ``turn_timeout(None)`` (the client's slot-write rule);
3. the result page (``inspect:settled-now``'s template inside the Effects shell, Reconciliation
   current), built from the answer and the #44 row read right after the call.

A refusal re-renders the confirm page with D9's ``world_refusal`` text ("settle an effect") and
its link (Config, Reconciliation, the paused story's check), a fresh key for an answered refusal
and the posted one for a transport failure or unreadable answer (#43's panel); 503
``erasure_pending`` is #87's held page. ``inspect:settled-now?report=<pk>`` re-renders a result
from its #44 row (no Engine read beyond the shell's).
"""

import dataclasses
import json
from collections.abc import Callable, Mapping
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.confirm import ConfirmPage, confirm_action, render_confirm, safe_next
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import ReconciliationListing, SettledNow, rejected_entries
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.held import is_held, render_held
from console.ids import is_ulid
from console.inspect import STATES, Tab, module_gate
from console.models import EngineOperation
from console.reconciliation import (
    EMPTY_TEXT,
    GATE_ID,
    MALFORMED_OUTBOX_ID,
    NOT_FOUND,
    RESULT_UNAVAILABLE,
    SCOPE_LINE,
    SECTION,
    SECTION_LABEL,
    count_text,
    listing_row,
    section_url,
    settle_confirm,
    settled_row,
    settled_view,
    unreadable_text,
)
from console.views.inspect import InspectContext, inspect_page
from console.world import expected_revision, world_refusal

TEMPLATE = "console/inspect/reconciliation.html"


def _section(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
    context: dict[str, Any] = {"scope_line": SCOPE_LINE, "gate_id": GATE_ID}
    try:
        body = ctx.client.get_reconciliation(ctx.story_id)
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(ctx.request, ctx.story_id, exc)
        log_engine_error(ctx.request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 404:
            context["not_found"] = NOT_FOUND
        else:
            context["reconciliation_error"] = panel_for(exc)
        return context

    raw = body.unparsed.get("listings") if body.unparsed else None
    rejected = rejected_entries(raw, ReconciliationListing.from_json) if raw is not None else []
    gate = module_gate(STATES, ctx.effective)
    on_page = {listing.outbox_id for listing in body.listings}
    rows = [
        listing_row(listing, ctx.story_id, on_page, story=ctx.story, gate=gate)
        for listing in body.listings
    ]
    greyed = any(action.gated for row in rows for action in row.actions)
    context.update(
        reconciliation=body,
        rows=rows,
        count=count_text(len(rows)) if rows else None,
        unreadable=unreadable_text(len(rejected)) if rejected else None,
        rejected=[json.dumps(e, indent=2, ensure_ascii=False, default=str) for e in rejected],
        empty=None if rows or rejected else EMPTY_TEXT,
        actions_gate=gate if greyed else None,
    )
    return context


@require_safe
def reconciliation(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:reconciliation``, every effect the story must reconcile (#249)."""
    return inspect_page(request, story_id, Tab.EFFECTS, TEMPLATE, _section)


# --- settle now (#256) ---------------------------------------------------------------------------

RESULT_TEMPLATE = "console/inspect/reconciliation_result.html"
MISSING_TEMPLATE = "console/inspect/reconciliation_missing.html"
SETTLE_VERB = "settle an effect"
BAD_RESPONSE = "bad_response"
CONFIG_LINK = "Config"
CHECK_LINK = "See the check"
# The link label after a refusal, by code (a ``WorldRefusal.url`` or the no-listing link).
REFUSAL_LINKS: Mapping[str, str] = {
    "module_off": CONFIG_LINK,
    "not_found": SECTION_LABEL,
    "story_paused": CHECK_LINK,
}


def _missing_id(request: HttpRequest, story_id: str) -> HttpResponse:
    """The malformed-id page: status 404, "← Reconciliation", no Engine call."""
    context = {"text": MALFORMED_OUTBOX_ID, "back_url": section_url(story_id)}
    return render(request, MISSING_TEMPLATE, context, status=404)


def _refused(
    request: HttpRequest,
    page: ConfirmPage,
    exc: EngineClientError,
    story_id: str,
    effective: Mapping[str, object] | None,
    key: str,
) -> HttpResponse:
    """A refused settle-now: the confirm page again with the reason (D8's key rule, D9's text),
    or #43's panel above it; never the result page, never a redirect."""
    log_engine_error(request, exc)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )
    refusal = None if unknown else world_refusal(exc, story_id, SETTLE_VERB, effective)
    if refusal is None:
        # The outcome may be unknown: keep the posted key so a resubmit replays (D8). An answered
        # 4xx D9 doesn't word (idempotency_conflict, an unknown code) is a new attempt.
        answered = isinstance(exc, EngineError) and not unknown and exc.http_status < 500
        return render_confirm(
            request,
            page,
            extra={"engine_error": panel_for(exc)},
            idempotency_key=None if answered else key,
        )
    url = refusal.url
    code = exc.code if isinstance(exc, EngineError) else ""
    if not url and code == "not_found":
        url = section_url(story_id)
    error_page = dataclasses.replace(
        page,
        error=refusal.text,
        error_url=url,
        error_link=REFUSAL_LINKS.get(code, CHECK_LINK) if url else "",
    )
    return render_confirm(request, error_page)  # an answered refusal: a fresh key (D8)


def _settled_build(
    answer: SettledNow, row: EngineOperation | None, key: str
) -> Callable[[InspectContext], dict[str, object]]:
    def build(ctx: InspectContext) -> dict[str, object]:
        return {"result": settled_view(answer, row, ctx.story_id, key)}

    return build


@require_http_methods(["GET", "HEAD", "POST"])
def settle_now(request: HttpRequest, story_id: str, outbox_id: str) -> HttpResponse:
    """``inspect:settle-now``: the confirm page, then
    ``POST /stories/{id}/reconciliation/{outbox_id}/settle-now`` and its result page (#256)."""
    outbox_id = outbox_id.strip().upper()
    if not is_ulid(outbox_id):
        return _missing_id(request, story_id)
    data = request.POST if request.method == "POST" else request.GET
    cancel = safe_next(request, f"{section_url(story_id)}#outbox-{outbox_id}")
    page = settle_confirm(story_id, outbox_id, data, cancel)

    def perform(key: str) -> HttpResponse:
        client = engine_client_for(request)
        effective: Mapping[str, object] | None
        try:
            effective = client.get_effective_config(story_id).effective
        except EngineClientError as exc:
            if is_held(exc):
                return render_held(request, story_id, exc)
            log_engine_error(request, exc)
            effective = None
        gate = module_gate(STATES, effective)
        if gate is not None:  # a local refusal: nothing was sent, the posted key is kept
            config = reverse("stories:config", args=[story_id]) if effective is not None else ""
            error_page = dataclasses.replace(
                page,
                error=gate.reason,
                error_url=config,
                error_link=CONFIG_LINK if config else "",
            )
            return render_confirm(request, error_page, idempotency_key=key)
        try:
            answer = client.settle_now(
                story_id,
                outbox_id,
                expected_revision=expected_revision(request.POST),
                idempotency_key=key,
            )
        except EngineClientError as exc:
            if is_held(exc):
                return render_held(request, story_id, exc)
            return _refused(request, page, exc, story_id, effective, key)
        pk = client.last_operation_id
        row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
        return inspect_page(
            request,
            story_id,
            Tab.EFFECTS,
            RESULT_TEMPLATE,
            _settled_build(answer, row, key),
            current=SECTION,
            gate_section=False,
        )

    return confirm_action(request, page, perform, require_confirmed=True)


def _settled_report(ctx: InspectContext) -> dict[str, object]:
    found = settled_row(ctx.request.GET.get("report"), ctx.story_id)
    if found is None:
        return {
            "result_unavailable": RESULT_UNAVAILABLE,
            "section_url": section_url(ctx.story_id),
        }
    row, answer = found
    return {"result": settled_view(answer, row, ctx.story_id, row.idempotency_key)}


@require_safe
def settled_now(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:settled-now?report=<pk>``: a settle-now's result page again, from the #44 row."""
    return inspect_page(
        request,
        story_id,
        Tab.EFFECTS,
        RESULT_TEMPLATE,
        _settled_report,
        current=SECTION,
        gate_section=False,
    )

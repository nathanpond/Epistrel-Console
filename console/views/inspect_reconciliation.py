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
"""

import json
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_safe

from console.engine.client import EngineClientError, EngineError
from console.engine.models import ReconciliationListing, rejected_entries
from console.engine_errors import log_engine_error, panel_for
from console.held import is_held, render_held
from console.inspect import STATES, Tab, module_gate
from console.reconciliation import (
    EMPTY_TEXT,
    GATE_ID,
    NOT_FOUND,
    SCOPE_LINE,
    count_text,
    listing_row,
    unreadable_text,
)
from console.views.inspect import InspectContext, inspect_page

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

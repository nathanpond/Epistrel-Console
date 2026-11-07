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

**Compensate** (#264; D8, D9, founder decision 6). ``inspect:compensate``
(``…/effects/reconciliation/<outbox_id>/compensate/``, GET/HEAD/POST) renders through
``inspect_page`` (Effects, Reconciliation current), so its only reads are the shell's two; a
malformed outbox id is the 404 page above with no call on any method. GET renders
``console.forms.reconciliation.CompensateForm`` (State prefilled from the link's ``state``) with
the link's display facts. POST: bind → ``edit=1`` (the form again, values and the carried key
kept) → invalid (the form with its errors) → a closed ``module_gate(STATES, …)`` (the form with the
reason, no POST, C6) → ``confirm_action(require_confirmed=True)``: an unconfirmed POST is the
confirm page (``console.reconciliation.compensate_confirm``, the values as typed in hidden
fields); a confirmed one re-runs the same checks on the hidden fields and calls ``compensate``
(``expected_revision`` posted, D8). A success is ``reconciliation_result.html`` from
``compensated_view`` (no redirect); ``inspect:compensated?report=<pk>`` re-renders it from the #44
row. Refusals return to the **form** with the values kept, by D8's re-render rule: a
``validation_error`` through ``apply_engine_errors`` with ``COMPENSATE_ERRORS``, anything else
through D9's ``world_refusal`` ("compensate an effect") on its field or above the form (a
``no_listing`` linking Reconciliation), ``None`` #43's panel; 503 ``erasure_pending`` #87's held
page.

**Resolve** (#326; D8, D9, FR-EXT-22). ``inspect:resolve-listing``
(``…/effects/reconciliation/<listing>/resolve/``, E11) is settle-now's confirmed-mode confirm page
for a :func:`console.reconciliation.resolvable` row: ``listing`` is a consumer-applied row's
``request_id`` or an ``imported_unsettled`` row's ``outbox_id`` (C13: trimmed, upper-cased; a
non-ULID is :data:`console.reconciliation.MALFORMED_LISTING_ID`'s 404, no call). The resolution
comes from the link's ``?resolution=`` (the hidden field on POST); anything but the two blocks the
page. Its GET makes no Engine call; the confirmed POST re-checks the gate exactly as settle-now
does (one ``get_effective_config``), then calls ``resolve_listing`` (the posted
``expected_revision``, ``turn_timeout(None)``) and renders the result
(``console.reconciliation.resolved_view``) on the POST response. Refusals re-render the confirm
page through ``world_refusal`` ("resolve an effect") as settle-now's do.
``inspect:resolved?report=<pk>`` re-renders a result from its #44 row.
"""

import dataclasses
import json
from collections.abc import Callable, Mapping
from typing import Any, Literal, cast

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.confirm import (
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
    safe_next,
)
from console.engine.client import EngineClient, EngineClientError, EngineError, EngineUnreachable
from console.engine.models import (
    ListingResolved,
    ReconciliationListing,
    SettledNow,
    rejected_entries,
)
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.reconciliation import (
    CAST_UNREAD,
    COMPENSATE_ERRORS,
    EFFECT_FIELDS,
    CompensateForm,
)
from console.held import is_held, render_held
from console.ids import is_ulid, new_ulid
from console.inspect import STATES, Tab, module_gate
from console.models import EngineOperation
from console.reconciliation import (
    COMPENSATE_RESULT_TITLE,
    EMPTY_TEXT,
    GATE_ID,
    MALFORMED_LISTING_ID,
    MALFORMED_OUTBOX_ID,
    NOT_FOUND,
    RESOLVE_RESULT_TITLE,
    RESOLVE_VERB,
    RESULT_UNAVAILABLE,
    SCOPE_LINE,
    SECTION,
    SECTION_LABEL,
    chosen_resolution,
    compensate_confirm,
    compensate_facts,
    compensate_url,
    compensated_row,
    compensated_view,
    count_text,
    listing_row,
    resolve_cancel_url,
    resolve_confirm,
    resolved_row,
    resolved_view,
    row_url,
    section_url,
    settle_confirm,
    settled_row,
    settled_view,
    unreadable_text,
)
from console.views.inspect import TEMPLATE_KEY, InspectContext, inspect_page
from console.world import (
    COMPENSATE_VERB,
    EXPECTED_REVISION_FIELD,
    expected_revision,
    world_refusal,
)

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
    on_page = {listing.outbox_id for listing in body.listings if listing.outbox_id is not None}
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
IMPORT_LINK = "See the import"
# The link label after a refusal, by code (a ``WorldRefusal.url`` or the no-listing link).
REFUSAL_LINKS: Mapping[str, str] = {
    "module_off": CONFIG_LINK,
    "not_found": SECTION_LABEL,
    "story_paused": CHECK_LINK,
    "story_importing": IMPORT_LINK,  # #326 (E10's import job link)
}


def _missing_id(
    request: HttpRequest, story_id: str, text: str = MALFORMED_OUTBOX_ID
) -> HttpResponse:
    """The malformed-id page: status 404, "← Reconciliation", no Engine call."""
    context = {"text": text, "back_url": section_url(story_id)}
    return render(request, MISSING_TEMPLATE, context, status=404)


def _effective(
    request: HttpRequest, client: EngineClient, story_id: str
) -> Mapping[str, object] | HttpResponse | None:
    """The confirmed POST's one ``get_effective_config`` read: the effective configuration,
    ``None`` when it failed (``GATE_UNREAD``), or #87's held page."""
    try:
        effective: Mapping[str, object] = client.get_effective_config(story_id).effective
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        log_engine_error(request, exc)
        return None
    return effective


def _gate_refused(
    request: HttpRequest,
    page: ConfirmPage,
    story_id: str,
    effective: Mapping[str, object] | None,
    key: str,
) -> HttpResponse | None:
    """A closed ``module_gate(STATES, …)`` (or an unread config): the confirm page again with the
    gate's reason and the posted key, nothing sent (#256's rule); ``None`` when open."""
    gate = module_gate(STATES, effective)
    if gate is None:
        return None
    config = reverse("stories:config", args=[story_id]) if effective is not None else ""
    error_page = dataclasses.replace(
        page,
        error=gate.reason,
        error_url=config,
        error_link=CONFIG_LINK if config else "",
    )
    return render_confirm(request, error_page, idempotency_key=key)


def _refused(
    request: HttpRequest,
    page: ConfirmPage,
    exc: EngineClientError,
    story_id: str,
    effective: Mapping[str, object] | None,
    key: str,
    *,
    verb: str = SETTLE_VERB,
) -> HttpResponse:
    """A refused settle-now (or #326's resolve, ``verb``): the confirm page again with the reason
    (D8's key rule, D9's text), or #43's panel above it; never the result page, never a
    redirect."""
    log_engine_error(request, exc)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )
    refusal = None if unknown else world_refusal(exc, story_id, verb, effective)
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
        effective = _effective(request, client, story_id)
        if isinstance(effective, HttpResponse):
            return effective
        refused = _gate_refused(request, page, story_id, effective, key)
        if refused is not None:  # a local refusal: nothing was sent, the posted key is kept
            return refused
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


# --- compensate (#264) ---------------------------------------------------------------------------

COMPENSATE_TEMPLATE = "console/inspect/compensate.html"
EDIT_FIELD = "edit"


def _form_revision(ctx: InspectContext, data: Mapping[str, Any]) -> str:
    """D8: the shell's ``Story.timeline_revision`` when that read succeeded, else the carried
    value (the link's on GET), else none."""
    revision = ctx.story.timeline_revision if ctx.story is not None else None
    if isinstance(revision, int) and not isinstance(revision, bool):
        return str(revision)
    carried = data.get(EXPECTED_REVISION_FIELD)
    return carried.strip() if isinstance(carried, str) else ""


def _typed(data: Mapping[str, Any]) -> dict[str, str]:
    """The form's values as posted (raw text), for the confirm page's hidden fields."""
    typed: dict[str, str] = {}
    for name in EFFECT_FIELDS:
        value = data.get(name)
        if isinstance(value, str):
            typed[name] = value
    revision = data.get(EXPECTED_REVISION_FIELD)
    if isinstance(revision, str) and revision.strip():
        typed[EXPECTED_REVISION_FIELD] = revision
    return typed


def _compensate_refused(
    ctx: InspectContext,
    exc: EngineClientError,
    form: CompensateForm,
    page: dict[str, Any],
) -> dict[str, Any] | HttpResponse:
    """A refused compensation: the form again with the values kept (D8's key and revision rule),
    the refusal on the field it names or above the form; never the result page."""
    if is_held(exc):
        return render_held(ctx.request, ctx.story_id, exc)
    log_engine_error(ctx.request, exc)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )
    if unknown or not isinstance(exc, EngineError):
        page["write_error"] = panel_for(exc)  # the outcome is unknown: posted key and revision
        return page
    # An answered refusal: a fresh key, the revision from this POST's story read (D8).
    page.update(
        idempotency_key=new_ulid(),
        expected_revision=_form_revision(ctx, ctx.request.POST),
    )
    if exc.code == "validation_error":
        if not apply_engine_errors(exc, form, aliases=COMPENSATE_ERRORS):
            form.add_error(None, exc.message)
        return page
    refusal = world_refusal(exc, ctx.story_id, COMPENSATE_VERB, ctx.effective)
    if refusal is None:
        page["write_error"] = panel_for(exc)
        return page
    url = refusal.url
    if not url and exc.code == "not_found" and refusal.field is None:
        url = section_url(ctx.story_id)
    if refusal.field in form.fields and not url:
        form.add_error(refusal.field, refusal.text)
        return page
    page.update(
        refusal=refusal.text,
        refusal_url=url,
        refusal_link=REFUSAL_LINKS.get(exc.code, CHECK_LINK) if url else "",
    )
    return page


def _compensate(outbox_id: str) -> Callable[[InspectContext], dict[str, Any] | HttpResponse]:
    def build(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
        request = ctx.request
        story_id = ctx.story_id
        posted = request.method == "POST"
        data: Mapping[str, Any] = request.POST if posted else request.GET
        cast = ctx.story.cast if ctx.story is not None else None
        facts = compensate_facts(data, posted=posted)
        form = CompensateForm(
            request.POST if posted else None, cast=cast, state=request.GET.get("state", "")
        )
        gate = module_gate(STATES, ctx.effective)
        page: dict[str, Any] = {
            "outbox_id": outbox_id,
            "facts": facts,
            "form": form,
            "gate": gate,
            "cast_unread": CAST_UNREAD if cast is None else None,
            "action_url": compensate_url(story_id, outbox_id),
            "cancel_url": row_url(story_id, outbox_id),
            "idempotency_key": new_ulid(),  # #43: every render mints a fresh key
            "expected_revision": _form_revision(ctx, data),
        }
        if not posted:
            return page
        # D8: local input (Edit, invalid, a closed gate) keeps the posted key and revision.
        key = posted_idempotency_key(request)
        posted_revision = request.POST.get(EXPECTED_REVISION_FIELD, "")
        page.update(idempotency_key=key, expected_revision=posted_revision.strip())
        valid = form.is_valid()
        if request.POST.get(EDIT_FIELD) == "1":
            page["hide_errors"] = True
            return page
        if not valid:
            return page
        if gate is not None:  # C6: a closed gate refuses locally, no POST
            page.update(
                refusal=gate.reason,
                refusal_url=reverse("stories:config", args=[story_id]),
                refusal_link=CONFIG_LINK,
            )
            return page
        effect = form.effect()
        confirm = compensate_confirm(
            story_id, outbox_id, effect, form.names, facts, _typed(request.POST)
        )
        outcome: dict[str, Any] | HttpResponse | None = None

        def perform(sent_key: str) -> HttpResponse:
            nonlocal outcome
            try:
                answer = ctx.client.compensate(
                    story_id,
                    outbox_id,
                    effect,
                    expected_revision=expected_revision(request.POST),
                    idempotency_key=sent_key,
                )
            except EngineClientError as exc:
                outcome = _compensate_refused(ctx, exc, form, page)
                return HttpResponse(status=204)  # replaced by ``outcome`` below
            pk = getattr(ctx.client, "last_operation_id", None)
            row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
            outcome = {
                TEMPLATE_KEY: RESULT_TEMPLATE,
                "result": compensated_view(answer, row, story_id, sent_key),
                "result_title": COMPENSATE_RESULT_TITLE,
            }
            return HttpResponse(status=204)

        response = confirm_action(request, confirm, perform, require_confirmed=True)
        return response if outcome is None else outcome

    return build


@require_http_methods(["GET", "HEAD", "POST"])
def compensate(request: HttpRequest, story_id: str, outbox_id: str) -> HttpResponse:
    """``inspect:compensate``: the form, its confirm page, then
    ``POST /stories/{id}/reconciliation/{outbox_id}/compensate`` and its result page (#264)."""
    outbox_id = outbox_id.strip().upper()
    if not is_ulid(outbox_id):
        return _missing_id(request, story_id)
    return inspect_page(
        request,
        story_id,
        Tab.EFFECTS,
        COMPENSATE_TEMPLATE,
        _compensate(outbox_id),
        current=SECTION,
    )


def _compensated_report(ctx: InspectContext) -> dict[str, object]:
    found = compensated_row(ctx.request.GET.get("report"), ctx.story_id)
    context: dict[str, object] = {"result_title": COMPENSATE_RESULT_TITLE}
    if found is None:
        context.update(result_unavailable=RESULT_UNAVAILABLE, section_url=section_url(ctx.story_id))
        return context
    row, answer = found
    context["result"] = compensated_view(answer, row, ctx.story_id, row.idempotency_key)
    return context


@require_safe
def compensated(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:compensated?report=<pk>``: a compensation's result page again, from the #44
    row."""
    return inspect_page(
        request,
        story_id,
        Tab.EFFECTS,
        RESULT_TEMPLATE,
        _compensated_report,
        current=SECTION,
        gate_section=False,
    )


# --- resolve (#326) ------------------------------------------------------------------------------


def _resolved_build(
    answer: ListingResolved, row: EngineOperation | None, key: str
) -> Callable[[InspectContext], dict[str, object]]:
    def build(ctx: InspectContext) -> dict[str, object]:
        return {
            "result": resolved_view(answer, row, ctx.story_id, key),
            "result_title": RESOLVE_RESULT_TITLE,
        }

    return build


@require_http_methods(["GET", "HEAD", "POST"])
def resolve_listing(request: HttpRequest, story_id: str, listing: str) -> HttpResponse:
    """``inspect:resolve-listing``: the confirm page, then
    ``POST /stories/{id}/reconciliation/{listing}/resolve {resolution}`` and its result page
    (#326)."""
    listing_id = listing.strip().upper()
    if not is_ulid(listing_id):
        return _missing_id(request, story_id, MALFORMED_LISTING_ID)
    data = request.POST if request.method == "POST" else request.GET
    cancel = safe_next(request, resolve_cancel_url(story_id, listing_id, data))
    page = resolve_confirm(story_id, listing_id, data, cancel)

    def perform(key: str) -> HttpResponse:
        resolution = chosen_resolution(request.POST)
        if resolution is None:  # unreachable while the page is blocked; never send a guess
            return render_confirm(request, page, idempotency_key=key)
        client = engine_client_for(request)
        effective = _effective(request, client, story_id)
        if isinstance(effective, HttpResponse):
            return effective
        refused = _gate_refused(request, page, story_id, effective, key)
        if refused is not None:
            return refused
        try:
            answer = client.resolve_listing(
                story_id,
                listing_id,
                resolution=cast(Literal["applied_elsewhere", "abandoned"], resolution),
                expected_revision=expected_revision(request.POST),
                idempotency_key=key,
            )
        except EngineClientError as exc:
            if is_held(exc):
                return render_held(request, story_id, exc)
            return _refused(request, page, exc, story_id, effective, key, verb=RESOLVE_VERB)
        pk = client.last_operation_id
        row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
        return inspect_page(
            request,
            story_id,
            Tab.EFFECTS,
            RESULT_TEMPLATE,
            _resolved_build(answer, row, key),
            current=SECTION,
            gate_section=False,
        )

    return confirm_action(request, page, perform, require_confirmed=True)


def _resolved_report(ctx: InspectContext) -> dict[str, object]:
    found = resolved_row(ctx.request.GET.get("report"), ctx.story_id)
    context: dict[str, object] = {"result_title": RESOLVE_RESULT_TITLE}
    if found is None:
        context.update(result_unavailable=RESULT_UNAVAILABLE, section_url=section_url(ctx.story_id))
        return context
    row, answer = found
    context["result"] = resolved_view(answer, row, ctx.story_id, row.idempotency_key)
    return context


@require_safe
def resolved(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:resolved?report=<pk>``: a resolve's result page again, from the #44 row."""
    return inspect_page(
        request,
        story_id,
        Tab.EFFECTS,
        RESULT_TEMPLATE,
        _resolved_report,
        current=SECTION,
        gate_section=False,
    )

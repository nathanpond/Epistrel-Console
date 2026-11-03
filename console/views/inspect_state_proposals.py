"""The World tab's Proposed States (#266; K2, Engine FR-STATE-3; D1, D3, D8, D9, D15, D17, D19).

``inspect:state-proposals`` (``/stories/<id>/inspect/world/states/proposals/``, GET/HEAD) is a D3
section gated on ``states`` at ``prompt_only``: through :func:`console.views.inspect.inspect_page`
a closed gate renders the inline greyed panel with no proposals read. Reads per render: the
shell's two, then one ``GET /stories/{id}/state-proposals``. Above the table: the analysis
notice, the **Run an analysis** form (a fresh idempotency key) and C11's count line. Errors stay
in the section (convention 10): a 404 is the not-found line, a 409 ``module_off`` escapes to the
shell's greyed panel, a 503 ``erasure_pending`` to #87's held wrapper, anything else is #43's
panel under the button. Rejected entries (D15's ``rejected_entries``) are one notice with the
raw entries in ``<details>``.

``inspect:state-proposals-analyze`` (POST only; ``@require_POST`` sits before the shell, so any
other method is 405 with no read) goes through the shell's two reads, checks ``module_gate`` (a
closed or unread gate: an error flash of its reason, zero ``POST …/analyze``), then calls
``analyze_state_proposals`` once with the page's key and redirects to the section (PRG). The
flash says the job was queued and asks for a reload: the Engine serves no read of the job (D18),
so the Console never says an analysis finished, and the #44 row stays ``queued``. A replay (the
#44 row says ``replayed``) reads "Already queued". A refusal is D9's text as an error flash;
anything else renders the section with #43's panel (no redirect).

``inspect:state-proposal-approve`` (GET/HEAD/POST) is a form page below the section: the gate
first (closed: the inline panel, no proposals read, no approve), then one proposals read to find
the proposal. A missing or not-``proposed`` one (an id found only among rejected entries too)
shows D9's ``no_pending_proposal`` sentence and no form. :class:`StateOverridesForm` starts from
the suggestion; a valid POST makes one ``approve_state_proposal`` with its ``overrides()``
(``None`` untouched: ``{}``), D8's ``expected_revision`` (the shell's ``Story.timeline_revision``:
the proposals read carries no revision) and the page's key. Refusals go through #261's
:func:`declaration_refusal`; the result is #261's ``state_declared.html`` with this page's
heading, proposal lines and links, rendered on the POST response and again at
``inspect:state-proposal-approved?report=<pk>`` from the #44 row.

``inspect:state-proposal-dismiss`` (GET/HEAD/POST) is #43's confirm page in #134's confirmed
mode. The GET makes no Engine call; the confirmed POST reads the effective configuration (its only
call besides the write), gates on ``states``, then calls ``dismiss_state_proposal`` once and
redirects to the section with a success flash. No dismiss path sends an expected revision.
"""

import dataclasses
from collections.abc import Mapping
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from console.canon import cast_names
from console.confirm import (
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
    safe_next,
    shown_name,
)
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import ProposalApproved, StateProposal, StateProposals, rejected_entries
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.forms.states import STATE_NAME, StateOverridesForm
from console.held import is_held, render_held
from console.ids import new_ulid
from console.inspect import STATES, Tab, is_module_off, module_gate
from console.models import EngineOperation
from console.state_proposals import (
    ANALYSIS_NOTICE,
    APPROVE_HEADING,
    APPROVED_HEADING,
    APPROVED_PROPOSAL_LINE,
    DECLARED_AS_LINE,
    DISMISS_QUESTION,
    DISMISS_TITLE,
    DISMISSED_TEXT,
    EMPTY_TEXT,
    PROPOSED,
    QUEUED_TEXT,
    REPLAYED_TEXT,
    UNCHANGED_NOTE,
    UNPARSED_TEXT,
    analyze_url,
    approve_url,
    approved_url,
    count_text,
    proposal_row,
    proposal_rows,
    section_url,
)
from console.states import set_url, state_url
from console.views.inspect import InspectContext, inspect_page
from console.views.inspect_state_declare import (
    DECLARED_TEMPLATE,
    REFUSAL_LINKS,
    STORY_OWNER,
    declaration_refusal,
    declared_context,
)
from console.wording import humanised
from console.world import NO_PENDING_PROPOSAL_TEXT, expected_revision, world_refusal

SECTION_TEMPLATE = "console/inspect/state_proposals.html"
APPROVE_TEMPLATE = "console/inspect/state_proposal_approve.html"
SECTION = "inspect:state-proposals"
ANALYZE_KIND = "analyze_state_proposals"
APPROVE_KIND = "approve_state_proposal"
REPORT_PARAM = "report"
ANALYZE_VERB = "analyze the story for States"
APPROVE_VERB = "approve a proposed State"
DISMISS_VERB = "dismiss a proposed State"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
BAD_RESPONSE = "bad_response"
NO_PENDING = "no_pending_proposal"
BACK_LABEL = "Back to Proposed States"
APPROVED_MISSING_TITLE = "Approved a proposed State"


def _names(ctx: InspectContext) -> dict[str, str]:
    return cast_names(ctx.story.cast if ctx.story is not None else None)


def _story_revision(ctx: InspectContext) -> int | None:
    revision = ctx.story.timeline_revision if ctx.story is not None else None
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else None


def _rejected(body: StateProposals) -> list[Any]:
    raw = body.unparsed.get("proposals") if body.unparsed else None
    return rejected_entries(raw, StateProposal.from_json) if raw is not None else []


def _read(ctx: InspectContext) -> StateProposals | dict[str, Any]:
    """One ``list_state_proposals``: the body, or convention 10's in-section error context (held
    and ``module_off`` re-raised to the middleware and the shell)."""
    try:
        return ctx.client.list_state_proposals(ctx.story_id)
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(ctx.request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 404:
            return {"not_found": NOT_FOUND}
        return {"proposals_error": panel_for(exc)}


# --- the section ------------------------------------------------------------------------------


def _section(ctx: InspectContext) -> dict[str, Any]:
    story_id = ctx.story_id
    context: dict[str, Any] = {
        "notice": ANALYSIS_NOTICE,
        "analyze_url": analyze_url(story_id),
        "idempotency_key": new_ulid(),
    }
    body = _read(ctx)
    if not isinstance(body, StateProposals):
        context.update(body)
        return context
    rejected = _rejected(body)
    rows = proposal_rows(body.proposals, story_id, _names(ctx))
    context.update(
        count=count_text(body.proposals) if body.proposals else None,
        rows=rows,
        rejected=rejected,
        unparsed_text=UNPARSED_TEXT if rejected else None,
        empty=None if body.proposals or rejected else EMPTY_TEXT,
    )
    return context


@require_safe
def state_proposals(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:state-proposals``: the Engine's proposed States (#266)."""
    return inspect_page(request, story_id, Tab.WORLD, SECTION_TEMPLATE, _section)


# --- run an analysis --------------------------------------------------------------------------


def _analysis_row(ctx: InspectContext, key: str) -> EngineOperation | None:
    """The #44 row the call just wrote, else this story's latest analysis row under ``key``."""
    pk = getattr(ctx.client, "last_operation_id", None)
    row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
    if row is not None:
        return row
    return (
        EngineOperation.objects.filter(
            kind=ANALYZE_KIND, story_id=ctx.story_id, idempotency_key=key
        )
        .order_by("-created_at", "-id")
        .first()
    )


def _analyze(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    story_id = ctx.story_id
    back = section_url(story_id)
    closed = module_gate(STATES, ctx.effective)
    if closed is not None:
        messages.error(request, closed.reason)
        return redirect(back)
    key = posted_idempotency_key(request)
    try:
        queued = ctx.client.analyze_state_proposals(story_id, idempotency_key=key)
    except EngineClientError as exc:
        if is_held(exc):
            raise
        log_engine_error(request, exc)
        unknown = isinstance(exc, EngineUnreachable) or (
            isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
        )
        refusal = None if unknown else world_refusal(exc, story_id, ANALYZE_VERB, ctx.effective)
        if refusal is not None:
            messages.error(request, refusal.text)  # the Config link dropped (C5's flash rule)
            return redirect(back)
        context = _section(ctx)
        context["analyze_error"] = panel_for(exc)
        return context
    row = _analysis_row(ctx, key)
    if row is not None and row.replayed:
        messages.info(request, REPLAYED_TEXT.replace("{job_id}", queued.job_id))
    else:
        before, _, after = QUEUED_TEXT.partition("{job_id}")
        text = format_html("{}<code>{}</code>{}", before, queued.job_id, after)
        messages.success(request, text, extra_tags="safe")
    return redirect(back)


@require_POST
def state_proposals_analyze(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:state-proposals-analyze``: ``POST /stories/{id}/state-proposals/analyze``, then
    the section with a queued flash (#266)."""
    return inspect_page(
        request,
        story_id,
        Tab.WORLD,
        SECTION_TEMPLATE,
        _analyze,
        current=SECTION,
        gate_section=False,
    )


# --- approve ----------------------------------------------------------------------------------


def _find(body: StateProposals, proposal_id: str) -> tuple[StateProposal | None, list[Any]]:
    """The parsed proposal ``proposal_id``, else ``None`` with any rejected raw entry of that id."""
    for proposal in body.proposals:
        if proposal.id == proposal_id:
            return proposal, []
    raw = [
        entry
        for entry in _rejected(body)
        if isinstance(entry, Mapping) and entry.get("id") == proposal_id
    ]
    return None, raw


def approved_context(
    ctx: InspectContext, answer: ProposalApproved, row: EngineOperation | None, raw: Any
) -> dict[str, Any]:
    """#261's result page for an approval: its heading, proposal lines and links."""
    story_id = ctx.story_id
    proposal = answer.proposal
    context = declared_context(ctx, answer, row, posted_name=proposal.name, raw=raw)
    stored = answer.definition.get("name")
    name = stored if isinstance(stored, str) and stored else proposal.name
    back = section_url(story_id)
    links: list[tuple[str, str]] = []
    if STATE_NAME.fullmatch(name):
        links.append((f"See {name}", state_url(story_id, name)))
        if answer.definition.get("owner", STORY_OWNER) == STORY_OWNER:
            links.append(("Set its value", set_url(story_id, name)))
    links.append((BACK_LABEL, back))
    status = humanised(proposal.status) if proposal.status else "—"
    context.update(
        state_name=name,
        heading=APPROVED_HEADING.replace("{name}", proposal.name),
        proposal_lines=[
            APPROVED_PROPOSAL_LINE.replace("{id}", proposal.id).replace("{status}", status),
            DECLARED_AS_LINE.replace("{name}", name),
        ],
        links=links,
        back_url=back,
        slot=dataclasses.replace(context["slot"], back_url=back),
        report_url=approved_url(story_id, row.pk) if row is not None else None,
    )
    return context


def _approve(proposal_id: str) -> Any:
    def build(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
        request = ctx.request
        story_id = ctx.story_id
        back = section_url(story_id)
        page: dict[str, Any] = {
            "back_url": back,
            "back_label": BACK_LABEL,
            "action_url": approve_url(story_id, proposal_id),
            "proposal_id": proposal_id,
            "unchanged_note": UNCHANGED_NOTE,
        }
        closed = module_gate(STATES, ctx.effective)
        if closed is not None:
            page["gate_unavailable"] = closed
            return page
        body = _read(ctx)
        if not isinstance(body, StateProposals):
            page.update(body)
            return page
        proposal, raw = _find(body, proposal_id)
        if proposal is None or proposal.status != PROPOSED:
            page.update(not_pending=NO_PENDING_PROPOSAL_TEXT, rejected=raw)
            if proposal is not None:
                page["heading"] = APPROVE_HEADING.replace("{name}", proposal.name)
            return page
        names = _names(ctx)
        page.update(
            heading=APPROVE_HEADING.replace("{name}", proposal.name),
            row=proposal_row(proposal, story_id, names),
        )
        if request.method != "POST":
            page["form"] = StateOverridesForm(
                suggestion=proposal, initial_revision=_story_revision(ctx), names=names
            )
            return page
        form = StateOverridesForm(
            request.POST,
            suggestion=proposal,
            initial_revision=_story_revision(ctx),
            names=names,
        )
        page["form"] = form
        if not form.is_valid():
            return page
        try:
            answer = ctx.client.approve_state_proposal(
                story_id,
                proposal_id,
                definition=form.overrides(),
                expected_revision=expected_revision(request.POST),
                idempotency_key=posted_idempotency_key(request),
            )
        except EngineClientError as exc:
            refused = declaration_refusal(ctx, exc, form, APPROVE_VERB)
            if isinstance(refused, HttpResponse):
                return refused
            page.update(refused)
            if (
                isinstance(exc, EngineError)
                and exc.code == "not_found"
                and exc.details.get("reason") == NO_PENDING
            ):
                page["form_hidden"] = True
            return page
        pk = getattr(ctx.client, "last_operation_id", None)
        row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
        raw_answer = row.response if row is not None else dataclasses.asdict(answer)
        return approved_context(ctx, answer, row, raw_answer)

    return build


@require_http_methods(["GET", "HEAD", "POST"])
def state_proposal_approve(request: HttpRequest, story_id: str, proposal_id: str) -> HttpResponse:
    """``inspect:state-proposal-approve``: the approve form, then ``POST
    /stories/{id}/state-proposals/{pid}/approve`` and the result on the POST response (#266)."""
    return inspect_page(
        request,
        story_id,
        Tab.WORLD,
        APPROVE_TEMPLATE,
        _approve(proposal_id),
        current=SECTION,
        gate_section=False,
    )


def approved_row(pk: object, story_id: str) -> tuple[EngineOperation, ProposalApproved] | None:
    """The #44 row ``pk`` and its answer when it is an ``ok`` ``approve_state_proposal`` of this
    story whose stored response parses; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    if (
        row.kind != APPROVE_KIND
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    parsed = ProposalApproved.from_json(row.response)
    return None if parsed is None else (row, parsed)


def _approved_report(ctx: InspectContext) -> dict[str, Any]:
    found = approved_row(ctx.request.GET.get(REPORT_PARAM), ctx.story_id)
    if found is None:
        return {
            "missing": NOT_FOUND,
            "missing_title": APPROVED_MISSING_TITLE,
            "back_url": section_url(ctx.story_id),
            "back_label": BACK_LABEL,
        }
    row, answer = found
    return approved_context(ctx, answer, row, row.response)


@require_safe
def state_proposal_approved(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:state-proposal-approved?report=<pk>``: an approval's result page again."""
    return inspect_page(
        request,
        story_id,
        Tab.WORLD,
        DECLARED_TEMPLATE,
        _approved_report,
        current=SECTION,
        gate_section=False,
    )


# --- dismiss ----------------------------------------------------------------------------------


def dismiss_confirm(story_id: str, proposal_id: str, name: str, cancel_url: str) -> ConfirmPage:
    shown = name or proposal_id
    return ConfirmPage(
        title=DISMISS_TITLE,
        question=DISMISS_QUESTION,
        emphasis=shown,
        confirm_label="Dismiss",
        cancel_url=cancel_url,
        hidden={"name": name},
        danger=True,
    )


def _dismiss_refused(
    request: HttpRequest,
    page: ConfirmPage,
    exc: EngineClientError,
    story_id: str,
    effective: Mapping[str, Any] | None,
    key: str,
) -> HttpResponse:
    log_engine_error(request, exc)
    back = section_url(story_id)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )
    if unknown or not isinstance(exc, EngineError):
        return render_confirm(
            request, page, extra={"engine_error": panel_for(exc)}, idempotency_key=key
        )
    if exc.http_status == 404:
        if exc.details.get("reason") == NO_PENDING:
            messages.warning(request, NO_PENDING_PROPOSAL_TEXT)
            return redirect(back)
        return render_confirm(request, dataclasses.replace(page, error=NOT_FOUND))
    refusal = world_refusal(exc, story_id, DISMISS_VERB, effective)
    if refusal is not None:
        messages.error(request, refusal.text)
        return redirect(back)
    return render_confirm(
        request, page, extra={"engine_error": panel_for(exc)}, idempotency_key=key
    )


@require_http_methods(["GET", "HEAD", "POST"])
def state_proposal_dismiss(request: HttpRequest, story_id: str, proposal_id: str) -> HttpResponse:
    """``inspect:state-proposal-dismiss``: the confirmed-mode confirm page, then ``POST
    /stories/{id}/state-proposals/{pid}/dismiss`` and a redirect to the section (#266)."""
    name = shown_name(request)
    back = section_url(story_id)
    page = dismiss_confirm(story_id, proposal_id, name, safe_next(request, back))

    def perform(key: str) -> HttpResponse:
        client = engine_client_for(request)
        effective: Mapping[str, Any] | None
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
                error_link=REFUSAL_LINKS["module_off"] if config else "",
            )
            return render_confirm(request, error_page, idempotency_key=key)
        try:
            answer = client.dismiss_state_proposal(story_id, proposal_id, idempotency_key=key)
        except EngineClientError as exc:
            if is_held(exc):
                return render_held(request, story_id, exc)
            return _dismiss_refused(request, page, exc, story_id, effective, key)
        shown = answer.proposal.name or name or proposal_id
        messages.success(request, DISMISSED_TEXT.replace("{name}", shown))
        return redirect(back)

    return confirm_action(request, page, perform, require_confirmed=True)

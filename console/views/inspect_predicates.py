"""The Predicates tab, and approving or rejecting a strengthening proposal (#136).

``inspect:predicates`` (``/stories/<id>/inspect/predicates/``) is the story's predicate registry
from one ``GET /stories/{id}/predicates`` read on top of #135's shell: one row per predicate in
the Engine's order (anchor ``p-<id>``), the story's implication rules, and a header with the
configuration revision and the pending proposals. ``?layer=core|author|auto`` re-renders the
same read with only that layer's rows (any other value is ``all``); the rules table and the
header are never filtered. Rows come from :mod:`console.predicates`.

A row whose ``pending_proposal`` is ``pending`` gets a sub-row with the proposed changes and
three controls, each carrying its own idempotency key minted when the tab rendered, the
predicate's ``name``, the proposal's fields (``offered``, in its order) and ``layer``, all
display-only:

- **Approve all** and **Reject**: links to ``inspect:predicate-approve`` /
  ``inspect:predicate-reject``;
- **Approve selected** (two or more fields): a GET form to the approve URL adding
  ``mode=selected`` and the ticked ``field`` values.

Each lands on #43's confirm page in #134's confirmed mode (no Engine call on GET; a POST without
``confirmed=1`` re-renders it with no call). In selected mode the page renders one checkbox per
``offered`` field, so its POST carries the final choice: the ticked fields that are among the
posted ``offered`` ones, in ``offered`` order (a forged list meets the Engine's 422
``not_proposed``); none ticked re-renders the page with "Choose at least one field to approve"
and no call. Approve all sends ``{}``, approve selected ``{"changes": [...]}``, reject no body.

The decision renders its result page on the POST response (#135 convention 8), reachable again
at ``inspect:predicate-decision?report=<pk>``, rebuilt from the #44 row's stored response (no
Engine read either way). It renders outside the tab shell, so it needs no effective-config or
story read; its row names only the decided predicate (other ids are short ids linked to the
unfiltered tab).

Refusals: 404 ``no_pending_proposal`` redirects to the tab with a warning flash; another 404 is
convention 10's not-found text; a 422 re-renders the confirm page with the Engine's message (and
its ``details.fields``); 503 ``erasure_pending`` is #87's held wrapper; 409 ``story_archived``,
``revision_conflict``, ``idempotency_conflict``, 423 ``story_busy`` and other 503s escape to
#43's panel.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.confirm import (
    CONFIRMED_FIELD,
    KEY_FIELD,
    ConfirmPage,
    carried_idempotency_key,
    confirm_action,
    render_confirm,
    shown_name,
)
from console.engine.client import EngineClientError, EngineError
from console.engine.models import Predicate, PredicateDecision
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.held import is_held, render_held
from console.ids import new_ulid
from console.inspect import Tab, is_module_off, short_id
from console.models import EngineOperation
from console.predicates import (
    ALL,
    LAYERS,
    PENDING,
    PredicateRow,
    ProposalView,
    Refs,
    ValueCell,
    layer_filter,
    predicate_row,
    row_url,
    rule_row,
    tab_url,
    value_cell,
)
from console.views.inspect import InspectContext, inspect_page

PREDICATES_TEMPLATE = "console/inspect/predicates.html"
DECISION_TEMPLATE = "console/inspect/predicate_decision.html"
MISSING_TEMPLATE = "console/inspect/predicate_missing.html"
FIELDS_TEMPLATE = "console/inspect/_predicate_fields.html"
ACTIONS_TEMPLATE = "console/inspect/_proposal_actions.html"

APPROVE = "approve_predicate"
REJECT = "reject_predicate"
DECISION_KINDS = (APPROVE, REJECT)
SELECTED = "selected"
REPORT_PARAM = "report"
NO_PENDING_PROPOSAL = "no_pending_proposal"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
NO_LONGER_PENDING = "That proposal is no longer pending — the registry has been reloaded"
CHOOSE_A_FIELD = "Choose at least one field to approve"
UNTICKED_DROPPED = "Fields you leave unticked are dropped: the proposal closes."
REVISION_LINE = (
    "The story's configuration revision moves by one; existing propositions it would flag are "
    "listed, never changed."
)
REJECT_QUESTION = (
    "Reject the proposal for {name}? Nothing changes; the Engine won't file the same proposal "
    "again."
)


# --- the tab -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalControls:
    """A pending proposal's controls (``_proposal_actions.html``): the two links and the
    approve-selected GET form, each with its own key minted at this render."""

    approve_all_url: str
    reject_url: str
    selected_url: str | None  # the form's action; ``None`` for a one-field proposal
    selected_hidden: tuple[tuple[str, str], ...]
    offered: tuple[str, ...]


@dataclass(frozen=True)
class TabRow:
    row: PredicateRow
    controls: ProposalControls | None


def _query(params: Sequence[tuple[str, str]]) -> str:
    return urlencode(list(params))


def proposal_controls(
    story_id: str, predicate: Predicate, proposal: ProposalView, layer: str
) -> ProposalControls:
    """The controls of ``proposal``'s sub-row (see the module docstring)."""
    offered = tuple(proposal.offered)
    carried = [("name", predicate.name), *(("offered", f) for f in offered), ("layer", layer)]
    approve = reverse("inspect:predicate-approve", args=[story_id, predicate.id])
    reject = reverse("inspect:predicate-reject", args=[story_id, predicate.id])
    selected = (
        (*carried, ("mode", SELECTED), (KEY_FIELD, new_ulid())) if proposal.selectable else ()
    )
    return ProposalControls(
        approve_all_url=f"{approve}?{_query([*carried, (KEY_FIELD, new_ulid())])}",
        reject_url=f"{reject}?{_query([*carried, (KEY_FIELD, new_ulid())])}",
        selected_url=approve if proposal.selectable else None,
        selected_hidden=tuple(selected),
        offered=offered,
    )


def pending_text(count: int) -> str:
    if count == 0:
        return "No pending proposals"
    return f"{count} pending proposal{'' if count == 1 else 's'}"


def _predicates(ctx: InspectContext) -> dict[str, Any]:
    """The tab's one ``GET /stories/{id}/predicates`` read and its rows. A failed read renders
    convention 10's line (404) or #43's panel in place of the tab content, with no rows;
    ``erasure_pending`` (and a ``module_off`` the route doesn't declare) escapes to the shell."""
    layer = layer_filter(ctx.request.GET.get("layer"))
    context: dict[str, Any] = {"layer": layer, "layers": (ALL, *LAYERS)}
    try:
        registry = ctx.client.list_predicates(ctx.story_id)
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(ctx.request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 404:
            context["predicates_not_found"] = True
        else:
            context["predicates_error"] = panel_for(exc)
        return context
    shown = [p for p in registry.predicates if layer == ALL or p.layer == layer]
    refs = Refs(ctx.story_id, registry.names(), on_page={p.id for p in shown})
    rows = []
    for predicate in shown:
        row = predicate_row(predicate, refs)
        controls = (
            proposal_controls(ctx.story_id, predicate, row.proposal, layer)
            if row.proposal is not None
            else None
        )
        rows.append(TabRow(row, controls))
    pending = [
        p
        for p in registry.predicates
        if p.pending_proposal is not None and p.pending_proposal.status == PENDING
    ]
    context.update(
        registry=registry,
        rows=rows,
        rules=[rule_row(r, refs) for r in registry.rules],
        pending_text=pending_text(len(pending)),
        pending_url=row_url(ctx.story_id, pending[0].id) if pending else None,
        actions=ACTIONS_TEMPLATE,  # convention 14: the proposal controls
        filter_url=tab_url(ctx.story_id),
    )
    return context


@require_safe
def predicates(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:predicates``, the story's predicate registry (#136)."""
    return inspect_page(request, story_id, Tab.PREDICATES, PREDICATES_TEMPLATE, _predicates)


# --- the confirm step and the call ---------------------------------------------------------------


def offered_fields(data: QueryDict) -> list[str]:
    """The ``offered`` fields carried (display-only), in order, blanks and repeats dropped."""
    seen: list[str] = []
    for value in data.getlist("offered"):
        if value and value not in seen:
            seen.append(value)
    return seen


def decision_confirm(
    action: str,
    name: str,
    predicate_id: str,
    story_id: str,
    offered: Sequence[str],
    *,
    selected: bool,
    cancel_url: str,
    hidden: Mapping[str, str],
) -> ConfirmPage:
    """The confirm page (#134's confirmed mode) for approving (all, or the ``selected``
    fields) or rejecting ``name``'s proposal; ``offered`` are the proposal's fields as carried
    (display-only)."""
    changes = ", ".join(offered) or "—"
    lines: tuple[str, ...] = ()
    label, title = "Approve", "Approve proposal"
    if action == REJECT:
        question, label, title = REJECT_QUESTION, "Reject", "Reject proposal"
    elif selected:
        question = (
            f"Approve the ticked fields of the proposal for {{name}}? It proposes: {changes}. "
            f"{REVISION_LINE}"
        )
        lines = (UNTICKED_DROPPED,)
    else:
        question = (
            f"Approve the whole proposal for {{name}}? It changes: {changes}. {REVISION_LINE}"
        )
    return ConfirmPage(
        title=title,
        question=question,
        emphasis=name,
        confirm_label=label,
        cancel_url=cancel_url,
        lines=lines,
        facts=(("Predicate id", predicate_id), ("Story id", story_id)),
        hidden={**hidden, CONFIRMED_FIELD: "1"},
        fields_template=FIELDS_TEMPLATE,
    )


def engine_message(exc: EngineError) -> str:
    """A 422's text for the confirm page: the Engine's message, plus "Fields: a, b" when
    ``details.fields`` names them."""
    named = exc.details.get("fields")
    fields = [str(f) for f in named] if isinstance(named, list) else []
    return f"{exc.message} Fields: {', '.join(fields)}" if fields else exc.message


def _missing(request: HttpRequest, story_id: str, back: str) -> HttpResponse:
    """Convention 10's not-found text on #43's shared error page, with "← Predicates"."""
    return render(
        request, MISSING_TEMPLATE, {"story_id": story_id, "back_url": back, "text": NOT_FOUND}
    )


def _decide(request: HttpRequest, story_id: str, predicate_id: str, action: str) -> HttpResponse:
    """The confirm page (GET; POST without ``confirmed=1``) and the confirmed call."""
    data = request.POST if request.method == "POST" else request.GET
    name = shown_name(request) or short_id(predicate_id)
    offered = offered_fields(data)
    layer = layer_filter(data.get("layer"))
    selected = action == APPROVE and data.get("mode") == SELECTED
    back = tab_url(story_id, layer)
    hidden = {"name": name, "layer": layer}
    if selected:
        hidden["mode"] = SELECTED
    page = decision_confirm(
        action,
        name,
        predicate_id,
        story_id,
        offered,
        selected=selected,
        cancel_url=back,
        hidden=hidden,
    )
    ticked = set(data.getlist("field"))
    extra = {"offered": offered, "selected": selected, "ticked": ticked}
    if request.method != "POST":
        return render_confirm(
            request, page, extra=extra, idempotency_key=carried_idempotency_key(request.GET)
        )

    def perform(key: str) -> HttpResponse:
        changes: list[str] | None = None
        if selected:
            changes = [f for f in offered if f in ticked]
            if not changes:
                error_page = dataclasses.replace(page, error=CHOOSE_A_FIELD)
                return render_confirm(request, error_page, extra=extra, idempotency_key=key)
        client = engine_client_for(request)
        try:
            if action == APPROVE:
                decision = client.approve_predicate(
                    story_id, predicate_id, changes, idempotency_key=key
                )
            else:
                decision = client.reject_predicate(story_id, predicate_id, idempotency_key=key)
        except EngineClientError as exc:
            if is_held(exc):
                return render_held(request, story_id, exc)
            if not isinstance(exc, EngineError) or exc.http_status not in (404, 422):
                raise
            log_engine_error(request, exc)
            if exc.http_status == 404 and exc.details.get("reason") == NO_PENDING_PROPOSAL:
                messages.warning(request, NO_LONGER_PENDING)
                return redirect(back)
            if exc.http_status == 404:
                return _missing(request, story_id, back)
            # The operator may change the ticks: a fresh key for the next attempt.
            error_page = dataclasses.replace(page, error=engine_message(exc))
            return render_confirm(request, error_page, extra=extra)
        pk = client.last_operation_id
        row = EngineOperation.objects.filter(pk=pk).first() if pk is not None else None
        replayed = row is not None and row.replayed
        return render_decision(request, story_id, decision, action, replayed, back, pk)

    return confirm_action(request, page, perform, extra=extra, require_confirmed=True)


@require_http_methods(["GET", "HEAD", "POST"])
def approve(request: HttpRequest, story_id: str, predicate_id: str) -> HttpResponse:
    """``inspect:predicate-approve``: the confirm page, then
    ``POST /stories/{id}/predicates/{pid}/approve``."""
    return _decide(request, story_id, predicate_id, APPROVE)


@require_http_methods(["GET", "HEAD", "POST"])
def reject(request: HttpRequest, story_id: str, predicate_id: str) -> HttpResponse:
    """``inspect:predicate-reject``: the confirm page, then
    ``POST /stories/{id}/predicates/{pid}/reject``."""
    return _decide(request, story_id, predicate_id, REJECT)


# --- the result page -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AppliedRow:
    field: str
    value: ValueCell


def render_decision(
    request: HttpRequest,
    story_id: str,
    decision: PredicateDecision,
    kind: str,
    replayed: bool,
    back: str,
    pk: int | None,
) -> HttpResponse:
    """The decision's result page (no Engine read): the status, what was applied, the
    predicate as it now is (its row names only itself), the flagged propositions and the
    configuration revision (``M-1 → M`` on an approval)."""
    predicate = decision.predicate
    refs = Refs(story_id, {predicate.id: predicate.name})
    context = {
        "story_id": story_id,
        "decision": decision,
        "approved": kind == APPROVE,
        "replayed": replayed,
        "back_url": back,
        "report_url": report_url(story_id, pk) if pk is not None else None,
        "applied": [AppliedRow(f, value_cell(f, v, refs)) for f, v in decision.applied.items()],
        "row": predicate_row(predicate, refs),
        "actions": None,
        "revision_before": decision.config_revision - 1,
        "more": {
            "proposal_id": decision.proposal_id,
            "timeline_revision": decision.timeline_revision,
            "idempotency_key": decision.idempotency_key,
        },
    }
    return render(request, DECISION_TEMPLATE, context)


def report_url(story_id: str, pk: int) -> str:
    """Where the result page is reachable again: ``inspect:predicate-decision?report=<pk>``."""
    url = reverse("inspect:predicate-decision", args=[story_id])
    return f"{url}?{urlencode({REPORT_PARAM: pk})}"


def decision_row(pk: object, story_id: str) -> tuple[EngineOperation, PredicateDecision] | None:
    """The #44 row ``pk`` and its decision when it is an ``ok`` approve or reject of this
    story whose stored response parses; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    if (
        row.kind not in DECISION_KINDS
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    decision = PredicateDecision.from_json(row.response)
    return None if decision is None else (row, decision)


@require_safe
def decision_report(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:predicate-decision?report=<pk>``: a decision's result page again, from the #44
    row's stored response (no Engine call); anything else is convention 10's not-found text."""
    back = tab_url(story_id)
    found = decision_row(request.GET.get(REPORT_PARAM), story_id)
    if found is None:
        return _missing(request, story_id, back)
    row, body = found
    return render_decision(request, story_id, body, row.kind, row.replayed, back, row.pk)

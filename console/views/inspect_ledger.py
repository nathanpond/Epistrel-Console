"""The Ledger tab (#148): a story's commitments, its thunks and the promissory ledger.

``inspect:ledger`` (``/stories/<id>/inspect/ledger/``) asks ``GET
/stories/{id}/inspect/ledger`` for one page (``viewpoint=omniscient``, ``limit=100``), with the
GET filters of :class:`console.forms.inspect.LedgerForm`: ``state`` (any, open, resolved,
expired) and ``discharged`` (any, discharged, undischarged), each left out for "any". The page
cursor ``after_id`` is passed through as given (a malformed one is the Engine's 422, shown above
the filters with "First page"). An unknown filter value is an error beside its select, and no
ledger call is made.

Reads per render, sequential: #135's effective config and story (cast names), the ledger read,
then (once it answered) convention 2's one registry read (:func:`console.predicates.
predicate_names`, ``{}`` on failure: short ids) and one ``operation_links`` over the page's
discharge ids (Console rows; no Engine call). The first page's hidden facts (#208) come from the
same ledger read, whatever the filters; the registry names and cast names feed their
propositions (``predicate_names``, ``cast_names`` in the context). Full page render on every
GET; a rewind needs no stale cue, as each GET is a fresh read. Rows: :mod:`console.ledger`.

Errors: 409 ``module_off`` escapes to the shell's greyed panel naming ``commitment``
(convention 7); 422 lands beside the select ``details.field`` names, else above the filters;
``viewpoint_unresolved`` and 403 ``omniscient_required`` read "This read needs the omniscient
viewpoint"; 404 is convention 10's not-found line; 503 ``erasure_pending`` is #87's held
wrapper; anything else (``database_unavailable``, ``ledger_unavailable``, unreachable) is #43's
panel below the filters. The counts and the revision render only with an answer.
"""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_safe

from console.canon import cast_names
from console.engine.client import EngineClientError, EngineError
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import OMNISCIENT_TEXT, LedgerForm
from console.held import is_held
from console.history import operation_links
from console.inspect import Tab, is_module_off
from console.ledger import discharge_ids, empty_state, ledger_view, paging
from console.predicates import Refs, predicate_names
from console.views.inspect import OMNISCIENT_REQUIRED, InspectContext, inspect_page

LEDGER_TEMPLATE = "console/inspect/ledger.html"
PAGE_LIMIT = 100  # convention 6 (the route allows 500)
CAST_UNAVAILABLE = "Cast names unavailable — showing ids"


def _ledger(ctx: InspectContext) -> dict[str, Any]:
    form = LedgerForm(ctx.request.GET)
    after_id = ctx.request.GET.get("after_id") or None
    params = ctx.request.GET.copy()
    context: dict[str, Any] = {
        "form": form,
        "filter_url": reverse(Tab.LEDGER.url_name, args=[ctx.story_id]),
        **paging(params, after_id, None),
    }
    if not form.is_valid():
        return context
    try:
        body = ctx.client.inspect_ledger(
            ctx.story_id, after_id=after_id, limit=PAGE_LIMIT, **form.query()
        )
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(ctx.request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 422:
            form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and exc.code == OMNISCIENT_REQUIRED:
            context["omniscient_required"] = OMNISCIENT_TEXT
        elif isinstance(exc, EngineError) and exc.http_status == 404:
            context["ledger_not_found"] = True
        else:
            context["ledger_error"] = panel_for(exc)
        return context

    names = cast_names(ctx.story.cast if ctx.story is not None else None)
    refs = Refs(ctx.story_id, predicate_names(ctx.request, ctx.client, ctx.story_id))
    links = operation_links(ctx.story_id, discharge_ids(body.ledger))
    context.update(
        ledger=ledger_view(
            ctx.story_id,
            body.thunks,
            body.ledger,
            body.revision,
            refs=refs,
            names=names,
            links=links,
            first_page=after_id is None,
            partial=bool(body.next_after_id or after_id),
            filtered=form.any_filter,
            hidden_facts=body.hidden_facts,
            hidden_facts_unread=body.unparsed.get("hidden_facts"),
        ),
        predicate_names=refs.names,
        cast_names=names,
        empty=empty_state(paged=after_id is not None, state_filter=form.state_filter),
        cast_unavailable=CAST_UNAVAILABLE if ctx.story is None else None,
        **paging(params, after_id, body.next_after_id),
    )
    return context


@require_safe
def ledger(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:ledger``, the story's thunks and promissory ledger (#148)."""
    return inspect_page(request, story_id, Tab.LEDGER, LEDGER_TEMPLATE, _ledger)

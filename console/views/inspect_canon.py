"""The Canon tab (#137): a story's canon facts on both time axes.

``inspect:canon`` (``/stories/<id>/inspect/canon/``) asks the Engine one question per page,
``GET /admin/stories/{id}/facts`` (``viewpoint=omniscient``), with GET names equal to the
Engine's query parameters (:class:`console.forms.inspect.CanonForm`): ``subject``, ``predicate``,
``as_of_story_time`` ("true at story-minute T"), ``as_of_record_time`` ("as the record stood at
moment R", read as UTC, sent as the end of that second), ``include_superseded`` and ``cursor``.

Reads per render, sequential: #135's effective config and story (cast names), then
``list_predicates`` (convention 2's registry read: the predicate select and names), then
``list_facts``, then (only when some row needs it) the current-set read of
:func:`console.canon.fact_states`. A failed registry read turns the predicate filter into a
free-text id field; a failed story read leaves entity ids as ids. Neither blocks the facts read.
A filter the form refuses makes **no** facts call. A ``cursor`` failing the id pattern is
dropped (first page, with a notice), never sent. Paged per #135 convention 6 (``limit=100``):
"Next page" keeps the filters, "First page" drops the cursor, and the Apply form carries no
cursor (a changed filter starts over).

Errors: 409 ``module_off`` escapes to the shell's greyed panel (convention 7); 422
``validation_error`` lands beside the filter ``details.field`` names (``query.<name>``), else
above the filters; ``viewpoint_unresolved`` and 403 ``omniscient_required`` read "This read
needs the omniscient viewpoint"; 404 is convention 10's not-found line; 503
``erasure_pending`` is #87's held wrapper; anything else #43's panel below the filters.
"""

from typing import Any

from django.http import HttpRequest, HttpResponse, QueryDict
from django.urls import reverse
from django.views.decorators.http import require_safe

from console.canon import (
    PERMANENT_NOTE,
    CurrentSet,
    cast_names,
    fact_states,
    question_text,
    read_current_set,
)
from console.engine.client import EngineClientError, EngineError
from console.engine.models import PredicateList
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import OMNISCIENT_TEXT, CanonForm
from console.held import is_held
from console.ids import is_ulid
from console.inspect import Tab, is_module_off
from console.views.inspect import OMNISCIENT_REQUIRED, InspectContext, inspect_page

CANON_TEMPLATE = "console/inspect/canon.html"
PAGE_LIMIT = 100  # convention 6
CURSOR_DROPPED = "That page link was invalid — showing the first page"
CAST_UNAVAILABLE = "Cast names unavailable — showing ids"


def _registry(ctx: InspectContext) -> PredicateList | None:
    """Convention 2's one registry read; ``None`` when it fails (logged)."""
    try:
        return ctx.client.list_predicates(ctx.story_id)
    except EngineClientError as exc:
        log_engine_error(ctx.request, exc)
        return None


def _paging(params: QueryDict, cursor: str | None, next_cursor: str | None) -> dict[str, Any]:
    """Convention 6's links: ``next_query`` keeps every filter, ``first_query`` drops the
    cursor; ``paged`` when this page was reached by a (valid) cursor."""
    first = params.copy()
    first.pop("cursor", None)
    following = ""
    if next_cursor:
        after = first.copy()
        after["cursor"] = next_cursor
        following = after.urlencode()
    return {"first_query": first.urlencode(), "next_query": following, "paged": bool(cursor)}


def _canon(ctx: InspectContext) -> dict[str, Any]:
    registry = _registry(ctx)
    cast = ctx.story.cast if ctx.story is not None else None
    names = registry.names() if registry is not None else {}
    form = CanonForm(ctx.request.GET, registry=registry.predicates if registry else None)
    typed_cursor = ctx.request.GET.get("cursor", "")
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    context: dict[str, Any] = {
        "form": form,
        "predicate_names": names,
        "cast_names": cast_names(cast),
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "filter_url": reverse(Tab.CANON.url_name, args=[ctx.story_id]),
        "actions": None,  # convention 14: #146's row actions
    }
    if not form.is_valid():
        return context
    query = form.query()
    try:
        body = ctx.client.list_facts(ctx.story_id, limit=PAGE_LIMIT, cursor=cursor, **query)
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(ctx.request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 422:
            form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and exc.code == OMNISCIENT_REQUIRED:
            context["omniscient_required"] = OMNISCIENT_TEXT
        elif isinstance(exc, EngineError) and exc.http_status == 404:
            context["facts_not_found"] = True
        else:
            context["facts_error"] = panel_for(exc)
        return context

    def current() -> CurrentSet:
        return read_current_set(
            ctx.request,
            ctx.client,
            ctx.story_id,
            subject=query["subject"],
            predicate=query["predicate"],
            revision=body.timeline_revision,
        )

    include_superseded = query["include_superseded"]
    states = fact_states(body.facts, include_superseded=include_superseded, current=current)
    rows = [(fact, states.by_id[fact.id] if include_superseded else None) for fact in body.facts]
    params = ctx.request.GET.copy()  # the filters as normalised (subject, predicate)
    for name in ("subject", "predicate"):
        if name in params:
            params[name] = str(form.data.get(name) or "")
    params.pop("cursor", None)
    if cursor:
        params["cursor"] = cursor
    context.update(
        facts=body,
        rows=rows,
        show_state=include_superseded,
        question=question_text(
            query["as_of_story_time"], query["as_of_record_time"], include_superseded
        ),
        permanent_note=(
            PERMANENT_NOTE if include_superseded and query["as_of_story_time"] is not None else None
        ),
        unconfirmed_notice=states.notice,
        any_filter=form.any_filter,
        **_paging(params, cursor, body.next_cursor),
    )
    return context


@require_safe
def canon(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:canon``, the story's canon facts (#137)."""
    return inspect_page(request, story_id, Tab.CANON, CANON_TEMPLATE, _canon)

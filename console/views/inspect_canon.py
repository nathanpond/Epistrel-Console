"""The Canon tab (#137): a story's canon facts on both time axes; and one fact's lineage (#138).

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

**The lineage view** (``inspect:canon-lineage``, ``canon/<fact id>/lineage/``, the Canon tab
selected) draws one version's history: every version connected to it through supersede links
(:func:`console.canon.lineage_component`; an automatic supersede starts a new ``lineage_id``, so
the links, not ``lineage_id``, decide), on valid-time and tx-time bars. Reads per render:
#135's effective config and story, then the facts reads of :func:`_locate` (the lineage read of
the link's ``subject`` and ``predicate``, or :func:`console.canon.find_fact` first for an
id-only link or stale params), then the registry read (always, also when the fact isn't found
or a read failed). States follow :func:`console.canon.lineage_states`: no current-set read.
"""

from dataclasses import dataclass, field
from typing import Any

from django.http import HttpRequest, HttpResponse, QueryDict
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from console.canon import (
    CURRENT,
    PERMANENT_NOTE,
    UNDONE,
    CurrentSet,
    EntityRef,
    FactState,
    Span,
    bar_spans,
    cast_names,
    entity_ref,
    fact_states,
    find_fact,
    lineage_component,
    lineage_states,
    question_text,
    read_current_set,
    read_lineage,
)
from console.engine.client import EngineClientError, EngineError
from console.engine.models import FactBody, PredicateList
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import OMNISCIENT_TEXT, CanonForm
from console.held import is_held
from console.ids import is_ulid
from console.inspect import Tab, is_module_off, short_id
from console.predicates import PredicateRef, Refs, predicate_names
from console.views.inspect import OMNISCIENT_REQUIRED, InspectContext, inspect_page

CANON_TEMPLATE = "console/inspect/canon.html"
LINEAGE_TEMPLATE = "console/inspect/canon_lineage.html"
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


# --- a fact's lineage (#138) ---------------------------------------------------------------------

NOT_IN_TIMELINE = "That fact version isn't in the active timeline (it may have been truncated)"
BEYOND = (
    "This fact version is beyond the first 2,000 versions of its subject and predicate; its "
    "lineage can't be drawn"
)
INCOMPLETE = (
    "This subject and predicate have more than 2,000 versions; only the first 2,000 were read, "
    "so this lineage may be incomplete."
)
VIEWPOINT_UNRESOLVED = "viewpoint_unresolved"


@dataclass(frozen=True)
class Lineage:
    """Where :func:`_locate` landed: the fact's subject and predicate once confirmed (the
    lineage read holds the fact, or :func:`console.canon.find_fact` returned it), the
    connected versions, whether the read was complete, and ``missing`` (the not-in-timeline or
    "beyond the first 2,000" text) when there is nothing to draw."""

    subject: str | None = None
    predicate: str | None = None
    versions: list[FactBody] = field(default_factory=list)
    complete: bool = True
    missing: str | None = None


def _given(params: QueryDict) -> tuple[str, str] | None:
    """The link's ``subject`` and ``predicate``, only when both pass the id pattern (anything
    else is ignored: the view resolves the id alone)."""
    subject, predicate = params.get("subject", ""), params.get("predicate", "")
    return (subject, predicate) if is_ulid(subject) and is_ulid(predicate) else None


def _key(fact: FactBody) -> tuple[str, str]:
    return fact.proposition.subject_id, fact.proposition.predicate_id


def _locate(ctx: InspectContext, fact_id: str) -> Lineage:
    """The lineage of ``fact_id`` (the story's AC): with the link's subject and predicate, one
    lineage read, then (fact not in it: stale params) one ``find_fact`` and, under another
    subject or predicate, that lineage; without them, ``find_fact`` then the lineage read.
    Raises :class:`EngineClientError` on any failed read (no partial lineage)."""
    client, story_id = ctx.client, ctx.story_id
    key = _given(ctx.request.GET)
    resolved = False
    if key is None:
        found = find_fact(client, story_id, fact_id)
        if found is None:
            return Lineage(missing=NOT_IN_TIMELINE)
        key, resolved = _key(found), True
    versions, complete = read_lineage(client, story_id, *key)
    component = lineage_component(versions, fact_id)
    if not component and not resolved:
        found = find_fact(client, story_id, fact_id)
        if found is None:
            return Lineage(missing=NOT_IN_TIMELINE)
        if _key(found) != key:
            key = _key(found)
            versions, complete = read_lineage(client, story_id, *key)
            component = lineage_component(versions, fact_id)
    if not component:
        if complete:  # a complete read without it: it left the timeline meanwhile
            return Lineage(missing=NOT_IN_TIMELINE)
        return Lineage(*key, complete=False, missing=BEYOND)
    return Lineage(*key, versions=component, complete=complete)


@dataclass(frozen=True)
class LineageRow:
    """``_lineage_row.html``'s context for one version."""

    fact: FactBody
    state: FactState
    valid: Span
    tx: Span | None
    this: bool


def _lineage_error(ctx: InspectContext, exc: EngineClientError) -> dict[str, Any]:
    """Convention 10 in place of the rows (held and ``module_off`` escape to the shell)."""
    if is_held(exc) or is_module_off(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if isinstance(exc, EngineError) and exc.code in (OMNISCIENT_REQUIRED, VIEWPOINT_UNRESOLVED):
        return {"omniscient_required": OMNISCIENT_TEXT}
    if isinstance(exc, EngineError) and exc.http_status == 404:
        return {"lineage_not_found": True}
    return {"lineage_error": panel_for(exc)}


def _lineage(ctx: InspectContext, fact_id: str) -> dict[str, Any]:
    names = cast_names(ctx.story.cast if ctx.story is not None else None)
    context: dict[str, Any] = {
        "fact_id": fact_id,
        "fact_short": short_id(fact_id),
        "back_url": reverse(Tab.CANON.url_name, args=[ctx.story_id]),
        "cast_names": names,
        "actions": None,  # convention 14: #146's row actions
    }
    try:
        lineage: Lineage | None = _locate(ctx, fact_id)
    except EngineClientError as exc:
        lineage = None
        context.update(_lineage_error(ctx, exc))
    # Convention 2's registry read runs on every render the gate lets through.
    registry = predicate_names(ctx.request, ctx.client, ctx.story_id)
    context["predicate_names"] = registry
    if lineage is None:
        return context
    subject: EntityRef | None = None
    predicate: PredicateRef | None = None
    if lineage.subject and lineage.predicate:
        subject = entity_ref(lineage.subject, names)
        predicate = Refs(ctx.story_id, registry).ref(lineage.predicate)
    context.update(
        lineage=lineage,
        subject_ref=subject,
        predicate_ref=predicate,
        missing=lineage.missing,
        incomplete=INCOMPLETE if not (lineage.complete or lineage.missing) else None,
    )
    if lineage.missing:
        return context
    states = lineage_states(lineage.versions, lineage.complete)
    to_now = {fid for fid, s in states.by_id.items() if s.key in (CURRENT, UNDONE)}
    bars = bar_spans(lineage.versions, timezone.now(), to_now)
    context.update(
        bars=bars,
        rows=[
            LineageRow(f, states.by_id[f.id], bars.valid[f.id], bars.tx.get(f.id), f.id == fact_id)
            for f in lineage.versions
        ],
    )
    return context


@require_safe
def lineage(request: HttpRequest, story_id: str, fact_id: str) -> HttpResponse:
    """``inspect:canon-lineage``, one fact's history on valid-time and tx-time bars (#138)."""
    return inspect_page(
        request, story_id, Tab.CANON, LINEAGE_TEMPLATE, lambda ctx: _lineage(ctx, fact_id)
    )

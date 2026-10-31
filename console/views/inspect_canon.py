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

**Author canon writes** (#146; :mod:`console.forms.canon` builds the bodies):

- ``inspect:canon-new`` (``canon/new/``, in the Canon tab): the predicate chosen first (a GET
  select of the registry, ``?predicate=<id>``), then the gated :class:`FactForm`; its POST calls
  ``POST /admin/stories/{id}/facts`` and renders the result page on the response.
- ``inspect:canon-supersede`` (``canon/<fid>/supersede/``): the version from
  :func:`console.canon.locate_fact` (its state from :func:`console.canon.fact_states` when it
  carries a closure), the pre-filled :class:`SupersedeForm` and its signed snapshot; the POST
  checks the snapshot before anything else (a bad one: the message alone, no Engine call), then
  ``POST …/facts/{fid}/supersede`` and the result page.
- ``inspect:canon-written?report=<pk>``: the result page again from the #44 row (convention 8).
- ``inspect:canon-lock`` / ``-unlock``: plain POSTs from the rows' actions (current and
  closure-undone rows, never under "as recorded at"), ``POST|DELETE …/facts/{fid}/lock``, then
  back to ``next`` with a flash. No confirm step and no effective-config read.

The pages in the tab go through #135's shell, so a greyed Canon tab makes no fact call; a
``module_off`` answer is the greyed panel (a lock's is flashed). Refusals follow the story's map:
``fact_not_found`` flashes and returns to Canon, the story's 404 is convention 10's page, the
fact codes and the shared refusals render above the form (a fresh key), a 422 lands on fields.
"""

import dataclasses
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from console import twostage
from console.canon import (
    CURRENT,
    CURRENT_STATE,
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
    lineage_url,
    locate_fact,
    question_text,
    read_current_set,
    read_lineage,
)
from console.confirm import posted_idempotency_key, safe_next
from console.engine.client import EngineClientError, EngineError
from console.engine.models import FactBody, FactWritten, Predicate, PredicateList
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.forms.canon import (
    ROWS_MAX,
    ROWS_PREFIX,
    SNAPSHOT_EXPIRED,
    SNAPSHOT_FIELD,
    FactForm,
    Snapshot,
    SupersedeForm,
    prefill_data,
    read_snapshot,
    sign_snapshot,
    snapshot_of,
    supersede_initial,
)
from console.forms.inspect import OMNISCIENT_TEXT, PREDICATES_UNAVAILABLE, CanonForm
from console.formset_rows import edit_rows, wants_edit
from console.held import is_held, render_held
from console.ids import is_ulid, new_ulid
from console.inspect import Tab, is_module_off, module_off_unavailable, retrieval_url, short_id
from console.models import EngineOperation
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED, paging
from console.predicates import PredicateRef, Refs, predicate_names
from console.views.inspect import (
    OMNISCIENT_REQUIRED,
    TEMPLATE_KEY,
    InspectContext,
    inspect_page,
)
from console.views.inspect_memory import refusal_text

CANON_TEMPLATE = "console/inspect/canon.html"
LINEAGE_TEMPLATE = "console/inspect/canon_lineage.html"
PAGE_LIMIT = 100  # convention 6


def _registry(ctx: InspectContext) -> PredicateList | None:
    """Convention 2's one registry read; ``None`` when it fails (logged)."""
    try:
        return ctx.client.list_predicates(ctx.story_id)
    except EngineClientError as exc:
        log_engine_error(ctx.request, exc)
        return None


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
        "actions": None,  # convention 14: #146's row actions, set once the rows are read
        "actions_next": ctx.request.get_full_path(),
        "new_fact_url": new_fact_url(
            ctx.story_id,
            {n: _normalised(ctx.request.GET, n) for n in ("subject", "predicate")},
        ),
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
        # #146: current rows get Lock/Unlock and Supersede…, except under "as recorded at".
        actions=ACTIONS_TEMPLATE if query["as_of_record_time"] is None else None,
        **paging(params, cursor, body.next_cursor),
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
    """Convention 10 in place of the rows (held escapes to the shell). A ``module_off`` (the
    gate passed, then the configuration changed) is #135's greyed panel in place of the rows,
    under the header and "Back to Canon" (#171)."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if is_module_off(exc):
        return {"lineage_unavailable": module_off_unavailable(exc, Tab.CANON, ctx.config.effective)}
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
        "actions": ACTIONS_TEMPLATE,  # convention 14: #146's row actions (current rows only)
        "actions_next": ctx.request.get_full_path(),
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


# --- author canon writes (#146) ------------------------------------------------------------------

NEW_TEMPLATE = "console/inspect/canon_new.html"
SUPERSEDE_TEMPLATE = "console/inspect/canon_supersede.html"
WRITTEN_TEMPLATE = "console/inspect/canon_written.html"
MISSING_TEMPLATE = "console/inspect/canon_missing.html"
EXPIRED_TEMPLATE = "console/inspect/canon_expired.html"
ACTIONS_TEMPLATE = "console/inspect/_fact_actions.html"
COMMIT_KIND = "commit_fact"
SUPERSEDE_KIND = "supersede_fact"
WRITTEN_KINDS = (COMMIT_KIND, SUPERSEDE_KIND)
REPORT_PARAM = "report"
LOCK, UNLOCK = "lock", "unlock"
ACTION_STATES = (CURRENT, UNDONE)  # the Engine treats both as current (``_load``'s ``_closed_by``)

NOT_FOUND = "Not found (it may have been deleted or truncated)"
FACT_NOT_FOUND = "fact_not_found"
FACT_SUPERSEDED = "fact_superseded"
FACT_INACTIVE = "fact_inactive"
KEY_MISMATCH = "key_mismatch"
BAD_RESPONSE = "bad_response"
KEY_MISMATCH_TEXT = "A successor must keep the subject, predicate and key qualifiers"
FACT_INACTIVE_TEXT = "That version's operation is no longer on the timeline"
SUPERSEDED_TEXT = "That version was already superseded. Supersede the current one:"
CHOOSE_PREDICATE = "Choose a predicate above to fill in the fact."
PREDICATE_NOT_IN_REGISTRY = "That predicate isn't in the registry — choose another."
NO_MEMORY = "No author memory written"
LOCK_UNREADABLE = "the Engine's answer couldn't be read; reload to see the fact's state"
# The shared refusals in canon's words (else console.views.inspect_memory.refusal_text's).
CANON_REFUSALS = {
    "story_archived": "This story is archived — unarchive it to change its canon",
    "revision_conflict": "The story changed meanwhile — reload and try again",
    "erased_subject_referenced": "This fact refers to an erased subject — nothing was changed",
}
# action → (flash on a change, flash on an unchanged answer)
LOCK_WORDS = {
    LOCK: ("Locked: new version", "Already locked; nothing changed"),
    UNLOCK: ("Unlocked: new version", "Already unlocked; nothing changed"),
}


def canon_url(story_id: str) -> str:
    return reverse(Tab.CANON.url_name, args=[story_id])


def canon_refusal(exc: EngineError, story_id: str) -> str | None:
    """Convention 10's text for a shared refusal of a fact write, else ``None``."""
    return CANON_REFUSALS.get(exc.code) or refusal_text(exc, story_id)


def superseded_message(story_id: str, fact_id: str) -> SafeString:
    """``fact_superseded``: the text with a link to the posted version's lineage view."""
    return format_html(
        '{} <a class="lineage-link" href="{}">lineage of {}</a>',
        SUPERSEDED_TEXT,
        lineage_url(story_id, fact_id),
        short_id(fact_id),
    )


def new_fact_url(story_id: str, params: Mapping[str, str]) -> str:
    """``inspect:canon-new`` with the Canon filter's ``subject`` and ``predicate`` as pre-fill."""
    url = reverse("inspect:canon-new", args=[story_id])
    kept = {k: v for k, v in params.items() if v}
    return f"{url}?{urlencode(kept)}" if kept else url


def written_report_url(story_id: str, pk: int) -> str:
    """Where a commit's or supersede's result page is reachable again (convention 8)."""
    url = reverse("inspect:canon-written", args=[story_id])
    return f"{url}?{urlencode({REPORT_PARAM: pk})}"


def _missing(request: HttpRequest, story_id: str) -> HttpResponse:
    """Convention 10's not-found text (the story is gone) on #43's error page."""
    context = {"story_id": story_id, "back_url": canon_url(story_id), "text": NOT_FOUND}
    return render(request, MISSING_TEMPLATE, context)


def _normalised(params: QueryDict, name: str) -> str:
    return str(params.get(name) or "").strip().upper()


# --- the result page ---


def _operation_row(pk: int | None) -> EngineOperation | None:
    return EngineOperation.objects.filter(pk=pk).first() if pk is not None else None


def _written(
    ctx: InspectContext,
    written: FactWritten,
    kind: str,
    row: EngineOperation | None,
    *,
    names: Mapping[str, str],
    back: str,
) -> dict[str, Any]:
    """The result page's context (rendered in place of the form, or for ``?report=``)."""
    story_id = ctx.story_id
    fact = written.fact
    subject, predicate = fact.proposition.subject_id, fact.proposition.predicate_id
    memory = written.memory_id
    return {
        TEMPLATE_KEY: WRITTEN_TEMPLATE,
        "written": written,
        "heading": "Fact superseded" if kind == SUPERSEDE_KIND else "Fact committed",
        "fact": fact,
        "state": CURRENT_STATE,
        "actions": None,
        "predicate_names": names,
        "cast_names": cast_names(ctx.story.cast if ctx.story is not None else None),
        "superseded": [
            (s, lineage_url(story_id, s, subject, predicate)) for s in written.superseded
        ],
        "memory_url": retrieval_url(story_id, {"memory": memory}) if memory else None,
        "no_memory": NO_MEMORY,
        "operation_pk": row.pk if row is not None else None,
        "replayed": row is not None and row.replayed,
        "report_url": written_report_url(story_id, row.pk) if row is not None else None,
        "fact_lineage_url": lineage_url(story_id, fact.id, subject, predicate),
        "back_url": back,
        "back_label": "Back to Canon" if back == canon_url(story_id) else "Back",
    }


def written_row(pk: object, story_id: str) -> tuple[EngineOperation, FactWritten] | None:
    """The #44 row ``pk`` and its answer when it is an ``ok`` commit or supersede of this story
    whose stored response parses; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    if (
        row.kind not in WRITTEN_KINDS
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    body = FactWritten.from_json(row.response)
    return None if body is None else (row, body)


def _report(ctx: InspectContext) -> dict[str, Any]:
    names = predicate_names(ctx.request, ctx.client, ctx.story_id)  # convention 2
    found = written_row(ctx.request.GET.get(REPORT_PARAM), ctx.story_id)
    back = canon_url(ctx.story_id)
    if found is None:
        return {"missing": NOT_FOUND, "back_url": back, "predicate_names": names}
    row, body = found
    return _written(ctx, body, row.kind, row, names=names, back=back)


@require_safe
def written(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:canon-written?report=<pk>``: a commit's or supersede's result page again, from
    the #44 row's stored response (convention 8), with the page's name reads."""
    return inspect_page(request, story_id, Tab.CANON, WRITTEN_TEMPLATE, _report)


# --- refusals of a commit or supersede ---


def _refused(
    ctx: InspectContext,
    exc: EngineClientError,
    form: FactForm,
    context: dict[str, Any],
    fact_id: str | None = None,
) -> dict[str, Any] | HttpResponse:
    """A refused commit or supersede: the form again with the refusal (and a fresh key: the
    operator will change the body), a flash and back to Canon (``fact_not_found``), or the
    not-found page (the story). Held and ``module_off`` escape to the shell."""
    if is_held(exc) or is_module_off(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    context["idempotency_key"] = new_ulid()
    if not isinstance(exc, EngineError):
        context["write_error"] = panel_for(exc)
        return context
    if exc.code == FACT_NOT_FOUND:
        messages.error(ctx.request, NOT_IN_TIMELINE)
        return redirect(canon_url(ctx.story_id))
    if exc.http_status == 404:
        return _missing(ctx.request, ctx.story_id)
    if exc.code == KEY_MISMATCH:
        context["refusal"] = KEY_MISMATCH_TEXT
    elif exc.code == FACT_SUPERSEDED and fact_id is not None:
        context["refusal"] = superseded_message(ctx.story_id, fact_id)
    elif exc.code == FACT_INACTIVE:
        context["refusal"] = FACT_INACTIVE_TEXT
    elif exc.http_status == 422:
        form.apply_engine_error(exc)
    elif (text := canon_refusal(exc, ctx.story_id)) is not None:
        context["refusal"] = text
    else:
        context["write_error"] = panel_for(exc)
    return context


def _last_row(ctx: InspectContext) -> EngineOperation | None:
    pk = getattr(ctx.client, "last_operation_id", None)
    return _operation_row(pk if isinstance(pk, int) else None)


def _row_edit(data: QueryDict) -> QueryDict | None:
    """The posted form after a qualifier row's add or remove, or ``None`` for a submit."""
    if not wants_edit(data):
        return None
    return edit_rows(data, ROWS_PREFIX, max_rows=ROWS_MAX, min_rows=1, new_row={"type": ["str"]})


# --- the new-fact page ---


def _new(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    registry = _registry(ctx)
    names = registry.names() if registry is not None else {}
    cast = ctx.story.cast if ctx.story is not None else None
    posting = request.method == "POST"
    chosen = _normalised(request.POST if posting else request.GET, "predicate") or _normalised(
        request.GET, "predicate"
    )
    gate = None
    if registry is not None and chosen:
        gate = next((p for p in registry.predicates if p.id == chosen), None)
    back = canon_url(ctx.story_id)
    subject = _normalised(request.GET, "subject")
    action = reverse("inspect:canon-new", args=[ctx.story_id])
    context: dict[str, Any] = {
        "registry": registry,
        "chosen": chosen,
        "choice_subject": subject,
        "predicate_unknown": PREDICATE_NOT_IN_REGISTRY
        if registry and chosen and not gate
        else None,
        "choose_predicate": CHOOSE_PREDICATE,
        "predicates_unavailable": PREDICATES_UNAVAILABLE if registry is None else None,
        "predicate_names": names,
        "cast_names": cast_names(cast),
        "back_url": back,
        "action_url": f"{action}?{urlencode({'predicate': gate.id})}" if gate else action,
        "form": None,
    }
    if registry is not None and gate is None:
        return context  # the predicate comes first
    if not posting:
        initial = {"subject": subject}
        if gate is None:
            initial["predicate"] = chosen
        form = FactForm(predicate=gate, cast=cast, initial=initial)
        context.update(form=form, idempotency_key=new_ulid())
        return context
    key = posted_idempotency_key(request)
    edited = _row_edit(request.POST)
    form = FactForm(edited if edited is not None else request.POST, predicate=gate, cast=cast)
    context.update(form=form, idempotency_key=key)
    if edited is not None:
        form.suppress_errors()
        return context
    if not form.is_valid():
        return context
    try:
        result = ctx.client.commit_fact(ctx.story_id, form.body, idempotency_key=key)
    except EngineClientError as exc:
        return _refused(ctx, exc, form, context)
    return _written(ctx, result, COMMIT_KIND, _last_row(ctx), names=names, back=back)


@require_http_methods(["GET", "HEAD", "POST"])
def new_fact(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:canon-new``: the predicate choice, the fact form, then
    ``POST /admin/stories/{id}/facts`` and the result page on the POST response."""
    return inspect_page(request, story_id, Tab.CANON, NEW_TEMPLATE, _new)


# --- the supersede page ---


def _gate(registry: PredicateList | None, predicate_id: str) -> Predicate | None:
    """The version's predicate in a successful registry read; ``None`` (no registry) when the
    read failed or the registry doesn't hold it."""
    if registry is None:
        return None
    return next((p for p in registry.predicates if p.id == predicate_id), None)


def _supersede_context(
    ctx: InspectContext, fact_id: str, back: str, registry: PredicateList | None
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "fact_short": short_id(fact_id),
        "back_url": back,
        "next_url": back,
        "predicate_names": registry.names() if registry is not None else {},
        "cast_names": cast_names(ctx.story.cast if ctx.story is not None else None),
        "predicates_unavailable": PREDICATES_UNAVAILABLE if registry is None else None,
        "action_url": reverse("inspect:canon-supersede", args=[ctx.story_id, fact_id]),
    }


def _supersede_form(ctx: InspectContext, fact_id: str) -> dict[str, Any] | HttpResponse:
    """The supersede page's GET: the version (one id-only read), its state when it carries a
    closure, then the registry read and the pre-filled form with its signed snapshot."""
    request, story_id = ctx.request, ctx.story_id
    back = safe_next(request, canon_url(story_id))
    try:
        fact, revision = locate_fact(ctx.client, story_id, fact_id)
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 404:
            return _missing(request, story_id)
        registry = _registry(ctx)
        return {**_supersede_context(ctx, fact_id, back, registry), "write_error": panel_for(exc)}
    if fact is None:
        messages.error(request, NOT_IN_TIMELINE)
        return redirect(canon_url(story_id))
    state = CURRENT_STATE
    if fact.tx_to is not None:
        subject, predicate_id = _key(fact)

        def current() -> CurrentSet:
            return read_current_set(
                request,
                ctx.client,
                story_id,
                subject=subject,
                predicate=predicate_id,
                revision=revision,
            )

        state = fact_states([fact], include_superseded=True, current=current).by_id[fact.id]
    registry = _registry(ctx)
    context = _supersede_context(ctx, fact_id, back, registry)
    context.update(version=fact, version_state=state)
    if state.superseded:
        context["refusal"] = superseded_message(story_id, fact_id)
        return context
    gate = _gate(registry, fact.proposition.predicate_id)
    cast = ctx.story.cast if ctx.story is not None else None
    initial, rows = supersede_initial(fact, gate)
    bare = snapshot_of(story_id, fact, gate, None)
    names = context["cast_names"]
    probe = SupersedeForm(
        prefill_data(initial, rows), snapshot=bare, predicate=gate, cast=cast, cast_names=names
    )
    snapshot = dataclasses.replace(bare, prefill_body=probe.body if probe.is_valid() else None)
    form = SupersedeForm(
        snapshot=snapshot,
        predicate=gate,
        cast=cast,
        cast_names=names,
        initial=initial,
        rows=rows,
    )
    context.update(
        form=form,
        snapshot=snapshot,
        subject_ref=entity_ref(snapshot.subject_id, names),
        token=sign_snapshot(snapshot),
        idempotency_key=new_ulid(),
        body_text=_pretty(snapshot.prefill_body),
    )
    return context


def _pretty(body: object) -> str:
    return json.dumps(body, indent=2, ensure_ascii=False) if body else ""


def _supersede_write(
    ctx: InspectContext, fact_id: str, snapshot: Snapshot, token: str
) -> dict[str, Any] | HttpResponse:
    """The supersede page's POST: the registry read, the form against the snapshot, then
    ``POST …/facts/{fid}/supersede`` and the result page."""
    request, story_id = ctx.request, ctx.story_id
    back = safe_next(request, canon_url(story_id))
    registry = _registry(ctx)
    gate = _gate(registry, snapshot.predicate_id)
    cast = ctx.story.cast if ctx.story is not None else None
    context = _supersede_context(ctx, fact_id, back, registry)
    key = posted_idempotency_key(request)
    edited = _row_edit(request.POST)
    form = SupersedeForm(
        edited if edited is not None else request.POST,
        snapshot=snapshot,
        predicate=gate,
        cast=cast,
        cast_names=context["cast_names"],
    )
    context.update(
        form=form,
        snapshot=snapshot,
        subject_ref=entity_ref(snapshot.subject_id, context["cast_names"]),
        token=token,
        idempotency_key=key,
        body_text=_pretty(snapshot.prefill_body),
    )
    if edited is not None:
        form.suppress_errors()
        return context
    if not form.is_valid():
        return context
    context["body_text"] = _pretty(form.body)
    try:
        result = ctx.client.supersede_fact(story_id, fact_id, form.body, idempotency_key=key)
    except EngineClientError as exc:
        return _refused(ctx, exc, form, context, fact_id)
    names = context["predicate_names"]
    return _written(ctx, result, SUPERSEDE_KIND, _last_row(ctx), names=names, back=back)


@require_http_methods(["GET", "HEAD", "POST"])
def supersede(request: HttpRequest, story_id: str, fact_id: str) -> HttpResponse:
    """``inspect:canon-supersede``: the pre-filled successor form, then
    ``POST /admin/stories/{id}/facts/{fid}/supersede`` and the result page. A POST whose
    snapshot is tampered with or expired re-renders only that message, with no Engine call."""
    if request.method == "POST":
        token = request.POST.get(SNAPSHOT_FIELD, "")
        try:
            snapshot = read_snapshot(token, story_id, fact_id)
        except twostage.ConfirmationExpired:
            back = safe_next(request, canon_url(story_id))
            context = {"story_id": story_id, "text": SNAPSHOT_EXPIRED, "back_url": back}
            return render(request, EXPIRED_TEMPLATE, context)
        return inspect_page(
            request,
            story_id,
            Tab.CANON,
            SUPERSEDE_TEMPLATE,
            lambda ctx: _supersede_write(ctx, fact_id, snapshot, token),
        )
    return inspect_page(
        request, story_id, Tab.CANON, SUPERSEDE_TEMPLATE, lambda ctx: _supersede_form(ctx, fact_id)
    )


# --- lock and unlock (plain writes, convention 8's PRG) ---


def _lock_write(request: HttpRequest, story_id: str, fact_id: str, action: str) -> HttpResponse:
    """``POST`` (lock) or ``DELETE`` (unlock) ``/admin/stories/{id}/facts/{fid}/lock`` with the
    row's key, then back to ``next`` with a flash naming the new version (or "nothing
    changed"). No confirm step and no effective-config read."""
    back = safe_next(request, canon_url(story_id))
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        if action == LOCK:
            result = client.lock_fact(story_id, fact_id, idempotency_key=key)
        else:
            result = client.unlock_fact(story_id, fact_id, idempotency_key=key)
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        if not isinstance(exc, EngineError):
            raise
        log_engine_error(request, exc)
        if is_module_off(exc):
            reason = module_off_unavailable(exc, Tab.CANON).reason
            messages.error(request, reason.replace("`", ""))
        elif exc.code == FACT_NOT_FOUND:
            messages.error(request, NOT_IN_TIMELINE)
            return redirect(canon_url(story_id))
        elif exc.http_status == 404:
            return _missing(request, story_id)
        elif exc.code == FACT_SUPERSEDED:
            messages.error(request, superseded_message(story_id, fact_id), extra_tags="safe")
        elif exc.code == FACT_INACTIVE:
            messages.error(request, FACT_INACTIVE_TEXT)
        elif exc.code == BAD_RESPONSE and exc.http_status == 200:
            verb = LOCK_WORDS[action][0].split(":")[0]
            messages.warning(request, f"{verb} {short_id(fact_id)} — {LOCK_UNREADABLE}")
        elif exc.http_status == 422:
            messages.error(request, exc.message)
        elif (text := canon_refusal(exc, story_id)) is not None:
            messages.error(request, text)
        else:
            raise
        return redirect(back)
    changed, unchanged = LOCK_WORDS[action]
    if result.operation_id is None:
        messages.info(request, unchanged)
    else:
        messages.success(request, f"{changed} {short_id(result.fact.id)}")
    return redirect(back)


@require_POST
def lock(request: HttpRequest, story_id: str, fact_id: str) -> HttpResponse:
    """``inspect:canon-lock``: ``POST /admin/stories/{id}/facts/{fid}/lock``."""
    return _lock_write(request, story_id, fact_id, LOCK)


@require_POST
def unlock(request: HttpRequest, story_id: str, fact_id: str) -> HttpResponse:
    """``inspect:canon-unlock``: ``DELETE /admin/stories/{id}/facts/{fid}/lock``."""
    return _lock_write(request, story_id, fact_id, UNLOCK)

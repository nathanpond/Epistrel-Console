"""The Knowledge tab's Beliefs section (#190): what one character believes.

``inspect:beliefs`` (``/stories/<id>/inspect/knowledge/beliefs/``, the Knowledge tab selected)
renders through #135's :func:`console.views.inspect.inspect_page` with ``Tab.KNOWLEDGE`` (#189),
so a greyed tab makes no beliefs or registry read. The GET filters are
:class:`console.forms.inspect.BeliefsForm`'s (``character``, ``as_of``, ``proposition_id``), each
the Engine's query parameter; the cursor is ``after`` (convention 6 via
:func:`console.paging.paging`).

Reads per render, sequential: the shell's effective config and story (cast names), then one
``GET /stories/{id}/inspect/beliefs`` (``viewpoint=omniscient``, ``limit=100``), then, only when
it answered at least one row, convention 2's one registry read (``list_predicates``: the names,
``{}`` on failure: short ids, and #191's ``cardinality``), then #191's canon comparison
(:func:`console.belief_canon.canon_column`: at most 4 ``list_facts`` calls, none for a past
turn or a page with no comparable row). No beliefs call for
an invalid filter, a blank character (no modeled characters, or nothing typed when the story
read failed). An ``after`` failing the id pattern is dropped (:data:`console.paging.
CURSOR_DROPPED`) and never sent.

Errors stay inside the section (convention 10): 422 ``unknown_character`` beside Character; any
other 422 beside the field ``details.field`` names, else above the filters (``query.after`` with
"First page"); ``viewpoint_unresolved`` and 403 ``omniscient_required`` read "This read needs the
omniscient viewpoint"; 404 is the not-found line; 409 ``module_off`` escapes to the shell's
greyed panel; 503 ``erasure_pending`` to #87's held wrapper; anything else #43's panel below the
filters. Wording and rows: :mod:`console.beliefs`.
"""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_safe

from console.belief_canon import CanonColumn, canon_column
from console.beliefs import (
    ENTER_CHARACTER,
    FIRST_ORDER_NOTICE,
    IDENTITIES,
    IDENTITIES_FILTERED,
    IDENTITIES_FIRST_PAGE,
    NO_CHARACTERS,
    PLAYER_NOTICE,
    belief_rows,
    count_text,
    empty_text,
    header_text,
    identity_rows,
    story_minor_ids,
    time_text,
)
from console.canon import cast_names
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import PredicateList
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import OMNISCIENT_TEXT, VIEWPOINT_UNRESOLVED, BeliefsForm
from console.held import is_held
from console.ids import is_ulid
from console.inspect import Tab, is_module_off
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED, paging
from console.views.inspect import OMNISCIENT_REQUIRED, InspectContext, inspect_page
from console.wording import character_label

BELIEFS_TEMPLATE = "console/inspect/beliefs.html"
PAGE_LIMIT = 100  # convention 6 (the route allows 500)
CURSOR = "after"


def _beliefs(ctx: InspectContext) -> dict[str, Any]:
    request = ctx.request
    cast = ctx.story.cast if ctx.story is not None else None
    names = cast_names(cast)
    minor = story_minor_ids(ctx.story.characters if ctx.story is not None else None)
    labels = {c.id: character_label(c.id, names, minor) for c in cast or ()}
    form = BeliefsForm(request.GET, cast=cast, labels=labels)
    typed_cursor = request.GET.get(CURSOR, "")
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    context: dict[str, Any] = {
        "form": form,
        "cast_names": names,
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "filter_url": reverse("inspect:beliefs", args=[ctx.story_id]),
        "actions": None,  # convention 14: #194's row actions
        "actions_next": request.get_full_path(),
        "marks": None,  # #191's Canon column (set once a beliefs page answered)
    }
    form.is_valid()
    if not form.valid("character"):
        return context
    character: str = form.cleaned_data["character"]
    if not character:
        if not form.errors:
            context["prompt"] = NO_CHARACTERS if cast is not None else ENTER_CHARACTER
        return context
    label = character_label(character, names, minor)
    player = any(c.id == character and c.is_player for c in cast or ())
    as_of_valid = form.valid("as_of")
    time = time_text(form.cleaned_data["as_of"]) if as_of_valid else None
    context["header"] = header_text(label, time, None)
    params = request.GET.copy()
    params["character"] = character  # the (possibly defaulted) character, always explicit
    if "proposition_id" in params:
        params["proposition_id"] = str(form.data.get("proposition_id") or "")
    params.pop(CURSOR, None)
    if cursor:
        params[CURSOR] = cursor
    context.update(paging(params, cursor, None, param=CURSOR))
    if not form.is_valid():
        return context
    query = form.query()
    try:
        body = ctx.client.inspect_beliefs(ctx.story_id, limit=PAGE_LIMIT, after=cursor, **query)
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.code in (OMNISCIENT_REQUIRED, VIEWPOINT_UNRESOLVED):
            context["omniscient_required"] = OMNISCIENT_TEXT
        elif isinstance(exc, EngineError) and exc.http_status == 422:
            context["after_refused"] = form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and exc.http_status == 404:
            context["beliefs_not_found"] = True
        else:
            context["beliefs_error"] = panel_for(exc)
        return context

    as_of, proposition = query["as_of"], query["proposition_id"]
    column = CanonColumn({})
    if body.beliefs:
        registry = _registry(request, ctx.client, ctx.story_id)
        context["predicate_names"] = {} if registry is None else registry.names()
        column = canon_column(
            request,
            ctx.client,
            ctx.story_id,
            body.beliefs,
            body.revision,
            cardinality=None if registry is None else registry.cardinality(),
            as_of=as_of,
        )
    rows = belief_rows(ctx.story_id, body.beliefs, column.marks)
    filtered = as_of is not None or proposition is not None
    context.update(
        beliefs=body,
        rows=rows,
        header=header_text(label, time, body.revision),
        count=count_text(len(rows), partial=bool(cursor or body.next_after), filtered=filtered),
        first_order=FIRST_ORDER_NOTICE,
        marks=column.marks,
        canon_notices=column.notices,
        canon_summary=column.summary,
        player_notice=PLAYER_NOTICE if player else None,
        empty=empty_text(
            label, paged=cursor is not None, proposition=bool(proposition), as_of=as_of
        ),
        show_identities=not player,
        identities_heading=IDENTITIES_FILTERED if filtered else IDENTITIES,
        identities_first_page=IDENTITIES_FIRST_PAGE if cursor else None,
        identities=identity_rows(
            ctx.story_id, body.identities, names, frozenset(b.belief_id for b in body.beliefs)
        ),
        **paging(params, cursor, body.next_after, param=CURSOR),
    )
    return context


def _registry(request: HttpRequest, client: EngineClient, story_id: str) -> PredicateList | None:
    """Convention 2's one registry read, supplying both the predicate names and #191's
    ``cardinality``; ``None`` when it fails (logged: short ids, no functional rule)."""
    try:
        return client.list_predicates(story_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


@require_safe
def beliefs(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:beliefs``, one character's partition (#190)."""
    return inspect_page(request, story_id, Tab.KNOWLEDGE, BELIEFS_TEMPLATE, _beliefs)

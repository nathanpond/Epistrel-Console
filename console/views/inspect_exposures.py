"""The Knowledge tab's Exposures section (#193): what witnesses heard and whether it resolved.

``inspect:exposures`` (``/stories/<id>/inspect/knowledge/exposures/``, the Knowledge tab
selected) renders through #135's :func:`console.views.inspect.inspect_page` with
``Tab.KNOWLEDGE`` (#189), so a greyed tab makes no exposures call. An exposure is a claim a
non-salient witness heard that hasn't (yet) become a belief; the Engine resolves it lazily when a
later turn needs that witness's belief (FR-BELIEF-5, FR-BELIEF-14). The read is raw: opening the
list never resolves anything. The GET filters are :class:`console.forms.inspect.ExposuresForm`'s
(``character``, ``state``, ``as_of``), each the Engine's query parameter; the cursor is
``after_id`` (convention 6 via :func:`console.paging.paging`).

Reads per render, sequential: the shell's effective config and story (cast names), then one
``GET /stories/{id}/inspect/exposures`` (``viewpoint=omniscient``, ``limit=100``), then, only
when it answered at least one item, convention 2's one registry read (``predicate_names``, ``{}``
on failure: short ids) and one ``operation_links`` over the page's ``operation_id`` and
``resolution_operation_id`` values (Console rows, no Engine call; a ``DatabaseError`` is logged
and leaves every id as ``<code>``). No call for an invalid filter. An ``after_id`` failing the id
pattern is dropped (:data:`console.paging.CURSOR_DROPPED`) and never sent.

Resolved by follows the row's ``state`` as the Engine computed it through ``as_of`` (C15: the
Engine nulls the resolution ids of a row unresolved then), so a past view never shows a later
resolution. Its "belief" link opens the witness's *current* belief in the proposition.

Errors stay inside the section (convention 10): 422 ``unknown_character`` beside Witness; any
other 422 beside the filter ``details.field`` names, else above the filters (``query.after_id``
with "First page"); ``viewpoint_unresolved`` and 403 ``omniscient_required`` read "This read
needs the omniscient viewpoint"; 404 is the not-found line; 409 ``module_off`` escapes to the
shell's greyed panel; 503 ``erasure_pending`` to #87's held wrapper; anything else #43's panel
below the filters.
"""

import dataclasses
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_safe

from console.beliefs import Linked, MoreItem, story_minor_ids
from console.canon import cast_names
from console.cast import Character
from console.engine.client import EngineClientError, EngineError
from console.engine.models import ExposureItem, Proposition
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import OMNISCIENT_TEXT, VIEWPOINT_UNRESOLVED, ExposuresForm
from console.held import is_held
from console.history import operation_links
from console.ids import is_ulid
from console.inspect import Tab, is_module_off
from console.ledger import MATCHING, ON_PAGE
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED, paging
from console.positions import knowledge_position_text, knowledge_position_url, position_step
from console.predicates import predicate_names
from console.views.inspect import OMNISCIENT_REQUIRED, InspectContext, inspect_page
from console.views.inspect_divergences import Speaker, speaker_of
from console.wording import character_label

log = logging.getLogger(__name__)

EXPOSURES_TEMPLATE = "console/inspect/exposures.html"
PAGE_LIMIT = 100  # convention 6 (the route allows 500)
CURSOR = "after_id"
RAW_READ = "A raw read: opening this list never resolves an exposure."
NONE = "No exposures recorded"
NONE_FILTERED = "No exposures match these filters"
EMPTY_PAGE = "No exposures on this page — the list may have changed since the last page."
GENESIS = "Genesis opening"
CONTENT = "content"
RESOLVED = "resolved"
PARTIAL = "partial"
PARTIAL_TITLE = "partial — the witness caught only part of the claim"
TRANSITION = "Transition"
BELIEF_TITLE = "Current belief in this proposition (may have changed since it resolved)"
NOT_FOUND = "Not found (it may have been deleted or truncated)"


def count_text(n: int, revision: int, *, partial: bool, filtered: bool) -> str:
    """C11: "12 exposures" / "1 exposure", then "on this page" and/or "matching filters", then
    " · revision R"."""
    noun = "exposure" if n == 1 else "exposures"
    qualifier = ", ".join(text for text, on in ((ON_PAGE, partial), (MATCHING, filtered)) if on)
    count = f"{n:,} {noun} {qualifier}" if qualifier else f"{n:,} {noun}"
    return f"{count} · revision {revision}"


def empty_text(*, paged: bool, filtered: bool) -> str:
    """The empty page's line: a cursor page's, then a filtered one's, then the plain one."""
    if paged:
        return EMPTY_PAGE
    return NONE_FILTERED if filtered else NONE


@dataclass(frozen=True)
class ExposureRow:
    """``_exposure_row.html``'s context for one item. ``source`` is ``None`` for the genesis
    opening; ``resolved`` follows the Engine's effective ``state``; ``belief_url`` the Resolved
    by "belief" link (``None`` unresolved, or without the Beliefs route); ``op_pk`` /
    ``resolution_op_pk`` the #44 rows when the Console logged those operations."""

    item: ExposureItem
    when: Linked
    witness: Speaker
    heard: Proposition | None
    source: Speaker | None
    fidelity_title: str
    resolved: bool
    belief_url: str | None
    op_pk: int | None
    resolution_op_pk: int | None
    more: tuple[MoreItem, ...]
    step: MoreItem
    raw_item: str

    @property
    def resolved_parts(self) -> bool:
        """Whether Resolved by has anything to list (a transition, an operation or the link)."""
        item = self.item
        return bool(item.resolved_by_transition or item.resolution_operation_id or self.belief_url)


def _pretty(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _more(item: ExposureItem) -> tuple[MoreItem, ...]:
    """More's ids before the operation (the template adds it, then :attr:`ExposureRow.step`)."""
    return (
        MoreItem("Exposure id", item.id, "code"),
        MoreItem("Proposition id", item.proposition_id, "code"),
        MoreItem("Source event id", item.source_event_id, "code"),
        MoreItem("Kind", item.kind),
    )


def _belief_url(beliefs_url: str | None, item: ExposureItem) -> str | None:
    if beliefs_url is None:
        return None
    query = urlencode({"character": item.witness, "proposition_id": item.proposition_id})
    return f"{beliefs_url}?{query}"


def exposure_rows(
    story_id: str,
    items: Sequence[ExposureItem],
    *,
    cast: Sequence[Character] | None,
    minor: frozenset[str],
    links: Mapping[str, int],
) -> list[ExposureRow]:
    """The rows in the Engine's order (by exposure id, i.e. recording order)."""
    try:
        beliefs_url: str | None = reverse("inspect:beliefs", args=[story_id])
    except NoReverseMatch:
        beliefs_url = None
    rows = []
    for item in items:
        resolved = item.state == RESOLVED
        rows.append(
            ExposureRow(
                item=item,
                when=Linked(
                    knowledge_position_text(item.position),
                    knowledge_position_url(story_id, item.position),
                ),
                witness=speaker_of(item.witness, cast, minor),
                heard=item.proposition_view,
                source=None if item.source is None else speaker_of(item.source, cast, minor),
                fidelity_title=PARTIAL_TITLE if item.fidelity == PARTIAL else item.fidelity,
                resolved=resolved,
                belief_url=_belief_url(beliefs_url, item) if resolved else None,
                op_pk=links.get(item.operation_id),
                resolution_op_pk=links.get(item.resolution_operation_id)
                if item.resolution_operation_id
                else None,
                more=_more(item),
                step=MoreItem(*position_step(item.position)),
                raw_item=_pretty(dataclasses.asdict(item)),
            )
        )
    return rows


def _operation_ids(items: Sequence[ExposureItem]) -> list[str]:
    """The page's ``operation_id`` and ``resolution_operation_id`` values, deduplicated, nulls
    dropped, in page order."""
    ids: dict[str, None] = {}
    for item in items:
        ids[item.operation_id] = None
        if item.resolution_operation_id:
            ids[item.resolution_operation_id] = None
    return list(ids)


def _links(story_id: str, items: Sequence[ExposureItem]) -> dict[str, int]:
    """One ``operation_links`` over the page's operation ids; ``{}`` on a database failure
    (logged: every id renders as ``<code>``)."""
    try:
        return operation_links(story_id, _operation_ids(items))
    except DatabaseError:
        log.warning("operation links unavailable for story %s", story_id, exc_info=True)
        return {}


def _exposures(ctx: InspectContext) -> dict[str, Any]:
    request = ctx.request
    cast = ctx.story.cast if ctx.story is not None else None
    names = cast_names(cast)
    minor = story_minor_ids(ctx.story.characters if ctx.story is not None else None)
    labels = {c.id: character_label(c.id, names, minor) for c in cast or ()}
    form = ExposuresForm(request.GET, cast=cast, labels=labels)
    typed_cursor = request.GET.get(CURSOR, "")
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    params = request.GET.copy()
    if "character" in params:
        params["character"] = form.normalised("character")
    params.pop(CURSOR, None)
    if cursor:
        params[CURSOR] = cursor
    context: dict[str, Any] = {
        "form": form,
        "raw_read": RAW_READ,
        "cast_names": names,
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "filter_url": reverse("inspect:exposures", args=[ctx.story_id]),
        "actions": None,  # convention 14: no story sets row actions yet
        "genesis": GENESIS,
        "content_kind": CONTENT,
        "transition": TRANSITION,
        "belief_title": BELIEF_TITLE,
        **paging(params, cursor, None, param=CURSOR),
    }
    if not form.is_valid():
        return context
    try:
        body = ctx.client.inspect_exposures(
            ctx.story_id, limit=PAGE_LIMIT, after_id=cursor, **form.query()
        )
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.code in (OMNISCIENT_REQUIRED, VIEWPOINT_UNRESOLVED):
            context["omniscient_required"] = OMNISCIENT_TEXT
        elif isinstance(exc, EngineError) and exc.http_status == 422:
            context["after_refused"] = form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and exc.http_status == 404:
            context["not_found"] = NOT_FOUND
        else:
            context["exposures_error"] = panel_for(exc)
        return context

    filtered = form.any_filter
    links: dict[str, int] = {}
    if body.items:
        context["predicate_names"] = predicate_names(request, ctx.client, ctx.story_id)
        links = _links(ctx.story_id, body.items)
    context.update(
        exposures=body,
        rows=exposure_rows(ctx.story_id, body.items, cast=cast, minor=minor, links=links),
        count=count_text(
            len(body.items),
            body.revision,
            partial=bool(cursor or body.next_after_id),
            filtered=filtered,
        ),
        empty=empty_text(paged=cursor is not None, filtered=filtered),
        **paging(params, cursor, body.next_after_id, param=CURSOR),
    )
    return context


@require_safe
def exposures(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:exposures``, what witnesses heard (#193)."""
    return inspect_page(request, story_id, Tab.KNOWLEDGE, EXPOSURES_TEMPLATE, _exposures)

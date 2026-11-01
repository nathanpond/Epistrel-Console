"""The Memory tab's memory list (#236): every memory of a story, filtered and paged.

``inspect:memory-list`` (``/stories/<id>/inspect/memory/list/``, the Memory tab selected) renders
through #135's :func:`console.views.inspect.inspect_page` with ``Tab.MEMORY``, so it keeps the
tab's episodic gate (``memory.episodic.enabled``): a greyed tab makes no list read. Reads per
render, sequential: the shell's effective config and story (cast names), then one
``GET /admin/stories/{id}/memories`` (``viewpoint=omniscient``, ``limit=100``; Engine #531,
``EngineClient.list_story_memories``) with the valid filters and the cursor when it is valid.

The GET filters are :class:`console.forms.inspect.MemoryListForm`'s, each the Engine's query
parameter (``kind``, ``retrieval_tier``, ``holder_character_id``, ``from_turn``, ``to_turn``,
``quarantined``, ``pinned``); an invalid filter makes no call. The cursor is ``cursor`` (C12's
:func:`console.paging.paging`): one that isn't a ULID is dropped
(:data:`console.paging.CURSOR_DROPPED`) and never sent; Apply carries none, so a changed filter
starts over.

Rows keep the Engine's order (by id). Each carries #143's ``_memory_actions.html`` with
``actions_next`` this page's full path, so a pin, quarantine, release or sanitize comes back to
the same page and filters (and the sanitize page finds the memory's text from this list:
``inspect_memory.explorer_text``). A page entry that doesn't parse is counted in one notice and
shown raw in the page's More (D15).

Errors (convention 10): a 422 at ``query.<filter>`` lands beside that filter, any other 422
above the filters (``query.cursor`` with "First page"); 403 ``omniscient_required`` and 422
``viewpoint_unresolved`` read :data:`console.forms.inspect.OMNISCIENT_TEXT`; 404 is the
not-found line; 503 ``erasure_pending`` is #87's held wrapper; anything else #43's panel below
the filters, the filters kept.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_safe

from console.beliefs import story_minor_ids
from console.canon import cast_names
from console.cast import Character, character_labels
from console.engine.client import EngineClientError, EngineError
from console.engine.models import (
    ConsolidationProvenance,
    ListedMemory,
    MemoryList,
    rejected_entries,
)
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import (
    MEMORY_LIST_FIELDS,
    OMNISCIENT_TEXT,
    VIEWPOINT_UNRESOLVED,
    MemoryListForm,
)
from console.held import is_held
from console.ids import is_ulid
from console.inspect import (
    Tab,
    history_turn_url,
    is_module_off,
    memory_list_url,
    position_text,
    retrieval_url,
)
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED, paging
from console.retrieval import KINDS, TIERS
from console.views.inspect import OMNISCIENT_REQUIRED, InspectContext, inspect_page
from console.views.inspect_memory import shown_reason
from console.wording import character_label, humanised

MEMORY_LIST_TEMPLATE = "console/inspect/memory_list.html"
PAGE_LIMIT = 100  # C11's page size (the route allows 500)
CURSOR = "cursor"
NONE = "This story has no memories yet"
NONE_FILTERED = "No memories match these filters"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
EN_DASH = "\u2013"


def count_heading(n: int, *, filtered: bool) -> str:
    """ "Memories (N on this page)", with " · filtered" when a filter was sent."""
    heading = f"Memories ({n:,} on this page)"
    return f"{heading} · filtered" if filtered else heading


def unread_text(n: int) -> str:
    """D15's notice: "1 memory couldn't be read" / "N memories couldn't be read"."""
    return f"{n:,} memory couldn't be read" if n == 1 else f"{n:,} memories couldn't be read"


def sources_text(n: int) -> str:
    return f"{n:,} source" if n == 1 else f"{n:,} sources"


def turns_text(start: int | None, end: int | None) -> str | None:
    """A consolidation window: "turns F\u2013T", "turn F" when equal, "from turn F" / "to turn T"
    with one side null, ``None`` with both."""
    if start is not None and end is not None:
        return f"turn {start}" if start == end else f"turns {start}{EN_DASH}{end}"
    if start is not None:
        return f"from turn {start}"
    if end is not None:
        return f"to turn {end}"
    return None


@dataclass(frozen=True)
class Part:
    """One part of the Consolidation cell; ``code`` renders ``text`` in ``<code>``."""

    prefix: str
    text: str
    code: bool = False


@dataclass(frozen=True)
class SourceLink:
    memory_id: str
    url: str | None


@dataclass(frozen=True)
class ConsolidationCell:
    """A consolidation output's provenance: the window, scope and level parts (each only when
    set), the sources (``sources`` empty: no count and no details), the operation id for
    ``title``."""

    operation_id: str
    parts: tuple[Part, ...]
    sources: tuple[SourceLink, ...]
    sources_text: str


@dataclass(frozen=True)
class Holder:
    id: str
    label: str
    is_player: bool


@dataclass(frozen=True)
class MemoryListRow:
    """``memory_list.html``'s row: ``memory`` (the ListedMemory, so ``_memory_actions.html``
    reads ``row.memory``) plus the derived labels and links."""

    memory: ListedMemory
    id_url: str | None
    kind_label: str | None
    tier_label: str | None
    retired_reason: str | None
    holder: Holder | None
    position: str | None
    position_url: str | None
    pinned_text: str | None
    quarantine_reason: str
    consolidation: ConsolidationCell | None
    more: str


def _pretty(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _id_url(story_id: str, memory: ListedMemory) -> str | None:
    """The Id column's Retrieval link: ``memory``, ``tier`` (only a known tier) and
    ``include_quarantined=1``."""
    params = {"memory": memory.id}
    if memory.retrieval_tier in TIERS:
        params["tier"] = memory.retrieval_tier
    params["include_quarantined"] = "1"
    return retrieval_url(story_id, params)


def _consolidation(story_id: str, found: ConsolidationProvenance) -> ConsolidationCell:
    parts: list[Part] = []
    window = turns_text(found.from_turn, found.to_turn)
    if window is not None:
        parts.append(Part("", window))
    if found.scope is not None:
        parts.append(Part("scope ", found.scope, code=True))
    if found.level is not None:
        parts.append(Part("", f"level {found.level}"))
    sources = tuple(
        SourceLink(source, retrieval_url(story_id, {"memory": source, "include_quarantined": "1"}))
        for source in found.source_memory_ids
    )
    return ConsolidationCell(
        operation_id=found.operation_id,
        parts=tuple(parts),
        sources=sources,
        sources_text=sources_text(len(sources)) if sources else "",
    )


def _more(memory: ListedMemory) -> str:
    """Convention 13's per-row More."""
    return _pretty(
        {
            "character_id": memory.character_id,
            "implication_count": memory.implication_count,
            "embedded": memory.embedded,
            "membership": memory.membership,
            "provenance": memory.provenance,
            "visibility": memory.visibility,
            "quarantined_at": memory.quarantined_at,
            "quarantined_by": memory.quarantined_by,
        }
    )


def memory_rows(
    story_id: str,
    memories: Sequence[ListedMemory],
    *,
    cast: Sequence[Character] | None,
    minor: frozenset[str] = frozenset(),
) -> list[MemoryListRow]:
    """The rows in the Engine's order (by id)."""
    labels = character_labels(cast) if cast is not None else {}
    players = {c.id for c in cast or () if c.is_player}
    rows = []
    for m in memories:
        holder = None
        if m.holder_character_id:
            holder_id = m.holder_character_id
            label = character_label(holder_id, labels, minor) if holder_id in labels else ""
            holder = Holder(holder_id, label, holder_id in players)
        position = None
        url = None
        if m.narrative_turn is not None:
            position = position_text(m.narrative_turn, m.beat)
            url = history_turn_url(story_id, m.narrative_turn)
        pinned = None
        if m.pinned:
            pinned = f"pinned (by {m.pinned_by})" if m.pinned_by else "pinned"
        rows.append(
            MemoryListRow(
                memory=m,
                id_url=_id_url(story_id, m),
                kind_label=humanised(m.kind) if m.kind in KINDS else None,
                tier_label=humanised(m.retrieval_tier) if m.retrieval_tier in TIERS else None,
                retired_reason=m.retired_reason.replace("_", " ") if m.retired_reason else None,
                holder=holder,
                position=position,
                position_url=url,
                pinned_text=pinned,
                quarantine_reason=shown_reason(m.quarantine_reason or ""),
                consolidation=(
                    _consolidation(story_id, m.consolidation) if m.consolidation else None
                ),
                more=_more(m),
            )
        )
    return rows


def unread_entries(body: MemoryList) -> list[Any]:
    """D15: the page's raw entries that didn't parse (none when every entry did)."""
    raw = body.unparsed.get("memories") if body.unparsed else None
    if raw is None:
        return []
    return rejected_entries(raw, ListedMemory.from_json)


def _option_labels(cast: Sequence[Character] | None, minor: frozenset[str]) -> Mapping[str, str]:
    names = cast_names(cast)
    return {c.id: character_label(c.id, names, minor) for c in cast or ()}


def _memory_list(ctx: InspectContext) -> dict[str, Any]:
    request = ctx.request
    cast = ctx.story.cast if ctx.story is not None else None
    minor = story_minor_ids(ctx.story.characters if ctx.story is not None else None)
    form = MemoryListForm(request.GET, cast=cast, labels=_option_labels(cast, minor))
    typed_cursor = request.GET.get(CURSOR, "")
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    params = request.GET.copy()
    if "holder_character_id" in params:
        params["holder_character_id"] = str(form.data.get("holder_character_id") or "")
    params.pop(CURSOR, None)
    if cursor:
        params[CURSOR] = cursor
    filter_url = memory_list_url(ctx.story_id)
    context: dict[str, Any] = {
        "form": form,
        "filter_url": filter_url,
        "memory_url": reverse(Tab.MEMORY.url_name, args=[ctx.story_id]),
        "filters_set": any(request.GET.get(name) for name in MEMORY_LIST_FIELDS),
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "actions_next": request.get_full_path(),
        **paging(params, cursor, None, param=CURSOR),
    }
    if not form.is_valid():
        return context
    try:
        body = ctx.client.list_story_memories(
            ctx.story_id, limit=PAGE_LIMIT, cursor=cursor, **form.query()
        )
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.code in (OMNISCIENT_REQUIRED, VIEWPOINT_UNRESOLVED):
            context["omniscient_required"] = OMNISCIENT_TEXT
        elif isinstance(exc, EngineError) and exc.http_status == 422:
            context["cursor_refused"] = form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and exc.http_status == 404:
            context["not_found"] = NOT_FOUND
        else:
            context["list_error"] = panel_for(exc)
        return context

    filtered = form.any_filter
    unread = unread_entries(body)
    rows = memory_rows(ctx.story_id, body.memories, cast=cast, minor=minor)
    context.update(
        memories=body,
        rows=rows,
        heading=count_heading(len(rows), filtered=filtered),
        unread=unread,
        unread_text=unread_text(len(unread)) if unread else None,
        unread_json=_pretty(unread) if unread else None,
        empty=None if rows or unread else (NONE_FILTERED if filtered else NONE),
        **paging(params, cursor, body.next_cursor, param=CURSOR),
    )
    return context


@require_safe
def memory_list(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:memory-list``, every memory of the story (#236)."""
    return inspect_page(request, story_id, Tab.MEMORY, MEMORY_LIST_TEMPLATE, _memory_list)

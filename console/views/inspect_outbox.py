"""The Effects tab's Outbox section (#325; E2, E10, E12, E17, E21, E22, E23, convention 10, D15).

``inspect:outbox`` (``/stories/<id>/inspect/effects/outbox/``, ``@require_safe``) lists every
outbox row the story committed, whatever its status (Engine #679), through #135's
:func:`console.views.inspect.inspect_page` with ``Tab.EFFECTS``. It is never gated (E12): the
Engine serves the read under every configuration, and a story with States off has no rows. The
read is omniscient-only (E2), so the section shows ``OUTBOX_OMNISCIENT_LINE`` and no viewpoint
picker; a ``viewpoint`` on the URL is ignored and never sent.

**Reads per render**, in order: the shell's effective-config and story reads, then exactly one
``list_outbox(story_id, status=…, provider=…, after_id=…, limit=PAGE_SIZE)`` (blank filters
left out). An invalid filter form makes no read.

**Paging** (E22). The Console's cursor parameter is ``after``, sent as the Engine's
``after_id``. It is trimmed and upper-cased (C13) and must pass ``is_ulid``; otherwise it is
dropped with C12's ``CURSOR_DROPPED`` above the filters and the first page is read. "Next page"
(only with ``next_after``) and "First page" (only when a cursor was sent) are rebuilt from the
cleaned, non-blank filters, so stray parameters (``viewpoint``) are dropped.

**Errors stay in the section** (convention 10), the filter form still rendered: 404 #249's
``NOT_FOUND``; 403 ``omniscient_required`` or 422 ``viewpoint_unresolved`` E2's
``OMNISCIENT_REQUIRED_TEXT`` (a Console bug); a 422 ``validation_error`` naming ``query.status``
or ``query.provider`` the Engine's message beside that field (E21), anything else unmapped
#43's panel. Every error is logged (``log_engine_error``). 503 ``erasure_pending`` is #87's held
wrapper and 409 ``story_importing`` is re-raised to #295's importing middleware (E10).
"""

from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest, HttpResponse, QueryDict
from django.views.decorators.http import require_safe

from console.engine.client import EngineClientError, EngineError
from console.engine_errors import log_engine_error, panel_for
from console.forms.reconciliation import OUTBOX_FILTERS, OutboxFilterForm
from console.held import is_held, render_held
from console.ids import is_ulid
from console.inspect import Tab
from console.outbox import (
    PAGE_SIZE,
    REJECTED_ROW_TEXT,
    SCOPE_LINE,
    count_text,
    empty_text,
    outbox_rows,
    rejected_rows,
    unreadable_text,
)
from console.paging import CURSOR_DROPPED, paging
from console.portability import is_importing
from console.reconciliation import NOT_FOUND
from console.rewrites import OMNISCIENT_REQUIRED_TEXT
from console.views.inspect import InspectContext, inspect_page

TEMPLATE = "console/inspect/outbox.html"
AFTER = "after"
OMNISCIENT_CODES = frozenset({"omniscient_required", "viewpoint_unresolved"})
VALIDATION = "validation_error"
QUERY_PREFIX = "query."
NEXT_TEXT = "Next page"
FIRST_TEXT = "First page"


def _with_query(path: str, query: str) -> str:
    return f"{path}?{query}" if query else path


def _failure_paths(exc: EngineError) -> list[tuple[str, str]]:
    """``(path, message)`` per failure in ``details.errors[*]`` (a ``field`` string or a ``loc``
    list joined with "."), else ``details.field`` with the error's message."""
    details = exc.details if isinstance(exc.details, Mapping) else {}
    found: list[tuple[str, str]] = []
    errors = details.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if not isinstance(item, Mapping):
                continue
            path = item.get("field")
            loc = item.get("loc")
            if not isinstance(path, str) and isinstance(loc, list):
                path = ".".join(str(part) for part in loc)
            if isinstance(path, str):
                message = item.get("message") or item.get("msg")
                found.append((path, message if isinstance(message, str) else exc.message))
    field = details.get("field")
    if not found and isinstance(field, str):
        found.append((field, exc.message))
    return found


def place_validation_error(exc: EngineError, form: OutboxFilterForm) -> bool:
    """E21: place a 422's ``query.status`` / ``query.provider`` failures beside their fields.
    True when every failure was placed; anything unmapped (``query.after_id``, ``query.limit``)
    leaves it False, and the caller shows #43's panel."""
    failures = _failure_paths(exc)
    placed = 0
    for path, message in failures:
        name = path.removeprefix(QUERY_PREFIX) if path.startswith(QUERY_PREFIX) else ""
        if name in OUTBOX_FILTERS:
            form.add_error(name, message)
            placed += 1
    return bool(failures) and placed == len(failures)


def _section(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    path = request.path
    form = OutboxFilterForm(request.GET)
    typed = request.GET.get(AFTER, "").strip().upper()
    cursor = typed if is_ulid(typed) else None
    context: dict[str, Any] = {
        "scope_line": SCOPE_LINE,
        "filter_form": form,
        "filter_url": path,
        "clear_url": path if form.filtered or typed else None,
        "cursor_dropped": CURSOR_DROPPED if typed and cursor is None else None,
        "list_read": False,
        "first_text": FIRST_TEXT,
        "next_text": NEXT_TEXT,
        "rejected_text": REJECTED_ROW_TEXT,
        "first_url": None,
        "next_url": None,
    }
    if not form.is_valid():
        return context
    filters = form.query()
    params = QueryDict(mutable=True)
    for name, value in filters.items():
        params[name] = value
    try:
        page = ctx.client.list_outbox(
            ctx.story_id,
            status=filters.get("status"),
            provider=filters.get("provider"),
            after_id=cursor,
            limit=PAGE_SIZE,
        )
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, ctx.story_id, exc)
        if is_importing(exc):
            raise
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 404:
            context["not_found"] = NOT_FOUND
        elif isinstance(exc, EngineError) and exc.code in OMNISCIENT_CODES:
            context["omniscient_required"] = OMNISCIENT_REQUIRED_TEXT
        elif (
            isinstance(exc, EngineError)
            and exc.code == VALIDATION
            and place_validation_error(exc, form)
        ):
            pass  # the messages are beside their fields
        else:
            context["outbox_error"] = panel_for(exc)
        return context
    rows = outbox_rows(page.rows, ctx.story_id)
    rejected = rejected_rows(page.unparsed)
    links = paging(params, cursor, page.next_after, param=AFTER)
    filtered = bool(filters)
    context.update(
        list_read=True,
        rows=rows,
        rejected=rejected,
        count=count_text(len(rows), filtered=filtered) if rows else None,
        unreadable=unreadable_text(len(rejected)) if rejected else None,
        empty=(empty_text(cursor=cursor, filtered=filtered) if not rows and not rejected else None),
        next_url=_with_query(path, links["next_query"]) if links["next_query"] else None,
        first_url=_with_query(path, links["first_query"]) if links["paged"] else None,
    )
    return context


@require_safe
def outbox(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:outbox``: every outbox row the story committed, a page at a time (#325)."""
    return inspect_page(request, story_id, Tab.EFFECTS, TEMPLATE, _section)

"""A story's Operations page (#324; owner answer 13a; nathanpond/Epistrel#681; C3, C11,
convention 4/6/10, D13, D15, D19, E10, E11, E15, E22, E23).

``stories:operations-list`` (``/stories/<id>/operations/``, ``@require_safe``) lists every
operation the Engine ran on the story, whoever asked for it, through #296's
:func:`console.views.story_pages.story_page`. Its path sits beside ``operations/<str:…>/poll/``
without shadowing it (E11). Nothing here writes.

**Reads per render**, in order: the frame's two, then one ``list_operations(story_id,
limit=OPERATIONS_PAGE, after_id=<cursor or None>, **form.query())``, then **one local query**:
the :class:`~console.models.EngineOperation` rows of this story whose ``engine_operation_id`` is
among the page's ids (the Console-log links), skipped when the page has no rows. No other
Engine read.

**Filters** are :class:`~console.forms.story_operations.StoryOperationsForm` (E22): any field
error makes no list read. The cursor is ``after_id`` (convention 6's
:func:`console.paging.paging`): one failing ``is_ulid`` is dropped with ``CURSOR_DROPPED`` and
never sent. Next keeps every filter, First drops the cursor, Filter and Clear filters carry none.

**Rows** (:func:`operation_rows`) keep the Engine's order (by id). The label cell is #257's
:func:`console.operation_log.engine_label_value` / ``engine_label_text`` rule, so a staged turn
reads D13's "Staged turn" here as on the Console's own log. The Operation cell links #250's page
through D19's :func:`console.effects.engine_operation_url` (plain ``engine_id`` while
unregistered); the Turn cell links History (convention 3); the Console-log cell links the newest
local row (#44) for that Engine id.

**Errors stay in the page** (convention 10); the filters always render. A 422
``validation_error`` naming a filter (``query.label`` … ``query.until``, from
``details.errors[*]``'s ``field`` or ``loc``, else ``details.field``) is the Engine's message
beside that field; ``query.after_id`` is the message above the filters with "First page"; any
other 422 the message above the filters. A 404 is the frame's not-found page (status 404). 409
``story_importing`` and 503 ``erasure_pending`` are re-raised to their middlewares (E10, #87).
Anything else is #43's panel below the filters.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse, QueryDict
from django.urls import reverse
from django.views.decorators.http import require_safe

from console import effects
from console.engine.client import EngineClientError, EngineError
from console.engine.models import OperationSummary, StoryOperations
from console.engine_errors import log_engine_error, panel_for
from console.forms.story_operations import FILTERS, StoryOperationsForm, kind_text
from console.held import is_held
from console.ids import is_ulid
from console.inspect import history_turn_url
from console.models import EngineOperation
from console.operation_log import engine_label_text, engine_label_value
from console.paging import CURSOR_DROPPED, paging
from console.portability import is_importing
from console.raw import plural
from console.views.story_pages import StoryBuild, StoryPageContext, story_page
from console.wording import humanised

TEMPLATE = "console/stories/operations.html"
CURRENT = "stories:operations-list"
OPERATIONS_PAGE = 50  # convention 6: the Engine's default (it allows 200)
AFTER_ID = "after_id"
QUERY_PREFIX = "query."
VALIDATION = "validation_error"
EMPTY = "—"

SCOPE_TEXT = "Every operation the Engine ran on this story, whoever asked for it"
LOG_LINK_TEXT = "This Console's own log for this story"
COUNT_SUFFIX = " on this page"
FILTERED_SUFFIX = ", matching filters"
UNPARSED_TEXT = "Some operations couldn't be read"
OPERATION_REJECTED_TEXT = "This operation couldn't be read"
NO_OPERATIONS = "The Engine has no operations for this story"
NO_OPERATIONS_MATCH = "No operations match these filters"
NO_MORE_OPERATIONS = "No more operations after this one"
NEXT_TEXT = "Next page"
FIRST_TEXT = "First page"


@dataclass(frozen=True)
class OperationRow:
    """One operation as the table shows it."""

    operation_id: str
    operation_url: str | None
    kind: str
    kind_text: str
    label: str | None  # the raw Engine label, for ``title``
    label_text: str
    status: str
    status_text: str
    turn_text: str | None
    turn_url: str | None
    principal: str
    started_at: str
    committed_at: str | None
    log_pk: int | None
    log_url: str | None


def turn_text(turn: int, variant: int | None) -> str:
    """ "turn N · vV", the variant only when it is set and not 0."""
    return f"turn {turn:,}" + (f" · v{variant:,}" if variant else "")


def operation_row(op: OperationSummary, story_id: str, log_rows: Mapping[str, int]) -> OperationRow:
    turn = op.narrative_turn
    log_pk = log_rows.get(op.operation_id)
    return OperationRow(
        operation_id=op.operation_id,
        operation_url=effects.engine_operation_url(op.operation_id),
        kind=op.kind,
        kind_text=kind_text(op.kind),
        label=op.label or None,
        label_text=engine_label_text(engine_label_value(op)) or EMPTY,
        status=op.status,
        status_text=humanised(op.status),
        turn_text=turn_text(turn, op.turn_variant) if turn is not None else None,
        turn_url=(
            history_turn_url(story_id, turn, op.turn_variant or None) if turn is not None else None
        ),
        principal=op.principal,
        started_at=op.started_at,
        committed_at=op.committed_at,
        log_pk=log_pk,
        log_url=reverse("operations:detail", args=[log_pk]) if log_pk is not None else None,
    )


def operation_rows(
    page: StoryOperations, story_id: str, log_rows: Mapping[str, int]
) -> list[OperationRow]:
    """The page's parsed operations, in the Engine's order. ``log_rows`` maps an Engine
    operation id to the newest local log row's pk."""
    return [operation_row(op, story_id, log_rows) for op in page.operations]


def rejected_operations(page: StoryOperations) -> list[Any]:
    """The raw entries the lenient list couldn't read (D15)."""
    rejected = page.unparsed.get("operations")
    return list(rejected) if isinstance(rejected, list) else []


def log_rows_for(story_id: str, ids: list[str]) -> dict[str, int]:
    """One local query: the newest log row's pk per Engine operation id of this story."""
    newest: dict[str, int] = {}
    if not ids:
        return newest
    found = EngineOperation.objects.filter(
        story_id=story_id, engine_operation_id__in=ids
    ).values_list("engine_operation_id", "pk")
    for engine_id, pk in found:
        if engine_id and pk > newest.get(engine_id, 0):
            newest[engine_id] = pk
    return newest


def empty_text(*, cursor: str | None, filtered: bool) -> str:
    """E22's order: a cursor page, then filtered, then unfiltered."""
    if cursor:
        return NO_MORE_OPERATIONS
    return NO_OPERATIONS_MATCH if filtered else NO_OPERATIONS


def _failure_paths(exc: EngineError) -> list[tuple[str, str]]:
    """``(path, message)`` per failure: ``details.errors[*]`` (a ``field`` string or a ``loc``
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


def place_validation_error(exc: EngineError, form: StoryOperationsForm) -> tuple[list[str], bool]:
    """Place a 422's failures: a ``query.<filter>`` beside its field; ``query.after_id`` and
    anything else above the filters. Returns the messages above and whether the cursor was
    named (the page then offers "First page")."""
    above: list[str] = []
    cursor = False
    failures = _failure_paths(exc) if exc.code == VALIDATION else []
    for path, message in failures:
        name = path.removeprefix(QUERY_PREFIX) if path.startswith(QUERY_PREFIX) else ""
        if name in FILTERS:
            form.add_error(name, message)
        else:
            cursor = cursor or name == AFTER_ID
            above.append(message)
    if not failures:
        above.append(exc.message)
    return above, cursor


def _with_query(path: str, query: str) -> str:
    return f"{path}?{query}" if query else path


def log_list_url(story_id: str) -> str:
    """The Console's own Operations log (#44), filtered to the story."""
    query = QueryDict(mutable=True)
    query["story"] = story_id
    return _with_query(reverse("operations:list"), query.urlencode())


def _passed_on(exc: EngineClientError) -> bool:
    return is_held(exc) or is_importing(exc)


@dataclass
class _Status:
    """The build's status for the frame's response (a 404 renders inside the frame)."""

    code: int = 200


def _build(status: _Status) -> StoryBuild:
    def build(ctx: StoryPageContext) -> dict[str, Any]:
        request = ctx.request
        path = request.path
        form = StoryOperationsForm(request.GET)
        typed_cursor = request.GET.get(AFTER_ID, "")
        cursor = typed_cursor if is_ulid(typed_cursor) else None
        filtered = any(str(request.GET.get(name, "")).strip() for name in FILTERS)
        context: dict[str, Any] = {
            "filter_form": form,
            "filter_url": path,
            "clear_url": path if filtered else None,
            "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
            "scope_text": SCOPE_TEXT,
            "log_url": log_list_url(ctx.story_id),
            "log_link_text": LOG_LINK_TEXT,
            "list_read": False,
            "refusals": [],
            "first_url": None,
            "next_url": None,
            "first_text": FIRST_TEXT,
            "next_text": NEXT_TEXT,
            "rejected_text": OPERATION_REJECTED_TEXT,
        }
        if not form.is_valid():
            return context
        params = QueryDict(mutable=True)
        for name, value in form.raw_filters().items():
            params[name] = value
        first_url = _with_query(path, paging(params, None, None, param=AFTER_ID)["first_query"])
        try:
            page = ctx.client.list_operations(
                ctx.story_id, limit=OPERATIONS_PAGE, after_id=cursor, **form.query()
            )
        except EngineClientError as exc:
            if _passed_on(exc):
                raise
            log_engine_error(request, exc)
            if isinstance(exc, EngineError) and exc.http_status == 404:
                status.code = 404
                context.update(story_not_found=True, page_links=[])  # convention 10
                return context
            if isinstance(exc, EngineError) and exc.http_status == 422:
                above, cursor_named = place_validation_error(exc, form)
                context["refusals"] = above
                if cursor_named:
                    context["cursor_refused"] = True
                    context["first_url"] = first_url
                return context
            context["list_error"] = panel_for(exc)
            return context
        rows: list[OperationRow] = []
        if page.operations:
            log_rows = log_rows_for(ctx.story_id, [op.operation_id for op in page.operations])
            rows = operation_rows(page, ctx.story_id, log_rows)
        rejected = rejected_operations(page)
        links = paging(params, cursor, page.next_after, param=AFTER_ID)
        count = ""
        if rows:
            count = (
                plural(len(rows), "operation")
                + COUNT_SUFFIX
                + (FILTERED_SUFFIX if form.query() else "")
            )
        context.update(
            list_read=True,
            rows=rows,
            rejected=rejected,
            unparsed_text=UNPARSED_TEXT if rejected else "",
            count_text=count,
            empty_text=(
                empty_text(cursor=cursor, filtered=bool(form.query()))
                if not rows and not rejected
                else ""
            ),
            next_url=_with_query(path, links["next_query"]) if links["next_query"] else None,
            first_url=first_url if links["paged"] else None,
        )
        return context

    return build


@require_safe
def operations_list(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:operations-list``: the story's operations, as the Engine lists them (#324)."""
    status = _Status()
    response = story_page(request, story_id, TEMPLATE, _build(status), current=CURRENT)
    if status.code != 200:
        response.status_code = status.code
    return response

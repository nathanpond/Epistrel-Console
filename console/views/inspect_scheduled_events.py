"""The World tab's Scheduled events section (#244; D1, D3, D4, D6, D15).

``inspect:scheduled-events`` (``/stories/<id>/inspect/world/scheduled-events/``) renders through
#135's :func:`console.views.inspect.inspect_page` with ``Tab.WORLD``. The section is ungated (D1:
the Engine serves the list under every configuration, the clock off too). Reads per render, in
order:

1. the shell's effective-config and story reads;
2. one ``GET /stories/{id}/scheduled-events?viewpoint=<form value>`` (D4's
   :class:`~console.forms.world.WorldViewpointForm`: omniscient by default, never the story's
   default viewpoint; ``?viewpoint=`` is undeclared in the Engine's OpenAPI, D18, Engine bug
   nathanpond/Epistrel#682, and sent anyway);
3. one ``GET /stories/{id}/clock`` for the "Now" and stops lines. Its failure (held included)
   is the plain "Couldn't read the clock" line, logged, and never fails the list.

A missing or empty ``viewpoint`` is the initial omniscient; any other value outside the choices
is ``VIEWPOINT_NOT_IN_CAST`` and makes neither read 2 nor read 3. A failed events read (404,
422, anything else) skips the clock read and the header lines; the picker and the story name
stay (convention 10). A 422 ``viewpoint_unresolved`` lands on the field; a 503
``erasure_pending`` on the events read escapes to #87's held wrapper. The Engine's
``output_viewpoint`` is ignored: the viewpoint line follows the form's value.

The row partial's ``actions`` hook (``Mapping[event_id, RowActions] | None``) is #262's; this
story leaves it ``None`` (no Actions column).
"""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_safe

from console.canon import cast_names
from console.cast import OMNISCIENT
from console.engine.client import EngineClientError, EngineError
from console.engine.models import ScheduledEvent, WorldClock, rejected_entries
from console.engine_errors import log_engine_error, panel_for
from console.forms.world import WorldViewpointForm
from console.held import is_held
from console.inspect import Tab
from console.paging import CAST_UNAVAILABLE
from console.scheduled_events import (
    CLOCK_UNREAD,
    EVENTS_NOTE,
    EVENTS_OMNISCIENT_NOTE,
    count_text,
    empty_text,
    event_rows,
    off_line,
    partitions_note,
    stops_line,
    unparsed_text,
)
from console.views.inspect import InspectContext, inspect_page
from console.world import viewpoint_line

TEMPLATE = "console/inspect/scheduled_events.html"
NOT_FOUND = "Not found (it may have been deleted or truncated)"


def _read_error(
    ctx: InspectContext, exc: EngineClientError, form: WorldViewpointForm
) -> dict[str, Any]:
    """Convention 10's in-section error for the events read (held re-raised to the
    middleware)."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if isinstance(exc, EngineError) and exc.http_status == 422:
        form.apply_engine_error(exc)
        return {}
    if isinstance(exc, EngineError) and exc.http_status == 404:
        return {"not_found": NOT_FOUND}
    return {"events_error": panel_for(exc)}


def _clock(ctx: InspectContext) -> WorldClock | None:
    """The one clock read; a failed or held one is logged and ``None``."""
    try:
        return ctx.client.get_clock(ctx.story_id)
    except EngineClientError as exc:
        log_engine_error(ctx.request, exc)
        return None


def _section(ctx: InspectContext) -> dict[str, Any]:
    cast = ctx.story.cast if ctx.story is not None else None
    names = cast_names(cast)
    data = ctx.request.GET.copy()
    if not data.get("viewpoint"):
        data["viewpoint"] = OMNISCIENT  # a missing or empty param is the initial omniscient
    form = WorldViewpointForm(data, cast=cast)
    context: dict[str, Any] = {
        "form": form,
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "actions": None,  # #262: Reschedule and Cancel, keyed by event_id
        "actions_template": None,
    }
    if not form.is_valid():
        return context
    viewpoint = form.query()["viewpoint"]
    try:
        body = ctx.client.list_scheduled_events(ctx.story_id, viewpoint=viewpoint)
    except EngineClientError as exc:
        context.update(_read_error(ctx, exc, form))
        return context
    clock = _clock(ctx)
    raw = body.unparsed.get("events") if body.unparsed else None
    rejected = rejected_entries(raw, ScheduledEvent.from_json) if raw is not None else []
    context.update(
        events=body,
        viewpoint_line=viewpoint_line(
            viewpoint, names, note=EVENTS_NOTE, omniscient_note=EVENTS_OMNISCIENT_NOTE
        ),
        partitions_note=partitions_note(viewpoint, ctx.effective),
        clock=clock,
        clock_unread=CLOCK_UNREAD if clock is None else None,
        stops=stops_line(ctx.effective, clock, ctx.story_id) if clock is not None else None,
        off_line=off_line(ctx.effective),
        count=count_text(len(body.events), body.timeline_revision),
        rows=event_rows(body.events, names, ctx.story_id),
        rejected=rejected,
        unparsed_text=unparsed_text(len(rejected)) if rejected else None,
        empty=None if body.events or rejected else empty_text(viewpoint, names),
    )
    return context


@require_safe
def scheduled_events(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:scheduled-events``, the story's scheduled events for one viewpoint (#244)."""
    return inspect_page(request, story_id, Tab.WORLD, TEMPLATE, _section)

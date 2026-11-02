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

``inspect:scheduled-event-new`` (``world/scheduled-events/new/``, GET/HEAD/POST, #253; D1, D6, D8,
D9) is the New scheduled event form, below the section (``current="inspect:scheduled-events"``).
GET makes only the shell's two reads: :class:`~console.forms.scheduled_events.ScheduledEventForm`
with the shell's cast (``None`` when the story read failed: no character lists, C12's
``CAST_UNAVAILABLE``), a fresh idempotency key and D8's ``expected_revision`` from the shell's
``Story.timeline_revision``. The submit is greyed with D1's reason while ``m6_gate(CLOCK, …)`` is
closed. A POST: ``gate_refusal`` on ``proposition``/``planned`` (a refusal re-renders, no write),
then the form (invalid: re-render, no write), then one ``create_scheduled_event`` with the posted
``expected_revision`` and the page's key. The result page renders on the POST response.
Refusals follow D8's re-render rule: an answered refusal rebinds the form to a copy of the POST
with a fresh key and the POST's shell revision; local invalid input and an unknown outcome keep
the posted ones. Placement: D9's ``world_refusal`` with ``field_map={"to": "planned"}`` (beside
the field it names when the form has it, else above the form; ``module_off`` above it with the
Config link), then request-validation errors through ``apply_engine_errors`` (indexed list paths
land on the list field), then #43's panel.

``inspect:scheduled-event-written?report=<pk>`` (``world/scheduled-events/written/``) renders a
create, reschedule or cancel result again from its #44 row (:func:`console.scheduled_events.
written_row`; #262 shares the page): "<verb>: <name>", the event through
``_scheduled_event_row.html``, D8's lines from ``slot_result`` (no "Slot:" line:
``ScheduledEventWritten`` carries none) and "Back to Scheduled events" to ``#ev-<event_id>``.

The row partial's ``actions`` hook (``Mapping[event_id, RowActions] | None``) is #262's; this
story leaves it ``None`` (no Actions column).
"""

import dataclasses
from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.canon import cast_names
from console.cast import OMNISCIENT
from console.confirm import KEY_FIELD, posted_idempotency_key
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import (
    ScheduledEvent,
    ScheduledEventWritten,
    WorldClock,
    rejected_entries,
)
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.scheduled_events import CHARACTER_FIELDS, MAX_CHARACTERS, ScheduledEventForm
from console.forms.world import WorldViewpointForm
from console.held import is_held
from console.ids import new_ulid
from console.inspect import CLOCK, Tab, gate_refusal, m6_gate
from console.models import EngineOperation
from console.paging import CAST_UNAVAILABLE
from console.scheduled_events import (
    CLOCK_UNREAD,
    EVENTS_NOTE,
    EVENTS_OMNISCIENT_NOTE,
    WRITE_KINDS,
    count_text,
    empty_text,
    event_row,
    event_rows,
    off_line,
    partitions_note,
    stops_line,
    unparsed_text,
    written_row,
)
from console.views.inspect import TEMPLATE_KEY, InspectContext, inspect_page
from console.world import (
    EXPECTED_REVISION_FIELD,
    expected_revision,
    slot_result,
    viewpoint_line,
    world_refusal,
)

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
        # #253: the New link, in every state of the section (it needs no events read).
        "new_url": new_url(ctx.story_id),
        "new_gate": m6_gate(CLOCK, ctx.effective),
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


# --- create (#253) and the shared result page (#253, #262) ----------------------------------------

SECTION = "inspect:scheduled-events"
FORM_TEMPLATE = "console/inspect/scheduled_event_form.html"
WRITTEN_TEMPLATE = "console/inspect/scheduled_event_written.html"
CREATE_KIND = "create_scheduled_event"
CREATE_VERB = "schedule an event"
REPORT_PARAM = "report"
BAD_RESPONSE = "bad_response"
FIELD_MAP: Mapping[str, str] = {"to": "planned"}  # D9: the Engine names the time ``to``
REFUSAL_LINKS: Mapping[str, str] = {"module_off": "Config"}
# D1: a create is gated on the clock at ``prompt_only``.
CREATE_GATED: Mapping[str, tuple[str, str | None]] = {
    name: (CLOCK, "prompt_only") for name in ("proposition", "planned")
}
# Indexed list paths (``participants[3]``) land on the list field.
LIST_ALIASES: Mapping[str, str] = {
    f"{name}[{i}]": name for name in CHARACTER_FIELDS for i in range(MAX_CHARACTERS)
}


def section_url(story_id: str) -> str:
    return reverse(SECTION, args=[story_id])


def new_url(story_id: str) -> str:
    return reverse("inspect:scheduled-event-new", args=[story_id])


def written_url(story_id: str, pk: int) -> str:
    """``inspect:scheduled-event-written?report=<pk>``."""
    return f"{reverse('inspect:scheduled-event-written', args=[story_id])}?{REPORT_PARAM}={pk}"


def _story_revision(ctx: InspectContext) -> int | None:
    """D8: the shell's story read's ``timeline_revision`` (``None`` when that read failed)."""
    revision = ctx.story.timeline_revision if ctx.story is not None else None
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else None


def _written(
    ctx: InspectContext,
    answer: ScheduledEventWritten,
    row: EngineOperation | None,
    *,
    verb: str,
    raw: Any,
) -> dict[str, Any]:
    """The result page's context (on the POST response, and for ``?report=``)."""
    story_id = ctx.story_id
    cast = ctx.story.cast if ctx.story is not None else None
    event = event_row(answer.event, cast_names(cast), story_id)
    back = f"{section_url(story_id)}#ev-{event.event_id}"
    return {
        TEMPLATE_KEY: WRITTEN_TEMPLATE,
        "verb": verb,
        "row": event,
        "rows": [event],
        "actions": None,
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "slot": slot_result(story_id, answer, row, back),
        "replayed": row is not None and row.replayed,
        "again_url": new_url(story_id),
        "back_url": back,
        "report_url": written_url(story_id, row.pk) if row is not None else None,
        "raw_answer": raw,
    }


def _written_report(ctx: InspectContext) -> dict[str, Any]:
    found = written_row(ctx.request.GET.get(REPORT_PARAM), ctx.story_id)
    if found is None:
        return {"missing": NOT_FOUND, "back_url": section_url(ctx.story_id)}
    row, answer = found
    return _written(ctx, answer, row, verb=WRITE_KINDS[row.kind], raw=row.response)


@require_safe
def scheduled_event_written(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:scheduled-event-written?report=<pk>``: a scheduled-event write's result page
    again, from the #44 row (#253; #262's reschedule and cancel too)."""
    return inspect_page(
        request, story_id, Tab.WORLD, WRITTEN_TEMPLATE, _written_report, current=SECTION
    )


def _refreshed(ctx: InspectContext, form: ScheduledEventForm) -> ScheduledEventForm:
    """D8: the form rebound to a copy of the POST with a fresh key and, when the POST's story
    read gave one, the refreshed revision."""
    data = ctx.request.POST.copy()
    data[KEY_FIELD] = new_ulid()  # an answered refusal: a new attempt is a new write
    revision = _story_revision(ctx)
    if revision is not None:
        data[EXPECTED_REVISION_FIELD] = str(revision)
    refreshed = ScheduledEventForm(data, cast=form.cast, initial_revision=revision)
    refreshed.is_valid()
    return refreshed


def _create_refused(
    ctx: InspectContext,
    exc: EngineClientError,
    form: ScheduledEventForm,
    page: dict[str, Any],
) -> dict[str, Any]:
    """A refused create: the form again (D8's key and revision rule), the refusal placed."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )
    if unknown or not isinstance(exc, EngineError):
        page["write_error"] = panel_for(exc)  # the outcome is unknown: the posted key and revision
        return page
    form = _refreshed(ctx, form)
    page["form"] = form
    refusal = world_refusal(exc, ctx.story_id, CREATE_VERB, ctx.effective, field_map=FIELD_MAP)
    if refusal is not None:
        if refusal.field in form.fields and not refusal.url:
            form.add_error(refusal.field, refusal.text)
        else:
            page.update(
                refusal=refusal.text,
                refusal_url=refusal.url,
                refusal_link=REFUSAL_LINKS.get(exc.code, "See the check") if refusal.url else "",
            )
        return page
    if exc.http_status == 422 and apply_engine_errors(exc, form, aliases=LIST_ALIASES):
        return page
    page["write_error"] = panel_for(exc)
    return page


def _create(ctx: InspectContext) -> dict[str, Any]:
    request = ctx.request
    story_id = ctx.story_id
    cast = ctx.story.cast if ctx.story is not None else None
    page: dict[str, Any] = {
        "back_url": section_url(story_id),
        "action_url": new_url(story_id),
        "gate": m6_gate(CLOCK, ctx.effective),
        "cast_unavailable": CAST_UNAVAILABLE if cast is None else None,
    }
    if request.method != "POST":
        page["form"] = ScheduledEventForm(cast=cast, initial_revision=_story_revision(ctx))
        return page
    form = ScheduledEventForm(request.POST, cast=cast, initial_revision=_story_revision(ctx))
    page["form"] = form
    refused = gate_refusal(request.POST, ctx.effective, CREATE_GATED)
    if refused is not None:
        page["refusal"] = refused.reason
        return page
    if not form.is_valid():
        return page
    try:
        answer = ctx.client.create_scheduled_event(
            story_id,
            form.body(),
            expected_revision=expected_revision(request.POST),
            idempotency_key=posted_idempotency_key(request),
        )
    except EngineClientError as exc:
        return _create_refused(ctx, exc, form, page)
    pk = getattr(ctx.client, "last_operation_id", None)
    row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
    raw = row.response if row is not None else dataclasses.asdict(answer)
    return _written(ctx, answer, row, verb=WRITE_KINDS[CREATE_KIND], raw=raw)


@require_http_methods(["GET", "HEAD", "POST"])
def scheduled_event_new(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:scheduled-event-new``: the New scheduled event form, then ``POST
    /stories/{id}/scheduled-events`` and the result page on the POST response (#253)."""
    return inspect_page(request, story_id, Tab.WORLD, FORM_TEMPLATE, _create, current=SECTION)

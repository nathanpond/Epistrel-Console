"""Set a State between turns (#251; D1, D5, D7, D8, D9), below the World tab's States section.

``inspect:state-set`` (``/stories/<id>/inspect/world/state/<state_name:name>/set/``, GET/HEAD/
POST) renders through :func:`console.views.inspect.inspect_page` with ``Tab.WORLD`` and
``current="inspect:states"`` (D3), so with the States gate closed (D1, ``prompt_only``) the shell
renders the inline greyed panel, GET and POST alike, before any State read or write; ``build``
checks ``module_gate(STATES, …)`` again first (the one-State page's rule).

Reads per render, GET and POST: the shell's effective-config and story reads, then one
``get_state(story_id, name, viewpoint="omniscient")`` (a set isn't a viewpoint matter). Its
``type``, ``scope``, ``unit`` and ``owner`` build :class:`console.forms.states.StateSetForm`,
and its ``timeline_revision`` fills D8's hidden ``expected_revision``. The Engine never reads
``world_clock`` through this route (404), so that name gets D9's ``reserved_state`` sentence
with no read; an ``owner: external`` State gets D9's ``READONLY_EXTERNAL_TEXT`` and a type
outside number, boolean, enum and text "The Console can't set a State of type `<type>`", each
with no form and, on a POST, no PUT.

A valid POST calls ``set_state`` once with the form's body, ``expected_revision`` from the
**posted** hidden field (the read the operator saw; none sent when missing or non-integer) and
the page's idempotency key; the client waits ``turn_timeout(None)`` (a slot write queues behind a
running turn). The result page (``state_written.html``) renders on the POST response and again at
``inspect:state-written?report=<pk>`` from the #44 row (convention 8), which makes no State read
and is not gated by the States module (``gate_section=False``): its values render through D5
with no type or unit.

Refusals re-render the form with every input kept, by D8's rule: an answered refusal gets a fresh
key and ``expected_revision`` from the POST's own State read; a local invalid form keeps the
posted key and revision; a transport error or ``bad_response`` keeps both and shows #43's panel.
This story's own wording comes first: ``validation_error`` ``delta``/``undefined`` →
``NO_VALUE_YET`` beside Amount. Then D9's ``world_refusal`` (the read State's type and unit):
beside the field it names, else above the form with its link (Config for ``module_off``, the
paused check for ``story_paused``). A 404 is convention 10's not-found page; 503
``erasure_pending`` escapes to #87's held wrapper; ``None`` (``idempotency_conflict``) is #43's
panel above the kept form.
"""

import dataclasses
from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.canon import cast_names
from console.cast import OMNISCIENT
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import StateWritten, StoryState
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.states import CHANGE_BY, NO_VALUE_YET, StateSetForm
from console.held import is_held
from console.ids import new_ulid
from console.inspect import STATES, Tab, is_module_off, module_gate
from console.models import EngineOperation
from console.paging import CAST_UNAVAILABLE
from console.states import (
    CLOCK_TYPE,
    EXTERNAL_OWNER,
    SETTABLE_TYPES,
    WORLD_CLOCK,
    clock_url,
    set_url,
    state_row,
    state_url,
    states_url,
)
from console.views.inspect import TEMPLATE_KEY, InspectContext, inspect_page
from console.wording import character_label
from console.world import (
    EXPECTED_REVISION_FIELD,
    READONLY_EXTERNAL_TEXT,
    RESERVED_STATE_TEXT,
    change_text,
    expected_revision,
    five_part,
    signed_delta_text,
    slot_result,
    world_refusal,
)

SET_TEMPLATE = "console/inspect/state_set.html"
WRITTEN_TEMPLATE = "console/inspect/state_written.html"
WRITE_KIND = "set_state"
REPORT_PARAM = "report"
VERB_PHRASE = "set a State"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
UNSETTABLE_TYPE = "The Console can't set a State of type `{type}`"
STORY_REVISION = "Story revision {n}"
BAD_RESPONSE = "bad_response"
UNDEFINED_REASON = "undefined"
# The label of a refusal's link: convention 24's Config, else the paused story's check.
REFUSAL_LINKS: Mapping[str, str] = {"module_off": "Config"}
PAUSED_LINK = "See the check"


def written_url(story_id: str, name: str, pk: int) -> str:
    """``inspect:state-written?report=<pk>`` for ``name``."""
    return f"{reverse('inspect:state-written', args=[story_id, name])}?{REPORT_PARAM}={pk}"


def _cast(ctx: InspectContext) -> tuple[Any, dict[str, str], list[str]]:
    cast = ctx.story.cast if ctx.story is not None else None
    return cast, cast_names(cast), [c.id for c in cast or ()]


def _cannot_set(name: str, state: StoryState | None) -> tuple[str, str | None] | None:
    """The sentence (and link) shown in place of the form, or ``None`` when the Console can set
    the State."""
    if name == WORLD_CLOCK or (state is not None and state.type == CLOCK_TYPE):
        return RESERVED_STATE_TEXT, None  # the link is the template's clock_url
    if state is None:
        return None
    if state.owner == EXTERNAL_OWNER:
        return READONLY_EXTERNAL_TEXT, None
    if state.type not in SETTABLE_TYPES:
        return UNSETTABLE_TYPE.replace("{type}", str(state.type).replace("`", "")), None
    return None


# --- the result page ------------------------------------------------------------------------


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _written(
    ctx: InspectContext,
    name: str,
    result: StateWritten,
    row: EngineOperation | None,
    *,
    body: Mapping[str, Any],
    state: StoryState | None,
    raw: Any,
) -> dict[str, Any]:
    """The result page's context (on the POST response with the read State's type and unit;
    for ``?report=`` without them)."""
    story_id = ctx.story_id
    _, names, _ = _cast(ctx)
    value_type = state.type if state is not None else None
    unit = state.unit if state is not None else None
    scope_key = body.get("scope_key") if isinstance(body.get("scope_key"), str) else None
    reason = body.get("reason") if isinstance(body.get("reason"), str) else None
    change = change_text(result.old_value, result.new_value, value_type, unit)
    delta = body.get(CHANGE_BY)
    value_line = (
        f"Changed by {signed_delta_text(delta, unit)} ({change})"
        if CHANGE_BY in body and _number(delta)
        else f"Value: {change}"
    )
    position = five_part(result.slot)
    as_of_url = None
    if position is not None:
        boundary = f"{position.turn}+{position.slot_k}" if position.slot_k else f"{position.turn}"
        as_of_url = state_url(story_id, name, as_of=boundary)
    back = states_url(story_id, None)
    return {
        TEMPLATE_KEY: WRITTEN_TEMPLATE,
        "state_name": name,
        "written": result,
        "character": character_label(scope_key, names) if scope_key else None,
        "value_line": value_line,
        "reason": reason,
        "slot": slot_result(story_id, result, row, back),
        "replayed": row is not None and row.replayed,
        "as_of_url": as_of_url,
        "again_url": set_url(story_id, name, scope_key),
        "back_url": back,
        "report_url": written_url(story_id, name, row.pk) if row is not None else None,
        "raw_answer": raw,
    }


def written_row(
    pk: object, story_id: str, name: str
) -> tuple[EngineOperation, StateWritten] | None:
    """The #44 row ``pk`` and its answer when it is an ``ok`` ``set_state`` of this story whose
    stored response parses and names ``name``; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    if (
        row.kind != WRITE_KIND
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    parsed = StateWritten.from_json(row.response)
    if parsed is None or parsed.state != name:
        return None
    return row, parsed


def _stored_body(row: EngineOperation) -> Mapping[str, Any]:
    stored = row.request if isinstance(row.request, dict) else {}
    body = stored.get("body")
    return body if isinstance(body, dict) else {}


def _report(name: str) -> Any:
    def build(ctx: InspectContext) -> dict[str, Any]:
        found = written_row(ctx.request.GET.get(REPORT_PARAM), ctx.story_id, name)
        if found is None:
            return {
                "state_name": name,
                "missing": NOT_FOUND,
                "back_url": states_url(ctx.story_id, None),
            }
        row, result = found
        return _written(
            ctx, name, result, row, body=_stored_body(row), state=None, raw=row.response
        )

    return build


@require_safe
def state_written(request: HttpRequest, story_id: str, name: str) -> HttpResponse:
    """``inspect:state-written?report=<pk>``: a set's result page again, from the #44 row."""
    return inspect_page(
        request,
        story_id,
        Tab.WORLD,
        WRITTEN_TEMPLATE,
        _report(name),
        current="inspect:states",
        gate_section=False,
    )


# --- the form page --------------------------------------------------------------------------


def _refused(
    ctx: InspectContext,
    name: str,
    exc: EngineClientError,
    state: StoryState,
    revision: int,
    form: StateSetForm,
    page: dict[str, Any],
) -> dict[str, Any]:
    """A refused set: the form again (D8's key and revision rule), the refusal placed."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )
    if unknown or not isinstance(exc, EngineError):
        page["write_error"] = panel_for(exc)  # the outcome is unknown: the posted key and revision
        return page
    if exc.http_status == 404:
        return {"state_name": name, "missing": NOT_FOUND, "back_url": page["back_url"]}
    page.update(idempotency_key=new_ulid(), expected_revision=str(revision))
    details = exc.details if isinstance(exc.details, Mapping) else {}
    field = details.get("field")
    member = field.removeprefix("body.") if isinstance(field, str) else None
    if (
        exc.code == "validation_error"
        and member == CHANGE_BY
        and details.get("reason") == UNDEFINED_REASON
        and CHANGE_BY in form.fields
    ):
        form.add_error(CHANGE_BY, NO_VALUE_YET)  # convention 24: this story's wording first
        return page
    refusal = world_refusal(
        exc, ctx.story_id, VERB_PHRASE, ctx.effective, value_type=state.type, unit=state.unit
    )
    if refusal is not None:
        if refusal.field in form.fields and not refusal.url:
            form.add_error(refusal.field, refusal.text)
        else:
            page.update(
                refusal=refusal.text,
                refusal_url=refusal.url,
                refusal_link=REFUSAL_LINKS.get(exc.code, PAUSED_LINK),
            )
        return page
    if exc.http_status == 422 and apply_engine_errors(exc, form):
        return page
    page["write_error"] = panel_for(exc)
    return page


def _set(name: str) -> Any:
    def build(ctx: InspectContext) -> dict[str, Any]:
        request = ctx.request
        cast, names, order = _cast(ctx)
        back = states_url(ctx.story_id, None)
        page: dict[str, Any] = {
            "state_name": name,
            "back_url": back,
            "clock_url": clock_url(ctx.story_id),
        }
        closed = module_gate(STATES, ctx.effective)
        if closed is not None:
            page["gate_unavailable"] = closed
            return page
        blocked = _cannot_set(name, None)
        if blocked is not None:
            page["cannot_set"] = blocked[0]
            return page
        try:
            view = ctx.client.get_state(ctx.story_id, name, viewpoint=OMNISCIENT)
        except EngineClientError as exc:
            if is_held(exc) or is_module_off(exc):
                raise
            log_engine_error(request, exc)
            if isinstance(exc, EngineError) and exc.http_status == 404:
                page["missing"] = NOT_FOUND
            else:
                page["read_error"] = panel_for(exc)
            return page
        state = view.state
        page.update(
            row=state_row(state, names, order, ctx.story_id, None),
            revision_line=STORY_REVISION.replace("{n}", str(view.timeline_revision)),
        )
        blocked = _cannot_set(name, state)
        if blocked is not None:
            page["cannot_set"] = blocked[0]
            return page
        posted = request.method == "POST"
        form = StateSetForm(
            request.POST if posted else None,
            state=state,
            cast=cast,
            scope_key=request.GET.get("scope_key", ""),
        )
        page.update(
            form=form,
            unit=state.unit if state.type == "number" else None,
            action_url=set_url(ctx.story_id, name),
            cast_unavailable=CAST_UNAVAILABLE if ctx.story is None and form.per_character else None,
            idempotency_key=new_ulid(),  # #43: every render mints a fresh key
            expected_revision=str(view.timeline_revision),
        )
        if not posted:
            return page
        key = posted_idempotency_key(request)
        # D8: a local invalid form (and an unknown outcome) keeps the posted key and revision.
        page.update(
            idempotency_key=key,
            expected_revision=request.POST.get(EXPECTED_REVISION_FIELD, ""),
        )
        if not form.is_valid():
            return page
        body = form.body
        try:
            result = ctx.client.set_state(
                ctx.story_id,
                name,
                body,
                expected_revision=expected_revision(request.POST),
                idempotency_key=key,
            )
        except EngineClientError as exc:
            return _refused(ctx, name, exc, state, view.timeline_revision, form, page)
        pk = getattr(ctx.client, "last_operation_id", None)
        row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
        raw = row.response if row is not None else dataclasses.asdict(result)
        return _written(ctx, name, result, row, body=body, state=state, raw=raw)

    return build


@require_http_methods(["GET", "HEAD", "POST"])
def state_set(request: HttpRequest, story_id: str, name: str) -> HttpResponse:
    """``inspect:state-set``: the Set form, then ``PUT /stories/{id}/states/{name}`` and the
    result page on the POST response (#251)."""
    return inspect_page(
        request, story_id, Tab.WORLD, SET_TEMPLATE, _set(name), current="inspect:states"
    )

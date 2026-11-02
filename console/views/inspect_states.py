"""The World tab's States section and one State as of a boundary (#242; D3, D4, D5, D7).

``inspect:states`` (``/stories/<id>/inspect/world/states/``) renders through #135's
:func:`console.views.inspect.inspect_page` with ``Tab.WORLD``, so #239's greyed-section
short-circuit covers its gate (``states`` at ``prompt_only``, D1): a closed gate renders the
inline panel and makes no States read. Reads per render, in order: the shell's effective-config
and story reads, then one ``GET /stories/{id}/states?viewpoint=<form value>``. The viewpoint
comes from D4's :class:`~console.forms.world.WorldViewpointForm` (omniscient by default, never the
story's default viewpoint); an invalid viewpoint makes no read. The Console sends the viewpoint
and renders what comes back: it never filters States itself and never reuses another
viewpoint's read (a character is never given a ``hidden`` State; Engine FR-STATE-9).

``inspect:state`` (``/stories/<id>/inspect/world/state/<state_name:name>/``) reads one State
through an "As of" boundary (D7: ``N`` or ``N+k``; blank: the story as it stands). It isn't a D3
section, so it checks ``module_gate(STATES, …)`` itself; it passes ``current="inspect:states"``
(D3's current-section rule), so the shell marks States current and, with the gate closed,
renders the inline panel before ``build`` runs. Reads: the shell's two, then one
``GET /stories/{id}/states/{name}?viewpoint=&as_of=``.

``?viewpoint=`` is undeclared on both routes in the Engine's OpenAPI (D18, Engine bug
nathanpond/Epistrel#682); the client sends it anyway.

Errors stay inside the section (convention 10), the form always rendered: a 404 is the
not-found line (the one-State page's not-readable line when ``details.state`` is present); a 422
lands on the form (``viewpoint_unresolved`` on Viewpoint, ``query.as_of`` beside As of, any other
above the form); a 409 ``module_off`` escapes to the shell's greyed panel; a 503
``erasure_pending`` to #87's held wrapper; anything else is #43's panel below the form. Every
caught error is logged through ``log_engine_error``.
"""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_safe

from console.canon import cast_names
from console.engine.client import EngineClientError, EngineError
from console.engine.models import StoryState, rejected_entries
from console.engine_errors import log_engine_error, panel_for
from console.forms.states import StateAsOfForm
from console.forms.world import WorldViewpointForm
from console.held import is_held
from console.inspect import STATES, Tab, is_module_off, module_gate
from console.paging import CAST_UNAVAILABLE
from console.states import (
    STATES_UNPARSED,
    clock_url,
    count_text,
    empty_text,
    not_readable_text,
    state_row,
    state_rows,
    states_url,
)
from console.views.inspect import InspectContext, inspect_page
from console.world import boundary_text, viewpoint_line

STATES_TEMPLATE = "console/inspect/states.html"
STATE_TEMPLATE = "console/inspect/state.html"
NOT_FOUND = "Not found (it may have been deleted or truncated)"


def _cast(ctx: InspectContext) -> tuple[Any, dict[str, str], list[str]]:
    cast = ctx.story.cast if ctx.story is not None else None
    return cast, cast_names(cast), [c.id for c in cast or ()]


def _header(viewpoint: str | None, names: dict[str, str], revision: int) -> str:
    return f"{viewpoint_line(viewpoint, names)} · revision {revision}"


def _read_error(
    ctx: InspectContext, exc: EngineClientError, form: WorldViewpointForm
) -> dict[str, Any]:
    """Convention 10's in-section error for a States read (held and ``module_off`` re-raised
    to the middleware and the shell)."""
    if is_held(exc) or is_module_off(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if isinstance(exc, EngineError) and exc.http_status == 422:
        form.apply_engine_error(exc)
        return {}
    if isinstance(exc, EngineError) and exc.http_status == 404:
        return {"not_found": NOT_FOUND}
    return {"states_error": panel_for(exc)}


def _states(ctx: InspectContext) -> dict[str, Any]:
    """The section: the form, then (valid form) one ``get_states`` and its header, clock,
    count, unparsed notice and rows."""
    cast, names, order = _cast(ctx)
    form = WorldViewpointForm(ctx.request.GET, cast=cast)
    context: dict[str, Any] = {
        "form": form,
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "actions": None,  # convention 14: #251 (Set) and #265 (Change definition)
    }
    if not form.is_valid():
        return context
    viewpoint = form.query()["viewpoint"]
    try:
        body = ctx.client.get_states(ctx.story_id, viewpoint=viewpoint)
    except EngineClientError as exc:
        context.update(_read_error(ctx, exc, form))
        return context
    raw = body.unparsed.get("states") if body.unparsed else None
    rejected = rejected_entries(raw, StoryState.from_json) if raw is not None else []
    context.update(
        states=body,
        header=_header(body.output_viewpoint, names, body.timeline_revision),
        clock_url=clock_url(ctx.story_id),
        count=count_text(len(body.states)),
        rows=state_rows(body.states, names, order, ctx.story_id, viewpoint),
        rejected=rejected,
        unparsed_text=STATES_UNPARSED if rejected else None,
        empty=None if body.states or rejected else empty_text(body.output_viewpoint, names),
    )
    return context


@require_safe
def states(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:states``, the story's States for one viewpoint (#242)."""
    return inspect_page(request, story_id, Tab.WORLD, STATES_TEMPLATE, _states)


def _state(name: str) -> Any:
    def build(ctx: InspectContext) -> dict[str, Any]:
        """One State: the gate first, then the form, then (valid form) one ``get_state``."""
        cast, names, order = _cast(ctx)
        context: dict[str, Any] = {"state_name": name, "actions": None}
        closed = module_gate(STATES, ctx.effective)
        if closed is not None:
            context.update(gate_unavailable=closed, back_url=states_url(ctx.story_id, None))
            return context
        form = StateAsOfForm(ctx.request.GET, cast=cast)
        context.update(
            form=form,
            cast_unavailable=CAST_UNAVAILABLE if ctx.story is None else None,
            back_url=states_url(ctx.story_id, form.data.get("viewpoint")),
        )
        if not form.is_valid():
            # The boundary still heads the page when it cleaned (an invalid one: the name alone).
            if "as_of" in form.cleaned_data:
                context["boundary"] = boundary_text(form.cleaned_data["as_of"] or None)
            return context
        query = form.query()
        context["boundary"] = boundary_text(query["as_of"])
        context["back_url"] = states_url(ctx.story_id, query["viewpoint"])
        try:
            view = ctx.client.get_state(ctx.story_id, name, **query)
        except EngineClientError as exc:
            if isinstance(exc, EngineError) and exc.http_status == 404 and "state" in exc.details:
                log_engine_error(ctx.request, exc)
                context["not_readable"] = not_readable_text(name, query["as_of"])
            else:
                context.update(_read_error(ctx, exc, form))
            return context
        context.update(
            view=view,
            header=_header(view.output_viewpoint, names, view.timeline_revision),
            row=state_row(view.state, names, order, ctx.story_id, query["viewpoint"]),
        )
        return context

    return build


@require_safe
def state(request: HttpRequest, story_id: str, name: str) -> HttpResponse:
    """``inspect:state``, one State as of a boundary (#242)."""
    return inspect_page(
        request, story_id, Tab.WORLD, STATE_TEMPLATE, _state(name), current="inspect:states"
    )

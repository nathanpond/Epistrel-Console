"""The Scene tab and the entity page (#58): a structured story's scene, and an entity's standing
profile, at a chosen turn and viewpoint.

``inspect:scene`` (``/stories/<id>/inspect/scene/``) asks ``GET /stories/{id}/scene``;
``inspect:entity`` (``/stories/<id>/inspect/entities/<eid>/``, ``eid`` a ULID, so anything else
is Django's 404 with no Engine call) asks ``GET /stories/{id}/entities/{eid}/profile``. Both
render in the Scene tab (#135's Scene rule, ``pipeline == "structured"``, gates both: a Lite
story gets the greyed panel and no scene or profile call) under one GET form
(:class:`console.forms.inspect.SceneForm`, ``_scene_form.html``): Turn (``as_of``, sent only
when entered) and Viewpoint (always sent). These are the only M4 reads that send a character
viewpoint (#135 convention 1).

Reads per render, sequential: #135's effective config and story (the head, the cast and the
default viewpoint), then at most one scene or profile read. No name reads: names come from the
response (convention 2). Rows: :mod:`console.scene`.

Errors: the story read's 404 is convention 10's story-level not-found, made before any scene or
profile call (any other failure of it degrades the form: "Omniscient" plus the requested
viewpoint, no upper bound). An invalid form makes no call ("Fix the form above to read the
scene."). A 422 lands beside Turn (``query.as_of``) or Viewpoint (``viewpoint_unresolved``,
``query.viewpoint``), else above the form; a profile 404 reads "That entity isn't one of this
story's"; 503 ``erasure_pending`` is #87's held wrapper; anything else is #43's panel below the
form.
"""

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_safe

from console.engine.client import EngineClientError, EngineError
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import SceneForm
from console.held import is_held
from console.inspect import Tab, is_module_off, short_id
from console.scene import (
    ENTITY_NOT_FOUND,
    M4_CLOCK,
    entity_link,
    raw,
    scene_url,
    scene_view,
    viewpoint_header,
)
from console.views.inspect import InspectContext, inspect_page

SCENE_TEMPLATE = "console/inspect/scene.html"
ENTITY_TEMPLATE = "console/inspect/entity.html"
FIX_FORM = "Fix the form above to read the scene."


def _form(ctx: InspectContext) -> SceneForm:
    story = ctx.story
    default = story.settings.get("default_output_viewpoint") if story is not None else None
    return SceneForm(
        ctx.request.GET,
        cast=story.cast if story is not None else None,
        head=story.next_turn_index - 1 if story is not None else None,
        default_viewpoint=default if isinstance(default, str) else None,
    )


def _story_not_found(ctx: InspectContext) -> bool:
    error = ctx.story_error
    return isinstance(error, EngineError) and error.http_status == 404


def _base(form: SceneForm, action: str) -> dict[str, Any]:
    return {"form": form, "form_action": action, "m4_clock": M4_CLOCK}


def _read_failed(ctx: InspectContext, form: SceneForm, exc: EngineClientError) -> dict[str, Any]:
    """A failed scene or profile read's context (a 404 is the caller's): a 422 on the form (then
    the fix-the-form line), else #43's panel. Held and ``module_off`` escape to the shell."""
    if is_held(exc) or is_module_off(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if isinstance(exc, EngineError) and exc.http_status == 422:
        form.apply_engine_error(exc)
        return {"fix_form": FIX_FORM}
    return {"read_error": panel_for(exc)}


def _scene(ctx: InspectContext) -> dict[str, Any]:
    form = _form(ctx)
    context = _base(form, reverse(Tab.SCENE.url_name, args=[ctx.story_id]))
    if _story_not_found(ctx):
        context["not_found"] = True
        return context
    if not form.is_valid():
        context["fix_form"] = FIX_FORM
        return context
    try:
        body = ctx.client.get_scene(ctx.story_id, **form.query())
    except EngineClientError as exc:
        if isinstance(exc, EngineError) and exc.http_status == 404:
            log_engine_error(ctx.request, exc)
            context["not_found"] = True
            return context
        context.update(_read_failed(ctx, form, exc))
        return context
    cast = ctx.story.cast if ctx.story is not None else None
    context.update(scene=scene_view(ctx.story_id, body, cast), more=raw(body))
    return context


@require_safe
def scene(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:scene``, the scene at a turn and viewpoint (#58)."""
    return inspect_page(request, story_id, Tab.SCENE, SCENE_TEMPLATE, _scene)


def _entity(ctx: InspectContext, entity_id: str) -> dict[str, Any]:
    form = _form(ctx)
    context = _base(form, reverse("inspect:entity", args=[ctx.story_id, entity_id]))
    # Until a profile answers, the header is the id and "← Scene" keeps the submitted values.
    context.update(
        entity_id=entity_id,
        entity_short=short_id(entity_id),
        back_url=scene_url(ctx.story_id, form.value("as_of"), form["viewpoint"].value()),
    )
    if _story_not_found(ctx):
        context["not_found"] = True
        return context
    if not form.is_valid():
        context["fix_form"] = FIX_FORM
        return context
    try:
        profile = ctx.client.get_entity_profile(ctx.story_id, entity_id, **form.query())
    except EngineClientError as exc:
        if isinstance(exc, EngineError) and exc.http_status == 404:
            log_engine_error(ctx.request, exc)
            context["entity_not_found"] = ENTITY_NOT_FOUND
            return context
        context.update(_read_failed(ctx, form, exc))
        return context
    cast = ctx.story.cast if ctx.story is not None else None
    context.update(
        profile=profile,
        entity=entity_link(ctx.story_id, profile.entity, profile.as_of, profile.viewpoint),
        context_line=f"Turn {profile.as_of} · as {viewpoint_header(profile.viewpoint, cast)}",
        back_url=scene_url(ctx.story_id, profile.as_of, profile.viewpoint),
        link_as_of=profile.as_of,
        link_viewpoint=profile.viewpoint,
        more=raw(profile),
    )
    return context


@require_safe
def entity(request: HttpRequest, story_id: str, entity_id: str) -> HttpResponse:
    """``inspect:entity``, an entity's standing profile at a turn and viewpoint (#58), in the
    Scene tab."""
    return inspect_page(
        request, story_id, Tab.SCENE, ENTITY_TEMPLATE, lambda ctx: _entity(ctx, entity_id)
    )

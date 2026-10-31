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

**Model as a minor character** (#199). The entity page shows, under its header and context line,
"Character: <name>" (linked to the cast row) when the entity id is a character of the shell's
``get_story`` cast, else, for a ``person`` or ``other`` entity of a story that isn't archived,
the "Model as a minor character…" link with a muted note when that read already shows the
Engine will refuse (paused, else Allow new arrivals off); no read is added. The link opens
``inspect:entity-model`` (``entities/<eid>/model/``, Scene tab, same gate): one omniscient profile
read at head names the entity, signed into the form (salt ``console.inspect.entity_model``) so the
POST makes no read; :class:`console.forms.inspect.ModelEntityForm` is checked before any call;
``POST /stories/{id}/entities/{eid}/model`` then redirects to the entity page (``as_of`` and
``viewpoint`` kept when #58's :class:`SceneForm` accepts them) with a flash linking the cast row.
Refusals re-render the form: an Engine 4xx mints a fresh key, a 503, a 401 or no answer keeps the
posted one (the write may have landed).
"""

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_safe

from console.cast import OMNISCIENT
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClientError, EngineError
from console.engine.models import EntityProfileBody, Story
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import SCENE_FIELDS, ModelEntityForm, SceneForm
from console.held import is_held
from console.ids import new_ulid
from console.inspect import Tab, is_module_off, module_off_unavailable, short_id
from console.scene import (
    ENTITY_NOT_FOUND,
    M4_CLOCK,
    entity_link,
    kind_label,
    link_query,
    raw,
    scene_url,
    scene_view,
    viewpoint_header,
)
from console.story_pause import is_paused
from console.templatetags.inspect_tags import code_spans
from console.views.inspect import InspectContext, inspect_page
from console.views.story_characters import cast_row_url
from console.wording import archived_refusal, write_refusal

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
    context.update(model_action(ctx.story_id, ctx.story, profile))
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


# --- model the entity as a minor character (#199) -------------------------------------------------

ENTITY_MODEL_TEMPLATE = "console/inspect/entity_model.html"
MODEL_SALT = "console.inspect.entity_model"
MODEL_VERB = "model its entities"
MODELABLE_KINDS = frozenset({"person", "other"})
PAUSED_NOTE = "Paused — the Engine will refuse until the check finishes"
ARRIVALS_NOTE = (
    "This story allows no new arrivals — the Engine will refuse; turn it on in "
    '<a href="{}">Settings</a>'
)
ALREADY_MODELED = 'Already a character — <a href="{}">see it in the cast</a>'
NOT_A_PERSON = "Only a person (or an entity of kind other) can be modeled"
NO_ARRIVALS_REFUSAL = (
    'This story allows no new arrivals — turn on Allow new arrivals in <a href="{}">Settings</a> '
    "first"
)
MODELED = (
    'Modeled {} as a minor character — timeline revision {}. <a href="{}">See it in the cast</a>'
)
MODELED_UNREADABLE = "Modeled — the Engine's answer couldn't be read; see the cast"
BAD_RESPONSE = "bad_response"
KEEP_KEY_STATUSES = frozenset({401, 503})  # the write may have landed: retry under the same key


def _settings_url(story_id: str) -> str:
    return reverse("stories:settings", args=[story_id])


def refusal_note(story: Story) -> SafeString | str | None:
    """Why the Engine will refuse a modeling write, from the shell's ``get_story`` alone: the
    pause first (it is temporary), else Allow new arrivals off (a missing key reads as on)."""
    if is_paused(story):
        return PAUSED_NOTE
    if story.settings.get("allow_new_arrivals") is False:
        return format_html(ARRIVALS_NOTE, _settings_url(story.id))
    return None


def model_url(story_id: str, entity_id: str, query: str = "") -> str:
    url = reverse("inspect:entity-model", args=[story_id, entity_id])
    return f"{url}?{query}" if query else url


def model_action(story_id: str, story: Story | None, profile: EntityProfileBody) -> dict[str, Any]:
    """The entity page's action line (#199): "Character: <name>" for a cast member, else the
    "Model as a minor character…" link (a person or ``other``, story not archived) with its
    :func:`refusal_note`; nothing when the story read failed."""
    if story is None:
        return {}
    entity_id = profile.entity.entity_id
    for character in story.cast:
        if character.id == entity_id:
            return {
                "cast_character": character.name or short_id(entity_id),
                "cast_url": cast_row_url(story_id, entity_id),
            }
    if story.status == "archived" or profile.entity.kind not in MODELABLE_KINDS:
        return {}
    query = link_query(profile.as_of, profile.viewpoint)
    return {"model_url": model_url(story_id, entity_id, query), "model_note": refusal_note(story)}


@dataclass(frozen=True)
class EntitySnapshot:
    """What the form page's GET read of the entity, signed into the form so a POST makes no
    read."""

    story_id: str
    entity_id: str
    name: str
    kind: str

    def signed(self) -> str:
        return signing.dumps(asdict(self), salt=MODEL_SALT)

    @classmethod
    def unsigned(cls, value: str | None, story_id: str, entity_id: str) -> EntitySnapshot | None:
        try:
            found = cls(**signing.loads(value or "", salt=MODEL_SALT))
        except signing.BadSignature, TypeError:
            return None
        if (found.story_id, found.entity_id) != (story_id, entity_id):
            return None
        if not isinstance(found.name, str) or not isinstance(found.kind, str):
            return None
        return found


def _kept_query(ctx: InspectContext) -> str:
    """The request's ``as_of`` and ``viewpoint``, each kept only when #58's
    :class:`SceneForm` accepts it."""
    form = _form(ctx)
    form.is_valid()
    kept: dict[str, str] = {}
    for name in SCENE_FIELDS:
        given = ctx.request.GET.get(name, "")
        if given and name not in form.errors and form.cleaned_data.get(name) is not None:
            kept[name] = str(form.cleaned_data[name])
    return urlencode(kept)


def with_article(kind: str) -> str:
    """ "a place", "an organization", "a living thing" (#58's kind label, lower case)."""
    label = kind_label(kind).lower()
    return f"{'an' if label[:1] in 'aeiou' else 'a'} {label}"


def _not_a_person(kind: object) -> str:
    if isinstance(kind, str) and kind:
        return f"{NOT_A_PERSON} — this is {with_article(kind)}"
    return NOT_A_PERSON


def _model_context(
    ctx: InspectContext, entity_id: str, query: str, snapshot: EntitySnapshot | None
) -> dict[str, Any]:
    story = ctx.story
    archived = story is not None and story.status == "archived"
    name = snapshot.name if snapshot is not None else ""
    return {
        "entity_id": entity_id,
        "entity_short": short_id(entity_id),
        "entity_name": name or short_id(entity_id),
        "entity_kind": kind_label(snapshot.kind) if snapshot is not None else None,
        "snapshot": snapshot.signed() if snapshot is not None else "",
        "back_url": f"{reverse('inspect:entity', args=[ctx.story_id, entity_id])}"
        + (f"?{query}" if query else ""),
        "form_action": model_url(ctx.story_id, entity_id, query),
        "archived_text": archived_refusal(MODEL_VERB) if archived else None,
        "model_note": refusal_note(story) if story is not None and not archived else None,
    }


def _model_refused(
    ctx: InspectContext,
    exc: EngineClientError,
    form: ModelEntityForm,
    snapshot: EntitySnapshot | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    """A refused write: the form again (inputs kept) with one line above it. An Engine 4xx mints
    a fresh key; a 503, a 401 or no answer keeps the posted one. Held escapes to the shell."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if not isinstance(exc, EngineError) or exc.http_status in KEEP_KEY_STATUSES:
        context["write_error"] = panel_for(exc)
        return context
    context["idempotency_key"] = new_ulid()
    story_id = ctx.story_id
    if is_module_off(exc):
        off = module_off_unavailable(exc, None, ctx.effective).reason
        config = reverse("stories:config", args=[story_id])
        context["refusal"] = format_html('{} <a href="{}">Config</a>', code_spans(off), config)
        return context
    if exc.http_status == 404:
        context.update(entity_not_found=ENTITY_NOT_FOUND, form=None)
        return context
    reason = exc.details.get("reason")
    if exc.http_status == 422 and reason == "already_modeled":
        character_id = exc.details.get("character_id")
        cast = (
            cast_row_url(story_id, character_id)
            if isinstance(character_id, str) and character_id
            else reverse("stories:detail", args=[story_id])
        )
        context["refusal"] = format_html(ALREADY_MODELED, cast)
    elif exc.http_status == 422 and reason == "not_a_person":
        kind = exc.details.get("kind") or (snapshot.kind if snapshot is not None else None)
        context["refusal"] = _not_a_person(kind)
    elif exc.http_status == 422 and reason == "allow_new_arrivals":
        context["refusal"] = format_html(NO_ARRIVALS_REFUSAL, _settings_url(story_id))
    elif exc.http_status == 422:
        form.apply_engine_error(exc)
    elif (refusal := write_refusal(exc, story_id, MODEL_VERB)) is not None:
        context["refusal"] = (
            format_html('{} <a href="{}">See the check</a>', refusal.text, refusal.url)
            if refusal.url
            else refusal.text
        )
    else:
        context["write_error"] = panel_for(exc)
    return context


def _entity_model(ctx: InspectContext, entity_id: str) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    query = _kept_query(ctx)
    if _story_not_found(ctx):
        context = _model_context(ctx, entity_id, query, None)
        context["not_found"] = True
        return context
    if request.method != "POST":
        try:
            profile = ctx.client.get_entity_profile(ctx.story_id, entity_id, viewpoint=OMNISCIENT)
        except EngineClientError as exc:
            if is_held(exc) or is_module_off(exc):
                raise
            log_engine_error(request, exc)
            context = _model_context(ctx, entity_id, query, None)
            if isinstance(exc, EngineError) and exc.http_status == 404:
                context["entity_not_found"] = ENTITY_NOT_FOUND
                return context
            context.update(
                read_error=panel_for(exc), form=ModelEntityForm(), idempotency_key=new_ulid()
            )
            return context
        found = profile.entity
        snapshot = EntitySnapshot(ctx.story_id, entity_id, found.name, found.kind)
        context = _model_context(ctx, entity_id, query, snapshot)
        context.update(form=ModelEntityForm(unnamed=not found.name), idempotency_key=new_ulid())
        return context
    posted = EntitySnapshot.unsigned(request.POST.get("snapshot"), ctx.story_id, entity_id)
    form = ModelEntityForm(request.POST, unnamed=posted is not None and not posted.name)
    key = posted_idempotency_key(request)
    context = _model_context(ctx, entity_id, query, posted)
    context.update(form=form, idempotency_key=key)
    if not form.is_valid():
        return context
    try:
        result = ctx.client.model_entity(
            ctx.story_id, entity_id, **form.body(), idempotency_key=key
        )
    except EngineClientError as exc:
        if isinstance(exc, EngineError) and exc.code == BAD_RESPONSE and exc.http_status == 201:
            log_engine_error(request, exc)
            messages.warning(request, MODELED_UNREADABLE)
            return redirect(context["back_url"])
        return _model_refused(ctx, exc, form, posted, context)
    character = result.character
    messages.success(
        request,
        format_html(
            MODELED,
            character.name or short_id(character.id),
            result.timeline_revision,
            cast_row_url(ctx.story_id, character.id),
        ),
        extra_tags="safe",
    )
    return redirect(context["back_url"])


@require_http_methods(["GET", "HEAD", "POST"])
def entity_model(request: HttpRequest, story_id: str, entity_id: str) -> HttpResponse:
    """``inspect:entity-model``: the "Model as a minor character" form (one omniscient profile
    read names the entity), then ``POST /stories/{id}/entities/{eid}/model`` and a redirect to
    the entity page with a flash (#199), in the Scene tab."""
    return inspect_page(
        request,
        story_id,
        Tab.SCENE,
        ENTITY_MODEL_TEMPLATE,
        lambda ctx: _entity_model(ctx, entity_id),
    )

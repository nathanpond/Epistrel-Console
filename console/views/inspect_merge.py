"""Merge entities (#306; FR-PROP-7; E2, E4, E5, E6, E10, E11, E19, E21, D8, C4, C5, C13).

``inspect:entity-merge`` (``/stories/<id>/inspect/canon/merge/``, GET/HEAD/POST) renders
through :func:`console.views.inspect.inspect_page` in the Canon tab, so the Canon tab's gate
(the structured pipeline, #135) applies: a greyed tab makes no scene read and no merge call. The
page is omniscient-only (E2): it shows ``OMNISCIENT_LINE`` and no viewpoint picker.

- **GET** makes the shell's two reads, then one ``get_scene(story_id, viewpoint="omniscient")``
  (no ``as_of``: the head). Held (#87), ``story_importing`` (E10) and ``erasure_pending`` answers
  go to their middleware or held page; any other failure is logged and
  :data:`SCENE_UNAVAILABLE` shows above the picker, which still offers the cast and the URL's
  ids. The picker (``console.forms.merge``) pre-ticks each valid ``?entity=`` id; an invalid one
  is dropped with ``ENTITY_ID_INVALID`` once, never echoed.
- **POST** makes the shell's two reads, validates the form (its candidates are the posted ULID
  ticks and the cast: no scene read before the write), then one ``merge_entities`` with
  :meth:`~console.forms.merge.MergeEntitiesForm.body` and the posted key (no expected revision,
  E19). A local error re-renders with the posted key and makes no merge call. A 201 is a 303 to
  E6's ``rewrite_session_url`` with :func:`merge_flash` (E21's session-state rule); a 2xx that
  doesn't parse warns :data:`MERGE_UNREADABLE` and redirects to ``rewrites_url`` (the Canon tab
  while that is unregistered). A refusal re-renders the picker with every input kept and the
  scene read once more: ``rewrite_refusal(exc, story_id, "merge entities", effective)``'s
  sentence (``merge_invalid`` on ``entity_ids``), an Engine ``validation_error`` placed through
  ``apply_engine_errors``, else #43's panel. D8's key: fresh after an answered refusal, the
  posted one after a transport error, a timeout, a 5xx or an unreadable answer.
"""

from collections.abc import Mapping
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods

from console.beliefs import story_minor_ids
from console.canon import cast_names
from console.cast import OMNISCIENT
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClientError, EngineError
from console.engine.models import RewriteSessionWritten, SceneBody
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.merge import (
    ENTITY_ID_INVALID,
    MergeEntitiesForm,
    merge_candidates,
    url_entity_ids,
)
from console.held import is_held
from console.ids import is_ulid, new_ulid
from console.inspect import Tab, short_id
from console.portability import is_importing
from console.raw import plural
from console.rewrites import (
    AWAITING_ANSWERS,
    AWAITING_APPROVAL,
    KEY_TAIL,
    NOT_APPLIED_PREFIX,
    rewrite_refusal,
    rewrite_session_url,
    rewrites_url,
    state_text,
    unanswered_count,
)
from console.views.inspect import InspectContext, inspect_page
from console.views.rewrites import ALIASES, DEFAULT_LINK, REFUSAL_LINKS
from console.wording import character_label

TEMPLATE = "console/inspect/entity_merge.html"
URL_NAME = "inspect:entity-merge"
ENTITY_PARAM = "entity"
VERB_PHRASE = "merge entities"
BAD_RESPONSE = "bad_response"
VALIDATION = "validation_error"
ENTITY_IDS = "entity_ids"

TITLE = "Merge entities"
INTRO = (
    "Reveal that these entities are one referent; the Engine previews what the merge touches "
    "before anything is written"
)
SUBMIT = "Plan the merge"
NO_CANDIDATES = "No entities to offer — type their ids below"
BACK_TEXT = "Back to Canon"
SCENE_UNAVAILABLE = "Scene entities unavailable — pick from the cast or type ids"
# E21's flash, by the written session's state ({session}: the short id in <code>).
MERGE_OPENED = "Merge planned as rewrite session {session}"
MERGE_QUESTIONS = "Merge planned as rewrite session {session} — {n} question(s) to answer"
MERGE_APPLIED = "Merge applied — rewrite session {session}"
MERGE_NOW = "Merge opened — the session is now {state}"
MERGE_UNREADABLE = (
    "Planned — the Engine's answer couldn't be read; find the session on the Rewrite page"
)


def entity_merge_url(story_id: str, entity_id: str | None = None) -> str | None:
    """``inspect:entity-merge``, with ``?entity=`` when given (D19: ``None`` unregistered)."""
    try:
        url = reverse(URL_NAME, args=[story_id])
    except NoReverseMatch:
        return None
    return f"{url}?{urlencode({ENTITY_PARAM: entity_id})}" if entity_id else url


def _canon_url(story_id: str) -> str:
    return reverse(Tab.CANON.url_name, args=[story_id])


def _session_html(session_id: str) -> SafeString:
    return format_html('<code title="{}">{}</code>', session_id, short_id(session_id))


def merge_flash(written: RewriteSessionWritten, key: str, *, apply_directly: bool) -> SafeString:
    """E21's flash from the written session: applied, questions (the unanswered count, C11
    plural), planned, else "Merge opened — the session is now <STATE_TEXT>"; "Not applied
    directly: " first when Apply directly was ticked and nothing was applied; the key tail
    last (the answer's key, else ``key``)."""
    session = written.session
    tail = format_html(KEY_TAIL, written.idempotency_key or key)
    code = _session_html(session.id)
    if written.apply is not None:
        return format_html("{}{}", format_html(MERGE_APPLIED, session=code), tail)
    if written.state == AWAITING_ANSWERS:
        count = plural(unanswered_count(session), "question")
        text = format_html(
            MERGE_QUESTIONS.replace("{n} question(s)", "{count}"), session=code, count=count
        )
    elif written.state == AWAITING_APPROVAL:
        text = format_html(MERGE_OPENED, session=code)
    else:
        text = format_html("{}", MERGE_NOW.format(state=state_text(written.state)))
    prefix = NOT_APPLIED_PREFIX if apply_directly else ""
    return format_html("{}{}{}", prefix, text, tail)


def _cast_labels(ctx: InspectContext) -> dict[str, str] | None:
    """Each cast member's C4 label (" (player)", " (minor)"), in cast order; ``None`` without
    the shell's story read."""
    if ctx.story is None:
        return None
    cast = ctx.story.cast
    names = cast_names(cast)
    minor = story_minor_ids(ctx.story.characters)
    return {c.id: character_label(c.id, names, minor) for c in cast}


def _scene(ctx: InspectContext) -> SceneBody | None:
    """The one omniscient scene read at the head; ``None`` (logged) when it failed."""
    try:
        return ctx.client.get_scene(ctx.story_id, viewpoint=OMNISCIENT)
    except EngineClientError as exc:
        if is_held(exc) or is_importing(exc):
            raise
        log_engine_error(ctx.request, exc)
        return None


def _picker(
    ctx: InspectContext,
    kept: list[str],
    *,
    data: Mapping[str, Any] | None = None,
    preticked: tuple[str, ...] = (),
) -> tuple[MergeEntitiesForm, dict[str, Any]]:
    """Read the scene once and build the picker over it (a bound form re-validated)."""
    scene = _scene(ctx)
    candidates = merge_candidates(_cast_labels(ctx), scene, kept)
    form = MergeEntitiesForm(candidates, data, preticked=preticked)
    if data is not None:
        form.is_valid()
    context: dict[str, Any] = {
        "form": form,
        "scene_unavailable": SCENE_UNAVAILABLE if scene is None else "",
        "no_candidates": NO_CANDIDATES if not candidates else "",
        "title": TITLE,
        "intro": INTRO,
        "submit_text": SUBMIT,
        "back_url": _canon_url(ctx.story_id),
        "back_text": BACK_TEXT,
        "form_url": entity_merge_url(ctx.story_id) or ctx.request.path,
    }
    return form, context


def _get(ctx: InspectContext) -> dict[str, Any]:
    kept, invalid = url_entity_ids(ctx.request.GET.getlist(ENTITY_PARAM))
    _, context = _picker(ctx, kept, preticked=tuple(kept))
    context.update(
        idempotency_key=new_ulid(), entity_id_invalid=ENTITY_ID_INVALID if invalid else ""
    )
    return context


def _posted_ticks(ctx: InspectContext) -> list[str]:
    return list(dict.fromkeys(t for t in ctx.request.POST.getlist(ENTITY_IDS) if is_ulid(t)))


def _redirect(target: str) -> HttpResponse:
    response = HttpResponseRedirect(target)
    response.status_code = 303
    return response


def _refused(
    ctx: InspectContext, form: MergeEntitiesForm, exc: EngineClientError, key: str
) -> dict[str, Any]:
    """A refused merge's additions (field errors go on ``form``); D8's key."""
    if not isinstance(exc, EngineError) or exc.code == BAD_RESPONSE:
        return {"write_error": panel_for(exc), "idempotency_key": key}  # the outcome is unknown
    refusal = rewrite_refusal(exc, ctx.story_id, VERB_PHRASE, ctx.effective)
    if refusal is not None:
        if refusal.field == ENTITY_IDS:
            form.add_error(ENTITY_IDS, refusal.text)
            return {"idempotency_key": new_ulid()}
        return {
            "write_refusal": refusal.text,
            "refusal_url": refusal.url,
            "refusal_link": REFUSAL_LINKS.get(exc.code, DEFAULT_LINK) if refusal.url else "",
            "idempotency_key": new_ulid(),
        }
    if exc.code == VALIDATION and apply_engine_errors(exc, form, aliases=ALIASES):
        return {"idempotency_key": new_ulid()}
    if exc.http_status >= 500:
        return {"write_error": panel_for(exc), "idempotency_key": key}
    return {"write_error": panel_for(exc), "idempotency_key": new_ulid()}


def _post(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    key = posted_idempotency_key(request)
    kept = _posted_ticks(ctx)
    form = MergeEntitiesForm(merge_candidates(_cast_labels(ctx), None, kept), request.POST)
    if not form.is_valid():
        _, context = _picker(ctx, kept, data=request.POST)  # D8: local invalid input
        return {**context, "idempotency_key": key}
    apply_directly = bool(form.cleaned_data.get("apply_directly"))
    try:
        written = ctx.client.merge_entities(ctx.story_id, form.body(), idempotency_key=key)
    except EngineClientError as exc:
        if is_held(exc) or is_importing(exc):
            raise
        log_engine_error(request, exc)
        if (
            isinstance(exc, EngineError)
            and exc.code == BAD_RESPONSE
            and 200 <= exc.http_status < 300
        ):
            unreadable = format_html("{}{}", MERGE_UNREADABLE, format_html(KEY_TAIL, key))
            messages.warning(request, unreadable, extra_tags="safe")
            return _redirect(rewrites_url(ctx.story_id) or _canon_url(ctx.story_id))
        shown, context = _picker(ctx, kept, data=request.POST)
        return {**context, **_refused(ctx, shown, exc, key)}
    messages.success(
        request, merge_flash(written, key, apply_directly=apply_directly), extra_tags="safe"
    )
    target = (
        rewrite_session_url(ctx.story_id, written.session.id)
        or rewrites_url(ctx.story_id)
        or _canon_url(ctx.story_id)
    )
    return _redirect(target)


@require_http_methods(["GET", "HEAD", "POST"])
def entity_merge(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:entity-merge``: plan an entity merge as a rewrite session (#306)."""

    def build(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
        return _post(ctx) if request.method == "POST" else _get(ctx)

    return inspect_page(request, story_id, Tab.CANON, TEMPLATE, build)

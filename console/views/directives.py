"""The Play page's Direct tab POST (#300; FR-DIRECT-1,2,6, FR-LITE-8; E2, E5, E11, E19, E21).

``stories:direct`` (``POST /stories/<id>/direct/``) sends the tab's direction:
``apply_directive(story_id, form.body(), expected_revision=None, idempotency_key=<posted key>)``,
once, waiting ``turn_timeout(None)`` (E19). It sends no expected revision: the tab switches through
the Play page's signed snapshot with no story read, so it can't know the revision, and a direction
means "now", as a turn or an ingest batch does. The route is omniscient-only (E2): the client
always sends ``viewpoint=omniscient``, and the Turn tab's ``output_viewpoint`` (carried as a
hidden display-only field) is never put into the body.

The cast and pipeline come from the signed snapshot, else one story read (the regenerate pattern,
through ``console.views.play._snapshot``). An invalid form makes no Engine write. The answer is the
tab alone to HTMX (``_direct.html``, target ``#direct``, ``outerHTML``) and the whole Play page with
Direct chosen otherwise (the Ingest tab's ``_ingest_answer`` pattern; no redirect, no flash: the
outcome in the tab is the only notice).

After a success the form is fresh (empty text, kind and modes at their initials, a new key) with
:func:`console.directives.direct_outcome` above it. A refusal re-renders the tab (200) with the
whole bound form kept: E5's :func:`console.rewrites.rewrite_refusal` first (the directive codes and
a ``body.text`` guardrail on Text, ``directive_ambiguous`` with its string candidates; the steer
budget, author-fact limit, session limit and ``module_off`` above the form with their links; D9 and
C5's texts for the rest), then ``validation_error``'s paths through ``apply_engine_errors``, else
#43's panel. D8's key rule: a fresh key after an answered refusal, the posted key after local
invalid input or an unknown outcome (transport failure, timeout, ``bad_response``, an unworded 5xx).
A held (#87) or importing (E10) answer goes to its middleware.
"""

from dataclasses import replace
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from console.canon import cast_names
from console.confirm import posted_idempotency_key
from console.directives import DirectView, ambiguous_suffix, direct_outcome
from console.engine.client import EngineClientError, EngineError
from console.engine_access import engine_client_for
from console.engine_errors import is_htmx, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.directives import DirectForm
from console.held import is_held
from console.ids import new_ulid
from console.models import EngineOperation
from console.portability import is_importing
from console.rewrites import rewrite_refusal
from console.views.play import (
    DIRECT_TAB,
    SNAPSHOT_FIELD,
    TRANSCRIPT_PLAYER_VIEW,
    PlayStory,
    _direct_view,
    _form,
    _initial,
    _page,
    _player_view,
    _snapshot,
)
from console.views.rewrites import ALIASES, REFUSAL_LINKS
from console.views.story_config import model_json

VERB_PHRASE = "direct the story"
BAD_RESPONSE = "bad_response"
AMBIGUOUS = "directive_ambiguous"
DEFAULT_LINK = "See the check"
TEXT_FIELD = "text"


def _refused(
    request: HttpRequest, story_id: str, form: DirectForm, exc: EngineClientError, key: str
) -> dict[str, Any]:
    """The refused direction's ``DirectView`` changes (the form carries field errors itself)."""
    if is_held(exc) or is_importing(exc):
        raise exc
    log_engine_error(request, exc)
    if not isinstance(exc, EngineError) or exc.code == BAD_RESPONSE:
        return {"panel": panel_for(exc), "key": key}  # the outcome is unknown
    refusal = rewrite_refusal(exc, story_id, VERB_PHRASE, None)
    if refusal is not None:
        if exc.code == AMBIGUOUS:
            refusal = replace(refusal, text=refusal.text + ambiguous_suffix(exc.details))
        if refusal.field == TEXT_FIELD and not refusal.url:
            form.add_error(TEXT_FIELD, refusal.text)
            return {"key": new_ulid()}
        link = REFUSAL_LINKS.get(exc.code, DEFAULT_LINK) if refusal.url else ""
        return {"refusal": refusal, "refusal_link": link, "key": new_ulid()}
    if exc.code == "validation_error" and apply_engine_errors(exc, form, aliases=ALIASES):
        return {"key": new_ulid()}
    if exc.http_status >= 500:
        return {"panel": panel_for(exc), "key": key}
    return {"panel": panel_for(exc), "key": new_ulid()}


def _replayed(pk: int | None) -> tuple[bool, Any]:
    """The #44 row's replay flag and recorded answer (``(False, None)`` without a row)."""
    row = EngineOperation.objects.filter(pk=pk).first() if pk is not None else None
    return (row is not None and row.replayed, row.response if row is not None else None)


@require_POST
def direct(request: HttpRequest, story_id: str) -> HttpResponse:
    """Send the Direct tab's direction; answer the tab (HTMX) or the whole Play page."""
    play_story = _snapshot(request, story_id, request.POST.get(SNAPSHOT_FIELD))
    viewpoint = request.POST.get("output_viewpoint", "")
    shown = _player_view(request.POST.get(TRANSCRIPT_PLAYER_VIEW))
    form = DirectForm(request.POST, pipeline=play_story.pipeline)
    key = posted_idempotency_key(request)
    if not form.is_valid():
        view = _direct_view(play_story, viewpoint, form=form, key=key, transcript_player_view=shown)
        return _answer(request, play_story, view)
    client = engine_client_for(request)
    try:
        answer = client.apply_directive(
            story_id, form.body(), expected_revision=None, idempotency_key=key
        )
    except EngineClientError as exc:
        changes = _refused(request, story_id, form, exc, key)
        view = _direct_view(
            play_story,
            viewpoint,
            form=form,
            key=changes.pop("key"),
            transcript_player_view=shown,
            **changes,
        )
        return _answer(request, play_story, view)
    replayed, recorded = _replayed(client.last_operation_id)
    outcome = direct_outcome(
        answer,
        story_id,
        play_story.pipeline,
        key=key,
        names=cast_names(play_story.cast),
        raw=recorded if recorded is not None else model_json(answer),
        replayed=replayed,
    )
    return _answer(
        request,
        play_story,
        _direct_view(play_story, viewpoint, outcome=outcome, transcript_player_view=shown),
    )


def _answer(request: HttpRequest, play_story: PlayStory, view: DirectView) -> HttpResponse:
    if not is_htmx(request):
        return _page(
            request,
            play_story.id,
            tab=DIRECT_TAB,
            direct_view=view,
            form=_form(play_story, initial=_initial(play_story, view.viewpoint)),
            player_view=view.transcript_player_view,
        )
    return render(request, "console/play/_direct.html", {"dv": view})

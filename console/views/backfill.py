"""The story's Backfill page and its cancel (#308; E7, E10, E11, E21; ``console/backfill.py``).

``stories:backfill`` (``/stories/<id>/backfill/``, GET/HEAD) answers three ways, ``banner``
first, then ``partial``, then the page:

- **``?banner=1``** (the pause banner's poll, #80/E10): one ``GET /stories/{id}``, rendered
  through ``console.story_pause.pause_banner``. While the story is paused the fragment is the
  banner (a backfill cause polls this URL at ``SLOW_POLL_TRIGGER``; a rating or reactivation
  check keeps its own poll). Once it isn't, an empty 200 with no trigger: the banner goes and
  polling stops. A failed read follows ``poll_failure``: unreachable or 503 keeps the banner
  polling with ``RETRYING_TEXT``; anything else is an empty 200. It never reads the backfill or
  a rating check.
- **``?partial=1``** (E7's polled block): one ``GET /stories/{id}/backfill`` (open conflicts,
  ``limit`` 50), answering ``console/_job_poll.html`` around ``_backfill_body.html`` alone (no
  frame, no nav). Once the backfill is terminal or waiting the answer carries
  ``HX-Refresh: true`` (it has no trigger, so it happens once), so the full page reloads with
  the report, the conflicts and the pending change. Failure order (E7): 503 ``erasure_pending``
  → D17's held fragment in the block, no trigger; 404 ``backfill_not_found`` →
  :data:`~console.backfill.NO_BACKFILL`, no trigger; then ``poll_failure`` (unreachable/503 →
  ``RETRYING_TEXT`` and keep polling; any other 404 or error → the panel, no trigger).
- **The page**, through #296's ``story_page``: the frame reads the story and the effective
  configuration, and ``build`` reads the backfill (``conflicts=all`` under ``?conflicts=all``,
  else ``open``; ``limit`` 50). ``pending_backfill`` comes from the frame's configuration read
  (a failed one leaves the line out), so the page makes no second configuration read. 404
  ``backfill_not_found`` is a normal page with :data:`~console.backfill.NO_BACKFILL`; any other
  failed read is #43's panel inside the frame.

``stories:backfill-cancel`` (``backfill/cancel/``, GET/HEAD/POST) is #134's confirmed confirm
page: the GET makes no Engine call (its Backfill id and Status facts come from the link's query,
display only), and the confirmed POST sends ``DELETE /stories/{id}/backfill`` (no body,
``MUTATION_TIMEOUT_S``). Success flashes :data:`~console.backfill.CANCELLED_TEXT` with E21's key
tail and answers 303 to the Backfill page. A refusal re-renders the confirm page (200, a fresh
key): ``backfill_not_cancellable`` → :data:`~console.backfill.NOT_CANCELLABLE_TEXT`,
``backfill_not_found`` → ``NO_BACKFILL``, ``story_importing`` → E10's text, else C5's
``write_refusal``, else #43's panel. An unknown outcome (transport, timeout, ``bad_response``)
re-renders with #43's panel and the posted key, so a resubmit replays (E21).
"""

import dataclasses
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_http_methods, require_safe

from console.backfill import (
    ALL,
    BACKFILL_PARAM,
    CANCEL_LABEL,
    CANCEL_LINES,
    CANCEL_QUESTION,
    CANCEL_TITLE,
    CANCEL_VERB,
    CANCELLED_TEXT,
    CONFLICT_LIMIT,
    CONFLICTS_PARAM,
    NO_BACKFILL,
    NOT_CANCELLABLE_TEXT,
    OMNISCIENT_NOTE,
    OPEN,
    REFRESH_CONFLICTS,
    SHOW_ALL,
    SHOW_OPEN,
    STATUS_PARAM,
    BackfillView,
    backfill_view,
    pending_change,
    status_fact,
)
from console.canon import cast_names
from console.confirm import ConfirmPage, confirm_action, render_confirm
from console.engine.client import EngineClient, EngineClientError, EngineError, EngineUnreachable
from console.engine.models import StoryBackfill
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.held import held_context, is_held, render_held
from console.job_poll import POLL_TEMPLATE, poll_context, poll_failure
from console.jobs import PARTIAL_PARAM, SLOW_POLL_TRIGGER, job_poll_context
from console.portability import importing_refusal, is_importing
from console.raw import EMPTY
from console.story_pause import BANNER_PARAM, CAUSE_PARAM, PauseBanner, pause_banner
from console.upgrades import BACKFILL, BACKFILL_CAUSES
from console.views.story_pages import StoryPageContext, story_page
from console.wording import write_refusal

TEMPLATE = "console/stories/backfill.html"
BODY_TEMPLATE = "console/stories/_backfill_body.html"
BANNER_TEMPLATE = "console/stories/_pause_banner.html"
NOT_FOUND = "backfill_not_found"
NOT_CANCELLABLE = "backfill_not_cancellable"
BAD_RESPONSE = "bad_response"
IMPORT_JOB_LINK = "Import job"
DETAILS_LINK = "Details"


def page_url(story_id: str) -> str:
    return reverse("stories:backfill", args=[story_id])


def _flag(request: HttpRequest, name: str) -> bool:
    return request.GET.get(name) == "1"


def _not_found(exc: EngineClientError) -> bool:
    return isinstance(exc, EngineError) and exc.http_status == 404 and exc.code == NOT_FOUND


def _read(client: EngineClient, story_id: str, everything: bool) -> StoryBackfill:
    return client.get_backfill(
        story_id, conflicts=ALL if everything else OPEN, limit=CONFLICT_LIMIT
    )


def _block(story_id: str, view: BackfillView | None, *, retrying: bool = False) -> dict[str, Any]:
    """The polled block's context: E7's job context for ``view``, or an empty polling block
    (``retrying`` with no backfill read yet)."""
    base = page_url(story_id)
    poll_url = f"{base}?{PARTIAL_PARAM}=1"
    if view is None:
        return {
            **poll_context(
                poll_url=poll_url,
                refresh_url=base,
                body_template=BODY_TEMPLATE,
                polling=retrying,
                retrying=retrying,
                trigger=SLOW_POLL_TRIGGER,
            ),
            "backfill": None,
        }
    return {
        **job_poll_context(
            "backfill",
            view.id,
            view.status,
            poll_url=poll_url,
            refresh_url=base,
            body_template=BODY_TEMPLATE,
            progress=view.progress,
            retrying=retrying,
        ),
        "backfill": view,
        "error": view.error or None,
        "facts": (),
        "refresh_conflicts_text": REFRESH_CONFLICTS,
    }


# --- ?banner=1 ------------------------------------------------------------------------------------


def _banner(request: HttpRequest, story_id: str) -> HttpResponse:
    client = engine_client_for(request)
    try:
        story = client.get_story(story_id)
    except EngineClientError as exc:
        failure = poll_failure(request, exc)
        if not failure.retry:
            return HttpResponse("")
        cause = request.GET.get(CAUSE_PARAM, "")
        banner = PauseBanner(
            story_id, cause if cause in BACKFILL_CAUSES else BACKFILL, None, None, None,
            retrying=True,
        )  # fmt: skip
        return render(request, BANNER_TEMPLATE, {"pause_banner": banner})
    banner_found = pause_banner(story)
    if banner_found is None:
        return HttpResponse("")
    return render(request, BANNER_TEMPLATE, {"pause_banner": banner_found})


# --- ?partial=1 -----------------------------------------------------------------------------------


def _partial(request: HttpRequest, story_id: str) -> HttpResponse:
    client = engine_client_for(request)
    try:
        backfill = _read(client, story_id, everything=False)
    except EngineClientError as exc:
        return _partial_failed(request, story_id, exc)
    view = backfill_view(backfill, story_id, {})
    response = render(request, POLL_TEMPLATE, _block(story_id, view))
    if not view.state.polling:
        response["HX-Refresh"] = "true"  # the full page shows what the block doesn't
    return response


def _partial_failed(request: HttpRequest, story_id: str, exc: EngineClientError) -> HttpResponse:
    context = _block(story_id, None)
    if is_held(exc):
        log_engine_error(request, exc)
        held = held_context(story_id, exc.retry_after, page_url(story_id))
        held.pop("delete_url")
        return render(request, POLL_TEMPLATE, {**context, **held, "held": True})
    if _not_found(exc):
        log_engine_error(request, exc)
        return render(request, POLL_TEMPLATE, {**context, "no_backfill": NO_BACKFILL})
    failure = poll_failure(request, exc)
    if failure.retry:
        return render(request, POLL_TEMPLATE, _block(story_id, None, retrying=True))
    return render(request, POLL_TEMPLATE, {**context, "poll_panel": panel_for(exc)})


# --- the page -------------------------------------------------------------------------------------


def _build(ctx: StoryPageContext) -> dict[str, Any]:
    everything = ctx.request.GET.get(CONFLICTS_PARAM) == ALL
    config = ctx.config
    pending = pending_change(config.pending_backfill) if config is not None else None
    base = page_url(ctx.story_id)
    context: dict[str, Any] = {
        "pending": pending,
        "everything": everything,
        "show_all_url": f"{base}?{CONFLICTS_PARAM}={ALL}",
        "show_open_url": base,
        "show_all_text": SHOW_ALL,
        "show_open_text": SHOW_OPEN,
        "omniscient_note": OMNISCIENT_NOTE,
    }
    try:
        backfill = _read(ctx.client, ctx.story_id, everything)
    except EngineClientError as exc:
        if not _not_found(exc):
            raise
        log_engine_error(ctx.request, exc)
        return {**context, "no_backfill": NO_BACKFILL}
    names = cast_names(ctx.story.cast if ctx.story is not None else None)
    view = backfill_view(backfill, ctx.story_id, names, everything=everything)
    return {**context, **_block(ctx.story_id, view)}


@require_safe
def backfill_page(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:backfill``: the banner fragment, the polled block or the page (module
    docstring)."""
    if _flag(request, BANNER_PARAM):
        return _banner(request, story_id)
    if _flag(request, PARTIAL_PARAM):
        return _partial(request, story_id)
    return story_page(request, story_id, TEMPLATE, _build, current="stories:backfill")


# --- cancel ---------------------------------------------------------------------------------------


def cancel_page(story_id: str, data: Any) -> ConfirmPage:
    backfill_id = data.get(BACKFILL_PARAM, "") or ""
    status = data.get(STATUS_PARAM, "") or ""
    return ConfirmPage(
        title=CANCEL_TITLE,
        question=CANCEL_QUESTION,
        confirm_label=CANCEL_LABEL,
        cancel_url=page_url(story_id),
        lines=CANCEL_LINES,
        facts=(
            ("Story", story_id),
            ("Backfill id", backfill_id or EMPTY),
            ("Status", status_fact(status)),
        ),
        hidden={BACKFILL_PARAM: backfill_id, STATUS_PARAM: status},
        danger=True,
    )


def _unknown(exc: EngineClientError) -> bool:
    return isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )


def _refusal_text(exc: EngineError, story_id: str) -> tuple[str, str]:
    if exc.code == NOT_CANCELLABLE:
        return NOT_CANCELLABLE_TEXT, ""
    if exc.code == NOT_FOUND:
        return NO_BACKFILL, ""
    if is_importing(exc):
        refusal = importing_refusal(exc)
        return refusal.text, refusal.url
    found = write_refusal(exc, story_id, CANCEL_VERB)
    if found is not None:
        return found.text, found.url
    panel = panel_for(exc)
    return (f"{panel.headline} — {panel.message}" if panel.message else panel.headline), ""


def _link_label(exc: EngineError) -> str:
    return IMPORT_JOB_LINK if is_importing(exc) else DETAILS_LINK


def _cancel_refused(
    request: HttpRequest, page: ConfirmPage, exc: EngineClientError, story_id: str, key: str
) -> HttpResponse:
    log_engine_error(request, exc)
    if _unknown(exc) or not isinstance(exc, EngineError):
        return render_confirm(
            request, page, extra={"engine_error": panel_for(exc)}, idempotency_key=key
        )
    text, url = _refusal_text(exc, story_id)
    error_page = dataclasses.replace(
        page, error=text, error_url=url, error_link=_link_label(exc) if url else ""
    )
    return render_confirm(request, error_page)  # an answered refusal: a fresh key (E21)


@require_http_methods(["GET", "HEAD", "POST"])
def backfill_cancel(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:backfill-cancel``: the confirm page, then ``DELETE /stories/{id}/backfill``
    and a 303 to the Backfill page (module docstring)."""
    data = request.POST if request.method == "POST" else request.GET
    page = cancel_page(story_id, data)

    def perform(key: str) -> HttpResponse:
        client = engine_client_for(request)
        try:
            answer = client.cancel_backfill(story_id, idempotency_key=key)
        except EngineClientError as exc:
            if is_held(exc):
                return render_held(request, story_id, exc)
            return _cancel_refused(request, page, exc, story_id, key)
        messages.success(
            request,
            format_html(
                "{} — idempotency key <code>{}</code>",
                CANCELLED_TEXT,
                answer.idempotency_key or key,
            ),
        )
        response = redirect(page_url(story_id))
        response.status_code = 303
        return response

    return confirm_action(request, page, perform, require_confirmed=True)

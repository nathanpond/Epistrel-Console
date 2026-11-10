"""The story's Backfill page and its cancel (#308; E7, E10, E11, E21; ``console/backfill.py``).

``stories:backfill`` (``/stories/<id>/backfill/``, GET/HEAD) answers three ways, ``banner``
first, then ``partial``, then the page:

- **``?banner=1``** (the pause banner's poll, #80/E10): one ``GET /stories/{id}``, rendered
  through ``console.story_pause.pause_banner``. While the story is paused the fragment is the
  banner (a backfill cause polls this URL at ``SLOW_POLL_TRIGGER``; a rating or reactivation
  check keeps its own poll). Once it isn't, an empty 200 with no trigger: the banner goes and
  polling stops. A failed read follows ``poll_failure``: unreachable or any 5xx (#353: the
  story read can 500 while a backfill pauses it, Engine #783) keeps the banner polling with the
  cause's paused text, no counts, and ``RETRYING_TEXT``; anything else is an empty 200. It never
  reads the backfill or a rating check.
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

**#309: the three writes that steer a backfill** (E8, E21, E24). Every re-render below is the
full page (200) through :func:`_page_build`'s :class:`PageExtras`, so it reads the backfill
again; "no Engine call" means no write.

- ``stories:backfill-conflict`` (``backfill/conflicts/<conflict_id>/``, POST only): the URL's id
  is trimmed and upper-cased (C13); one that fails ``is_ulid`` is the 404 page
  :data:`~console.backfill.MALFORMED_CONFLICT_ID` with no call. A ``get_backfill(open, 200)``
  read finds the conflict for ``ConflictResolveForm``'s local ``choose`` check (missed or failed
  → the Engine decides); an invalid form re-renders with ``NO_CHOICE`` on that conflict. The
  write is ``resolve_backfill_conflict`` with ``body()`` under the form's key; success flashes
  :func:`~console.backfill.resolved_text` with E21's key tail and answers 303 to ``#conflicts``
  (``#backfill-status`` once the backfill completed). Refusals re-render with the reason on that
  conflict's form (a fresh key): closed, not awaiting, not found, ``invalid_resolution`` (on
  ``value`` when one was typed, else the radios), ``validation_error`` by loc, E10's importing
  text, C5, else #43's panel. An unknown outcome keeps the posted key on that form only. A
  refused conflict missing from the fresh read shows its line above the conflict list.
- ``stories:backfill-start`` (``backfill/start/``, POST only) serves both modes from the hidden
  ``mode`` (``start`` | ``resume``). A POST without ``twostage.CONFIRM_FIELD`` is the first
  stage: ``start_backfill(capabilities=<ticked or None>, ignore_warnings=False)`` (resume sends
  no capabilities). A POST with it is the confirm: unsigned under ``backfill-start`` or
  ``backfill-resume`` by ``mode`` (an expired token, another story's, or the other mode's kind
  → ``EXPIRED_TEXT``, 303, zero calls), re-sending the signed body with ``ignore_warnings: true``
  under the partial's key. Either answer branches (:func:`_answered`) on ``started`` (the 202:
  "Gap fill started", 303) before anything else; then a resume preview (``conflicts_open``,
  ``applied`` false) renders the resume confirm signed ``{ignore_warnings: true}`` under
  ``backfill-resume`` with ``base`` ``{conflicts_open, subjects}``; then ``applied`` (a resume
  that finished) flashes :func:`~console.backfill.finished_text`; else the gap-fill preview
  renders ``_gap_fill_preview.html`` signed under ``backfill-start`` with ``base``
  ``{preview}`` (no token when ``needed`` is false). The Engine's state wins over the posted
  mode. Refusals at either stage re-render the plain form with the reason (nothing to fill, in
  progress, ``validation_error`` at ``body.capabilities`` on the checkboxes (one naming
  ``details.not_enabled`` reads :func:`~console.backfill.not_fillable_text`, #340), E10, C5,
  else the panel); a confirm-stage refusal keeps the signed preview above it, muted (a stale
  resume's ``nothing_to_backfill`` shows the gap-fill form instead, #352). An unknown outcome
  on the confirm re-renders the same confirm (token and key) with #43's panel.
- **The page's ``#gap-fill`` section** (:func:`_default_gap`): the "Fill coverage gaps…" form
  when there is no backfill or it ended, "Finish now…" while it waits on conflicts, nothing
  while it runs. The checkboxes are the frame's effective configuration's ``gap_fillable``
  (no second read; #340, nathanpond/Epistrel#790), the only names the Engine's gap fill
  accepts; an empty list shows none and :data:`~console.backfill.NOTHING_GAP_FILLABLE`. An
  Engine before #790 sends no ``gap_fillable``: the boxes are then ``capabilities``, and a
  refused one gets :func:`~console.backfill.not_fillable_text`. A failed read, or an empty
  ``capabilities`` there, shows none and :data:`~console.backfill.CAPABILITIES_UNREAD`.
"""

import dataclasses
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from console import twostage
from console.backfill import (
    ALL,
    ANSWER_UNREADABLE,
    BACK_TEXT,
    BACKFILL_PARAM,
    CANCEL_LABEL,
    CANCEL_LINES,
    CANCEL_QUESTION,
    CANCEL_TITLE,
    CANCEL_VERB,
    CANCELLED_TEXT,
    CAPABILITIES_UNREAD,
    CONFLICT_CLOSED_TEXT,
    CONFLICT_LIMIT,
    CONFLICT_NOT_FOUND_TEXT,
    CONFLICTS_PARAM,
    FINISH_HEADING,
    FINISH_NOW_LABEL,
    FINISH_NOW_LINK,
    GAP_FILL_HEADING,
    GAP_FILL_HELP,
    GAP_FILL_STARTED,
    GAP_FILL_VERB,
    IN_PROGRESS_TEXT,
    MALFORMED_CONFLICT_ID,
    NO_BACKFILL,
    NOT_AWAITING_TEXT,
    NOT_CANCELLABLE_TEXT,
    NOTHING_GAP_FILLABLE,
    NOTHING_TO_FILL,
    OMNISCIENT_NOTE,
    OPEN,
    PAUSES_LINE,
    PREVIEW_LABEL,
    REFRESH_CONFLICTS,
    RESOLVE_VERB,
    RESUME_VERB,
    SHOW_ALL,
    SHOW_OPEN,
    START_LABEL,
    STATUS_PARAM,
    UNRESOLVABLE_ENTRY_TEXT,
    BackfillView,
    GapFillPreviewView,
    backfill_view,
    conflict_url,
    finished_text,
    gap_fill_preview,
    not_fillable_text,
    pending_change,
    resolved_text,
    resume_line,
    start_url,
    status_fact,
    subject_summary,
)
from console.canon import cast_names
from console.confirm import ConfirmPage, confirm_action, posted_idempotency_key, render_confirm
from console.engine.client import EngineClient, EngineClientError, EngineError, EngineUnreachable
from console.engine.models import BackfillConflict, GapFill, GapFillPreview, StoryBackfill
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import engine_field_errors
from console.forms.backfill import (
    ACCEPT_LABEL,
    CAPABILITIES,
    CHOOSE,
    MODE,
    MODES,
    RESOLVE_LABEL,
    RESUME,
    START,
    VALUE,
    ConflictResolveForm,
    GapFillForm,
)
from console.held import held_context, is_held, render_held
from console.ids import is_ulid, new_ulid
from console.job_poll import POLL_TEMPLATE, poll_context, poll_failure
from console.jobs import PARTIAL_PARAM, SLOW_POLL_TRIGGER, job_poll_context
from console.portability import importing_refusal, is_importing
from console.raw import EMPTY
from console.story_pause import BANNER_PARAM, CAUSE_PARAM, PauseBanner, pause_banner
from console.upgrades import BACKFILL, BACKFILL_CAUSES, GAP_FILL_ANCHOR
from console.views.story_pages import StoryBuild, StoryPageContext, story_page
from console.wording import write_refusal

TEMPLATE = "console/stories/backfill.html"
BODY_TEMPLATE = "console/stories/_backfill_body.html"
BANNER_TEMPLATE = "console/stories/_pause_banner.html"
NOT_FOUND = "backfill_not_found"
NOT_CANCELLABLE = "backfill_not_cancellable"
BAD_RESPONSE = "bad_response"
IMPORT_JOB_LINK = "Import job"
DETAILS_LINK = "Details"
STATUS_ANCHOR = "backfill-status"
CONFLICTS_ANCHOR = "conflicts"
RESOLVE_READ_LIMIT = 200  # the local ``choose`` check's read (the Engine's maximum)


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


def _server_error(exc: EngineClientError) -> bool:
    """A 5xx from the story read keeps the banner polling (#353): the Engine's story read can
    fail while a backfill pauses the story (nathanpond/Epistrel#783), and the pause outlives it."""
    return isinstance(exc, EngineError) and exc.http_status >= 500


def _banner(request: HttpRequest, story_id: str) -> HttpResponse:
    client = engine_client_for(request)
    try:
        story = client.get_story(story_id)
    except EngineClientError as exc:
        failure = poll_failure(request, exc)
        if not failure.retry and not _server_error(exc):
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


@dataclass(frozen=True)
class ResolveBlock:
    """One open conflict's resolve form: the form, its key, where it posts, and a refusal's
    text (and link) or #43's panel."""

    form: ConflictResolveForm
    key: str
    action_url: str
    error: str = ""
    error_url: str = ""
    panel: ErrorPanel | None = None


@dataclass(frozen=True)
class ResolveOverride:
    """A resolve POST's re-render (#309): the conflict, its posted data, the errors to place
    (``(field or None, message)``), its refusal line or panel and the key to keep ("" mints a
    fresh one)."""

    conflict_id: str
    data: Any
    errors: tuple[tuple[str | None, str], ...] = ()
    error: str = ""
    error_url: str = ""
    panel: ErrorPanel | None = None
    key: str = ""


@dataclass(frozen=True)
class GapBlock:
    """The page's ``#gap-fill`` section (#309): the "Fill coverage gaps…" form (``mode``
    start) or "Finish now…" (resume); a preview or the resume confirm with its token; a
    refusal's text, link or panel; ``muted`` keeps a previewed summary above the plain form
    after a confirm-stage refusal."""

    mode: str
    action_url: str
    page_url: str
    form: GapFillForm
    key: str
    capabilities_note: str = ""
    error: str = ""
    error_url: str = ""
    panel: ErrorPanel | None = None
    preview: GapFillPreviewView | None = None
    resume_line: str = ""
    subjects: tuple[tuple[tuple[str, bool], ...], ...] = ()
    token: str = ""
    confirm_key: str = ""
    muted: bool = False

    @property
    def confirming(self) -> bool:
        return bool(self.token) or (self.preview is not None and not self.muted)

    @property
    def resume(self) -> bool:
        return self.mode == RESUME

    @property
    def confirm_label(self) -> str:
        return FINISH_NOW_LABEL if self.resume else START_LABEL

    @property
    def hidden(self) -> tuple[tuple[str, str], ...]:
        return ((MODE, self.mode),)


GapFactory = Callable[[StoryPageContext, Mapping[str, str]], GapBlock]


@dataclass(frozen=True)
class PageExtras:
    """What a POST's re-render adds to the page: a conflict's form state, a page-level line
    over the conflict list, and the gap-fill block."""

    resolve: ResolveOverride | None = None
    conflicts_error: str = ""
    conflicts_panel: ErrorPanel | None = None
    gap: GapFactory | None = None


def _capability_choices(ctx: StoryPageContext) -> tuple[list[str], str]:
    """The gap fill's boxes and the note under them (module docstring, #340)."""
    config = ctx.config
    if config is not None and config.gap_fillable is not None:
        fillable = list(config.gap_fillable)
        return fillable, "" if fillable else NOTHING_GAP_FILLABLE
    found = list(config.capabilities) if config is not None else []
    return found, "" if found else CAPABILITIES_UNREAD


def plain_gap(
    ctx: StoryPageContext,
    mode: str,
    *,
    data: Any = None,
    key: str = "",
    **changes: Any,
) -> GapBlock:
    """The plain form (no preview), bound to ``data`` when given."""
    choices, note = _capability_choices(ctx)
    form = GapFillForm(data, choices=choices, mode=mode)
    if data is not None:
        form.is_valid()
    return GapBlock(
        mode=mode,
        action_url=start_url(ctx.story_id) or "",
        page_url=page_url(ctx.story_id),
        form=form,
        key=key or new_ulid(),
        capabilities_note=note,
        **changes,
    )


def _default_gap(ctx: StoryPageContext, view: BackfillView | None) -> GapBlock | None:
    """The form a GET shows: start when there is no backfill or it ended, "Finish now…" while
    it waits on conflicts, none while it runs (or while the URL is unregistered, D19)."""
    if start_url(ctx.story_id) is None:
        return None
    if view is None or view.state.terminal:
        return plain_gap(ctx, START)
    if view.state.waiting:
        return plain_gap(ctx, RESUME)
    return None


def _resolve_blocks(
    ctx: StoryPageContext,
    backfill: StoryBackfill,
    names: Mapping[str, str],
    override: ResolveOverride | None,
) -> dict[str, ResolveBlock]:
    """Each open conflict's form; the overridden one bound to its POST with its errors."""
    blocks: dict[str, ResolveBlock] = {}
    for conflict in backfill.conflict_list:
        url = conflict_url(ctx.story_id, conflict.id)
        if conflict.status != OPEN or url is None:
            continue
        if override is not None and override.conflict_id == conflict.id:
            form = ConflictResolveForm(conflict.id, conflict, override.data, names=names)
            form.is_valid()
            for field, message in override.errors:
                form.add_error(field, message)
            blocks[conflict.id] = ResolveBlock(
                form,
                override.key or new_ulid(),
                url,
                override.error,
                override.error_url,
                override.panel,
            )
            continue
        blocks[conflict.id] = ResolveBlock(
            ConflictResolveForm(conflict.id, conflict, names=names), new_ulid(), url
        )
    return blocks


def _override_line(override: ResolveOverride | None) -> str:
    """A refused conflict that has no form on the fresh read: its line for the list's top."""
    if override is None:
        return ""
    messages_found = [message for _, message in override.errors]
    if override.error:
        messages_found.insert(0, override.error)
    if override.panel is not None and not messages_found:
        messages_found.append(override.panel.headline)
    return " · ".join(messages_found)


def _page_build(extras: PageExtras) -> StoryBuild:
    def build(ctx: StoryPageContext) -> dict[str, Any]:
        everything = ctx.request.GET.get(CONFLICTS_PARAM) == ALL
        config = ctx.config
        pending = pending_change(config.pending_backfill) if config is not None else None
        base = page_url(ctx.story_id)
        names = cast_names(ctx.story.cast if ctx.story is not None else None)
        context: dict[str, Any] = {
            "pending": pending,
            "everything": everything,
            "show_all_url": f"{base}?{CONFLICTS_PARAM}={ALL}",
            "show_open_url": base,
            "show_all_text": SHOW_ALL,
            "show_open_text": SHOW_OPEN,
            "omniscient_note": OMNISCIENT_NOTE,
            "reload_url": base if ctx.request.method == "POST" else ctx.request.get_full_path(),
            "gap_fill_heading": GAP_FILL_HEADING,
            "finish_heading": FINISH_HEADING,
            "gap_fill_help": GAP_FILL_HELP,
            "finish_now_text": FINISH_NOW_LINK,
            "preview_label": PREVIEW_LABEL,
            "pauses_line": PAUSES_LINE,
            "nothing_to_fill": NOTHING_TO_FILL,
            "back_text": BACK_TEXT,
            "unresolvable_text": UNRESOLVABLE_ENTRY_TEXT,
            "resolve_label": RESOLVE_LABEL,
            "accept_label": ACCEPT_LABEL,
        }
        try:
            backfill = _read(ctx.client, ctx.story_id, everything)
        except EngineClientError as exc:
            if not _not_found(exc):
                raise
            log_engine_error(ctx.request, exc)
            gap = extras.gap(ctx, names) if extras.gap else _default_gap(ctx, None)
            return {**context, "no_backfill": NO_BACKFILL, "gap": gap}
        view = backfill_view(backfill, ctx.story_id, names, everything=everything)
        blocks = _resolve_blocks(ctx, backfill, names, extras.resolve)
        orphan = extras.resolve is not None and extras.resolve.conflict_id not in blocks
        gap = extras.gap(ctx, names) if extras.gap else _default_gap(ctx, view)
        return {
            **context,
            **_block(ctx.story_id, view),
            "resolve_forms": blocks,
            "resolvable": conflict_url(ctx.story_id, backfill.id) is not None,
            "conflicts_error": extras.conflicts_error
            or (_override_line(extras.resolve) if orphan else ""),
            "conflicts_panel": extras.conflicts_panel,
            "gap": gap,
        }

    return build


def _render_page(request: HttpRequest, story_id: str, extras: PageExtras) -> HttpResponse:
    return story_page(request, story_id, TEMPLATE, _page_build(extras), current="stories:backfill")


@require_safe
def backfill_page(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:backfill``: the banner fragment, the polled block or the page (module
    docstring)."""
    if _flag(request, BANNER_PARAM):
        return _banner(request, story_id)
    if _flag(request, PARTIAL_PARAM):
        return _partial(request, story_id)
    return _render_page(request, story_id, PageExtras())


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


# --- #309: resolve one conflict -------------------------------------------------------------------

INVALID_RESOLUTION = "invalid_resolution"
VALIDATION_ERROR = "validation_error"
CONFLICT_CLOSED = "backfill_conflict_closed"
NOT_AWAITING = "backfill_not_awaiting_conflicts"
CONFLICT_NOT_FOUND = "backfill_conflict_not_found"
NOTHING_TO_BACKFILL = "nothing_to_backfill"
IN_PROGRESS = "backfill_in_progress"
START_KIND = "backfill-start"
RESUME_KIND = "backfill-resume"
RESOLVE_FIELDS: Mapping[str, str] = {
    "value": VALUE,
    "choose": CHOOSE,
    "accept_recommendation": CHOOSE,
}


def _see_other(url: str) -> HttpResponse:
    response = redirect(url)
    response.status_code = 303
    return response


def _key_tail(text: str, key: str) -> SafeString:
    return format_html("{} — idempotency key <code>{}</code>", text, key)


def _loc_field(path: str) -> str:
    """E21: a ``body.<field>`` (or ``body.<field>.0`` / ``<field>[0]``) loc's field name."""
    stripped = path.removeprefix("body.")
    return re.split(r"[.\[]", stripped, maxsplit=1)[0]


def _refusal_line(exc: EngineError, story_id: str, verb: str) -> tuple[str, str] | None:
    """E10's importing text, else C5's ``write_refusal``; ``None`` → #43's panel."""
    if is_importing(exc):
        refusal = importing_refusal(exc)
        return refusal.text, refusal.url
    found = write_refusal(exc, story_id, verb)
    return (found.text, found.url) if found is not None else None


def _malformed_conflict(request: HttpRequest) -> HttpResponse:
    context = {"engine_error": ErrorPanel(MALFORMED_CONFLICT_ID)}
    return render(request, "console/engine_error_page.html", context, status=404)


def _open_conflict(
    request: HttpRequest, client: EngineClient, story_id: str, conflict_id: str
) -> BackfillConflict | HttpResponse | None:
    """The conflict from a ``get_backfill(open, 200)`` read, for the local ``choose`` check;
    ``None`` when the read missed it or failed (the Engine decides); a held story's page."""
    try:
        backfill = client.get_backfill(story_id, conflicts=OPEN, limit=RESOLVE_READ_LIMIT)
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        log_engine_error(request, exc)
        return None
    return next((c for c in backfill.conflict_list if c.id == conflict_id), None)


def _resolve_refusal(
    exc: EngineClientError, form: ConflictResolveForm, story_id: str, key: str, data: Any
) -> ResolveOverride:
    cid = form.conflict_id
    if _unknown(exc) or not isinstance(exc, EngineError):
        return ResolveOverride(cid, data, panel=panel_for(exc), key=key)
    if exc.code == CONFLICT_CLOSED:
        return ResolveOverride(cid, data, error=CONFLICT_CLOSED_TEXT)
    if exc.code == NOT_AWAITING:
        return ResolveOverride(cid, data, error=NOT_AWAITING_TEXT)
    if exc.code in (CONFLICT_NOT_FOUND, NOT_FOUND):
        return ResolveOverride(cid, data, error=CONFLICT_NOT_FOUND_TEXT)
    if exc.code == INVALID_RESOLUTION:
        return ResolveOverride(cid, data, errors=((form.choice_error_field(), exc.message),))
    if exc.code == VALIDATION_ERROR:
        failures = engine_field_errors(exc)
        placed = tuple(
            (RESOLVE_FIELDS[_loc_field(f.path)], f.message or exc.message)
            for f in failures
            if _loc_field(f.path) in RESOLVE_FIELDS
        )
        if placed and len(placed) == len(failures):
            return ResolveOverride(cid, data, errors=placed)
        return ResolveOverride(cid, data, errors=placed, panel=panel_for(exc))
    line = _refusal_line(exc, story_id, RESOLVE_VERB)
    if line is not None:
        return ResolveOverride(cid, data, error=line[0], error_url=line[1])
    return ResolveOverride(cid, data, panel=panel_for(exc))


@require_POST
def backfill_conflict(request: HttpRequest, story_id: str, conflict_id: str) -> HttpResponse:
    """``stories:backfill-conflict``: resolve one conflict (module docstring)."""
    cid = conflict_id.strip().upper()
    if not is_ulid(cid):
        return _malformed_conflict(request)
    client = engine_client_for(request)
    found = _open_conflict(request, client, story_id, cid)
    if isinstance(found, HttpResponse):
        return found
    form = ConflictResolveForm(cid, found, request.POST)
    if not form.is_valid():  # E24: the page reads the backfill again; no write
        override = ResolveOverride(cid, request.POST)
        return _render_page(request, story_id, PageExtras(resolve=override))
    key = posted_idempotency_key(request)
    try:
        answer = client.resolve_backfill_conflict(story_id, cid, form.body(), idempotency_key=key)
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        log_engine_error(request, exc)
        override = _resolve_refusal(exc, form, story_id, key, request.POST)
        return _render_page(request, story_id, PageExtras(resolve=override))
    text, completed = resolved_text(answer.backfill)
    messages.success(request, _key_tail(text, answer.idempotency_key or key))
    anchor = STATUS_ANCHOR if completed else CONFLICTS_ANCHOR
    return _see_other(f"{page_url(story_id)}#{anchor}")


# --- #309: finish now and the gap fill ------------------------------------------------------------


def _token_kind(mode: str) -> str:
    return RESUME_KIND if mode == RESUME else START_KIND


def _preview_gap(
    preview_raw: Any, body: Mapping[str, Any], *, token: str = "", key: str = ""
) -> GapFactory | None:
    """A gap-fill preview block from the raw preview (an answer's ``backfill`` or a signed
    ``base``), with a fresh token signing ``body`` when needed; ``None`` when unreadable."""
    preview = GapFillPreview.from_json(preview_raw)
    if preview is None:
        return None
    view = gap_fill_preview(preview)

    def factory(ctx: StoryPageContext, names: Mapping[str, str]) -> GapBlock:
        signed = token
        if not signed and view.needed:
            signed = twostage.sign(START_KIND, ctx.story_id, dict(body), {"preview": preview_raw})
        return plain_gap(
            ctx,
            START,
            preview=view,
            token=signed if view.needed else "",
            confirm_key=key or new_ulid(),
        )

    return factory


def _resume_base(conflicts_open: int, conflicts: Any, names: Mapping[str, str]) -> dict[str, Any]:
    subjects = [subject_summary(c.subject, names) for c in conflicts or ()]
    return {"conflicts_open": conflicts_open, "subjects": subjects}


def _subjects(base: Mapping[str, Any]) -> tuple[tuple[tuple[str, bool], ...], ...]:
    found = base.get("subjects")
    rows: list[tuple[tuple[str, bool], ...]] = []
    for subject in found if isinstance(found, list) else []:
        if isinstance(subject, list):
            rows.append(
                tuple(
                    (str(part[0]), bool(part[1]))
                    for part in subject
                    if isinstance(part, list) and len(part) == 2
                )
            )
    return tuple(rows)


def _resume_gap(base: Mapping[str, Any], *, token: str = "", key: str = "") -> GapFactory:
    def factory(ctx: StoryPageContext, names: Mapping[str, str]) -> GapBlock:
        n = base.get("conflicts_open")
        return plain_gap(
            ctx,
            RESUME,
            resume_line=resume_line(n) if isinstance(n, int) else "",
            subjects=_subjects(base),
            token=token,
            confirm_key=key or new_ulid(),
        )

    return factory


def _resume_from_answer(answer: GapFill) -> GapFactory:
    def factory(ctx: StoryPageContext, names: Mapping[str, str]) -> GapBlock:
        base = _resume_base(answer.conflicts_open or 0, answer.conflicts, names)
        token = twostage.sign(RESUME_KIND, ctx.story_id, {"ignore_warnings": True}, base)
        return _resume_gap(base, token=token)(ctx, names)

    return factory


def _answered(
    request: HttpRequest, story_id: str, answer: GapFill, key: str, body: Mapping[str, Any]
) -> HttpResponse:
    """E8's branches on a start/resume answer: ``started`` (the 202) first, never ``applied``;
    a resume preview; a resume that finished (applied at once); a gap-fill preview."""
    shown_key = answer.idempotency_key or key
    if answer.started is not None:
        messages.success(request, _key_tail(GAP_FILL_STARTED, shown_key))
        return _see_other(page_url(story_id))
    if answer.conflicts_open is not None and not answer.applied:
        return _render_page(request, story_id, PageExtras(gap=_resume_from_answer(answer)))
    if answer.applied:
        messages.success(
            request, _key_tail(finished_text(answer.conflicts_made_uncertain), shown_key)
        )
        return _see_other(page_url(story_id))
    factory = _preview_gap(answer.backfill, body)
    if factory is None:

        def unreadable(ctx: StoryPageContext, names: Mapping[str, str]) -> GapBlock:
            return plain_gap(ctx, START, error=ANSWER_UNREADABLE)

        return _render_page(request, story_id, PageExtras(gap=unreadable))
    return _render_page(request, story_id, PageExtras(gap=factory))


def _not_enabled(exc: EngineError) -> list[str]:
    """The capabilities a gap fill's 422 refused (``details.not_enabled``, #340): the Engine
    fills only its data-producing capabilities, named without a ``:mode`` suffix."""
    found = exc.details.get("not_enabled")
    return [c for c in found if isinstance(c, str)] if isinstance(found, list) else []


def _start_refused(
    request: HttpRequest,
    story_id: str,
    exc: EngineClientError,
    *,
    mode: str,
    key: str,
    kept: GapFactory | None = None,
) -> HttpResponse:
    """A refusal at either stage: the plain form with the refusal's text (a fresh key), or
    #43's panel (an unknown outcome keeps the posted key); ``kept`` re-shows what was
    previewed, muted, above it. ``nothing_to_backfill`` on a resume (a stale "Finish now…"
    once the backfill ended) follows the Engine (#309, #352): the gap-fill form in ``start``
    mode, with nothing kept from the resume."""
    data = request.POST if mode == START else None
    if mode == RESUME and isinstance(exc, EngineError) and exc.code == NOTHING_TO_BACKFILL:
        mode, kept = START, None
    verb = RESUME_VERB if mode == RESUME else GAP_FILL_VERB
    changes: dict[str, Any] = {}
    errors: list[tuple[str, str]] = []
    if _unknown(exc) or not isinstance(exc, EngineError):
        changes.update(panel=panel_for(exc), key=key)
    elif exc.code == NOTHING_TO_BACKFILL:
        changes["error"] = NOTHING_TO_FILL
    elif exc.code == IN_PROGRESS:
        changes["error"] = IN_PROGRESS_TEXT
    elif exc.code == VALIDATION_ERROR and _not_enabled(exc):
        errors = [(CAPABILITIES, not_fillable_text(_not_enabled(exc)))]
    elif exc.code == VALIDATION_ERROR:
        failures = engine_field_errors(exc)
        errors = [
            (CAPABILITIES, f.message or exc.message)
            for f in failures
            if _loc_field(f.path) == CAPABILITIES
        ]
        if len(errors) != len(failures) or not failures:
            changes["panel"] = panel_for(exc)
    else:
        line = _refusal_line(exc, story_id, verb)
        if line is not None:
            changes.update(error=line[0], error_url=line[1])
        else:
            changes["panel"] = panel_for(exc)

    def factory(ctx: StoryPageContext, names: Mapping[str, str]) -> GapBlock:
        block = plain_gap(ctx, mode, data=data, **changes)
        for field, message in errors:
            block.form.add_error(field, message)
        if kept is None:
            return block
        shown = kept(ctx, names)
        return dataclasses.replace(
            block,
            preview=shown.preview,
            resume_line=shown.resume_line,
            subjects=shown.subjects,
            muted=True,
        )

    return _render_page(request, story_id, PageExtras(gap=factory))


def _confirm_again(
    request: HttpRequest, story_id: str, payload: twostage.Payload, mode: str, token: str,
    key: str, exc: EngineClientError,
) -> HttpResponse:  # fmt: skip
    """E21: an unknown outcome on the confirm re-renders the same confirm with #43's panel,
    the same token and the same key, so a resubmit replays."""
    base = payload.base if isinstance(payload.base, dict) else {}
    factory: GapFactory | None
    if mode == RESUME:
        factory = _resume_gap(base, token=token, key=key)
    else:
        factory = _preview_gap(base.get("preview"), payload.body, token=token, key=key)
    if factory is None:
        return _start_refused(request, story_id, exc, mode=mode, key=key)
    inner = factory

    def with_panel(ctx: StoryPageContext, names: Mapping[str, str]) -> GapBlock:
        return dataclasses.replace(inner(ctx, names), panel=panel_for(exc))

    return _render_page(request, story_id, PageExtras(gap=with_panel))


def _kept(payload: twostage.Payload, mode: str) -> GapFactory | None:
    base = payload.base if isinstance(payload.base, dict) else {}
    if mode == RESUME:
        return _resume_gap(base)
    return _preview_gap(base.get("preview"), payload.body)


def _start_confirm(request: HttpRequest, story_id: str, mode: str) -> HttpResponse:
    token = request.POST.get(twostage.CONFIRM_FIELD, "")
    try:
        payload = twostage.unsign(_token_kind(mode), token)
    except twostage.ConfirmationExpired:
        payload = None
    if payload is None or payload.target_id != story_id or not isinstance(payload.body, dict):
        messages.error(request, twostage.EXPIRED_TEXT)
        return _see_other(f"{page_url(story_id)}#{GAP_FILL_ANCHOR}")
    body: dict[str, Any] = payload.body
    capabilities = body.get(CAPABILITIES)
    chosen = (
        [c for c in capabilities if isinstance(c, str)] if isinstance(capabilities, list) else None
    )
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        answer = client.start_backfill(
            story_id, capabilities=chosen, ignore_warnings=True, idempotency_key=key
        )
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        log_engine_error(request, exc)
        if _unknown(exc) or not isinstance(exc, EngineError):
            return _confirm_again(request, story_id, payload, mode, token, key, exc)
        return _start_refused(request, story_id, exc, mode=mode, key=key, kept=_kept(payload, mode))
    return _answered(request, story_id, answer, key, body)


@require_POST
def backfill_start(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:backfill-start``: a gap fill's or a resume's two stages (module docstring)."""
    posted_mode = request.POST.get(MODE, START)
    mode = posted_mode if posted_mode in MODES else START
    if twostage.CONFIRM_FIELD in request.POST:
        return _start_confirm(request, story_id, mode)
    form = GapFillForm(request.POST, mode=mode)
    form.is_valid()
    chosen = form.chosen() if mode == START and form.is_valid() else None
    body: dict[str, Any] = {"ignore_warnings": True}
    if chosen is not None:
        body[CAPABILITIES] = chosen
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        answer = client.start_backfill(
            story_id, capabilities=chosen, ignore_warnings=False, idempotency_key=key
        )
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        log_engine_error(request, exc)
        return _start_refused(request, story_id, exc, mode=mode, key=key)
    return _answered(request, story_id, answer, key, body)

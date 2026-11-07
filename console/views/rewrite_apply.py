"""Applying or cancelling a rewrite session (#299; FR-REWRITE-6, FR-CONC-7,13; E3, E5, E19, E21).

**Apply** (``stories:rewrite-apply``, ``…/rewrites/<ulid:session_id>/apply/``, POST only) is the
action of the Apply form in E3's footer, which the session page renders while the session is
``awaiting_approval`` with a preview: a hidden ``base_revision`` (the *preview's*), the page's
key, :data:`APPLY_LINE` and "Apply this plan" beside the plan's short hash. The preview *is* the
approval step: there is no second confirm page. It renders through #297's
:func:`console.views.rewrites.render_session_page`, so every re-render is the session page.

1. A ``base_revision`` that isn't 1 to 18 digits sends nothing: one ``get_rewrite`` re-read and
   E5's ``base_revision_required`` sentence above the partial (a fresh key, E21).
2. Otherwise one ``apply_rewrite(story_id, session_id, base_revision=<int>,
   idempotency_key=<posted key>)``, waiting ``turn_timeout(None)``, with no expected revision
   (E19): the Engine guards with the base, and a plan outside the blast radius applies even when
   the story moved.
3. **Applied** → 303 to the session page with :data:`APPLIED_TEXT` (the amendment in
   ``<code>``, linked through E6 once ``stories:amendment`` resolves, the new revision and #43's
   key tail). The redirect's GET reads the story header again through #296's frame.

**A stale plan is shown, never applied.** 409 ``rewrite_stale`` makes one ``get_rewrite``
re-read and answers 200 with the session page: E5's sentence (``STALE_TEXT`` with the reasons,
``BASE_MISMATCH_TEXT`` or ``PIPELINE_CHANGED_TEXT``) in a :class:`console.rewrites.StaleNotice`
above the partial, the carried-answers count and the reasons' refs under More. The re-read's
preview, with its new ``base_revision`` in a fresh Apply form (and a fresh key), is what the
author approves next. When the re-read fails, the refusal's own ``preview`` renders over a
synthetic display-only session (:func:`refusal_session`) with "(read from the refusal)" and no
Apply form; with no usable preview there (``pipeline_changed``, or one that fails #292's model,
logged as a contract bug) the sentence shows above #43's panel.

The view never re-sends an apply, on ``rewrite_stale`` or anything else: exactly one apply POST
per click. A browser reload of a stale render re-POSTs the old ``base_revision`` and key; that
is accepted, because the old base can only yield ``base_mismatch`` or a replay at the Engine,
never an apply. A session reaching this page previews first (or already awaits approval), so
there is no wording for the Engine's own ``rewrite_stale_retries`` re-apply of an
``apply_directly`` session.

**Other refusals** re-render from one fresh read with their sentence above the partial: E5's
(``rewrite_session_closed``, ``rewrite_apply_in_progress``, ``rewrite_questions_pending``,
``record_in_flight``, 503 ``rewrite_apply_failed``, whose re-read shows ``last_error``) and C5's
(``erased_subject_referenced``, ``story_paused``, ``story_importing``), with a fresh key. A code
nothing words is #43's panel. 404 ``rewrite_session_not_found`` makes no re-read (the sentence
and the panel). An unknown outcome (transport, timeout, ``bad_response``, an unworded 5xx)
re-renders from one read with #43's panel and the posted key, so a re-click replays (D8). A
held story (503 ``erasure_pending``) goes to #87's middleware.

**Cancel** (``stories:rewrite-cancel``, ``…/rewrites/<ulid:session_id>/cancel/``, GET/HEAD/POST)
is a confirm page (``console/confirm.py``, ``require_confirmed=True``), not ``danger``: nothing
in the story is lost. E3's Cancel link carries the display facts (``kind``, ``state``,
``instruction``; :func:`console.rewrites.rewrite_cancel_url`); the GET reads them from the
query and makes no Engine call. The confirmed POST calls ``cancel_rewrite`` once (no expected
revision, E19) → 303 to the session page with :data:`CANCELLED_TEXT`. A refusal re-renders the
confirm page with ``error=`` (#134's pattern, never a redirect): ``rewrite_refusal``'s text, else
#43's headline; the posted key is kept on an unknown outcome, fresh otherwise.
"""

import dataclasses
import logging
import re
from collections.abc import Mapping
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_POST

from console.confirm import (
    CONFIRMED_FIELD,
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
)
from console.engine.client import EngineClientError, EngineError
from console.engine.models import RewriteApplied, RewriteSession, RewriteStaleDetails
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.held import is_held
from console.ids import new_ulid
from console.raw import EMPTY, number_text
from console.rewrites import (
    AWAITING_ANSWERS,
    AWAITING_APPROVAL,
    BASE_REVISION_REQUIRED_TEXT,
    KEY_TAIL,
    NOT_FOUND_TEXT,
    amendment_url,
    kind_text,
    rewrite_refusal,
    rewrite_session_url,
    stale_notice,
    state_text,
)
from console.views.rewrites import read_session, render_session_page, session_context
from console.views.story_pages import StoryPageContext

log = logging.getLogger(__name__)

APPLY_LABEL = "Apply this plan"
APPLY_LINE = (
    "Applying writes every change above as one amendment; the story's revision moves by one"
)
APPLIED_TEXT = "Applied: amendment {amendment} · story revision now {revision}"
CANCELLED_TEXT = "Cancelled — nothing in the story changed"
FROM_REFUSAL_NOTE = "(read from the refusal)"
CANCEL_TITLE = "Cancel rewrite"
CANCEL_QUESTION = "Cancel this rewrite session?"
CANCEL_LINE = (
    "Nothing in the story changes. The session's plan and answers are kept for the record, but "
    "it can't be applied any more."
)
CANCEL_LABEL = "Cancel session"
APPLY_VERB = "apply the rewrite"
CANCEL_VERB = "cancel the rewrite"
BASE_FIELD = "base_revision"
BASE_REVISION = re.compile(r"[0-9]{1,18}")
STALE_CODE = "rewrite_stale"
NOT_FOUND_CODE = "rewrite_session_not_found"
BAD_RESPONSE = "bad_response"


def _effective(ctx: StoryPageContext) -> Mapping[str, Any] | None:
    return ctx.config.effective if ctx.config is not None else None


def _unknown(exc: EngineClientError, worded: bool) -> bool:
    """D8's unknown outcome: no answer, an unreadable one, or a 5xx nothing words."""
    if not isinstance(exc, EngineError) or exc.code == BAD_RESPONSE:
        return True
    return exc.http_status >= 500 and not worded


def _see_other(url: str) -> HttpResponse:
    response = HttpResponseRedirect(url)
    response.status_code = 303
    return response


def applied_flash(applied: RewriteApplied, story_id: str, key: str) -> SafeString:
    """:data:`APPLIED_TEXT`: the amendment's full id in ``<code>`` (linked once
    ``stories:amendment`` resolves), the story's new revision and #43's key tail."""
    amendment = applied.amendment_id
    url = amendment_url(story_id, amendment)
    shown = (
        format_html("<code>{}</code>", amendment)
        if url is None
        else format_html('<a href="{}"><code>{}</code></a>', url, amendment)
    )
    before, after = APPLIED_TEXT.split("{amendment}", 1)
    return format_html(
        "{}{}{}{}",
        before,
        shown,
        after.replace("{revision}", number_text(applied.timeline_revision)),
        format_html(KEY_TAIL, applied.idempotency_key or key),
    )


def refusal_session(
    details: RewriteStaleDetails, story_id: str, session_id: str
) -> RewriteSession | None:
    """A display-only session built from a ``rewrite_stale`` refusal (the re-read failed): the
    URL's ids, the refusal's ``base_revision`` and ``preview``, its ``new_questions`` (read-only),
    and ``awaiting_answers`` when one of them is required, else ``awaiting_approval``. The
    members the refusal lacks are placeholders ("" / ``None``, shown as "—"). The stale count
    is left at 0: the notice shows it. ``None`` without a parsed preview."""
    if details.preview is None:
        return None
    required = any(q.required for q in details.new_questions)
    return RewriteSession(
        id=session_id,
        story_id=story_id,
        kind="",
        state=AWAITING_ANSWERS if required else AWAITING_APPROVAL,
        instruction=None,
        resolution_mode=None,
        preview_mode="",
        pipeline="",
        principal="",
        created_at="",
        updated_at="",
        expires_at="",
        base_revision=details.base_revision,
        preview=details.preview,
        questions=list(details.new_questions),
    )


def _stale(
    ctx: StoryPageContext, session_id: str, exc: EngineError, text: str
) -> Mapping[str, Any]:
    """The stale plan's re-render: the re-read's session under the notice, else the refusal's
    own preview, else the notice above #43's panel. Never a second apply."""
    read = read_session(ctx, session_id)
    if not isinstance(read, tuple):
        notice = stale_notice(text, exc.details)
        return session_context(ctx, read, key=new_ulid(), stale=notice)
    failed = read[1]
    details = RewriteStaleDetails.from_details(exc.details)
    raw = exc.details.get("preview") if isinstance(exc.details, Mapping) else None
    synthetic = refusal_session(details, ctx.story_id, session_id) if details is not None else None
    if synthetic is None:
        if raw is not None:
            log.warning(
                "contract: rewrite_stale preview failed to parse (story %s, session %s)",
                ctx.story_id,
                session_id,
            )
        notice = stale_notice(text, exc.details)
        return {**failed, "stale_notice": notice, "idempotency_key": new_ulid()}
    notice = stale_notice(text, exc.details, from_refusal=True)
    return session_context(
        ctx,
        synthetic,
        key=new_ulid(),
        stale=notice,
        display_only=True,
        preview_source_note=FROM_REFUSAL_NOTE,
    )


def _refused(
    ctx: StoryPageContext, session_id: str, exc: EngineClientError, key: str
) -> Mapping[str, Any]:
    """A refused (or unanswered) apply's re-render, from one fresh read."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    refusal = rewrite_refusal(exc, ctx.story_id, APPLY_VERB, _effective(ctx))
    if isinstance(exc, EngineError) and exc.code == NOT_FOUND_CODE:
        return {
            "session_notices": (NOT_FOUND_TEXT,),
            "write_error": panel_for(exc),
            "idempotency_key": new_ulid(),
        }
    if isinstance(exc, EngineError) and exc.code == STALE_CODE and refusal is not None:
        return _stale(ctx, session_id, exc, refusal.text)
    unknown = _unknown(exc, refusal is not None)
    shown_key = key if unknown else new_ulid()
    notices = (refusal.text,) if refusal is not None and not unknown else ()
    write_error = panel_for(exc) if unknown or refusal is None else None
    read = read_session(ctx, session_id)
    if isinstance(read, tuple):
        failed = read[1]
        return {
            **failed,
            "session_notices": notices if not failed.get("session_not_found") else (),
            "write_error": write_error,
            "idempotency_key": shown_key,
        }
    context = session_context(ctx, read, key=shown_key, notices=notices)
    return {**context, "write_error": write_error}


@require_POST
def apply(request: HttpRequest, story_id: str, session_id: str) -> HttpResponse:
    """``stories:rewrite-apply``: apply the session's previewed plan, once (#299)."""

    def build(ctx: StoryPageContext) -> Mapping[str, Any] | HttpResponse:
        key = posted_idempotency_key(request)
        posted = request.POST.get(BASE_FIELD, "")
        if not BASE_REVISION.fullmatch(posted):
            read = read_session(ctx, session_id)
            if isinstance(read, tuple):
                failed = read[1]
                if failed.get("session_not_found"):
                    return failed
                return {**failed, "session_notices": (BASE_REVISION_REQUIRED_TEXT,)}
            return session_context(ctx, read, notices=(BASE_REVISION_REQUIRED_TEXT,))
        try:
            applied = ctx.client.apply_rewrite(
                story_id, session_id, base_revision=int(posted), idempotency_key=key
            )
        except EngineClientError as exc:
            return _refused(ctx, session_id, exc, key)
        messages.success(request, applied_flash(applied, story_id, key), extra_tags="safe")
        return _see_other(rewrite_session_url(story_id, session_id) or request.path)

    return render_session_page(request, story_id, session_id, build)


def cancel_page(request: HttpRequest, story_id: str, session_id: str) -> ConfirmPage:
    """The Cancel confirm page; its facts come from the link's display-only query."""
    query = request.GET
    instruction = query.get("instruction", "")
    return ConfirmPage(
        title=CANCEL_TITLE,
        question=CANCEL_QUESTION,
        confirm_label=CANCEL_LABEL,
        cancel_url=rewrite_session_url(story_id, session_id) or request.path,
        lines=(CANCEL_LINE,),
        facts=(
            ("Session", session_id),
            ("Kind", kind_text(query.get("kind"))),
            ("Instruction", instruction or EMPTY),
            ("State", state_text(query.get("state"))),
        ),
    )


def _cancel_refused(
    request: HttpRequest, page: ConfirmPage, story_id: str, exc: EngineClientError, key: str
) -> HttpResponse:
    """The confirm page again with ``error=`` (never a redirect, never the full-page panel)."""
    if is_held(exc):
        raise exc
    log_engine_error(request, exc)
    refusal = rewrite_refusal(exc, story_id, CANCEL_VERB)
    unknown = _unknown(exc, refusal is not None)
    text = refusal.text if refusal is not None and not unknown else panel_for(exc).headline
    failed = dataclasses.replace(page, error=text, hidden={**page.hidden, CONFIRMED_FIELD: "1"})
    return render_confirm(request, failed, idempotency_key=key if unknown else None)


@require_http_methods(["GET", "HEAD", "POST"])
def cancel(request: HttpRequest, story_id: str, session_id: str) -> HttpResponse:
    """``stories:rewrite-cancel``: confirm, then cancel the session once (#299)."""
    page = cancel_page(request, story_id, session_id)

    def perform(key: str) -> HttpResponse:
        client = engine_client_for(request)
        try:
            written = client.cancel_rewrite(story_id, session_id, idempotency_key=key)
        except EngineClientError as exc:
            return _cancel_refused(request, page, story_id, exc, key)
        flash = format_html(
            "{}{}", CANCELLED_TEXT, format_html(KEY_TAIL, written.idempotency_key or key)
        )
        messages.success(request, flash, extra_tags="safe")
        return _see_other(page.cancel_url)

    return confirm_action(request, page, perform, require_confirmed=True)

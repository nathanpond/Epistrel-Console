"""Prune a story after its dry-run impact, and see past prunings (#317, FR-STORE-19,
FR-STORE-32, ADR-175; E8, E9, E11).

Two URLs (E11), each framed by #296's :func:`~console.views.story_pages.story_page` with the label
:data:`~console.pruning.LABEL` (an unlinked page: no nav row is current):

- ``stories:prune`` (``/stories/<id>/prune/``, :func:`prune`):

  - **GET** makes the frame's two reads, then one ``list_prune_runs``, and never ``prune_story``.
    It shows the policy line (the frame's ``Story.pruning``), the "Run a dry run" form (its only
    input the per-render idempotency key, E21) and Past prunings. A failed ``list_prune_runs``
    puts #43's panel in that section only.
  - **POST** (no confirm token: this URL never applies) is the dry run, E8's first stage: one
    ``prune_story(..., ignore_warnings=False, expected_revision=None)`` under the posted key,
    with ``targets`` the ticked boxes when ``narrowed=1`` (stripped, blanks dropped,
    de-duplicated in form order), else ``None``. It runs before the frame (so the frame's story
    read is after it), then the page renders with the report (``_prune_impact.html``), a "Run a
    full dry run again" form and Past prunings. A narrowed POST with no box ticked (or more
    than 200) re-renders the signed last report (``report_token``) with the error and makes no
    Engine write; a report token past its age shows the error on the bare page, and a tampered
    or other-story one is ``EXPIRED_TEXT`` and a redirect.

- ``stories:prune-confirm`` (``/stories/<id>/prune/confirm/``, :func:`prune_confirm`, POST; a GET
  redirects to ``stories:prune``): E9's typed-name ``ConfirmPage``
  (:func:`console.pruning.confirm_page`) through ``confirm_action(..., require_confirmed=True)``.
  Opening it calls nothing; a token that fails ``twostage.unsign`` (or names another story) is
  ``EXPIRED_TEXT``, a redirect and zero calls. The confirmed POST with the name typed exactly
  sends one ``prune_story(targets=body["targets"], ignore_warnings=True,
  expected_revision=base["timeline_revision"])`` (E8, E19). ``applied: true`` renders the result
  block in the frame (200, no flash; it ends with the muted key tail, E21); ``applied: false``
  re-renders the new report with :data:`~console.pruning.REPLANNED_TEXT`. An unknown outcome
  re-renders the same confirm under #43's panel with the same token and key (E21).

**Refusals** (either stage, 200, never a redirect, never retried): ``revision_conflict`` is C5's
text plus :data:`~console.pruning.RERUN_TEXT` with no report; ``prune_target_unknown`` /
``prune_target_ineligible`` is :data:`~console.pruning.TARGET_GONE_TEXT`; ``story_archived``,
``story_paused`` (E10's backfill causes included) and ``story_busy`` (" · retry after <n> s") are
C5's shared texts through ``write_refusal``; each re-renders the bare page (frame reads plus one
``list_prune_runs``). A held (#87) or importing (E10) story is re-raised to its middleware.
Anything else is #43's panel inside the frame.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from console import pruning, twostage
from console.confirm import (
    CONFIRMED_FIELD,
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
)
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import Pruned
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.held import is_held
from console.ids import new_ulid
from console.portability import is_importing
from console.views.certify import busy_text
from console.views.story_pages import StoryPageContext, story_page
from console.wording import humanised, write_refusal

TEMPLATE = "console/stories/prune.html"
NARROWED_FIELD = "narrowed"
TARGET_FIELD = "target"
REPORT_FIELD = "report_token"
BAD_RESPONSE = "bad_response"


@dataclass(frozen=True)
class Refusal:
    """A refusal's line above the page: its text, the text's link, and a second sentence."""

    text: str
    url: str | None = None
    then: str = ""


def prune_page_url(story_id: str) -> str:
    return reverse("stories:prune", args=[story_id])


def _unknown(exc: EngineClientError) -> bool:
    """D8's unknown outcome: no answer, or one that didn't parse (the write may have run)."""
    return isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )


def _passed_on(exc: EngineClientError) -> bool:
    return is_held(exc) or is_importing(exc)


def refusal_for(exc: EngineClientError, story_id: str) -> Refusal | None:
    """The page's line for a refused prune, or ``None`` (#43's panel)."""
    if not isinstance(exc, EngineError):
        return None
    if exc.code in pruning.TARGET_CODES:
        reason = exc.details.get("reason")
        shown = humanised(reason) if isinstance(reason, str) and reason else None
        return Refusal(pruning.TARGET_GONE_TEXT.format(reason=shown or pruning.TARGET_GONE_REASON))
    if exc.code == "story_busy":
        return Refusal(busy_text(exc))
    found = write_refusal(exc, story_id, pruning.VERB_PHRASE)
    if found is None:
        return None
    then = pruning.RERUN_TEXT if exc.code == "revision_conflict" else ""
    return Refusal(found.text, found.url or None, then)


def posted_targets(request: HttpRequest) -> list[str]:
    """The ticked ``target`` values: stripped, blanks dropped, de-duplicated in form order."""
    values = (value.strip() for value in request.POST.getlist(TARGET_FIELD))
    return list(dict.fromkeys(value for value in values if value))


# --- the page -------------------------------------------------------------------------------------


def _runs(ctx: StoryPageContext) -> dict[str, Any]:
    try:
        runs = ctx.client.list_prune_runs(ctx.story_id)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(ctx.request, exc)
        return {"runs": None, "runs_error": panel_for(exc)}
    return {"runs": pruning.run_rows(runs), "runs_error": None}


def _names(ctx: StoryPageContext) -> dict[str, Any]:
    return {"story_name": ctx.story.name if ctx.story is not None else None}


@dataclass(frozen=True)
class Shown:
    """What one render of the Prune page shows beyond the policy line and Past prunings."""

    report: Pruned | None = None
    refusal: Refusal | None = None
    error: str = ""  # "Tick at least one target" / "Tick at most 200 targets"
    panel: ErrorPanel | None = None  # an unknown dry-run outcome (the key kept)
    key: str | None = None
    replanned: bool = False
    result: pruning.ResultView | None = None


def _context(ctx: StoryPageContext, shown: Shown) -> dict[str, Any]:
    story = ctx.story
    context: dict[str, Any] = {
        "policy": pruning.policy_line(story.pruning if story is not None else None),
        "settings_url": reverse("stories:settings", args=[ctx.story_id]),
        "prune_url": prune_page_url(ctx.story_id),
        "confirm_url": reverse("stories:prune-confirm", args=[ctx.story_id]),
        "dry_run_line": pruning.DRY_RUN_LINE,
        "dry_run_label": pruning.DRY_RUN_LABEL,
        "full_dry_run_label": pruning.FULL_DRY_RUN_LABEL,
        "narrow_label": pruning.NARROW_LABEL,
        "prune_label": pruning.PRUNE_LABEL,
        "prune_all_note": pruning.PRUNE_ALL_NOTE,
        "nothing_to_prune": pruning.NOTHING_TO_PRUNE,
        "never_pruned": pruning.NEVER_PRUNED,
        "dry_run_key": shown.key or new_ulid(),
        "narrow_key": new_ulid(),
        "confirm_key": new_ulid(),
        "refusal": shown.refusal,
        "error": shown.error,
        "engine_error": shown.panel,
        "replanned_text": pruning.REPLANNED_TEXT if shown.replanned else "",
        "report": None,
        "result": shown.result,
    }
    if shown.result is not None:
        return context
    if shown.report is not None:
        context["report"] = pruning.impact_view(shown.report, ctx.story_id, _names(ctx))
    context.update(_runs(ctx))
    return context


def _render(request: HttpRequest, story_id: str, shown: Shown) -> HttpResponse:
    return story_page(
        request, story_id, TEMPLATE, lambda ctx: _context(ctx, shown), label=pruning.LABEL
    )


def _refused(request: HttpRequest, story_id: str, exc: EngineClientError) -> HttpResponse:
    """A refusal re-renders the bare page with its line; an unmapped error is #43's panel inside
    the frame (``build`` raises it, so ``story_page`` logs and renders it)."""
    log_engine_error(request, exc)
    found = refusal_for(exc, story_id)
    if found is not None:
        return _render(request, story_id, Shown(refusal=found))

    def build(ctx: StoryPageContext) -> Mapping[str, Any]:
        raise exc

    return story_page(request, story_id, TEMPLATE, build, label=pruning.LABEL)


def _expired(request: HttpRequest, story_id: str) -> HttpResponse:
    messages.error(request, twostage.EXPIRED_TEXT)
    return redirect("stories:prune", story_id=story_id)


def _narrowed_to_nothing(request: HttpRequest, story_id: str, error: str) -> HttpResponse:
    """E8: the last report (signed into ``report_token``) again with ``error``; no Engine write."""
    try:
        report = pruning.unsign_report(request.POST.get(REPORT_FIELD, ""), story_id)
    except pruning.ReportExpired:
        return _render(request, story_id, Shown(error=error))
    except pruning.ReportInvalid:
        return _expired(request, story_id)
    return _render(request, story_id, Shown(report=report, error=error))


def _dry_run(request: HttpRequest, story_id: str) -> HttpResponse:
    targets: list[str] | None = None
    if request.POST.get(NARROWED_FIELD) == "1":
        targets = posted_targets(request)
        if not targets:
            return _narrowed_to_nothing(request, story_id, pruning.TICK_AT_LEAST_ONE)
        if len(targets) > pruning.MAX_TARGETS:
            return _narrowed_to_nothing(request, story_id, pruning.TICK_AT_MOST)
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        answer = client.prune_story(
            story_id,
            targets=targets,
            ignore_warnings=False,
            expected_revision=None,
            idempotency_key=key,
        )
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        if _unknown(exc):
            log_engine_error(request, exc)
            return _render(request, story_id, Shown(panel=panel_for(exc), key=key))
        return _refused(request, story_id, exc)
    if answer.applied and answer.result is not None:  # E8: applied at once goes to the result
        base = {"counts": pruning.counts(answer.impact.to_delete)}
        return _render(request, story_id, Shown(result=pruning.result_view(answer, base, key)))
    return _render(request, story_id, Shown(report=answer))


@require_http_methods(["GET", "HEAD", "POST"])
def prune(request: HttpRequest, story_id: str) -> HttpResponse:
    if request.method == "POST":
        return _dry_run(request, story_id)
    return _render(request, story_id, Shown())


# --- the confirm and the apply --------------------------------------------------------------------


@dataclass(frozen=True)
class Signed:
    token: str
    body: dict[str, Any]
    base: dict[str, Any]


def unsigned(request: HttpRequest, story_id: str) -> Signed | None:
    """The confirm token's body and base; ``None`` when it is expired, tampered, for another
    story or not a body :func:`console.pruning.impact_view` signed."""
    token = request.POST.get(twostage.CONFIRM_FIELD, "")
    try:
        payload = twostage.unsign(pruning.KIND, token)
    except twostage.ConfirmationExpired:
        return None
    body, base = payload.body, payload.base
    if (
        payload.target_id != story_id
        or not isinstance(body, dict)
        or body.get("ignore_warnings") is not True
        or not (body.get("targets") is None or isinstance(body.get("targets"), list))
        or not isinstance(base, dict)
        or not isinstance(base.get("timeline_revision"), int)
    ):
        return None
    return Signed(token, body, base)


def _apply(
    request: HttpRequest, story_id: str, signed: Signed, page: ConfirmPage, key: str
) -> HttpResponse:
    client = engine_client_for(request)
    try:
        answer = client.prune_story(
            story_id,
            targets=signed.body["targets"],
            ignore_warnings=True,
            expected_revision=signed.base["timeline_revision"],
            idempotency_key=key,
        )
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        if _unknown(exc):  # E21: the same confirm, same token and key, under #43's panel
            log_engine_error(request, exc)
            confirmed = dataclasses.replace(page, hidden={**page.hidden, CONFIRMED_FIELD: "1"})
            return render_confirm(
                request, confirmed, extra={"engine_error": panel_for(exc)}, idempotency_key=key
            )
        return _refused(request, story_id, exc)
    if not answer.applied or answer.result is None:  # the Engine re-planned: never a success
        return _render(request, story_id, Shown(report=answer, replanned=True))
    result = pruning.result_view(answer, signed.base, key)
    return _render(request, story_id, Shown(result=result))


@require_http_methods(["GET", "HEAD", "POST"])
def prune_confirm(request: HttpRequest, story_id: str) -> HttpResponse:
    if request.method != "POST":
        return redirect("stories:prune", story_id=story_id)
    signed = unsigned(request, story_id)
    if signed is None:
        return _expired(request, story_id)
    page = pruning.confirm_page(story_id, signed.base, signed.token, prune_page_url(story_id))
    return confirm_action(
        request,
        page,
        lambda key: _apply(request, story_id, signed, page, key),
        require_confirmed=True,
    )

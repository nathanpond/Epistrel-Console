"""Rate, re-rate and retract feedback on a rendered message (#83).

Every generated message in #48's play view and #52's history carries a collapsed widget, a bare
"Rate" link (no read until it is opened). The routes, under ``/stories/<id>/messages/<mid>/``:

- ``GET feedback/``: the open form. It reads the acting principal's rating
  (:meth:`~console.engine.client.EngineClient.get_message_feedback`) and the deployment's tags
  and text cap (``GET /admin/settings``) at once on #41's pool. A failed settings read leaves no
  tags and the Engine's default 2000 cap, with a note; a 404 on the rating read makes the widget
  "Can't be rated …". ``?close=1`` re-collapses it with zero Engine calls.
- ``POST feedback/``: save. The form is checked against the signed pre-fill (expired, tampered
  or bound to another story, message or principal: "This form expired — open it again", zero
  calls); an unchanged submission makes no PUT ("No change"). 201 "Rated N", 200 "Updated to
  N", or "No change (already rated N)" when ``updated_at`` did not move.
- ``POST feedback/retract/``: ``DELETE`` the rating (204 even when there was none), then the
  open form re-renders from a fresh read with "Rating retracted".

Under HTMX each answer is the widget, swapped ``outerHTML`` into ``#feedback-<view>-<mid>``.
Without JS the GET is a shell page with a back link, and POST results come back as flash
messages after a redirect to the validated ``next`` (the story's play view otherwise).
Feedback never changes the story, so nothing here is disabled on an archived story or while a
turn runs.
"""

from collections.abc import Callable
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from console.acting import acting_principal
from console.confirm import safe_next
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import DeploymentSettings, MessageFeedback
from console.engine.pool import fan_out
from console.engine_access import engine_client_for
from console.engine_errors import is_htmx, log_engine_error, panel_for
from console.engine_form_errors import engine_field_errors
from console.feedback import (
    DEFAULT_TEXT_MAX,
    EXPIRED_TEXT,
    NO_CHANGE_TEXT,
    PREFILL_FIELD,
    RETRACTED_TEXT,
    TAGS_NOTE,
    VIEWS,
    ContextLine,
    FeedbackWidget,
    Prefill,
    View,
    same_value,
    sign_prefill,
    unsign_prefill,
)
from console.forms.feedback import FeedbackForm
from console.models import EngineOperation
from console.views.profiles import detail_url

PARTIAL = "console/stories/_feedback.html"
PAGE = "console/stories/feedback.html"
FEEDBACK = "feedback"
SETTINGS = "settings"
EMPTY = "—"


def _view(request: HttpRequest) -> View:
    value = request.GET.get("view") or request.POST.get("view") or ""
    for view in VIEWS:
        if value == view:
            return view
    return "play"


def _base(request: HttpRequest, story_id: str, message_id: str) -> FeedbackWidget:
    fallback = reverse("stories:detail", args=[story_id])
    return FeedbackWidget(story_id, message_id, _view(request), safe_next(request, fallback))


def _answer(request: HttpRequest, widget: FeedbackWidget) -> HttpResponse:
    if is_htmx(request):
        return render(request, PARTIAL, {"fb": widget})
    return render(request, PAGE, {"fb": widget})


# --- the context block --------------------------------------------------------------------------


def _operation_url(operation_id: str) -> str:
    """The newest #44 row recording the Engine operation, linked; "" when none does."""
    row = (
        EngineOperation.objects.filter(engine_operation_id=operation_id)
        .order_by("-pk")
        .values_list("pk", flat=True)
        .first()
    )
    return reverse("operations:detail", args=[row]) if row is not None else ""


def _profile_url(profile_version: str) -> str:
    """``<profile id>@<version>`` → ``/profiles/<id>/?version=N`` ("" when it is not that)."""
    profile_id, _, version = profile_version.rpartition("@")
    if not profile_id or not version.isdigit():
        return ""
    return detail_url(profile_id, int(version))


def model_line(purpose: str, model_id: str | None, reported_version: str | None) -> str:
    """``purpose: model (version)``; the parenthesis dropped when no version is reported."""
    line = f"{purpose}: {model_id or EMPTY}"
    return f"{line} ({reported_version})" if reported_version is not None else line


def _context(feedback: MessageFeedback) -> tuple[tuple[ContextLine, ...], tuple[str, ...]]:
    context = feedback.context
    if context is None or feedback.rating is None:
        return (), ()
    profile = context.profile_version
    lines = (
        ContextLine(
            "Operation", context.operation_id, _operation_url(context.operation_id), code=True
        ),
        ContextLine("Config fingerprint", context.config_fingerprint or EMPTY, code=True),
        ContextLine("Profile version", profile or EMPTY, _profile_url(profile) if profile else ""),
        ContextLine("Rating version", context.rating_version_id or EMPTY, code=True),
        ContextLine("Quality status", context.quality_status or EMPTY),
    )
    models = tuple(
        model_line(purpose, identity.id, identity.reported_version)
        for purpose, identity in (context.models or {}).items()
    )
    return lines, models


# --- rendering the open form --------------------------------------------------------------------


def _opened(
    base: FeedbackWidget,
    prefill: Prefill,
    *,
    form: FeedbackForm | None = None,
    notice: str = "",
    error: EngineClientError | None = None,
) -> FeedbackWidget:
    feedback = prefill.feedback
    if form is None:
        form = FeedbackForm(
            configured=prefill.configured,
            current_tags=feedback.tags,
            cap=prefill.cap,
            initial={"rating": feedback.rating, "text": feedback.text or ""},
        )
    context, models = _context(feedback)
    return FeedbackWidget(
        base.story_id,
        base.message_id,
        base.view,
        base.next_url,
        state="open",
        notice=notice,
        feedback=feedback,
        form=form,
        token=sign_prefill(prefill),
        cap=prefill.cap,
        tags_note="" if prefill.tags_read else TAGS_NOTE,
        error=panel_for(error) if error is not None else None,
        context=context,
        models=models,
    )


def _unavailable(base: FeedbackWidget) -> FeedbackWidget:
    return FeedbackWidget(
        base.story_id, base.message_id, base.view, base.next_url, state="unavailable"
    )


def _read(
    request: HttpRequest, client: EngineClient, base: FeedbackWidget, *, notice: str = ""
) -> FeedbackWidget:
    """The open widget from a fresh read of the rating and the settings (at once, on #41's
    pool); a 404 makes it unavailable, another failure shows the panel without a form."""
    reads: dict[str, Callable[[], Any]] = {
        FEEDBACK: lambda: client.get_message_feedback(base.story_id, base.message_id),
        SETTINGS: client.get_deployment_settings,
    }
    found = fan_out(lambda key: reads[key](), list(reads))
    feedback = found[FEEDBACK]
    if not isinstance(feedback, MessageFeedback):
        log_engine_error(request, feedback)
        if isinstance(feedback, EngineError) and feedback.http_status == 404:
            return _unavailable(base)
        return FeedbackWidget(
            base.story_id,
            base.message_id,
            base.view,
            base.next_url,
            state="open",
            notice=notice,
            error=panel_for(feedback),
        )
    settings = found[SETTINGS]
    if isinstance(settings, DeploymentSettings):
        configured, cap, tags_read = (
            tuple(settings.feedback_tags),
            settings.feedback_text_max_chars,
            True,
        )
    else:
        if isinstance(settings, EngineClientError):
            log_engine_error(request, settings)
        configured, cap, tags_read = (), DEFAULT_TEXT_MAX, False
    prefill = Prefill(
        base.story_id,
        base.message_id,
        acting_principal(request),
        feedback,
        configured,
        cap,
        tags_read,
    )
    return _opened(base, prefill, notice=notice)


def _collapsed(base: FeedbackWidget, result: str, badge: int | None) -> FeedbackWidget:
    return FeedbackWidget(
        base.story_id, base.message_id, base.view, base.next_url, result=result, badge=badge
    )


# --- the routes ---------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def feedback(request: HttpRequest, story_id: str, message_id: str) -> HttpResponse:
    base = _base(request, story_id, message_id)
    if request.method == "POST":
        return _save(request, base)
    if request.GET.get("close"):
        if not is_htmx(request):
            return redirect(base.next_url)
        return _answer(request, base)
    return _answer(request, _read(request, engine_client_for(request), base))


def _prefill(request: HttpRequest, base: FeedbackWidget) -> Prefill | None:
    return unsign_prefill(
        request.POST.get(PREFILL_FIELD, ""),
        story_id=base.story_id,
        message_id=base.message_id,
        principal=acting_principal(request),
    )


def _expired(request: HttpRequest, base: FeedbackWidget) -> HttpResponse:
    if not is_htmx(request):
        messages.error(request, EXPIRED_TEXT)
        return redirect(base.next_url)
    return _answer(request, _collapsed(base, EXPIRED_TEXT, None))


def _done(
    request: HttpRequest, base: FeedbackWidget, result: str, badge: int | None
) -> HttpResponse:
    if not is_htmx(request):
        messages.success(request, result)
        return redirect(base.next_url)
    return _answer(request, _collapsed(base, result, badge))


def _save(request: HttpRequest, base: FeedbackWidget) -> HttpResponse:
    prefill = _prefill(request, base)
    if prefill is None:
        return _expired(request, base)
    current = prefill.feedback
    form = FeedbackForm(
        request.POST, configured=prefill.configured, current_tags=current.tags, cap=prefill.cap
    )
    if not form.is_valid():
        return _answer(request, _opened(base, prefill, form=form))
    rating: int = form.cleaned_data["rating"]
    tags: list[str] = form.cleaned_data["tags"]
    text: str | None = form.cleaned_data["text"]
    unchanged = same_value(
        rating,
        tags,
        text,
        current_rating=current.rating,
        current_tags=current.tags,
        current_text=current.text,
    )
    if unchanged:
        return _done(request, base, NO_CHANGE_TEXT, current.rating)
    client = engine_client_for(request)
    try:
        saved, created = client.put_message_feedback(
            base.story_id, base.message_id, rating=rating, tags=tags, text=text
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _refused(request, base, prefill, form, exc)
    if created:
        result = f"Rated {saved.rating}"
    elif current.updated_at is not None and saved.updated_at == current.updated_at:
        result = f"No change (already rated {saved.rating})"
    else:
        result = f"Updated to {saved.rating}"
    return _done(request, base, result, saved.rating)


def _refused(
    request: HttpRequest,
    base: FeedbackWidget,
    prefill: Prefill,
    form: FeedbackForm,
    exc: EngineClientError,
) -> HttpResponse:
    """A refused save: 404 → unavailable; 422 ``unknown_feedback_tag`` → a form error naming the
    tags, their boxes marked; 422 ``validation_error`` on ``text`` → the text field's error;
    anything else → the #43 panel above the kept form."""
    if isinstance(exc, EngineError):
        if exc.http_status == 404:
            return _answer(request, _unavailable(base))
        if exc.http_status == 422 and exc.code == "unknown_feedback_tag":
            unknown = exc.details.get("unknown_tags")
            tags = [str(t) for t in unknown] if isinstance(unknown, list) else []
            form.mark_unknown(tags)
            form.add_error(None, f"Unknown tags: {', '.join(tags) or EMPTY}")
            return _answer(request, _opened(base, prefill, form=form))
        if exc.http_status == 422 and exc.code == "validation_error":
            failures = [
                f for f in engine_field_errors(exc) if f.path.removeprefix("body.") == "text"
            ]
            if failures:
                form.add_error("text", failures[0].message or exc.message)
                return _answer(request, _opened(base, prefill, form=form))
    return _answer(request, _opened(base, prefill, form=form, error=exc))


@require_POST
def retract(request: HttpRequest, story_id: str, message_id: str) -> HttpResponse:
    base = _base(request, story_id, message_id)
    prefill = _prefill(request, base)
    if prefill is None:
        return _expired(request, base)
    client = engine_client_for(request)
    try:
        client.delete_message_feedback(story_id, message_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 404:
            return _answer(request, _unavailable(base))
        form = FeedbackForm(
            request.POST,
            configured=prefill.configured,
            current_tags=prefill.feedback.tags,
            cap=prefill.cap,
        )
        form.is_valid()  # bound, so the kept input re-renders
        return _answer(request, _opened(base, prefill, form=form, error=exc))
    if not is_htmx(request):
        messages.success(request, RETRACTED_TEXT)
        return redirect(base.open_url)
    return _answer(request, _read(request, client, base, notice=RETRACTED_TEXT))

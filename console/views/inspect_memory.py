"""Pin, unpin, quarantine and release a memory from the retrieval explorer (#143).

The explorer's memory rows (``console/inspect/_memory_actions.html``) carry the controls:

- **Pin** / **Unpin**: POST forms straight to ``inspect:memory-pin`` / ``-unpin``
  (``POST|DELETE /stories/{id}/memories/{mid}/pin``), each with the key minted when the row
  rendered. No confirm: an unpin is reversible, and the overflow episode it may open is the
  Engine's normal budget path.
- **Quarantine**: a link to ``inspect:memory-quarantine``, a bare page with a reason
  (:class:`console.forms.inspect.QuarantineForm`, checked before any call), which POSTs
  ``POST /admin/stories/{id}/memories/{mid}/quarantine {reason}``.
- **Release**: a link to ``inspect:memory-release``, #43's confirm page in #134's confirmed mode
  (``DELETE /admin/stories/{id}/memories/{mid}/quarantine``).
- **Sanitize…** (#144): a link to ``inspect:memory-sanitize``, a bare page with a span and its
  surrogate (:class:`console.forms.inspect.SanitizeForm`, checked before any call), whose valid
  POST renders the confirmed-mode confirm page; its Confirm POSTs
  ``POST /admin/stories/{id}/memories/{mid}/sanitize {span, surrogate}``. No Engine route reads
  one memory and its text must not travel in a URL, so the page's GET re-asks the explorer's
  question (held in ``next``) and shows this memory's text when the answer has it.

Every write POST-redirect-GETs to ``next`` (the explorer's full path through ``safe_next``,
falling back to ``inspect:retrieval``) with a flash naming the memory's state after the call
(#135 convention 8). GETs make no Engine call (but the sanitize page's one retrieve); the memory
id comes from the URL alone (its ``<ulid:…>`` converter 404s anything else), and no memory text
travels in a URL.

Refusals: 422 ``pin_limit_exceeded`` names the ceiling; 422 ``validation_error`` lands on the
quarantine page's field (or above the form), else is an error flash; 409 ``module_off`` (quarantine
and release only) renders #135's greyed panel with status 409; 404 and the shared mutation codes
are convention 10's text, an error flash (row actions) or above the form (the quarantine page);
503 ``erasure_pending`` is #87's held wrapper; anything else (other 503s, an unreachable Engine)
is #43's error panel. A 200 the Console can't read flashes a warning. #44's recorder writes each
call's row (``entity_type="story"``, kind = the client method); a sanitize's row keeps only
lengths (``console.operation_log``), and its view is wrapped in ``sensitive_post_parameters`` so
an error report never shows the span either.

**Consolidate a window** (#145) lives here too: the Memory tab's GET form lands on
``inspect:consolidate`` (no Engine call before the confirm), whose confirmed POST calls
``POST /admin/stories/{id}/consolidate`` for the shared scope and redirects to the Memory tab at
``#consolidate``. The job's #44 row stays ``queued``: the Engine reports no progress for it, so
the Console never says a consolidation finished or wrote anything.
"""

import dataclasses
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from console.confirm import (
    CONFIRMED_FIELD,
    ConfirmPage,
    carried_idempotency_key,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
    safe_next,
)
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import ConsolidationQueued, MemoryView, MemoryWritten
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import (
    SANITIZE_MAX,
    ConsolidateForm,
    QuarantineForm,
    RetrievalForm,
    SanitizeForm,
)
from console.held import is_held, render_held
from console.ids import new_ulid
from console.inspect import (
    CONSOLIDATION,
    Tab,
    is_module_off,
    module_off_unavailable,
    short_id,
    unavailable_for,
)
from console.models import EngineOperation
from console.operation_log import CONSOLIDATE_KIND
from console.story_pause import paused_refusal
from console.turn_reports import ViolationRow, violation_rows

QUARANTINE_TEMPLATE = "console/inspect/quarantine.html"
UNAVAILABLE_TEMPLATE = "console/inspect/unavailable_page.html"
SANITIZE_TEMPLATE = "console/inspect/sanitize.html"
SANITIZE_EDIT_TEMPLATE = "console/inspect/_sanitize_edit.html"
SANITIZE_SENSITIVE = ("span", "surrogate", "shown_text")
SANITIZE_QUESTION = (
    "Every occurrence is replaced. The original is kept only in the Engine's audit record."
)
SURROGATE_KEEPS_SPAN = "The surrogate contains the span, so the span stays in the text."
EMPTY_SURROGATE = "(empty — removes the span)"
GUARDRAIL_VIOLATION = "guardrail_violation"

REASON_SHOWN = 80  # a flash shows the reason's first 80 characters
NOT_FOUND = "Not found (it may have been deleted or truncated)"
UNREADABLE = "the Engine's answer couldn't be read; re-run the question to see its state"
PIN_LIMIT = "Pin limit reached"
BAD_RESPONSE = "bad_response"
PIN_LIMIT_EXCEEDED = "pin_limit_exceeded"
VALIDATION_ERROR = "validation_error"
# Convention 10's text for the shared mutation refusals (story_paused and story_busy are built).
REFUSALS = {
    "not_found": NOT_FOUND,
    "story_archived": "This story is archived — unarchive it to change its memories",
    "revision_conflict": "The story changed meanwhile — re-run the question and try again",
    "idempotency_conflict": "That form was already used for another request — reload and retry",
    "erased_subject_referenced": "This memory refers to an erased subject — nothing was changed",
}
STORY_PAUSED = "story_paused"
STORY_BUSY = "story_busy"
_WHITESPACE = re.compile(r"\s+")

# action → (verb on success, the unchanged answer's word)
WORDS = {
    "pin": ("Pinned", "Already pinned"),
    "unpin": ("Unpinned", "Not pinned"),
    "quarantine": ("Quarantined", "Already quarantined"),
    "release": ("Released", "Not quarantined"),
    "sanitize": ("Sanitized", ""),  # no unchanged answer: a sanitize writes or is refused
}


# --- what a flash says ---------------------------------------------------------------------------


def shown_reason(reason: str) -> str:
    """A quarantine reason for a flash: whitespace runs collapsed to one space, then its first
    80 characters with "…" when longer."""
    collapsed = _WHITESPACE.sub(" ", reason).strip()
    return f"{collapsed[:REASON_SHOWN]}…" if len(collapsed) > REASON_SHOWN else collapsed


def memory_state(memory: MemoryView) -> str:
    """ "pinned · quarantined: <reason> · tier hot": the pin, the quarantine (and its reason) and
    the retrieval tier (with its humanised ``retired_reason`` when it has one; an unknown tier
    shows raw)."""
    pinned = "pinned" if memory.pinned else "not pinned"
    if memory.quarantined:
        reason = shown_reason(memory.quarantine_reason or "")
        quarantined = f"quarantined: {reason}" if reason else "quarantined"
    else:
        quarantined = "not quarantined"
    tier = f"tier {memory.retrieval_tier}"
    if memory.retired_reason:
        tier += f" ({memory.retired_reason.replace('_', ' ')})"
    return " · ".join((pinned, quarantined, tier))


def written_flash(
    request: HttpRequest, action: str, memory_id: str, written: MemoryWritten
) -> None:
    """Success: "Pinned memory …a1b2c3d4 — <state>"; an unchanged answer (``operation_id``
    null) at info level: "Already pinned — memory …a1b2c3d4 — <state>". An action with no
    unchanged word (sanitize) always flashes as a success."""
    verb, unchanged = WORDS[action]
    state = memory_state(written.memory)
    if written.operation_id is None and unchanged:
        messages.info(request, f"{unchanged} — memory {short_id(memory_id)} — {state}")
    else:
        messages.success(request, f"{verb} memory {short_id(memory_id)} — {state}")


def _count(value: object) -> int | None:
    """A non-negative integer (not a boolean), else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def pin_limit_text(exc: EngineError) -> str:
    """422 ``pin_limit_exceeded``: "Pin limit reached: the story allows 5 pins (`cap`); this pin
    would make 6." (a character's ceiling names the character by its last 8). A missing or
    malformed ``limit``/``actual`` or scope: "Pin limit reached." and the Engine's message."""
    details = exc.details
    limit, actual = _count(details.get("limit")), _count(details.get("actual"))
    scope = details.get("scope")
    character = details.get("character_id")
    if scope == "story":
        holder = "the story"
    elif scope == "character" and isinstance(character, str) and character:
        holder = f"character {short_id(character)}"
    else:
        holder = ""
    if limit is None or actual is None or not holder:
        return f"{PIN_LIMIT}. {exc.message}"
    cap = details.get("cap")
    named = f" (`{cap}`)" if isinstance(cap, str) and cap else ""
    return f"{PIN_LIMIT}: {holder} allows {limit} pins{named}; this pin would make {actual}."


def refusal_text(exc: EngineError, story_id: str) -> str | None:
    """Convention 10's text for a 404 or a shared mutation refusal, else ``None``."""
    if exc.code == STORY_PAUSED:
        return paused_refusal(exc, story_id).text
    if exc.code == STORY_BUSY:
        wait = f" in {exc.retry_after} s" if exc.retry_after else ""
        return f"A turn is running — try again{wait}"
    if exc.http_status == 404:
        return NOT_FOUND
    return REFUSALS.get(exc.code)


def unreadable_text(action: str, memory_id: str) -> str:
    return f"{WORDS[action][0]} memory {short_id(memory_id)} — {UNREADABLE}"


# --- shared --------------------------------------------------------------------------------------


def _back(request: HttpRequest, story_id: str) -> str:
    return safe_next(request, reverse(Tab.RETRIEVAL.url_name, args=[story_id]))


def _module_off(
    request: HttpRequest,
    exc: EngineError,
    story_id: str,
    back: str,
    *,
    module: str | None = None,
    back_label: str = "",
) -> HttpResponse:
    """#135's greyed panel for a 409 ``module_off`` (``details.module``, else ``module``, else
    ``episodic``), answered on the POST with status 409. With ``module`` (#145's consolidate) the
    panel also says the configuration changed since the page loaded, with ``back`` to reload."""
    named = exc.details.get("module")
    if module is not None:
        unavailable = unavailable_for(named if isinstance(named, str) and named else module)
    else:
        unavailable = module_off_unavailable(exc, Tab.RETRIEVAL)
    context = {
        "unavailable": unavailable,
        "config_url": reverse("stories:config", args=[story_id]),
        "story_id": story_id,
        "next_url": back,
        "back_label": back_label,
        "config_changed": module is not None,
        "reload_url": back,
    }
    return render(request, UNAVAILABLE_TEMPLATE, context, status=409)


def _row_write(
    request: HttpRequest,
    story_id: str,
    memory_id: str,
    action: str,
    call: Callable[[EngineClient, str], MemoryWritten],
    key: str,
    *,
    module_off: bool,
) -> HttpResponse:
    """A body-less row action (pin, unpin, release): the call, then a redirect to ``next`` with
    the state flash, or the refusal (see the module docstring)."""
    back = _back(request, story_id)
    try:
        written = call(engine_client_for(request), key)
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        if not isinstance(exc, EngineError):
            raise
        log_engine_error(request, exc)
        if module_off and is_module_off(exc):
            return _module_off(request, exc, story_id, back)
        if exc.code == BAD_RESPONSE and exc.http_status == 200:
            messages.warning(request, unreadable_text(action, memory_id))
        elif exc.code == PIN_LIMIT_EXCEEDED:
            messages.error(request, pin_limit_text(exc))
        elif exc.code == VALIDATION_ERROR:
            messages.error(request, exc.message)
        elif (text := refusal_text(exc, story_id)) is not None:
            messages.error(request, text)
        else:
            raise
        return redirect(back)
    written_flash(request, action, memory_id, written)
    return redirect(back)


# --- the views -----------------------------------------------------------------------------------


@require_POST
def pin(request: HttpRequest, story_id: str, memory_id: str) -> HttpResponse:
    """``inspect:memory-pin``: ``POST /stories/{id}/memories/{mid}/pin``."""
    return _row_write(
        request,
        story_id,
        memory_id,
        "pin",
        lambda client, key: client.pin_memory(story_id, memory_id, idempotency_key=key),
        posted_idempotency_key(request),
        module_off=False,
    )


@require_POST
def unpin(request: HttpRequest, story_id: str, memory_id: str) -> HttpResponse:
    """``inspect:memory-unpin``: ``DELETE /stories/{id}/memories/{mid}/pin``."""
    return _row_write(
        request,
        story_id,
        memory_id,
        "unpin",
        lambda client, key: client.unpin_memory(story_id, memory_id, idempotency_key=key),
        posted_idempotency_key(request),
        module_off=False,
    )


@require_http_methods(["GET", "HEAD", "POST"])
def release(request: HttpRequest, story_id: str, memory_id: str) -> HttpResponse:
    """``inspect:memory-release``: #43's confirm page in confirmed mode (#134), then
    ``DELETE /admin/stories/{id}/memories/{mid}/quarantine``."""
    shown = short_id(memory_id)
    page = ConfirmPage(
        title="Release memory",
        question=(
            "Release the quarantine on memory {name}? It becomes retrievable, and can be "
            "consolidated and placed in context again."
        ),
        emphasis=shown,
        confirm_label="Release",
        cancel_url=_back(request, story_id),
        facts=(("Memory id", memory_id), ("Story id", story_id)),
    )

    def perform(key: str) -> HttpResponse:
        return _row_write(
            request,
            story_id,
            memory_id,
            "release",
            lambda client, k: client.release_memory(story_id, memory_id, idempotency_key=k),
            key,
            module_off=True,
        )

    return confirm_action(request, page, perform, require_confirmed=True)


@require_http_methods(["GET", "HEAD", "POST"])
def quarantine(request: HttpRequest, story_id: str, memory_id: str) -> HttpResponse:
    """``inspect:memory-quarantine``: the reason page (no Engine call on GET), then
    ``POST /admin/stories/{id}/memories/{mid}/quarantine {reason}``.

    A reason the form refuses re-renders with the posted key (no call was made); an Engine
    refusal re-renders with a fresh key (the operator will change the body)."""
    back = _back(request, story_id)
    context: dict[str, Any] = {
        "story_id": story_id,
        "memory_id": memory_id,
        "memory_short": short_id(memory_id),
        "next_url": back,
    }
    if request.method != "POST":
        context.update(form=QuarantineForm(), idempotency_key=new_ulid())
        return render(request, QUARANTINE_TEMPLATE, context)
    key = posted_idempotency_key(request)
    form = QuarantineForm(request.POST)
    context.update(form=form, idempotency_key=key)
    if not form.is_valid():
        return render(request, QUARANTINE_TEMPLATE, context)
    client = engine_client_for(request)
    try:
        written = client.quarantine_memory(
            story_id, memory_id, form.cleaned_data["reason"], idempotency_key=key
        )
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        log_engine_error(request, exc)
        if isinstance(exc, EngineError):
            if is_module_off(exc):
                return _module_off(request, exc, story_id, back)
            if exc.code == BAD_RESPONSE and exc.http_status == 200:
                messages.warning(request, unreadable_text("quarantine", memory_id))
                return redirect(back)
        context["idempotency_key"] = new_ulid()
        if isinstance(exc, EngineError) and exc.code == VALIDATION_ERROR:
            form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and (text := refusal_text(exc, story_id)) is not None:
            context["refusal"] = text
        else:
            context["engine_error"] = panel_for(exc)
        return render(request, QUARANTINE_TEMPLATE, context)
    written_flash(request, "quarantine", memory_id, written)
    return redirect(back)


# --- sanitize (#144) -----------------------------------------------------------------------------


def explorer_text(request: HttpRequest, story_id: str, memory_id: str, back: str) -> str | None:
    """The memory's text as the explorer's question retrieves it now: ``back`` is the explorer's
    path for this story with its question (``q``, ``as_of``, ``actor``, ``include_quarantined``),
    asked again (one ``GET /stories/{id}/retrieve``, omniscient, #135 convention 1) and searched
    for this id among its memories, assessments and episodes. ``None`` (no read, or nothing
    shown) for any other ``next``, no ``q``, a question the form refuses, a failed read or an
    answer without this memory."""
    parts = urlsplit(back)
    if parts.path != reverse(Tab.RETRIEVAL.url_name, args=[story_id]):
        return None
    params = QueryDict(parts.query)
    if "q" not in params:
        return None
    form = RetrievalForm(params, cast=None, head=None)
    if not form.is_valid():
        return None
    try:
        body = engine_client_for(request).retrieve(story_id, **form.query())
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None
    for found in (*body.memories, *body.assessments, *body.episodes):
        if found.id == memory_id:
            return found.text
    return None


def _input_violations(exc: EngineError) -> list[ViolationRow]:
    """A 422 ``guardrail_violation``'s ``details.violations`` as #47's table rows."""
    found = exc.details.get("violations")
    return violation_rows(
        [v for v in found if isinstance(v, dict)] if isinstance(found, list) else []
    )


def sanitize_confirm(form: SanitizeForm, memory_id: str, back: str) -> ConfirmPage:
    """The confirm page (#134's confirmed mode) for a valid :class:`SanitizeForm`: the memory id,
    span and surrogate, and, when the text is shown, the occurrences and the text after. The
    validated inputs ride along hidden, so its POST needs no further read."""
    data = form.cleaned_data
    span, surrogate, text = data["span"], data["surrogate"], form.text
    facts: list[tuple[str, str]] = [
        ("Memory id", memory_id),
        ("Span", span),
        ("Surrogate", surrogate or EMPTY_SURROGATE),
    ]
    if text is not None:
        facts += [
            ("Occurrences", f"{text.count(span):,}"),
            ("After", text.replace(span, surrogate)),
        ]
    return ConfirmPage(
        title="Sanitize memory",
        question=SANITIZE_QUESTION,
        confirm_label="Sanitize",
        cancel_url=back,
        lines=(SURROGATE_KEEPS_SPAN,) if span in surrogate else (),
        facts=tuple(facts),
        hidden={"span": span, "surrogate": surrogate, "shown_text": text or ""},
        danger=True,
        buttons_template=SANITIZE_EDIT_TEMPLATE,
    )


def _sanitize_call(
    request: HttpRequest,
    story_id: str,
    memory_id: str,
    form: SanitizeForm,
    key: str,
    context: dict[str, Any],
) -> HttpResponse:
    """The confirmed sanitize: the call, then a redirect to ``next`` with the state flash, or the
    sanitize form again (inputs kept, a fresh key) with the refusal."""
    back = context["next_url"]
    data = form.cleaned_data
    try:
        written = engine_client_for(request).sanitize_memory(
            story_id, memory_id, data["span"], data["surrogate"], idempotency_key=key
        )
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        log_engine_error(request, exc)
        if isinstance(exc, EngineError):
            if is_module_off(exc):
                return _module_off(request, exc, story_id, back)
            if exc.code == BAD_RESPONSE and exc.http_status == 200:
                messages.warning(request, unreadable_text("sanitize", memory_id))
                return redirect(back)
        context["idempotency_key"] = new_ulid()  # the operator will change the body
        if isinstance(exc, EngineError) and exc.http_status == 422:
            if exc.code == GUARDRAIL_VIOLATION:
                context["violations"] = _input_violations(exc)
            else:
                form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and (text := refusal_text(exc, story_id)) is not None:
            context["refusal"] = text
        else:
            context["engine_error"] = panel_for(exc)
        return render(request, SANITIZE_TEMPLATE, context)
    written_flash(request, "sanitize", memory_id, written)
    return redirect(back)


@sensitive_post_parameters(*SANITIZE_SENSITIVE)
@require_http_methods(["GET", "HEAD", "POST"])
def sanitize(request: HttpRequest, story_id: str, memory_id: str) -> HttpResponse:
    """``inspect:memory-sanitize``: the sanitize page (GET: the explorer's one retrieve, to show
    the text), its POST → the confirm page (no call; an ``edit=1`` POST re-renders the form),
    and the confirm's ``confirmed=1`` POST → ``POST /admin/stories/{id}/memories/{mid}/sanitize``
    with the key the sanitize form rendered.

    A refused form re-renders with the posted key (no call was made); an Engine refusal
    re-renders with a fresh key."""
    back = _back(request, story_id)
    context: dict[str, Any] = {
        "story_id": story_id,
        "memory_id": memory_id,
        "memory_short": short_id(memory_id),
        "next_url": back,
    }
    if request.method != "POST":
        text = explorer_text(request, story_id, memory_id, back)
        shown = text if text is not None and len(text) <= SANITIZE_MAX else ""
        context.update(
            form=SanitizeForm(initial={"shown_text": shown}),
            text=text,
            idempotency_key=new_ulid(),
        )
        return render(request, SANITIZE_TEMPLATE, context)
    form = SanitizeForm(request.POST)
    valid = form.is_valid()
    context.update(form=form, text=form.text, idempotency_key=posted_idempotency_key(request))
    if not valid or request.POST.get("edit") == "1":
        return render(request, SANITIZE_TEMPLATE, context)
    return confirm_action(
        request,
        sanitize_confirm(form, memory_id, back),
        lambda key: _sanitize_call(request, story_id, memory_id, form, key, context),
        require_confirmed=True,
    )


# --- requesting consolidation (#145) -------------------------------------------------------------

CONSOLIDATE_TEMPLATE = "console/inspect/consolidate.html"
CONSOLIDATE_FRAGMENT = "consolidate"  # the Memory tab section's id
SCOPE_MISMATCH = "scope_mismatch"
SCOPE_MISMATCH_TEXT = (
    "These turns span scopes no single consolidation may combine. Pick a narrower window."
)
CONSOLIDATE_LINES = (
    "The Engine reads at most memory.consolidation.window_memories memories per run and snaps "
    "to whole turns, so the outputs may cover fewer turns.",
    "A window with nothing to consolidate still queues a job, which writes nothing.",
)
QUEUED_TEXT = (
    "Consolidation queued (job {job_id}). Its semantic facts and reflections appear in Retrieval "
    "when the worker finishes — use the links under Consolidate a window."
)
JOINED_TEXT = (
    " This is the job your request of {when} joined: the Engine gives a repeated request the job "
    "still waiting."
)
REPLAYED_TEXT = "Already queued (repeated submit) — job {job_id}."
CONSOLIDATE_UNREADABLE = (
    "Consolidation requested — the Engine's answer couldn't be read; its outputs, if any, appear "
    "in Retrieval as semantic facts and reflections"
)


def _memory_url(story_id: str) -> str:
    return reverse(Tab.MEMORY.url_name, args=[story_id])


def _window(data: Mapping[str, Any]) -> str:
    return f"turns {data['from_turn']}\u2013{data['to_turn']}"


def consolidate_confirm(data: Mapping[str, Any], story_id: str) -> ConfirmPage:
    """The confirm page (#134's confirmed mode) for a valid :class:`ConsolidateForm`; the
    window and the carried ``head`` ride along hidden, so its POST re-checks them."""
    window = _window(data)
    head = data.get("head")
    hidden = {"from_turn": str(data["from_turn"]), "to_turn": str(data["to_turn"])}
    if head is not None:
        hidden["head"] = str(head)
    return ConfirmPage(
        title=f"Consolidate {window}",
        question=(
            f"Consolidate {window} (shared scope)? A window already consolidated is rerun, and "
            "its earlier outputs are superseded."
        ),
        confirm_label="Consolidate",
        cancel_url=_memory_url(story_id),
        lines=CONSOLIDATE_LINES,
        facts=(("Story id", story_id), ("Window", window), ("Scope", "shared")),
        hidden=hidden,
        danger=True,
    )


def _shown_time(moment: datetime) -> str:
    """#135 convention 4's datetime, as plain text: ``2026-10-24 14:03 UTC``."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def queued_flash(
    request: HttpRequest, story_id: str, queued: ConsolidationQueued, key: str, pk: int | None
) -> None:
    """The flash for a 202. A replay (the #44 row ``pk`` the call just wrote says ``replayed``;
    no row counts as fresh) says only "Already queued". A fresh answer whose ``job_id`` an
    earlier ``consolidate_story`` row of this story holds under another key says it joined that
    request's job (the Engine coalesces a repeat while the job waits)."""
    row = EngineOperation.objects.filter(pk=pk).first() if pk is not None else None
    if row is not None and row.replayed:
        messages.info(request, REPLAYED_TEXT.format(job_id=queued.job_id))
        return
    text = QUEUED_TEXT.format(job_id=queued.job_id)
    earlier_rows = EngineOperation.objects.filter(
        kind=CONSOLIDATE_KIND, story_id=story_id, job_id=queued.job_id
    ).exclude(idempotency_key=key)
    if pk is not None:
        earlier_rows = earlier_rows.exclude(pk=pk)
    earlier = earlier_rows.order_by("-created_at", "-id").first()
    if earlier is not None:
        text += JOINED_TEXT.format(when=_shown_time(earlier.created_at))
    messages.success(request, text)


def _consolidate_call(
    request: HttpRequest,
    story_id: str,
    form: ConsolidateForm,
    key: str,
    context: dict[str, Any],
) -> HttpResponse:
    """The confirmed request: the call, then the Memory tab at ``#consolidate`` with the
    flash, or the consolidate page again (values kept, a fresh key) with the refusal."""
    back = _memory_url(story_id)
    data = form.cleaned_data
    client = engine_client_for(request)
    try:
        queued = client.consolidate_story(
            story_id, data["from_turn"], data["to_turn"], idempotency_key=key
        )
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        log_engine_error(request, exc)
        if isinstance(exc, EngineError):
            if is_module_off(exc):
                return _module_off(
                    request, exc, story_id, back, module=CONSOLIDATION, back_label="Back to Memory"
                )
            if exc.code == BAD_RESPONSE and exc.http_status and 200 <= exc.http_status < 300:
                messages.warning(request, CONSOLIDATE_UNREADABLE)
                return redirect(f"{back}#{CONSOLIDATE_FRAGMENT}")
        context["idempotency_key"] = new_ulid()  # the operator will change the window
        status = 200
        if isinstance(exc, EngineError) and exc.code == SCOPE_MISMATCH:
            context.update(refusal=SCOPE_MISMATCH_TEXT, scopes=exc.details.get("scopes"))
            status = 422
        elif isinstance(exc, EngineError) and exc.code == VALIDATION_ERROR:
            form.apply_engine_error(exc)
            status = 422
        elif isinstance(exc, EngineError) and (text := refusal_text(exc, story_id)) is not None:
            context["refusal"] = text
        else:
            context["engine_error"] = panel_for(exc)
        return render(request, CONSOLIDATE_TEMPLATE, context, status=status)
    queued_flash(request, story_id, queued, key, client.last_operation_id)
    return redirect(f"{back}#{CONSOLIDATE_FRAGMENT}")


@require_http_methods(["GET", "HEAD", "POST"])
def consolidate(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:consolidate``: the Memory tab's GET form lands here (no Engine call): neither
    turn given is the empty form; an invalid window re-renders the page with its errors; a valid
    one renders the confirm page carrying the Memory tab's key. Its ``confirmed=1`` POST
    re-checks the window and calls ``POST /admin/stories/{id}/consolidate`` (the shared scope)
    with that key; a POST without it re-renders the confirm page (#134)."""
    data = request.POST if request.method == "POST" else request.GET
    key = carried_idempotency_key(data)
    context: dict[str, Any] = {
        "story_id": story_id,
        "memory_url": _memory_url(story_id),
        "consolidate_url": reverse("inspect:consolidate", args=[story_id]),
        "idempotency_key": key,
    }
    if request.method != "POST" and "from_turn" not in data and "to_turn" not in data:
        context["form"] = ConsolidateForm(initial={"from_turn": "1"})
        return render(request, CONSOLIDATE_TEMPLATE, context)
    form = ConsolidateForm(data)
    context["form"] = form
    if not form.is_valid():
        return render(request, CONSOLIDATE_TEMPLATE, context)
    page = consolidate_confirm(form.cleaned_data, story_id)
    if request.method != "POST":
        page = dataclasses.replace(page, hidden={**page.hidden, CONFIRMED_FIELD: "1"})
        return render_confirm(request, page, idempotency_key=key)
    return confirm_action(
        request,
        page,
        lambda posted: _consolidate_call(request, story_id, form, posted, context),
        require_confirmed=True,
    )

"""Timeline operations (#53): regenerate, rewind, undo rewind, swap and fork, from the UI.

Routes under ``/stories/<id>/``: ``regenerate/``, ``rewind/<turn>/``, ``undo-rewind/``,
``swap/?chain=T:V``, ``fork/<turn>/``. Each GET renders a page and makes no Engine call; its POST
performs the operation. Regenerate, rewind, undo and swap change which turns are active, so
their GET is a confirm page (#45's :func:`console.confirm.confirm_action`); fork only creates,
so its GET is a plain form (the new story's name).

The link's ``rev``/``head``/``name`` come back as hidden fields (:mod:`console.forms.timeline`);
``rev`` is sent as ``X-Epistrel-Expected-Revision``. A missing or non-integer ``rev`` re-renders
the page with the error and sends nothing.

After a write: POST/redirect/GET to the play view with a flash naming the operation id, the new
timeline revision and the idempotency key (#43). A refusal renders ``timeline/refused.html``:
409 ``revision_conflict`` only the reload line; the rest in #48's inspector wording (busy,
erased subject, guardrail, model unavailable) or the error panel, plus links where the AC asks
(``unknown_chain``'s candidates, Variants and Fork after ``nothing_to_undo``).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from console.confirm import (
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
    safe_next,
)
from console.engine.client import EngineClientError, EngineError, turn_timeout
from console.engine.models import ReactivationCheck, RegenerateResult
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error
from console.engine_form_errors import apply_engine_errors
from console.forms.timeline import (
    FORK_ERRORS,
    REGENERATE_ERRORS,
    ForkForm,
    RegenerateForm,
    SwapForm,
    TimelineForm,
    default_fork_name,
    parse_chain,
)
from console.ids import new_ulid
from console.timeline import Basis, Link, chain_label, reload_message
from console.views.play import (
    FORK_BANNER_KEY,
    SNAPSHOT_FIELD,
    Inspector,
    PlayStory,
    error_inspector,
    read_story,
)

FIELDS_TEMPLATE = "console/timeline/_fields.html"
REGENERATE_FIELDS = "console/timeline/_regenerate_fields.html"
REFUSED_TEMPLATE = "console/timeline/refused.html"
FORK_TEMPLATE = "console/timeline/fork.html"
RECHECK_LINE = (
    "These turns were checked under older guardrails; the Engine re-checks them first and "
    "pauses the story until it finishes."
)
REGENERATE_QUESTION = (
    "Replace the latest reply? The current one is kept as a dormant variant you can swap back to."
)
NOTHING_TO_REGENERATE = "Nothing to regenerate — the latest turn has no reply."
NOTHING_TO_UNDO = "Nothing to undo — there is no rewind to reverse."
SWAP_INSTEAD = "New turns were added since the rewind — swap to the old chain instead"
# Regenerate refusals placed beside the form's fields (the operator's inputs kept).
REGENERATE_PLACED = frozenset({"validation_error", "viewpoint_unresolved"})


def _play_url(story_id: str, **params: str) -> str:
    path = reverse("stories:detail", args=[story_id])
    return f"{path}?{urlencode(params)}" if params else path


def _data(request: HttpRequest) -> Any:
    return request.POST if request.method == "POST" else request.GET


def _label(name: str, story_id: str) -> str:
    return name or story_id


def _facts(story_id: str, form: TimelineForm) -> tuple[tuple[str, str], ...]:
    rev = form.value("rev")
    return (
        ("Story id", story_id),
        ("Expected timeline revision", "—" if rev is None else str(rev)),
    )


def _flash(request: HttpRequest, text: str, *, operation: str | None, rev: int, key: str) -> None:
    op = f"operation {operation}, " if operation else ""
    messages.success(request, f"{text} — {op}timeline revision {rev}, idempotency key {key}")


def _recheck_flash(request: HttpRequest, check: ReactivationCheck, key: str) -> None:
    messages.success(
        request,
        f"Guardrail re-check started — the story is paused while check {check.job_id} runs; the "
        f"change applies only if every turn passes. Operation {check.operation_id}, timeline "
        f"revision {check.timeline_revision}, config revision {check.config_revision}, "
        f"idempotency key {check.idempotency_key or key}",
    )


# --- Refusals -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """What ``refused.html`` shows: the reload line alone, or a notice with links and #48's
    inspector content."""

    title: str
    story_id: str
    conflict: str = ""  # the reload line (409 revision_conflict)
    notice: str = ""
    links: tuple[Link, ...] = ()
    inspector: Inspector | None = None


def _basis(story_id: str, form: TimelineForm, next_url: str) -> Basis | None:
    rev = form.value("rev")
    if rev is None:
        return None
    head = form.value("head")
    return Basis(story_id, rev, head if head is not None else 0, form.shown_name, False, next_url)


def _candidates(exc: EngineError) -> list[tuple[int, int]]:
    """``unknown_chain``'s ``details.candidates``: ``{turn, variant}`` objects (or ``"T:V"``)."""
    found = []
    for raw in exc.details.get("candidates") or []:
        chain = None
        if isinstance(raw, dict):
            chain = parse_chain(f"{raw.get('turn')}:{raw.get('variant')}")
        elif isinstance(raw, str):
            chain = parse_chain(raw)
        if chain is not None:
            found.append(chain)
    return found


def _refused(
    request: HttpRequest,
    exc: EngineClientError,
    *,
    title: str,
    story_id: str,
    key: str,
    basis: Basis | None,
    candidate_link: Callable[[Basis, int, int], Link] | None = None,
) -> HttpResponse:
    """The page for a refused or failed operation (logged; nothing was changed)."""
    log_engine_error(request, exc)
    found = replace(error_inspector(exc, key), title="", revisions=False)
    refusal = Refusal(title=title, story_id=story_id, inspector=found)
    if isinstance(exc, EngineError):
        if exc.code == "revision_conflict":
            refusal = Refusal(
                title=title,
                story_id=story_id,
                conflict=reload_message(exc.details.get("current_revision")),
            )
        elif exc.code == "nothing_to_regenerate":
            refusal = replace(refusal, notice=NOTHING_TO_REGENERATE)
        elif exc.code == "nothing_to_undo":
            options = exc.details.get("options") or []
            links: tuple[Link, ...] = ()
            notice = NOTHING_TO_UNDO
            if "swap" in options:
                notice = f"{NOTHING_TO_UNDO} {SWAP_INSTEAD}."
                variants = reverse("stories:history", args=[story_id]) + "#variants"
                links = (Link("Variants", variants),)
                if basis is not None and "fork" in options:
                    links = (*links, basis.fork(basis.head))
            refusal = replace(refusal, notice=notice, links=links)
        elif exc.code == "unknown_chain" and basis is not None and candidate_link is not None:
            links = tuple(candidate_link(basis, t, v) for t, v in _candidates(exc))
            notice = "That chain continues in more than one way — choose one:" if links else ""
            refusal = replace(refusal, notice=notice, links=links)
    return render(request, REFUSED_TEMPLATE, {"refusal": refusal})


# --- Regenerate -----------------------------------------------------------------------------------


def _regenerate_story(request: HttpRequest, story_id: str, form: RegenerateForm) -> PlayStory:
    """The cast and turn deadline: the play page's signed snapshot, else one story read."""
    found = PlayStory.unsigned(form.data.get(SNAPSHOT_FIELD), story_id)
    return found if found is not None else read_story(request, story_id)[1]


@require_http_methods(["GET", "HEAD", "POST"])
def regenerate(request: HttpRequest, story_id: str) -> HttpResponse:
    data = _data(request)
    play_story = _regenerate_story(request, story_id, RegenerateForm(data, cast=()))
    form = RegenerateForm(data, cast=play_story.cast)
    valid = form.is_valid()
    back = safe_next(request, _play_url(story_id))
    page = ConfirmPage(
        title="Regenerate the latest reply",
        question=REGENERATE_QUESTION,
        confirm_label="Regenerate",
        cancel_url=back,
        facts=(*_facts(story_id, form), ("Turn", str(form.value("head") or "—"))),
        hidden={**form.hidden(), SNAPSHOT_FIELD: play_story.signed()},
        fields_template=REGENERATE_FIELDS,
    )
    extra = {"form": form, "archived": play_story.archived}

    def perform(key: str) -> HttpResponse:
        if not valid:
            return confirm_page(request, page, extra)
        try:
            result = engine_client_for(request).regenerate(
                story_id,
                form.body(),
                expected_revision=form.cleaned_data["rev"],
                idempotency_key=key,
                timeout=turn_timeout(play_story.turn_deadline),
            )
        except EngineClientError as exc:
            if isinstance(exc, EngineError) and exc.code in REGENERATE_PLACED:
                log_engine_error(request, exc)
                apply_engine_errors(exc, form, aliases=REGENERATE_ERRORS)
                return confirm_page(request, page, extra)
            return _refused(
                request,
                exc,
                title="Regenerate refused",
                story_id=story_id,
                key=key,
                basis=_basis(story_id, form, back),
            )
        return _regenerated(request, story_id, result, key)

    return confirm_action(request, page, perform, extra=extra)


def confirm_page(request: HttpRequest, page: ConfirmPage, extra: dict[str, Any]) -> HttpResponse:
    """The confirm page again (a fresh key), showing the form's errors; nothing was sent."""
    return render_confirm(request, page, extra=extra)


def _regenerated(
    request: HttpRequest, story_id: str, result: RegenerateResult, key: str
) -> HttpResponse:
    operation = result.entries[0].operation_id if result.entries else None
    config = f", config revision {result.config_revision}" if result.config_revision else ""
    _flash(
        request,
        f"Regenerated turn {result.turn}: variant {result.variant} is active, previous variant "
        f"{result.previous_variant} is kept as a dormant variant{config}",
        operation=operation,
        rev=result.timeline_revision,
        key=result.idempotency_key or key,
    )
    url = _play_url(story_id, turn=str(result.turn), viewpoint=result.output_viewpoint)
    return redirect(f"{url}#turn-{result.turn}")


# --- Rewind and undo ------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def rewind(request: HttpRequest, story_id: str, turn: int) -> HttpResponse:
    form = TimelineForm(_data(request))
    valid = form.is_valid()
    head = form.value("head")
    back = safe_next(request, _play_url(story_id))
    target = "the start (turn 0)" if turn == 0 else f"turn {turn}"
    if head is None:
        later = "Every later turn becomes inactive."
    else:
        count = max(head - turn, 0)
        later = f"{count} later turn{'s become' if count != 1 else ' becomes'} inactive."
    page = ConfirmPage(
        title="Rewind",
        question=f"Rewind {{name}} to {target}?",
        emphasis=_label(form.shown_name, story_id),
        lines=(later, "Nothing is deleted: Undo rewind brings them back."),
        confirm_label="Rewind",
        cancel_url=back,
        facts=_facts(story_id, form),
        hidden=form.hidden(),
        fields_template=FIELDS_TEMPLATE,
    )
    extra = {"form": form}

    def perform(key: str) -> HttpResponse:
        if not valid:
            return confirm_page(request, page, extra)
        try:
            result = engine_client_for(request).truncate(
                story_id, turn, expected_revision=form.cleaned_data["rev"], idempotency_key=key
            )
        except EngineClientError as exc:
            return _refused(
                request,
                exc,
                title="Rewind refused",
                story_id=story_id,
                key=key,
                basis=_basis(story_id, form, back),
            )
        _flash(
            request,
            f"Rewound to turn {result.to_turn}: next turn index {result.next_turn_index}, "
            f"{result.discarded_operations} discarded operations",
            operation=result.operation_id,
            rev=result.timeline_revision,
            key=result.idempotency_key or key,
        )
        return redirect(_play_url(story_id))

    return confirm_action(request, page, perform, extra=extra)


@require_http_methods(["GET", "HEAD", "POST"])
def undo_rewind(request: HttpRequest, story_id: str) -> HttpResponse:
    form = TimelineForm(_data(request))
    valid = form.is_valid()
    back = safe_next(request, _play_url(story_id))
    page = ConfirmPage(
        title="Undo rewind",
        question="Undo the last rewind of {name}?",
        emphasis=_label(form.shown_name, story_id),
        lines=(
            "The turns it set aside become active again. If new turns were added since, the "
            "Engine swaps back to the old chain and keeps the new turns as a dormant variant.",
        ),
        confirm_label="Undo rewind",
        cancel_url=back,
        facts=_facts(story_id, form),
        hidden=form.hidden(),
        fields_template=FIELDS_TEMPLATE,
    )
    extra = {"form": form}

    def perform(key: str) -> HttpResponse:
        if not valid:
            return confirm_page(request, page, extra)
        try:
            result = engine_client_for(request).untruncate(
                story_id, expected_revision=form.cleaned_data["rev"], idempotency_key=key
            )
        except EngineClientError as exc:
            return _refused(
                request,
                exc,
                title="Undo rewind refused",
                story_id=story_id,
                key=key,
                basis=_basis(story_id, form, back),
            )
        if isinstance(result, ReactivationCheck):
            _recheck_flash(request, result, key)
            return redirect(_play_url(story_id))
        if result.swapped and result.active_chain is not None:
            chain = chain_label(result.active_chain.turn, result.active_chain.variant)
            text = (
                f"Rewind undone as a swap — new turns had been added since the rewind, so chain "
                f"{chain} is active again and {result.dormant_operations} operations became "
                f"dormant; restored head turn {result.next_turn_index - 1}"
            )
        else:
            text = (
                f"Rewind undone: restored head turn {result.next_turn_index - 1}, "
                f"{result.restored_operations} restored operations"
            )
        _flash(
            request,
            text,
            operation=result.operation_id,
            rev=result.timeline_revision,
            key=result.idempotency_key or key,
        )
        return redirect(_play_url(story_id))

    return confirm_action(request, page, perform, extra=extra)


# --- Swap -----------------------------------------------------------------------------------------


def _swap_link(basis: Basis, turn: int, variant: int) -> Link:
    return basis.swap(turn, variant)


@require_http_methods(["GET", "HEAD", "POST"])
def swap(request: HttpRequest, story_id: str) -> HttpResponse:
    form = SwapForm(_data(request))
    valid = form.is_valid()
    chain = form.value("chain")
    back = safe_next(request, _play_url(story_id))
    shown = chain_label(*chain) if chain else str(form.data.get("chain", "")) or "—"
    lines = (RECHECK_LINE,) if form.data.get("check") == "1" else ()
    page = ConfirmPage(
        title="Swap variant",
        question=f"Make chain {shown} the active timeline of {{name}}?",
        emphasis=_label(form.shown_name, story_id),
        lines=(
            *lines,
            "Turns after the point where it parts from the active chain become dormant; "
            "nothing is deleted.",
        ),
        confirm_label="Swap",
        cancel_url=back,
        facts=_facts(story_id, form),
        hidden=form.hidden(),
        fields_template=FIELDS_TEMPLATE,
    )
    extra = {"form": form}

    def perform(key: str) -> HttpResponse:
        if not valid or chain is None:
            return confirm_page(request, page, extra)
        expected = form.cleaned_data["rev"]
        try:
            result = engine_client_for(request).swap(
                story_id,
                chain[0],
                chain[1],
                expected_revision=expected,
                idempotency_key=key,
                timeout=turn_timeout(None),
            )
        except EngineClientError as exc:
            return _refused(
                request,
                exc,
                title="Swap refused",
                story_id=story_id,
                key=key,
                basis=_basis(story_id, form, back),
                candidate_link=_swap_link,
            )
        if isinstance(result, ReactivationCheck):
            _recheck_flash(request, result, key)
            return redirect(_play_url(story_id))
        active = (result.active_chain.turn, result.active_chain.variant)
        if active == chain and result.timeline_revision == expected:
            messages.info(
                request,
                f"Already active — chain {chain_label(*chain)} is the active timeline "
                f"(timeline revision {result.timeline_revision}); nothing was changed",
            )
            return redirect(_play_url(story_id))
        _flash(
            request,
            f"Swapped: chain {chain_label(*active)} is active ({result.activated_operations} "
            f"operations activated, {result.dormant_operations} dormant), next turn index "
            f"{result.next_turn_index}",
            operation=result.operation_id,
            rev=result.timeline_revision,
            key=result.idempotency_key or key,
        )
        return redirect(_play_url(story_id))

    return confirm_action(request, page, perform, extra=extra)


# --- Fork -----------------------------------------------------------------------------------------


def _fork_link(basis: Basis, turn: int, variant: int) -> Link:
    link = basis.fork(basis.head, chain=(turn, variant))
    return Link(f"Fork along {chain_label(turn, variant)}", link.url)


def _fork_form_page(
    request: HttpRequest, story_id: str, turn: int, form: ForkForm, back: str
) -> HttpResponse:
    context = {
        "form": form,
        "story_id": story_id,
        "turn": turn,
        "label": _label(form.shown_name, story_id),
        "facts": _facts(story_id, form),
        "idempotency_key": new_ulid(),
        "next_url": back,
    }
    return render(request, FORK_TEMPLATE, context)


@require_http_methods(["GET", "HEAD", "POST"])
def fork(request: HttpRequest, story_id: str, turn: int) -> HttpResponse:
    """The fork form (GET, no Engine call); POST forks and redirects to the new story."""
    back = safe_next(request, _play_url(story_id))
    if request.method != "POST":
        data = request.GET.copy()
        if not data.get("new_name"):
            data["new_name"] = default_fork_name(data.get("name", ""), story_id, turn)
        form = ForkForm(data)
        form.is_valid()
        return _fork_form_page(request, story_id, turn, form, back)
    form = ForkForm(request.POST)
    if not form.is_valid():
        return _fork_form_page(request, story_id, turn, form, back)
    key = posted_idempotency_key(request)
    try:
        result = engine_client_for(request).fork(
            story_id,
            at_turn=turn,
            name=form.cleaned_data["new_name"],
            variant_chain=form.cleaned_data["variant_chain"],
            expected_revision=form.cleaned_data["rev"],
            idempotency_key=key,
        )
    except EngineClientError as exc:
        if isinstance(exc, EngineError) and exc.code == "validation_error":
            log_engine_error(request, exc)
            apply_engine_errors(exc, form, aliases=FORK_ERRORS)
            return _fork_form_page(request, story_id, turn, form, back)
        return _refused(
            request,
            exc,
            title="Fork refused",
            story_id=story_id,
            key=key,
            basis=_basis(story_id, form, back),
            candidate_link=_fork_link,
        )
    if isinstance(result, ReactivationCheck):
        _recheck_flash(request, result, key)
        return redirect(_play_url(story_id))
    request.session[FORK_BANNER_KEY.format(result.story_id)] = {
        "source_id": story_id,
        "source_name": form.shown_name,
        "at_turn": result.forked_from.at_turn,
        "revision": result.forked_from.revision,
        "characters": [[c.name, c.source_id, c.id] for c in result.characters],
    }
    _flash(
        request,
        f"Forked {_label(form.shown_name, story_id)} at turn {result.forked_from.at_turn} into "
        f"{result.name or result.story_id}",
        operation=result.operation_id,
        rev=result.timeline_revision,
        key=result.idempotency_key or key,
    )
    return redirect("stories:detail", story_id=result.story_id)

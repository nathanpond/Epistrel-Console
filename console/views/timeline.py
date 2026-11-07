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
timeline revision and the idempotency key (#43). Undo rewind, swap and fork may instead answer
202, held back for a reactivation re-check (#81): they redirect to #80's check page with
``?op=untruncate|swap|fork``, which follows the check to the edit's answer. A refusal renders
``timeline/refused.html``: 409 ``revision_conflict`` only the reload line; the rest in #48's
inspector wording (busy, erased subject, guardrail, model unavailable) or the error panel, plus
links where the AC asks (``unknown_chain``'s candidates, Variants and Fork after
``nothing_to_undo``).

Correct (#263, FR-RESP-14): ``correct/<turn>/`` replaces the latest turn's input in one Engine
operation (``POST /stories/{id}/correct``), the replaced turn kept dormant. Its GET reads the
story (as the play view does) and, for the head turn, the omniscient history at that turn to
pre-fill the turn's say/do and show its notices read-only; a setup, later-than-head,
not-latest or already-corrected turn offers no form, an archived or paused story shows the
form disabled (``ConfirmPage.blocked``). Its POST re-checks the head with one story read before
writing. Success redirects to the play view at the corrected turn, as regenerate does.

When a rewind's, undo rewind's, swap's or fork's answer lists external effects the edit could
not undo or re-send (#258), the POST answers 200 with ``timeline/effects_report.html`` instead:
the flash's success line, the lists (``_edit_effects.html``) and "Continue to the story", with
no flash queued. With every list empty the redirect and flash are as before.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode, urlsplit

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_http_methods

from console.canon import cast_names
from console.confirm import (
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
    safe_next,
)
from console.engine.client import EngineClient, EngineClientError, EngineError, turn_timeout
from console.engine.models import DirectedPending, ReactivationCheck, RegenerateResult, Story
from console.engine_access import engine_client_for
from console.engine_errors import EMPTY, ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors, engine_field_errors
from console.forms.timeline import (
    CHANNELS,
    CORRECT_ERRORS,
    FORK_ERRORS,
    REGENERATE_ERRORS,
    REWIND_ERRORS,
    CorrectForm,
    ForkForm,
    RegenerateForm,
    RewindForm,
    SwapForm,
    TimelineForm,
    default_fork_name,
    parse_chain,
    sign_original,
)
from console.ids import new_ulid
from console.inspect import history_turn_url
from console.notices import EXTERNAL_EFFECTS_TEXT, NoticeCards, notice_cards
from console.reactivation import FORK, OP_PARAM, SWAP, UNTRUNCATE
from console.story_pause import check_url, is_paused, story_paused_refusal
from console.timeline import (
    Basis,
    EditEffects,
    Link,
    chain_label,
    edit_effects,
    fork_effects,
    reload_message,
)
from console.views.play import (
    FORK_BANNER_KEY,
    SNAPSHOT_FIELD,
    Inspector,
    PlayStory,
    error_inspector,
    read_story,
)
from console.wording import archived_refusal, character_label
from console.world import slot_turn_text, world_refusal

log = logging.getLogger(__name__)

FIELDS_TEMPLATE = "console/timeline/_fields.html"
REWIND_FIELDS = "console/timeline/_rewind_fields.html"
REGENERATE_FIELDS = "console/timeline/_regenerate_fields.html"
REFUSED_TEMPLATE = "console/timeline/refused.html"
DIRECTED_TEMPLATE = "console/timeline/directed_pending.html"
# #292: a directed regenerate's 202 when the rewrite session page isn't registered yet.
HELD_FOR_REVIEW_TEXT = "The Engine held this reply for review in a rewrite session"
FORK_TEMPLATE = "console/timeline/fork.html"
REPORT_TEMPLATE = "console/timeline/effects_report.html"
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
SLOT_PATHS = frozenset({"body.slot", "slot"})  # a 422 about the slot k (#209)


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


def _success_text(text: str, *, operation: str | None, rev: int, key: str) -> str:
    op = f"operation {operation}, " if operation else ""
    return f"{text} — {op}timeline revision {rev}, idempotency key {key}"


def _flash(request: HttpRequest, text: str, *, operation: str | None, rev: int, key: str) -> None:
    messages.success(request, _success_text(text, operation=operation, rev=rev, key=key))


@dataclass(frozen=True)
class EditReport:
    """What ``effects_report.html`` shows (#258): the edit's title, the success line the flash
    would have carried, the effects it could not undo and where the redirect would have gone."""

    title: str
    success: str
    effects: EditEffects
    continue_url: str


def _report(
    request: HttpRequest, *, title: str, success: str, effects: EditEffects, continue_url: str
) -> HttpResponse:
    report = EditReport(title, success, effects, continue_url)
    return render(request, REPORT_TEMPLATE, {"report": report})


def _held(
    request: HttpRequest,
    story_id: str,
    check: ReactivationCheck,
    *,
    op: str,
    key: str,
    client: EngineClient,
) -> HttpResponse:
    """A 202 (#81): the edit is held back for a reactivation re-check. Redirect to #80's check
    page with ``?op=``, built from the body's ``job_id`` (a ``Location`` that disagrees is
    logged and ignored). The flash names the hold, not #53's success; a fork's revision is the
    source's, so it is left out."""
    location = client.last_location
    expected = f"/content-rating/checks/{check.job_id}"
    if location and not urlsplit(location).path.endswith(expected):
        log.warning(
            "reactivation check: Location %s disagrees with job_id %s", location, check.job_id
        )
    revision = "" if op == FORK else f", revision {check.timeline_revision}"
    messages.info(
        request,
        f"Held for a guardrail re-check: operation {check.operation_id}, key "
        f"{check.idempotency_key or key}{revision}",
    )
    return redirect(check_url(story_id, check.job_id, **{OP_PARAM: op}))


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
    nothing_text: str = NOTHING_TO_REGENERATE,
    turn: int | None = None,
) -> HttpResponse:
    """The page for a refused or failed operation (logged; nothing was changed). ``turn`` (a
    correction's, #263) adds the notices and links for ``not_latest_turn``,
    ``already_corrected`` and ``provider_unbound``; ``nothing_text`` is
    ``nothing_to_regenerate``'s notice."""
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
            refusal = replace(refusal, notice=nothing_text)
        elif exc.code == "not_latest_turn" and turn is not None:
            refusal = replace(
                refusal, notice=not_latest_text(turn), links=_not_latest_links(story_id, turn)
            )
        elif exc.code == "already_corrected" and turn is not None:
            refusal = replace(
                refusal,
                notice=already_corrected_text(turn),
                links=(_variants_link(story_id),),
            )
        elif exc.code == "provider_unbound" and turn is not None:
            unbound = world_refusal(exc, story_id, CORRECT_VERB, None)
            if unbound is not None:
                shown = (Link("Providers", unbound.url),) if unbound.url else ()
                refusal = replace(refusal, notice=unbound.text, links=shown)
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
        if isinstance(result, DirectedPending):
            return _held_for_review(request, story_id, result)
        return _regenerated(request, story_id, result, key)

    return confirm_action(request, page, perform, extra=extra)


def _rewrite_session_url(story_id: str, session_id: str) -> str | None:
    """The rewrite session page (#297), ``None`` while ``stories:rewrite`` is unregistered (D19)
    or for a blank id. TODO(#294): use ``console.rewrites.rewrite_session_url`` (E6)."""
    if not session_id:
        return None
    try:
        return reverse("stories:rewrite", args=[story_id, session_id])
    except NoReverseMatch:
        return None


def _held_for_review(request: HttpRequest, story_id: str, pending: DirectedPending) -> HttpResponse:
    """A directed regenerate's 202 (#292): the rewrite session holding the reply, else (its
    page not registered yet) #43's panel with :data:`HELD_FOR_REVIEW_TEXT`. Nothing sends a
    directive before #301, which replaces this; no flash."""
    session_id = pending.rewrite_session.id
    url = _rewrite_session_url(story_id, session_id)
    if url is not None:
        return redirect(url)
    context = {
        "engine_error": ErrorPanel(HELD_FOR_REVIEW_TEXT),
        "session_id": session_id,
        "play_url": _play_url(story_id),
    }
    return render(request, DIRECTED_TEMPLATE, context, status=200)


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


# --- Correct (#263) ------------------------------------------------------------------------------

CORRECT_FIELDS = "console/timeline/_correct_fields.html"
CORRECT_REFUSED = "Correction refused"
CORRECT_VERB = "correct a turn"
CORRECT_HISTORY_LIMIT = 200
SETUP_TEXT = "Turn 0 is the story's setup — there is nothing to correct"


def correct_lines(turn: int) -> tuple[str, str]:
    return (
        f"The story is rewound to before turn {turn} and the new input is played as its next "
        "variant, in one operation.",
        "The replaced turn is kept as a dormant variant — swap back to it from History \u203a "
        "Variants.",
    )


def not_latest_text(turn: int) -> str:
    return (
        f"Turn {turn} is no longer the story's latest — a later turn has committed, so it can't "
        "be corrected. Rewind or fork to change it."
    )


def ahead_text(turn: int, head: int) -> str:
    return f"Turn {turn} doesn't exist yet — the story's latest turn is {head}"


def already_corrected_text(turn: int) -> str:
    return (
        f"Turn {turn} was written by a correction — it can't be corrected again. The original "
        "is a dormant variant: swap back from History \u203a Variants."
    )


def nothing_to_correct_text(turn: int) -> str:
    return (
        f"Nothing to correct — turn {turn} has no reply yet (it may still be waiting on its "
        "outcome)"
    )


def unread_input_text(turn: int) -> str:
    return f"Couldn't read turn {turn}'s input — enter the replacement"


def absent_input_text(turn: int) -> str:
    return f"Turn {turn}'s input isn't in the history — enter the replacement"


def reconciliation_clause(count: int) -> str:
    """ "; 1 external effect stays listed for reconciliation" ("" for none)."""
    if not count:
        return ""
    if count == 1:
        return "; 1 external effect stays listed for reconciliation"
    return f"; {count} external effects stay listed for reconciliation"


def _variants_link(story_id: str) -> Link:
    return Link("Variants", reverse("stories:history", args=[story_id]) + "#variants")


def _not_latest_links(story_id: str, turn: int) -> tuple[Link, ...]:
    return (
        Link(f"History at turn {turn}", history_turn_url(story_id, turn)),
        _variants_link(story_id),
    )


@dataclass(frozen=True)
class Blocked:
    """Why the correction page can't proceed: the text (``ConfirmPage.blocked``), its links, and
    whether the say/do fields still show (disabled: archived, paused) or are left out."""

    text: str
    links: tuple[Link, ...] = ()
    fields: bool = False


def _head(story: Story) -> int:
    return story.next_turn_index - 1


def _blocked(story: Story, turn: int) -> Blocked | None:
    """The no-form or disabled state the story read puts the page in, else ``None``."""
    head = _head(story)
    if turn == 0:
        return Blocked(SETUP_TEXT)
    if turn > head:
        return Blocked(ahead_text(turn, head))
    if turn < head:
        return Blocked(not_latest_text(turn), _not_latest_links(story.id, turn))
    if story.status == "archived":
        return Blocked(archived_refusal(CORRECT_VERB), fields=True)
    if is_paused(story):
        paused = story_paused_refusal(story)
        links = (Link("The check", paused.url),) if paused.url else ()
        return Blocked(paused.text, links, fields=True)
    return None


@dataclass(frozen=True)
class TurnInput:
    """What the history read gave the page: turn N's say/do (``None``: no readable item on that
    channel), the Actor label, its notices (read-only cards), whether any has external effects
    or was written by a correction, and the read's panel when it failed."""

    say: str | None = None
    do: str | None = None
    actor: str = EMPTY
    notices: NoticeCards | None = None
    external: bool = False
    corrected: bool = False
    error: ErrorPanel | None = None

    @property
    def found(self) -> bool:
        return self.say is not None or self.do is not None


def _read_turn(request: HttpRequest, story: Story, turn: int) -> TurnInput:
    """One omniscient history read as of ``turn``: the turn's ``player_input`` items (a redacted
    or quarantined item counts as absent; several on one channel joined in beat order) and its
    active-chain notices."""
    try:
        history = engine_client_for(request).get_history(
            story.id, viewpoint="omniscient", as_of=turn, limit=CORRECT_HISTORY_LIMIT
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return TurnInput(error=panel_for(exc))
    names = cast_names(story.cast)
    inputs = [i for i in history.entries if i.kind == "player_input" and i.turn == turn]
    inputs.sort(key=lambda item: item.beat if item.beat is not None else 0)
    texts: dict[str, str | None] = {}
    for channel in CHANNELS:
        shown = [
            i.text for i in inputs if i.channel == channel and not i.redacted and not i.quarantined
        ]
        texts[channel] = "\n".join(shown) if shown else None
    actor = next((i.actor_character_id for i in inputs if i.actor_character_id), None)
    summary = next((t for t in history.turns if t.turn == turn), None)
    cards, external, corrected = None, False, False
    if summary is not None:
        raw = summary.unparsed.get("notices")
        cards = notice_cards(
            raw if raw is not None else summary.notices,
            story.id,
            names,
            overridden=frozenset(),
            latest=None,
            controls=False,
        )
        external = any(n.external_effects for n in summary.notices)
        corrected = any(not n.correctable for n in summary.notices)
    return TurnInput(
        say=texts["say"],
        do=texts["do"],
        actor=character_label(actor, names) if actor else EMPTY,
        notices=cards or None,
        external=external,
        corrected=corrected,
    )


@dataclass(frozen=True)
class CorrectView:
    """``_correct_fields.html``'s context beside the form: whether the fields show, the blocked
    state's links, the history read and the line above the fields."""

    fields: bool
    links: tuple[Link, ...] = ()
    read: TurnInput | None = None
    line: str = ""


def _correct_page(
    story_id: str,
    turn: int,
    story: Story,
    form: CorrectForm,
    back: str,
    *,
    blocked: Blocked | None,
    read: TurnInput | None,
    hidden: dict[str, str],
    engine_error: ErrorPanel | None = None,
) -> tuple[ConfirmPage, dict[str, Any]]:
    lines: tuple[str, ...] = correct_lines(turn)
    if read is not None and read.external:
        lines = (*lines, EXTERNAL_EFFECTS_TEXT)
    rev = hidden.get("rev") or EMPTY
    fields = blocked is None or blocked.fields
    if blocked is not None and blocked.fields:
        for name in CHANNELS:
            form.fields[name].widget.attrs["disabled"] = True
    line = ""
    if fields and read is not None:
        if read.error is not None:
            line = unread_input_text(turn)
        elif not read.found and not form.is_bound:
            line = absent_input_text(turn)
    page = ConfirmPage(
        title=f"Correct turn {turn}",
        question=f"Replace turn {turn}'s input in {{name}}?",
        emphasis=_label(story.name or "", story_id),
        lines=lines,
        confirm_label="Correct turn",
        cancel_url=back,
        facts=(
            ("Story id", story_id),
            ("Expected timeline revision", rev),
            ("Turn", str(turn)),
            ("Actor", read.actor if read is not None else EMPTY),
        ),
        hidden=hidden,
        fields_template=CORRECT_FIELDS,
        blocked=blocked.text if blocked is not None else "",
    )
    view = CorrectView(fields, blocked.links if blocked is not None else (), read, line)
    extra: dict[str, Any] = {"form": form, "correct": view}
    if engine_error is not None:
        extra["engine_error"] = engine_error
    return page, extra


def _initial(
    story: Story, play_story: PlayStory, turn: int, read: TurnInput | None
) -> dict[str, Any]:
    initial: dict[str, Any] = {
        "rev": story.timeline_revision,
        "head": _head(story),
        "name": story.name or "",
        "snapshot": play_story.signed(),
    }
    if read is not None and read.found:
        initial.update(say=read.say or "", do=read.do or "")
        initial["original"] = sign_original(story.id, turn, read.say, read.do)
    return initial


def _input_refused(exc: EngineError) -> bool:
    """A 422 ``validation_error`` about the input only (re-rendered beside Say/Do)."""
    if exc.code != "validation_error":
        return False
    failures = engine_field_errors(exc)
    return bool(failures) and all(f.path.removeprefix("body.") in CORRECT_ERRORS for f in failures)


def _unknown_outcome(exc: EngineClientError) -> bool:
    """A transport error, timeout or unreadable answer: the write may have happened."""
    return not isinstance(exc, EngineError) or exc.code == "bad_response"


@require_http_methods(["GET", "HEAD", "POST"])
def correct(request: HttpRequest, story_id: str, turn: int) -> HttpResponse:
    back = safe_next(request, _play_url(story_id))
    if request.method == "POST":
        return _correct_post(request, story_id, turn, back)
    story, play_story, _ = read_story(request, story_id)
    blocked = _blocked(story, turn)
    read = _read_turn(request, story, turn) if blocked is None or blocked.fields else None
    if blocked is None and read is not None and read.corrected:
        blocked = Blocked(already_corrected_text(turn), (_variants_link(story_id),))
    initial = _initial(story, play_story, turn, read)
    form = CorrectForm(story_id=story_id, turn=turn, initial=initial)
    page, extra = _correct_page(
        story_id, turn, story, form, back, blocked=blocked, read=read, hidden=form.hidden()
    )
    return confirm_action(
        request, page, lambda _key: render_confirm(request, page, extra=extra), extra=extra
    )


def _correct_post(request: HttpRequest, story_id: str, turn: int, back: str) -> HttpResponse:
    """The confirmed correction: re-check the head and status (one story read), then one
    ``/correct`` with the posted revision and key (D8's re-render rule on the way back)."""
    key = posted_idempotency_key(request)
    form = CorrectForm(request.POST, story_id=story_id, turn=turn)
    snapshot = PlayStory.unsigned(request.POST.get(SNAPSHOT_FIELD), story_id)
    try:
        if snapshot is not None:
            story = engine_client_for(request).get_story(story_id)
            play_story = snapshot
        else:
            story, play_story, _ = read_story(request, story_id)
    except EngineClientError as exc:
        return _refused(
            request, exc, title=CORRECT_REFUSED, story_id=story_id, key=key, basis=None, turn=turn
        )
    blocked = _blocked(story, turn)

    def page_for(
        hidden: dict[str, str], engine_error: ErrorPanel | None = None
    ) -> tuple[ConfirmPage, dict[str, Any]]:
        read = _read_turn(request, story, turn) if blocked is None or blocked.fields else None
        return _correct_page(
            story_id,
            turn,
            story,
            form,
            back,
            blocked=blocked,
            read=read,
            hidden=hidden,
            engine_error=engine_error,
        )

    if blocked is not None:  # a stale or forged POST: the no-form state again, nothing sent
        hidden = {**form.hidden(), "rev": str(story.timeline_revision)}
        page, extra = page_for(hidden)
        return confirm_action(
            request, page, lambda _key: render_confirm(request, page, extra=extra), extra=extra
        )

    def perform(key: str) -> HttpResponse:
        if not form.is_valid():  # local invalid input: the posted key and revision kept
            page, extra = page_for(form.hidden())
            return render_confirm(request, page, extra=extra, idempotency_key=key)
        try:
            result = engine_client_for(request).correct(
                story_id,
                form.body(),
                expected_revision=form.cleaned_data["rev"],
                idempotency_key=key,
                timeout=turn_timeout(play_story.turn_deadline),
            )
        except EngineClientError as exc:
            if _unknown_outcome(exc):  # replay with the same key and revision
                log_engine_error(request, exc)
                page, extra = page_for(form.hidden(), panel_for(exc))
                return render_confirm(request, page, extra=extra, idempotency_key=key)
            if isinstance(exc, EngineError) and _input_refused(exc):  # a fresh key and revision
                log_engine_error(request, exc)
                apply_engine_errors(exc, form, aliases=CORRECT_ERRORS)
                page, extra = page_for({**form.hidden(), "rev": str(story.timeline_revision)})
                return render_confirm(request, page, extra=extra)
            return _refused(
                request,
                exc,
                title=CORRECT_REFUSED,
                story_id=story_id,
                key=key,
                basis=_basis(story_id, form, back),
                nothing_text=nothing_to_correct_text(turn),
                turn=turn,
            )
        return _corrected(request, story_id, result, key)

    page, extra = _correct_page(
        story_id, turn, story, form, back, blocked=None, read=None, hidden=form.hidden()
    )
    return confirm_action(request, page, perform, extra=extra)


def _corrected(
    request: HttpRequest, story_id: str, result: RegenerateResult, key: str
) -> HttpResponse:
    operation = result.entries[0].operation_id if result.entries else None
    config = f", config revision {result.config_revision}" if result.config_revision else ""
    _flash(
        request,
        f"Corrected turn {result.turn}: variant {result.variant} is active, the replaced variant "
        f"{result.previous_variant} is kept as a dormant variant{config}"
        f"{reconciliation_clause(len(result.reconciliation_required))}",
        operation=operation,
        rev=result.timeline_revision,
        key=result.idempotency_key or key,
    )
    url = _play_url(story_id, turn=str(result.turn), viewpoint=result.output_viewpoint)
    return redirect(f"{url}#turn-{result.turn}")


# --- Rewind and undo ------------------------------------------------------------------------------


def _slot_k(form: TimelineForm) -> int | None:
    """The form's valid slot k when it is at least 1 (0, blank or invalid: ``None``)."""
    k = form.value("slot")
    return k if isinstance(k, int) and k >= 1 else None


def _rewind_slot_line(turn: int, k: int | None) -> str:
    if k is None:
        return (
            f"Turn {turn}'s own inter-turn slots become inactive too, unless you keep slots 1..k "
            "below."
        )
    if k == 1:
        return f"Turn {turn}'s inter-turn slot 1 is kept; later slots become inactive."
    return f"Turn {turn}'s inter-turn slots 1\u2013{k} are kept; later slots become inactive."


def _fork_slots_note(k: int | None) -> str:
    if k is None:
        return ""
    if k == 1:
        return "(its inter-turn slot 1 included)"
    return f"(its inter-turn slots 1\u2013{k} included)"


def _slot_refused(exc: EngineError) -> bool:
    """Whether a refusal names the slot field, so it goes beside Inter-turn slot."""
    return any(failure.path in SLOT_PATHS for failure in engine_field_errors(exc))


@require_http_methods(["GET", "HEAD", "POST"])
def rewind(request: HttpRequest, story_id: str, turn: int) -> HttpResponse:
    form = RewindForm(_data(request), turn=turn)
    valid = form.is_valid()
    head = form.value("head")
    k = _slot_k(form)
    back = safe_next(request, _play_url(story_id))
    if k is not None:
        target = slot_turn_text(turn, k)
    else:
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
        lines=(
            later,
            _rewind_slot_line(turn, k),
            "Nothing is deleted: Undo rewind brings them back.",
        ),
        confirm_label="Rewind",
        cancel_url=back,
        facts=_facts(story_id, form),
        hidden=form.hidden(),
        fields_template=REWIND_FIELDS,
    )
    extra = {"form": form}

    def perform(key: str) -> HttpResponse:
        if not valid:
            return confirm_page(request, page, extra)
        try:
            result = engine_client_for(request).truncate(
                story_id,
                turn,
                slot=form.cleaned_data["slot"],
                expected_revision=form.cleaned_data["rev"],
                idempotency_key=key,
            )
        except EngineClientError as exc:
            if isinstance(exc, EngineError) and _slot_refused(exc):
                log_engine_error(request, exc)
                apply_engine_errors(exc, form, aliases=REWIND_ERRORS)
                return confirm_page(request, page, extra)
            return _refused(
                request,
                exc,
                title="Rewind refused",
                story_id=story_id,
                key=key,
                basis=_basis(story_id, form, back),
            )
        text = (
            f"Rewound to {slot_turn_text(result.to_turn, k)}: next turn index "
            f"{result.next_turn_index}, "
            f"{result.discarded_operations} discarded operations"
        )
        success = _success_text(
            text,
            operation=result.operation_id,
            rev=result.timeline_revision,
            key=result.idempotency_key or key,
        )
        effects = edit_effects(story_id, result)
        if effects is not None:
            return _report(
                request,
                title="Rewind",
                success=success,
                effects=effects,
                continue_url=_play_url(story_id),
            )
        messages.success(request, success)
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
        client = engine_client_for(request)
        try:
            result = client.untruncate(
                story_id,
                expected_revision=form.cleaned_data["rev"],
                idempotency_key=key,
                timeout=turn_timeout(None),
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
            return _held(request, story_id, result, op=UNTRUNCATE, key=key, client=client)
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
        success = _success_text(
            text,
            operation=result.operation_id,
            rev=result.timeline_revision,
            key=result.idempotency_key or key,
        )
        effects = edit_effects(story_id, result)
        if effects is not None:
            return _report(
                request,
                title="Undo rewind",
                success=success,
                effects=effects,
                continue_url=_play_url(story_id),
            )
        messages.success(request, success)
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
        client = engine_client_for(request)
        try:
            result = client.swap(
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
            return _held(request, story_id, result, op=SWAP, key=key, client=client)
        active = (result.active_chain.turn, result.active_chain.variant)
        if active == chain and result.timeline_revision == expected:
            messages.info(
                request,
                f"Already active — chain {chain_label(*chain)} is the active timeline "
                f"(timeline revision {result.timeline_revision}); nothing was changed",
            )
            return redirect(_play_url(story_id))
        text = (
            f"Swapped: chain {chain_label(*active)} is active ({result.activated_operations} "
            f"operations activated, {result.dormant_operations} dormant), next turn index "
            f"{result.next_turn_index}"
        )
        success = _success_text(
            text,
            operation=result.operation_id,
            rev=result.timeline_revision,
            key=result.idempotency_key or key,
        )
        effects = edit_effects(story_id, result)
        if effects is not None:
            return _report(
                request,
                title="Swap variant",
                success=success,
                effects=effects,
                continue_url=_play_url(story_id),
            )
        messages.success(request, success)
        return redirect(_play_url(story_id))

    return confirm_action(request, page, perform, extra=extra)


# --- Fork -----------------------------------------------------------------------------------------


def _fork_link(basis: Basis, turn: int, variant: int, *, slot: int | None = None) -> Link:
    """An ``unknown_chain`` candidate: the fork form again along ``T:V``, carrying the valid slot
    k the operator posted (#209) so the re-opened form shows it."""
    extra = {"variant_chain": f"{turn}:{variant}"}
    if slot is not None:
        extra["slot"] = str(slot)
    return Link(f"Fork along {chain_label(turn, variant)}", basis.url("fork", basis.head, **extra))


def _fork_form_page(
    request: HttpRequest, story_id: str, turn: int, form: ForkForm, back: str
) -> HttpResponse:
    k = _slot_k(form)
    context = {
        "form": form,
        "story_id": story_id,
        "turn": turn,
        "turn_text": slot_turn_text(turn, k),
        "slots_note": _fork_slots_note(k),
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
        form = ForkForm(data, turn=turn)
        form.is_valid()
        return _fork_form_page(request, story_id, turn, form, back)
    form = ForkForm(request.POST, turn=turn)
    if not form.is_valid():
        return _fork_form_page(request, story_id, turn, form, back)
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        result = client.fork(
            story_id,
            at_turn=turn,
            name=form.cleaned_data["new_name"],
            variant_chain=form.cleaned_data["variant_chain"],
            slot=form.cleaned_data["slot"],
            expected_revision=form.cleaned_data["rev"],
            idempotency_key=key,
            timeout=turn_timeout(None),
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
            candidate_link=lambda basis, t, v: _fork_link(basis, t, v, slot=form.value("slot")),
        )
    if isinstance(result, ReactivationCheck):
        return _held(request, story_id, result, op=FORK, key=key, client=client)
    request.session[FORK_BANNER_KEY.format(result.story_id)] = {
        "source_id": story_id,
        "source_name": form.shown_name,
        "at_turn": result.forked_from.at_turn,
        "revision": result.forked_from.revision,
        "characters": [[c.name, c.source_id, c.id] for c in result.characters],
    }
    text = (
        f"Forked {_label(form.shown_name, story_id)} at "
        f"{slot_turn_text(result.forked_from.at_turn, _slot_k(form))} into "
        f"{result.name or result.story_id}"
    )
    success = _success_text(
        text,
        operation=result.operation_id,
        rev=result.timeline_revision,
        key=result.idempotency_key or key,
    )
    effects = fork_effects(story_id, result)
    if effects is not None:
        return _report(
            request,
            title="Fork",
            success=success,
            effects=effects,
            continue_url=reverse("stories:detail", args=[result.story_id]),
        )
    messages.success(request, success)
    return redirect("stories:detail", story_id=result.story_id)

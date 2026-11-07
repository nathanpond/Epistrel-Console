"""Edit one recorded line from History (#305; FR-LITE-8, FR-REWRITE-12; E2, E4, E5, E11, E21).

``stories:line-edit`` (``/stories/<id>/history/line/``, GET/HEAD/POST) renders through #296's
:func:`console.views.story_pages.story_page` with Rewrite current (a line edit is a rewrite
session). The page is omniscient-only (E2): it shows ``OMNISCIENT_LINE`` and no viewpoint
picker, and every read and write it makes sends ``viewpoint=omniscient``.

**The target** (:func:`console.forms.rewrites.line_target`): ``?event_id=`` (a history item's
``message_id``; History's "Edit this line…" link names the line this way, with ``turn`` to read
it), else ``?turn=`` and an optional ``?beat=``. Neither, or a malformed value, is
:data:`NO_LINE_TEXT` inside the frame (200), with no Engine call beyond the frame's and no form.
The POST takes its target from the form's hidden fields; the query string feeds only the reads.

**Reads per render**: the frame's two, then one omniscient
``get_history(viewpoint="omniscient", as_of=str(turn), limit=200)`` when ``turn`` is known
(defaults otherwise: no audit, no summary, the active timeline). By id, any item of the read
whose ``message_id`` is ``event_id``; by turn, the turn's items at ``beat``. Each matched item is
listed as "Turn <t> · beat <b> · <speaker> · <channel>" over its text, and the box is pre-filled
with their texts joined by a newline (:data:`SHARED_MESSAGE_NOTE` when there are several). No
``turn``, a failed read, no match, or a redacted or quarantined match is :data:`LINE_UNREAD` and
an empty box with no ``original``. By turn with no beat over several messages, each message is a
choice link (as ``beat_required``'s are) and the box is empty.

**Submitting** (POST, PRG): an invalid form makes no write. A valid one calls ``open_rewrite``
once with :meth:`LineEditForm.body`, then 303s to the session page with E21's flash
(:func:`console.rewrites.session_write_flash`, verb :data:`OPENED_VERB`). A refusal re-renders
(200), the typed text kept and the line re-read: E5's ``rewrite_refusal`` sentence (one that
names a field goes on ``new_text``, the form's one text box), ``beat_required``'s choice links
from ``details.beats``, ``validation_error`` through ``apply_engine_errors``, else #43's panel.
D8's key: fresh after an answered refusal, the posted one after an unknown outcome.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import NoReverseMatch, reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods

from console import cast as cast_helpers
from console.confirm import posted_idempotency_key, safe_next
from console.engine.client import EngineClientError, EngineError
from console.engine.models import HistoryItem
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.rewrites import (
    PLAN_EDIT,
    LineEditForm,
    LineTarget,
    line_target,
    normalised_line,
)
from console.held import is_held
from console.history import narrated_label, split_audit
from console.ids import is_ulid, new_ulid
from console.portability import is_importing
from console.raw import EMPTY
from console.rewrites import (
    BEAT_REQUIRED_TEXT,
    rewrite_refusal,
    rewrite_session_url,
    rewrites_url,
    session_write_flash,
)
from console.views.rewrites import ALIASES, REFUSAL_LINKS
from console.views.story_pages import StoryPageContext, story_page
from console.wording import humanised

TEMPLATE = "console/rewrites/line_edit.html"
CURRENT = "stories:rewrites"
LABEL = "Edit a line"
VERB_PHRASE = "edit a line"
OPENED_VERB = "Line edit opened"  # E21's fallback flash ("<verb> — the session is now <state>")
LINE_LIMIT = 200  # the history read's page (#263's Correct page reads the same)
BAD_RESPONSE = "bad_response"
VALIDATION = "validation_error"
BEAT_REQUIRED = "beat_required"
NEW_TEXT = "new_text"

NO_LINE_TEXT = "That isn't a line of this story — open it from History"
LINE_UNREAD = "The line's current text couldn't be read — the Engine will still find it by its id"
SHARED_MESSAGE_NOTE = "This message holds several lines; the new text replaces all of them"
SEVERAL_MESSAGES_TEXT = BEAT_REQUIRED_TEXT  # by turn, no beat, several messages (planner)
BACK_TEXT = "← Back to History"
BEAT_CHOICE = "Beat {beat}"
# E5's links, plus ``record_inactive``'s swap (History's Variants).
LINKS: Mapping[str, str] = {**REFUSAL_LINKS, "record_inactive": "Variants"}
DEFAULT_LINK = "See the check"


@dataclass(frozen=True)
class LineItem:
    """One matched history item: its header line ("Turn 2 · beat 1 · Alice · say") and text."""

    header: str
    text: str


@dataclass(frozen=True)
class Choice:
    """One "choose a line" entry: its text and the page's URL for it (``None``: text only)."""

    text: str
    url: str | None


@dataclass
class LineRead:
    """What the page read of the line: the matched items, the prefill (``None`` when unread),
    the back link's fragment, and the choices when a turn holds several messages."""

    items: list[LineItem] = field(default_factory=list)
    original: str | None = None
    fragment: str = ""
    choices: list[Choice] = field(default_factory=list)

    @property
    def shared(self) -> bool:
        return len(self.items) > 1


def _history_url(story_id: str) -> str:
    try:
        return reverse("stories:history", args=[story_id])
    except NoReverseMatch:
        return ""


def _page_url(
    request: HttpRequest, story_id: str, event_id: str, turn: int | None, back: str
) -> str:
    query: dict[str, str] = {"event_id": event_id}
    if turn is not None:
        query["turn"] = str(turn)
    if back:
        query["next"] = back
    return f"{request.path}?{urlencode(query)}"


def _header(item: HistoryItem, labels: Mapping[str, str]) -> str:
    parts = [f"Turn {item.turn if item.turn is not None else EMPTY}"]
    if item.beat is not None:
        parts.append(f"beat {item.beat}")
    parts += [narrated_label(item, labels), item.channel]
    return " · ".join(parts)


def _labels(ctx: StoryPageContext) -> dict[str, str]:
    return cast_helpers.character_labels(ctx.story.cast) if ctx.story is not None else {}


def _choice_text(beat: object, speaker: str | None) -> str:
    text = BEAT_CHOICE.format(beat=beat if isinstance(beat, int) else EMPTY)
    return f"{text} ({speaker})" if speaker else text


def _read(ctx: StoryPageContext, target: LineTarget, back: str) -> LineRead:
    """The one omniscient history read as of the target's turn and what it holds of the line."""
    if target.turn is None:
        return LineRead()
    try:
        history = ctx.client.get_history(
            ctx.story_id,
            viewpoint=cast_helpers.OMNISCIENT,
            as_of=str(target.turn),
            limit=LINE_LIMIT,
        )
    except EngineClientError as exc:
        if is_held(exc) or is_importing(exc):
            raise
        log_engine_error(ctx.request, exc)
        return LineRead()
    items, _ = split_audit(history)
    labels = _labels(ctx)
    if target.event_id:
        matched = [i for i in items if i.message_id == target.event_id]
    else:
        in_turn = [i for i in items if i.turn == target.turn]
        if target.beat is not None:
            matched = [i for i in in_turn if i.beat == target.beat]
        else:
            messages_in_turn = list(dict.fromkeys(i.message_id for i in in_turn))
            if len(messages_in_turn) > 1:
                choices = []
                for message_id in messages_in_turn:
                    first = next(i for i in in_turn if i.message_id == message_id)
                    url = (
                        _page_url(ctx.request, ctx.story_id, message_id, target.turn, back)
                        if is_ulid(message_id)
                        else None
                    )
                    choices.append(
                        Choice(_choice_text(first.beat, narrated_label(first, labels)), url)
                    )
                return LineRead(choices=choices)
            matched = in_turn
    if not matched or any(i.redacted or i.quarantined for i in matched):
        return LineRead()
    first = matched[0]
    variant = first.variant if first.variant is not None else 0
    return LineRead(
        items=[LineItem(_header(i, labels), i.text) for i in matched],
        original="\n".join(normalised_line(i.text) for i in matched),
        fragment=f"#turn-{first.turn}-{variant}",
    )


def _beat_choices(
    request: HttpRequest, story_id: str, details: object, turn: int | None, back: str
) -> list[Choice]:
    """``beat_required``'s ``details.beats``, one choice per entry: "Beat <b> (<speaker kind>)",
    linked to this page by the entry's ``event_id`` (text only when it isn't a ULID)."""
    beats = details.get("beats") if isinstance(details, Mapping) else None
    if not isinstance(beats, list):
        return []
    choices = []
    for entry in beats:
        if not isinstance(entry, Mapping):
            continue
        kind = entry.get("speaker_kind")
        speaker = humanised(kind) if isinstance(kind, str) and kind else None
        event_id = entry.get("event_id")
        url = (
            _page_url(request, story_id, event_id, turn, back)
            if isinstance(event_id, str) and is_ulid(event_id)
            else None
        )
        choices.append(Choice(_choice_text(entry.get("beat"), speaker), url))
    return choices


def _initial(target: LineTarget, read: LineRead, back: str) -> dict[str, Any]:
    return {
        "new_text": read.original or "",
        "event_id": target.event_id or "",
        "turn": "" if target.turn is None else str(target.turn),
        "beat": "" if target.beat is None else str(target.beat),
        "next": back,
        "original": read.original or "",
    }


def _context(
    target: LineTarget, read: LineRead, back: str, form: LineEditForm, key: str
) -> dict[str, Any]:
    return {
        "target": target,
        "read": read,
        "line_unread": LINE_UNREAD if read.original is None and not read.choices else "",
        "shared_note": SHARED_MESSAGE_NOTE if read.shared else "",
        "several_text": SEVERAL_MESSAGES_TEXT if read.choices else "",
        "form": form,
        "idempotency_key": key,
        "back_url": back,
        "back_text": BACK_TEXT,
        "submit_text": PLAN_EDIT,
    }


def _back(ctx: StoryPageContext, fragment: str = "") -> str:
    history = _history_url(ctx.story_id)
    return safe_next(ctx.request, f"{history}{fragment}" if history else "")


def _refused(
    ctx: StoryPageContext,
    form: LineEditForm,
    exc: EngineClientError,
    key: str,
    target: LineTarget,
    back: str,
) -> dict[str, Any]:
    """A refused open's additions (the form carries field errors itself); D8's key."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if not isinstance(exc, EngineError) or exc.code == BAD_RESPONSE:
        return {"write_error": panel_for(exc), "idempotency_key": key}  # the outcome is unknown
    effective = ctx.config.effective if ctx.config is not None else None
    refusal = rewrite_refusal(exc, ctx.story_id, VERB_PHRASE, effective)
    if refusal is not None:
        if refusal.field is not None and not refusal.url:
            form.add_error(NEW_TEXT, refusal.text)  # the form's one text box (planner)
            return {"idempotency_key": new_ulid()}
        added: dict[str, Any] = {
            "refusal": refusal.text,
            "refusal_url": refusal.url,
            "refusal_link": LINKS.get(exc.code, DEFAULT_LINK) if refusal.url else "",
            "idempotency_key": new_ulid(),
        }
        if exc.code == BEAT_REQUIRED:
            added["beat_choices"] = _beat_choices(
                ctx.request, ctx.story_id, exc.details, target.turn, back
            )
        return added
    if exc.code == VALIDATION and apply_engine_errors(
        exc, form, aliases={**ALIASES, "line_edit": NEW_TEXT, "line_edit.new_text": NEW_TEXT}
    ):
        return {"idempotency_key": new_ulid()}
    if exc.http_status >= 500:
        return {"write_error": panel_for(exc), "idempotency_key": key}
    return {"write_error": panel_for(exc), "idempotency_key": new_ulid()}


def _redirect(target: str) -> HttpResponse:
    response = HttpResponseRedirect(target)
    response.status_code = 303
    return response


def _get(ctx: StoryPageContext) -> dict[str, Any]:
    target = line_target(ctx.request.GET)
    if target is None:
        return {"no_line": NO_LINE_TEXT, "back_url": _back(ctx), "back_text": BACK_TEXT}
    read = _read(ctx, target, _back(ctx))
    back = _back(ctx, read.fragment)
    form = LineEditForm(initial=_initial(target, read, back), target=target)
    return _context(target, read, back, form, new_ulid())


def _post(ctx: StoryPageContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    target = line_target(request.POST)
    if target is None:
        return {"no_line": NO_LINE_TEXT, "back_url": _back(ctx), "back_text": BACK_TEXT}
    form = LineEditForm(request.POST, target=target)
    key = posted_idempotency_key(request)
    if not form.is_valid():
        read = _read(ctx, target, _back(ctx))
        return _context(target, read, _back(ctx, read.fragment), form, new_ulid())
    try:
        written = ctx.client.open_rewrite(ctx.story_id, form.body(), idempotency_key=key)
    except EngineClientError as exc:
        read = _read(ctx, target, _back(ctx))
        back = _back(ctx, read.fragment)
        refused = _refused(ctx, form, exc, key, target, back)
        return {**_context(target, read, back, form, key), **refused}
    flash = session_write_flash(
        written,
        ctx.story_id,
        key,
        apply_directly=bool(form.cleaned_data.get("apply_directly")),
        verb=OPENED_VERB,
    )
    messages.success(request, flash, extra_tags="safe")
    session = rewrite_session_url(ctx.story_id, written.id) or rewrites_url(ctx.story_id)
    return _redirect(session or request.path)


@require_http_methods(["GET", "HEAD", "POST"])
def line_edit(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:line-edit``: plan a one-line edit of the recorded transcript (#305)."""

    def build(ctx: StoryPageContext) -> dict[str, Any] | HttpResponse:
        return _post(ctx) if request.method == "POST" else _get(ctx)

    return story_page(request, story_id, TEMPLATE, build, current=CURRENT, label=LABEL)

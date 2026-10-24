"""The play view (#48): play a Lite story turn by turn, side by side with each turn's report.

``/stories/<id>/`` shows the story header and two columns: on the left the transcript (the last
50 history items at the chosen viewpoint) and the turn form, on the right the inspector. The
form posts with HTMX to :func:`turn`, which calls ``respond`` with ``Prefer: respond-async`` and
the render's idempotency key, and answers with the inspector's new content plus out-of-band
swaps of the transcript (re-fetched from ``GET /history``, never appended client-side), the form
(a fresh key) and the waiting area. A queued turn (202) gets a waiting card that polls
:func:`poll` every second until the operation is ``committed``, ``failed`` or ``abandoned``.

The story's cast, default viewpoint and turn deadline travel in a signed hidden ``snapshot``
(#45's pattern), so the form re-renders, and refuses bad input, without an Engine read. Earlier
turns' reports come from the operation log (#44).

Endpoints: ``POST <id>/turn/``, ``GET <id>/operations/<op>/poll/``, ``GET <id>/inspector/``
(``?operation=``) and ``GET <id>/transcript/`` (``?viewpoint=``); each answers a full page
to a request without HTMX.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from django.core import signing
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from console import cast as cast_helpers
from console.cast import CastMember, character_of, viewpoint_for
from console.confirm import confirm_query, posted_idempotency_key
from console.engine.client import (
    DEFAULT_TURN_DEADLINE_S,
    EngineClient,
    EngineClientError,
    EngineError,
    EngineUnreachable,
    turn_timeout,
)
from console.engine.models import (
    History,
    HistoryItem,
    OperationRecord,
    QueuedTurn,
    RespondResponse,
    Story,
    TurnReport,
)
from console.engine_access import complete_queued, engine_client_for
from console.engine_errors import ErrorPanel, is_htmx, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.play import DEADLINE_MAX, ERROR_PATHS, STORY_DEFAULT, TurnForm
from console.ids import new_ulid
from console.models import EngineOperation
from console.operation_log import input_violations, turn_report_json
from console.turn_reports import ViolationRow, stored_turn_report, violation_rows

TRANSCRIPT_LIMIT = 50
POLL_GRACE_S = 30.0  # polling stops this long after the turn's deadline (planner)
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.play.snapshot"
PENDING_KEY = "console.play.pending"  # session: engine operation id -> the posted form
REPORT_SCAN = 50  # log rows looked at for the first-load inspector
TERMINAL = frozenset({"committed", "failed", "abandoned"})
ARCHIVED_NOTE = "Archived — unarchive to play"
EMPTY = "—"
# Errors whose input stays in the form and whose details belong beside a field.
PLACED_CODES = frozenset(
    {"validation_error", "viewpoint_unresolved", "budget_below_safety_minimum"}
)
FORM_ALIASES = {**ERROR_PATHS, "minimum_required": "max_calls"}


# --- The story as the play view carries it ------------------------------------------------------


@dataclass(frozen=True)
class PlayStory:
    """What the form needs from ``GET /stories/{id}`` (and the turn deadline from its
    settings), signed into the page so a POST needs no read."""

    id: str
    name: str | None
    status: str
    cast: tuple[CastMember, ...]
    default_viewpoint: str | None
    turn_deadline: float | None  # effective_budget.turn_deadline, when the read succeeded

    @classmethod
    def of(cls, story: Story, turn_deadline: float | None) -> PlayStory:
        default = story.settings.get("default_output_viewpoint")
        return cls(
            story.id,
            story.name,
            story.status,
            tuple(cast_helpers.members(story.cast)),
            default if isinstance(default, str) and default else None,
            turn_deadline,
        )

    def signed(self) -> str:
        return signing.dumps(
            {
                "id": self.id,
                "name": self.name,
                "status": self.status,
                "cast": [[c.id, c.name, c.is_player] for c in self.cast],
                "default_viewpoint": self.default_viewpoint,
                "turn_deadline": self.turn_deadline,
            },
            salt=SNAPSHOT_SALT,
            compress=True,
        )

    @classmethod
    def unsigned(cls, value: str | None, story_id: str) -> PlayStory | None:
        """The snapshot, or ``None`` when missing, tampered, malformed or for another story."""
        if not value:
            return None
        try:
            raw = signing.loads(value, salt=SNAPSHOT_SALT)
            members = tuple(CastMember(str(i), str(n), bool(p)) for i, n, p in raw["cast"])
            deadline = raw["turn_deadline"]
            found = cls(
                str(raw["id"]),
                raw["name"],
                str(raw["status"]),
                members,
                raw["default_viewpoint"],
                float(deadline) if deadline is not None else None,
            )
        except signing.BadSignature, KeyError, TypeError, ValueError:
            return None
        return found if found.id == story_id else None

    @property
    def archived(self) -> bool:
        return self.status == "archived"

    def default_form_viewpoint(self) -> str:
        """The viewpoint select's first choice: the story default, else the default actor."""
        if self.default_viewpoint:
            return STORY_DEFAULT
        actor = cast_helpers.default_actor(self.cast)
        return viewpoint_for(actor) if actor else ""

    def resolve(self, form_viewpoint: str | None) -> str | None:
        """The viewpoint a form value stands for (``default`` → the story's)."""
        if not form_viewpoint or form_viewpoint == STORY_DEFAULT:
            if self.default_viewpoint:
                return self.default_viewpoint
            actor = cast_helpers.default_actor(self.cast)
            return viewpoint_for(actor) if actor else None
        return form_viewpoint


def _turn_deadline(client: EngineClient, request: HttpRequest, story_id: str) -> float | None:
    """``effective_budget.turn_deadline``; a failed read degrades to the 45 s default."""
    try:
        return client.get_story_settings(story_id).effective_budget.turn_deadline
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


def _read_story(request: HttpRequest, story_id: str) -> tuple[Story, PlayStory]:
    client = engine_client_for(request)
    story = client.get_story(story_id)
    return story, PlayStory.of(story, _turn_deadline(client, request, story_id))


def _snapshot(request: HttpRequest, story_id: str, value: str | None) -> PlayStory:
    found = PlayStory.unsigned(value, story_id)
    return found if found is not None else _read_story(request, story_id)[1]


def _form(play: PlayStory, data: Any = None, *, initial: dict[str, Any] | None = None) -> TurnForm:
    return TurnForm(
        data,
        cast=play.cast,
        default_viewpoint=play.default_viewpoint,
        initial=initial if initial is not None else _initial(play),
    )


def _initial(play: PlayStory, viewpoint: str | None = None) -> dict[str, Any]:
    actor = cast_helpers.default_actor(play.cast)
    chosen = play.default_form_viewpoint()
    if viewpoint and character_of(viewpoint) in {c.id for c in play.cast}:
        chosen = viewpoint
    return {"actor": actor, "output_viewpoint": chosen}


@dataclass(frozen=True)
class FormView:
    """The turn form as rendered: a fresh key per render, disabled while archived or waiting."""

    story_id: str
    form: TurnForm
    snapshot: str
    idempotency_key: str
    disabled: bool = False
    note: str = ""
    # The browser's wait for a turn POST: the longest deadline a request may ask for, plus the
    # Console's 15 s and a margin, so the Console's own timeout always answers first.
    timeout_s: int = int(turn_timeout(DEADLINE_MAX)) + 30


def _form_view(play: PlayStory, form: TurnForm, *, waiting: bool = False) -> FormView:
    if play.archived:
        return FormView(play.id, form, play.signed(), new_ulid(), True, ARCHIVED_NOTE)
    note = "Waiting for the queued turn to finish." if waiting else ""
    return FormView(play.id, form, play.signed(), new_ulid(), waiting, note)


# --- Transcript ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    item: HistoryItem
    speaker: str


@dataclass(frozen=True)
class Transcript:
    story_id: str
    viewpoint: str | None
    viewpoint_label: str
    entries: tuple[Entry, ...] = ()
    older_before_turn: int | None = None
    error: ErrorPanel | None = None


def _speaker(item: HistoryItem, labels: dict[str, str]) -> str:
    if item.actor_character_id:
        return labels.get(item.actor_character_id, item.actor_character_id)
    if item.channel == "message" and item.render_character_ids:
        return ", ".join(labels.get(c, c) for c in item.render_character_ids)
    return ""


def _transcript(
    request: HttpRequest, story_id: str, viewpoint: str | None, cast: tuple[CastMember, ...]
) -> Transcript:
    """The last 50 items at ``viewpoint`` (oldest first); a failed read degrades this section."""
    label = cast_helpers.viewpoint_label(viewpoint, cast)
    try:
        history: History = engine_client_for(request).get_history(
            story_id, viewpoint=viewpoint, limit=TRANSCRIPT_LIMIT
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return Transcript(story_id, viewpoint, label, error=panel_for(exc))
    labels = cast_helpers.character_labels(cast)
    entries = tuple(Entry(item, _speaker(item, labels)) for item in history.entries)
    return Transcript(story_id, viewpoint, label, entries, history.next_before_turn)


# --- Inspector ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Inspector:
    """What the inspector shows for the selected turn or the last outcome."""

    title: str = ""
    notice: str = ""
    report: TurnReport | None = None
    reply: str = ""
    input_violations: tuple[ViolationRow, ...] = ()
    facts: tuple[tuple[str, str], ...] = ()
    error: ErrorPanel | None = None
    error_line: str = ""  # "HTTP 423 story_busy: …" for errors worded specially
    timeline_revision: int | None = None
    config_revision: int | None = None
    idempotency_key: str = ""
    operation_id: str = ""
    log_pk: int | None = None  # the operation-log row, linked
    revisions: bool = True  # show the revision/key lines


NOTHING_SELECTED = Inspector(notice="Select a turn to see its report.", revisions=False)
NOT_PLAYED_HERE = "Report not available — this turn was not played from this Console"
INGESTED = "No report — this turn was ingested, not generated"
ERASED = "This turn refers to an erased subject — nothing was written"
NOT_SENT = "The turn was not sent — correct the form."


def _report(value: object) -> TurnReport | None:
    found = turn_report_json(value)
    return TurnReport.from_json(found) if found is not None else None


def _detail(details: dict[str, Any], key: str) -> str:
    value = details.get(key)
    return EMPTY if value is None else str(value)


def error_inspector(exc: EngineClientError, idempotency_key: str) -> Inspector:
    """The inspector for a refused or failed turn (#48's wording per error code)."""
    if not isinstance(exc, EngineError):
        return Inspector(
            title="Turn not played", error=panel_for(exc), idempotency_key=idempotency_key
        )
    details = exc.details
    line = f"HTTP {exc.http_status} {exc.code}: {exc.message}"
    base: dict[str, Any] = {
        "title": "Turn refused",
        "idempotency_key": idempotency_key,
        "error_line": line,
    }
    retry = (("Retry after", f"{exc.retry_after} s"),) if exc.retry_after else ()
    if exc.code == "story_busy":
        reason = details.get("reason")
        if reason == "queue_full":
            depth = details.get("queue_depth")
            notice = f"Story busy — queue full ({EMPTY if depth is None else depth} waiting)"
        elif reason == "in_flight":
            notice = "Story busy — another turn is running"
        else:
            notice = "Story busy"
        facts = (("Operation", _detail(details, "operation_id")), *retry)
        return Inspector(notice=notice, facts=facts, **base)
    if exc.code == "guardrail_violation":
        return Inspector(
            notice="Input rejected by a guardrail — no reply was added.",
            input_violations=tuple(
                violation_rows(input_violations({"error": {"details": details}}))
            ),
            report=_report({"error": {"details": details}}),
            **base,
        )
    if exc.code == "budget_below_safety_minimum":
        facts = (
            ("Minimum required", _detail(details, "minimum_required")),
            ("Max calls asked", _detail(details, "max_calls")),
        )
        return Inspector(
            notice="Budget below the safety minimum — no model was called.", facts=facts, **base
        )
    if exc.code == "model_unavailable":
        facts = (
            ("Purpose", _detail(details, "purpose")),
            ("Reason", _detail(details, "reason")),
            ("Provider status", _detail(details, "provider_status")),
            ("Attempts", _detail(details, "attempts")),
            *retry,
        )
        return Inspector(
            notice="Model unavailable — nothing was written.",
            facts=facts,
            report=_report({"error": {"details": details}}),
            **base,
        )
    if exc.code == "erased_subject_referenced":
        facts = (
            ("Erasure", _detail(details, "erasure_id")),
            ("Matches", _detail(details, "match_count")),
        )
        return Inspector(notice=ERASED, facts=facts, **base)
    return Inspector(title="Turn refused", error=panel_for(exc), idempotency_key=idempotency_key)


def result_inspector(result: RespondResponse, idempotency_key: str, operation_id: str) -> Inspector:
    return Inspector(
        title=f"Turn {result.turn} (variant {result.variant})",
        report=result.report,
        reply=result.message.text,
        timeline_revision=result.timeline_revision,
        config_revision=result.config_revision,
        idempotency_key=result.idempotency_key or idempotency_key,
        operation_id=operation_id,
        log_pk=_log_pk(operation_id),
    )


def _log_pk(operation_id: str) -> int | None:
    if not operation_id:
        return None
    row = EngineOperation.objects.filter(engine_operation_id=operation_id).first()
    return row.pk if row is not None else None


def row_inspector(row: EngineOperation) -> Inspector:
    """The inspector for a turn the operation log holds (#44)."""
    report = stored_turn_report(row.response)
    turn = f"Turn {report.turn} (variant {report.variant})" if report and report.turn else "Turn"
    violations = tuple(violation_rows(input_violations(row.response)))
    notice = "Input rejected by a guardrail — no reply was added." if violations else ""
    return Inspector(
        title=turn if not violations else "Rejected input",
        notice=notice,
        report=report,
        input_violations=violations,
        error_line=f"HTTP {row.http_status} {row.error_code}" if row.error_code else "",
        timeline_revision=row.timeline_revision,
        config_revision=row.config_revision,
        idempotency_key=row.idempotency_key,
        operation_id=row.engine_operation_id,
        log_pk=row.pk,
    )


def _latest_inspector(story_id: str) -> Inspector:
    """First load: the newest logged turn of this story with a report, else "select a turn"."""
    rows = EngineOperation.objects.filter(story_id=story_id, response__isnull=False)
    for row in rows[:REPORT_SCAN]:
        if turn_report_json(row.response) is not None:
            return row_inspector(row)
    return NOTHING_SELECTED


def selected_inspector(operation_id: str, origin: str = "") -> Inspector:
    """The inspector for a clicked history item, by the operation that recorded it."""
    rows = list(EngineOperation.objects.filter(engine_operation_id=operation_id)[:REPORT_SCAN])
    for row in rows:
        if turn_report_json(row.response) is not None:
            return row_inspector(row)
    ingested = origin == "ingested" or any(row.kind.startswith("ingest") for row in rows)
    return Inspector(
        title=f"Operation {operation_id}",
        notice=INGESTED if ingested else NOT_PLAYED_HERE,
        operation_id=operation_id,
        revisions=False,
    )


# --- Waiting cards ------------------------------------------------------------------------------


@dataclass(frozen=True)
class WaitingCard:
    story_id: str
    operation_id: str
    state: str  # queued | running | unreachable
    position: int | None
    viewpoint: str
    deadline_s: float
    started_at: str = ""

    @property
    def text(self) -> str:
        if self.state == "queued":
            where = EMPTY if self.position is None else self.position
            return f"Waiting — queued, position {where}"
        if self.state == "unreachable":
            return "Engine unreachable — retrying"
        return "Running…"

    @property
    def poll_url(self) -> str:
        query = {"viewpoint": self.viewpoint, "deadline": f"{self.deadline_s:g}"}
        if self.position is not None:
            query["position"] = str(self.position)
        if self.started_at:
            query["started"] = self.started_at
        path = reverse("stories:poll", args=[self.story_id, self.operation_id])
        return f"{path}?{urlencode(query)}"


def _body(row: EngineOperation) -> dict[str, Any]:
    body = row.request.get("body") if isinstance(row.request, dict) else None
    return body if isinstance(body, dict) else {}


def _row_deadline(row: EngineOperation, play: PlayStory) -> float:
    budget = _body(row).get("budget")
    asked = budget.get("deadline_s") if isinstance(budget, dict) else None
    if isinstance(asked, int | float) and not isinstance(asked, bool):
        return float(asked)
    return play.turn_deadline if play.turn_deadline is not None else DEFAULT_TURN_DEADLINE_S


def _restored_cards(play: PlayStory) -> list[WaitingCard]:
    """Queued turns of this story still inside their deadline + 30 s, newest first."""
    now = timezone.now()
    cards = []
    for row in EngineOperation.objects.filter(
        story_id=play.id, outcome=EngineOperation.Outcome.QUEUED
    ).exclude(engine_operation_id=""):
        deadline = _row_deadline(row, play)
        if row.created_at + timedelta(seconds=deadline + POLL_GRACE_S) < now:
            continue
        queued = QueuedTurn.from_json(row.response)
        viewpoint = _body(row).get("output_viewpoint")
        cards.append(
            WaitingCard(
                play.id,
                row.engine_operation_id,
                "queued",
                queued.position if queued else None,
                viewpoint if isinstance(viewpoint, str) else "",
                deadline,
            )
        )
    return cards


# --- Pages and partials ---------------------------------------------------------------------------


@dataclass
class PlayPage:
    story: Story
    play: PlayStory
    form_view: FormView
    transcript: Transcript
    inspector: Inspector
    cards: list[WaitingCard] = field(default_factory=list)
    status_note: str = ""

    def context(self, request: HttpRequest) -> dict[str, Any]:
        return {
            "story": self.story,
            "cast": self.story.cast,
            "actions_query": confirm_query(
                self.story.name, reverse("stories:detail", args=[self.story.id])
            ),
            "fv": self.form_view,
            "transcript": self.transcript,
            "inspector": self.inspector,
            "cards": self.cards,
            "status_note": self.status_note,
        }


def _page(
    request: HttpRequest,
    story_id: str,
    *,
    form: TurnForm | None = None,
    inspector: Inspector | None = None,
    cards: list[WaitingCard] | None = None,
    status_note: str = "",
    viewpoint: str | None = None,
) -> HttpResponse:
    story, play = _read_story(request, story_id)
    restored = _restored_cards(play) if cards is None else cards
    if form is None:
        form = _form(play, initial=_initial(play, viewpoint))
    chosen = viewpoint or play.resolve(
        form["output_viewpoint"].value() if form.is_bound else form.initial.get("output_viewpoint")
    )
    page = PlayPage(
        story,
        play,
        _form_view(play, form, waiting=bool(restored)),
        _transcript(request, story_id, chosen, play.cast),
        inspector if inspector is not None else _latest_inspector(story_id),
        restored,
        status_note,
    )
    return render(request, "console/play/play.html", page.context(request))


@require_safe
def play(request: HttpRequest, story_id: str) -> HttpResponse:
    """The play view. ``?viewpoint=`` picks the transcript's viewpoint, ``?operation=`` the
    inspector's turn (the no-JavaScript forms of the HTMX controls)."""
    operation = request.GET.get("operation", "")
    inspector = selected_inspector(operation, request.GET.get("origin", "")) if operation else None
    return _page(
        request, story_id, inspector=inspector, viewpoint=request.GET.get("viewpoint") or None
    )


@require_safe
def transcript(request: HttpRequest, story_id: str) -> HttpResponse:
    """The transcript partial at ``?viewpoint=`` (or the viewpoint select's ``output_viewpoint``,
    ``default`` resolved through the snapshot)."""
    viewpoint = request.GET.get("viewpoint") or None
    if not is_htmx(request):
        return _page(request, story_id, viewpoint=viewpoint)
    play_story = _snapshot(request, story_id, request.GET.get(SNAPSHOT_FIELD))
    if viewpoint is None:
        viewpoint = play_story.resolve(request.GET.get("output_viewpoint"))
    found = _transcript(request, story_id, viewpoint, play_story.cast)
    return render(request, "console/play/_transcript.html", {"transcript": found})


@require_safe
def inspector(request: HttpRequest, story_id: str) -> HttpResponse:
    """The inspector for one turn, by ``?operation=`` (the item's ``operation_id``)."""
    operation = request.GET.get("operation", "")
    origin = request.GET.get("origin", "")
    found = selected_inspector(operation, origin) if operation else NOTHING_SELECTED
    if not is_htmx(request):
        return _page(request, story_id, inspector=found)
    return render(request, "console/play/_inspector_body.html", {"inspector": found})


def _posted(data: QueryDict) -> dict[str, list[str]]:
    return {k: data.getlist(k) for k in data if k != "csrfmiddlewaretoken"}


def _query_dict(posted: dict[str, list[str]]) -> QueryDict:
    data = QueryDict(mutable=True)
    for key, values in posted.items():
        data.setlist(key, values)
    return data


@require_POST
def turn(request: HttpRequest, story_id: str) -> HttpResponse:
    """Send one turn. Answers the inspector (plus out-of-band transcript, form and waiting
    area) to HTMX, the whole page otherwise."""
    play_story = _snapshot(request, story_id, request.POST.get(SNAPSHOT_FIELD))
    form = _form(play_story, request.POST)
    key = posted_idempotency_key(request)
    if not form.is_valid():
        return _turn_answer(request, play_story, form, Inspector(notice=NOT_SENT, revisions=False))
    deadline = form.cleaned_data["deadline_s"]
    if deadline is None:
        deadline = play_story.turn_deadline
    try:
        result = engine_client_for(request).respond(
            story_id, form.body(), idempotency_key=key, timeout=turn_timeout(deadline)
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.code in PLACED_CODES:
            apply_engine_errors(exc, form, aliases=FORM_ALIASES)
        return _turn_answer(request, play_story, form, error_inspector(exc, key))
    if isinstance(result, QueuedTurn):
        pending = request.session.get(PENDING_KEY, {})
        pending[result.operation_id] = _posted(request.POST)
        request.session[PENDING_KEY] = pending
        card = WaitingCard(
            story_id,
            result.operation_id,
            "queued",
            result.position,
            form.resolved_viewpoint(),
            deadline if deadline is not None else DEFAULT_TURN_DEADLINE_S,
        )
        waiting = Inspector(
            title="Turn queued",
            notice="Its report appears here when it finishes.",
            idempotency_key=key,
            operation_id=result.operation_id,
            log_pk=_log_pk(result.operation_id),
            revisions=False,
        )
        kept = _form(play_story, initial=form.kept_values(clear_input=False))
        return _turn_answer(request, play_story, kept, waiting, cards=[card])
    operation_id = result.entries[0].operation_id if result.entries else ""
    fresh = _form(play_story, initial=form.kept_values(clear_input=True))
    return _turn_answer(
        request,
        play_story,
        fresh,
        result_inspector(result, key, operation_id),
        viewpoint=result.output_viewpoint,
    )


def _turn_answer(
    request: HttpRequest,
    play_story: PlayStory,
    form: TurnForm,
    found: Inspector,
    *,
    cards: list[WaitingCard] | None = None,
    viewpoint: str | None = None,
) -> HttpResponse:
    """The answer to a turn POST. ``viewpoint`` set means the turn finished: the transcript is
    re-fetched at it."""
    if not is_htmx(request):
        return _page(
            request,
            play_story.id,
            form=form,
            inspector=found,
            cards=cards or [],
            viewpoint=viewpoint,
        )
    context: dict[str, Any] = {
        "inspector": found,
        "fv": _form_view(play_story, form, waiting=bool(cards)),
        "cards": cards or [],
        "status_note": "",
        "transcript": (
            _transcript(request, play_story.id, viewpoint, play_story.cast) if viewpoint else None
        ),
    }
    return render(request, "console/play/_turn_response.html", context)


# --- Polling a queued turn ----------------------------------------------------------------------


def _float(value: str | None, fallback: float) -> float:
    try:
        parsed = float(value) if value else fallback
    except ValueError:
        return fallback
    return parsed if parsed > 0 else fallback


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _past_cutoff(started_at: str, deadline_s: float) -> bool:
    if not started_at:
        return False
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return False
    if started.tzinfo is None:
        return False
    return timezone.now() > started + timedelta(seconds=deadline_s + POLL_GRACE_S)


def _pending_form(
    request: HttpRequest, story_id: str, operation_id: str, *, clear_input: bool
) -> tuple[PlayStory, TurnForm]:
    """The form to show once polling ends: the posted values (say/do cleared after a
    committed turn), or a fresh form when this session did not send the turn."""
    pending = request.session.get(PENDING_KEY, {})
    posted = pending.pop(operation_id, None)
    request.session[PENDING_KEY] = pending
    if posted is None:
        play_story = _read_story(request, story_id)[1]
        return play_story, _form(play_story)
    data = _query_dict(posted)
    play_story = _snapshot(request, story_id, data.get(SNAPSHOT_FIELD))
    bound = _form(play_story, data)
    if bound.is_valid():
        return play_story, _form(play_story, initial=bound.kept_values(clear_input=clear_input))
    return play_story, bound


def _stored_error(op: OperationRecord) -> EngineError | None:
    """The error a failed operation stored, as the Engine would have answered it."""
    if op.result is None or op.result.status < 400 or not isinstance(op.result.body, dict):
        return None
    error = op.result.body.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        return None
    details = error.get("details")
    return EngineError(
        error["code"],
        str(error.get("message", "")),
        details if isinstance(details, dict) else {},
        op.result.status,
    )


@require_safe
def poll(request: HttpRequest, story_id: str, operation_id: str) -> HttpResponse:
    """One poll of a queued turn's operation (never recorded). Until it is terminal the answer
    is the waiting card again (it polls once more after 1 s); then it is a closing line, with
    the inspector, transcript and form swapped out-of-band."""
    viewpoint = request.GET.get("viewpoint", "")
    deadline_s = _float(request.GET.get("deadline"), DEFAULT_TURN_DEADLINE_S)
    card = WaitingCard(
        story_id,
        operation_id,
        "queued",
        _int(request.GET.get("position")),
        viewpoint,
        deadline_s,
        request.GET.get("started", ""),
    )
    try:
        op = engine_client_for(request).get_operation(operation_id, viewpoint=viewpoint or None)
    except EngineUnreachable as exc:
        log_engine_error(request, exc)
        if _past_cutoff(card.started_at, deadline_s):
            return _poll_end(request, card, None, "Still running — refresh to check", None)
        return _card(request, replace(card, state="unreachable"))
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _poll_end(
            request, card, error_inspector(exc, ""), "Polling stopped — see the inspector.", None
        )
    card = replace(card, started_at=op.started_at)
    if op.status not in TERMINAL:
        if op.status == "queued":
            return _card(request, replace(card, state="queued"))
        if _past_cutoff(op.started_at, deadline_s):
            return _poll_end(request, card, None, "Still running — refresh to check", None)
        return _card(request, replace(card, state="running"))
    complete_queued(operation_id, op)
    key = op.idempotency_key or ""
    if op.status == "committed":
        result = RespondResponse.from_json(op.result.body) if op.result else None
        if result is not None:
            found = result_inspector(result, key, operation_id)
            note = f"Turn {result.turn} committed."
            return _poll_end(request, card, found, note, result.output_viewpoint)
        stored = _stored_error(op)
        if stored is None:
            found = Inspector(
                title="Turn committed",
                notice=(
                    "Turn committed — result not available from the Engine (outside the 24 h "
                    "window or another viewpoint)"
                ),
                timeline_revision=op.timeline_revision,
                idempotency_key=key,
                operation_id=operation_id,
                log_pk=_log_pk(operation_id),
            )
            return _poll_end(request, card, found, "Turn committed.", viewpoint or None)
        return _poll_end(request, card, error_inspector(stored, key), "Turn refused.", None)
    stored = _stored_error(op)
    if stored is not None:
        found = error_inspector(stored, key)
    else:
        found = Inspector(
            title=f"Turn {op.status}",
            error=ErrorPanel(f"Turn {op.status}", code=op.status, message=f"operation {op.status}"),
            idempotency_key=key,
            operation_id=operation_id,
        )
    found = replace(found, operation_id=operation_id, log_pk=_log_pk(operation_id))
    return _poll_end(request, card, found, f"Turn {op.status} — see the inspector.", None)


def _card(request: HttpRequest, card: WaitingCard) -> HttpResponse:
    return render(request, "console/play/_waiting_card.html", {"card": card})


def _poll_end(
    request: HttpRequest,
    card: WaitingCard,
    found: Inspector | None,
    note: str,
    viewpoint: str | None,
) -> HttpResponse:
    """Polling is over: a closing line in the card's place, and out-of-band the inspector (when
    there is something new to show), the transcript (after a committed turn) and the form."""
    committed = viewpoint is not None
    play_story, form = _pending_form(
        request, card.story_id, card.operation_id, clear_input=committed
    )
    if not is_htmx(request):
        return _page(request, card.story_id, form=form, inspector=found, cards=[], status_note=note)
    context: dict[str, Any] = {
        "card": card,
        "note": note,
        "inspector": found,
        "fv": _form_view(play_story, form),
        "transcript": (
            _transcript(request, card.story_id, viewpoint, play_story.cast) if committed else None
        ),
    }
    return render(request, "console/play/_poll_response.html", context)

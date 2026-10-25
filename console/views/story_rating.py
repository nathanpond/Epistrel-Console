"""Change a story's content rating (#80): relax at once, tighten after a live history check.

``/stories/<id>/rating/`` shows the story's rating and a select of the non-retired ratings, each
labelled by what choosing it does. The label compares effective guardrails, as the Engine does:
the target's policy set within the current one relaxes ("applies at once"), anything else
tightens ("checks history first"); a rating whose detail read failed is "unknown — treated as
tighten". The label is advisory: the Engine's 200 or 202 decides what happens.

The page makes one ``GET /admin/content-ratings`` (non-retired), then the detail reads of the
current rating and each listed one on #41's pool. An archived or paused story skips the detail
reads and shows the form disabled.

A POST recomputes the direction from fresh reads (a forged "relax" cannot skip the confirm):
a relax PUTs at once; a tighten (or unknown) redirects to ``rating/confirm/?t=<signed>`` (#64
convention 3, kind ``story_rating``), whose GET makes no call and whose POST makes the PUT after
one story read binds the rating the confirm was computed against. Choosing the current rating
makes no call ("No change").

A 202 goes to ``rating/checks/<job_id>/``: the check's status and progress, polled every 2 s
(:mod:`console.job_poll`) until it ends ``applied``, ``refused`` (violations by position and
policy, never their text) or ``failed``. One URL serves the full page, its poll partial
(``HX-Request``) and the pause banner's partial (``HX-Request`` + ``?banner=1``, which answers
``HX-Refresh: true`` once the check ends). The first terminal read settles the PUT's ``queued``
operation-log row (#44) with the full check body.

The same page follows a reactivation re-check (#81, ``kind: reactivation``): undo rewind, swap or
fork held back by a 202 redirect here with ``?op=``. It polls only while ``checking`` and shows
the ending in ``_reactivation_status.html``: the edit's answer parsed from ``outcome`` (#53's
result fields, the transcript at the restored head, or the new fork), the violations, or the
error; the #44 row of the 202 takes the reactivated edit's operation id and revision.
"""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_http_methods, require_safe

from console import twostage
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import (
    ChainRef,
    CheckViolation,
    ContentRating,
    ContentRatingSummary,
    ForkResult,
    RatingCheck,
    RatingCheckStarted,
    Story,
    SwapResult,
    UntruncateResult,
)
from console.engine.pool import fan_out
from console.engine_access import complete_job, engine_client_for
from console.engine_errors import ErrorPanel, is_htmx, log_engine_error, panel_for
from console.ids import new_ulid
from console.job_poll import poll_context, poll_failure
from console.models import EngineOperation
from console.reactivation import (
    CHECKING,
    FAILED_LINE,
    FORK,
    NO_FORK,
    OP_PARAM,
    REACTIVATED,
    UNCHANGED,
    UNEXPECTED_STATUS,
    UNREADABLE,
    WAITING_LINE,
    Outcome,
    heading,
    is_reactivation,
    job_overrides,
    job_row,
    parse_outcome,
    raw_json,
    requested_fork_name,
    shown_op,
    transcript_url,
    valid_op,
)
from console.story_pause import (
    BANNER_PARAM,
    CAUSE_PARAM,
    RATING_CHECK,
    RATING_FORM_NOTE,
    PauseBanner,
    check_url,
    is_paused,
    pause_banner,
    progress_text,
)
from console.timeline import chain_label
from console.views.play import FORK_BANNER_KEY, ForkBanner

KIND = "story_rating"  # the two-stage confirm's kind (salt console.twostage.story_rating)
CONFIRM_QUERY = "t"
RATING_FIELD = "rating"
CURRENT_FIELD = "current"  # the rating the page showed as current (choosing it: no call)
TERMINAL = frozenset({"applied", "refused", "failed", "reactivated"})
OK_STATUSES = frozenset({"applied", "reactivated"})
REFUSED_CODE = "rating_refused"

RELAXES = "relaxes — applies at once"
TIGHTENS = "tightens — checks history first"
UNKNOWN = "unknown — treated as tighten"
CURRENT = "current — no change"
RETIRED_SUFFIX = " (retired)"
NO_CHANGE = "No change"
CHOOSE = "Choose a rating from the list."
RETIRED_TEXT = "That rating was retired — choose another."
ALREADY_RUNNING = "A rating check is already running"
ARCHIVED_NOTE = "Archived — unarchive to change its rating"
CURRENT_UNREAD = "Couldn't read the current rating's guardrails — every change will be checked."
CANT_COMPARE = "Couldn't compare guardrails."
CONFIRM_LINE = (
    "The story is paused while every past turn, ingested event, pinned memory and custom "
    "instruction is checked against {name}. Turns are refused until it finishes."
)
CONFIRM_LABEL = "Change rating"
NOT_FOUND = "No such check, or it expired (kept 30 days)"
NOT_CHANGED = "The rating was not changed"
NAMES_UNAVAILABLE = "Rating names unavailable"
STARTED = "Rating check started"
# #81: a reactivation re-check's 404 (its page was opened from an undo, swap or fork).
REACTIVATION_NOT_FOUND = (
    "This check no longer exists (checks are kept 30 days) or isn't one of this story's"
)

Direction = Literal["relax", "tighten", "unknown", "current"]
DIRECTION_TEXT: dict[str, str] = {
    "relax": RELAXES,
    "tighten": TIGHTENS,
    "unknown": UNKNOWN,
    "current": CURRENT,
}


def _rating_url(story_id: str) -> str:
    return reverse("stories:rating", args=[story_id])


# --- relax or tighten -----------------------------------------------------------------------------


def guardrail_set(rating: ContentRating) -> frozenset[str]:
    """A rating's live policy set: its ``effective_guardrails`` policy ids (#49)."""
    return frozenset(g.policy_id for g in rating.effective_guardrails)


def direction(current: frozenset[str] | None, target: frozenset[str] | None) -> Direction:
    """What changing from ``current`` to ``target`` does, as the Engine decides it: a target
    within the current set (an equal one too) relaxes, any other tightens (sideways included).
    Either set unread (``None``): unknown, treated as a tighten."""
    if current is None or target is None:
        return "unknown"
    return "relax" if target <= current else "tighten"


@dataclass(frozen=True)
class Option:
    """One rating in the select. ``direction`` is ``None`` when the page skipped the detail
    reads (archived or paused); ``adds``/``drops`` are the policy ids a change adds and drops."""

    id: str
    name: str
    version_id: str
    version: int | None
    direction: Direction | None
    adds: tuple[str, ...] = ()
    drops: tuple[str, ...] = ()
    retired: bool = False

    @property
    def label(self) -> str:
        name = f"{self.name}{RETIRED_SUFFIX}" if self.retired else self.name
        shown = f"{name} (v{self.version})" if self.version is not None else name
        if self.direction is None:
            return shown
        return f"{shown} — {DIRECTION_TEXT[self.direction]}"


@dataclass(frozen=True)
class CurrentRating:
    """The story's rating: its id, name and version id, and the integer version when the
    version id is the rating's latest (else the page shows the id)."""

    id: str
    name: str
    version_id: str
    version: int | None
    retired: bool


@dataclass
class Reads:
    """What the rating page read: the story, the select's options (``list_error`` when the
    list read failed) and whether the current rating's guardrails were read."""

    story: Story
    current: CurrentRating
    options: list[Option] = field(default_factory=list)
    list_error: ErrorPanel | None = None
    current_unread: bool = False

    @property
    def blocked(self) -> bool:
        return self.story.status == "archived" or is_paused(self.story)

    def option(self, rating_id: str) -> Option | None:
        """A listed rating other than the current one, labelled (so it can be chosen)."""
        for option in self.options:
            if option.id == rating_id and option.direction not in (None, "current"):
                return option
        return None


def _version(version_id: str, *items: ContentRatingSummary | None) -> int | None:
    for item in items:
        if item is not None and item.version_id == version_id:
            return item.version
    return None


def read_ratings(request: HttpRequest, client: EngineClient, story: Story) -> Reads:
    """The rating page's reads after the story's: the non-retired list, then (unless the story
    is archived or paused) every listed rating's and the current one's detail, on the pool."""
    rating = story.content_rating
    current = CurrentRating(rating.id, rating.name, rating.version_id, None, False)
    try:
        listed = client.list_content_ratings()
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return Reads(story, current, list_error=panel_for(exc))
    reads = Reads(story, current)
    details: Mapping[str, ContentRating | EngineClientError] = {}
    if not reads.blocked:
        details = fan_out(client.get_content_rating, [rating.id, *(r.id for r in listed)])
    found = details.get(rating.id)
    current_detail = found if isinstance(found, ContentRating) else None
    current_listed = next((r for r in listed if r.id == rating.id), None)
    current = CurrentRating(
        rating.id,
        rating.name,
        rating.version_id,
        _version(rating.version_id, current_listed, current_detail),
        current_listed is None,
    )
    reads.current = current
    current_set = guardrail_set(current_detail) if current_detail is not None else None
    reads.current_unread = not reads.blocked and current_set is None
    if current_listed is None:  # retired (or gone): kept, pre-selected, labelled
        reads.options.append(
            Option(
                rating.id,
                rating.name,
                rating.version_id,
                current.version,
                None if reads.blocked else "current",
                retired=True,
            )
        )
    for item in listed:
        if item.id == rating.id:
            reads.options.append(
                Option(
                    item.id,
                    item.name,
                    item.version_id,
                    item.version,
                    None if reads.blocked else "current",
                )
            )
            continue
        if reads.blocked:
            reads.options.append(Option(item.id, item.name, item.version_id, item.version, None))
            continue
        detail = details.get(item.id)
        target = guardrail_set(detail) if isinstance(detail, ContentRating) else None
        adds: tuple[str, ...] = ()
        drops: tuple[str, ...] = ()
        if target is not None and current_set is not None:
            adds, drops = tuple(sorted(target - current_set)), tuple(sorted(current_set - target))
        reads.options.append(
            Option(
                item.id,
                item.name,
                item.version_id,
                item.version,
                direction(current_set, target),
                adds,
                drops,
            )
        )
    for error in details.values():
        if isinstance(error, EngineClientError):
            log_engine_error(request, error)
    return reads


# --- the rating page ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RatingRefusal:
    """A refused PUT, shown above the form: a line (linked to the running check for 409
    ``story_paused``) or the generic panel."""

    text: str = ""
    url: str = ""
    panel: ErrorPanel | None = None


def _render(
    request: HttpRequest,
    reads: Reads,
    *,
    selected: str | None = None,
    field_error: str = "",
    refusal: RatingRefusal | None = None,
) -> HttpResponse:
    story = reads.story
    note, note_url = "", ""
    if story.status == "archived":
        note = ARCHIVED_NOTE
    elif is_paused(story):
        note = RATING_FORM_NOTE
        job_id = story.pause.job_id if story.pause is not None else None
        note_url = check_url(story.id, job_id) if job_id else ""
    context = {
        "story": story,
        "reads": reads,
        "current": reads.current,
        "options": reads.options,
        "selected": selected if selected is not None else reads.current.id,
        "field_error": field_error,
        "refusal": refusal,
        "disabled_note": note,
        "disabled_url": note_url,
        "current_unread": CURRENT_UNREAD if reads.current_unread else "",
        "idempotency_key": new_ulid(),
        "pause_banner": pause_banner(story),
        "rating_url": _rating_url(story.id),
    }
    return render(request, "console/stories/rating.html", context)


@require_http_methods(["GET", "HEAD", "POST"])
def rating_page(request: HttpRequest, story_id: str) -> HttpResponse:
    """GET: the current rating and the labelled select. POST: change it (see the module)."""
    if request.method == "POST":
        return _save(request, story_id)
    client = engine_client_for(request)
    return _render(request, read_ratings(request, client, client.get_story(story_id)))


def _no_change(request: HttpRequest, story_id: str) -> HttpResponse:
    messages.success(request, NO_CHANGE)
    return redirect(_rating_url(story_id))


def _save(request: HttpRequest, story_id: str) -> HttpResponse:
    posted = request.POST.get(RATING_FIELD, "").strip()
    if posted and posted == request.POST.get(CURRENT_FIELD, ""):
        return _no_change(request, story_id)  # #43/#54: no write without a change, no call
    client = engine_client_for(request)
    reads = read_ratings(request, client, client.get_story(story_id))
    if reads.list_error is not None or reads.blocked:
        return _render(request, reads)
    if posted == reads.current.id:
        return _no_change(request, story_id)
    option = reads.option(posted)
    if option is None:
        return _render(request, reads, selected=posted, field_error=CHOOSE)
    if option.direction == "relax":
        return _put(request, client, story_id, option.id, _target(option))
    token = twostage.sign(KIND, story_id, _target(option), base={"current": reads.current.id})
    url = reverse("stories:rating-confirm", args=[story_id])
    return redirect(f"{url}?{urlencode({CONFIRM_QUERY: token})}")


def _target(option: Option) -> dict[str, Any]:
    """What the confirm token (and a direct PUT's flash) carries about the target."""
    return {
        "rating_id": option.id,
        "name": option.name,
        "version_id": option.version_id,
        "version": option.version,
        "adds": list(option.adds),
        "drops": list(option.drops),
        "unknown": option.direction == "unknown",
    }


def version_text(version_id: str, target: Mapping[str, Any]) -> str:
    """The integer version when ``version_id`` is the target's listed latest, else the id."""
    version = target.get("version")
    if version_id == target.get("version_id") and isinstance(version, int):
        return str(version)
    return version_id


def _put(
    request: HttpRequest,
    client: EngineClient,
    story_id: str,
    rating_id: str,
    target: Mapping[str, Any],
) -> HttpResponse:
    """The PUT, whatever the label said: the Engine's 200 flashes, its 202 goes to the check."""
    key = posted_idempotency_key(request)
    try:
        result = client.set_story_content_rating(story_id, rating_id, idempotency_key=key)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _refused(request, client, story_id, exc, rating_id)
    if isinstance(result, RatingCheckStarted):
        messages.success(
            request,
            f"{STARTED} — operation {result.operation_id}, idempotency key "
            f"{result.idempotency_key or key}",
        )
        return redirect(check_url(story_id, result.job_id))
    if not result.changed:
        return _no_change(request, story_id)
    name = target.get("name") or result.content_rating_id
    shown = version_text(result.rating_version, target)
    messages.success(request, f"Rating changed to {name} (version {shown})")
    return redirect(_rating_url(story_id))


def _refused(
    request: HttpRequest,
    client: EngineClient,
    story_id: str,
    exc: EngineClientError,
    rating_id: str,
) -> HttpResponse:
    """The page again (the list re-read) with the refusal: 409 ``story_paused`` links the
    running check, 422 ``rating_retired`` sits at the select, 423 ``story_busy`` says when to
    retry, anything else is the generic panel."""
    reads = read_ratings(request, client, client.get_story(story_id))
    if not isinstance(exc, EngineError):
        return _render(request, reads, refusal=RatingRefusal(panel=panel_for(exc)))
    if exc.code == "rating_retired":
        return _render(request, reads, selected=rating_id, field_error=RETIRED_TEXT)
    if exc.code == "story_paused":
        job_id = exc.details.get("job_id")
        if not (isinstance(job_id, str) and job_id) and reads.story.pause is not None:
            job_id = reads.story.pause.job_id
        url = check_url(story_id, job_id) if isinstance(job_id, str) and job_id else ""
        return _render(request, reads, refusal=RatingRefusal(ALREADY_RUNNING, url))
    if exc.code == "story_busy":
        wait = f"{exc.retry_after} s" if exc.retry_after else "a moment"
        text = f"A turn is running — try again in {wait}"
        return _render(request, reads, selected=rating_id, refusal=RatingRefusal(text))
    return _render(request, reads, refusal=RatingRefusal(panel=panel_for(exc)))


# --- the confirm page -----------------------------------------------------------------------------


def _payload(token: str, story_id: str) -> twostage.Payload:
    """The confirm token for ``story_id`` (another story's, or a malformed one, is expired)."""
    payload = twostage.unsign(KIND, token)
    body, base = payload.body, payload.base
    if (
        payload.target_id != story_id
        or not isinstance(body, dict)
        or not isinstance(body.get("rating_id"), str)
        or not isinstance(base, dict)
        or not isinstance(base.get("current"), str)
    ):
        raise twostage.ConfirmationExpired(twostage.EXPIRED_TEXT)
    return payload


def _expired(request: HttpRequest, story_id: str) -> HttpResponse:
    messages.error(request, twostage.EXPIRED_TEXT)
    return redirect(_rating_url(story_id))


@require_http_methods(["GET", "HEAD", "POST"])
def rating_confirm(request: HttpRequest, story_id: str) -> HttpResponse:
    """GET ``?t=``: what a tighten does, with Confirm and Cancel; no Engine call. POST: one
    story read (the current rating must still be the one the token was computed against),
    then the PUT."""
    token = (
        request.POST.get(twostage.CONFIRM_FIELD, "")
        if request.method == "POST"
        else request.GET.get(CONFIRM_QUERY, "")
    )
    try:
        payload = _payload(token, story_id)
    except twostage.ConfirmationExpired:
        return _expired(request, story_id)
    body: dict[str, Any] = payload.body
    if request.method == "POST":
        client = engine_client_for(request)
        story = client.get_story(story_id)
        if story.content_rating.id != payload.base["current"]:
            return _expired(request, story_id)
        return _put(request, client, story_id, body["rating_id"], body)
    context = {
        "story_id": story_id,
        "target": body,
        "line_before": CONFIRM_LINE.split("{name}")[0],
        "line_after": CONFIRM_LINE.split("{name}")[1],
        "cant_compare": CANT_COMPARE if body.get("unknown") else "",
        "token": token,
        "confirm_url": reverse("stories:rating-confirm", args=[story_id]),
        "confirm_label": CONFIRM_LABEL,
        "cancel_url": _rating_url(story_id),
        "confirm_key": new_ulid(),
    }
    return render(request, "console/stories/rating_confirm.html", context)


# --- the check page -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RatingName:
    """A rating as the check page names it: its name ("" when unresolved) and, when known, its
    listed latest version id and integer version (``version_of`` resolves only that one)."""

    id: str
    name: str = ""
    version_id: str = ""
    version: int | None = None

    def version_of(self, version_id: str | None) -> int | None:
        if version_id and version_id == self.version_id:
            return self.version
        return None

    def query(self) -> dict[str, str]:
        """The display-only poll parameters (the partial reads only the check)."""
        found = {"t_name": self.name, "t_vid": self.version_id}
        if self.version is not None:
            found["t_v"] = str(self.version)
        return {k: v for k, v in found.items() if v}

    @classmethod
    def from_query(cls, rating_id: str, query: QueryDict) -> RatingName:
        raw = query.get("t_v", "")
        return cls(
            rating_id,
            query.get("t_name", ""),
            query.get("t_vid", ""),
            int(raw) if raw.isdigit() else None,
        )


def rating_names(
    request: HttpRequest, client: EngineClient
) -> dict[str, ContentRatingSummary] | None:
    """Every rating (retired included) by id, from one list read; ``None`` when it failed."""
    try:
        listed = client.list_content_ratings(include_retired=True)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None
    return {item.id: item for item in listed}


def _named(rating_id: str, names: Mapping[str, ContentRatingSummary] | None) -> RatingName:
    item = (names or {}).get(rating_id)
    if item is None:
        return RatingName(rating_id)
    return RatingName(rating_id, item.name, item.version_id, item.version)


@dataclass(frozen=True)
class ViolationRow:
    """One violation as the refused table shows it. ``position`` is "turn T, variant V, beat
    B" (linked to the transcript at that turn) or "kind ``id``"; a position of neither shape is
    "—" with ``raw`` (its JSON) in the row's "More"."""

    position: str
    position_id: str  # the ``{kind, id}`` id, rendered in <code>
    position_url: str
    raw: str
    policy_id: str | None
    policy_url: str
    policy_name: str | None
    code: str | None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _policy_url(policy_id: str | None) -> str:
    if not policy_id:
        return ""
    try:
        return reverse("policies:detail", args=[policy_id])
    except NoReverseMatch:
        return ""


def violation_row(story_id: str, violation: CheckViolation) -> ViolationRow:
    position = violation.position
    turn, variant, beat = (_int(position.get(k)) for k in ("turn", "variant", "beat"))
    kind, item_id = position.get("kind"), position.get("id")
    text, shown_id, url, raw = "—", "", "", ""
    if turn is not None and variant is not None and beat is not None:
        text = f"turn {turn}, variant {variant}, beat {beat}"
        query = urlencode({"as_of": str(turn), "variant": f"{turn}:{variant}"})
        url = f"{reverse('stories:history', args=[story_id])}?{query}#turn-{turn}"
    elif isinstance(kind, str) and kind and isinstance(item_id, str) and item_id:
        text, shown_id = kind, item_id
    else:
        raw = json.dumps(position, sort_keys=True, ensure_ascii=False)
    return ViolationRow(
        text,
        shown_id,
        url,
        raw,
        violation.policy_id,
        _policy_url(violation.policy_id),
        violation.policy_name,
        violation.code,
    )


@dataclass(frozen=True)
class CheckView:
    """A read check, as the status partial shows it."""

    check: RatingCheck
    target: RatingName
    violations: tuple[ViolationRow, ...] = ()

    @property
    def terminal(self) -> bool:
        return self.check.status in TERMINAL

    @property
    def progress(self) -> str:
        return progress_text(self.check.progress.done, self.check.progress.total)

    @property
    def indeterminate(self) -> bool:
        return self.check.progress.total == 0

    @property
    def target_name(self) -> str:
        return self.target.name or self.check.target.rating_id

    @property
    def applied_version(self) -> int | None:
        return self.target.version_of(self.check.rating_version)

    @property
    def error_class(self) -> str:
        found = (self.check.error or {}).get("class")
        return str(found) if found is not None else "—"

    @property
    def error_message(self) -> str:
        found = (self.check.error or {}).get("message")
        return str(found) if found is not None else "—"


def check_view(check: RatingCheck, target: RatingName) -> CheckView:
    rows = tuple(violation_row(check.story_id, v) for v in check.violations)
    return CheckView(check, target, rows)


def check_ended(check: RatingCheck) -> bool:
    """Whether polling stops: a reactivation re-check (#81) ends at any status but
    ``checking``; #80's check at a :data:`TERMINAL` one (an unknown status keeps polling)."""
    if is_reactivation(check):
        return check.status != CHECKING
    return check.status in TERMINAL


def settle_check(check: RatingCheck) -> int:
    """Settle the ``queued`` #44 row of a check that ended (idempotent): ``applied`` and
    ``reactivated`` → ``ok``; ``refused`` → ``error``/``rating_refused``; ``failed`` →
    ``error``/``error.class``. A reactivation re-check (#81) that ended ``reactivated`` also
    sets the row's ``engine_operation_id``, ``timeline_revision`` (and a fork's ``story_id``,
    the new story) from ``outcome.result``; any other ending is ``error``/
    ``unexpected_status``. The full check body is stored as the row's response."""
    if not check_ended(check):
        return 0
    outcome, code = EngineOperation.Outcome.OK, ""
    overrides: dict[str, Any] = {}
    if check.status == "refused":
        outcome, code = EngineOperation.Outcome.ERROR, REFUSED_CODE
    elif check.status == "failed":
        outcome = EngineOperation.Outcome.ERROR
        found = (check.error or {}).get("class")
        code = str(found) if found else "failed"
    elif check.status == REACTIVATED:
        overrides = job_overrides(check.outcome)
    elif check.status not in TERMINAL:
        outcome, code = EngineOperation.Outcome.ERROR, UNEXPECTED_STATUS
    return complete_job(check.job_id, outcome, code, asdict(check), overrides=overrides or None)


@dataclass(frozen=True)
class CheckRead:
    """One read of the check: the check, or why there is none (keep polling while the Engine
    does not answer; a 404 is "expired"; anything else stops on the panel)."""

    check: RatingCheck | None
    retrying: bool = False
    not_found: bool = False
    panel: ErrorPanel | None = None

    @property
    def polling(self) -> bool:
        if self.check is None:
            return self.retrying
        return not check_ended(self.check)


def read_check(request: HttpRequest, client: EngineClient, story_id: str, job_id: str) -> CheckRead:
    try:
        check = client.get_rating_check(story_id, job_id)
    except EngineClientError as exc:
        failure = poll_failure(request, exc)
        return CheckRead(None, failure.retry, failure.not_found, failure.panel)
    settle_check(check)
    return CheckRead(check)


# --- Reactivation re-checks (#81) -----------------------------------------------------------------


@dataclass(frozen=True)
class ResultRow:
    """One ``<dt>``/``<dd>`` of a reactivated edit's result (``code``: the value is an id)."""

    label: str
    value: str
    code: bool = False


@dataclass(frozen=True)
class ReactivationView:
    """A reactivation re-check (undo rewind, swap or fork held back, #81), as its status partial
    shows it. ``op`` is the edit the page names (:func:`console.reactivation.shown_op`);
    ``outcome`` is parsed once the check ended ``reactivated``; ``row`` is the #44 row whose
    202 started it (the requested fork name and the idempotency key), when the Console has one."""

    story_id: str
    check: RatingCheck | None
    op: str | None
    outcome: Outcome | None = None
    violations: tuple[ViolationRow, ...] = ()
    fork_name: str = ""
    key: str = ""
    source_name: str = ""

    @property
    def status(self) -> str | None:
        return self.check.status if self.check is not None else None

    @property
    def heading(self) -> str:
        return heading(self.status, self.op)

    @property
    def waiting(self) -> bool:
        return self.status == CHECKING

    @property
    def ended(self) -> bool:
        return self.check is not None and check_ended(self.check)

    @property
    def unexpected(self) -> bool:
        return self.ended and self.status not in (REACTIVATED, "refused", "failed")

    @property
    def unchanged_text(self) -> str:
        return NO_FORK if self.op == FORK else UNCHANGED

    @property
    def progress(self) -> str:
        if self.check is None:
            return ""
        return progress_text(self.check.progress.done, self.check.progress.total)

    @property
    def indeterminate(self) -> bool:
        return self.check is not None and self.check.progress.total == 0

    @property
    def error_class(self) -> str:
        found = ((self.check.error if self.check else None) or {}).get("class")
        return str(found) if found is not None else "—"

    @property
    def error_message(self) -> str:
        found = ((self.check.error if self.check else None) or {}).get("message")
        return str(found) if found is not None else "—"

    @property
    def raw_check(self) -> str:
        return raw_json(asdict(self.check)) if self.check is not None else ""

    @property
    def result(self) -> UntruncateResult | SwapResult | ForkResult | None:
        return self.outcome.result if self.outcome is not None else None

    @property
    def timeline_result(self) -> UntruncateResult | SwapResult | None:
        found = self.result
        return found if isinstance(found, UntruncateResult | SwapResult) else None

    @property
    def fork_result(self) -> ForkResult | None:
        found = self.result
        return found if isinstance(found, ForkResult) else None

    @property
    def head(self) -> int | None:
        found = self.timeline_result
        return found.next_turn_index - 1 if found is not None else None

    @property
    def transcript_url(self) -> str:
        found = self.timeline_result
        return transcript_url(self.story_id, found) if found is not None else ""

    @property
    def shown_key(self) -> str:
        found = self.result
        return self.key or (found.idempotency_key if found is not None else None) or "—"

    @property
    def rows(self) -> tuple[ResultRow, ...]:
        """#53's result fields: undo shows ``restored_operations``, ``swapped``,
        ``active_chain``, ``truncate_operation_id`` and ``dormant_operations``; swap shows
        ``active_chain``, ``activated_operations`` and ``dormant_operations``; a fork where it
        was forked from and its new story. All show the operation id and the idempotency key."""
        found = self.result
        if found is None:
            return ()
        rows: list[ResultRow] = []
        if isinstance(found, UntruncateResult | SwapResult):
            rows += [
                ResultRow("Head turn", str(found.next_turn_index - 1)),
                ResultRow("Timeline revision", str(found.timeline_revision)),
            ]
        if isinstance(found, UntruncateResult):
            rows += [
                ResultRow("Restored operations", str(found.restored_operations)),
                ResultRow("Swapped", "yes" if found.swapped else "no"),
                ResultRow("Active chain", _chain(found.active_chain)),
                ResultRow("Truncate operation", found.truncate_operation_id, code=True),
                ResultRow("Dormant operations", str(found.dormant_operations)),
            ]
        elif isinstance(found, SwapResult):
            rows += [
                ResultRow("Active chain", _chain(found.active_chain)),
                ResultRow("Activated operations", str(found.activated_operations)),
                ResultRow("Dormant operations", str(found.dormant_operations)),
            ]
        else:
            rows += [
                ResultRow("New story", found.name or "—"),
                ResultRow("New story id", found.story_id, code=True),
                ResultRow("At turn", str(found.forked_from.at_turn)),
                ResultRow("Variant chain", _chain(found.forked_from.variant_chain, "active")),
            ]
        rows += [
            ResultRow("Operation", found.operation_id or "—", code=bool(found.operation_id)),
            ResultRow("Idempotency key", self.shown_key, code=self.shown_key != "—"),
        ]
        return tuple(rows)

    @property
    def more_rows(self) -> tuple[ResultRow, ...]:
        """A fork's ``counts``, ``forked_from.revision`` and new ``timeline_revision``."""
        found = self.fork_result
        if found is None:
            return ()
        counts = ", ".join(f"{k}: {v}" for k, v in sorted(found.counts.items())) or "—"
        return (
            ResultRow("Counts", counts),
            ResultRow("Forked from revision", str(found.forked_from.revision)),
            ResultRow("Timeline revision", str(found.timeline_revision)),
        )

    @property
    def new_story_url(self) -> str:
        found = self.fork_result
        return reverse("stories:detail", args=[found.story_id]) if found is not None else ""

    @property
    def fork_banner(self) -> ForkBanner | None:
        """#53's "Forked from" banner for the new story, shown inline."""
        found = self.fork_result
        if found is None:
            return None
        return ForkBanner(
            self.story_id,
            self.source_name,
            found.forked_from.at_turn,
            tuple((c.name, c.source_id, c.id) for c in found.characters),
        )


def _chain(chain: ChainRef | None, missing: str = "—") -> str:
    return chain_label(chain.turn, chain.variant) if chain is not None else missing


def reactivation_view(
    story_id: str, check: RatingCheck | None, op: str | None, *, source_name: str = ""
) -> ReactivationView:
    """The view of a reactivation re-check read as ``check`` (``None``: the read failed) on a
    page opened with ``?op=op``."""
    row = job_row(check.job_id if check is not None else None)
    shown = shown_op(check, op)
    outcome = None
    violations: tuple[ViolationRow, ...] = ()
    if check is not None and check.status == REACTIVATED:
        outcome = parse_outcome(check.outcome, op)
    if check is not None:
        violations = tuple(violation_row(story_id, v) for v in check.violations)
    return ReactivationView(
        story_id,
        check,
        shown,
        outcome,
        violations,
        fork_name=requested_fork_name(row) if shown == FORK else "",
        key=(row.idempotency_key or "") if row is not None else "",
        source_name=source_name,
    )


def _remember_fork(request: HttpRequest, view: ReactivationView) -> None:
    """A reactivated fork sets #53's session banner key for its new story (every terminal
    render; the new story's first view pops it), keeping a source name an earlier render set."""
    banner = view.fork_banner
    found = view.fork_result
    if banner is None or found is None:
        return
    key = FORK_BANNER_KEY.format(found.story_id)
    earlier = request.session.get(key)
    name = banner.source_name
    if not name and isinstance(earlier, dict):
        name = str(earlier.get("source_name") or "")
    request.session[key] = {
        "source_id": banner.source_id,
        "source_name": name,
        "at_turn": banner.at_turn,
        "revision": found.forked_from.revision,
        "characters": [list(c) for c in banner.characters],
    }


def _is_reactivation_page(read: CheckRead, op: str | None) -> bool:
    """A reactivation page: the check is one, or (unread) the page was opened with ``?op=``.
    A check of any other kind renders #80's page, whatever ``op`` says."""
    if read.check is not None:
        return is_reactivation(read.check)
    return valid_op(op) is not None


def _check_context(
    story_id: str,
    job_id: str,
    read: CheckRead,
    target: RatingName,
    params: Mapping[str, str],
    rx: ReactivationView | None = None,
) -> dict[str, Any]:
    body = "console/stories/_rating_check_status.html"
    if rx is not None:
        body = "console/stories/_reactivation_status.html"
    context = poll_context(
        poll_url=check_url(story_id, job_id, **params),
        refresh_url=check_url(story_id, job_id, **params),
        body_template=body,
        polling=read.polling,
        retrying=read.retrying,
    )
    context.update(
        {
            "story_id": story_id,
            "job_id": job_id,
            "view": check_view(read.check, target) if read.check is not None else None,
            "not_found": read.not_found,
            "not_found_text": NOT_FOUND,
            "engine_error": read.panel,
            "not_changed": NOT_CHANGED,
            "rating_url": _rating_url(story_id),
            "story_url": reverse("stories:detail", args=[story_id]),
            "rx": rx,
            "rx_text": {
                "not_found": REACTIVATION_NOT_FOUND,
                "waiting": WAITING_LINE,
                "unreadable": UNREADABLE,
                "failed": FAILED_LINE,
            },
            "variants_url": reverse("stories:history", args=[story_id]) + "#variants",
        }
    )
    return context


def _poll_params(request: HttpRequest, target: RatingName) -> dict[str, str]:
    """The poll URL's display parameters: the target's name and version, plus any others the
    page was opened with (#81's ``op``), minus the banner's."""
    kept = {
        key: request.GET.get(key, "")
        for key in request.GET
        if key not in (BANNER_PARAM, CAUSE_PARAM, "t_name", "t_vid", "t_v")
    }
    return {**kept, **target.query()}


def _banner(request: HttpRequest, story_id: str, job_id: str) -> HttpResponse:
    """The pause banner's poll (``?banner=1``): the banner again with the check's progress, or
    ``HX-Refresh: true`` once it ended (the page reloads unpaused)."""
    read = read_check(request, engine_client_for(request), story_id, job_id)
    if read.check is not None and check_ended(read.check):
        response = HttpResponse("")
        response["HX-Refresh"] = "true"
        return response
    progress = read.check.progress if read.check is not None else None
    banner = PauseBanner(
        story_id,
        request.GET.get(CAUSE_PARAM) or RATING_CHECK,
        job_id,
        progress.done if progress is not None else 0,
        progress.total if progress is not None else 0,
        unavailable=read.panel,
        not_found=read.not_found,
        retrying=read.retrying,
        op=valid_op(request.GET.get(OP_PARAM)) or "",
    )
    return render(request, "console/stories/_pause_banner.html", {"pause_banner": banner})


@require_safe
def rating_check(request: HttpRequest, story_id: str, job_id: str) -> HttpResponse:
    """The check page; with ``HX-Request`` its poll partial (reads only the check), and with
    ``?banner=1`` too the pause banner's partial."""
    if is_htmx(request) and request.GET.get(BANNER_PARAM) == "1":
        return _banner(request, story_id, job_id)
    client = engine_client_for(request)
    op = request.GET.get(OP_PARAM)
    if is_htmx(request):
        read = read_check(request, client, story_id, job_id)
        target = RatingName.from_query(
            read.check.target.rating_id if read.check is not None else "", request.GET
        )
        rx = None
        if _is_reactivation_page(read, op):
            rx = reactivation_view(story_id, read.check, op)
            _remember_fork(request, rx)
        context = _check_context(story_id, job_id, read, target, _poll_params(request, target), rx)
        return render(request, "console/_job_poll.html", context)
    story = _story_or_none(request, client, story_id)
    read = read_check(request, client, story_id, job_id)
    names = rating_names(request, client) if read.check is not None else {}
    target = RatingName("")
    from_rating = RatingName("")
    if read.check is not None:
        target = _named(read.check.target.rating_id, names)
        from_rating = _named(read.check.from_rating_id, names)
    rx = None
    if _is_reactivation_page(read, op):
        source = (story.name or "") if story is not None else ""
        rx = reactivation_view(story_id, read.check, op, source_name=source)
        _remember_fork(request, rx)
    context = _check_context(story_id, job_id, read, target, _poll_params(request, target), rx)
    context.update(
        {
            "story": story,
            "check": read.check,
            "target": target,
            "target_version": target.version_of(
                read.check.target.version if read.check is not None else None
            ),
            "from_rating": from_rating,
            "names_unavailable": NAMES_UNAVAILABLE if names is None else "",
        }
    )
    status = 404 if read.not_found else 200
    return render(request, "console/stories/rating_check.html", context, status=status)


def _story_or_none(request: HttpRequest, client: EngineClient, story_id: str) -> Story | None:
    """The story for the page's name and breadcrumb; a failed read leaves just the id."""
    try:
        return client.get_story(story_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None

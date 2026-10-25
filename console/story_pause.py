"""A paused story (#80): the banner its pages show while a check runs, the controls that pause
disables, and the line a 409 ``story_paused`` refusal shows.

The Engine pauses a story while a rating check (a tightening rating change, #80) or a
reactivation re-check (#81) runs over its history; every call that would change the story's
truth answers 409 ``story_paused`` until it ends, and reads are served. ``GET /stories/{id}``
reports it as ``status: paused`` with ``pause {job_id, cause, progress}``.

Every page that already reads the story (play view, settings, history, the rating page) puts
:func:`pause_banner` in its context as ``pause_banner``; ``base.html`` renders
``console/stories/_pause_banner.html`` under the acting-as banner and the flash messages. The
banner polls the check page's banner partial (``?banner=1``) every 2 s; when the check ends that
partial answers ``HX-Refresh: true`` and the page reloads unpaused.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard
from urllib.parse import urlencode

from django.urls import reverse

from console.engine.client import EngineError
from console.engine.models import Story
from console.engine_errors import ErrorPanel
from console.job_poll import POLL_TRIGGER, RETRYING_TEXT
from console.reactivation import OP_PARAM, REACTIVATION_CHECK, job_op, op_name

RATING_CHECK = "rating_check"  # Engine ``pause.RATING_CHECK``
# What the banner leads with, by ``pause.cause``; ``reactivation_check`` (#81) names the edit
# held back (:func:`cause_text`); any other cause shows "Paused: <cause>".
CAUSE_TEXT: dict[str, str] = {RATING_CHECK: "Paused: rating check in progress"}
REACTIVATION_TEXT = "Paused: guardrail re-check before {op}"
AN_EDIT = "an edit"  # a reactivation whose op the Console did not record
UNAVAILABLE_TEXT = "Paused — check status unavailable"
STARTING = "starting…"
NOTHING_TO_CHECK = "nothing to check"  # #163: an ended check that counted no records
# Disabled controls while paused (#80's wording).
TURN_NOTE = "Paused — turns are refused until the check finishes"
CONTROLS_NOTE = "Paused while the check runs"
RATING_FORM_NOTE = "A rating check is running"
PAUSED_LINE = "The story is paused for a check"
BANNER_PARAM = "banner"  # ``?banner=1``: the check view answers the banner partial
CAUSE_PARAM = "cause"  # the banner partial's ``pause.cause`` (its read is the check's alone)


def check_url(story_id: str, job_id: str, **params: str) -> str:
    """The check page (#80) for ``job_id``, with ``params`` (``banner=1``; #81's ``op``)."""
    path = reverse("stories:rating-check", args=[story_id, job_id])
    return f"{path}?{urlencode(params)}" if params else path


def progress_text(done: int, total: int, *, ended: bool = False) -> str:
    """``done/total``, or with no records counted "starting…" while the check runs and
    "nothing to check" once it has ended (#163)."""
    if total == 0:
        return NOTHING_TO_CHECK if ended else STARTING
    return f"{done}/{total}"


def cause_text(cause: str, op: str = "") -> str:
    """The banner's lead for ``cause``; a reactivation re-check names ``op`` (from the #44 row
    that started it), else "an edit"."""
    if cause == REACTIVATION_CHECK:
        return REACTIVATION_TEXT.format(op=op_name(op).lower() or AN_EDIT)
    return CAUSE_TEXT.get(cause, f"Paused: {cause}")


@dataclass(frozen=True)
class PauseBanner:
    """``console/stories/_pause_banner.html``'s context (``pause_banner``). ``polling``: the
    banner re-reads the check every 2 s (it has a job and its last read did not fail);
    ``unavailable``: the last read failed (404 or another error), so it stopped; ``retrying``:
    the Engine did not answer, so it keeps polling. ``op`` (#81): the held-back edit of a
    ``reactivation_check``, carried as ``?op=`` on the check and poll links."""

    story_id: str
    cause: str
    job_id: str | None
    done: int
    total: int
    unavailable: ErrorPanel | None = None
    not_found: bool = False
    retrying: bool = False
    op: str = ""

    @property
    def text(self) -> str:
        if self.unavailable is not None or self.not_found:
            return UNAVAILABLE_TEXT
        return f"{cause_text(self.cause, self.op)} ({progress_text(self.done, self.total)})"

    @property
    def _op_params(self) -> dict[str, str]:
        return {OP_PARAM: self.op} if self.op else {}

    @property
    def check_url(self) -> str:
        return check_url(self.story_id, self.job_id, **self._op_params) if self.job_id else ""

    @property
    def poll_url(self) -> str:
        if not self.job_id:
            return ""
        params = {BANNER_PARAM: "1", CAUSE_PARAM: self.cause, **self._op_params}
        return check_url(self.story_id, self.job_id, **params)

    @property
    def polling(self) -> bool:
        return bool(self.job_id) and self.unavailable is None and not self.not_found

    @property
    def poll_trigger(self) -> str:
        return POLL_TRIGGER

    @property
    def retrying_text(self) -> str:
        return RETRYING_TEXT if self.retrying else ""

    @property
    def refresh_url(self) -> str:
        return reverse("stories:detail", args=[self.story_id])

    @property
    def indeterminate(self) -> bool:
        return self.total == 0


def pause_banner(story: Story) -> PauseBanner | None:
    """The banner for ``story`` while ``GET /stories/{id}`` reports ``pause``, else ``None``.
    A reactivation re-check's op comes from the #44 row that started it (no Engine call)."""
    pause = story.pause
    if pause is None:
        return None
    op = job_op(pause.job_id) if pause.cause == REACTIVATION_CHECK else None
    return PauseBanner(
        story.id,
        pause.cause,
        pause.job_id,
        pause.progress.done,
        pause.progress.total,
        op=op or "",
    )


def is_paused(story: Story) -> bool:
    return story.pause is not None or story.status == "paused"


@dataclass(frozen=True)
class PausedRefusal:
    """A 409 ``story_paused``'s line: "The story is paused for a check (done/total)", linked to
    the check when the refusal names it."""

    text: str
    url: str


def paused_refusal(exc: EngineError, story_id: str | None = None) -> PausedRefusal:
    """The line for a 409 ``story_paused`` refusal (``details``: ``story_id``, ``job_id``,
    ``cause``, ``progress {done, total}``). ``story_id`` defaults to the refusal's own."""
    details: Mapping[str, Any] = exc.details
    progress = details.get("progress")
    done = progress.get("done") if isinstance(progress, dict) else None
    total = progress.get("total") if isinstance(progress, dict) else None
    shown = f" ({progress_text(done, total)})" if _count(done) and _count(total) else ""
    if story_id is None:
        found = details.get("story_id")
        story_id = found if isinstance(found, str) and found else None
    job_id = details.get("job_id")
    url = ""
    if story_id and isinstance(job_id, str) and job_id:
        url = check_url(story_id, job_id)
    return PausedRefusal(f"{PAUSED_LINE}{shown}", url)


def _count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)

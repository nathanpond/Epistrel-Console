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

**E10's backfill causes** (#295). A backfill pauses the story with ``cause: backfill`` while it
runs and ``backfill_conflicts`` while it waits on the author. For those two causes the banner's
and the refusals' text is ``console.upgrades.backfill_pause_text``, their link the Backfill page
(``console.upgrades.backfill_url``, "" while unregistered), and the banner polls
``<backfill_url>?banner=1&cause=<cause>`` at ``SLOW_POLL_TRIGGER`` only while that name resolves
(otherwise it doesn't poll and shows "Refresh"). No rating-check URL is ever built for them. The
banner's counts are the backfill's chunks (``chunks_done``/``chunks_total``, else ``done``/
``total``) under E7's count rule; a missing count gives the indeterminate ``<progress>``. The
banner makes no extra Engine read, so ``backfill_conflicts`` reads "its conflicts" there; the
refusal uses ``details.conflicts_open``. ``rating_check`` and ``reactivation_check`` are unchanged.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard
from urllib.parse import urlencode

from django.urls import reverse

from console.engine.client import EngineError
from console.engine.models import PauseDetails, Story, StoryPause
from console.engine_errors import ErrorPanel
from console.job_poll import POLL_TRIGGER, RETRYING_TEXT, SLOW_POLL_TRIGGER
from console.reactivation import OP_PARAM, REACTIVATION_CHECK, job_op, op_name
from console.upgrades import BACKFILL_CAUSES, backfill_pause_text, backfill_url, chunk_counts

RATING_CHECK = "rating_check"  # Engine ``pause.RATING_CHECK``
# What the banner leads with, by ``pause.cause``; ``reactivation_check`` (#81) names the edit
# held back (:func:`cause_text`); any other cause shows "Paused: <cause>".
CAUSE_TEXT: dict[str, str] = {RATING_CHECK: "Paused: rating check in progress"}
REACTIVATION_TEXT = "Paused: guardrail re-check before {op}"
AN_EDIT = "an edit"  # a reactivation whose op the Console did not record
UNAVAILABLE_TEXT = "Paused — check status unavailable"
NOT_FOUND_TEXT = "No such check, or it expired (kept 30 days)"
BACKFILL_UNAVAILABLE_TEXT = "Paused — backfill status unavailable"  # E10 (#295)
BACKFILL_NOT_FOUND_TEXT = "No such backfill"
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
    done: int | None
    total: int | None
    unavailable: ErrorPanel | None = None
    not_found: bool = False
    retrying: bool = False
    op: str = ""

    @property
    def backfill(self) -> bool:
        """E10: a backfill's pause (``backfill`` or ``backfill_conflicts``)."""
        return self.cause in BACKFILL_CAUSES

    @property
    def text(self) -> str:
        if self.unavailable is not None or self.not_found:
            return BACKFILL_UNAVAILABLE_TEXT if self.backfill else UNAVAILABLE_TEXT
        if self.backfill:
            progress = {"chunks_done": self.done, "chunks_total": self.total}
            return backfill_pause_text(self.cause, PauseDetails(self.cause, self.job_id, progress,
                                                                None, None))  # fmt: skip
        return (
            f"{cause_text(self.cause, self.op)} ({progress_text(self.done or 0, self.total or 0)})"
        )

    @property
    def not_found_text(self) -> str:
        return BACKFILL_NOT_FOUND_TEXT if self.backfill else NOT_FOUND_TEXT

    @property
    def _op_params(self) -> dict[str, str]:
        return {OP_PARAM: self.op} if self.op else {}

    @property
    def check_url(self) -> str:
        if self.backfill:
            return backfill_url(self.story_id) or ""
        return check_url(self.story_id, self.job_id, **self._op_params) if self.job_id else ""

    @property
    def poll_url(self) -> str:
        if self.backfill:
            found = backfill_url(self.story_id)
            query = urlencode({BANNER_PARAM: "1", CAUSE_PARAM: self.cause})
            return f"{found}?{query}" if found else ""
        if not self.job_id:
            return ""
        params = {BANNER_PARAM: "1", CAUSE_PARAM: self.cause, **self._op_params}
        return check_url(self.story_id, self.job_id, **params)

    @property
    def polling(self) -> bool:
        if self.unavailable is not None or self.not_found:
            return False
        return bool(self.poll_url) if self.backfill else bool(self.job_id)

    @property
    def poll_trigger(self) -> str:
        return SLOW_POLL_TRIGGER if self.backfill else POLL_TRIGGER

    @property
    def retrying_text(self) -> str:
        return RETRYING_TEXT if self.retrying else ""

    @property
    def refresh_url(self) -> str:
        return reverse("stories:detail", args=[self.story_id])

    @property
    def indeterminate(self) -> bool:
        if self.backfill:
            return self.done is None or self.total is None or self.total <= 0
        return self.total == 0 or self.total is None


def pause_banner(story: Story) -> PauseBanner | None:
    """The banner for ``story`` while ``GET /stories/{id}`` reports ``pause``, else ``None``.
    A reactivation re-check's op comes from the #44 row that started it (no Engine call)."""
    pause = story.pause
    if pause is None:
        return None
    progress = pause.progress
    if pause.cause in BACKFILL_CAUSES:
        done, total = chunk_counts(_progress_map(pause))
        return PauseBanner(story.id, pause.cause, pause.job_id, done, total)
    op = job_op(pause.job_id) if pause.cause == REACTIVATION_CHECK else None
    return PauseBanner(
        story.id,
        pause.cause,
        pause.job_id,
        progress.done if progress.done is not None else 0,
        progress.total if progress.total is not None else 0,
        op=op or "",
    )


def _progress_map(pause: StoryPause) -> dict[str, Any]:
    """The story read's ``pause.progress`` as the raw mapping ``backfill_pause_text`` reads."""
    progress = pause.progress
    return {
        "chunks_done": progress.chunks_done,
        "chunks_total": progress.chunks_total,
        "done": progress.done,
        "total": progress.total,
        "phase": progress.phase,
    }


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
    if story_id is None:
        found = details.get("story_id")
        story_id = found if isinstance(found, str) and found else None
    cause = details.get("cause")
    if isinstance(cause, str) and cause in BACKFILL_CAUSES:
        text = backfill_pause_text(cause, PauseDetails.from_details(details))
        return PausedRefusal(text, (backfill_url(story_id) if story_id else None) or "")
    progress = details.get("progress")
    done = progress.get("done") if isinstance(progress, dict) else None
    total = progress.get("total") if isinstance(progress, dict) else None
    shown = f" ({progress_text(done, total)})" if _count(done) and _count(total) else ""
    job_id = details.get("job_id")
    url = ""
    if story_id and isinstance(job_id, str) and job_id:
        url = check_url(story_id, job_id)
    return PausedRefusal(f"{PAUSED_LINE}{shown}", url)


def _count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def story_paused_refusal(story: Story) -> PausedRefusal:
    """:func:`paused_refusal`'s line for a story the page read as paused (``Story.pause``),
    so a page can say it before any write (#263): the progress when the read reports it, linked
    to the check when it names a job."""
    pause = story.pause
    if pause is None:
        return PausedRefusal(PAUSED_LINE, "")
    if pause.cause in BACKFILL_CAUSES:
        details = PauseDetails(pause.cause, pause.job_id, _progress_map(pause), None, None)
        return PausedRefusal(
            backfill_pause_text(pause.cause, details), backfill_url(story.id) or ""
        )
    done, total = pause.progress.done, pause.progress.total
    shown = f" ({progress_text(done, total)})" if _count(done) and _count(total) else ""
    url = check_url(story.id, pause.job_id) if pause.job_id else ""
    return PausedRefusal(f"{PAUSED_LINE}{shown}", url)

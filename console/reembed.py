"""The embedding re-embed run (#150): ``GET /admin/reembed/status`` as the run page shows it.

A run is **finished** when the Engine says so (``finished_at`` set) or, since the Engine leaves
``finished_at`` null for a run with no stories, when it has started and its first page (no
``after``) is its only page (no ``next``) with no story ``queued`` or ``running``, zero stories
included. Otherwise a started run is **running**, and the run section polls every 5 s keeping
its cursor (#64 convention 10: no answer or a 503 keeps polling with "Engine unavailable —
retrying"; any other failure stops on the error panel).

Story names come from one ``GET /stories`` read per render (not ``story_labels``, whose cap is
below the 100-row page); an id the list doesn't hold, and every id when the read fails, is a
``<code>…last 8</code>``.
"""

from dataclasses import dataclass, field

from django.http import HttpRequest
from django.urls import reverse
from django.utils.http import urlencode

from console.engine.client import EngineClient, EngineClientError
from console.engine.models import ReembedStatus, StorySummary
from console.engine_errors import ErrorPanel, Fix, error_class_fix, log_engine_error, panel_for
from console.job_poll import poll_failure

PAGE_SIZE = 100
POLL_TRIGGER = "every 5s"  # a run lasts minutes, and each poll re-reads the story list
QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
ACTIVE = frozenset({QUEUED, RUNNING})

EMPTY = "—"
FIRST_PIN = "none (first pin)"
NO_RUN = "No embedding model change yet — nothing to re-embed."
NO_STORIES = "Finished — no story had memories to re-embed."
UNNAMED = "(unnamed)"
RETRY_QUESTION = (
    "Retry every story whose latest re-embed job failed? The Engine enqueues a new job for "
    "each, including failed stories on other pages."
)
RETRY_LABEL = "Retry failed"
NOTHING_TO_RETRY = "Nothing to retry"


def retried_text(count: int) -> str:
    """The retry's flash: "Retried 1 story" / "Retried N stories"."""
    return "Retried 1 story" if count == 1 else f"Retried {count:,} stories"


@dataclass(frozen=True)
class StoryRow:
    story_id: str
    url: str | None  # None: not in the story list (or the list read failed)
    name: str | None  # None: unnamed, or not listed (then the id alone)
    archived: bool
    status: str
    done: int
    total: int
    error: str | None
    fix: Fix | None  # an embedding mismatch's configuration-error line

    @property
    def failed(self) -> bool:
        return self.status == FAILED

    @property
    def listed(self) -> bool:
        return self.url is not None


@dataclass(frozen=True)
class Counts:
    queued: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0


@dataclass(frozen=True)
class Run:
    """The run section: the status read and its rows, or the panel or retrying notice standing
    in for it."""

    after: str | None = None
    status: ReembedStatus | None = None
    rows: list[StoryRow] = field(default_factory=list)
    counts: Counts = Counts()
    error: ErrorPanel | None = None
    retrying: bool = False

    @property
    def empty(self) -> bool:
        """No model change yet: every header field null and no stories."""
        s = self.status
        return s is not None and (
            s.from_version is None
            and s.to_version is None
            and s.started_at is None
            and s.finished_at is None
            and not s.stories
            and s.next_cursor is None
        )

    @property
    def finished(self) -> bool:
        s = self.status
        if s is None:
            return False
        if s.finished_at is not None:
            return True
        return (
            s.started_at is not None
            and self.after is None
            and s.next_cursor is None
            and not any(story.status in ACTIVE for story in s.stories)
        )

    @property
    def running(self) -> bool:
        s = self.status
        return s is not None and s.started_at is not None and not self.finished

    @property
    def no_stories(self) -> bool:
        """A started run with no story at all (the Engine leaves ``finished_at`` null)."""
        s = self.status
        return (
            s is not None
            and s.started_at is not None
            and not s.stories
            and self.after is None
            and s.next_cursor is None
        )

    @property
    def polling(self) -> bool:
        return self.retrying or self.running

    @property
    def paged(self) -> bool:
        """Counts are labelled "on this page" when the run has another page."""
        return self.after is not None or (
            self.status is not None and self.status.next_cursor is not None
        )

    @property
    def retry_shown(self) -> bool:
        return self.status is not None and (
            self.counts.failed > 0 or self.status.next_cursor is not None
        )

    @property
    def from_version(self) -> str:
        value = self.status.from_version if self.status else None
        return FIRST_PIN if value is None else value


def counts(status: ReembedStatus) -> Counts:
    """Per-status counts for the page; an unknown status counts in no bucket."""
    seen = [story.status for story in status.stories]
    return Counts(*(seen.count(name) for name in (QUEUED, RUNNING, DONE, FAILED)))


def rows(status: ReembedStatus, stories: list[StorySummary] | None) -> list[StoryRow]:
    """The page's stories in the Engine's order, named from ``stories`` (``None``: the read
    failed, so every story is its id)."""
    by_id = {s.id: s for s in stories or ()}
    result = []
    for story in status.stories:
        listed = by_id.get(story.story_id)
        result.append(
            StoryRow(
                story_id=story.story_id,
                url=reverse("stories:detail", args=[story.story_id]) if listed else None,
                name=listed.name if listed else None,
                archived=bool(listed and listed.status == "archived"),
                status=story.status,
                done=story.done,
                total=story.total,
                error=story.error,
                fix=error_class_fix(story.error),
            )
        )
    return result


def _stories(request: HttpRequest, client: EngineClient) -> list[StorySummary] | None:
    try:
        return client.list_stories()
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


def read_run(
    request: HttpRequest, client: EngineClient, *, after: str | None, poll: bool = False
) -> Run:
    """One page of the run (``limit=100``) and one story-list read. A failed status read is the
    error panel in the section's place; while polling, no answer (or a 503) keeps polling."""
    try:
        status = client.get_reembed_status(limit=PAGE_SIZE, after=after)
    except EngineClientError as exc:
        if poll:
            failure = poll_failure(request, exc)
            if failure.retry:
                return Run(after=after, retrying=True)
            return Run(after=after, error=failure.panel or panel_for(exc))
        log_engine_error(request, exc)
        return Run(after=after, error=panel_for(exc))
    stories = _stories(request, client) if status.stories else []
    return Run(after=after, status=status, rows=rows(status, stories), counts=counts(status))


def run_url(after: str | None = None) -> str:
    url = reverse("models:reembed")
    return f"{url}?{urlencode({'after': after})}" if after else url

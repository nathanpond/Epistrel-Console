"""Shared lookups for M3 forms and pages (#64, M3 conventions 13 and 14).

- :func:`rating_choices` is the one content-rating select every M3 form uses (rating ids come
  only from it; a "default" option omits the field). One ``GET /admin/content-ratings`` per call;
  read it at most once per request and never cache it across requests.
- :func:`story_labels` names the stories a page mentions by id (at most ``cap`` reads, on the
  shared fan-out pool, ``console.engine.pool``).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from console.engine.client import EngineClient, EngineClientError, EngineError, EngineUnreachable
from console.engine.models import ContentRatingSummary
from console.engine.pool import fan_out
from console.entities import label

RETIRED_SUFFIX = " (retired)"
MISSING_SUFFIX = " (missing)"
STORY_LABEL_CAP = 25
ERASURE_PENDING = "erasure_pending"


def error_text(exc: EngineClientError) -> str:
    """``<status> <code>`` for an Engine error, ``unreachable (<reason>)`` without an answer."""
    if isinstance(exc, EngineError):
        return f"{exc.http_status} {exc.code}"
    if isinstance(exc, EngineUnreachable):
        return f"unreachable ({exc.reason})"
    return type(exc).__name__


@dataclass(frozen=True)
class RatingChoices:
    """A rating select's options, ``(rating id, label)`` in ``display_order``, or the read's
    ``error``. When ``error`` is set the form shows :attr:`unavailable_text` in place of the
    select (and offers no rating field)."""

    choices: list[tuple[str, str]]
    error: EngineClientError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def unavailable_text(self) -> str:
        if self.error is None:
            return ""
        return f"Ratings unavailable: {error_text(self.error)} — reload to retry"


def rating_choices(
    client: EngineClient, *, current: str | None = None, include_retired: bool = False
) -> RatingChoices:
    """The content-rating select's options, in ``display_order``, labelled by name.

    Non-retired ratings only, unless ``include_retired`` (list filters), where retired ones are
    labelled "<name> (retired)". ``current`` (the saved rating id) is always kept: as
    "<name> (retired)" when it is retired, as "<id> (missing)" when the Engine no longer lists
    it. One read (retired included, so a retired ``current`` keeps its name). A failed read
    returns its error and no choices; it never raises.
    """
    try:
        ratings: list[ContentRatingSummary] = client.list_content_ratings(include_retired=True)
    except EngineClientError as exc:
        return RatingChoices([], exc)
    ordered = sorted(ratings, key=lambda r: (r.display_order, r.name, r.id))
    choices: list[tuple[str, str]] = []
    for rating in ordered:
        if not rating.retired:
            choices.append((rating.id, rating.name))
        elif include_retired or rating.id == current:
            choices.append((rating.id, f"{rating.name}{RETIRED_SUFFIX}"))
    if current and current not in {rating_id for rating_id, _ in choices}:
        choices.append((current, f"{current}{MISSING_SUFFIX}"))
    return RatingChoices(choices)


@dataclass(frozen=True)
class StoryLabel:
    """How a page names one story: ``text`` is its name, "(unnamed) …<last 6>", "held …<last
    6>" (a story held by a pending erasure: 503 ``erasure_pending``) or, when the read failed or
    was not made (over the cap), the id itself with ``error`` saying why."""

    story_id: str
    text: str
    error: str | None = None
    held: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


def held_label(story_id: str) -> str:
    return f"held …{story_id[-6:]}"


def story_labels(
    client: EngineClient, story_ids: Iterable[str], *, cap: int = STORY_LABEL_CAP
) -> dict[str, StoryLabel]:
    """Labels for ``story_ids`` (de-duplicated, in order): one ``GET /stories/{id}`` each for
    the first ``cap``, run on the fan-out pool under its shared deadline; the rest are labelled
    by id with ``error="not looked up"``. Never raises for a failed read."""
    distinct = [story_id for story_id in dict.fromkeys(story_ids) if story_id]
    looked_up, skipped = distinct[:cap], distinct[cap:]
    results = fan_out(lambda story_id: client.get_story(story_id).name, looked_up)
    labels: dict[str, StoryLabel] = {}
    for story_id in looked_up:
        result = results[story_id]
        if isinstance(result, EngineError) and result.code == ERASURE_PENDING:
            labels[story_id] = StoryLabel(story_id, held_label(story_id), held=True)
        elif isinstance(result, EngineClientError):
            labels[story_id] = StoryLabel(story_id, story_id, error_text(result))
        else:
            labels[story_id] = StoryLabel(story_id, label(result, story_id))
    for story_id in skipped:
        labels[story_id] = StoryLabel(story_id, story_id, "not looked up")
    return labels

"""Shared lookups for M3 forms and pages (#64, M3 conventions 13 and 14).

- :func:`rating_choices` is the one content-rating select every M3 form uses (rating ids come
  only from it; a "default" option omits the field). One ``GET /admin/content-ratings`` per call;
  read it at most once per request and never cache it across requests.
- :func:`engine_profile_choices` is the engine-profile select (#68's recommendation): every
  non-deleted profile, labelled by name, following cursors up to :data:`PROFILES_MAX`.
- :func:`story_labels` names the stories a page mentions by id (at most ``cap`` reads, on the
  shared fan-out pool, ``console.engine.pool``).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from console.engine.client import EngineClient, EngineClientError, EngineError, EngineUnreachable
from console.engine.models import ContentRatingSummary, EngineProfileSummary
from console.engine.pool import fan_out
from console.entities import label

RETIRED_SUFFIX = " (retired)"
MISSING_SUFFIX = " (missing)"
DELETED_SUFFIX = " (deleted)"
PROFILES_MAX = 500  # "All X" selects stop here (#64 convention 6)
TOO_MANY = "Too many to list — enter the id"
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
class ProfileChoices:
    """An engine-profile select's options, ``(profile id, label)``, or why there is no select:
    the read's ``error``, or ``too_many`` (more than :data:`PROFILES_MAX` profiles). Either way
    the form offers a free-text id input with :attr:`unavailable_text` beside it. ``note``
    carries that text across a signed snapshot (the read is not repeated)."""

    choices: list[tuple[str, str]]
    error: EngineClientError | None = None
    too_many: bool = False
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and not self.too_many and not self.note

    @property
    def unavailable_text(self) -> str:
        if self.note:
            return self.note
        if self.error is not None:
            return f"Engine profiles unavailable: {error_text(self.error)} — enter the id"
        return TOO_MANY if self.too_many else ""


def engine_profile_choices(client: EngineClient, *, current: str | None = None) -> ProfileChoices:
    """The engine-profile select's options: every non-deleted profile, by name (then id), as
    the Engine lists them; ``current`` (the saved id) is kept as "<name> (deleted)" or "<id>
    (missing)". Reads ``GET /engine-profiles`` (deleted included, so a deleted ``current``
    keeps its name) page by page, stopping after :data:`PROFILES_MAX` profiles (``too_many``).
    Never raises."""
    found: list[EngineProfileSummary] = []
    cursor: str | None = None
    try:
        while True:
            items, cursor = client.list_engine_profiles(include_deleted=True, cursor=cursor)
            found.extend(items)
            if len(found) > PROFILES_MAX:
                return ProfileChoices([], too_many=True)
            if not cursor:
                break
    except EngineClientError as exc:
        return ProfileChoices([], exc)
    choices = [(p.id, p.name) for p in found if p.deleted_at is None]
    if current and current not in {profile_id for profile_id, _ in choices}:
        deleted = next((p for p in found if p.id == current), None)
        shown = f"{deleted.name}{DELETED_SUFFIX}" if deleted else f"{current}{MISSING_SUFFIX}"
        choices.append((current, shown))
    return ProfileChoices(choices)


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

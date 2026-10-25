"""An ``ImpactReport`` laid out for ``console/_impact_report.html`` (#75 owns the partial; #84
renders a profile change's per-story table, #85 a story's override change).

:func:`impact_rows` turns each affected story into an :class:`ImpactRow`: the story linked and
named (``story_labels``), capabilities gained and lost, the backfill ("—", "Needed (no
estimate)" or "N model calls, T tokens, ~D"), whether the upgrade is unavailable, the
fingerprints before and after (short in ``<code>``, the full value in ``title``) and the
quality status after. The partial reads them through ``{% impact_rows impact story_labels as
rows %}`` (``{% load impact %}``).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse

from console.engine.choices import StoryLabel
from console.engine.models import AffectedStory, Backfill, ImpactReport

EMPTY = "—"
FINGERPRINT_SHOWN = 12
FINGERPRINT_PREFIX = "sha256:"
QUALITY_CLASSES = {"certified": "quality-certified", "stale": "quality-stale"}


def duration_text(seconds: float) -> str:
    """ "D s" below a minute, else "Xm Ys"."""
    whole = round(seconds)
    if whole < 60:
        return f"{whole} s"
    return f"{whole // 60}m {whole % 60}s"


def backfill_text(backfill: Backfill) -> str:
    """Not needed "—"; needed "N model calls, T tokens, ~D"; needed without an estimate
    "Needed (no estimate)"."""
    if not backfill.needed:
        return EMPTY
    estimate = backfill.estimate
    if estimate is None:
        return "Needed (no estimate)"
    return (
        f"{estimate.model_calls:,} model calls, {estimate.tokens:,} tokens, "
        f"~{duration_text(estimate.duration_s)}"
    )


def short_fingerprint(value: str | None) -> str:
    """The first 12 hex digits after ``sha256:`` (the Engine's fingerprint form, #85)."""
    if not value:
        return ""
    return value.removeprefix(FINGERPRINT_PREFIX)[:FINGERPRINT_SHOWN]


def quality_class(status: str) -> str:
    """A quality status badge's class (#85): ``certified`` green, ``stale`` amber, anything else
    (``uncertified``) the plain grey ``quality``."""
    return QUALITY_CLASSES.get(status, "")


def capabilities_text(values: Sequence[str] | None) -> list[str]:
    return list(values or [])


@dataclass(frozen=True)
class ImpactRow:
    story_id: str
    url: str
    name: str
    note: str
    gained: list[str]
    lost: list[str]
    backfill: str
    upgrade_unavailable: bool
    fingerprint_before: str | None
    fingerprint_after: str | None
    quality_after: str

    @property
    def before_short(self) -> str:
        return short_fingerprint(self.fingerprint_before)

    @property
    def after_short(self) -> str:
        return short_fingerprint(self.fingerprint_after)


def _story_url(story_id: str) -> str:
    try:
        return reverse("stories:detail", args=[story_id])
    except NoReverseMatch:
        return ""


def impact_row(story: AffectedStory, labels: Mapping[str, StoryLabel]) -> ImpactRow:
    label = labels.get(story.story_id)
    named = label is not None and label.ok and label.text != story.story_id
    return ImpactRow(
        story.story_id,
        _story_url(story.story_id),
        label.text if named and label is not None else "",
        (label.error or "") if label is not None else "",
        list(story.capabilities_gained),
        list(story.capabilities_lost),
        backfill_text(story.backfill),
        story.upgrade_unavailable,
        story.fingerprint_before,
        story.fingerprint_after,
        story.quality_status_after.status if story.quality_status_after else "",
    )


def impact_rows(
    impact: ImpactReport, labels: Mapping[str, StoryLabel] | None = None
) -> list[ImpactRow]:
    return [impact_row(s, labels or {}) for s in impact.affected_stories]


def more_count(impact: ImpactReport) -> int:
    """How many affected stories the report counts but does not list."""
    return max(impact.affected_count - len(impact.affected_stories), 0)

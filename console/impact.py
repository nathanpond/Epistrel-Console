"""An ``ImpactReport`` laid out for ``console/_impact_report.html`` (#75 owns the partial; #84
renders a profile change's per-story table, #85 a story's override change).

:func:`impact_rows` turns each affected story into an :class:`ImpactRow`: the story linked and
named (``story_labels``), capabilities gained and lost, the backfill ("—", "Needed (no
estimate)" or "N model calls, T tokens, ~D"), whether the upgrade is unavailable, the
fingerprints before and after (short in ``<code>``, the full value in ``title``) and the
quality status after. The partial reads them through ``{% impact_rows impact story_labels as
rows %}`` (``{% load impact %}``).

#310 (E8) adds the M7 members: each row's upgrade mode and queue position (the "Mode" column,
:attr:`ImpactRow.mode_cell`), its forward-only gap and dormant data (the "Gap" column,
:attr:`ImpactRow.gap_cell`) and the Backfill cell's turn range; and the report-level
:func:`certifications_lost` rows (``{% certifications_lost impact story_labels as lost %}``),
rendered only when the report lists any. #311 and #320 reuse them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse

from console.engine.choices import StoryLabel
from console.engine.models import (
    AffectedStory,
    Backfill,
    CertificationLost,
    ForwardOnlyGap,
    ImpactReport,
    TurnSpan,
)
from console.upgrades import upgrade_mode_text

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


def turns_text(span: TurnSpan | None) -> str:
    """ "turns 1\u20133", "turn 3" for a one-turn span, "" with no span (E23's ``{:,}``)."""
    if span is None:
        return ""
    if span.from_turn == span.to_turn:
        return f"turn {span.from_turn:,}"
    return f"turns {span.from_turn:,}–{span.to_turn:,}"  # noqa: RUF001


def backfill_cell(backfill: Backfill) -> str:
    """:func:`backfill_text`, plus " (turns a\u2013b)" when a needed backfill has a range."""
    text = backfill_text(backfill)
    if backfill.needed and backfill.range is not None:
        return f"{text} ({turns_text(backfill.range)})"
    return text


def gap_text(gap: ForwardOnlyGap | None) -> str:
    """ "No data for <capabilities> through turn <n>" (the capabilities ", "-joined), else ""."""
    if gap is None:
        return ""
    capabilities = ", ".join(gap.capabilities) or EMPTY
    return f"No data for {capabilities} through turn {gap.through_turn:,}"


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
    # #310 (E8): M7's per-story members, defaulted so positional construction keeps working.
    upgrade_mode_text: str = ""
    queue_position: int | None = None
    gap_text: str = ""
    dormant: bool = False

    @property
    def mode_cell(self) -> str:
        """ "Backfill", "Backfill · queue #2", "— · queue #2" or "—"."""
        mode = self.upgrade_mode_text or EMPTY
        if self.queue_position is not None:
            return f"{mode} · queue #{self.queue_position:,}"
        return mode

    @property
    def gap_cell(self) -> str:
        """The gap text (else "—"), plus " · data kept dormant"."""
        text = self.gap_text or EMPTY
        return f"{text} · data kept dormant" if self.dormant else text

    @property
    def before_short(self) -> str:
        return short_fingerprint(self.fingerprint_before)

    @property
    def after_short(self) -> str:
        return short_fingerprint(self.fingerprint_after)


def story_url(story_id: str) -> str:
    """``stories:detail`` for ``story_id``; "" when it does not reverse."""
    try:
        return reverse("stories:detail", args=[story_id])
    except NoReverseMatch:
        return ""


def impact_row(story: AffectedStory, labels: Mapping[str, StoryLabel]) -> ImpactRow:
    label = labels.get(story.story_id)
    named = label is not None and label.ok and label.text != story.story_id
    return ImpactRow(
        story.story_id,
        story_url(story.story_id),
        label.text if named and label is not None else "",
        (label.error or "") if label is not None else "",
        list(story.capabilities_gained),
        list(story.capabilities_lost),
        backfill_cell(story.backfill),
        story.upgrade_unavailable,
        story.fingerprint_before,
        story.fingerprint_after,
        story.quality_status_after.status if story.quality_status_after else "",
        upgrade_mode_text(story.upgrade_mode),
        story.queue_position,
        gap_text(story.forward_only_gap),
        story.data_kept_dormant,
    )


def impact_rows(
    impact: ImpactReport, labels: Mapping[str, StoryLabel] | None = None
) -> list[ImpactRow]:
    return [impact_row(s, labels or {}) for s in impact.affected_stories]


@dataclass(frozen=True)
class StatusText:
    """A quality status as E13's text, with the raw value for ``title`` (E23's enum rule)."""

    text: str
    raw: str


@dataclass(frozen=True)
class CertificationLostRow:
    """One ``CertificationLost``: the story (as the per-story table names it; ``story_id`` ""
    for the profile's own configuration), its status before → after, and the certification."""

    story_id: str
    url: str
    name: str
    note: str
    before: StatusText
    after: StatusText
    certification_id: str
    certification_url: str | None


def status_text(value: str) -> StatusText:
    """``QUALITY_TEXT[value]``; a value it lacks humanised (E23), "—" when blank."""
    from console.certification import QUALITY_TEXT  # certification → impact
    from console.wording import humanised

    if not value:
        return StatusText(EMPTY, "")
    return StatusText(QUALITY_TEXT.get(value) or humanised(value), value)


def certification_lost_row(
    lost: CertificationLost, labels: Mapping[str, StoryLabel]
) -> CertificationLostRow:
    from console.certification import certification_url  # certification → impact

    story_id = lost.story_id or ""
    label = labels.get(story_id) if story_id else None
    named = label is not None and label.ok and label.text != story_id
    return CertificationLostRow(
        story_id,
        story_url(story_id) if story_id else "",
        label.text if named and label is not None else "",
        (label.error or "") if label is not None else "",
        status_text(lost.from_status),
        status_text(lost.to_status),
        lost.certification_id,
        certification_url(lost.certification_id),
    )


def certifications_lost(
    impact: ImpactReport, labels: Mapping[str, StoryLabel] | None = None
) -> list[CertificationLostRow]:
    """The report's ``certifications_lost`` as rows, in the Engine's order ([] for none)."""
    return [certification_lost_row(c, labels or {}) for c in impact.certifications_lost]


def report_story_ids(impact: ImpactReport) -> list[str]:
    """Every story id the report names (the per-story table's, then the certifications
    lost's), de-duplicated in order: the one ``story_labels`` call's ids."""
    ids = [s.story_id for s in impact.affected_stories]
    ids += [c.story_id for c in impact.certifications_lost if c.story_id]
    return list(dict.fromkeys(ids))


def more_count(impact: ImpactReport) -> int:
    """How many affected stories the report counts but does not list."""
    return max(impact.affected_count - len(impact.affected_stories), 0)

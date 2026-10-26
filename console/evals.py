"""The eval reports page (#149): escalation yield and claim detection's shadow report.

Each section is a plain GET form with its own params: the yield report's ``y_from`` / ``y_to``,
the shadow report's ``s_from`` / ``s_to`` / ``s_story`` and its cursor ``s_after``. A
``datetime-local`` input is prefilled with the last 7 days (UTC, to the minute) only when its
key is absent; a key present but empty stays blank and is left out of the call (the Engine's
30-day default). A typed value is ``YYYY-MM-DDTHH:MM`` (optional ``:SS``) read as UTC; anything
else is the section's form error and makes no call for that report.

Each form carries the other section's params as hidden inputs exactly as submitted, so applying
one keeps the other's window; the yield form also keeps ``s_after``, the shadow form never sends
it (a changed shadow window or story starts at the first page). "Last 7 days" drops a section's
params and keeps the other's.

Rows and texts follow #135's conventions: counts with thousands separators, rates one decimal
with ``%``, gate reasons, statuses and miss categories humanised with the raw value in
``title``, a shape as its raw key, ids as short ids (convention 2), turns linked into history
(convention 3) and operation ids linked to #44 when the Console logged them.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from django.http import QueryDict
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.engine.client import EngineError
from console.engine.models import (
    EscalationYield,
    ExtractionShadow,
    ShadowSample,
    StorySummary,
    YieldCluster,
)
from console.engine_errors import ErrorPanel
from console.forms.inspect import local_moment
from console.history import operation_links
from console.inspect import history_turn_url, short_id
from console.retrieval import humanised
from console.templatetags.console_format import percent

Y_FROM, Y_TO = "y_from", "y_to"
S_FROM, S_TO, S_STORY, S_AFTER = "s_from", "s_to", "s_story", "s_after"
YIELD_KEYS = (Y_FROM, Y_TO)
SHADOW_KEYS = (S_FROM, S_TO, S_STORY, S_AFTER)

DEFAULT_SPAN = timedelta(days=7)
LOCAL_FORMAT = "%Y-%m-%dT%H:%M"  # a datetime-local input's value, to the minute
SAMPLE_LIMIT = 100  # convention 6 (the route allows 200)

DATETIME_INVALID = "Enter a date and time (YYYY-MM-DDTHH:MM)"
BLANK_HINT = "Blank fields use the Engine's default: 30 days up to the end, or up to now"
NO_CLAIMS = "No claims checked in this window"
NO_EXTRACTED = "no claims extracted in this window"
NO_SAMPLES = "No samples in this window — shadow sampling may be off, or nothing was sampled yet."
STORIES_UNAVAILABLE = "Story list unavailable — enter an id"
EVAL_RUNS_ISSUE = "https://github.com/nathanpond/Epistrel/issues/532"
SHAPE_TITLE = "layer|cardinality|temporal|polarity"
EMPTY = "—"

FROM_FIELDS = frozenset({"from", "query.from"})
TO_FIELDS = frozenset({"to", "query.to"})


# --- numbers -------------------------------------------------------------------------------------
# Rates use ``console_format.percent`` (convention 5's one decimal with ``%``).


def counted(value: int) -> str:
    """Convention 5's count with thousands separators."""
    return f"{value:,}"


# --- the window inputs ---------------------------------------------------------------------------


@dataclass
class TimeInput:
    """One ``datetime-local`` input: its param name, the value it shows, the moment it sends
    (``None`` when blank or invalid) and its errors."""

    name: str
    label: str
    value: str
    moment: datetime | None = None
    errors: list[str] = field(default_factory=list)


def time_input(params: QueryDict, name: str, label: str, default: datetime) -> TimeInput:
    """The input for ``name``: ``default`` when the key is absent, blank when present but
    empty, else the typed value (the form error when it doesn't parse)."""
    value = default.strftime(LOCAL_FORMAT) if name not in params else params.get(name, "").strip()
    if not value:
        return TimeInput(name, label, "")
    moment = local_moment(value)
    if moment is None:
        return TimeInput(name, label, value, errors=[DATETIME_INVALID])
    return TimeInput(name, label, value, moment)


@dataclass
class Window:
    """A section's ``from`` / ``to`` inputs plus the errors shown above its form."""

    start: TimeInput
    end: TimeInput
    errors: list[str] = field(default_factory=list)

    @property
    def inputs(self) -> tuple[TimeInput, TimeInput]:
        return self.start, self.end

    @property
    def valid(self) -> bool:
        return not (self.start.errors or self.end.errors)

    def apply_engine_error(self, exc: EngineError) -> None:
        """A 422 on the input ``details.field`` names (``from``/``query.from``,
        ``to``/``query.to``), else above the form; the Engine's message verbatim."""
        named = exc.details.get("field")
        if named in FROM_FIELDS:
            self.start.errors.append(exc.message)
        elif named in TO_FIELDS:
            self.end.errors.append(exc.message)
        else:
            self.errors.append(exc.message)


def window(params: QueryDict, keys: tuple[str, str], now: datetime) -> Window:
    """The section's window inputs, prefilled with 7 days before ``now`` .. ``now``."""
    start, end = keys
    return Window(
        time_input(params, start, "From", now - DEFAULT_SPAN), time_input(params, end, "To", now)
    )


def default_now(moment: datetime) -> datetime:
    """The prefill's "now": UTC, truncated to the minute."""
    return moment.astimezone(UTC).replace(second=0, microsecond=0)


def carried(params: QueryDict, keys: Iterable[str]) -> list[tuple[str, str]]:
    """The hidden inputs that carry ``keys`` as submitted (an empty value stays empty; an absent
    key is not carried, so it stays prefilled)."""
    return [(key, params.get(key, "")) for key in keys if key in params]


def without(params: QueryDict, keys: Iterable[str]) -> str:
    """The page's query string without ``keys`` ("Last 7 days", "First page")."""
    kept = params.copy()
    for key in keys:
        kept.pop(key, None)
    encoded = kept.urlencode()
    return f"?{encoded}" if encoded else "?"


def window_text(start: datetime, end: datetime, *, inclusive: bool) -> SafeString:
    """The window the Engine used: "Window 2026-10-17 14:03 UTC \N{EN DASH} 2026-10-24 14:03 UTC,
    end exclusive" (each bound in ``<time>`` with its ISO value in ``title``)."""
    return format_html(
        "Window {} \u2013 {}, end {}",
        _moment(start),
        _moment(end),
        "inclusive" if inclusive else "exclusive",
    )


def _moment(value: datetime) -> SafeString:
    moment = value.astimezone(UTC)
    shown = moment.strftime(
        "%Y-%m-%d %H:%M UTC"
        if not (moment.second or moment.microsecond)
        else "%Y-%m-%d %H:%M:%S UTC"
    )
    raw = value.isoformat()
    return format_html('<time datetime="{}" title="{}">{}</time>', raw, raw, shown)


# --- escalation yield ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterTable:
    """One cluster table: which key columns it shows, its rows in the Engine's order, and the
    ``totals`` cluster for the closing row."""

    key: str
    title: str
    by_reason: bool
    by_shape: bool
    rows: Sequence[YieldCluster]
    totals: YieldCluster


def yield_summary(report: EscalationYield) -> str:
    """ "312 claims, 41 escalated, rate 13.1% · reference 20.0%, not a gate"; a ``None`` rate
    keeps the counts and reads :data:`NO_CLAIMS` in its place."""
    counts = f"{counted(report.claims)} claims, {counted(report.escalated)} escalated"
    reference = (
        f"reference {percent(report.reference_baseline)}, "
        f"{'gate' if report.is_gate else 'not a gate'}"
    )
    if report.escalation_rate is None:
        return f"{counts} · {NO_CLAIMS} · {reference}"
    return f"{counts}, rate {percent(report.escalation_rate)} · {reference}"


def cluster_tables(report: EscalationYield) -> list[ClusterTable]:
    return [
        ClusterTable(
            "by-gate-reason", "By gate reason", True, False, report.by_gate_reason, report.totals
        ),
        ClusterTable("by-shape", "By predicate shape", False, True, report.by_shape, report.totals),
        ClusterTable(
            "by-gate-reason-shape",
            "By gate reason and shape",
            True,
            True,
            report.by_gate_reason_shape,
            report.totals,
        ),
    ]


# --- the shadow report ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Category:
    """A miss category: raw key (``title``), humanised label, count."""

    raw: str
    label: str
    count: int


def categories(counts: Mapping[str, int]) -> list[Category]:
    """Count descending, then name."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [Category(raw, humanised(raw), n) for raw, n in ordered]


def misses_text(counts: Mapping[str, int]) -> str:
    """A sample's misses by category, "Age \N{MULTIPLICATION SIGN}2, Place
    \N{MULTIPLICATION SIGN}1"; "—" when none."""
    shown = categories(counts)
    return ", ".join(f"{c.label} \u00d7{c.count}" for c in shown) if shown else EMPTY


def shadow_summary(report: ExtractionShadow) -> str:
    """The whole window's counts (not the page's): "12 sampled, 1 failed (extraction call
    failed) · 340 claims detected, 298 checked · shadow extraction made 55 claims, 4 missed,
    miss rate 7.3%"."""
    rate = NO_EXTRACTED if report.miss_rate is None else f"miss rate {percent(report.miss_rate)}"
    return (
        f"{counted(report.samples)} sampled, {counted(report.failed)} failed "
        f"(extraction call failed) · {counted(report.detected_total)} claims detected, "
        f"{counted(report.checked_total)} checked · shadow extraction made "
        f"{counted(report.extraction_claims)} claims, {counted(report.missed)} missed, {rate}"
    )


def story_label(story: StorySummary) -> str:
    """ "Name (…last 8)"; the short id alone for a story without a name (held by erasure)."""
    return f"{story.name} ({short_id(story.id)})" if story.name else short_id(story.id)


@dataclass(frozen=True)
class StoryOption:
    value: str
    label: str
    selected: bool


def story_options(stories: Sequence[StorySummary], selected: str) -> list[StoryOption]:
    """ "All stories" first, then the Engine's newest-first order, archived ones suffixed
    "(archived)". A selected id not in the list (a bookmark, a free-text id) gets its own
    option, so it is still sent."""
    options = [StoryOption("", "All stories", not selected)]
    for story in stories:
        label = story_label(story) + (" (archived)" if story.status == "archived" else "")
        options.append(StoryOption(story.id, label, story.id == selected))
    if selected and all(story.id != selected for story in stories):
        options.append(StoryOption(selected, f"{short_id(selected)} (not in the list)", True))
    return options


@dataclass(frozen=True)
class SampleRow:
    """One sample's row: its story's label and link (``None`` when the story isn't in the
    list), the turn's history link, the operation's #44 row and the texts."""

    sample: ShadowSample
    story_label: str | None
    story_url: str | None
    turn_url: str | None
    operation_pk: int | None
    status_label: str
    misses: str

    @property
    def failed(self) -> bool:
        return self.sample.status == "failed"


def sample_rows(
    items: Sequence[ShadowSample], stories: Sequence[StorySummary] | None
) -> list[SampleRow]:
    """The page's rows in the Engine's order. ``operation_links`` runs once per distinct story
    (it takes one story)."""
    known = {story.id: story for story in stories or ()}
    links: dict[str, int] = {}
    by_story: dict[str, set[str]] = {}
    for sample in items:
        by_story.setdefault(sample.story_id, set()).add(sample.operation_id)
    for story_id, operation_ids in by_story.items():
        links.update(operation_links(story_id, operation_ids))
    rows = []
    for sample in items:
        story = known.get(sample.story_id)
        turn = sample.narrative_turn
        rows.append(
            SampleRow(
                sample=sample,
                story_label=story_label(story) if story is not None else None,
                story_url=reverse("stories:detail", args=[story.id]) if story else None,
                turn_url=history_turn_url(sample.story_id, turn) if turn is not None else None,
                operation_pk=links.get(sample.operation_id),
                status_label=humanised(sample.status),
                misses=misses_text(sample.missed_by_category),
            )
        )
    return rows


def paging(
    params: QueryDict, shadow: Window | None, next_cursor: str | None
) -> dict[str, str | bool]:
    """Convention 6 over ``s_after``: "First page" keeps every other param; "Next page" also
    pins the shadow window to the values this page sent, so a prefilled "now" doesn't move
    between pages. "First page" shows whenever this page was reached by a cursor (a refused
    cursor's 422 included)."""
    first = without(params, (S_AFTER,))
    following = ""
    if next_cursor and shadow is not None:
        after = params.copy()
        for box in (shadow.start, shadow.end):
            after[box.name] = box.value
        after[S_AFTER] = next_cursor
        following = f"?{after.urlencode()}"
    return {"first_url": first, "next_url": following, "paged": bool(params.get(S_AFTER))}


# --- sections ------------------------------------------------------------------------------------


@dataclass
class YieldSection:
    window: Window
    hidden: list[tuple[str, str]]
    last_days_url: str
    report: EscalationYield | None = None
    error: ErrorPanel | None = None

    @property
    def summary(self) -> str:
        return yield_summary(self.report) if self.report is not None else ""

    @property
    def tables(self) -> list[ClusterTable]:
        return cluster_tables(self.report) if self.report is not None else []

    @property
    def window_text(self) -> SafeString | str:
        if self.report is None:
            return ""
        return window_text(self.report.window_from, self.report.window_to, inclusive=False)


@dataclass
class ShadowSection:
    window: Window
    hidden: list[tuple[str, str]]
    last_days_url: str
    story: str
    after: str | None
    options: list[StoryOption] | None  # None: the story list read failed (free-text field)
    report: ExtractionShadow | None = None
    error: ErrorPanel | None = None
    rows: list[SampleRow] = field(default_factory=list)
    pages: Mapping[str, str | bool] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        return shadow_summary(self.report) if self.report is not None else ""

    @property
    def categories(self) -> list[Category]:
        return categories(self.report.missed_by_category) if self.report is not None else []

    @property
    def window_text(self) -> SafeString | str:
        if self.report is None:
            return ""
        return window_text(self.report.window_from, self.report.window_to, inclusive=True)


def yield_section(params: QueryDict, now: datetime) -> YieldSection:
    return YieldSection(
        window=window(params, YIELD_KEYS, now),
        hidden=carried(params, SHADOW_KEYS),
        last_days_url=without(params, YIELD_KEYS),
    )


def shadow_section(
    params: QueryDict, now: datetime, stories: Sequence[StorySummary] | None
) -> ShadowSection:
    story = params.get(S_STORY, "").strip()
    return ShadowSection(
        window=window(params, (S_FROM, S_TO), now),
        hidden=carried(params, YIELD_KEYS),
        last_days_url=without(params, SHADOW_KEYS),
        story=story,
        after=params.get(S_AFTER) or None,
        options=story_options(stories, story) if stories is not None else None,
        pages=paging(params, None, None),
    )

"""The story's Prune page (#317, FR-STORE-19, FR-STORE-32, ADR-175): its wording and its views of
the Engine's dry-run report, past prunings and an applied pruning.

**E1 — the import rule.** The prune page's wording lives here (page-specific, like #249's
``console/reconciliation.py``); ``console/views/prune.py`` imports it. ``prune_url`` (E6) is
``console.portability``'s and the typed-name mechanics (E9) are ``console.confirm``'s; neither is
redefined here. E23's renderers come from ``console/raw.py``.

**The report** (:func:`impact_view` → :class:`PruneImpactView`, rendered by
``templates/console/stories/_prune_impact.html``): the dry run's revision line, the two target
tables in the order "Discarded tails" then "Dormant continuations" (a ``to_delete`` key beyond
the two renders as an extra humanised table after them), the operations no longer possible
(:data:`LOST_TEXT`), what is kept, the targets left out (:data:`EXCLUDED_TEXT`), the "keep it
first" lines (:data:`KEEP_FIRST_TEXT`, linked to History's Variants, where Fork lives) and the
E8 token :func:`twostage.sign("prune", …) <console.twostage.sign>` carrying exactly the targets
listed (``None`` past the schema's 200, the expected revision still pinning the story). Entries
are raw (E15): a malformed position reads "—", an unknown operation, reason or key is humanised.

**Past prunings** (:func:`run_rows`, :func:`impact_counts`): one row per parsed ``PruneRun`` in
the Engine's order, with D15's notice and rejected rows for those that didn't parse.

**The confirm page** (:func:`confirm_page`): E9's typed-name ``ConfirmPage`` built from the
token's ``base`` (the story's name at the dry run, the revision and the counts), so opening it
makes no Engine call.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from django.core import signing
from django.urls import reverse

from console import twostage
from console.confirm import ConfirmPage, typed_name_for
from console.engine.models import Pruned, PruneRun, PruneRuns, rejected_entries
from console.raw import EMPTY, compact_json, plural
from console.rewrites import amendment_url
from console.wording import humanised

KIND: Final = "prune"  # E8's TWOSTAGE_KINDS entry
LABEL: Final = "Prune"
MAX_TARGETS: Final = 200  # the schema's ``targets.maxItems``

# --- wording --------------------------------------------------------------------------------------

DRY_RUN_LINE: Final = "A dry run shows what pruning would delete and changes nothing"
DRY_RUN_LABEL: Final = "Run a dry run"
FULL_DRY_RUN_LABEL: Final = "Run a full dry run again"
NARROW_LABEL: Final = "Dry run with only the ticked"
PRUNE_LABEL: Final = "Prune these… (next: confirm)"
PRUNE_ALL_NOTE: Final = "Prunes every target listed above; untick and re-run the dry run to narrow"
NOTHING_TO_PRUNE: Final = "Nothing to prune — no discarded tail or dormant continuation is eligible"
REPLANNED_TEXT: Final = "The Engine didn't prune — review this dry run and confirm again"
RERUN_TEXT: Final = "Run the dry run again to see what changed"
TICK_AT_LEAST_ONE: Final = "Tick at least one target"
TICK_AT_MOST: Final = f"Tick at most {MAX_TARGETS} targets"
TARGET_GONE_TEXT: Final = (
    "One of those targets can't be pruned any more ({reason}) — run the dry run again"
)
TARGET_GONE_REASON: Final = "it no longer exists"
TARGET_CODES: Final = frozenset({"prune_target_unknown", "prune_target_ineligible"})
DRY_RUN_TEXT: Final = "Dry run at story revision {revision} — nothing was changed"
NEVER_PRUNED: Final = "This story has never been pruned."
VERB_PHRASE: Final = "prune the story"  # C5's verb phrase

# The policy line (from the frame's ``Story.pruning``).
POLICY_PREFIX: Final = "Automatic pruning: "
POLICY_OFF: Final = "off"
POLICY_ON: Final = "on"  # a present but malformed policy (no int ``older_than_days``)
POLICY_OLDER: Final = "older than {days}"
NEVER_PRUNED_SHORT: Final = "Never pruned"
SETTINGS_LINK: Final = "Change in Settings"

LOST_TEXT: Final[Mapping[str, str]] = {
    "untruncate": "Undo the rewind that discarded {range}",
    "swap": "Swap back to {range}",
    "promote": "Fork {range} into its own chain",
}
EXCLUDED_TEXT: Final[Mapping[str, str]] = {
    "amendment_dependency": "An amendment names its records",
    "settlement_dependency": "An active change settles one of its State changes",
    "in_flight": "A turn is writing to it",
}
KEEP_FIRST_TEXT: Final = "To keep {range}, fork it before pruning"
KEEP_FIRST_CODE: Final = "promote_before_pruning"

BUCKETS: Final = (
    ("discarded_tails", "Discarded tails"),
    ("dormant_continuations", "Dormant continuations"),
)
# ``retained`` keys: (singular, plural, suffix).
RETAINED_TEXT: Final[Mapping[str, tuple[str, str, str]]] = {
    "audit_events": ("audit event", "audit events", ""),
    "outbox_rows": ("outbox row", "outbox rows", ""),
    "feedback_marked": ("rating", "ratings", " (marked pruned)"),
}

# The confirm page (E9).
CONFIRM_TITLE: Final = "Prune"
CONFIRM_QUESTION: Final = "Prune {name}?"
CONFIRM_LABEL: Final = "Prune permanently"
CONFIRM_DELETES: Final = (
    "This permanently deletes the records of {tails} and {dormant} ({operations})."
)
CONFIRM_MARKERS: Final = (
    "Their operations stay as markers; undoing those rewinds, swapping back to them or forking "
    "them stops being possible."
)
CONFIRM_KEPT: Final = (
    "Audit and amendment events, outbox rows and outcomes, the erasure ledger and ratings are kept."
)
CONFIRM_EVERY: Final = (
    f"More than {MAX_TARGETS} targets: every eligible one at revision {{revision}} is pruned"
)

# The result.
PRUNED_LINE: Final = "Pruned: {tails} and {dormant}"
DELETED_NOTHING: Final = "nothing"
REVISION_NOW: Final = "Story revision now {revision}"

# Past prunings.
TRIGGER_MANUAL: Final = "Manual"
TRIGGER_POLICY: Final = "By policy"
TRIGGER_POLICY_DAYS: Final = "By policy (older than {days})"

REPORT_SALT: Final = "console.pruning.report"


# --- small readers --------------------------------------------------------------------------------


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _days(policy: object) -> int | None:
    return _int(policy.get("older_than_days")) if isinstance(policy, Mapping) else None


def tails_text(n: int) -> str:
    return plural(n, "discarded tail")


def dormant_text(n: int) -> str:
    return plural(n, "dormant continuation")


def _position(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    turn, variant = _int(value.get("turn")), _int(value.get("variant"))
    if turn is None or variant is None:
        return None
    return f"turn {turn:,} · variant {variant:,}"


def range_text(entry: object) -> str:
    """C1's position text for a raw ``PruneTarget``: "turn 3 · variant 1 → turn 4 · variant 1";
    a malformed position (or entry) reads "—"."""
    if not isinstance(entry, Mapping):
        return EMPTY
    start, end = _position(entry.get("from")), _position(entry.get("to"))
    if start is None or end is None:
        return EMPTY
    return f"{start} → {end}"


def _id(entry: object) -> str:
    value = entry.get("id") if isinstance(entry, Mapping) else None
    return value if isinstance(value, str) else ""


# --- the policy line ------------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyLine:
    """The Prune page's first line: the policy text, the next run and last prune (raw times, for
    ``engine_time``), and the raw policy under More when it is malformed."""

    text: str
    next_run_at: str | None = None
    last_prune_at: str | None = None
    show_last: bool = True  # "Last pruned …" / "Never pruned"; off when ``pruning`` is absent
    raw_policy: Any = None
    malformed: bool = False


def policy_line(pruning: Any) -> PolicyLine:
    """``Story.pruning`` (a :class:`~console.engine.models.StoryPruning`, or ``None``) as the
    policy line."""
    if pruning is None:
        return PolicyLine(POLICY_PREFIX + EMPTY, show_last=False)
    policy = pruning.policy
    last = pruning.last_prune_at or None
    if policy is None:
        return PolicyLine(POLICY_PREFIX + POLICY_OFF, last_prune_at=last)
    days = _days(policy)
    if days is None:
        return PolicyLine(
            POLICY_PREFIX + POLICY_ON, last_prune_at=last, raw_policy=policy, malformed=True
        )
    text = POLICY_PREFIX + POLICY_OLDER.format(days=plural(days, "day"))
    return PolicyLine(text, next_run_at=pruning.next_run_at or None, last_prune_at=last)


# --- the report -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetRow:
    """One target a pruning would delete: its id (``""`` when the entry has none, then no
    checkbox), its range, its operations and when it was left behind (raw), the raw entry."""

    id: str
    range_text: str
    operations_text: str
    flipped_at: str | None
    raw: Any


@dataclass(frozen=True)
class Bucket:
    key: str
    heading: str
    rows: tuple[TargetRow, ...]

    @property
    def title(self) -> str:
        return f"{self.heading} ({len(self.rows):,})"


@dataclass(frozen=True)
class Line:
    """A sentence, its optional link, and the raw entry for More."""

    text: str
    url: str | None = None
    raw: Any = None


@dataclass(frozen=True)
class AmendmentRef:
    id: str
    url: str | None


@dataclass(frozen=True)
class ExcludedRow:
    target: str
    text: str
    reason: str
    known: bool
    amendments: tuple[AmendmentRef, ...] = ()
    change_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PruneImpactView:
    """What ``_prune_impact.html`` renders for one dry run."""

    revision: int
    revision_text: str
    buckets: tuple[Bucket, ...]
    lost: tuple[Line, ...]
    kept_text: str
    excluded: tuple[ExcludedRow, ...]
    keep_first: tuple[Line, ...]
    target_ids: tuple[str, ...]
    counts: Mapping[str, int]
    token: str
    report_token: str
    every_eligible: bool = False  # past MAX_TARGETS: the token carries ``targets: None``

    @property
    def nothing_to_prune(self) -> bool:
        return not any(bucket.rows for bucket in self.buckets)


def _target_row(entry: object) -> TargetRow:
    raw = entry.get("operations") if isinstance(entry, Mapping) else None
    operations = _int(raw)
    flipped = entry.get("flipped_at") if isinstance(entry, Mapping) else None
    return TargetRow(
        id=_id(entry),
        range_text=range_text(entry),
        operations_text=plural(operations, "operation") if operations is not None else EMPTY,
        flipped_at=flipped if isinstance(flipped, str) and flipped else None,
        raw=entry,
    )


def buckets(to_delete: Mapping[str, Any]) -> tuple[Bucket, ...]:
    """The two known groups first (always, an empty one reading "None"), then any other group
    the Engine sent, humanised, in its order."""
    known = dict(BUCKETS)
    order = [key for key, _ in BUCKETS] + [k for k in to_delete if k not in known]
    out = []
    for key in order:
        items = to_delete.get(key)
        rows = tuple(_target_row(entry) for entry in items) if isinstance(items, list) else ()
        out.append(Bucket(key, known.get(key) or humanised(key), rows))
    return tuple(out)


def _lost_line(entry: object, ranges: Mapping[str, str]) -> Line:
    if not isinstance(entry, Mapping):
        return Line(compact_json(entry), raw=entry)
    operation = entry.get("operation")
    target = entry.get("target")
    shown = ranges.get(target, target) if isinstance(target, str) and target else EMPTY
    template = LOST_TEXT.get(operation) if isinstance(operation, str) else None
    if template is not None:
        return Line(template.format(range=shown), raw=entry)
    name = humanised(operation) if isinstance(operation, str) and operation else EMPTY
    return Line(f"{name}: {shown}", raw=entry)


def kept_text(retained: Mapping[str, Any]) -> str:
    """ "4 audit events, 1 outbox row, 2 ratings (marked pruned)", in the Engine's key order; an
    unknown key is humanised with its count."""
    parts = []
    for key, value in retained.items():
        n = _int(value)
        count = f"{n:,}" if n is not None else compact_json(value)
        if key in RETAINED_TEXT and n is not None:
            singular, many, suffix = RETAINED_TEXT[key]
            parts.append(plural(n, singular, many) + suffix)
        else:
            parts.append(f"{humanised(str(key))} {count}")
    return ", ".join(parts) or EMPTY


def _excluded_row(story_id: str, entry: Any) -> ExcludedRow:
    reason = entry.reason
    text = EXCLUDED_TEXT.get(reason)
    amendments: tuple[AmendmentRef, ...] = ()
    changes: tuple[str, ...] = ()
    if reason == "amendment_dependency":
        amendments = tuple(AmendmentRef(a, amendment_url(story_id, a)) for a in entry.amendment_ids)
    if reason == "settlement_dependency":
        changes = tuple(entry.settling_change_ids)
    return ExcludedRow(
        target=entry.target,
        text=text if text is not None else humanised(reason),
        reason=reason,
        known=text is not None,
        amendments=amendments,
        change_ids=changes,
    )


def variants_url(story_id: str) -> str:
    """History's Variants panel, where Fork lives (#53)."""
    return reverse("stories:history", args=[story_id]) + "#variants"


def _keep_first(entry: object, ranges: Mapping[str, str], story_id: str) -> Line | None:
    if not isinstance(entry, Mapping) or entry.get("code") != KEEP_FIRST_CODE:
        return None
    target = entry.get("target")
    shown = ranges.get(target, target) if isinstance(target, str) and target else EMPTY
    return Line(KEEP_FIRST_TEXT.format(range=shown), variants_url(story_id), entry)


def counts(to_delete: Mapping[str, Any]) -> dict[str, int]:
    """``{discarded_tails, dormant_continuations, operations}`` for the token's ``base``: the two
    groups' lengths and the int ``operations`` summed across both (non-ints skipped)."""
    out = {"discarded_tails": 0, "dormant_continuations": 0, "operations": 0}
    for key, _ in BUCKETS:
        items = to_delete.get(key)
        if isinstance(items, list):
            out[key] = len(items)
            for entry in items:
                n = _int(entry.get("operations")) if isinstance(entry, Mapping) else None
                out["operations"] += n or 0
    return out


def confirm_body(target_ids: Sequence[str]) -> dict[str, Any]:
    """What the confirm re-sends: exactly the listed targets (``None`` past :data:`MAX_TARGETS`:
    every eligible one, still pinned by the expected revision)."""
    targets = list(target_ids) if len(target_ids) <= MAX_TARGETS else None
    return {"targets": targets, "ignore_warnings": True}


def impact_view(pruned: Pruned, story_id: str, names: Mapping[str, Any]) -> PruneImpactView:
    """The dry run ``pruned`` as the report; ``names`` holds the page facts the token's ``base``
    carries (``story_name``: the story's name at the dry run, ``None`` when unnamed)."""
    impact = pruned.impact
    found = buckets(impact.to_delete)
    ranges = {row.id: row.range_text for bucket in found for row in bucket.rows if row.id}
    ids = tuple(dict.fromkeys(row.id for bucket in found for row in bucket.rows if row.id))
    tally = counts(impact.to_delete)
    base: dict[str, Any] = {
        "timeline_revision": pruned.timeline_revision,
        "story_name": names.get("story_name"),
        "counts": tally,
    }
    body = confirm_body(ids)
    if body["targets"] is None:
        base["every_eligible"] = True
    keep = (_keep_first(entry, ranges, story_id) for entry in impact.suggestions)
    return PruneImpactView(
        revision=pruned.timeline_revision,
        revision_text=DRY_RUN_TEXT.format(revision=f"{pruned.timeline_revision:,}"),
        buckets=found,
        lost=tuple(_lost_line(entry, ranges) for entry in impact.lost_operations),
        kept_text=kept_text(impact.retained),
        excluded=tuple(_excluded_row(story_id, entry) for entry in impact.excluded),
        keep_first=tuple(line for line in keep if line is not None),
        target_ids=ids,
        counts=tally,
        token=twostage.sign(KIND, story_id, body, base) if ids else "",
        report_token=sign_report(pruned, story_id),
        every_eligible=body["targets"] is None,
    )


# --- the report token (E8: a narrowed-to-nothing re-render makes no Engine call) ---------------


class ReportExpired(Exception):
    """The report token is too old: the page shows the error without the report."""


class ReportInvalid(Exception):
    """The report token is tampered with, for another story or no longer a ``PruneBody``."""


def sign_report(pruned: Pruned, story_id: str) -> str:
    from console.views.story_config import model_json  # the one model-to-JSON helper (#307)

    raw = {"story_id": story_id, "report": model_json(pruned)}
    return signing.dumps(raw, salt=REPORT_SALT, compress=True)


def unsign_report(token: str, story_id: str, *, max_age: int = twostage.MAX_AGE_S) -> Pruned:
    """The report ``token`` carries, or :class:`ReportExpired` / :class:`ReportInvalid`."""
    try:
        raw: Any = signing.loads(token, salt=REPORT_SALT, max_age=max_age)
    except signing.SignatureExpired as exc:
        raise ReportExpired from exc
    except signing.BadSignature as exc:
        raise ReportInvalid from exc
    if not isinstance(raw, dict) or raw.get("story_id") != story_id:
        raise ReportInvalid
    pruned = Pruned.from_json(raw.get("report"))
    if pruned is None:
        raise ReportInvalid
    return pruned


# --- the confirm page (E9) ------------------------------------------------------------------------


def _base_count(base: Mapping[str, Any], key: str) -> int:
    found = base.get("counts")
    n = _int(found.get(key)) if isinstance(found, Mapping) else None
    return n or 0


def confirm_page(
    story_id: str, base: Mapping[str, Any], token: str, cancel_url: str
) -> ConfirmPage:
    """E9's typed-name confirm for the signed dry run: the name typed is ``base["story_name"]``
    (the story's id when unnamed), never a fresh read."""
    name = base.get("story_name")
    name = name if isinstance(name, str) else None
    revision = _int(base.get("timeline_revision"))
    lines = [
        CONFIRM_DELETES.format(
            tails=tails_text(_base_count(base, "discarded_tails")),
            dormant=dormant_text(_base_count(base, "dormant_continuations")),
            operations=plural(_base_count(base, "operations"), "operation"),
        ),
        CONFIRM_MARKERS,
        CONFIRM_KEPT,
    ]
    if base.get("every_eligible"):
        lines.append(CONFIRM_EVERY.format(revision=revision))
    page = ConfirmPage(
        title=CONFIRM_TITLE,
        question=CONFIRM_QUESTION,
        emphasis=(name or "").strip() or story_id,
        confirm_label=CONFIRM_LABEL,
        cancel_url=cancel_url,
        lines=tuple(lines),
        facts=(
            ("Story id", story_id),
            ("Dry run at revision", f"{revision:,}" if revision else EMPTY),
        ),
        hidden={twostage.CONFIRM_FIELD: token},
        danger=True,
    )
    return typed_name_for(page, name, story_id)


# --- the result -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultView:
    pruned_text: str
    deleted_text: str
    revision_text: str
    prune_id: str
    more: Mapping[str, Any]
    key: str


def deleted_text(deleted: Mapping[str, Any]) -> str:
    """ "Deleted: Events 9, Operations 3" (the Engine's key order, zeros kept, ``{:,}``); a
    non-int value is left to More; an empty map reads "Deleted: nothing"."""
    parts = [
        f"{humanised(str(key))} {n:,}"
        for key, value in deleted.items()
        if (n := _int(value)) is not None
    ]
    return "Deleted: " + (", ".join(parts) if parts else DELETED_NOTHING)


def result_view(pruned: Pruned, base: Mapping[str, Any], key: str) -> ResultView:
    """An applied pruning: the counts from the answer's ``impact.to_delete``, falling back to the
    signed ``base`` counts."""
    assert pruned.result is not None  # noqa: S101 - the caller checked ``applied``
    to_delete = pruned.impact.to_delete
    lengths = []
    for group, _ in BUCKETS:
        items = to_delete.get(group)
        lengths.append(len(items) if isinstance(items, list) else _base_count(base, group))
    result = pruned.result
    return ResultView(
        pruned_text=PRUNED_LINE.format(
            tails=tails_text(lengths[0]), dormant=dormant_text(lengths[1])
        ),
        deleted_text=deleted_text(result.deleted),
        revision_text=REVISION_NOW.format(revision=f"{result.timeline_revision:,}"),
        prune_id=result.prune_id,
        more={"operation_id": result.operation_id, "deleted": result.deleted},
        key=pruned.idempotency_key or key,
    )


# --- past prunings --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunRow:
    prune_id: str
    at: str
    trigger_text: str
    trigger_raw: str
    principal: str
    revision_text: str
    counts_text: str
    impact: Any
    policy: Any


def impact_counts(impact: object) -> str:
    """ "1 discarded tail, 1 dormant continuation" when a run's raw ``impact.to_delete`` lists
    them, else "—"."""
    to_delete = impact.get("to_delete") if isinstance(impact, Mapping) else None
    if not isinstance(to_delete, Mapping):
        return EMPTY
    lists = [to_delete.get(key) for key, _ in BUCKETS]
    if not any(isinstance(items, list) for items in lists):
        return EMPTY
    tails, dormant = (len(items) if isinstance(items, list) else 0 for items in lists)
    return f"{tails_text(tails)}, {dormant_text(dormant)}"


def trigger_text(trigger: str, policy: object) -> str:
    if trigger == "manual":
        return TRIGGER_MANUAL
    if trigger == "policy":
        days = _days(policy)
        return TRIGGER_POLICY_DAYS.format(days=plural(days, "day")) if days else TRIGGER_POLICY
    return humanised(trigger) if trigger else EMPTY


def run_row(run: PruneRun) -> RunRow:
    revision = run.timeline_revision
    return RunRow(
        prune_id=run.prune_id,
        at=run.at,
        trigger_text=trigger_text(run.trigger, run.policy),
        trigger_raw=run.trigger,
        principal=run.principal,
        revision_text=f"Revision {revision:,}" if revision is not None else EMPTY,
        counts_text=impact_counts(run.impact),
        impact=run.impact,
        policy=run.policy,
    )


@dataclass(frozen=True)
class RunsView:
    rows: tuple[RunRow, ...]
    rejected: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def count_text(self) -> str:
        return plural(len(self.rows), "pruning") if self.rows else ""

    @property
    def rejected_text(self) -> str:
        n = len(self.rejected)
        return f"{plural(n, 'pruning')} couldn't be read — see More" if n else ""


def _run_of(entry: object) -> PruneRun | None:
    return PruneRun.from_json(entry)


def run_rows(runs: PruneRuns) -> RunsView:
    """The past prunings in the Engine's order (newest first), with D15's rejected entries."""
    unparsed = getattr(runs, "unparsed", {}) or {}
    rejected = rejected_entries(unparsed["prune_runs"], _run_of) if "prune_runs" in unparsed else []
    return RunsView(tuple(run_row(run) for run in runs.prune_runs), tuple(rejected))

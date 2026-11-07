"""The import job page's wording (#321; Engine #250/#251, ``core/bundles/import_job.py`` and
``orchestrator/import_gates.py`` at 95e57a5; owner answer 12; E7, E15, E23).

**E1 — the import rule.** Page-specific: only ``engine:import-job`` (``console/views/imports.py``)
imports these. E7's status texts, ``job_state`` and ``report_rows`` are ``console.jobs``'s and are
imported, never redefined; #320's corrupt-bundle reasons are ``console.forms.imports``'s.

- **Stages.** :data:`IMPORT_STAGES` is the Engine's ten working stages after ``queued``, in its
  ``STAGES`` order; :data:`STAGE_TEXT` words each. :func:`stage_text` is the status line's stage
  (E7's ``stage_text``: a known stage's text, else the humanised stage) and
  :func:`stage_count_text` "Stage <i> of 10" (a known stage only). :func:`shows_stage` is when
  either shows: a ``running`` job whose stage is a non-blank string other than ``queued``.
- **Errors.** :func:`error_view` words a refused or failed job's ``error {code, message,
  details}``: the code humanised, :data:`IMPORT_ERROR_TEXT` (a ``corrupt_bundle`` with a
  ``details.reason`` reads "<code text>: <reason text>"), the message ("—" when none), a
  ``rating_check_failed``'s :data:`RATING_CHECK_REASON_TEXT` (a ``details.reason`` with no
  violations) and its violations table (:func:`violation_rows`), and ``details`` for More. An
  error that is null, not an object or has no code is :data:`NO_ERROR_DETAILS_TEXT` with the raw
  error in More.
- **Violations** (``HistoryViolation.as_json``: ``position``, ``policy_id``, ``policy_name``,
  optional ``code``; never the text) are read leniently (E15): the policy name (else the id in
  ``<code>``, then " · " and the humanised violation code), the position (C1 from a ``[turn,
  variant, …]`` list or a ``{turn, variant, beat}`` map; a story image's ``{kind, id}`` as
  "<humanised kind> <code>id</code>"; else "—"), the excerpt in ``<q>`` only when some entry has
  an ``excerpt`` key, and the raw entry in More.
- **The report.** :func:`import_report_rows` wraps ``report_rows``: errors and warnings (each with
  its raw entry in More), the counts, the Outbox line (the outbox gate's ``{closed_staged_turns,
  parked, reservation_timers}``) and every other top-level key (``erasure``, ``guardrails``,
  ``profile``, …) as its own row with More, natural-sorted. A report that isn't an object is
  :data:`REPORT_UNREADABLE_TEXT` with the raw report in More.
- **Notes on a ready job.** :data:`BACKFILL_STARTED_NOTE` when the story read is ``paused`` by a
  ``backfill`` or the report's ``profile.upgrade_mode`` is ``backfill``
  (:func:`backfill_started`); :func:`parked_note` when ``report.outbox.parked`` is a positive int
  (a bool doesn't count).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.engine.models import NarrativePosition, Story
from console.forms.imports import corrupt_reason_text
from console.jobs import ReportEntry, report_rows
from console.positions import knowledge_position_text
from console.raw import EMPTY, more_json, plural
from console.wording import humanised

IMPORT_STAGES: tuple[str, ...] = (
    "reading",
    "inserting",
    "indexing",
    "erasure",
    "guardrails",
    "outbox",
    "profile",
    "rebuilding",
    "embedding",
    "cleaning",
)
STAGE_TEXT: Mapping[str, str] = {
    "reading": "Reading the archive",
    "inserting": "Creating the story's records",
    "indexing": "Indexing the story's records",
    "erasure": "Applying this deployment's erasures",
    "guardrails": "Checking the history against the rating",
    "outbox": "Parking unsettled external effects",
    "profile": "Attaching the engine profile",
    "rebuilding": "Rebuilding projections",
    "embedding": "Embedding memories",
    "cleaning": "Cleaning up",
}
STAGE_COUNT_TEXT = "Stage {i} of {n}"
RUNNING = "running"
QUEUED = "queued"

IMPORT_ERROR_TEXT: Mapping[str, str] = {
    "corrupt_bundle": "The bundle is damaged or isn't an Epistrel bundle",
    "bundle_too_large": "The archive passed this Engine's bundle limits while it was read",
    "unsupported_format_version": "This bundle's format isn't one this Engine reads",
    "rating_check_failed": "The story's history breaks the chosen rating's guardrails",
    "erased_subject_referenced": "The bundle names someone this deployment has erased",
    "import_failed": "The import failed — nothing was kept",
    "erasure_key_missing": (
        "This deployment's erasure keys are missing, so the bundle couldn't be cleared"
    ),
}
RATING_CHECK_REASON_TEXT: Mapping[str, str] = {
    "no_models": ("The rating's model-assisted policies can't be checked: no model is configured"),
}
CORRUPT_BUNDLE = "corrupt_bundle"
RATING_CHECK_FAILED = "rating_check_failed"
NO_STORY_TEXT = "No story was created"
NO_ERROR_DETAILS_TEXT = "The Engine gave no error details"
TRY_AGAIN = "Try again"
VIOLATIONS_CAPTION = "Guardrail violations ({n})"

READY_TEXT = "Imported as {story} — open it"
READY_NO_STORY_TEXT = "Imported as —"
BACKFILL_STARTED_NOTE = "The new profile adds data-producing capability, so a backfill started"
PARKED_NOTE = "{effects} parked unsettled — resolve each in Effects › Reconciliation"  # noqa: RUF001
BACKFILL = "backfill"
PAUSED = "paused"

REPORT_UNREADABLE_TEXT = "The report couldn't be read"
REPORT_SECTIONS = ("errors", "warnings", "counts")
OUTBOX = "outbox"
OUTBOX_SLOTS: tuple[tuple[str, str, str, str], ...] = (
    ("closed_staged_turns", "staged turn", "staged turns", "closed"),
    ("parked", "effect", "effects", "parked"),
    ("reservation_timers", "reservation timer", "reservation timers", ""),
)


# --- stages ---------------------------------------------------------------------------------------


def shows_stage(status: object, stage: object) -> bool:
    """A stage line (and E7's stage in the status text) shows only for a ``running`` job whose
    stage is a non-blank string other than ``queued``."""
    return status == RUNNING and isinstance(stage, str) and bool(stage.strip()) and stage != QUEUED


def stage_text(stage: object) -> str:
    """E7's ``stage_text``: the known stage's :data:`STAGE_TEXT`, else the humanised stage; ""
    for a blank or non-string one."""
    if not isinstance(stage, str) or not stage.strip():
        return ""
    return STAGE_TEXT.get(stage, humanised(stage))


def stage_count_text(stage: object) -> str:
    """ "Stage 5 of 10" for a stage in :data:`IMPORT_STAGES`; "" for any other."""
    if not isinstance(stage, str) or stage not in IMPORT_STAGES:
        return ""
    return STAGE_COUNT_TEXT.format(i=IMPORT_STAGES.index(stage) + 1, n=len(IMPORT_STAGES))


# --- violations -----------------------------------------------------------------------------------


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _turn_text(turn: int, variant: int, beat: int | None) -> str:
    if beat is None:
        return f"turn {turn}" + (f" · v{variant}" if variant else "")
    return knowledge_position_text(
        NarrativePosition(turn=turn, variant=variant, slot_k=0, beat=beat, step=0)
    )


def position_html(position: object) -> SafeString:
    """C1's text for a violation's position: a five-int ``[turn, variant, slot_k, beat, step]``
    list in full, a shorter ``[turn, variant, …]`` list by its turn and variant, a ``{turn,
    variant, beat}`` map (a null beat left out); a story image's ``{kind, id}`` as "<humanised
    kind> <code>id</code>"; anything else "—"."""
    if isinstance(position, list):
        parts = [_int(p) for p in position]
        if len(parts) == 5 and all(p is not None for p in parts):
            turn, variant, slot_k, beat, step = (p or 0 for p in parts)
            five = NarrativePosition(
                turn=turn, variant=variant, slot_k=slot_k, beat=beat, step=step
            )
            return format_html("{}", knowledge_position_text(five))
        first, second = [*parts, None, None][:2]
        if first is not None and second is not None:
            return format_html("{}", _turn_text(first, second, None))
        return format_html("{}", EMPTY)
    if isinstance(position, Mapping):
        at = _int(position.get("turn"))
        if at is not None:
            variant = _int(position.get("variant")) or 0
            return format_html("{}", _turn_text(at, variant, _int(position.get("beat"))))
        kind, found_id = _text(position.get("kind")), _text(position.get("id"))
        if kind is not None and found_id is not None:
            return format_html("{} <code>{}</code>", humanised(kind), found_id)
    return format_html("{}", EMPTY)


def _policy_html(entry: Mapping[str, Any]) -> SafeString:
    name, policy_id = _text(entry.get("policy_name")), _text(entry.get("policy_id"))
    if name is not None:
        policy = format_html("{}", name)
    elif policy_id is not None:
        policy = format_html("<code>{}</code>", policy_id)
    else:
        policy = format_html("{}", EMPTY)
    code = _text(entry.get("code"))
    return format_html("{} · {}", policy, humanised(code)) if code is not None else policy


class ViolationRow(NamedTuple):
    """One guardrail violation: the policy cell, the position, the excerpt (``None``: "—") and
    the raw entry for More."""

    policy: SafeString
    position: SafeString
    excerpt: str | None
    more: str


@dataclass(frozen=True)
class ViolationTable:
    caption: str
    rows: list[ViolationRow]
    has_excerpt: bool


def violation_rows(violations: object) -> ViolationTable | None:
    """``details.violations`` as a table; ``None`` for a non-list or an empty list. A
    non-object entry is a row of "—" cells with its raw entry in More (E23)."""
    if not isinstance(violations, list) or not violations:
        return None
    rows: list[ViolationRow] = []
    for entry in violations:
        if not isinstance(entry, Mapping):
            empty = format_html("{}", EMPTY)
            rows.append(ViolationRow(empty, empty, None, more_json(entry)))
            continue
        rows.append(
            ViolationRow(
                _policy_html(entry),
                position_html(entry.get("position")),
                _text(entry.get("excerpt")),
                more_json(entry),
            )
        )
    has_excerpt = any(isinstance(e, Mapping) and "excerpt" in e for e in violations)
    return ViolationTable(VIOLATIONS_CAPTION.format(n=len(rows)), rows, has_excerpt)


# --- the error block ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorView:
    """A refused or failed job's error as the page shows it, after "No story was created"."""

    code_text: str = ""
    code: str = ""
    text: str = ""  # IMPORT_ERROR_TEXT (with a corrupt bundle's reason); "" for an unknown code
    message: str = EMPTY
    reason_text: str = ""  # a rating check's ``details.reason``
    violations: ViolationTable | None = None
    more: str = ""  # ``details`` (or, with ``no_details``, the raw error)
    no_details: bool = False


def error_view(error: object) -> ErrorView:
    """:class:`ErrorView` for a job's raw ``error`` (module docstring)."""
    code = _text(error.get("code")) if isinstance(error, Mapping) else None
    if not isinstance(error, Mapping) or code is None:
        return ErrorView(
            text=NO_ERROR_DETAILS_TEXT,
            more=more_json(error) if error is not None else "",
            no_details=True,
        )
    details = error.get("details")
    found: Mapping[str, Any] = details if isinstance(details, Mapping) else {}
    text = IMPORT_ERROR_TEXT.get(code, "")
    reason = _text(found.get("reason"))
    if code == CORRUPT_BUNDLE and reason is not None:
        text = f"{text}: {corrupt_reason_text(reason)}"
    violations: ViolationTable | None = None
    reason_text = ""
    if code == RATING_CHECK_FAILED:
        violations = violation_rows(found.get("violations"))
        if violations is None and reason is not None:
            reason_text = RATING_CHECK_REASON_TEXT.get(reason, humanised(reason))
    return ErrorView(
        code_text=humanised(code),
        code=code,
        text=text,
        message=_text(error.get("message")) or EMPTY,
        reason_text=reason_text,
        violations=violations,
        more=more_json(details) if details not in (None, {}, []) else "",
    )


# --- the validation report ------------------------------------------------------------------------


class ReportRow(NamedTuple):
    """One error or warning: the code humanised, the message ("—" when none) and the raw entry
    for More."""

    code: str
    message: str
    more: str


@dataclass(frozen=True)
class ImportReport:
    unreadable: bool = False
    unreadable_text: str = REPORT_UNREADABLE_TEXT
    more: str = ""  # the raw report, when unreadable
    errors: list[ReportRow] = field(default_factory=list)
    warnings: list[ReportRow] = field(default_factory=list)
    counts: list[tuple[str, str]] = field(default_factory=list)
    outbox: str | None = None
    others: list[tuple[str, str]] = field(default_factory=list)


_DIGITS = re.compile(r"(\d+)")


def _natural(key: str) -> tuple[tuple[tuple[bool, Any], ...], str]:
    parts = tuple(int(p) if p.isdigit() else p.lower() for p in _DIGITS.split(key) if p)
    return tuple((isinstance(p, str), p) for p in parts), key


def _rows(entries: Sequence[ReportEntry], raw: object) -> list[ReportRow]:
    found = raw if isinstance(raw, list) else [raw]
    return [
        ReportRow(entry.code, entry.message, more_json(found[i] if i < len(found) else entry.more))
        for i, entry in enumerate(entries)
    ]


def _slot(value: object, singular: str, many: str, verb: str) -> str:
    n = _int(value)
    if n is not None:
        shown = plural(n, singular, many)
    else:
        shown = f"{EMPTY if value is None else value} {many}"
    return f"{shown} {verb}" if verb else shown


def outbox_text(outbox: Mapping[str, Any]) -> str:
    """ "1 staged turn closed, 2 effects parked, 0 reservation timers"; a missing member "—",
    a non-int one shown raw (E23)."""
    return ", ".join(
        _slot(outbox.get(name), singular, many, verb) for name, singular, many, verb in OUTBOX_SLOTS
    )


def import_report_rows(report: object) -> ImportReport:
    """The validation report's rows (module docstring)."""
    if not isinstance(report, Mapping):
        return ImportReport(unreadable=True, more=more_json(report))
    rows = report_rows(report)
    outbox = report.get(OUTBOX)
    others = [
        (humanised(str(key)), more_json(value))
        for key, value in sorted(report.items(), key=lambda kv: _natural(str(kv[0])))
        if key not in REPORT_SECTIONS and not (key == OUTBOX and isinstance(value, Mapping))
    ]
    return ImportReport(
        errors=_rows(rows.errors, report.get("errors")),
        warnings=_rows(rows.warnings, report.get("warnings")),
        counts=rows.counts,
        outbox=outbox_text(outbox) if isinstance(outbox, Mapping) else None,
        others=others,
    )


# --- a ready job's link and notes ---------------------------------------------------------------


def _reversed(name: str, *args: str) -> str | None:
    try:
        return reverse(name, args=list(args))
    except NoReverseMatch:
        return None


def ready_html(story_id: str | None, story: Story | None) -> SafeString:
    """ "Imported as <name> — open it" linked to ``stories:detail``; the id in ``<code>`` when
    the story read failed or answered a blank name; "Imported as —" with no story."""
    if not story_id:
        return format_html("{}", READY_NO_STORY_TEXT)
    name = _text(story.name) if story is not None else None
    shown = (
        format_html("{}", name) if name is not None else format_html("<code>{}</code>", story_id)
    )
    text = format_html(READY_TEXT.replace("{story}", "{}"), shown)
    url = _reversed("stories:detail", story_id)
    return format_html('<a class="import-story" href="{}">{}</a>', url, text) if url else text


def backfill_started(story: Story | None, report: object) -> bool:
    """The story read is ``paused`` by a ``backfill``, or the report's ``profile.upgrade_mode``
    is ``backfill`` (the profile gate writes it; it survives a finished backfill and a failed
    story read)."""
    pause = story.pause if story is not None else None
    if (
        story is not None
        and story.status == PAUSED
        and pause is not None
        and pause.cause == BACKFILL
    ):
        return True
    profile = report.get("profile") if isinstance(report, Mapping) else None
    return isinstance(profile, Mapping) and profile.get("upgrade_mode") == BACKFILL


def parked_count(report: object) -> int | None:
    """``report.outbox.parked`` when it is a positive int (a bool doesn't count), else ``None``."""
    outbox = report.get(OUTBOX) if isinstance(report, Mapping) else None
    value = outbox.get("parked") if isinstance(outbox, Mapping) else None
    return value if type(value) is int and value > 0 else None


def parked_text(n: int) -> str:
    """:data:`PARKED_NOTE` pluralised, its verb agreeing ("1 external effect was …")."""
    effects = plural(n, "external effect") + (" was" if n == 1 else " were")
    return PARKED_NOTE.format(effects=effects)


def _note(text: str, url: str | None) -> SafeString:
    if url is None:
        return format_html("{}", text)
    return format_html('<a href="{}">{}</a>', url, text)


def ready_notes(
    story_id: str | None, story: Story | None, report: object, backfill_url: str | None
) -> list[SafeString]:
    """A ready job's notes, each only when it applies: the backfill note (linked through
    ``backfill_url``, plain while unregistered) and the parked-effects note (linked to the
    story's ``inspect:reconciliation``, D19)."""
    notes: list[SafeString] = []
    if backfill_started(story, report):
        notes.append(_note(BACKFILL_STARTED_NOTE, backfill_url))
    parked = parked_count(report)
    if parked is not None:
        url = _reversed("inspect:reconciliation", story_id) if story_id else None
        notes.append(_note(parked_text(parked), url))
    return notes

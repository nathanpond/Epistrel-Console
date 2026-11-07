"""M7's background jobs (#295): export, import, backfill and certification run.

**E1 — the import rule.** Every M7 job page imports these; none defines its own status text,
interval, terminal set, recent-jobs query or two-stage kind. ``console/job_poll.py`` stays M3's
(#64 convention 10: ``_job_poll.html``, ``poll_failure``, ``settle_job``); it gains the
``trigger`` keyword, :data:`SLOW_POLL_TRIGGER` and :data:`PARTIAL_PARAM`, re-exported here.

**E7 — async jobs.**

- **Interval (bounded, fixed).** Export and import poll at ``POLL_TRIGGER`` ("every 2s"),
  backfills and certification runs at :data:`SLOW_POLL_TRIGGER` ("every 5s"). The interval never
  grows or shrinks. Polling stops at a terminal state, at a *waiting* state (a backfill
  ``paused_for_conflicts``: nothing changes until the operator resolves), or on M3's
  non-retryable failed read. "Refresh" always works without JavaScript.
- **The polled partial** is the job page's own URL with ``?partial=1`` (:data:`PARTIAL_PARAM`):
  it answers just the ``_job_poll.html`` block; a full GET renders the frame around it.
- **Status per kind** (:data:`JOB_KINDS`, :func:`job_state`): E7's table. An unknown status is
  not terminal (it keeps polling), humanised, with the raw value in ``title`` (``JobState.raw``).
- **Counts.** ``progress`` is the job's raw progress mapping (``None`` when absent). A count
  parenthesis ("(3/9 chunks)", "(3/9)") appears only when both counts are ints and the total is
  > 0; otherwise the text leaves it out (never "—/—", never "0/0"). An import's caller passes
  ``{**job.progress, "stage": job.stage}`` (``ImportJob`` carries ``stage`` top-level). A
  backfill's ``done``/``total`` are its chunks while extracting and its turns
  (``reconciled_through_turn``/``last_turn``) while reconciling; ``paused_for_conflicts`` takes
  ``<n>`` from ``progress["conflicts_open"]`` (the caller fills it from the backfill's open
  conflicts; missing → "its").
- **What each state shows** (``templates/console/_job_status.html``, context ``job``
  (:class:`JobState`), ``job_id``, ``facts``, ``error``, optional ``stage_text``): the status
  text and "Job <code>id</code>" always; a ``<progress>`` while running (not ``queued``, not
  waiting; indeterminate without a usable total); the error block (code humanised, message,
  details under More) when failed or refused; ``facts`` (``(label, value)`` pairs, values
  pre-rendered or escaped by the caller) as a ``<dl>`` on terminal success only. ``stage_text``
  replaces the humanised stage after the kind's running text (#321 passes its own wording).
- **Settling.** Any terminal read settles the start call's ``queued`` #44 row with
  ``settle_job(job_id, failed=state.failed, error_code=settle_error_code(error))``; waiting does
  not settle, and a null ``job_id`` skips it. :func:`settle_error_code`: ``error["code"]`` for a
  mapping (export, import), the string itself for a bare string (a certification run's
  exception name), else ""; cut to the #44 field's length.
- **The partial's failure order.** (1) 503 ``erasure_pending`` → D17's held fragment inside the
  poll block with no ``hx-trigger``; (2) job not found, or another story's → 200 with the
  not-found text and no ``hx-trigger`` (the full page answers 404 inside the frame); (3)
  everything else through M3's ``poll_failure`` unchanged.
- **No job list from the Engine** (E18). Past exports and imports are the Console's own #44 rows
  (:func:`recent_jobs`): an export or import started outside this Console isn't shown.

**E8 — impact first, then confirm.** These writes answer a report first and write only when
resent confirmed: prune (``"prune"``, #317), import (``"import"``, #320), a gap fill
(``"backfill-start"``, #309), a backfill resume (``"backfill-resume"``, #309), upgrading pinned
stories (``"upgrade-pinned"``, #310) and a certification run (``"certify-profile"``,
``"certify-story"``, #314) — :data:`TWOSTAGE_KINDS`, each a ``console.twostage`` salt suffix. The
first stage POSTs ``ignore_warnings: false`` (or no ``confirm``) and renders the report partial,
then ``console/_two_stage.html``. The token is ``twostage.sign(kind, target_id, body, base)``
carrying exactly what will be re-sent; ``base`` holds the revision the report was computed at and
the page facts the second stage shows. The confirm POST (to the same URL, carrying
``twostage.CONFIRM_FIELD``) unsigns it — ``ConfirmationExpired``, or a ``target_id`` other than
the URL's, is ``EXPIRED_TEXT`` with zero Engine calls — and re-sends ``body`` with
``ignore_warnings: true`` / ``confirm: true`` under the partial's fresh idempotency key. A first
stage that answers ``applied: true`` goes straight to the result; a confirm that answers
``applied: false`` again re-renders the new report with a fresh token. A re-render that must
show the report without an Engine call signs the report's JSON into ``base``. Only prune sends
``X-Epistrel-Expected-Revision`` (the dry run's ``timeline_revision``). #309, #310, #314, #317
and #320 cite this paragraph and none redefines it.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, NamedTuple

from console.engine.client import EngineError
from console.job_poll import PARTIAL_PARAM, POLL_TRIGGER, SLOW_POLL_TRIGGER, poll_context
from console.models import EngineOperation
from console.raw import EMPTY, more_json, plural
from console.wording import humanised

__all__ = [
    "JOB_KINDS",
    "PARTIAL_PARAM",
    "POLL_TRIGGER",
    "SLOW_POLL_TRIGGER",
    "TWOSTAGE_KINDS",
    "JobError",
    "JobKind",
    "JobSpec",
    "JobState",
    "RecentJob",
    "ReportEntry",
    "ReportRows",
    "job_error",
    "job_poll_context",
    "job_state",
    "recent_jobs",
    "report_rows",
    "settle_error_code",
]


class JobKind(StrEnum):
    EXPORT = "export"
    IMPORT = "import"
    BACKFILL = "backfill"
    CERTIFICATION_RUN = "certification_run"


@dataclass(frozen=True)
class JobSpec:
    """One kind's row of E7's table: its statuses, which are terminal, failed and waiting on the
    operator, each status's text (counts filled by :func:`job_state`) and the poll interval."""

    statuses: tuple[str, ...]
    terminal: frozenset[str]
    failed: frozenset[str]
    waiting: frozenset[str]
    texts: Mapping[str, str]
    trigger: str


QUEUED = "queued"

JOB_KINDS: Mapping[JobKind, JobSpec] = {
    JobKind.EXPORT: JobSpec(
        statuses=("queued", "running", "ready", "failed", "expired"),
        terminal=frozenset({"ready", "failed", "expired"}),
        failed=frozenset({"failed"}),
        waiting=frozenset(),
        texts={
            "queued": "Queued",
            "running": "Exporting…",
            "ready": "Ready to download",
            "failed": "Failed",
            "expired": "Expired (download no longer available)",
        },
        trigger=POLL_TRIGGER,
    ),
    JobKind.IMPORT: JobSpec(
        statuses=("queued", "running", "ready", "refused", "failed"),
        terminal=frozenset({"ready", "refused", "failed"}),
        failed=frozenset({"refused", "failed"}),
        waiting=frozenset(),
        texts={
            "queued": "Queued",
            "running": "Importing",  # + " — <humanised stage>" and the count
            "ready": "Imported",
            "refused": "Refused (no story was created)",
            "failed": "Failed (no story was created)",
        },
        trigger=POLL_TRIGGER,
    ),
    JobKind.BACKFILL: JobSpec(
        statuses=(
            "queued",
            "extracting",
            "reconciling",
            "completed",
            "cancelled",
            "failed",
            "paused_for_conflicts",
        ),
        terminal=frozenset({"completed", "cancelled", "failed"}),
        failed=frozenset({"failed"}),
        waiting=frozenset({"paused_for_conflicts"}),
        texts={
            "queued": "Queued",
            "extracting": "Extracting",  # + " (<done>/<total> chunks)"
            "reconciling": "Reconciling",  # + " (through turn <n> of <last_turn>)"
            "completed": "Completed",
            "cancelled": "Cancelled",
            "failed": "Failed",
            "paused_for_conflicts": "Waiting for you: {conflicts} to resolve",
        },
        trigger=SLOW_POLL_TRIGGER,
    ),
    JobKind.CERTIFICATION_RUN: JobSpec(
        statuses=("queued", "running", "completed", "failed"),
        terminal=frozenset({"completed", "failed"}),
        failed=frozenset({"failed"}),
        waiting=frozenset(),
        texts={
            "queued": "Queued",
            "running": "Running",
            "completed": "Completed",
            "failed": "Failed",
        },
        trigger=SLOW_POLL_TRIGGER,
    ),
}

# E8: the two-stage kinds (each a ``console.twostage`` salt suffix).
TWOSTAGE_KINDS: tuple[str, ...] = (
    "prune",
    "import",
    "backfill-start",
    "backfill-resume",
    "upgrade-pinned",
    "certify-profile",
    "certify-story",
)

STAGE_SEPARATOR = " — "


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usable(done: int | None, total: int | None) -> bool:
    """E7's count rule: both ints and the total above 0."""
    return done is not None and total is not None and total > 0


@dataclass(frozen=True)
class JobState:
    """A job's status as a page shows it. ``text`` is the status line; ``terminal``, ``failed``
    and ``waiting`` decide polling, settling and what shows; ``trigger`` is the kind's interval;
    ``done``/``total`` drive the ``<progress>`` (``None`` when unknown); ``raw`` is the raw
    status for an unknown one ("" for a known status), shown in ``title``. ``status`` is the raw
    value always, and ``stage`` an import's humanised stage while running (else ""), which a
    page's ``stage_text`` replaces (:meth:`text_with_stage`)."""

    text: str
    terminal: bool
    failed: bool
    waiting: bool
    trigger: str
    done: int | None = None
    total: int | None = None
    raw: str = ""
    status: str = ""
    stage: str = ""

    @property
    def running(self) -> bool:
        """The ``<progress>`` shows: not terminal, not waiting, not ``queued``."""
        return not self.terminal and not self.waiting and self.status != QUEUED

    @property
    def succeeded(self) -> bool:
        """Terminal and not failed: only then do the facts show."""
        return self.terminal and not self.failed

    @property
    def polling(self) -> bool:
        return not (self.terminal or self.waiting)

    @property
    def indeterminate(self) -> bool:
        return not _usable(self.done, self.total)

    def text_with_stage(self, stage_text: str) -> str:
        """:attr:`text` with the humanised stage replaced by ``stage_text`` (E7's optional
        ``stage_text``); unchanged when either is blank."""
        if not stage_text or not self.stage:
            return self.text
        shown, replaced = f"{STAGE_SEPARATOR}{self.stage}", f"{STAGE_SEPARATOR}{stage_text}"
        return self.text.replace(shown, replaced, 1)


def _count_text(done: int | None, total: int | None, unit: str = "") -> str:
    if not _usable(done, total):
        return ""
    suffix = f" {unit}" if unit else ""
    return f" ({done:,}/{total:,}{suffix})"


def job_state(
    kind: JobKind | str, status: str, *, progress: Mapping[str, Any] | None = None
) -> JobState:
    """E7's status for ``kind`` at ``status``, its counts filled from ``progress`` (raw)."""
    spec = JOB_KINDS[JobKind(kind)]
    raw_progress: Mapping[str, Any] = progress if isinstance(progress, Mapping) else {}
    status = status if isinstance(status, str) else str(status)
    if status not in spec.texts:
        return JobState(
            humanised(status) if status else EMPTY,
            terminal=False,
            failed=False,
            waiting=False,
            trigger=spec.trigger,
            raw=status,
            status=status,
        )
    text = spec.texts[status]
    done: int | None = None
    total: int | None = None
    stage = ""
    kind = JobKind(kind)
    if kind is JobKind.BACKFILL and status == "extracting":
        done, total = _int(raw_progress.get("chunks_done")), _int(raw_progress.get("chunks_total"))
        text += _count_text(done, total, "chunks")
    elif kind is JobKind.BACKFILL and status == "reconciling":
        done = _int(raw_progress.get("reconciled_through_turn"))
        total = _int(raw_progress.get("last_turn"))
        if _usable(done, total):
            text += f" (through turn {done:,} of {total:,})"
    elif kind is JobKind.BACKFILL and status == "paused_for_conflicts":
        open_count = _int(raw_progress.get("conflicts_open"))
        conflicts = "its conflicts" if open_count is None else plural(open_count, "conflict")
        text = text.format(conflicts=conflicts)
    elif kind is JobKind.IMPORT and status == "running":
        found = raw_progress.get("stage")
        stage = humanised(found) if isinstance(found, str) and found.strip() else ""
        if stage:
            text += f"{STAGE_SEPARATOR}{stage}"
        done, total = _int(raw_progress.get("done")), _int(raw_progress.get("total"))
        text += _count_text(done, total)
    elif status not in spec.terminal and status != QUEUED:
        done, total = _int(raw_progress.get("done")), _int(raw_progress.get("total"))
    return JobState(
        text,
        terminal=status in spec.terminal,
        failed=status in spec.failed,
        waiting=status in spec.waiting,
        trigger=spec.trigger,
        done=done,
        total=total,
        status=status,
        stage=stage,
    )


def job_poll_context(
    kind: JobKind | str,
    job_id: str,
    status: str,
    *,
    poll_url: str,
    refresh_url: str,
    body_template: str,
    progress: Mapping[str, Any] | None = None,
    retrying: bool = False,
) -> dict[str, Any]:
    """``poll_context`` for one job (polling until terminal or waiting, at the kind's interval)
    plus ``job`` (:class:`JobState`) and ``job_id``; the caller adds ``facts`` and ``error``."""
    state = job_state(kind, status, progress=progress)
    return {
        **poll_context(
            poll_url=poll_url,
            refresh_url=refresh_url,
            body_template=body_template,
            polling=state.polling,
            retrying=retrying,
            trigger=state.trigger,
        ),
        "job": state,
        "job_id": job_id,
    }


# --- the error block and settling -----------------------------------------------------------------

ERROR_CODE_LENGTH = EngineOperation._meta.get_field("error_code").max_length or 64


@dataclass(frozen=True)
class JobError:
    """A failed or refused job's error as ``_job_status.html`` shows it: the code humanised,
    the message ("" when none) and the details for More (``None`` when none)."""

    code_text: str
    code: str
    message: str
    details: Any

    @property
    def more(self) -> str:
        return more_json(self.details) if self.details not in (None, {}, []) else ""


def _member(error: Any, name: str) -> Any:
    if isinstance(error, Mapping):
        return error.get(name)
    return getattr(error, name, None)


def job_error(error: Any) -> JobError | None:
    """``error`` (a mapping or model with ``code``/``message``/``details``, a bare string such as
    a certification run's exception name, or an :class:`EngineError`) for the error block;
    ``None`` when there is none."""
    if error is None or error == "" or error == {}:
        return None
    if isinstance(error, EngineError):
        return JobError(humanised(error.code), error.code, error.message, error.details or None)
    if isinstance(error, str):  # an exception's class name: shown as is, never humanised
        return JobError(error, error, "", None)
    code = _member(error, "code")
    message = _member(error, "message")
    details = _member(error, "details")
    code_text = humanised(code) if isinstance(code, str) and code else EMPTY
    if isinstance(error, Mapping) and details is None:
        rest = {k: v for k, v in error.items() if k not in ("code", "message")}
        details = rest or None
    return JobError(
        code_text,
        code if isinstance(code, str) else "",
        message if isinstance(message, str) else "",
        details,
    )


def settle_error_code(error: Any) -> str:
    """E7's ``settle_job`` error code: ``error["code"]`` for a mapping with a string code, the
    string itself for a bare string, else ""; cut to the #44 field's length."""
    code: Any = ""
    if isinstance(error, Mapping):
        code = error.get("code")
    elif isinstance(error, str):
        code = error
    return code[:ERROR_CODE_LENGTH] if isinstance(code, str) else ""


# --- recent jobs (the Console's own #44 rows) ---------------------------------------------------

RECENT_KINDS: Mapping[JobKind, str] = {
    JobKind.EXPORT: "export_story",
    JobKind.IMPORT: "import_story",
}
RECENT_LIMIT = 20
_SCAN = 500  # rows read per query: replays and duplicates are dropped before ``limit``


@dataclass(frozen=True)
class RecentJob:
    """One export or import this Console started: the job id, when (``EngineOperation
    .created_at``), the principal and the row's raw outcome (``queued``/``ok``/``error``; each
    page words it)."""

    job_id: str
    started_at: datetime
    principal: str
    outcome: str


def _stored(row: EngineOperation, name: str) -> str:
    response = row.response
    value = response.get(name) if isinstance(response, dict) else None
    return value if isinstance(value, str) and value else ""


def recent_jobs(
    kind: JobKind | str, *, story_id: str | None = None, limit: int = RECENT_LIMIT
) -> list[RecentJob]:
    """The newest ``limit`` exports or imports in the #44 log (E7), newest first: the job id from
    the ``job_id`` column, else the stored response's ``job_id`` (a row with neither is
    skipped); ``replayed`` rows skipped, and one row per job id (the newest). With ``story_id``,
    only rows whose ``story_id`` column (else the stored response's) is that story. Any kind but
    export and import raises ``ValueError``."""
    try:
        operation_kind = RECENT_KINDS[JobKind(kind)]
    except KeyError, ValueError:
        raise ValueError(f"no recent-jobs list for {kind!r}") from None
    rows = EngineOperation.objects.filter(kind=operation_kind, replayed=False).order_by(
        "-created_at", "-id"
    )
    if story_id is not None:
        rows = rows.filter(story_id__in=(story_id, ""))
    found: list[RecentJob] = []
    seen: set[str] = set()
    for row in rows[:_SCAN]:
        job_id = row.job_id or _stored(row, "job_id")
        if not job_id or job_id in seen:
            continue
        if story_id is not None and (row.story_id or _stored(row, "story_id")) != story_id:
            continue
        seen.add(job_id)
        found.append(RecentJob(job_id, row.created_at, row.principal, row.outcome))
        if len(found) >= limit:
            break
    return found


# --- an import's validation report (raw, E15) ---------------------------------------------------

UNREADABLE = "Unreadable"
REPORT_UNREADABLE = "The validation report couldn't be read"
ENTRY_UNREADABLE = "This entry couldn't be read"


class ReportEntry(NamedTuple):
    """One error or warning: the code humanised, the message ("—" when none) and the entry's
    other members for More (``None`` when there are none)."""

    code: str
    message: str
    more: dict[str, Any] | None


@dataclass(frozen=True)
class ReportRows:
    """An import's validation report as rows: errors, then warnings, then the counts as
    ``(humanised key, "1,234")`` sorted by key (natural order)."""

    errors: list[ReportEntry]
    warnings: list[ReportEntry]
    counts: list[tuple[str, str]]


_DIGITS = re.compile(r"(\d+)")


def _natural(key: str) -> tuple[tuple[Any, ...], str]:
    parts = tuple(int(p) if p.isdigit() else p.lower() for p in _DIGITS.split(key) if p)
    return tuple((isinstance(p, str), p) for p in parts), key


def _entry(entry: object) -> ReportEntry:
    if not isinstance(entry, Mapping):
        return ReportEntry(UNREADABLE, ENTRY_UNREADABLE, {"entry": repr(entry)})
    code = entry.get("code")
    message = entry.get("message")
    details = entry.get("details")
    rest = {str(k): v for k, v in entry.items() if k not in ("code", "message")}
    more = dict(details) if isinstance(details, Mapping) and len(rest) == 1 else rest
    return ReportEntry(
        humanised(code) if isinstance(code, str) and code else EMPTY,
        message if isinstance(message, str) and message.strip() else EMPTY,
        more or None,
    )


def _entries(value: object) -> list[ReportEntry]:
    if not isinstance(value, Iterable) or isinstance(value, str | bytes | Mapping):
        return [] if value is None else [_entry(value)]
    return [_entry(e) for e in value]


def _count(value: object) -> str:
    return f"{value:,}" if _int(value) is not None else str(value)


def report_rows(report: object) -> ReportRows:
    """``report``'s top-level ``errors`` and ``warnings`` (lists of ``{code, message, …}``) and
    ``counts`` (a mapping), tolerating anything else (E23). A non-object report is one
    "couldn't be read" error holding its repr."""
    if not isinstance(report, Mapping):
        return ReportRows(
            [ReportEntry(UNREADABLE, REPORT_UNREADABLE, {"report": repr(report)})], [], []
        )
    counts = report.get("counts")
    shown = (
        [
            (humanised(str(key)), _count(n))
            for key, n in sorted(counts.items(), key=lambda kv: _natural(str(kv[0])))
        ]
        if isinstance(counts, Mapping)
        else []
    )
    return ReportRows(_entries(report.get("errors")), _entries(report.get("warnings")), shown)

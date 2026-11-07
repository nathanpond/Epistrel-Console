"""A story's backfill as its Backfill page shows it (#308; FR-PROF-9/10, Engine #228/#230).

When a story gains data-producing capability with history, the Engine re-derives the earlier
turns from the stored transcript. While that runs the story is paused (409 ``story_paused``,
``cause: backfill``); under ``backfill_conflict_policy: ask`` it can end its pass
``paused_for_conflicts`` and wait for the author. ``GET /stories/{id}/backfill`` answers the
latest one (:class:`~console.engine.models.StoryBackfill`), 404 ``backfill_not_found`` when the
story never had one.

**E1 — the import rule.** The page's wording and rows live here; the job status, polling and
the ``backfill`` kind are ``console.jobs`` (E7), the pause banner ``console.story_pause`` (E10),
the raw renderers ``console.raw`` (E23). #309 (resolve, resume, gap fill) imports these.

:func:`backfill_view` builds :class:`BackfillView`:

- **Facts.** "Kind: <humanised kind>", "Capabilities: <code> list" ("—" when empty), "Turns
  <from>-<to>" (an en dash) each linked into History (C1, only an int is linked),
  "Requested by <principal> · <time>" (E23's principal rule, no "(you)"), Started and
  Finished ("—" when null), and the conflict counts "<open> open · <resolved> resolved ·
  <uncertain> uncertain".
- **Status** (E7): ``job_state("backfill", status, progress={**progress, "conflicts_open": n})``.
- **Conflicts** (#309 adds a resolve form to each open one once ``stories:backfill-conflict``
  resolves, D19; ``console/forms/backfill.py``): each parsed
  :class:`~console.engine.models.BackfillConflict` in the Engine's order is a
  :class:`ConflictRow` (subject, status badge, one :class:`StatementRow` per statement with the
  recommendation marked, the resolution once resolved), then one "This conflict couldn't be
  read" row per rejected entry (E15's lenient list, E22's notice and rows). Speakers are C4's
  cast names, else "Narrator" for ``null``, else the raw id. Quotes are transcript text from an
  unscoped read: :data:`OMNISCIENT_NOTE` says so.
- **Report** (E7: facts on terminal success only). A completed backfill's ``report`` (raw, E15)
  as :class:`ReportGroup` s in the order "Counts", "By outcome", "Materialization (deferred
  genesis content)" and "Conflicts", keys in the Engine's order, numbers ``{:,}``. A section
  that is missing or not an object is skipped; each lists its int-valued members and, one level
  down, an object's int-valued members ("By resolution · Last mention"). With no ``counts``
  object, "Counts" lists the report's own top-level int members: the Engine's reconciliation
  tally (``core.backfill.reconcile.merge_report``: facts, memories, exposures, …) sits there.
- **Pending change.** ``EffectiveConfig.pending_backfill`` (raw, E15; the Engine sends ``{id}``)
  reads "Waiting on this backfill: <humanised kind and capabilities>" when the object carries
  them, else "a configuration change", raw in More. A completed **upgrade** reads "The new
  configuration now applies", linked to Config (a gap fill or resummarize holds no change).

**#309's wording** (resolve, finish now, gap fill): the flashes :func:`resolved_text`
(:data:`RESOLVED_TEXT` / :data:`LAST_RESOLVED_TEXT`), :func:`finished_text` and
:data:`GAP_FILL_STARTED`; :func:`resume_line`; the refusal lines; and the gap-fill preview
(:func:`gap_fill_preview`: "Capabilities: …", :func:`turns_text`, the estimate through
``console.impact.backfill_text``, unchanged). :func:`subject_summary` stores a subject's parts
in a signed ``base``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final
from urllib.parse import urlencode

from django.urls import NoReverseMatch, reverse

from console.engine.models import (
    Backfill,
    BackfillConflict,
    BackfillEstimate,
    ConflictStatement,
    GapFillPreview,
    StoryBackfill,
    rejected_entries,
)
from console.impact import backfill_text
from console.inspect import history_turn_url, short_id
from console.jobs import JobKind, JobState, job_state
from console.raw import CELL_LIMIT, EMPTY, compact_json, more_json, plural
from console.wording import humanised
from console.world import state_value_text

NO_BACKFILL = "This story has never had a backfill"
CANCELLED_TEXT = "Backfill cancelled — the story resumed on its current configuration"
OMNISCIENT_NOTE = "Quoted statements are shown as recorded (omniscient)"
NEW_CONFIG_TEXT = "The new configuration now applies"
WAITING_ON_TEXT = "Waiting on this backfill: {summary}"
A_CONFIG_CHANGE = "a configuration change"
REFRESH_CONFLICTS = "Refresh to see the conflicts"
CONFLICT_UNREADABLE = "This conflict couldn't be read"
NARRATOR = "Narrator"
RECOMMENDED_LAST = "(recommended — the latest mention)"
RECOMMENDED_TEXT = "(recommended — {reason})"
LAST_MENTION = "last_mention"
SHOW_ALL = "Show resolved conflicts"
SHOW_OPEN = "Show open conflicts only"
SHOWING_FIRST = "Showing the first {shown} of {total}"
NO_CONFLICTS = "None"
CONFLICT_LIMIT = 50  # the Engine's ``limit``; no paging (the backfill read has no cursor)
CONFLICTS_PARAM = "conflicts"
ALL: Final = "all"
OPEN: Final = "open"
COMPLETED = "completed"
CANCELLED = "cancelled"
UPGRADE = "upgrade"  # the kind whose completion applies a held configuration (Engine #228)
# The Engine's ``Kind`` (``core.backfill``); another kind is humanised with its raw value in title.
KIND_TEXT: Mapping[str, str] = {
    "upgrade": "Upgrade",
    "gap_fill": "Gap fill",
    "resummarize": "Resummarize",
}
CONFLICT_STATUS_TEXT: Mapping[str, str] = {
    "open": "Open",
    "resolved": "Resolved",
    "uncertain": "Uncertain",
}
REPORT_COUNTS = "Counts"
REPORT_BY_OUTCOME = "By outcome"
REPORT_MATERIALIZATION = "Materialization (deferred genesis content)"
REPORT_CONFLICTS = "Conflicts"
_REPORT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("counts", REPORT_COUNTS),
    ("by_outcome", REPORT_BY_OUTCOME),
    ("materialization", REPORT_MATERIALIZATION),
    ("conflicts", REPORT_CONFLICTS),
)

# --- the cancel confirm (#134's confirmed mode) ---------------------------------------------------
CANCEL_TITLE = "Cancel the backfill"
CANCEL_QUESTION = "Cancel this story's backfill?"
CANCEL_LINES = (
    "Its re-derived data is discarded and the configuration change it was holding is not applied.",
    "The story resumes on its current configuration.",
)
CANCEL_LABEL = "Cancel the backfill"
CANCEL_LINK = "Cancel the backfill…"
NOT_CANCELLABLE_TEXT = "This backfill has already ended — nothing was cancelled"
CANCEL_VERB = "cancel the backfill"
BACKFILL_PARAM = "backfill"
STATUS_PARAM = "status"

# --- #309: resolve, finish now, gap fill ----------------------------------------------------------
RESOLVED_TEXT = "Conflict resolved — {open} still open"
RESOLVED_BARE = "Conflict resolved"
RESOLVED_NOW = " — the backfill is now {status}"
LAST_RESOLVED_TEXT = (
    "That was the last open conflict — the backfill completed and the story resumed"
)
CONFLICT_CLOSED_TEXT = "This conflict was already settled — reload"
NOT_AWAITING_TEXT = "The backfill isn't waiting on conflicts any more — reload"
CONFLICT_NOT_FOUND_TEXT = "No such conflict for this story's backfill"
MALFORMED_CONFLICT_ID = "No such conflict — the id in the address isn't an Engine id"
UNRESOLVABLE_ENTRY_TEXT = "Can't be resolved here: unreadable entry"
RESOLVE_VERB = "resolve a backfill conflict"
RESUME_LINE = (
    "Finishing now makes the {conflicts} uncertain: the story carries each forward as an open "
    "question instead of a settled fact"
)
FINISHED_TEXT = "Backfill finished — {conflicts} made uncertain"
FINISHED_BARE = "Backfill finished"
GAP_FILL_STARTED = "Gap fill started — follow it below"
NOTHING_TO_FILL = "Nothing to fill — every capability covers this story's turns"
IN_PROGRESS_TEXT = "A backfill is already running — follow it below"
PAUSES_LINE = (
    "The story pauses while it runs; nothing is regenerated, only re-derived from the transcript"
)
CAPABILITIES_UNREAD = "The capability list couldn't be read"
NOT_FILLABLE_TEXT = (
    "The Engine can't gap-fill {capabilities}: it fills only the capabilities that "
    "produce data (named without a mode)"
)
ANSWER_UNREADABLE = "The Engine's answer couldn't be read — reload the page"
GAP_FILL_HEADING = "Fill coverage gaps…"
GAP_FILL_HELP = "Tick none to fill every capability that has a gap"
FINISH_HEADING = "Finish the backfill"
FINISH_NOW_LINK = "Finish now…"
FINISH_NOW_LABEL = "Finish now"
PREVIEW_LABEL = "Preview"
START_LABEL = "Start the gap fill"
BACK_TEXT = "Back"
GAP_FILL_VERB = "fill coverage gaps"
RESUME_VERB = "finish the backfill"
CAPABILITIES_NONE = "none"
PAUSED_FOR_CONFLICTS = "paused_for_conflicts"


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text(value: object) -> str:
    return value if isinstance(value, str) and value.strip() else ""


def enum_text(value: object, known: Mapping[str, str] | None = None) -> tuple[str, str]:
    """E23's enum rule: ``(text, title)``: a known value's text and no title, an unknown one
    humanised with the raw value as title, a blank one "—"."""
    found = _text(value)
    if not found:
        return EMPTY, ""
    if known is not None and found in known:
        return known[found], ""
    return humanised(found), found


# --- URLs -----------------------------------------------------------------------------------------


def _reversed(name: str, story_id: str) -> str | None:
    try:
        return reverse(name, args=[story_id])
    except NoReverseMatch:
        return None


def cancel_url(story_id: str, backfill_id: str, status: str) -> str | None:
    """``stories:backfill-cancel`` with the display-only ``?backfill=&status=``; ``None`` while
    unregistered (D19)."""
    found = _reversed("stories:backfill-cancel", story_id)
    if found is None:
        return None
    return f"{found}?{urlencode({BACKFILL_PARAM: backfill_id, STATUS_PARAM: status})}"


def config_url(story_id: str) -> str | None:
    return _reversed("stories:config", story_id)


def start_url(story_id: str) -> str | None:
    """``stories:backfill-start`` (#309), ``None`` while unregistered (D19)."""
    return _reversed("stories:backfill-start", story_id)


def conflict_url(story_id: str, conflict_id: str) -> str | None:
    """``stories:backfill-conflict`` (#309), ``None`` while unregistered (D19)."""
    try:
        return reverse("stories:backfill-conflict", args=[story_id, conflict_id])
    except NoReverseMatch:
        return None


# --- conflicts ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StatementRow:
    """One quoted statement: "Turn <n>" (linked, its position under More), the speaker, the
    humanised source, the quote ("—" when null), the value (D5) and the recommendation marker
    ("" on every statement but the recommended one)."""

    statement_id: str
    turn: int
    turn_url: str
    position: int
    speaker: str
    source: str
    source_raw: str
    quote: str
    value: str
    recommended: str

    @property
    def quoted(self) -> bool:
        return self.quote != EMPTY


@dataclass(frozen=True)
class ConflictRow:
    """One conflict: its anchor id, subject (text and the raw subject for More), status badge,
    statements, the resolution line once resolved, and More's raw extras (a resolution, an
    unmatched recommendation). A rejected entry is ``bad`` with only ``more``."""

    anchor: str
    subject: str = EMPTY
    subject_parts: tuple[tuple[str, bool], ...] = ()
    status: str = EMPTY
    status_raw: str = ""
    status_title: str = ""
    statements: tuple[StatementRow, ...] = ()
    resolved: str = ""
    resolved_by: str = ""
    resolved_at: str = ""
    more: str = ""
    subject_more: str = ""
    bad: bool = False
    id: str = ""


def speaker_text(speaker: str | None, names: Mapping[str, str]) -> str:
    """C4's cast name, "Narrator" for ``None``, else the raw id."""
    if speaker is None or speaker == "":
        return NARRATOR
    return names.get(speaker) or speaker


def recommended_text(reason: object) -> str:
    if reason == LAST_MENTION:
        return RECOMMENDED_LAST
    shown, _ = enum_text(reason)
    return RECOMMENDED_TEXT.format(reason=shown.lower() if shown != EMPTY else shown)


def _statement(
    statement: ConflictStatement, story_id: str, names: Mapping[str, str], marker: str
) -> StatementRow:
    source, source_raw = enum_text(statement.source)
    return StatementRow(
        statement_id=statement.statement_id,
        turn=statement.turn,
        turn_url=history_turn_url(story_id, statement.turn),
        position=statement.position,
        speaker=speaker_text(statement.speaker, names),
        source=source,
        source_raw=source_raw,
        quote=statement.quote if statement.quote is not None else EMPTY,
        value=state_value_text(statement.value, None),
        recommended=marker,
    )


def subject_parts(subject: object, names: Mapping[str, str]) -> tuple[tuple[str, bool], ...]:
    """The subject's present parts as ``(text, code)``: the humanised ``kind``, the ``key`` in
    ``<code>``, and the Engine's ``entity_id`` (a cast name, else the short id) and
    ``predicate`` (in ``<code>``); empty when it has none of them."""
    if not isinstance(subject, Mapping):
        return ()
    parts: list[tuple[str, bool]] = []
    kind = _text(subject.get("kind"))
    if kind:
        parts.append((humanised(kind), False))
    key = subject.get("key")
    if key is not None and key != "":
        parts.append((key if isinstance(key, str) else compact_json(key), True))
    entity = _text(subject.get("entity_id"))
    if entity:
        parts.append((names.get(entity) or short_id(entity), False))
    predicate = subject.get("predicate")
    if predicate is not None and predicate != "":
        parts.append((predicate if isinstance(predicate, str) else compact_json(predicate), True))
    return tuple(parts)


def _choice_text(choice: object, statements: Sequence[StatementRow]) -> str:
    """A resolution's choice: "Turn <n> “<quote>”" for a statement's id, else D5's value."""
    statement_id = choice.get("statement_id") if isinstance(choice, Mapping) else choice
    for row in statements:
        if isinstance(statement_id, str) and statement_id == row.statement_id:
            quote = f" “{row.quote}”" if row.quoted else ""
            return f"Turn {row.turn:,}{quote}"
    if isinstance(choice, Mapping) and "value" in choice:
        return state_value_text(choice["value"], None)
    if choice is None or (isinstance(choice, Mapping) and statement_id is None):
        return EMPTY
    return state_value_text(choice, None)


def _resolved_text(resolution: object, statements: Sequence[StatementRow]) -> str:
    """ "<humanised via> <choice>" (the Engine's ``{choice, via, by}``; a fixture's
    ``{choose}``; an automatic one's ``{policy}``)."""
    if not isinstance(resolution, Mapping):
        return EMPTY
    via = resolution.get("via") or resolution.get("policy")
    via_text, _ = enum_text(via)
    choice = resolution.get("choice", resolution.get("choose"))
    choice_text = _choice_text(choice, statements)
    if via_text == EMPTY:
        return choice_text
    return via_text if choice_text == EMPTY else f"{via_text} {choice_text}"


def conflict_row(
    conflict: BackfillConflict, story_id: str, names: Mapping[str, str]
) -> ConflictRow:
    recommended = conflict.recommended
    chosen = recommended.choose if recommended is not None else None
    marker = recommended_text(recommended.reason) if recommended is not None else ""
    matched = any(s.statement_id == chosen for s in conflict.statements)
    statements = tuple(
        _statement(s, story_id, names, marker if s.statement_id == chosen else "")
        for s in conflict.statements
    )
    extras: dict[str, Any] = {}
    if recommended is not None and not matched:
        extras["recommended"] = asdict(recommended)
    if conflict.resolution is not None:
        extras["resolution"] = conflict.resolution
    status, status_title = enum_text(conflict.status, CONFLICT_STATUS_TEXT)
    parts = subject_parts(conflict.subject, names)
    resolved = ""
    if conflict.status == "resolved" or conflict.resolution is not None:
        resolved = _resolved_text(conflict.resolution, statements)
    return ConflictRow(
        anchor=f"conflict-{conflict.id}",
        id=conflict.id,
        subject=" · ".join(text for text, _ in parts) if parts else compact_json(conflict.subject),
        subject_parts=parts,
        status=status,
        status_raw=conflict.status,
        status_title=status_title,
        statements=statements,
        resolved=resolved,
        resolved_by=conflict.resolved_by or "",
        resolved_at=conflict.resolved_at or "",
        more=more_json(extras) if extras else "",
        subject_more=more_json(conflict.subject),
    )


def rejected_row(entry: object, index: int) -> ConflictRow:
    """E23: a conflict entry that didn't parse: one row, the raw entry under More."""
    found = entry.get("id") if isinstance(entry, Mapping) else None
    anchor = f"conflict-{found}" if isinstance(found, str) and found else f"conflict-bad-{index}"
    return ConflictRow(anchor=anchor, bad=True, more=more_json(entry))


# --- the report -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportGroup:
    """One group of the report's ``<dl>``: its heading and ``(label, "1,234")`` rows."""

    heading: str
    rows: tuple[tuple[str, str], ...]


def _count_rows(section: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for key, value in section.items():
        n = _int(value)
        if n is not None:
            rows.append((humanised(str(key)), f"{n:,}"))
        elif isinstance(value, Mapping):
            parent = humanised(str(key))
            rows.extend(
                (f"{parent} · {humanised(str(k))}", f"{v:,}")
                for k, v in value.items()
                if _int(v) is not None
            )
    return tuple(rows)


def report_groups(report: object) -> tuple[ReportGroup, ...]:
    """A completed backfill's report as groups (module docstring); ``()`` for a non-object."""
    if not isinstance(report, Mapping):
        return ()
    groups: list[ReportGroup] = []
    for key, heading in _REPORT_SECTIONS:
        section = report.get(key)
        if key == "counts" and not isinstance(section, Mapping):
            section = {
                k: v
                for k, v in report.items()
                if _int(v) is not None and k not in dict(_REPORT_SECTIONS)
            }
        if not isinstance(section, Mapping):
            continue
        rows = _count_rows(section)
        if rows:
            groups.append(ReportGroup(heading, rows))
    return tuple(groups)


# --- the pending change ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingChange:
    """``pending_backfill``'s line and its raw value for More."""

    text: str
    more: str


def pending_change(pending: object) -> PendingChange | None:
    """ "Waiting on this backfill: …" for a set ``pending_backfill`` (raw), else ``None``."""
    if pending is None or pending == {} or pending == "":
        return None
    summary = A_CONFIG_CHANGE
    if isinstance(pending, Mapping):
        kind = _text(pending.get("kind"))
        capabilities = pending.get("capabilities")
        named = (
            [c for c in capabilities if isinstance(c, str) and c]
            if isinstance(capabilities, list)
            else []
        )
        if kind or named:
            summary = " ".join(
                part
                for part in (
                    humanised(kind) if kind else "",
                    f"({', '.join(named)})" if named else "",
                )
                if part
            )
    return PendingChange(WAITING_ON_TEXT.format(summary=summary), more_json(pending))


# --- the page -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BackfillView:
    """Everything the Backfill page shows of one backfill (module docstring)."""

    id: str
    status: str
    state: JobState
    progress: dict[str, Any] | None
    kind: str
    kind_title: str
    capabilities: tuple[str, ...]
    from_turn: int | None
    from_url: str
    to_turn: int | None
    to_url: str
    requested_by: str
    created_at: str
    started_at: str
    finished_at: str
    error: str
    counts_text: str
    phase: str
    phase_title: str
    conflicts: tuple[ConflictRow, ...]
    rejected_count: int
    showing_text: str
    report: tuple[ReportGroup, ...]
    report_more: str
    report_unreadable: bool
    cancel_url: str
    new_config_url: str
    new_config_applies: bool

    @property
    def show_report(self) -> bool:
        """E7: the report shows on terminal success only: ``completed`` (a cancelled backfill
        is terminal but not a success; its report goes raw under More)."""
        return self.status == COMPLETED

    @property
    def show_raw_report(self) -> bool:
        """A cancelled backfill's non-empty report, raw under More (no ``<dl>``)."""
        return self.status == CANCELLED and bool(self.report_more)

    @property
    def show_error(self) -> bool:
        """``error`` outside the status block: the block's own error shows it once failed."""
        return bool(self.error) and not self.state.failed

    @property
    def cancellable(self) -> bool:
        return not self.state.terminal and bool(self.cancel_url)


def _counts_text(backfill: StoryBackfill) -> str:
    counts = backfill.conflicts
    if counts is None:  # a queued backfill may have none yet (#308's planner note)
        return EMPTY
    return f"{counts.open:,} open · {counts.resolved:,} resolved · {counts.uncertain:,} uncertain"


def _progress(backfill: StoryBackfill) -> dict[str, Any] | None:
    progress = backfill.progress
    if progress is None:
        return None
    counts = backfill.conflicts
    return {**asdict(progress), "conflicts_open": counts.open if counts is not None else None}


def _shown_total(backfill: StoryBackfill, everything: bool) -> int | None:
    counts = backfill.conflicts
    if counts is None:
        return None
    return counts.open + counts.resolved + counts.uncertain if everything else counts.open


def backfill_view(
    backfill: StoryBackfill,
    story_id: str,
    names: Mapping[str, str],
    *,
    everything: bool = False,
) -> BackfillView:
    """The page's view of ``backfill`` (``everything``: read with ``?conflicts=all``)."""
    progress = _progress(backfill)
    state = job_state(JobKind.BACKFILL, backfill.status, progress=progress)
    kind, kind_title = enum_text(backfill.kind, KIND_TEXT)
    span = backfill.range
    from_turn = _int(span.from_turn) if span is not None else None
    to_turn = _int(span.to_turn) if span is not None else None
    rows = tuple(conflict_row(c, story_id, names) for c in backfill.conflict_list)
    raw = backfill.unparsed.get("conflict_list")
    rejected = (
        rejected_entries(raw, BackfillConflict.from_json) if "conflict_list" in backfill.unparsed
        else []
    )  # fmt: skip
    bad = tuple(rejected_row(entry, n) for n, entry in enumerate(rejected))
    listed = len(rows) + len(bad)
    total = _shown_total(backfill, everything)
    showing = (
        SHOWING_FIRST.format(shown=f"{listed:,}", total=plural(total, "conflict"))
        if total is not None and total > listed
        else ""
    )
    report = backfill.report
    phase_raw = backfill.progress.phase if backfill.progress is not None else ""
    phase, phase_title = enum_text(phase_raw)
    applies = backfill.status == COMPLETED and backfill.kind == UPGRADE
    return BackfillView(
        id=backfill.id,
        status=backfill.status,
        state=state,
        progress=progress,
        kind=kind,
        kind_title=kind_title,
        capabilities=tuple(backfill.capabilities),
        from_turn=from_turn,
        from_url=history_turn_url(story_id, from_turn) if from_turn is not None else "",
        to_turn=to_turn,
        to_url=history_turn_url(story_id, to_turn) if to_turn is not None else "",
        requested_by=backfill.requested_by,
        created_at=backfill.created_at,
        started_at=backfill.started_at or "",
        finished_at=backfill.finished_at or "",
        error=backfill.error or "",
        counts_text=_counts_text(backfill),
        phase=phase,
        phase_title=phase_title,
        conflicts=(*rows, *bad),
        rejected_count=len(bad),
        showing_text=showing,
        report=report_groups(report),
        report_more=more_json(report) if report not in (None, {}) else "",
        report_unreadable=not isinstance(report, Mapping),
        cancel_url=cancel_url(story_id, backfill.id, backfill.status) or "",
        new_config_url=(config_url(story_id) or "") if applies else "",
        new_config_applies=applies,
    )


def status_fact(status: str) -> str:
    """The cancel page's Status fact: E7's text with no progress ("Extracting"), an unknown
    status humanised, a missing one "—"."""
    if not status:
        return EMPTY
    return job_state(JobKind.BACKFILL, status).text.split(" (", 1)[0].split(":", 1)[0]


# --- #309: the flashes and the gap-fill preview ---------------------------------------------------


def resolved_text(backfill: Mapping[str, Any]) -> tuple[str, bool]:
    """The resolve flash's text and whether it completed the backfill: ``completed`` →
    :data:`LAST_RESOLVED_TEXT`; else :data:`RESOLVED_TEXT` (no count clause without an int
    ``conflicts_open``), plus " — the backfill is now <E7 status>" for a status other than
    ``paused_for_conflicts``."""
    status = backfill.get("status")
    if status == COMPLETED:
        return LAST_RESOLVED_TEXT, True
    left = _int(backfill.get("conflicts_open"))
    text = RESOLVED_TEXT.format(open=f"{left:,}") if left is not None else RESOLVED_BARE
    if isinstance(status, str) and status and status != PAUSED_FOR_CONFLICTS:
        text += RESOLVED_NOW.format(status=status_fact(status))
    return text, False


def finished_text(made_uncertain: object) -> str:
    """ "Backfill finished — <n> conflict(s) made uncertain" (C11), or the bare text."""
    n = _int(made_uncertain)
    if n is None:
        return FINISHED_BARE
    return FINISHED_TEXT.format(conflicts=plural(n, "conflict"))


def not_fillable_text(refused: Sequence[str]) -> str:
    """#340: a gap fill's 422 ``validation_error`` naming ``details.not_enabled``."""
    return NOT_FILLABLE_TEXT.format(capabilities=", ".join(refused))


def resume_line(conflicts_open: int) -> str:
    return RESUME_LINE.format(conflicts=plural(conflicts_open, "open conflict"))


def turns_text(from_turn: object, to_turn: object) -> str:
    """ "Turns <from>\u2013<to>", "Turn <n>" when the bounds are equal, "—" for a missing
    bound."""
    low, high = _int(from_turn), _int(to_turn)
    if low is not None and low == high:
        return f"Turn {low:,}"
    shown = [f"{n:,}" if n is not None else EMPTY for n in (low, high)]
    return f"Turns {shown[0]}–{shown[1]}"  # noqa: RUF001


def capabilities_line(capabilities: object) -> str:
    """ "Capabilities: a, b" (raw ids, E23), "Capabilities: none" for an empty list."""
    named = (
        [c for c in capabilities if isinstance(c, str)] if isinstance(capabilities, list) else []
    )
    return f"Capabilities: {', '.join(named) if named else CAPABILITIES_NONE}"


@dataclass(frozen=True)
class GapFillPreviewView:
    """The gap-fill preview's lines (``_gap_fill_preview.html``)."""

    needed: bool
    capabilities: str
    turns: str
    estimate: str


def gap_fill_preview(preview: GapFillPreview) -> GapFillPreviewView:
    """The preview through ``console.impact.backfill_text`` (its wording unchanged)."""
    estimate = preview.estimate
    found = Backfill(
        needed=preview.needed,
        estimate=BackfillEstimate(
            model_calls=estimate.model_calls,
            tokens=estimate.tokens,
            duration_s=estimate.duration_s,
        ),
    )
    return GapFillPreviewView(
        needed=preview.needed,
        capabilities=capabilities_line(preview.capabilities),
        turns=turns_text(preview.range.from_turn, preview.range.to_turn),
        estimate=backfill_text(found),
    )


def subject_summary(subject: object, names: Mapping[str, str]) -> list[list[Any]]:
    """A conflict subject as JSON-able ``[[text, code], …]`` (#308's parts; one compact-JSON
    code part when it has none), for a signed ``base``."""
    parts = subject_parts(subject, names)
    if parts:
        return [[text, code] for text, code in parts]
    return [[compact_json(subject, CELL_LIMIT), True]]

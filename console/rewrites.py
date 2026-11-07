"""The rewrite family's shared helpers (#294): the session view every page that shows a rewrite
session renders, the resolution-mode wording, the rewrite refusals, the rewrite and amendment
URL helpers, the omniscient-only lines and the Engine gaps M7 says once.

The E-list below is the M7 cross-story conventions' own (M7 milestone description); this module
carries the rewrite half of E2 to E6 and E18, ``console/forms/rewrites.py`` E4's forms and
``console/raw.py`` E23's renderers.

**E1 — where things live, and the import rule.** This module holds E3's session view
(:class:`SessionView`, :func:`session_view`, :data:`STATE_TEXT`, :data:`TERMINAL_STATES`,
:data:`OPEN_STATES`) and its entry views (:class:`ChangeRow`, :class:`RadiusRow`,
:func:`change_rows`, :func:`radius_rows`), E4's :func:`resolution_mode_text`, E5's
:func:`rewrite_refusal`, :func:`stale_reason_refs` and the exported sentences, E6's
rewrite/amendment URL helpers and E18's gap lines. No page story (#297 onwards) defines a
helper, a wording constant, a URL helper or a gate an E assigns to a module: it imports it. A
story that imports a module another story creates depends on that story. D19 still holds for
every link: a helper that reverses a name a later story registers returns ``None`` while it is
unregistered, and its template renders plain text.

**E2 — omniscient-only routes.** The six rewrite-session routes, the four amendment routes,
``merge_entities``, ``apply_directive``, ``list_outbox`` and ``GET …/history?view=as_recorded``
refuse anything but the omniscient viewpoint. The client always sends
``viewpoint=omniscient`` for them (#292). No page on these routes offers a viewpoint picker (D4
does not apply); each shows :data:`OMNISCIENT_LINE` through
``{% include "console/rewrites/_omniscient.html" %}``. A 403 ``omniscient_required`` that
reaches a page anyway is a Console bug: :func:`rewrite_refusal` returns
:data:`OMNISCIENT_REQUIRED_TEXT` above the form and logs the error. History's ``as_recorded``
view (#304) shows :data:`AS_RECORDED_LINE` instead.

**E3 — the rewrite-session partial.** Every page that shows a rewrite session (#297's session
page, #298's answers, #299's apply/cancel and stale, #305's line edit, #300's routed directive,
#301's directed regenerate, #303's undo and redo, #306's merge) renders it through
``{% include "console/rewrites/_session.html" with view=session_view %}``.
:func:`session_view` builds the frozen :class:`SessionView` from #292's ``RewriteSession`` (or
``RewriteSessionWritten.session``) and C4's cast names:

- **State**: ``state_text`` from :data:`STATE_TEXT` (an unknown state humanised, the raw value
  in ``title``), ``kind_text`` from :data:`KIND_TEXT` (a ``rewrite`` with no instruction reads
  "Line edit", #346), the instruction verbatim (or "—"), the
  pipeline humanised, ``expires_at`` through ``engine_time`` plus ``expires_text`` ("expires in
  Nm", "expires in Hh Mm", "expires in <1m"; empty once past or closed), the stale count as
  :data:`STALE_COUNT_TEXT` when > 0, ``last_error`` as ``(code, detail, more)`` (a message-less
  error's detail is its humanised ``reasons``, else empty and the code shows alone), and the result
  and target amendments linked through :func:`amendment_url` (plain ``<code>`` while
  unregistered; the target labelled "Undoes" / "Redoes" by kind). ``held_render`` reads "Held
  reply (shown once applied)" with the reply quoted and "N model call(s) to replay".
- **Modes**: ``resolution_text`` (E4's :func:`resolution_mode_text`) and ``preview_mode_text``
  ("Preview first" / "Apply directly").
- **Questions** (``_questions.html``): one :class:`QuestionView` per parsed question, in the
  Engine's order: the prompt, a High/Low severity badge with text (never colour alone), Required
  / Optional, the options as radios with the recommended one **pre-selected**, marked
  " (recommended)" and followed by "Why: <reason>", and a ``text_<qid>`` box. There is no
  per-question accept: only E4's form-wide "Accept every recommendation". An answered question
  shows its answer (the option label, plus " (recommended)" when the Engine's
  ``accepted_recommendation`` is true, or the quoted text) and "by <principal> · <time>"; an
  answer with none of these reads "—" with the raw question under More. A question the Engine
  sent that doesn't parse is one "This question couldn't be read" row (E22) with its raw entry
  under More. The answers form (E4) renders only while ``awaiting_answers``.
- **Preview** (``_preview.html``): ``None`` while a required question is unanswered
  (:data:`PREVIEW_PENDING`). Otherwise "Planned at story revision N", the plan hash under More,
  the changes (:func:`change_rows`), the blast radius's three tables in order — "Conflicts"
  (``irreducible``, High before Low, the Engine's order otherwise), "Cascaded repairs",
  "Incidental mentions" — each with its count and "None" when empty (:func:`radius_rows`),
  :data:`TRUNCATED_TEXT` as a warning when ``blast_radius_truncated``, the relabels, new
  entities, suggested ports and timing-uncertain rows (headings left out when empty), the
  refused changes ("The Engine won't make these changes", never hidden), the notifications
  ("Who is told") and, on a Lite session, the reach (:data:`REACH_TEXT`, plus
  :data:`REACH_TRUNCATED_TEXT`).
- **Report** (an applied session): the raw report under More, with "N change(s) applied · M
  resolution(s)" for whichever of ``applied`` / ``resolutions`` is a list.
- **Actions** (the footer): ``(label, url, method)``, each only while its URL name resolves
  (D19). "Answer" (``stories:rewrite-answers``) while ``awaiting_answers`` is the questions
  form's action, not a footer form; "Apply" (``stories:rewrite-apply``) while
  ``awaiting_approval`` is a POST form with the page's fresh idempotency key and the caller's
  ``base_revision``; "Cancel" (``stories:rewrite-cancel``) while open is a link to #299's
  confirm page; none once terminal. A read-only caller passes ``dataclasses.replace(view,
  actions=())`` and no ``answers_form``.

Entry dicts stay raw (E15): :func:`radius_rows` and :func:`change_rows` never raise on a
malformed entry, which becomes a row reading "—" with the raw entry as compact JSON in
``more``. The partial never re-reads anything and calls no helper: every wording decision is
made here.

**E4 — rewrite modes and answers.** ``console/forms/rewrites.py`` holds ``RewriteModesForm``,
``RESOLUTION_CHOICES`` and ``AnswersForm`` (its docstring carries E4).
:func:`resolution_mode_text` reads a stored ``resolution_mode`` back to the choice labels.

**E5 — rewrite, amendment, merge and directive refusals.** :func:`rewrite_refusal` ``(exc,
story_id, verb_phrase, effective=None) -> WorldRefusal | None`` reuses D9's ``WorldRefusal(text,
url, field)``; its own codes are matched first and anything else falls through to D9's
``world_refusal`` (which falls through to C5). Each text is exact and exported here:
``rewrite_stale`` (:data:`STALE_TEXT` plus the humanised, de-duplicated reason kinds;
:data:`BASE_MISMATCH_TEXT`; :data:`PIPELINE_CHANGED_TEXT`; the refs only under More via
:func:`stale_reason_refs`), the session codes (closed, not found, the limit linked to the
Rewrite page, questions pending, apply in progress, record in flight, apply failed), the
instruction codes (``rewrite_unparseable``, ``rewrite_noop`` on ``instruction``), the guardrail
violation (policy names from ``details.violations[*]``, the field from its first locator:
``body.instruction`` → ``instruction``, ``body.text`` → ``text``, ``body.line_edit.new_text`` →
``new_text``, ``body.directive[.text]`` → ``directive``, ``body.answers[n].text`` → the raw
locator, which the caller maps through the text answers it sent), ``invalid_answer`` (on
``choice_<qid>``), ``base_revision_required``, ``module_off`` ``lite_rewrites``
(``console.inspect.unavailable_for``, linked to Config), ``omniscient_required`` (E2),
``merge_invalid`` by reason (on ``entity_ids``), the amendment codes, the directive codes (on
``text``), ``steer_budget_exceeded``, and the six reconcile codes: ``rewrite_reach_failed``
(:data:`REACH_FAILED_TEXT`), ``rewrite_question_superseded`` (:data:`SUPERSEDED_TEXT`),
``line_not_editable`` (:data:`NOT_EDITABLE_TEXT`), ``beat_required``
(:data:`BEAT_REQUIRED_TEXT`, the sentence only), ``record_inactive`` (:data:`INACTIVE_TEXT`,
History's Variants with ``dormant_variant``) and ``author_fact_limit``
(:data:`AUTHOR_FACT_LIMIT_TEXT`). ``story_paused`` and ``story_importing`` are E10's wording and
``pruned`` #293's, both reached through the fall-through.

**E6 — URL helpers.** :func:`rewrites_url` (``stories:rewrites``), :func:`rewrite_session_url`
(``stories:rewrite``), :func:`amendments_url` (``stories:amendments``) and
:func:`amendment_url` (``stories:amendment``) each return ``None`` while the name is
unregistered, or for a blank or non-string id. ``tests/urls_with_m7_pages.py`` is the one
shared M7 stub urlconf for their linked cases.

**#297's additions.** The Rewrite page's session list: :class:`RewriteRow` and
:func:`rewrite_rows` (every cell pre-rendered), :func:`rejected_sessions` and
:func:`rejected_notice` (D15), and the empty texts :data:`NO_SESSIONS`,
:data:`NO_SESSIONS_MATCH` and :data:`NO_MORE_SESSIONS`. E21's state-driven flash for any write
that opens, answers or re-plans a session is :func:`session_write_flash` (#297 passes the open's
texts; a later story passes its own verb and texts). #298 adds
:func:`guardrail_answer_indexes`, every guardrail locator read as its free-text answer index.

**E18 — Engine gaps, said once (convention 24's rule).**

- **No canary-run list.** Canary results are reached only through the certification they made
  stale (E13). The Engine request is ``CANARY_LIST_REQUEST`` in ``console/certification.py``
  (#295): :data:`CANARY_LIST_REQUEST_URL`, nathanpond/Epistrel#776.
- **No export or import list.** The Console lists its own #44 rows (E7); an export or import
  started outside this Console isn't shown.
- **No rewrite-session filter by kind.** ``list_rewrites`` filters by ``state`` only, so the
  Rewrite page offers no kind filter (one would filter a single page client-side).
- **Not drivable through the API** (Engine ``docs/OPERATIONS.md``): whole-deployment backup and
  restore, the daily policy prune, rewrite expiry, the export retention sweep, and eval and
  certification CLI runs. The Console shows their results (prune runs with ``trigger: policy``,
  expired sessions, expired exports, eval runs) and drives none.
- **E10's ``pause.progress`` mismatch (confirmed by #295, nathanpond/Epistrel#783)**: ``GET
  /stories/{id}`` answers 500 while a backfill pauses the story, because a backfill's
  ``pause.progress`` (``{phase, chunks_done, …}``) fails ``StoryPauseProgressBody``'s ``{done,
  total}``. The Console reads either shape (``StoryPauseProgress`` is lenient) and the 500 shows
  as #43's panel until the Engine fixes it; the live test is ``xfail(strict=True)`` on #783.
- **The export bundle route** declares no 409/410 in its schema (Console #333; the tests serve
  the 410 undeclared).
- **The import route** declares no 413 ``bundle_too_large`` in its schema
  (nathanpond/Epistrel#786, found by #320; the tests serve it undeclared).
- **The edge image sets no bundle directory**, and an export whose job fails stays ``running``
  (Console #334); this matters to the export and import job pages' polling (#319, #321).
- **The resolve route's description** still says an outbox row is 404; the code resolves a
  parked ``imported_unsettled`` row by its ``outbox_id`` (nathanpond/Epistrel#787, found by
  #326, which follows the code).
- **A structured line's stored text isn't readable** (Console #357, found by ``/n8-verify M7``).
  A line edit plans against ``transcript_messages.text``: the preview's ``old_text`` and the
  Engine's ``rewrite_noop`` check both read it. For a structured turn's ``message_rendered``,
  that is the message composed for the turn's own viewpoint (``operations.viewpoint``) under
  its audience filter. The omniscient history read that Edit this line… pre-fills from
  (#305) recomposes that message from the beats (Engine ``core/history.py``
  ``_recomposed``). So on a Full story the pre-fill can differ from what the Engine holds:
  ``old_text: ""`` against a non-empty omniscient line. The local "unchanged" check then
  compares against the wrong text. No read returns the stored text, or says whether an item
  was recomposed. A read under the turn's viewpoint matches it only while the story's
  perception and player view are still the ones it was composed under. The Console keeps
  the omniscient pre-fill.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlencode

from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.engine.client import EngineClientError, EngineError
from console.engine.models import (
    NarrativePosition,
    RewriteList,
    RewritePreview,
    RewriteQuestion,
    RewriteSession,
    RewriteSessionWritten,
    RewriteStaleDetails,
    RewriteSummary,
    rejected_entries,
)
from console.engine_errors import log
from console.forms.rewrites import RESOLUTION_CHOICES, THRESHOLDS
from console.inspect import short_id, unavailable_for
from console.positions import knowledge_position_text
from console.raw import CELL_LIMIT, EMPTY, compact_json, more_json, number_text, plural
from console.templatetags.console_format import engine_time
from console.wording import humanised
from console.world import WorldRefusal, world_refusal

CANARY_LIST_REQUEST_URL = "https://github.com/nathanpond/Epistrel/issues/776"

# --- E2: the omniscient-only lines ---------------------------------------------------------------

OMNISCIENT_LINE = "Viewpoint: Omniscient — history rewrites read and change the whole story"
AS_RECORDED_LINE = (
    "Viewpoint: Omniscient — the history as originally recorded is read only as omniscient"
)
OUTBOX_OMNISCIENT_LINE = "Viewpoint: Omniscient — every outbox row"  # the Outbox section (#325)
OMNISCIENT_REQUIRED_TEXT = (
    "The Engine refused a character viewpoint here — this page reads as omniscient only "
    "(a Console bug; report it)"
)

# --- E3: states, kinds and sentences -------------------------------------------------------------

STATE_TEXT = {
    "awaiting_answers": "Waiting for your answers",
    "awaiting_approval": "Ready to apply",
    "applying": "Applying…",
    "applied": "Applied",
    "cancelled": "Cancelled",
    "expired": "Expired",
    "abandoned": "Abandoned",
}
TERMINAL_STATES = frozenset({"applied", "cancelled", "expired", "abandoned"})
OPEN_STATES = frozenset({"awaiting_answers", "awaiting_approval"})
AWAITING_ANSWERS = "awaiting_answers"
AWAITING_APPROVAL = "awaiting_approval"

KIND_TEXT = {
    "rewrite": "Rewrite",
    "undo": "Undo",
    "redo": "Redo",
    "directed_resolution": "Directed regenerate",
    "support_check": "Support check",
    "entity_merge": "Entity merge",
    "directive": "Directive",
}
"""E3's session kinds, keyed on the Engine's ``SESSION_KINDS`` (the ``kind`` CHECK of
``core/tables/rewrites.py`` at origin/main; #346). A line edit is ``kind: rewrite`` too: see
:func:`kind_text`."""
REWRITE_KIND = "rewrite"
LINE_EDIT_TEXT = "Line edit"
TARGET_LABELS = {"undo": "Undoes", "redo": "Redoes"}
TARGET_LABEL = "Target amendment"

PREVIEW_MODE_TEXT = {"preview_first": "Preview first", "apply_directly": "Apply directly"}

CONFLICT_REASON_TEXT = {
    "dependent_amendment": "A later amendment relies on this",
    "state_presupposed": "A line presupposes the State's old value",
    "player_authored": "The player wrote this",
    "decision_effects": "A decision here committed external effects",
}
SEVERITY_TEXT = {"high": "High", "low": "Low"}
SEVERITY_ORDER = {"high": 0, "low": 1}

PREVIEW_PENDING = "The preview appears once the required questions are answered"
TRUNCATED_TEXT = "The Engine stopped counting what this touches — there may be more than shown"
REACH_TRUNCATED_TEXT = (
    "The search hit its call cap, so some lines may be missed — this session previews first"
)
STALE_COUNT_TEXT = "Recomputed {n} time(s) because the story changed"
HELD_TEXT = "Held reply (shown once applied)"
CALLS_TEXT = "{n} model call(s) to replay"
BASE_TEXT = "Planned at story revision {n}"
REACH_TEXT = "Searched turns {from_turn}–{to_turn} ({method})"  # noqa: RUF001
REPORT_APPLIED_TEXT = "{n} change(s) applied"
REPORT_RESOLUTIONS_TEXT = "{n} resolution(s)"
QUESTION_REJECTED_TEXT = "This question couldn't be read"
REFUSED_HEADING = "The Engine won't make these changes"
NOTIFICATIONS_HEADING = "Who is told"
RECOMMENDED_SUFFIX = " (recommended)"

ANSWER = "Answer"
APPLY = "Apply"
CANCEL = "Cancel"

# --- E5: refusal sentences -----------------------------------------------------------------------

STALE_TEXT = (
    "The story changed under this plan — the Engine recomputed it; review the new preview "
    "before applying"
)
BASE_MISMATCH_TEXT = "This preview is no longer the session's current plan — review the new preview"
PIPELINE_CHANGED_TEXT = (
    "The story's pipeline changed, so the Engine abandoned this session — open a new rewrite"
)
CLOSED_TEXT = "This session has already ended ({state})"
NOT_FOUND_TEXT = "No such rewrite session for this story"
SESSION_LIMIT_TEXT = (
    "This story already has the most open rewrite sessions allowed ({limit}) — finish or "
    "cancel one first"
)
QUESTIONS_PENDING_TEXT = "Answer the required questions before applying"
APPLY_IN_PROGRESS_TEXT = "This session is already being applied — reload in a moment"
RECORD_IN_FLIGHT_TEXT = "A turn is writing to what this plan touches — try again once it ends"
APPLY_FAILED_TEXT = "Applying failed and nothing was written — the session is ready to apply again"
UNPARSEABLE_TEXT = "The Engine couldn't read that as a change to the story — rephrase it"
NOOP_TEXT = "That wouldn't change anything in the story"
GUARDRAIL_TEXT = "That text breaks the story's input guardrails ({policies}) — nothing was written"
BASE_REVISION_REQUIRED_TEXT = "This apply named no plan revision — reload the session"
MERGE_INVALID_TEXT = {
    "too_few": "Pick at least two entities to merge",
    "not_in_story": "One of those entities isn't in this story",
    "type_mismatch": "Only entities of the same type can be merged",
    "already_same": "Those entities are already one referent",
}
AMENDMENT_TEXT = {
    "amendment_not_found": "No such amendment for this story",
    "amendment_already_reverted": "This amendment is already undone",
    "amendment_not_reverted": "Only an undone amendment can be redone",
    "amendment_already_redone": "This amendment has already been redone",
    "use_redo": "This is an undo — redo the amendment it undid instead",
}
DIRECTIVE_TEXT = {
    "directive_unparseable": "The Engine couldn't read that as a direction — rephrase it",
    "directive_no_changes": "That direction changes nothing",
    "directive_split_required": (
        "That mixes a change to now with a change to the past — send them as two directions"
    ),
    "directive_ambiguous": "That direction is ambiguous — be more specific",
}
STEER_BUDGET_TEXT = (
    "The queued steering for the next reply is already at its limit (500 tokens) — play a turn "
    "first"
)
REACH_FAILED_TEXT = (
    "The Engine couldn't search the story's lines for this rewrite (every search call failed) "
    "— try again"
)
SUPERSEDED_TEXT = (
    "The plan was recomputed and this question was replaced — answer the new one below"
)
NOT_EDITABLE_TEXT = "That isn't an editable line (it's a {kind}, not a message of the transcript)"
NOT_EDITABLE_NO_KIND_TEXT = "That isn't an editable line (not a message of the transcript)"
BEAT_REQUIRED_TEXT = "That turn has several lines — choose one:"
INACTIVE_TEXT = "That line isn't on the active timeline any more ({membership})"
INACTIVE_SWAP_TEXT = " — it's on turn {turn}'s variant {variant}; swap to it first"
AUTHOR_FACT_LIMIT_TEXT = (
    "This story already keeps {max} lasting author facts, the most it can — undo one (rewind "
    "past it) or nudge instead"
)

FIELD_INSTRUCTION = "instruction"
FIELD_TEXT = "text"
FIELD_ENTITY_IDS = "entity_ids"
LOCATOR_FIELDS = {
    "body.instruction": "instruction",
    "body.text": "text",
    "body.line_edit.new_text": "new_text",
    "body.directive": "directive",
    "body.directive.text": "directive",
}
ANSWER_LOCATOR = re.compile(r"^body\.answers\[(\d+)\]\.text$")
LITE_REWRITES = "lite_rewrites"
CANCEL_INSTRUCTION_LIMIT = 200  # #299: the Cancel link carries at most this much instruction
VARIANTS_ANCHOR = "#variants"

# --- E6: URL helpers -----------------------------------------------------------------------------


def _reversed(name: str, *ids: object) -> str | None:
    """``name`` reversed with ``ids``; ``None`` while it is unregistered (D19) or for a blank or
    non-string id."""
    if not all(isinstance(i, str) and i for i in ids):
        return None
    try:
        return reverse(name, args=list(ids))
    except NoReverseMatch:
        return None


def rewrites_url(story_id: object) -> str | None:
    """The story's Rewrite page (#297, ``stories:rewrites``)."""
    return _reversed("stories:rewrites", story_id)


def rewrite_session_url(story_id: object, session_id: object) -> str | None:
    """A rewrite session's page (#297, ``stories:rewrite``)."""
    return _reversed("stories:rewrite", story_id, session_id)


def rewrite_cancel_url(
    story_id: object,
    session_id: object,
    *,
    kind: object = None,
    state: object = None,
    instruction: object = None,
) -> str | None:
    """``stories:rewrite-cancel`` (#299's confirm page) with its display-only query (``kind``,
    ``state``, ``instruction``, each left out when blank; the instruction cut to
    :data:`CANCEL_INSTRUCTION_LIMIT` characters with "…"); ``None`` while unregistered (D19)."""
    url = _reversed("stories:rewrite-cancel", story_id, session_id)
    if url is None:
        return None
    query: dict[str, str] = {}
    for name, value in (("kind", kind), ("state", state), ("instruction", instruction)):
        text = _text(value)
        if text is None:
            continue
        if name == "instruction" and len(text) > CANCEL_INSTRUCTION_LIMIT:
            text = text[:CANCEL_INSTRUCTION_LIMIT] + "…"
        query[name] = text
    return f"{url}?{urlencode(query)}" if query else url


def amendments_url(story_id: object) -> str | None:
    """The story's Amendments page (#302, ``stories:amendments``)."""
    return _reversed("stories:amendments", story_id)


def amendment_url(story_id: object, amendment_id: object) -> str | None:
    """An amendment's page (#302, ``stories:amendment``)."""
    return _reversed("stories:amendment", story_id, amendment_id)


# --- E3/E23: small readers -----------------------------------------------------------------------


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _enum(value: object, table: Mapping[str, str]) -> str:
    """A known value through ``table``, an unknown string humanised, anything else "—"."""
    text = _text(value)
    if text is None:
        return EMPTY
    return table.get(text) or humanised(text)


def _filled(template: str, n: int, singular: str, plural_word: str | None = None) -> str:
    """A "{n} <noun>(s)" template with its noun slot pluralised (E23): ``STALE_COUNT_TEXT`` with
    2 → "Recomputed 2 times because the story changed"."""
    return template.replace(f"{{n}} {singular}(s)", plural(n, singular, plural_word))


def state_text(state: object) -> str:
    """E3's badge text: :data:`STATE_TEXT`, an unknown state humanised (C3), "—" for none."""
    return _enum(state, STATE_TEXT)


def kind_text(kind: object, instruction: object) -> str:
    """E3's kind text: :data:`KIND_TEXT`, an unknown kind humanised, "—" for none. The Engine
    stores a line edit as ``kind: rewrite`` and echoes no ``line_edit`` (#346), but its
    ``RewriteRequest`` takes exactly one of ``instruction`` and ``line_edit``, and every other
    ``rewrite`` session it opens carries an instruction. So a ``rewrite`` whose ``instruction``
    is null (or empty: the Cancel link leaves a blank one out) reads :data:`LINE_EDIT_TEXT`."""
    if _text(kind) == REWRITE_KIND and _text(instruction) is None:
        return LINE_EDIT_TEXT
    return _enum(kind, KIND_TEXT)


def severity_text(severity: object) -> str:
    """ "High" / "Low", another value humanised, "" for none (no badge)."""
    text = _text(severity)
    if text is None:
        return ""
    return SEVERITY_TEXT.get(text) or humanised(text)


def resolution_mode_text(value: Any) -> str:
    """E4: a stored ``resolution_mode`` (``"synchronous"``, ``"best_effort"``,
    ``{"threshold": "high" | "low"}``) as its choice label; anything else compact JSON."""
    labels = dict(RESOLUTION_CHOICES)
    if isinstance(value, str) and value in labels and value not in THRESHOLDS:
        return labels[value]
    if isinstance(value, Mapping) and set(value) == {"threshold"}:
        for choice, threshold in THRESHOLDS.items():
            if value["threshold"] == threshold:
                return labels[choice]
    return compact_json(value)


def preview_mode_text(value: object) -> str:
    return _enum(value, PREVIEW_MODE_TEXT)


# --- E3: entry views -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeRow:
    """One preview entry (a change, relabel, new entity, port, timing row, refused change or
    notification): its humanised kind, its summary, and the raw entry as compact JSON."""

    kind_text: str
    summary: str
    more: str


@dataclass(frozen=True)
class RadiusRow:
    """One blast-radius entry. ``severity`` is "High" / "Low" (another value humanised), "" when
    the entry has none; ``severity_raw`` is the Engine's value (the badge's class and
    ``title``). ``excerpt`` is ``None`` when the entry quotes nothing."""

    record_text: str
    action_text: str
    excerpt: str | None
    position_text: str
    severity: str
    reason_text: str
    options_text: str
    more: str
    severity_raw: str | None = None


def _summary(entry: Mapping[str, Any]) -> str:
    for key in ("summary", "text", "key"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return compact_json(entry, CELL_LIMIT)


def change_rows(entries: Iterable[Any] | None) -> tuple[ChangeRow, ...]:
    """E3's change rows: the humanised ``kind``, the entry's ``summary``/``text``/``key`` (else
    its compact JSON); a non-object entry reads "—" with its JSON in ``more``."""
    rows = []
    for entry in entries or ():
        if not isinstance(entry, Mapping):
            rows.append(ChangeRow(EMPTY, EMPTY, compact_json(entry)))
            continue
        rows.append(ChangeRow(_enum(entry.get("kind"), {}), _summary(entry), compact_json(entry)))
    return tuple(rows)


def _record_text(
    record: object, names: Mapping[str, str], entity_names: Mapping[str, str] | None
) -> str:
    """ "<humanised table> <name or short id>"; "—" unless ``record`` is a ``{table, id}``."""
    if not isinstance(record, Mapping):
        return EMPTY
    table, record_id = _text(record.get("table")), _text(record.get("id"))
    if table is None or record_id is None:
        return EMPTY
    labels = entity_names if table == "entities" and entity_names is not None else names
    return f"{humanised(table)} {labels.get(record_id) or short_id(record_id)}"


def _position_text(position: object) -> str:
    """A ``[turn, variant, slot_k, beat, step]`` list through C1; anything else "—"."""
    if not isinstance(position, list) or len(position) != 5:
        return EMPTY
    if not all(isinstance(p, int) and not isinstance(p, bool) for p in position):
        return EMPTY
    turn, variant, slot_k, beat, step = position
    return knowledge_position_text(
        NarrativePosition(turn=turn, variant=variant, slot_k=slot_k, beat=beat, step=step)
    )


def _options_text(options: object) -> str:
    if not isinstance(options, list):
        return EMPTY
    shown = [humanised(o) for o in options if isinstance(o, str) and o]
    return ", ".join(shown) if shown else EMPTY


def _radius_row(
    entry: Any, names: Mapping[str, str], entity_names: Mapping[str, str] | None
) -> RadiusRow:
    if not isinstance(entry, Mapping):
        return RadiusRow(EMPTY, EMPTY, None, EMPTY, "", EMPTY, EMPTY, compact_json(entry))
    excerpt = _text(entry.get("excerpt")) or _text(entry.get("before_excerpt"))
    return RadiusRow(
        record_text=_record_text(entry.get("record"), names, entity_names),
        action_text=_enum(entry.get("action"), {}),
        excerpt=excerpt,
        position_text=_position_text(entry.get("position")),
        severity=severity_text(entry.get("severity")),
        reason_text=_enum(entry.get("reason"), CONFLICT_REASON_TEXT),
        options_text=_options_text(entry.get("options")),
        more=compact_json(entry),
        severity_raw=_text(entry.get("severity")),
    )


def _severity_rank(entry: Any) -> int:
    severity = entry.get("severity") if isinstance(entry, Mapping) else None
    return SEVERITY_ORDER.get(severity, len(SEVERITY_ORDER)) if isinstance(severity, str) else 2


def radius_rows(
    entries: Iterable[Any] | None,
    bucket: str,
    names: Mapping[str, str],
    *,
    entity_names: Mapping[str, str] | None = None,
) -> tuple[RadiusRow, ...]:
    """E3's blast-radius rows for one bucket (``irreducible``, ``cascaded`` or ``incidental``).
    The conflicts (``irreducible``) sort High, then Low, then anything else, keeping the
    Engine's order within each. A malformed entry is a row reading "—", never an exception."""
    listed = list(entries or ())
    if bucket == "irreducible":
        listed.sort(key=_severity_rank)  # stable: the Engine's order within a severity
    return tuple(_radius_row(entry, names, entity_names) for entry in listed)


# --- E3: the session view ------------------------------------------------------------------------


@dataclass(frozen=True)
class OptionView:
    id: str
    label: str
    recommended: bool

    @property
    def label_text(self) -> str:
        """The label, with " (recommended)" on the Engine's recommended option."""
        return f"{self.label}{RECOMMENDED_SUFFIX}" if self.recommended else self.label


@dataclass(frozen=True)
class AnsweredView:
    """An answered question's answer (``quoted``: free text, shown in quotes), who gave it and
    when (raw, through ``principal_link`` and ``engine_time``); ``more`` holds the raw question
    when the answer names nothing the Console can show."""

    text: str
    by: str | None
    at: str | None
    quoted: bool = False
    more: str | None = None


@dataclass(frozen=True)
class QuestionView:
    """One rewrite question as ``_questions.html`` shows it and ``AnswersForm`` asks it.
    ``severity`` is "High" / "Low" (another value humanised), "" for none; ``severity_raw`` the
    Engine's value. ``recommended_id`` and ``reason`` are ``None`` when the Engine gave none."""

    id: str
    prompt: str
    severity: str
    required: bool
    options: tuple[OptionView, ...]
    recommended_id: str | None
    reason: str | None
    answered: AnsweredView | None
    severity_raw: str | None = None

    @property
    def required_text(self) -> str:
        return "Required" if self.required else "Optional"

    @property
    def why_text(self) -> str:
        """ "Why: <reason>" under the recommended option; "" without a reason."""
        return f"Why: {self.reason}" if self.reason else ""


@dataclass(frozen=True)
class ReachView:
    """A Lite session's reach line, and :data:`REACH_TRUNCATED_TEXT` ("" when not truncated)."""

    text: str
    truncated_text: str


@dataclass(frozen=True)
class PreviewView:
    base_text: str
    plan_hash: str
    changes: tuple[ChangeRow, ...]
    conflicts: tuple[RadiusRow, ...]
    cascaded: tuple[RadiusRow, ...]
    incidental: tuple[RadiusRow, ...]
    truncated: bool
    relabels: tuple[ChangeRow, ...]
    new_entities: tuple[ChangeRow, ...]
    suggested_ports: tuple[ChangeRow, ...]
    timing_uncertain: tuple[ChangeRow, ...]
    refused: tuple[ChangeRow, ...]
    notifications: tuple[ChangeRow, ...]
    reach: ReachView | None

    truncated_text: ClassVar[str] = TRUNCATED_TEXT
    refused_heading: ClassVar[str] = REFUSED_HEADING
    notifications_heading: ClassVar[str] = NOTIFICATIONS_HEADING

    @property
    def radius(self) -> tuple[tuple[str, tuple[RadiusRow, ...]], ...]:
        """The three blast-radius tables in E3's order, with their headings."""
        return (
            ("Conflicts", self.conflicts),
            ("Cascaded repairs", self.cascaded),
            ("Incidental mentions", self.incidental),
        )

    @property
    def extras(self) -> tuple[tuple[str, tuple[ChangeRow, ...]], ...]:
        """The relabels, new entities, suggested ports and timing rows that have entries."""
        tables = (
            ("Relabels", self.relabels),
            ("New entities", self.new_entities),
            ("Suggested ports", self.suggested_ports),
            ("Timing uncertain", self.timing_uncertain),
        )
        return tuple((heading, rows) for heading, rows in tables if rows)


@dataclass(frozen=True)
class HeldView:
    message_text: str
    calls_text: str
    heading: str = HELD_TEXT


@dataclass(frozen=True)
class SessionAction:
    label: str
    url: str
    method: str


@dataclass(frozen=True)
class SessionView:
    """E3's prepared session: every member the partial lays out (see the module docstring)."""

    id: str
    state: str
    state_text: str
    kind_text: str
    instruction: str
    pipeline_text: str
    resolution_text: str
    preview_mode_text: str
    expires_at: str | None
    expires_text: str
    stale_text: str
    last_error: tuple[str, str, str] | None
    result_amendment_id: str | None
    result_amendment_url: str | None
    target_amendment_id: str | None
    target_amendment_url: str | None
    target_label: str
    held_render: HeldView | None
    questions: tuple[QuestionView, ...]
    rejected_questions: tuple[str, ...]
    preview: PreviewView | None
    report_more: tuple[str, str] | None
    actions: tuple[SessionAction, ...]

    preview_pending: ClassVar[str] = PREVIEW_PENDING
    rejected_text: ClassVar[str] = QUESTION_REJECTED_TEXT

    @property
    def awaiting_answers(self) -> bool:
        return self.state == AWAITING_ANSWERS

    @property
    def answer_url(self) -> str | None:
        """The questions form's action (the "Answer" action), ``None`` without one."""
        return next((a.url for a in self.actions if a.label == ANSWER), None)

    @property
    def footer_actions(self) -> tuple[SessionAction, ...]:
        """The footer's actions: every action but "Answer" (the questions form's own)."""
        return tuple(a for a in self.actions if a.label != ANSWER)


def expires_text(expires_at: object, state: str) -> str:
    """ "expires in 42m", "expires in 3h 5m", "expires in <1m" while the session is open; ""
    once past, closed or unreadable."""
    if state not in OPEN_STATES or not isinstance(expires_at, str):
        return ""
    try:
        moment = datetime.fromisoformat(expires_at)
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    seconds = (moment - timezone.now()).total_seconds()
    if seconds <= 0:
        return ""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "expires in <1m"
    if minutes < 60:
        return f"expires in {minutes}m"
    return f"expires in {minutes // 60}h {minutes % 60}m"


def _answered(question: RewriteQuestion, options: Sequence[OptionView]) -> AnsweredView | None:
    if (
        question.answer_option_id is None
        and question.answer_text is None
        and question.answered_at is None
        and question.answered_by is None
        and not question.accepted_recommendation
    ):
        return None
    labels = {o.id: o.label for o in options}
    suffix = RECOMMENDED_SUFFIX if question.accepted_recommendation else ""
    by, at = question.answered_by, question.answered_at
    if question.answer_option_id:
        label = labels.get(question.answer_option_id) or question.answer_option_id
        return AnsweredView(f"{label}{suffix}", by, at)
    if question.answer_text:
        return AnsweredView(question.answer_text, by, at, quoted=True)
    if question.accepted_recommendation and question.recommended_option_id in labels:
        return AnsweredView(f"{labels[question.recommended_option_id]}{suffix}", by, at)
    raw = {
        "accepted_recommendation": question.accepted_recommendation,
        "answer_option_id": question.answer_option_id,
        "answer_text": question.answer_text,
        "answered_at": question.answered_at,
        "answered_by": question.answered_by,
    }
    return AnsweredView(EMPTY, by, at, more=compact_json(raw))


def question_view(question: RewriteQuestion) -> QuestionView:
    recommended = question.recommended_option_id or None
    options = tuple(
        OptionView(o.id, o.label, recommended is not None and o.id == recommended)
        for o in question.options
    )
    return QuestionView(
        id=question.id,
        prompt=question.prompt,
        severity=severity_text(question.severity),
        required=question.required,
        options=options,
        recommended_id=recommended,
        reason=question.recommendation_reason or None,
        answered=_answered(question, options),
        severity_raw=question.severity,
    )


def _notification_rows(
    entries: Iterable[Any] | None,
    names: Mapping[str, str],
    entity_names: Mapping[str, str] | None,
) -> tuple[ChangeRow, ...]:
    """ "Who is told": the humanised ``kind`` and the entry's ``text``/``message``, else its
    record ("<humanised table> <short id>"; the Engine's entries are ``{kind, record}``)."""
    rows = []
    for entry in entries or ():
        if not isinstance(entry, Mapping):
            rows.append(ChangeRow(EMPTY, EMPTY, compact_json(entry)))
            continue
        text = (
            _text(entry.get("text"))
            or _text(entry.get("message"))
            or _record_text(entry.get("record"), names, entity_names)
        )
        rows.append(ChangeRow(_enum(entry.get("kind"), {}), text, compact_json(entry)))
    return tuple(rows)


def _refused_rows(entries: Iterable[Any] | None) -> tuple[ChangeRow, ...]:
    """ "The Engine won't make these changes": the humanised ``reason`` and the refused change
    (its summary, else compact JSON)."""
    rows = []
    for entry in entries or ():
        if not isinstance(entry, Mapping):
            rows.append(ChangeRow(EMPTY, EMPTY, compact_json(entry)))
            continue
        change = entry.get("change")
        summary = (
            _summary(change)
            if isinstance(change, Mapping)
            else (EMPTY if change is None else compact_json(change, CELL_LIMIT))
        )
        rows.append(ChangeRow(_enum(entry.get("reason"), {}), summary, compact_json(entry)))
    return tuple(rows)


def _turn_text(value: object) -> str:
    return number_text(value) if _int(value) is not None else EMPTY


def _reach(reach: object) -> ReachView | None:
    """:data:`REACH_TEXT` over ``reach["searched"]`` (the Engine's ``meta()``), top-level
    ``from_turn``/``to_turn`` without one, "—" for what's missing."""
    if not isinstance(reach, Mapping):
        return None
    searched = reach.get("searched")
    source: Mapping[str, Any] = searched if isinstance(searched, Mapping) else {}
    if "searched" not in reach:
        source = reach
    text = REACH_TEXT.format(
        from_turn=_turn_text(source.get("from_turn")),
        to_turn=_turn_text(source.get("to_turn")),
        method=_enum(reach.get("method"), {}),
    )
    return ReachView(text, REACH_TRUNCATED_TEXT if reach.get("truncated") is True else "")


def preview_view(
    preview: RewritePreview,
    names: Mapping[str, str],
    entity_names: Mapping[str, str] | None = None,
) -> PreviewView:
    radius = preview.blast_radius
    return PreviewView(
        base_text=BASE_TEXT.format(n=number_text(preview.base_revision)),
        plan_hash=preview.plan_hash,
        changes=change_rows(preview.changes),
        conflicts=radius_rows(radius.irreducible, "irreducible", names, entity_names=entity_names),
        cascaded=radius_rows(radius.cascaded, "cascaded", names, entity_names=entity_names),
        incidental=radius_rows(radius.incidental, "incidental", names, entity_names=entity_names),
        truncated=preview.blast_radius_truncated,
        relabels=change_rows(preview.relabels),
        new_entities=change_rows(preview.new_entities),
        suggested_ports=change_rows(preview.suggested_ports),
        timing_uncertain=change_rows(preview.timing_uncertain),
        refused=_refused_rows(preview.refused_changes),
        notifications=_notification_rows(preview.notifications, names, entity_names),
        reach=_reach(preview.reach),
    )


def _report_more(report: Mapping[str, Any] | None) -> tuple[str, str] | None:
    if report is None:
        return None
    parts = []
    applied, resolutions = report.get("applied"), report.get("resolutions")
    if isinstance(applied, list):
        parts.append(_filled(REPORT_APPLIED_TEXT, len(applied), "change"))
    if isinstance(resolutions, list):
        parts.append(_filled(REPORT_RESOLUTIONS_TEXT, len(resolutions), "resolution"))
    return " · ".join(parts), more_json(report)


def _last_error(error: Mapping[str, Any] | None) -> tuple[str, str, str] | None:
    """``(code, detail, more)``: the humanised code ("—" for none); the detail is the Engine's
    ``message``, else (#350) its ``reasons`` read as E5's stale sentence reads them (each kind
    C3-humanised, de-duplicated, "; "-joined), else "" (the partial then shows the code alone,
    never "<code>: —"); the raw JSON for More."""
    if error is None:
        return None
    code = _text(error.get("code"))
    message = _text(error.get("message"))
    if message is None:
        reasons = error.get("reasons")
        kinds = _stale_kinds(reasons) if isinstance(reasons, list) else []
        message = "; ".join(kind for kind, _ in kinds)
    return (humanised(code) if code else EMPTY, message, more_json(error))


def _actions(session: RewriteSession, story_id: str) -> tuple[SessionAction, ...]:
    """E3's actions that resolve (D19): Answer while ``awaiting_answers``, Apply while
    ``awaiting_approval`` with a preview to apply (#299), and Cancel (#299's confirm link,
    carrying the display facts) while open."""
    state, session_id = session.state, session.id
    found: list[tuple[str, str | None, str]] = []
    if state == AWAITING_ANSWERS:
        found.append((ANSWER, _reversed("stories:rewrite-answers", story_id, session_id), "post"))
    if state == AWAITING_APPROVAL and session.preview is not None:
        found.append((APPLY, _reversed("stories:rewrite-apply", story_id, session_id), "post"))
    if state in OPEN_STATES:
        cancel = rewrite_cancel_url(
            story_id, session_id, kind=session.kind, state=state, instruction=session.instruction
        )
        found.append((CANCEL, cancel, "get"))
    return tuple(SessionAction(label, url, method) for label, url, method in found if url)


def session_view(
    session: RewriteSession,
    story_id: str,
    names: Mapping[str, str],
    *,
    entity_names: Mapping[str, str] | None = None,
) -> SessionView:
    """E3's :class:`SessionView` of ``session`` (a ``RewriteSession`` or
    ``RewriteSessionWritten``), labelled with C4's cast ``names`` (and ``entity_names`` for an
    entity merge's records)."""
    held = session.held_render
    unparsed = getattr(session, "unparsed", {}) or {}
    rejected = (
        rejected_entries(unparsed["questions"], RewriteQuestion.from_json)
        if "questions" in unparsed
        else []
    )
    stale = session.stale_count if _int(session.stale_count) else 0
    return SessionView(
        id=session.id,
        state=session.state,
        state_text=state_text(session.state),
        kind_text=kind_text(session.kind, session.instruction),
        instruction=session.instruction or EMPTY,
        pipeline_text=_enum(session.pipeline, {}),
        resolution_text=resolution_mode_text(session.resolution_mode),
        preview_mode_text=preview_mode_text(session.preview_mode),
        expires_at=session.expires_at,
        expires_text=expires_text(session.expires_at, session.state),
        stale_text=_filled(STALE_COUNT_TEXT, stale, "time") if stale > 0 else "",
        last_error=_last_error(session.last_error),
        result_amendment_id=session.result_amendment_id,
        result_amendment_url=amendment_url(story_id, session.result_amendment_id),
        target_amendment_id=session.target_amendment_id,
        target_amendment_url=amendment_url(story_id, session.target_amendment_id),
        target_label=TARGET_LABELS.get(session.kind, TARGET_LABEL),
        held_render=(
            None
            if held is None
            else HeldView(held.message_text, _filled(CALLS_TEXT, held.model_calls, "model call"))
        ),
        questions=tuple(question_view(q) for q in session.questions),
        rejected_questions=tuple(compact_json(entry) for entry in rejected),
        preview=(
            None if session.preview is None else preview_view(session.preview, names, entity_names)
        ),
        report_more=_report_more(session.report),
        actions=_actions(session, story_id),
    )


# --- E5: refusals --------------------------------------------------------------------------------


def _stale_kinds(reasons: Iterable[Any]) -> list[tuple[str, list[Any]]]:
    """Each reason's humanised ``kind`` (a bare string humanised as is), de-duplicated in the
    Engine's order, with the refs of every entry of that kind."""
    kinds: dict[str, list[Any]] = {}
    for entry in reasons:
        if isinstance(entry, Mapping):
            kind = _text(entry.get("kind"))
            refs = entry.get("refs")
            found = list(refs) if isinstance(refs, list) else []
        else:
            kind, found = _text(entry), []
        if kind is None:
            continue
        kinds.setdefault(humanised(kind), []).extend(found)
    return list(kinds.items())


def stale_reason_refs(details: object) -> list[tuple[str, list[dict[str, Any]]]]:
    """A ``rewrite_stale``'s ``(kind text, refs)`` pairs for the caller's More (#299); the refs
    are never in the sentence."""
    parsed = RewriteStaleDetails.from_details(details)
    if parsed is None:
        return []
    return [
        (kind, [ref for ref in refs if isinstance(ref, dict)])
        for kind, refs in _stale_kinds(parsed.reasons)
    ]


def _stale_text(details: Mapping[str, Any]) -> str:
    parsed = RewriteStaleDetails.from_details(details)
    reason = parsed.reason if parsed is not None else None
    if reason == "base_mismatch":
        return BASE_MISMATCH_TEXT
    if reason == "pipeline_changed":
        return PIPELINE_CHANGED_TEXT
    kinds = [kind for kind, _ in _stale_kinds(parsed.reasons if parsed is not None else [])]
    return f"{STALE_TEXT} ({'; '.join(kinds)})" if kinds else STALE_TEXT


def _locators(violation: Mapping[str, Any]) -> list[str]:
    found = []
    if _text(violation.get("locator")):
        found.append(violation["locator"])
    locators = violation.get("locators")
    if isinstance(locators, list):
        found.extend(loc for loc in locators if isinstance(loc, str) and loc)
    return found


def _guardrail(details: Mapping[str, Any]) -> WorldRefusal:
    violations = details.get("violations")
    entries = (
        [v for v in violations if isinstance(v, Mapping)] if isinstance(violations, list) else []
    )
    policies: list[str] = []
    for violation in entries:
        name = _text(violation.get("name")) or _text(violation.get("policy_id"))
        if name is not None and name not in policies:
            policies.append(name)
    text = (
        GUARDRAIL_TEXT.format(policies=", ".join(policies))
        if policies
        else GUARDRAIL_TEXT.replace(" ({policies})", "")
    )
    locator = next((loc for v in entries for loc in _locators(v)), None)
    if locator is None:
        return WorldRefusal(text)
    if ANSWER_LOCATOR.match(locator):
        return WorldRefusal(text, field=locator)  # the caller maps n to ``text_<qid>``
    return WorldRefusal(text, field=LOCATOR_FIELDS.get(locator))


def guardrail_answer_indexes(details: object) -> list[int | None]:
    """A ``guardrail_violation``'s locators in order, each read as the ``n`` of
    ``body.answers[n].text`` (the index among the free-text answers sent, E5), ``None`` for any
    other or malformed locator. The answers caller (#298) maps each ``n`` to its ``text_<qid>``
    box; an empty list means the refusal named no locator."""
    if not isinstance(details, Mapping):
        return []
    violations = details.get("violations")
    if not isinstance(violations, list):
        return []
    found: list[int | None] = []
    for violation in violations:
        if not isinstance(violation, Mapping):
            continue
        for locator in _locators(violation):
            match = ANSWER_LOCATOR.match(locator)
            found.append(int(match.group(1)) if match else None)
    return found


def _inactive(details: Mapping[str, Any], story_id: str) -> WorldRefusal:
    membership = _text(details.get("membership"))
    text = (
        INACTIVE_TEXT.format(membership=humanised(membership))
        if membership is not None
        else INACTIVE_TEXT.replace(" ({membership})", "")
    )
    dormant = details.get("dormant_variant")
    if isinstance(dormant, Mapping):
        turn, variant = _int(dormant.get("turn")), _int(dormant.get("variant"))
        if turn is not None and variant is not None:
            text += INACTIVE_SWAP_TEXT.format(turn=number_text(turn), variant=variant)
            history = _reversed("stories:history", story_id)
            return WorldRefusal(text, f"{history}{VARIANTS_ANCHOR}" if history else "")
    return WorldRefusal(text)


def _fact_limit(details: Mapping[str, Any]) -> str:
    most = _int(details.get("max"))
    if most is None:
        return AUTHOR_FACT_LIMIT_TEXT.replace("keeps {max} lasting", "keeps the most lasting")
    return AUTHOR_FACT_LIMIT_TEXT.format(max=number_text(most))


def _log_omniscient(exc: EngineError, story_id: str) -> None:
    """E2: a Console bug, logged once in ``log_engine_error``'s shape plus the story id."""
    log.warning("engine error %s %s story %s", exc.http_status, exc.code, story_id)


def rewrite_refusal(
    exc: EngineClientError,
    story_id: str,
    verb_phrase: str,
    effective: Mapping[str, Any] | None = None,
) -> WorldRefusal | None:
    """E5's exact text for a refused rewrite, amendment, merge or directive write; any other code
    falls through to D9's ``world_refusal`` (then C5). ``None`` sends the caller to
    ``panel_for``."""
    if not isinstance(exc, EngineError):
        return None
    details: Mapping[str, Any] = exc.details if isinstance(exc.details, Mapping) else {}
    code = exc.code
    fixed = {
        "rewrite_session_not_found": NOT_FOUND_TEXT,
        "rewrite_questions_pending": QUESTIONS_PENDING_TEXT,
        "rewrite_apply_in_progress": APPLY_IN_PROGRESS_TEXT,
        "record_in_flight": RECORD_IN_FLIGHT_TEXT,
        "rewrite_apply_failed": APPLY_FAILED_TEXT,
        "base_revision_required": BASE_REVISION_REQUIRED_TEXT,
        "steer_budget_exceeded": STEER_BUDGET_TEXT,
        "rewrite_reach_failed": REACH_FAILED_TEXT,
        "rewrite_question_superseded": SUPERSEDED_TEXT,
        "beat_required": BEAT_REQUIRED_TEXT,
        **AMENDMENT_TEXT,
    }
    if code in fixed:
        return WorldRefusal(fixed[code])
    if code == "rewrite_stale":
        return WorldRefusal(_stale_text(details))
    if code == "rewrite_session_closed":
        state = _text(details.get("state"))
        return WorldRefusal(CLOSED_TEXT.format(state=state_text(state) if state else "closed"))
    if code == "rewrite_session_limit":
        limit = _int(details.get("limit"))
        shown = number_text(limit) if limit is not None else "the limit"
        return WorldRefusal(SESSION_LIMIT_TEXT.format(limit=shown), rewrites_url(story_id) or "")
    if code == "rewrite_unparseable":
        return WorldRefusal(UNPARSEABLE_TEXT, field=FIELD_INSTRUCTION)
    if code == "rewrite_noop":
        return WorldRefusal(NOOP_TEXT, field=FIELD_INSTRUCTION)
    if code == "guardrail_violation":
        return _guardrail(details)
    if code == "invalid_answer":
        question_id = _text(details.get("question_id"))
        return WorldRefusal(exc.message, field=f"choice_{question_id}" if question_id else None)
    if code == "module_off" and details.get("module") == LITE_REWRITES:
        reason = unavailable_for(LITE_REWRITES, effective).reason
        return WorldRefusal(reason, _reversed("stories:config", story_id) or "")
    if code == "omniscient_required":
        _log_omniscient(exc, story_id)
        return WorldRefusal(OMNISCIENT_REQUIRED_TEXT)
    if code == "merge_invalid":
        merge_reason = _text(details.get("reason"))
        text = MERGE_INVALID_TEXT.get(merge_reason or "") or exc.message
        return WorldRefusal(text, field=FIELD_ENTITY_IDS)
    if code in DIRECTIVE_TEXT:
        return WorldRefusal(DIRECTIVE_TEXT[code], field=FIELD_TEXT)
    if code == "line_not_editable":
        kind = _text(details.get("event_kind"))
        if kind is None:
            return WorldRefusal(NOT_EDITABLE_NO_KIND_TEXT)
        return WorldRefusal(NOT_EDITABLE_TEXT.format(kind=humanised(kind)))
    if code == "record_inactive":
        return _inactive(details, story_id)
    if code == "author_fact_limit":
        return WorldRefusal(_fact_limit(details))
    return world_refusal(exc, story_id, verb_phrase, effective)


# --- #297: the Rewrite page's session list and the open's flash ----------------------------------

NO_SESSIONS = "No rewrite sessions yet"
NO_SESSIONS_MATCH = "No sessions in the chosen states"
NO_MORE_SESSIONS = "No more sessions after this one"
SESSION_REJECTED_TEXT = "This session couldn't be read"
QUESTIONS_OPEN_TEXT = "{n} open"

# E21's session flashes, by the written state. #297's open passes its own verb; a later story
# that opens, answers or re-plans a session passes its own texts to :func:`session_write_flash`.
OPEN_VERB = "Rewrite opened"
APPLIED_DIRECTLY_TEXT = "Applied directly: amendment {amendment} · story revision now {revision}"
QUESTIONS_TEXT = "The Engine has {n} question(s) before it can plan"
PLANNED_TEXT = "Planned: review the preview, then apply it"
SESSION_NOW_TEXT = "{verb} — the session is now {state}"
NOT_APPLIED_PREFIX = "Not applied directly: "
KEY_TAIL = " — idempotency key <code>{}</code>"


@dataclass(frozen=True)
class RewriteRow:
    """One row of the Rewrite page's session list, every cell pre-rendered: ``state`` raw (the
    badge's ``title``), ``opened_text`` / ``expires_text`` through ``engine_time`` (HTML), and
    ``url`` the session page (``None`` while unregistered)."""

    state: str
    state_text: str
    kind_text: str
    instruction: str
    questions_text: str
    opened_text: str
    expires_text: str
    session_id: str
    url: str | None


def _questions_text(summary: RewriteSummary) -> str:
    open_questions = _int(summary.open_questions)
    if summary.state in OPEN_STATES and open_questions is not None and open_questions > 0:
        return QUESTIONS_OPEN_TEXT.format(n=number_text(open_questions))
    return EMPTY


def rewrite_rows(page: RewriteList, story_id: str) -> tuple[RewriteRow, ...]:
    """The parsed sessions of ``page`` as :class:`RewriteRow`, in the Engine's order (newest
    first). Questions read "<n> open" only for an open state with open questions (a terminal
    session can still report some); Expires only for an open state; "—" otherwise."""
    return tuple(
        RewriteRow(
            state=summary.state,
            state_text=state_text(summary.state),
            kind_text=kind_text(summary.kind, summary.instruction),
            instruction=summary.instruction or EMPTY,
            questions_text=_questions_text(summary),
            opened_text=engine_time(summary.created_at),
            expires_text=engine_time(summary.expires_at) if summary.state in OPEN_STATES else EMPTY,
            session_id=summary.id,
            url=rewrite_session_url(story_id, summary.id),
        )
        for summary in page.items
    )


def rejected_sessions(page: RewriteList) -> list[Any]:
    """The page's raw entries that didn't parse (D15), in the Engine's order (for More)."""
    unparsed = getattr(page, "unparsed", {}) or {}
    if "items" not in unparsed:
        return []
    return rejected_entries(unparsed["items"], _summary_of)


def _summary_of(entry: object) -> RewriteSummary | None:
    return RewriteSummary.from_json(entry)


def rejected_notice(n: int) -> str:
    """D15's notice: "1 session couldn't be read" / "N sessions couldn't be read"."""
    return f"{plural(n, 'session')} couldn't be read"


def unanswered_count(session: RewriteSession) -> int:
    """The session's parsed questions with no answer yet (E21's questions-flash count)."""
    return sum(1 for q in session.questions if question_view(q).answered is None)


def _amendment_html(story_id: str, amendment_id: str) -> SafeString:
    url = amendment_url(story_id, amendment_id)
    if url is None:
        return format_html("<code>{}</code>", amendment_id)
    return format_html('<a href="{}"><code>{}</code></a>', url, amendment_id)


def session_write_flash(
    written: RewriteSessionWritten,
    story_id: str,
    key: str,
    *,
    apply_directly: bool,
    verb: str = OPEN_VERB,
    planned: str = PLANNED_TEXT,
    questions: str = QUESTIONS_TEXT,
    applied: str = APPLIED_DIRECTLY_TEXT,
) -> SafeString:
    """E21's flash for a write that opened, answered or re-planned a session, picked from the
    written state: ``apply`` set → ``applied`` (the amendment's full id in ``<code>``, linked
    through :func:`amendment_url` when it resolves, and the post-apply revision);
    ``awaiting_answers`` → ``questions`` with the unanswered count (C11 plural);
    ``awaiting_approval`` → ``planned``; else "<verb> — the session is now <STATE_TEXT>". With
    "Apply directly" ticked and no ``apply``, it starts :data:`NOT_APPLIED_PREFIX`. It ends with
    #43's key tail (the answer's key, else ``key``)."""
    tail = format_html(KEY_TAIL, written.idempotency_key or key)
    done = written.apply
    if done is not None:
        revision = done.timeline_revision
        if _int(revision) is None:
            revision = written.timeline_revision
        amendment_slot, revision_slot = applied.split("{amendment}", 1)
        return format_html(
            "{}{}{}{}",
            amendment_slot,
            _amendment_html(story_id, done.amendment_id),
            revision_slot.replace("{revision}", number_text(revision)),
            tail,
        )
    if written.state == AWAITING_ANSWERS:
        text = _filled(questions, unanswered_count(written), "question")
    elif written.state == AWAITING_APPROVAL:
        text = planned
    else:
        text = SESSION_NOW_TEXT.format(verb=verb, state=state_text(written.state))
    if apply_directly:
        text = f"{NOT_APPLIED_PREFIX}{text}"
    return format_html("{}{}", text, tail)


# --- #299: a stale plan's notice ------------------------------------------------------------------

CARRIED_TEXT = "Answers carried over: {n}"


@dataclass(frozen=True)
class StaleNotice:
    """A refused apply's ``rewrite_stale`` notice on the session page (#299; a stable contract
    for #301): E5's sentence (``text``; ``url`` is reserved for #301 and ``None`` here), the
    count of carried answers (``None`` unless ``carried_answers`` is a non-empty list), the
    reasons' refs by humanised kind (:func:`stale_reason_refs`), and whether the preview shown
    comes from the refusal itself (the re-read failed). ``stale_text`` is E3's stale count, set
    only when ``from_refusal`` (otherwise the partial shows it); ``more`` is the raw reasons and
    carried answers for More ("" when both are empty)."""

    text: str
    url: str | None
    carried_count: int | None
    reason_refs: list[tuple[str, list[dict[str, Any]]]]
    from_refusal: bool
    stale_text: str = ""
    more: str = ""

    @property
    def carried_text(self) -> str:
        return CARRIED_TEXT.format(n=number_text(self.carried_count)) if self.carried_count else ""

    @property
    def ref_rows(self) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
        """One row per humanised kind: each ref as ``(humanised table, id)``, "—" for either
        when missing (E23)."""
        return tuple(
            (
                kind,
                tuple(
                    (
                        humanised(_text(ref.get("table")) or "") or EMPTY,
                        _text(ref.get("id")) or EMPTY,
                    )
                    for ref in refs
                ),
            )
            for kind, refs in self.reason_refs
        )


def stale_notice(text: str, details: object, *, from_refusal: bool = False) -> StaleNotice:
    """The :class:`StaleNotice` for a ``rewrite_stale`` refusal worded ``text`` (E5's), over its
    ``details``. ``base_mismatch`` and ``pipeline_changed`` details carry no reasons or carried
    answers, so they read as empty."""
    parsed = RewriteStaleDetails.from_details(details)
    carried = parsed.carried_answers if parsed is not None else None
    count = len(carried) if isinstance(carried, list) and carried else None
    reasons = parsed.reasons if parsed is not None else []
    stale = _int(parsed.stale_count) if parsed is not None else None
    raw: dict[str, Any] = {}
    if reasons:
        raw["reasons"] = reasons
    if isinstance(carried, list) and carried:
        raw["carried_answers"] = carried
    return StaleNotice(
        text=text,
        url=None,
        carried_count=count,
        reason_refs=stale_reason_refs(details),
        from_refusal=from_refusal,
        stale_text=(_filled(STALE_COUNT_TEXT, stale, "time") if from_refusal and stale else ""),
        more=more_json(raw) if raw else "",
    )

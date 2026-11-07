"""What the history browser (#52) shows, built from one ``GET /stories/{id}/history`` page.

- :func:`turn_groups`: the page's items under per-turn headers ("Turn N · variant V"), one row
  per item, with the turn's audit records under it and "no reply" marks;
- :func:`audit_record`: a ``guardrail_output_rejected`` (or other audit) item, read from its
  text, which the Engine fills with the event payload's canonical JSON; an ``erasure_marker``
  (#87) read from its ``[erasure marker: <id>, <scope>]`` text;
- :func:`policy_names`: policy id → name from ``GET /stories/{id}/settings``;
- :func:`operation_links`: Engine operation id → the Console's operation-log row (#44);
- :func:`explain_query` / :func:`explain_url`: a structured turn's "Explain retrieval" link
  into the Retrieval explorer (#142), rebuilt from the turn's transcript items;
- :func:`beat_trace`: a structured beat's labels, visibility, highlighted detected claims and
  Trace (#147), from the item and, when loaded, its turn's eval hooks (#139);
- :func:`audience_filter_label`, :func:`fidelity_badge` and :func:`narrated_label`: the audience
  filter a character read applied, how the viewpoint perceived each beat and who narrated it
  (#62; the play transcript reuses them, #196). One wording and style (convention 20).

M6 (#254): each group carries the State changes its turn committed (#245's shared rows, D10,
at every viewpoint) and whether it is an unfinished staged turn (D13's Incomplete badge, never
"no reply"); on an omniscient read each beat carries the changes it committed (K6).

#53 builds its timeline controls onto these groups.

M7 (#304): the history **as originally recorded** (``view=as_recorded``, FR-STORE-23) reuses
:func:`turn_groups` for its items; this module holds its wording (:data:`AS_RECORDED_NOTE`,
:data:`AS_RECORDED_CONTROLS_NOTE`, :data:`ERASED_SINCE`, the checkpoint refusals), the checkpoint
reader :func:`parse_boundary` (D7's ``N`` / ``N+k``), :func:`checkpoint_text`,
:func:`erasure_rows` and :func:`derived_rows` (``derived`` raw, E15; E23's rules). E2's
``AS_RECORDED_LINE`` and ``OMNISCIENT_REQUIRED_TEXT`` stay in :mod:`console.rewrites` (E1).
"""

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from django.urls import reverse
from django.utils.text import slugify

from console.effects import INCOMPLETE_TEXT, INCOMPLETE_TITLE, engine_operation_url
from console.engine.models import (
    BeatProposition,
    BeatVisibility,
    DetectedClaim,
    History,
    HistoryItem,
    RepairRung,
    StorySettings,
    SummarySegment,
    UtteranceLabels,
    VariantChain,
    Verdict,
    beat_entries,
)
from console.inspect import retrieval_url
from console.models import EngineOperation
from console.notices import NoticeCards
from console.raw import EMPTY, plural
from console.turn_trace import MorePart, TurnTrace, declared_beat, inline_json
from console.wording import character_label, humanised
from console.world import slot_turn_text

if TYPE_CHECKING:
    from console.feedback import FeedbackWidget
    from console.turn_world import StateChangeRow

INGEST_KIND = "ingest"  # the operation-log kind of an ingest batch (the client method's name)
REJECTED_KIND = "guardrail_output_rejected"
AUDIT_CHANNEL = "audit"
REPLY_CHANNELS = frozenset({"message", "narration"})
REPLY_KIND = "message_rendered"
NAME_WITHHELD = "(name withheld — erasure pending)"
MARKER_KIND = "erasure_marker"  # an audit item a deleted record left (#87, Engine brief §8)
INPUT_KIND = "player_input"  # a turn's input: a ``say`` item, then a ``do`` item
EXPLAIN_QUERY_MAX = 2000  # the turn pipeline's retrieval query cut (structured/pipeline.py)
MARKER_TEXT = re.compile(r"^\[erasure marker: (?P<id>[^,\]]+), (?P<scope>[^\]]+)\]$")
INGEST_SCAN = 500  # newest ingest rows of the story looked through for a batch's operations


# --- Policies and audit records -----------------------------------------------------------------


def policy_names(settings: StorySettings) -> dict[str, str | None]:
    """Policy id → name, from the effective then the inactive policies. A duplicated id takes
    the first effective entry; a ``None`` name is a pending catalog erasure."""
    names: dict[str, str | None] = {}
    for policy in [*settings.effective, *settings.inactive]:
        names.setdefault(policy.id, policy.name)
    return names


@dataclass(frozen=True)
class AuditPolicy:
    """One policy an audit record names: its id, the label shown and the policy's message."""

    id: str
    label: str  # the name, NAME_WITHHELD, or the raw id when unresolved
    resolved: bool
    message: str


@dataclass(frozen=True)
class ErasureMarker:
    """An ``erasure_marker`` audit item's text, parsed: the erased subject's id (shown raw,
    never resolved) and the erasure's scope."""

    subject_id: str
    scope: str

    @property
    def scope_label(self) -> str:
        """ "story scope", "catalog scope", or any other scope raw, the same way."""
        return f"{self.scope} scope"


def erasure_marker(text: str) -> ErasureMarker | None:
    found = MARKER_TEXT.match(text)
    return ErasureMarker(found["id"], found["scope"]) if found else None


@dataclass(frozen=True)
class AuditRecord:
    """An audit item as shown under its turn. ``draft`` is the rejected text (``None`` when the
    record holds none); ``raw`` is the item's text when it is not a JSON object (an
    unparsable erasure marker); ``marker`` is a parsed erasure marker (#87)."""

    item: HistoryItem
    reason: str = ""
    attempt: int | None = None
    policies: tuple[AuditPolicy, ...] = ()
    draft: str | None = None
    raw: str = ""
    marker: ErasureMarker | None = None

    @property
    def is_marker(self) -> bool:
        return self.item.kind == MARKER_KIND

    @property
    def policy_line(self) -> str:
        """``<kind> — <policy id>, …`` (the Demo's "guardrail_output_rejected —
        minors_no_sexual_content")."""
        ids = ", ".join(p.id for p in self.policies)
        return f"{self.item.kind} — {ids}" if ids else self.item.kind


def _payload(text: str) -> dict[str, Any] | None:
    try:
        found = json.loads(text)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _policy_label(policy_id: str, names: Mapping[str, str | None]) -> tuple[str, bool]:
    if policy_id not in names:
        return policy_id, False
    name = names[policy_id]
    return (name if name else NAME_WITHHELD), True


def audit_record(item: HistoryItem, names: Mapping[str, str | None]) -> AuditRecord:
    """``item`` (an audit-channel item) with its payload read: the policy ids (``policy_ids``,
    else the violations'), each violation's message and the rejected ``text``. An
    ``erasure_marker`` is routed first: its text parsed, else kept raw."""
    if item.kind == MARKER_KIND:
        marker = erasure_marker(item.text)
        return AuditRecord(item, marker=marker) if marker else AuditRecord(item, raw=item.text)
    payload = _payload(item.text)
    if payload is None:
        return AuditRecord(item, raw=item.text)
    messages: dict[str, str] = {}
    violations = payload.get("violations")
    for violation in violations if isinstance(violations, list) else []:
        if isinstance(violation, dict) and isinstance(violation.get("policy_id"), str):
            message = violation.get("message")
            messages.setdefault(
                str(violation["policy_id"]), message if isinstance(message, str) else ""
            )
    raw_ids = payload.get("policy_ids")
    ids = [i for i in raw_ids if isinstance(i, str)] if isinstance(raw_ids, list) else []
    for policy_id in messages:
        if policy_id not in ids:
            ids.append(policy_id)
    policies = []
    for policy_id in ids:
        label, resolved = _policy_label(policy_id, names)
        policies.append(AuditPolicy(policy_id, label, resolved, messages.get(policy_id, "")))
    text = payload.get("text")
    attempt = payload.get("attempt")
    reason = payload.get("reason")
    return AuditRecord(
        item,
        reason=reason if isinstance(reason, str) else "",
        attempt=attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else None,
        policies=tuple(policies),
        draft=text if isinstance(text, str) else None,
        raw="" if isinstance(text, str) else item.text,
    )


def split_audit(history: History) -> tuple[list[HistoryItem], list[HistoryItem]]:
    """``(items, audit)``. The Engine sends audit records in ``audit`` beside ``items``; should
    an answer carry none there, audit-channel and ``guardrail_output_rejected`` items found in
    ``items`` are routed to the audit display instead (planner)."""
    entries = history.entries
    audit = history.audit_entries
    if audit is not None:
        return entries, audit

    def is_audit(item: HistoryItem) -> bool:
        return item.channel == AUDIT_CHANNEL or item.kind == REJECTED_KIND

    return [i for i in entries if not is_audit(i)], [i for i in entries if is_audit(i)]


# --- Operation links ----------------------------------------------------------------------------


def operation_links(story_id: str, operation_ids: Iterable[str]) -> dict[str, int]:
    """Engine operation id → the newest operation-log row sharing it; an ingest batch's row
    stands for every operation in its ``response.results``. Ids the Console never logged are
    absent (shown as plain text)."""
    wanted = {i for i in operation_ids if i}
    links: dict[str, int] = {}
    if not wanted:
        return links
    for op_id, pk in EngineOperation.objects.filter(engine_operation_id__in=wanted).values_list(
        "engine_operation_id", "pk"
    ):  # newest first (the model's ordering)
        links.setdefault(op_id, pk)
    missing = wanted - links.keys()
    if missing:
        rows = EngineOperation.objects.filter(
            story_id=story_id, kind__startswith=INGEST_KIND, response__isnull=False
        ).values_list("pk", "response")[:INGEST_SCAN]
        for pk, response in rows:
            results = response.get("results") if isinstance(response, dict) else None
            for result in results if isinstance(results, list) else []:
                found = result.get("operation_id") if isinstance(result, dict) else None
                if isinstance(found, str) and found in missing:
                    links.setdefault(found, pk)
    return links


# --- Audience filter, fidelity and narrators (#62) ----------------------------------------------

ENVELOPE = "envelope"
NOT_PERCEIVED = "none"
PLAYER_NARRATOR = "player"  # ``narrated_by`` of a beat the player narrated (co_author, C16)
AUDIENCE_FILTER_LABELS = {
    "none": "None (perception off: every beat in full)",
    "co_location": "Co-location",
    "full": "Full",
    "nothing_perceived": "Nothing perceived (this viewpoint perceived none of these beats)",
    "bypassed_by_player_view": (
        "Bypassed by player view: every beat in full, marked with what this character would "
        "have perceived"
    ),
}


def audience_filter_label(value: str) -> tuple[str, str]:
    """``(label, raw)`` for a history read's ``audience_filter``; an unknown value humanised
    (C3)."""
    return AUDIENCE_FILTER_LABELS.get(value) or humanised(value), value


@dataclass(frozen=True)
class FidelityBadge:
    """How the viewpoint perceived a beat, when not in full: the badge's text and ``title``,
    and the class added to the item's row (``""``: none)."""

    text: str
    title: str
    row_class: str = ""


FIDELITY_BADGES: dict[str, FidelityBadge] = {
    ENVELOPE: FidelityBadge(
        "envelope",
        "This character perceived only that it happened; the text is the envelope",
        "fidelity-envelope",
    ),
    NOT_PERCEIVED: FidelityBadge(
        "not perceived",
        "Shown because player view is story; this character did not perceive this beat",
        "fidelity-none",
    ),
}


def fidelity_badge(value: str | None) -> FidelityBadge | None:
    """``None`` for ``content`` or no fidelity; an unknown value's badge is humanised (C3),
    titled with the raw value, with no row class."""
    if not value or value == "content":
        return None
    return FIDELITY_BADGES.get(value) or FidelityBadge(humanised(value), value)


def narrated_label(item: HistoryItem, labels: Mapping[str, str]) -> str:
    """The Actor column: the actor's label ("—" when none), then " (narrated by <name>)" for a
    narrated item: the narrator's cast label (a short id outside the cast, C4), or "the player"
    (C16)."""
    actor = _label(item.actor_character_id, labels) if item.actor_character_id else "—"
    if not item.narrated_by:
        return actor
    narrator = (
        "the player"
        if item.narrated_by == PLAYER_NARRATOR
        else character_label(item.narrated_by, labels)
    )
    return f"{actor} (narrated by {narrator})"


# --- Turn groups --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One item's table row."""

    item: HistoryItem
    actor: str  # the actor's label, "—" when none
    render: tuple[str, ...]  # render characters' labels (raw id when not in the cast)
    log_pk: int | None  # the operation-log row its operation id links to
    same_message: bool = False  # shares message_id with the row before it (say + do)
    feedback: FeedbackWidget | None = None  # the "Rate" control (#83, set by the view)
    trace: BeatTrace | None = None  # a structured beat's trace (#147, set by the view)
    fidelity: FidelityBadge | None = None  # an envelope / not-perceived beat's badge (#62)
    reaction: bool = False  # narrated by a primary, not the player (#202, #201's rule)
    # #250: the Engine operation page for an operation id this Console's log doesn't hold
    # (D19: ``None`` for a non-ULID id, or while ``operations:engine`` is unregistered).
    engine_url: str | None = None
    # #254 (K6): the State changes this beat committed, omniscient reads only (#245's rows), and
    # the raw entries that didn't parse.
    beat_changes: tuple[StateChangeRow, ...] = ()
    beat_changes_unparsed: tuple[Any, ...] = ()

    @property
    def beat_effects_summary(self) -> str:
        """ "1 State change at this beat" / "N State changes at this beat"; a beat whose every
        entry failed adds " · N couldn't be read"."""
        count = len(self.beat_changes)
        text = f"{count} State change{'' if count == 1 else 's'} at this beat"
        if not count and self.beat_changes_unparsed:
            text += f" · {len(self.beat_changes_unparsed)} couldn't be read"
        return text

    @property
    def beat_effects_unread(self) -> str:
        return unread_line(len(self.beat_changes_unparsed))


def unread_line(count: int) -> str:
    """#245's "couldn't be read" line for a State-change list ("" for none)."""
    if not count:
        return ""
    return f"{count} State change{'' if count == 1 else 's'} couldn't be read"


@dataclass
class TurnGroup:
    turn: int
    variant: int | None
    rows: list[Row] = field(default_factory=list)
    audit: list[AuditRecord] = field(default_factory=list)
    no_reply: bool = False
    explain_url: str | None = None  # "Explain retrieval" (#142, set by the view)
    perception_url: str | None = None  # "Perception" (#195, set by the view)
    render_order: str = ""  # "Renders: A → B" for a multi-render turn (#202)
    report_url: str | None = None  # "Report" (#203, set by the view): the play inspector
    # #254: the turn's State changes (#245's rows) and the raw entries that didn't parse, from
    # its TurnSummary; an unfinished staged turn (D13), its badge linking the turn's Engine
    # operation page (D19: ``None`` while unregistered or no row carries an id).
    state_changes: tuple[StateChangeRow, ...] = ()
    state_changes_unparsed: tuple[Any, ...] = ()
    incomplete: bool = False
    incomplete_url: str | None = None
    # #185: the turn's co-author notices: the summary's raw ``notices`` (its whole raw list when
    # an entry didn't parse, else the typed list), turned into cards by the view.
    notices_raw: Any = None
    notices: NoticeCards | None = None

    @property
    def state_changes_unread(self) -> str:
        return unread_line(len(self.state_changes_unparsed))

    @property
    def incomplete_text(self) -> str:
        return INCOMPLETE_TEXT

    @property
    def incomplete_title(self) -> str:
        return INCOMPLETE_TITLE


def _label(character_id: str, labels: Mapping[str, str]) -> str:
    return labels.get(character_id, character_id)


def is_reply(item: HistoryItem) -> bool:
    return item.kind == REPLY_KIND or item.channel in REPLY_CHANNELS


def turn_groups(
    history: History,
    items: Sequence[HistoryItem],
    audit: Sequence[AuditRecord],
    *,
    labels: Mapping[str, str],
    links: Mapping[str, int],
    actor_filtered: bool,
    omniscient: bool = False,
    story_id: str = "",
) -> list[TurnGroup]:
    """The page's turns in ascending order. Audit records render under their turn only for
    turns on this page (an erasure marker with no turn under turn 0, which only the oldest page
    holds). A turn ≥ 1 with no reply item says "no reply" (not while an actor filter is
    set, nor for an incomplete turn, #254). #202: a reaction row is marked, and a group's
    render order comes from its first ``message_rendered`` row on this page.

    #254: each group gets its ``TurnSummary``'s State changes (the first summary of a
    ``(turn, variant)`` wins) and ``incomplete``; with ``omniscient``, each row its beat's
    changes. ``labels`` name the scopes (C4); ``story_id`` builds the outbox links (D11)."""
    from console.renders import is_reaction, render_order_line  # console.renders imports us
    from console.turn_world import state_change_rows  # console.turn_world → console.world

    groups: dict[tuple[int, int | None], TurnGroup] = {}

    def group(turn: int, variant: int | None) -> TurnGroup:
        key = (turn, variant)
        if key not in groups:
            groups[key] = TurnGroup(turn, variant)
        return groups[key]

    summarised: set[tuple[int, int | None]] = set()
    for summary in history.turns:
        owner = group(summary.turn, summary.variant)
        if (summary.turn, summary.variant) in summarised:
            continue  # a duplicate summary: the first wins
        summarised.add((summary.turn, summary.variant))
        raw = summary.unparsed.get("state_effects")
        owner.state_changes, owner.state_changes_unparsed = state_change_rows(
            raw if raw is not None else summary.state_effects, story_id, labels
        )
        owner.incomplete = summary.incomplete
        raw_notices = summary.unparsed.get("notices")  # #185: the whole raw list, else typed
        owner.notices_raw = raw_notices if raw_notices is not None else summary.notices
    previous: HistoryItem | None = None
    for item in items:
        same = (
            previous is not None
            and previous.message_id == item.message_id
            and previous.turn == item.turn
        )
        render = tuple(_label(c, labels) for c in item.render_character_ids or ())
        beat_changes: tuple[StateChangeRow, ...] = ()
        beat_unparsed: tuple[Any, ...] = ()
        if omniscient:
            beat_changes = state_change_rows(item.effect_entries, story_id, labels)[0]
            beat_unparsed = tuple(item.effects_unparsed)
        row = Row(
            item,
            narrated_label(item, labels),
            render,
            links.get(item.operation_id),
            same,
            fidelity=fidelity_badge(item.fidelity),
            reaction=is_reaction(item),
            engine_url=(
                engine_operation_url(item.operation_id)
                if links.get(item.operation_id) is None
                else None
            ),
            beat_changes=beat_changes,
            beat_changes_unparsed=beat_unparsed,
        )
        owner = group(item.turn or 0, item.variant)
        if item.kind == REPLY_KIND and not any(r.item.kind == REPLY_KIND for r in owner.rows):
            owner.render_order = render_order_line(item, labels)
        owner.rows.append(row)
        previous = item
    by_turn: dict[int, TurnGroup] = {}
    for g in groups.values():
        by_turn.setdefault(g.turn, g)
    for record in audit:
        turn = record.item.turn or 0
        target = groups.get((turn, record.item.variant)) or by_turn.get(turn)
        if target is None and record.is_marker and turn == 0 and history.next_before_turn is None:
            target = by_turn.setdefault(0, group(0, None))
        if target is not None:
            target.audit.append(record)
    ordered = sorted(groups.values(), key=lambda g: (g.turn, g.variant or 0))
    for g in ordered:
        g.no_reply = (
            not actor_filtered
            and not g.incomplete
            and g.turn >= 1
            and not any(is_reply(r.item) for r in g.rows)
        )
        if g.incomplete:
            operation = next((r.item.operation_id for r in g.rows if r.item.operation_id), None)
            g.incomplete_url = engine_operation_url(operation)
    return ordered


# --- Explain retrieval (#142) -------------------------------------------------------------------


@dataclass(frozen=True)
class ExplainQuery:
    """A past turn's retrieval, re-run: the turn's input as the question, asked as the character
    who acted, as of just before the turn."""

    q: str
    as_of: int
    actor: str
    turn: int
    variant: int | None


def explain_query(
    items: Sequence[HistoryItem],
    turn: int,
    variant: int | None,
    story_structured: Callable[[], bool] | None = None,
) -> ExplainQuery | None:
    """The re-run for turn ``turn`` from its items as a page shows them, or ``None`` when there
    is none to offer: turn 0, a Lite turn, no input on the page, a blank input or no actor.

    "Structured" is any item of the turn carrying ``propositions``: the Engine sets it on every
    ``beat_*`` item and on a ``player_input`` that has labels, so a do-only input (no labels, no
    ``propositions``) still counts through its turn's beats (#172). When no shown item carries
    it (a do-only input whose beats the viewpoint did not perceive, #181), ``story_structured``
    decides: the story runs the structured pipeline. It is called only then.

    The input is the first ``player_input`` message's items (Engine order: say, then do; an
    ingest batch with several at one turn uses the first), their non-empty texts joined with
    ``"\n"`` as the Engine's ``joined_input`` does, cut to 2,000 code points. Redacted and
    quarantined items are used as they read; the explorer's banner is the caveat."""
    inputs = [i for i in items if i.kind == INPUT_KIND]
    if turn < 1 or not inputs:
        return None
    if not any(i.propositions is not None for i in items) and not (
        story_structured is not None and story_structured()
    ):
        return None
    first = inputs[0]
    parts = [i for i in inputs if i.message_id == first.message_id]
    q = "\n".join(i.text for i in parts if i.text)[:EXPLAIN_QUERY_MAX]
    if not q.strip() or first.actor_character_id is None:
        return None
    return ExplainQuery(q, turn - 1, first.actor_character_id, turn, variant)


def explain_url(story_id: str, query: ExplainQuery) -> str | None:
    """The explorer pre-filled with ``query`` (``q``, ``as_of``, ``actor``, ``explain``, then
    ``explain_variant`` for a variant ≥ 1), or ``None`` while ``inspect:retrieval`` isn't
    registered. Following it makes the one retrieve read; building it makes none."""
    params = {
        "q": query.q,
        "as_of": str(query.as_of),
        "actor": query.actor,
        "explain": str(query.turn),
    }
    if query.variant is not None and query.variant >= 1:
        params["explain_variant"] = str(query.variant)
    return retrieval_url(story_id, params)


# --- Beat traces (#147) -------------------------------------------------------------------------

# A beat trace's ``state``: what is known of its turn's escalation and repair.
LOADED = "loaded"  # the turn's eval hooks were read
NO_HOOKS = "no_hooks"  # the read succeeded and the turn recorded no eval hooks
NOT_LOADED = "not_loaded"  # no read was made ("Show all traces" off, past the cap, the play page)
UNAVAILABLE = "unavailable"  # the read failed or missed the deadline
NO_TRACE = "none"  # an ingested operation: nothing was verified or repaired
NOT_CHECKED = "not checked"  # convention 9: a null verdict
SAY_CHANNEL = "say"
DO_CHANNEL = "do"
SAFE_FALLBACK_RUNG = 4  # the repair ladder's last rung (orchestrator/repair/ladder)
REPAIRED_RUNGS = (1, 2, 3)
EN_DASH = "\u2013"  # between the bounds of "(invalid span 12-40)"


@dataclass(frozen=True)
class LabelTag:
    """One utterance label as a tag: the humanised value, ``title`` "intent: sincere";
    ``css_class`` is added after ``beat-label`` (the classification tag's, a corrected intent's)."""

    text: str
    title: str
    css_class: str = ""


@dataclass(frozen=True)
class Segment:
    """A run of a beat's text and the detected claims covering all of it (Engine order,
    duplicates kept); no claims: plain text."""

    text: str
    claims: tuple[DetectedClaim, ...] = ()

    @property
    def title(self) -> str:
        """``<category> · <verdict or not checked>`` per covering claim, joined with "; "."""
        return "; ".join(f"{c.category} · {c.verdict or NOT_CHECKED}" for c in self.claims)


@dataclass(frozen=True)
class DetectedRow:
    """A detected claim's Trace row: the span's text, or ``note`` when it isn't shown."""

    claim: DetectedClaim
    span: str | None
    note: str = ""


@dataclass(frozen=True)
class Person:
    """A character a latent beat is visible to: the cast label, ``None`` for a non-cast id."""

    id: str
    name: str | None


@dataclass(frozen=True)
class BeatTrace:
    """What one structured beat shows (``_beat_trace.html``). ``declared`` and ``repairs`` are
    the turn's eval-hook entries for this beat, read only in ``state`` :data:`LOADED`."""

    story_id: str
    item: HistoryItem
    state: str
    tags: tuple[LabelTag, ...]
    visibility: BeatVisibility | None
    visibility_json: str | None  # a ``visibility`` that doesn't parse, as compact JSON
    visible_to: tuple[Person, ...]
    segments: tuple[Segment, ...]  # empty on a redacted item (its text shows as read)
    propositions: tuple[BeatProposition, ...]
    detected: tuple[DetectedRow, ...]
    detected_count: int  # every ``detected`` entry, readable or not
    declared: tuple[Verdict, ...]
    repairs: tuple[RepairRung, ...]
    repair_line: str
    shared_with_say: bool  # a player input's ``do`` part, which repeats the ``say`` part's
    unavailable_url: str  # the turn in the play page's inspector (#139's trace block)
    more: tuple[MorePart, ...]
    predicate_names: Mapping[str, str] = field(default_factory=dict)
    cast_names: Mapping[str, str] = field(default_factory=dict)
    inspector_hint: bool = False  # the play page: escalation and repair live in the inspector
    open: bool = False  # "Show all traces": the Trace starts open

    @property
    def summary(self) -> str:
        """ "Trace · N proposition(s) · M detected"."""
        n = len(self.propositions)
        return f"Trace · {n} proposition{'' if n == 1 else 's'} · {self.detected_count} detected"

    @property
    def beat_known(self) -> bool:
        return self.item.beat is not None

    @property
    def classified(self) -> bool:
        """A classification tag is shown: the legend explains it (#207)."""
        return any(t.css_class.startswith(CLASSIFICATION_CLASS) for t in self.tags)


CLASSIFICATION_CLASS = "beat-classification"
CORRECTED_CLASS = "intent-corrected"
CORRECTED_SUFFIX = " (corrected)"
CORRECTED_TITLE = (
    "intent: {raw} — declared deceptive; relabelled sincere because the speaker's beliefs "
    "support it"
)


def label_tags(labels: UtteranceLabels) -> tuple[LabelTag, ...]:
    """Intent · belief · objective, humanised (convention 5), the raw value in ``title``; then,
    when the Engine classified the say (#207, omniscient reads only), its classification
    (humanised, C3; ``title`` "classification: lie", class ``beat-classification
    classification-<slug>``). An ``intent_corrected`` intent reads "Sincere (corrected)"."""
    if labels.intent_corrected:
        intent = LabelTag(
            humanised(labels.intent) + CORRECTED_SUFFIX,
            CORRECTED_TITLE.format(raw=labels.intent),
            CORRECTED_CLASS,
        )
    else:
        intent = LabelTag(humanised(labels.intent), f"intent: {labels.intent}")
    tags = [
        intent,
        LabelTag(humanised(labels.belief_relation), f"belief: {labels.belief_relation}"),
        LabelTag(humanised(labels.objective_relation), f"objective: {labels.objective_relation}"),
    ]
    raw = labels.classification
    if raw:
        slug = slugify(raw)
        css = f"{CLASSIFICATION_CLASS} classification-{slug}" if slug else CLASSIFICATION_CLASS
        tags.append(LabelTag(humanised(raw), f"classification: {raw}", css))
    return tuple(tags)


def valid_span(claim: DetectedClaim, text: str) -> bool:
    """``0 ≤ start < end ≤ len(text)`` (Python indices: code points, as the Engine's)."""
    return 0 <= claim.start < claim.end <= len(text)


def segments(text: str, claims: Sequence[DetectedClaim]) -> tuple[Segment, ...]:
    """``text`` cut at every valid span's bounds; each run carries every claim covering it, in
    Engine order. Invalid spans are ignored; adjacent unmarked runs merge."""
    valid = [c for c in claims if valid_span(c, text)]
    bounds = sorted({0, len(text), *(c.start for c in valid), *(c.end for c in valid)})
    found: list[Segment] = []
    for low, high in pairwise(bounds):
        covering = tuple(c for c in valid if c.start <= low and high <= c.end)
        run = text[low:high]
        if not covering and found and not found[-1].claims:
            found[-1] = Segment(found[-1].text + run)
        else:
            found.append(Segment(run, covering))
    return tuple(found)


def detected_rows(
    text: str, claims: Sequence[DetectedClaim], *, redacted: bool
) -> tuple[DetectedRow, ...]:
    rows = []
    for c in claims:
        if redacted:
            rows.append(DetectedRow(c, None, "(span unavailable: redacted)"))
        elif valid_span(c, text):
            rows.append(DetectedRow(c, text[c.start : c.end]))
        else:
            rows.append(DetectedRow(c, None, f"(invalid span {c.start}{EN_DASH}{c.end})"))
    return tuple(rows)


def repair_line(
    beat: int | None, declared: Sequence[Verdict], repairs: Sequence[RepairRung]
) -> str:
    """One phrase per repair entry, joined with "; "; "Not repaired" when the beat declared
    claims and none was repaired, "None" when it declared none, "—" for an item with no beat."""
    if beat is None:
        return "—"
    if repairs:
        return "; ".join(_rung_phrase(r) for r in repairs)
    return "Not repaired" if declared else "None"


def _rung_phrase(rung: RepairRung) -> str:
    if rung.rung in REPAIRED_RUNGS and rung.accepted is True:
        return f"Repaired at rung {rung.rung} (accepted)"
    if rung.rung == SAFE_FALLBACK_RUNG:
        return f"Safe fallback (rung {SAFE_FALLBACK_RUNG})"
    return f"Rung {rung.rung}"


def turn_label(turn: int | None, variant: int | None) -> str:
    """The transcript's turn label: "T3", "T3.2" for a variant other than 1."""
    label = f"T{turn or 0}"
    return f"{label}.{variant}" if variant and variant != 1 else label


def report_item(items: Sequence[HistoryItem]) -> HistoryItem | None:
    """#203: a turn group's chosen item (its last reply item, else its last item) when it was
    generated with an operation id, which its "Report" link opens; else ``None``."""
    replies = [item for item in items if is_reply(item)]
    chosen = (replies or list(items))[-1:]
    if not chosen or chosen[0].origin != "generated" or not chosen[0].operation_id:
        return None
    return chosen[0]


def inspector_url(story_id: str, item: HistoryItem) -> str:
    """The turn selected in the play page's inspector, where #139's trace block reads it."""
    base = reverse("stories:detail", args=[story_id])
    return f"{base}?{urlencode({'operation': item.operation_id, 'origin': item.origin})}"


def _state(item: HistoryItem, trace: TurnTrace | None, state: str) -> str:
    if item.origin == "ingested":
        return NO_TRACE
    if trace is None:
        return state
    return LOADED if trace.eval_hooks is not None else NO_HOOKS


def _more(
    item: HistoryItem,
    labels: UtteranceLabels | None,
    visibility: BeatVisibility | None,
    propositions: Sequence[BeatProposition],
    skipped_propositions: Sequence[tuple[int, Any]],
    claims: Sequence[DetectedClaim],
    skipped_claims: Sequence[tuple[int, Any]],
) -> tuple[MorePart, ...]:
    """Convention 13: the raw parts the Trace doesn't name."""
    parts: list[MorePart] = []
    if item.labels is not None and (labels is None or labels.extras):
        parts.append(MorePart("labels", item.labels))
    if visibility is not None and visibility.extras:
        parts.append(MorePart("visibility", item.visibility))
    for name, raw, parsed, skipped in (
        ("proposition", item.propositions, propositions, skipped_propositions),
        ("detected", item.detected, claims, skipped_claims),
    ):
        if raw is not None and not isinstance(raw, list):
            parts.append(MorePart(f"{name}s" if name == "proposition" else name, raw))
        entries = [(e.index, e.extras) for e in parsed if e.extras] + list(skipped)
        parts += [MorePart(f"{name} {i}", v) for i, v in sorted(entries, key=lambda p: p[0])]
    return tuple(parts)


def beat_trace(
    item: HistoryItem,
    trace: TurnTrace | None,
    state: str = NOT_LOADED,
    *,
    story_id: str,
    cast_names: Mapping[str, str] | None = None,
    predicate_names: Mapping[str, str] | None = None,
    inspector_hint: bool = False,
    open_trace: bool = False,
) -> BeatTrace | None:
    """``None`` for an item that isn't a structured beat (:func:`is_structured`). Otherwise
    the beat's trace. ``state`` is the caller's (:data:`NOT_LOADED`, :data:`UNAVAILABLE`) when
    there is no ``trace``; with one it follows the read (no hooks; loaded). An ingested item
    is always :data:`NO_TRACE`."""
    if not is_structured(item):
        return None
    names = cast_names or {}
    labels = UtteranceLabels.from_json(item.labels) if item.labels is not None else None
    visibility = BeatVisibility.from_json(item.visibility) if item.visibility is not None else None
    propositions, skipped_propositions = beat_entries(item.propositions, BeatProposition.from_json)
    claims, skipped_claims = beat_entries(item.detected, DetectedClaim.from_json)
    shown = _state(item, trace, state)
    hooks = trace.eval_hooks if trace is not None and shown == LOADED else None
    beat = item.beat
    declared: tuple[Verdict, ...] = ()
    repairs: tuple[RepairRung, ...] = ()
    if hooks is not None and beat is not None:
        declared = tuple(v for v in hooks.verdicts if declared_beat(v.claim_id) == beat)
        repairs = tuple(r for r in hooks.repair_rungs if r.beat == beat)
    return BeatTrace(
        story_id=story_id,
        item=item,
        state=shown,
        tags=label_tags(labels) if labels is not None else (),
        visibility=visibility,
        visibility_json=(
            inline_json(item.visibility).full
            if item.visibility is not None and visibility is None
            else None
        ),
        visible_to=tuple(Person(c, names.get(c)) for c in visibility.characters)
        if visibility is not None
        else (),
        segments=() if item.redacted else segments(item.text, claims),
        propositions=propositions,
        detected=detected_rows(item.text, claims, redacted=item.redacted),
        detected_count=len(claims) + len(skipped_claims),
        declared=declared,
        repairs=repairs,
        repair_line=repair_line(beat, declared, repairs),
        shared_with_say=item.kind == INPUT_KIND and item.channel == DO_CHANNEL,
        unavailable_url=inspector_url(story_id, item),
        more=_more(
            item,
            labels,
            visibility,
            propositions,
            skipped_propositions,
            claims,
            skipped_claims,
        ),
        predicate_names=predicate_names or {},
        cast_names=names,
        inspector_hint=inspector_hint,
        open=open_trace,
    )


def is_structured(item: HistoryItem) -> bool:
    """A structured beat with a trace: ``propositions`` is set (possibly ``[]``) and the beat
    isn't an envelope (#62: the Engine sends an envelope with ``propositions: []`` and no
    labels; it reads as plain text)."""
    return item.propositions is not None and item.fidelity != ENVELOPE


def summary_rows(segments: Sequence[SummarySegment] | None) -> list[SummarySegment]:
    """Summary segments ordered by the first turn they cover."""
    return sorted(segments or (), key=lambda s: (s.covers_from, s.level))


# --- Variants -----------------------------------------------------------------------------------


def chain_key(chain: VariantChain) -> str:
    """A chain's ``variant`` query value, ``T:V``."""
    return f"{chain.turn}:{chain.variant}"


def chain_tip(chains: Sequence[VariantChain], variant: str) -> int | None:
    """The tip turn of the chain ``variant`` (``T:V``) names, if listed."""
    for chain in chains:
        if chain_key(chain) == variant:
            return chain.tip_turn
    return None


# --- As originally recorded (#304) --------------------------------------------------------------

AS_RECORDED_NOTE = (
    "What the story held when this checkpoint committed, before any later amendment, discard or "
    "derived record. Read-only: rewrites, swaps and forks act on the corrected story."
)
AS_RECORDED_CONTROLS_NOTE = (
    "Not used on the as-recorded view: every category is shown, audit included"
)
ERASED_SINCE = "Erased since: {count} — erased content can't be shown as it was recorded"
CHECKPOINT_NOT_FOUND = "No checkpoint at {checkpoint} was ever committed on this chain"
NO_CHECKPOINT_YET = "No committed checkpoint on this chain"
CHECKPOINT_INCOMPLETE = (
    "That checkpoint's staged turn is still under way — try again once it settles"
)
INVALID_VIEW_TEXT = "The Engine didn't recognise this history view (a Console bug; report it)"
DERIVED_KINDS = (
    ("facts", "Facts"),
    ("beliefs", "Beliefs"),
    ("memories", "Memories"),
    ("states", "States"),
)
_CHECKPOINT = re.compile(r"^([0-9]+)(?:\+([0-9]+))?$")


def parse_boundary(text: str | None) -> tuple[int, int | None] | None:
    """D7's cleaned boundary ``"12"`` / ``"12+1"`` → ``(12, None)`` / ``(12, 1)``; anything
    else (``None``, blank, malformed) → ``None``."""
    if not isinstance(text, str):
        return None
    match = _CHECKPOINT.match(text)
    if match is None:
        return None
    k = match.group(2)
    return int(match.group(1)), int(k) if k is not None else None


def checkpoint_text(checkpoint: str | None, latest_turn: int | None = None) -> str:
    """The header's checkpoint: D7's "turn 2" / "turn 2⁺¹" for a requested checkpoint, else
    "the latest (turn <n>)" (the answer's ``as_of``); a malformed one as given."""
    parsed = parse_boundary(checkpoint)
    if parsed is not None:
        return slot_turn_text(*parsed)
    if checkpoint:
        return checkpoint
    return f"the latest (turn {latest_turn if latest_turn is not None else EMPTY})"


@dataclass(frozen=True)
class ErasureRow:
    """One erasure marker of an as-recorded answer (``{erasure_id, at}``, E15 raw): each
    missing member reads "—" (E23), with the raw entry kept for More when it is malformed."""

    erasure_id: str
    at: str | None  # the raw ISO time, for ``engine_time``; ``None`` reads "—"
    raw: Any = None  # the entry, when it isn't a well-formed marker


def erasure_rows(markers: object) -> list[ErasureRow]:
    """The answer's ``erasure_markers`` in the Engine's order; a non-list reads as none."""
    if not isinstance(markers, list):
        return []
    rows = []
    for entry in markers:
        found = entry if isinstance(entry, Mapping) else {}
        erasure_id, at = found.get("erasure_id"), found.get("at")
        shown_id = erasure_id if isinstance(erasure_id, str) and erasure_id else None
        shown_at = at if isinstance(at, str) and at else None
        well_formed = shown_id is not None and shown_at is not None
        rows.append(ErasureRow(shown_id or EMPTY, shown_at, None if well_formed else entry))
    return rows


def erased_since(count: int) -> str:
    """ "Erased since: 1 erasure — …" (C11 pluralisation)."""
    return ERASED_SINCE.format(count=plural(count, "erasure"))


@dataclass(frozen=True)
class DerivedRow:
    """One "Records at the checkpoint" row: "Facts · 1", the entries raw (E15) for More."""

    key: str
    label: str
    count: str  # "1", or "—" for a missing or non-list member
    entries: tuple[Any, ...] = ()


def derived_rows(derived: object) -> list[DerivedRow] | None:
    """The four rows from the answer's ``derived`` (facts, beliefs, memories, States); ``None``
    when ``derived`` is null or not an object (the section is left out)."""
    if not isinstance(derived, Mapping):
        return None
    rows = []
    for key, label in DERIVED_KINDS:
        value = derived.get(key)
        if isinstance(value, list):
            rows.append(DerivedRow(key, label, f"{len(value):,}", tuple(value)))
        else:
            rows.append(DerivedRow(key, label, EMPTY))
    return rows

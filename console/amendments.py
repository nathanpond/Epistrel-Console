"""A story's amendments (#302; FR-REWRITE-8,9,10,11; E2, E6, E15, E22, E23): the wording and
the row views of the Amendments list (``stories:amendments``) and an amendment's page
(``stories:amendment``).

E1 assigns no amendment wording to another module, so this one holds it: the amendment kinds
(:data:`AMENDMENT_KIND_TEXT`, a different enum from E3's session ``KIND_TEXT``), the status
(:data:`STATUS_TEXT`, ``reverted`` read as "Undone" to match E5's "This amendment is already
undone"; the raw value stays in ``title`` and the filter sends ``reverted``), the part kinds,
and the list's empty, rejected and truncated texts. #303 adds its Undo/Redo action helper here.
It imports E2's lines, E5's refusals and E6's URL helpers from ``console/rewrites.py`` and
defines none of them.

**Rows never raise (E15, E23).** ``parts``, ``relies_on`` and ``dependents`` are raw JSON. A
part that isn't an object, or whose ``ordinal`` isn't an int, is a :class:`PartRow` anchored
``part-bad-<index>`` whose cells read "—", the raw entry under More; a ``relies_on`` or
``dependents`` entry that isn't an object (or has no ``edge_type``) is the same, anchored
``relies-bad-<i>`` / ``dependent-bad-<i>``. A missing member reads "—" in its own cell. The What
column reads only members the Engine's ``PART_COLUMNS`` names; every other member stays under
More (``patch_before``/``patch_after`` are raw JSON whose shape depends on the record table, so
they are never rendered as a diff). Rows keep the Engine's order.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from console.canon import EntityRef, entity_ref, lineage_url
from console.engine.models import AmendmentPage, AmendmentSummary, rejected_entries
from console.inspect import short_id
from console.positions import valid_minutes_text
from console.predicates import PredicateRef
from console.raw import EMPTY, number_text
from console.rewrites import amendment_url, rewrite_session_url
from console.wording import humanised

AMENDMENTS_PAGE = 50  # convention 6: the Engine's default (it allows 200)

AMENDMENT_KIND_TEXT = {
    "rewrite": "Rewrite",
    "undo": "Undo",
    "redo": "Redo",
    "directed_resolution": "Directed regenerate",
}
STATUS_TEXT = {"active": "Active", "reverted": "Undone"}
PART_KIND_TEXT = {"proposition": "Proposition", "record": "Record", "lite_note": "Note"}
PROPOSITION = "proposition"
RECORD = "record"
LITE_NOTE = "lite_note"
NOTE_TEXT = "Note"

UNDOES = "Undoes"
REDOES = "Redoes"
UNDONE_BY = "Undone by"
TIMING_UNCERTAIN_TEXT = "Timing uncertain"

# E22's empty texts, first match wins: a cursor page, then the status filter, then none.
NO_AMENDMENTS = "This story has no amendments yet"
NO_ACTIVE = "No active amendments"
NO_UNDONE = "No undone amendments"
NO_MORE_AMENDMENTS = "No more amendments after this one"
FILTERED_EMPTY = {"active": NO_ACTIVE, "reverted": NO_UNDONE}
UNPARSED_TEXT = "Some amendments couldn't be read"
AMENDMENT_REJECTED_TEXT = "This amendment couldn't be read"

NO_PARTS = "No parts"
NO_RELIES = "Nothing recorded (a Lite amendment records none)"
NO_DEPENDENTS = "Nothing depends on it"
LATER_PART_TEXT = "A later amendment's part"
AMENDMENT_PARTS = "amendment_parts"
DEPENDENTS_TRUNCATED = "The Engine stopped listing dependents at its search cap — there may be more"
BACK_TEXT = "Back to Amendments"

DETAIL_VERB = "read this amendment"


# --- small readers -------------------------------------------------------------------------------


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class EnumCell:
    """An enum value as shown (E23): ``text`` from its map, an unknown value humanised, "—" for
    none; ``raw`` the value for ``title`` (and the badge's ``status-<raw>`` class)."""

    text: str
    raw: str


def enum_cell(value: object, table: Mapping[str, str]) -> EnumCell:
    raw = _text(value)
    if raw is None:
        return EnumCell(EMPTY, "")
    return EnumCell(table.get(raw) or humanised(raw), raw)


def kind_cell(kind: object) -> EnumCell:
    return enum_cell(kind, AMENDMENT_KIND_TEXT)


def status_cell(status: object) -> EnumCell:
    """An amendment's or a part's status (one enum, one map)."""
    return enum_cell(status, STATUS_TEXT)


def story_time_text(valid_from: object, valid_to: object) -> str:
    """``valid_minutes_text`` over a part's halves, "—" when both are null or either is a
    bool or a non-int."""
    if valid_from is None and valid_to is None:
        return EMPTY
    halves = []
    for half in (valid_from, valid_to):
        if half is not None and _int(half) is None:
            return EMPTY
        halves.append(_int(half))
    return valid_minutes_text(halves[0], halves[1])


# --- relations ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationLine:
    """One Relations line: "Undoes" / "Redoes" / "Undone by" and the amendment id, linked to
    its page through E6's ``amendment_url`` (``None`` while unregistered: plain ``<code>``)."""

    label: str
    amendment_id: str
    url: str | None


def relation_lines(summary: AmendmentSummary, story_id: str) -> tuple[RelationLine, ...]:
    """The amendment's set relations in a fixed order; empty when none is set ("—")."""
    pairs = ((UNDOES, summary.undoes), (REDOES, summary.redoes), (UNDONE_BY, summary.reverted_by))
    return tuple(
        RelationLine(label, value, amendment_url(story_id, value))
        for label, value in pairs
        if value
    )


# --- the list ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AmendmentRow:
    """One row of the Amendments list (and the detail page's summary): ``tx_time`` raw for
    ``engine_time``, the kind and status cells, the principal, the part count (``{:,}``), the
    relations, the session (linked through E6's ``rewrite_session_url``) and the amendment's
    own link (``amendment_url``)."""

    id: str
    tx_time: str
    kind: EnumCell
    status: EnumCell
    author: str
    parts_text: str
    relations: tuple[RelationLine, ...]
    session_id: str | None
    session_url: str | None
    url: str | None


def amendment_row(summary: AmendmentSummary, story_id: str) -> AmendmentRow:
    session = _text(summary.session_id)
    return AmendmentRow(
        id=summary.id,
        tx_time=summary.tx_time,
        kind=kind_cell(summary.kind),
        status=status_cell(summary.status),
        author=summary.author,
        parts_text=number_text(summary.parts_count),
        relations=relation_lines(summary, story_id),
        session_id=session,
        session_url=rewrite_session_url(story_id, session) if session else None,
        url=amendment_url(story_id, summary.id),
    )


def amendment_rows(page: AmendmentPage, story_id: str) -> tuple[AmendmentRow, ...]:
    """The page's parsed amendments, in the Engine's order (newest first)."""
    return tuple(amendment_row(summary, story_id) for summary in page.items)


def rejected_amendments(page: AmendmentPage) -> list[Any]:
    """The page's raw entries that didn't parse (D15), in the Engine's order."""
    unparsed = getattr(page, "unparsed", {}) or {}
    if "items" not in unparsed:
        return []
    return rejected_entries(unparsed["items"], AmendmentSummary.from_json)


def empty_text(*, status: str | None, cursor: str | None) -> str:
    """E22's empty text for a page with no entry at all."""
    if cursor:
        return NO_MORE_AMENDMENTS
    if status:
        return FILTERED_EMPTY.get(status, NO_AMENDMENTS)
    return NO_AMENDMENTS


# --- the detail: parts ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PartRow:
    """One part, every cell pre-read. ``anchor`` is ``part-<ordinal>`` (``part-bad-<i>`` for a
    malformed part, whose cells all read "—"). What is either a proposition (``proposition``
    true: ``subject`` and ``predicate``, each ``None`` for "—") or ``what`` text plus a record's
    ``record_id`` (a short id) and ``record_suffixes`` (" · <action>", " · <strategy>")."""

    anchor: str
    ordinal: str
    kind: EnumCell
    proposition: bool
    subject: EntityRef | None
    predicate: PredicateRef | None
    what: str
    record_id: str | None
    record_suffixes: tuple[str, ...]
    status: EnumCell
    story_time: str
    timing_uncertain: bool
    fact_id: str | None
    fact_url: str | None
    state_change_id: str | None
    raw: Any


def _bad_part(index: int, raw: Any) -> PartRow:
    empty = EnumCell(EMPTY, "")
    return PartRow(
        anchor=f"part-bad-{index}",
        ordinal=EMPTY,
        kind=empty,
        proposition=False,
        subject=None,
        predicate=None,
        what=EMPTY,
        record_id=None,
        record_suffixes=(),
        status=empty,
        story_time=EMPTY,
        timing_uncertain=False,
        fact_id=None,
        fact_url=None,
        state_change_id=None,
        raw=raw,
    )


def _part_row(
    part: Mapping[str, Any],
    ordinal: int,
    names: Mapping[str, str],
    predicate_names: Mapping[str, str],
    story_id: str,
) -> PartRow:
    kind = _text(part.get("kind"))
    subject_id = _text(part.get("subject_id"))
    predicate_id = _text(part.get("predicate_id"))
    what = ""
    record_id = None
    suffixes: tuple[str, ...] = ()
    if kind == RECORD:
        table = _text(part.get("record_table"))
        what = humanised(table) if table else EMPTY
        record_id = _text(part.get("record_id"))
        suffixes = tuple(
            humanised(text)
            for text in (_text(part.get("action")), _text(part.get("strategy")))
            if text is not None
        )
    elif kind == LITE_NOTE:
        what = NOTE_TEXT
    elif kind != PROPOSITION:
        what = EMPTY
    fact_id = _text(part.get("written_fact_id"))
    return PartRow(
        anchor=f"part-{ordinal}",
        ordinal=str(ordinal),
        kind=enum_cell(kind, PART_KIND_TEXT),
        proposition=kind == PROPOSITION,
        subject=entity_ref(subject_id, names) if subject_id else None,
        predicate=(
            PredicateRef(predicate_id, predicate_names.get(predicate_id), None)
            if predicate_id
            else None
        ),
        what=what,
        record_id=record_id,
        record_suffixes=suffixes,
        status=status_cell(part.get("status")),
        story_time=story_time_text(part.get("valid_from_minutes"), part.get("valid_to_minutes")),
        timing_uncertain=part.get("timing_uncertain") is True,
        fact_id=fact_id,
        fact_url=lineage_url(story_id, fact_id, subject_id, predicate_id) if fact_id else None,
        state_change_id=_text(part.get("written_state_change_id")),
        raw=part,
    )


def part_rows(
    parts: Iterable[Any],
    names: Mapping[str, str],
    predicate_names: Mapping[str, str],
    story_id: str,
) -> list[PartRow]:
    """One :class:`PartRow` per part, in the Engine's order (it sends them by ordinal; the
    Console doesn't re-sort). ``names`` are the cast labels (``entity_ref``), and
    ``predicate_names`` convention 2's registry names ({} when the read failed or was skipped:
    short ids)."""
    rows = []
    for index, part in enumerate(parts):
        ordinal = _int(part.get("ordinal")) if isinstance(part, Mapping) else None
        if not isinstance(part, Mapping) or ordinal is None:
            rows.append(_bad_part(index, part))
            continue
        rows.append(_part_row(part, ordinal, names, predicate_names, story_id))
    return rows


def needs_predicate_names(parts: Iterable[Any]) -> bool:
    """Convention 2's registry read happens only for a proposition part naming a predicate."""
    return any(
        isinstance(part, Mapping)
        and part.get("kind") == PROPOSITION
        and _text(part.get("predicate_id")) is not None
        for part in parts
    )


# --- the detail: edges ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetCell:
    """An edge's target: its humanised ``kind`` (raw in ``kind_raw``) and its ``id`` (shown
    short); "—" for either when missing."""

    kind_text: str
    kind_raw: str
    id: str | None


def _target(value: object) -> TargetCell:
    if not isinstance(value, Mapping):
        return TargetCell(EMPTY, "", None)
    kind = _text(value.get("kind"))
    return TargetCell(humanised(kind) if kind else EMPTY, kind or "", _text(value.get("id")))


@dataclass(frozen=True)
class EdgeRow:
    """One "Relies on" row: the edge type (humanised, raw in ``edge_raw``), the part ("#<n>",
    else the part id shown short; ``part_id`` the full id for ``title``) and the target. A
    malformed entry (``bad``) reads "—" in every cell, the raw entry under More."""

    anchor: str
    edge_text: str
    edge_raw: str
    part_text: str
    part_id: str | None
    target: TargetCell
    bad: bool
    raw: Any


def _ordinals(parts: Iterable[Any]) -> dict[str, int]:
    found: dict[str, int] = {}
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        part_id = _text(part.get("id"))
        ordinal = _int(part.get("ordinal"))
        if part_id is not None and ordinal is not None:
            found.setdefault(part_id, ordinal)
    return found


def _edge_type(entry: object) -> str | None:
    return _text(entry.get("edge_type")) if isinstance(entry, Mapping) else None


def edge_rows(relies_on: Iterable[Any], parts: Iterable[Any]) -> list[EdgeRow]:
    """``relies_on`` as :class:`EdgeRow`, in the Engine's order; ``parts`` map a ``part_id`` to
    its ordinal."""
    ordinals = _ordinals(parts)
    rows = []
    for index, entry in enumerate(relies_on):
        edge = _edge_type(entry)
        if not isinstance(entry, Mapping) or edge is None:
            rows.append(
                EdgeRow(f"relies-bad-{index}", EMPTY, "", EMPTY, None, _target(None), True, entry)
            )
            continue
        part_id = _text(entry.get("part_id"))
        if part_id is None:
            part_text = EMPTY
        elif part_id in ordinals:
            part_text = f"#{ordinals[part_id]}"
        else:
            part_text = short_id(part_id)
        rows.append(
            EdgeRow(
                anchor=f"relies-{index}",
                edge_text=humanised(edge),
                edge_raw=edge,
                part_text=part_text,
                part_id=part_id,
                target=_target(entry.get("target")),
                bad=False,
                raw=entry,
            )
        )
    return rows


@dataclass(frozen=True)
class DependentRow:
    """One "Depends on it" row: the edge type, the record (humanised ``table`` and its id shown
    short; an ``amendment_parts`` record reads :data:`LATER_PART_TEXT` with the full id in
    ``title`` only) and the target. A malformed entry (``bad``) reads "—" in every cell."""

    anchor: str
    edge_text: str
    edge_raw: str
    record_text: str
    record_table: str
    record_id: str | None
    later_part: bool
    target: TargetCell
    bad: bool
    raw: Any


def dependent_rows(dependents: Iterable[Any]) -> list[DependentRow]:
    """``dependents`` as :class:`DependentRow`, in the Engine's order."""
    rows = []
    for index, entry in enumerate(dependents):
        edge = _edge_type(entry)
        if not isinstance(entry, Mapping) or edge is None:
            rows.append(
                DependentRow(
                    f"dependent-bad-{index}",
                    EMPTY,
                    "",
                    EMPTY,
                    "",
                    None,
                    False,
                    _target(None),
                    True,
                    entry,
                )
            )
            continue
        record = entry.get("record")
        table = _text(record.get("table")) if isinstance(record, Mapping) else None
        record_id = _text(record.get("id")) if isinstance(record, Mapping) else None
        later = table == AMENDMENT_PARTS
        record_text = humanised(table) if table else EMPTY
        if later:
            record_text = LATER_PART_TEXT
        rows.append(
            DependentRow(
                anchor=f"dependent-{index}",
                edge_text=humanised(edge),
                edge_raw=edge,
                record_text=record_text,
                record_table=table or "",
                record_id=record_id,
                later_part=later,
                target=_target(entry.get("target")),
                bad=False,
                raw=entry,
            )
        )
    return rows


def raw_list(value: object) -> Sequence[Any]:
    """A raw member that should be a list, as one (anything else is no entries)."""
    return value if isinstance(value, list) else []

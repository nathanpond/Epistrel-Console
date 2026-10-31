"""How a multi-character turn was composed (#203): the turn inspector's "Composition" panel.

:func:`composition` reads the six composition fields of a respond answer (the live answer as
``dataclasses.asdict``, a polled operation's ``result.body`` or a logged ``row.response``: one
raw mapping, one code path) leniently (C2, convention 12):

- a field that is absent, JSON ``null`` or empty counts as absent;
- a field of the wrong shape (a non-dict ``roles``, a non-list array) is kept whole as raw;
- an entry missing a required key, or with a wrong-typed one, is dropped and kept raw (a
  ``bool`` is never an int; a non-string ``role``/``source``/``rule``/``reason`` drops it);
- a wrong-typed optional key (``needed``, ``fixed``) counts as absent.

The kept-raw parts go in :attr:`Composition.more`, one object keyed by field name in the six
fields' order (the whole bad field, or its dropped entries), shown under the panel's "More".

Every enum value is a :class:`Worded` (its wording, the raw value as ``title``); an unknown one
reads ``humanised`` (C3). A character id is named by the page's labels (C4); one outside them
reads its short id with the full id as ``title`` (convention 2). Entries are shown as given,
duplicates included (C15).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from console.inspect import short_id
from console.participation import role_label
from console.scene import entity_url
from console.wording import humanised

ROLE_SOURCES: dict[str, str] = {
    "default": "stored role",
    "fixed": "fixed role",
    "request": "this request",
    "escalated": "escalated (its reaction needed a render)",
    "reduced": "reduced (budget planning)",
}
DEFERRED_REASONS: dict[str, str] = {"budget": "the turn's budget ran out"}
FIXED_PRIMARY = "; fixed primary"
ESCALATION_NEEDED: dict[str, str] = {
    "speech": "speech",
    "decision": "a decision",
    "effect": "an effect",
}
NEEDED_UNKNOWN = "something"  # ``needed`` absent or null
ESCALATION_REASONS: dict[str, str] = {
    "fixed_role": "its role is fixed",
    "budget": "the turn's budget ran out",
}
ADJUDICATION_RULES: dict[str, str] = {
    "request_order": "request order (earlier-named primary)",
    "director": "director",
    "state": "state",
}
RECONCILE_RULES: dict[str, str] = {
    "author": "author (narrator or the player's input)",
    "earlier_beat": "earlier beat (sequential)",
    "request_order": "request order (simultaneous)",
    "dedupe": "duplicate dropped",
}
ENTITY_VIEWPOINT = "omniscient"
BEAT_TEXT = "beat {}"

FIELDS = (
    "roles",
    "deferred_characters",
    "reduced_characters",
    "escalation_deferred",
    "adjudications",
    "reconcile",
)


@dataclass(frozen=True)
class Worded:
    """Shown text and its ``title`` (``""``: plain text, no ``<span>``)."""

    text: str
    title: str = ""


@dataclass(frozen=True)
class RoleRow:
    name: Worded
    role: Worded
    source: Worded


@dataclass(frozen=True)
class DeferredRow:
    """ "<name> — <reason>", then "; fixed primary" when ``fixed``."""

    name: Worded
    reason: Worded
    fixed: bool


@dataclass(frozen=True)
class EscalationRow:
    """ "<name>'s reaction needed <needed> but stayed reactive: <reason> — …"."""

    name: Worded
    needed: Worded
    reason: Worded


@dataclass(frozen=True)
class AdjudicationRow:
    """``object`` is an entity id, shown short (``object_id`` its ``title``), linked to its
    entity page at the turn when ``object_url``."""

    object_text: str
    object_id: str
    object_url: str
    winner: Worded
    loser: Worded
    rule: Worded


@dataclass(frozen=True)
class BeatRef:
    """ "beat N", linked to ``#beat-N`` when the Renders section rendered that anchor."""

    text: str
    url: str = ""


@dataclass(frozen=True)
class ReconcileRow:
    proposition: str
    winner: BeatRef
    loser: BeatRef
    rule: Worded


@dataclass(frozen=True)
class Composition:
    """The panel: each subsection a tuple (``()``: not shown), then the kept-raw parts."""

    roles: tuple[RoleRow, ...] = ()
    deferred: tuple[DeferredRow, ...] = ()
    reduced: tuple[Worded, ...] = ()
    escalations: tuple[EscalationRow, ...] = ()
    adjudications: tuple[AdjudicationRow, ...] = ()
    reconcile: tuple[ReconcileRow, ...] = ()
    more: dict[str, Any] = field(default_factory=dict)


def _absent(value: object) -> bool:
    return value is None or (isinstance(value, list | dict) and not value)


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _worded(value: str, table: Mapping[str, str]) -> Worded:
    return Worded(table.get(value) or humanised(value), value)


def _name(character_id: str, labels: Mapping[str, str]) -> Worded:
    label = labels.get(character_id)
    return Worded(label) if label else Worded(short_id(character_id), character_id)


def _strings(entry: Mapping[str, Any], *keys: str) -> bool:
    return all(isinstance(entry.get(key), str) for key in keys)


def composition(
    response: Mapping[str, Any],
    labels: Mapping[str, str],
    story_id: str,
    *,
    structured: bool,
    turn: int | None,
    anchors: frozenset[int] = frozenset(),
) -> Composition | None:
    """The Composition panel of a turn answer, or ``None`` when it has none of the six fields
    (or isn't a turn answer: no ``turn``). ``structured`` and ``turn`` decide the adjudication
    object's entity link; ``anchors`` are the beat numbers #201's Renders section anchored."""
    if response.get("turn") is None:
        return None
    present = {name: response.get(name) for name in FIELDS if not _absent(response.get(name))}
    if not present:
        return None
    more: dict[str, Any] = {}

    def entries[T](name: str, row: Callable[[Any], T | None]) -> tuple[T, ...]:
        raw = present.get(name)
        if raw is None:
            return ()
        if not isinstance(raw, list):
            more[name] = raw
            return ()
        rows: list[T] = []
        dropped: list[Any] = []
        for entry in raw:
            found = row(entry)
            if found is None:
                dropped.append(entry)
            else:
                rows.append(found)
        if dropped:
            more[name] = dropped
        return tuple(rows)

    def deferred(entry: Any) -> DeferredRow | None:
        if not isinstance(entry, dict) or not _strings(entry, "character_id", "reason"):
            return None
        return DeferredRow(
            _name(entry["character_id"], labels),
            _worded(entry["reason"], DEFERRED_REASONS),
            entry.get("fixed") is True,
        )

    def reduced(entry: Any) -> Worded | None:
        return _name(entry, labels) if isinstance(entry, str) else None

    def escalation(entry: Any) -> EscalationRow | None:
        if not isinstance(entry, dict) or not _strings(entry, "character_id", "reason"):
            return None
        needed = entry.get("needed")
        return EscalationRow(
            _name(entry["character_id"], labels),
            (
                _worded(needed, ESCALATION_NEEDED)
                if isinstance(needed, str)
                else Worded(NEEDED_UNKNOWN)
            ),
            _worded(entry["reason"], ESCALATION_REASONS),
        )

    def adjudication(entry: Any) -> AdjudicationRow | None:
        if not isinstance(entry, dict) or not _strings(entry, "object", "winner", "loser", "rule"):
            return None
        target = entry["object"]
        url = (
            entity_url(story_id, target, turn, ENTITY_VIEWPOINT)
            if structured and turn is not None
            else None
        )
        return AdjudicationRow(
            short_id(target),
            target,
            url or "",
            _name(entry["winner"], labels),
            _name(entry["loser"], labels),
            _worded(entry["rule"], ADJUDICATION_RULES),
        )

    def beat(number: int) -> BeatRef:
        return BeatRef(BEAT_TEXT.format(number), f"#beat-{number}" if number in anchors else "")

    def reconcile(entry: Any) -> ReconcileRow | None:
        if (
            not isinstance(entry, dict)
            or not _strings(entry, "proposition", "rule")
            or not _int(entry.get("winner_beat"))
            or not _int(entry.get("loser_beat"))
        ):
            return None
        return ReconcileRow(
            entry["proposition"],
            beat(entry["winner_beat"]),
            beat(entry["loser_beat"]),
            _worded(entry["rule"], RECONCILE_RULES),
        )

    roles = _roles(present.get("roles"), labels, more)
    found = Composition(
        roles=roles,
        deferred=entries("deferred_characters", deferred),
        reduced=entries("reduced_characters", reduced),
        escalations=entries("escalation_deferred", escalation),
        adjudications=entries("adjudications", adjudication),
        reconcile=entries("reconcile", reconcile),
    )
    return replace(found, more={name: more[name] for name in FIELDS if name in more})


def _roles(raw: object, labels: Mapping[str, str], more: dict[str, Any]) -> tuple[RoleRow, ...]:
    """The Roles rows in cast order (``labels``' order), then ids outside the cast in the
    response's key order. A map's dropped entries are kept as a map (id → raw value)."""
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        more["roles"] = raw
        return ()
    kept: dict[str, RoleRow] = {}
    dropped: dict[str, Any] = {}
    for character_id, value in raw.items():
        if not isinstance(value, dict) or not _strings(value, "role", "source"):
            dropped[character_id] = value
            continue
        role = value["role"]
        kept[character_id] = RoleRow(
            _name(character_id, labels),
            Worded(role_label(role), role),
            _worded(value["source"], ROLE_SOURCES),
        )
    if dropped:
        more["roles"] = dropped
    order: Sequence[str] = [c for c in labels if c in kept] + [c for c in kept if c not in labels]
    return tuple(kept[c] for c in order)

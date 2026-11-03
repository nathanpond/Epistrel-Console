"""A structured turn's eval hooks and read set (#139; #147's per-beat trace builds on it).

**The read.** :func:`read_turn_trace` makes one ``GET /operations/{id}?viewpoint=omniscient``:
the Engine returns ``eval_hooks`` and the read set's ``keys`` to an omniscient read only.

Since Engine #534 an omniscient read gets ``eval_hooks`` and ``read_set.keys`` whatever the
turn's output viewpoint (with ``result`` null); any error raises.

**The view.** :func:`trace_view` derives what ``templates/console/_turn_trace.html`` shows:
latency in the Engine's ``STAGES`` order (unknown stages after, as received), model calls in the
Engine's order, the verdict counts and rows, the repair rungs, the read-set keys and the "More"
parts (#135 conventions 12, 13).
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse

from console.engine.client import EngineClient
from console.engine.models import (
    EvalHooks,
    OperationRecord,
    QueryKey,
    ReadSet,
    RepairRung,
    Verdict,
)
from console.ids import is_ulid
from console.inspect import Tab
from console.retrieval import humanised

OMNISCIENT = "omniscient"
TURN_KIND = "turn"
COMMITTED = "committed"

# The Engine's ``orchestrator/structured/eval_hooks.STAGES``, in its order.
STAGES = (
    "plan",
    "render",
    "normalize",
    "detection",
    "verify",
    "entailment",
    "repair",
    "composition",
    "commit",
)
VERDICT_KINDS = frozenset({"supported", "contradicted", "novel", "uncertain"})  # convention 9
# The Engine's ``orchestrator/repair/ladder`` rungs (4: the safe fallback, or a dropped beat).
RUNG_NAMES = {
    1: "narrowing",
    2: "substitution",
    3: "regeneration",
    4: "safe fallback (or dropped)",
}
INLINE_JSON_MAX = 80
HASH_SHOWN = 12
CANON = "canon"
HOLDER_PREFIXES = ("utterance", "belief")  # ``<prefix>:<character id>``


# --- the read -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnTrace:
    """What one turn's trace read found: the record read, its parsed hooks and read set."""

    operation: OperationRecord
    eval_hooks: EvalHooks | None
    read_set: ReadSet | None


def read_turn_trace(client: EngineClient, engine_operation_id: str) -> TurnTrace:
    """One omniscient read (Engine #534: it gets the hooks whatever the turn's viewpoint). Any
    error raises :class:`console.engine.client.EngineClientError`."""
    record = client.get_operation(engine_operation_id, viewpoint=OMNISCIENT)
    return TurnTrace(record, EvalHooks.parse(record.eval_hooks), ReadSet.parse(record.read_set))


# --- what the partial shows ---------------------------------------------------------------------


@dataclass(frozen=True)
class CountRow:
    """One latency or model-call row: the raw ``name`` (in ``title``), its label, the value."""

    name: str
    label: str
    value: int


@dataclass(frozen=True)
class CountTable:
    rows: tuple[CountRow, ...]
    total: int


@dataclass(frozen=True)
class VerdictCounts:
    """Claims, those decided without a model (``escalated`` false) and those escalated
    (``escalated`` true); an entry that doesn't say counts in neither."""

    claims: int
    deterministic: int
    escalated: int


@dataclass(frozen=True)
class Coded:
    """A cell's shown text with the raw code for ``title``."""

    text: str
    raw: str


@dataclass(frozen=True)
class VerdictRow:
    verdict: Verdict
    beat: str | None  # "beat N" for a ``d<N>.<n>`` claim id
    kind_known: bool  # a convention-9 verdict word (else the raw kind in ``<code>``)
    reason: Coded | None
    disposition: Coded | None
    permission: Coded | None
    cause: Coded | None


@dataclass(frozen=True)
class RungRow:
    rung: RepairRung
    label: str  # "rung 1 · narrowing", "rung 7"


@dataclass(frozen=True)
class InlineJson:
    """A value as compact JSON (sorted keys), cut at 80 characters with "…"; ``full`` for
    ``title``."""

    shown: str
    full: str


@dataclass(frozen=True)
class Cell:
    """A read-set key's subject, object or holder: ``kind`` is ``any`` (``*``), ``ids`` (each a
    convention-2 id), ``code`` (a literal shown verbatim), ``canon``, ``holder`` (``prefix · id``)
    or ``json`` (an unexpected shape)."""

    kind: str
    ids: tuple[str, ...] = ()
    text: str = ""
    json: InlineJson | None = None


@dataclass(frozen=True)
class KeyRow:
    key: QueryKey
    subject: Cell
    predicate_url: str | None
    object: Cell
    holder: Cell
    window: str | None  # "[3, …) min"; ``None`` with ``window_json`` for another shape
    window_json: InlineJson | None
    hash_short: str


@dataclass(frozen=True)
class MorePart:
    """One labelled part of "More": its label and the raw value (pretty JSON)."""

    label: str
    value: Any


@dataclass(frozen=True)
class HooksView:
    latency: CountTable | None
    calls: CountTable | None
    counts: VerdictCounts
    verdicts: tuple[VerdictRow, ...]
    rungs: tuple[RungRow, ...]
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReadSetView:
    keys_count: int | None
    retrieved_count: int | None
    keys: tuple[KeyRow, ...] | None  # ``None``: not shown to this read


@dataclass(frozen=True)
class TraceView:
    """Everything the partial derives from a :class:`TurnTrace` (#147 reuses the rows)."""

    hooks: HooksView | None
    read_set: ReadSetView | None
    more: tuple[MorePart, ...]


def coded(value: str | None) -> Coded | None:
    """Convention 5: humanised (sentence case) with the raw code kept; ``None`` stays."""
    return Coded(humanised(value), value) if value else None


def permission_cell(value: str | None) -> Coded | None:
    """``permitted:<basis>`` → "permitted · <basis>", ``denied:<reason>`` → "denied ·
    <reason>" (the word ``denied`` verbatim, convention 9); another value humanised."""
    if not value:
        return None
    head, sep, rest = value.partition(":")
    if sep and head in ("permitted", "denied"):
        return Coded(f"{head} · {rest.replace('_', ' ')}" if rest else head, value)
    return coded(value)


def declared_beat(claim_id: object) -> int | None:
    """The beat a declared claim id ``d<beat>.<n>`` (``structured/pipeline.py``) names: ``d3.1``
    → 3. Any other claim id (a detected claim's ``<operation>:<n>``, ``d2``, ``dX.y``, a
    non-string) → ``None``."""
    if not isinstance(claim_id, str) or not claim_id.startswith("d"):
        return None
    beat, dot, rest = claim_id[1:].partition(".")
    if dot and beat.isascii() and beat.isdigit() and rest.isascii() and rest.isdigit():
        return int(beat)
    return None


def beat_of(claim_id: str | None) -> str | None:
    """``d3.1`` → "beat 3"; any other claim id (an Engine id, ``01J…:4``) → ``None``."""
    beat = declared_beat(claim_id)
    return f"beat {beat}" if beat is not None else None


def rung_label(rung: int) -> str:
    name = RUNG_NAMES.get(rung)
    return f"rung {rung} · {name}" if name else f"rung {rung}"


def inline_json(value: Any) -> InlineJson:
    full = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    cut = full if len(full) <= INLINE_JSON_MAX else full[: INLINE_JSON_MAX - 1] + "…"
    return InlineJson(cut, full)


def _ordered(pairs: Iterable[tuple[str, int]], order: tuple[str, ...]) -> list[tuple[str, int]]:
    """``pairs`` with the names in ``order`` first, in that order; the rest as received."""
    given = list(pairs)
    known = sorted((p for p in given if p[0] in order), key=lambda p: order.index(p[0]))
    return known + [p for p in given if p[0] not in order]


def count_table(
    pairs: tuple[tuple[str, int], ...] | None, order: tuple[str, ...] = ()
) -> CountTable | None:
    if pairs is None:
        return None
    rows = tuple(CountRow(name, humanised(name), value) for name, value in _ordered(pairs, order))
    return CountTable(rows, sum(r.value for r in rows))


def verdict_counts(verdicts: tuple[Verdict, ...]) -> VerdictCounts:
    return VerdictCounts(
        len(verdicts),
        sum(1 for v in verdicts if v.escalated is False),
        sum(1 for v in verdicts if v.escalated is True),
    )


def verdict_row(verdict: Verdict) -> VerdictRow:
    return VerdictRow(
        verdict,
        beat_of(verdict.claim_id),
        verdict.kind is None or verdict.kind in VERDICT_KINDS,
        coded(verdict.reason),
        coded(verdict.disposition),
        permission_cell(verdict.permission),
        coded(verdict.cause),
    )


def hooks_view(hooks: EvalHooks) -> HooksView:
    return HooksView(
        count_table(hooks.latency_ms_by_stage, STAGES),
        count_table(hooks.model_calls_by_purpose),
        verdict_counts(hooks.verdicts),
        tuple(verdict_row(v) for v in hooks.verdicts),
        tuple(RungRow(r, rung_label(r.rung)) for r in hooks.repair_rungs),
        hooks.raw_render_event_ids,
    )


def _subject(value: Any) -> Cell:
    if value == "*":
        return Cell("any")
    if isinstance(value, list) and all(isinstance(i, str) for i in value):
        return Cell("ids", ids=tuple(value))
    return Cell("json", json=inline_json(value))


def _object(value: Any) -> Cell:
    if value == "*":
        return Cell("any")
    if isinstance(value, str):
        # An entity id, or a literal (shown verbatim: it isn't an id to shorten).
        return Cell("ids", ids=(value,)) if is_ulid(value) else Cell("code", text=value)
    return Cell("json", json=inline_json(value))


def _holder(value: str | None) -> Cell:
    """``canon`` → "canon"; ``utterance:<id>`` (``belief:<id>``) → "utterance · " + the id."""
    if value is None:
        return Cell("none")
    if value == CANON:
        return Cell("canon")
    prefix, sep, rest = value.partition(":")
    if sep and prefix in HOLDER_PREFIXES and rest:
        return Cell("holder", ids=(rest,), text=prefix)
    return Cell("code", text=value)


def window_text(value: Any) -> str | None:
    """Convention 4: ``[3, None]`` → "[3, …) min"; ``None`` for anything but a 2-list of
    integers or nulls."""
    if not isinstance(value, list) or len(value) != 2:
        return None
    if not all(v is None or (isinstance(v, int) and not isinstance(v, bool)) for v in value):
        return None
    low, high = ("…" if v is None else str(v) for v in value)
    return f"[{low}, {high}) min"


def predicate_url(story_id: str, predicate_id: str) -> str | None:
    """Convention 3: the predicate's row on the Predicates tab (#136), ``#p-<id>``; ``None``
    while that tab isn't registered (the id then renders as plain ``<code>``)."""
    try:
        return f"{reverse(Tab.PREDICATES.url_name, args=[story_id])}#p-{predicate_id}"
    except NoReverseMatch:
        return None


def key_row(story_id: str, key: QueryKey) -> KeyRow:
    window = window_text(key.window)
    digest = key.result_hash or ""
    return KeyRow(
        key,
        _subject(key.subject),
        predicate_url(story_id, key.predicate) if key.predicate else None,
        _object(key.object),
        _holder(key.holder),
        window,
        inline_json(key.window) if window is None and key.window is not None else None,
        digest if len(digest) <= HASH_SHOWN else f"{digest[:HASH_SHOWN]}…",
    )


def read_set_view(story_id: str, read_set: ReadSet) -> ReadSetView:
    keys = read_set.keys
    rows = tuple(key_row(story_id, k) for k in keys) if keys is not None else None
    return ReadSetView(read_set.keys_count, read_set.retrieved_count, rows)


def _labelled(
    name: str,
    parsed: Iterable[Verdict | RepairRung | QueryKey],
    skipped: Iterable[tuple[int, Any]],
) -> list[MorePart]:
    """Per-entry extras and skipped entries, labelled "<name> <1-based index>", in order."""
    parts = [(e.index, e.extras) for e in parsed if e.extras]
    parts += list(skipped)
    return [
        MorePart(f"{name} {index}", value) for index, value in sorted(parts, key=lambda p: p[0])
    ]


def more_parts(hooks: EvalHooks | None, read_set: ReadSet | None) -> tuple[MorePart, ...]:
    """Convention 13's "More": the top-level ``eval_hooks`` and ``read_set`` extras, then the
    per-entry extras and skipped entries ("verdict 3", "rung 1", "key 5")."""
    parts: list[MorePart] = []
    if hooks is not None and hooks.extras:
        parts.append(MorePart("eval_hooks", hooks.extras))
    if read_set is not None and read_set.extras:
        parts.append(MorePart("read_set", read_set.extras))
    if hooks is not None:
        parts += _labelled("verdict", hooks.verdicts, hooks.skipped_verdicts)
        parts += _labelled("rung", hooks.repair_rungs, hooks.skipped_rungs)
    if read_set is not None:
        parts += _labelled("key", read_set.keys or (), read_set.skipped_keys)
    return tuple(parts)


def trace_view(story_id: str, trace: TurnTrace) -> TraceView:
    hooks = trace.eval_hooks
    read_set = trace.read_set
    return TraceView(
        hooks_view(hooks) if hooks is not None else None,
        read_set_view(story_id, read_set) if read_set is not None else None,
        more_parts(hooks, read_set),
    )

"""Reading a turn's report and guardrail verdicts out of a stored or live Engine answer, for
the operation detail page (#44) and the play view's inspector (#48; #53's regenerate and swap
reuse it).

#330 (FR-PROF-8): a turn answer's ``coverage_gaps`` as the turn report's "Coverage gaps" lines.
:func:`stored_gaps` reads the raw answer entry by entry (D15's lenient rule, E23), and
:func:`unread_gap_count` counts what it skipped; :func:`coverage_gap_lines` words the gaps and
links each to the Backfill page's gap fill (``console.upgrades.gap_fill_url``, D19: ``""`` while
unregistered). ``GapLine`` and these three helpers are public: #332's Retrieval explainer reuses
them unchanged (E1's import rule: import them, never copy them).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.engine.models import CoverageGap, TurnReport
from console.operation_log import input_gate_spend, turn_report_json
from console.raw import plural
from console.upgrades import gap_fill_url

EMPTY = "—"


@dataclass(frozen=True)
class ViolationRow:
    """One guardrail violation as the ``console/_violations.html`` table shows it."""

    policy_id: str
    name: str
    message: str
    locators: tuple[str, ...]


def violation_rows(violations: list[dict[str, Any]]) -> list[ViolationRow]:
    """Rows for raw violations (``policy_id``, ``name``, ``message`` and a ``locator`` or
    ``locators``), missing values shown as "—"."""
    rows = []
    for v in violations:
        locators: tuple[str, ...] = ()
        if isinstance(v.get("locator"), str):
            locators = (v["locator"],)
        elif isinstance(v.get("locators"), list):
            locators = tuple(str(item) for item in v["locators"])
        rows.append(
            ViolationRow(
                str(v.get("policy_id", EMPTY)),
                str(v.get("name", EMPTY)),
                str(v.get("message", EMPTY)),
                locators,
            )
        )
    return rows


def stored_turn_report(value: object) -> TurnReport | None:
    """The turn report in a stored response (a turn, or a 422/503's ``error.details``)."""
    report = turn_report_json(value)
    return TurnReport.from_json(report) if report is not None else None


def spend_text(calls: int, elapsed_ms: int) -> str:
    """An input gate's spend: "1 model call, 3 ms"."""
    return f"{calls} model call{'' if calls == 1 else 's'}, {elapsed_ms} ms"


def input_gate_text(value: object) -> str:
    """A refused input's gate spend (``spend_text``), or "" when the answer carries none."""
    spend = input_gate_spend(value)
    return spend_text(*spend) if spend is not None else ""


# --- coverage gaps (#330, FR-PROF-8) ------------------------------------------------------------

GAP_TEXT = "{capability} has no data for {turns} (a forward-only upgrade or a backfill not yet run)"
GAP_TURN = "turn {n}"
GAP_TURNS = "turns {a}\u2013{b}"  # an en dash
GAP_UNREAD = "{entries} couldn't be read"
GAPS_FIELD = "coverage_gaps"


@dataclass(frozen=True)
class GapLine:
    """One "Coverage gaps" line: ``text`` (safe HTML, the capability in ``<code>``) and ``url``,
    the Backfill page's gap fill ("" while ``stories:backfill`` is unregistered or the story id
    is blank, D19: then the line has no "Fill the gap" link)."""

    text: SafeString
    url: str


def _turn(value: object) -> int | None:
    """A gap bound as the Engine sent it: an int ≥ 0 (a bool isn't one), else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _gap(entry: object) -> CoverageGap | None:
    """A raw gap entry, or ``None`` when malformed: not an object, a ``capability`` that isn't a
    non-blank string, a turn that isn't an int ≥ 0 (a bool isn't one), or ``to_turn`` before
    ``from_turn``."""
    if not isinstance(entry, dict):
        return None
    capability = entry.get("capability")
    start, end = _turn(entry.get("from_turn")), _turn(entry.get("to_turn"))
    if not isinstance(capability, str) or not capability.strip():
        return None
    if start is None or end is None or end < start:
        return None
    return CoverageGap(capability=capability, from_turn=start, to_turn=end)


def _raw_gaps(value: object) -> object:
    return value.get(GAPS_FIELD) if isinstance(value, dict) else None


def stored_gaps(value: object) -> list[CoverageGap]:
    """The gaps of a turn answer (the stored #44 response, or the live answer as a mapping):
    its top-level ``coverage_gaps``, entry by entry in the Engine's order, a malformed entry
    skipped (:func:`unread_gap_count` counts it). Absent, or not a list: ``[]``."""
    raw = _raw_gaps(value)
    if not isinstance(raw, list):
        return []
    return [gap for gap in map(_gap, raw) if gap is not None]


def unread_gap_count(value: object) -> int:
    """How many of the answer's ``coverage_gaps`` entries :func:`stored_gaps` skipped; a value
    that is present but not a list counts as 1."""
    if not isinstance(value, dict) or GAPS_FIELD not in value:
        return 0
    raw = value[GAPS_FIELD]
    if not isinstance(raw, list):
        return 1
    return sum(1 for entry in raw if _gap(entry) is None)


def unread_gap_text(count: int) -> str:
    """``GAP_UNREAD``: "1 gap entry couldn't be read", "2 gap entries couldn't be read"."""
    return GAP_UNREAD.format(entries=plural(count, "gap entry", "gap entries"))


def _turns_text(gap: CoverageGap) -> str:
    if gap.from_turn == gap.to_turn:
        return GAP_TURN.format(n=f"{gap.from_turn:,}")
    return GAP_TURNS.format(a=f"{gap.from_turn:,}", b=f"{gap.to_turn:,}")


def coverage_gap_lines(gaps: Iterable[CoverageGap], story_id: str) -> list[GapLine]:
    """One :class:`GapLine` per gap, in the Engine's order, a repeat of the same
    ``(capability, from_turn, to_turn)`` shown once, each linked to ``story_id``'s gap fill."""
    url = gap_fill_url(story_id) or ""
    seen: set[tuple[str, int, int]] = set()
    lines = []
    for gap in gaps:
        key = (gap.capability, gap.from_turn, gap.to_turn)
        if key in seen:
            continue
        seen.add(key)
        text = format_html(
            GAP_TEXT,
            capability=format_html("<code>{}</code>", gap.capability),
            turns=_turns_text(gap),
        )
        lines.append(GapLine(text, url))
    return lines

"""Reading a turn's report and guardrail verdicts out of a stored or live Engine answer, for
the operation detail page (#44) and the play view's inspector (#48; #53's regenerate and swap
reuse it).
"""

from dataclasses import dataclass
from typing import Any

from console.engine.models import TurnReport
from console.operation_log import turn_report_json

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

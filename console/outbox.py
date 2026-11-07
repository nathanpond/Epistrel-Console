"""The Effects tab's Outbox rows (#325; E2, E12, E17, E22, E23, C1, C3, C11, D7, D10, D11, D15,
D19).

``GET /stories/{id}/outbox`` (Engine #679, FR-EXT-10) lists **every** outbox row a story
committed, whatever its status, oldest first, always read as omniscient (E2). This module words
one row (:func:`outbox_row`) for ``templates/console/inspect/_outbox_row.html`` and holds the
section's own wording: :data:`SCOPE_LINE`, the empty, count and unreadable lines, the status
choices (:data:`OUTBOX_STATUSES`) and :data:`IMPORTED_UNSETTLED_TITLE`. #249's ``REDACTED``
(and, in the view, ``NOT_FOUND``) come from :mod:`console.reconciliation`, never copied, as do its
:func:`~console.reconciliation.effect_text` and
:func:`~console.reconciliation.outcome_cell`.

**Cells.** The full outbox id (D11); the provider linked through D19's ``provider_url``; the
provider value in ``<code>`` (a non-string as compact JSON, E23); the status humanised with the
raw value in ``title`` (D10); an ``imported_unsettled`` row adds :data:`IMPORTED_UNSETTLED_TITLE`
linking its Reconciliation row (``console.effects.outbox_url``). The effect through #249's
``effect_text``. "Declared at" is C1/D7's ``knowledge_position_text`` over ``five_part`` (a
``null`` turn or variant, or a malformed map, reads "—"). Timeline is the membership humanised.
"Waits on it" is :data:`CONTINGENT_TEXT` for a contingent row. Compensation links each id to
``#outbox-<id>`` when that row is on this page, else to ``outbox_url`` (Reconciliation; plain
``<code>`` while that is unregistered). Attempts and times are one line each. More holds the
effect, the raw status and membership and the position.

**A redacted row** (the Engine's ``redacted`` only): Effect reads :data:`REDACTED`; Value, Waits
on it, Compensation and Latest outcome read "—"; the attempts line is left out; Timeline and
"created …" still show. More holds no effect.
"""

import dataclasses
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from console.effects import outbox_url, provider_url
from console.engine.models import OutboxRow, rejected_entries
from console.positions import knowledge_position_text, knowledge_position_url
from console.raw import EMPTY, compact_json, more_json, plural
from console.reconciliation import REDACTED, OutcomeCell, effect_text, outcome_cell
from console.wording import humanised
from console.world import five_part

SECTION = "inspect:outbox"
# 50 rows a page: an outbox row is a wide table row (the Engine allows ``OUTBOX_LIMIT_MAX = 200``,
# ``api/routes/reconciliation.py`` at origin/main 95e57a5).
PAGE_SIZE = 50

# The statuses the Engine documents for an outbox row (``OutboxRow.status`` is a plain string,
# E15), in its lifecycle order; ``imported_unsettled`` is an imported story's (#321, #326).
OUTBOX_STATUSES: tuple[str, ...] = (
    "intended",
    "claimed",
    "confirmed",
    "failed",
    "unknown",
    "cancelled",
    "compensated",
    "imported_unsettled",
)
IMPORTED_UNSETTLED = "imported_unsettled"
# Worded to stay true once the row's Reconciliation listing is resolved (resolving closes the
# listing, not the status).
IMPORTED_UNSETTLED_TITLE = (
    "Imported with no final outcome — never sent; any open listing is in Reconciliation"
)

SCOPE_LINE = "Every external effect this story committed, whatever its status, oldest first"
NO_ROWS = "No outbox rows — this story has committed no external effect."
NO_ROWS_MATCH = "No outbox rows match these filters"
NO_MORE_ROWS = "No more outbox rows after this one"
REJECTED_ROW_TEXT = "This outbox row couldn't be read"
CONTINGENT_TEXT = "Yes — a story change waits on its outcome"
COMPENSATES_TEXT = "Compensates"
COMPENSATED_BY_TEXT = "Compensated by"
COUNT_SUFFIX = " on this page"
FILTERED_SUFFIX = ", matching filters"


def count_text(n: int, *, filtered: bool) -> str:
    """E22: "1 outbox row on this page", "3 outbox rows on this page, matching filters"."""
    return plural(n, "outbox row") + COUNT_SUFFIX + (FILTERED_SUFFIX if filtered else "")


def unreadable_text(n: int) -> str:
    """D15's notice: "1 outbox row couldn't be read — see More" (C11)."""
    return f"{plural(n, 'outbox row')} couldn't be read — see More"


def empty_text(*, cursor: str | None, filtered: bool) -> str:
    """E22's order: a later page, then filtered, then unfiltered."""
    if cursor:
        return NO_MORE_ROWS
    return NO_ROWS_MATCH if filtered else NO_ROWS


@dataclass(frozen=True)
class CompensationLink:
    """One Compensation line: "Compensates <id>" / "Compensated by <id>"; ``url`` is
    ``#outbox-<id>`` on this page, else Reconciliation's row, else ``None`` (plain)."""

    label: str
    outbox_id: str
    url: str | None


@dataclass(frozen=True)
class OutboxRowView:
    """``_outbox_row.html``'s context for one outbox row (see the module docstring)."""

    outbox_id: str
    anchor: str
    provider: str
    provider_url: str | None
    value: str | None
    status: str
    status_text: str
    imported_text: str | None
    imported_url: str | None
    effect_text: str
    redacted: bool
    position_text: str
    position_url: str | None
    membership: str
    membership_text: str
    contingent_text: str | None
    compensation: tuple[CompensationLink, ...]
    attempts_text: str | None
    created_at: str
    claimed_at: str | None
    sent_at: str | None
    outcome: OutcomeCell | None
    effect_json: str
    position_json: str


def _value_text(value: Any) -> str | None:
    """E23: a string as is, anything else as compact JSON; ``None`` for absent or blank."""
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else compact_json(value)


def _position(row: OutboxRow, story_id: str) -> tuple[str, str | None, Any]:
    """(the C1 text or "—", its History link, the raw map for More)."""
    raw = row.position
    mapping: Any = dataclasses.asdict(raw) if dataclasses.is_dataclass(raw) else raw
    position = five_part(mapping)
    if position is None:
        return EMPTY, None, mapping
    return (
        knowledge_position_text(position),
        knowledge_position_url(story_id, position),
        mapping,
    )


def _compensation(
    row: OutboxRow, story_id: str, on_page: Collection[str]
) -> tuple[CompensationLink, ...]:
    links = []
    for label, other in (
        (COMPENSATES_TEXT, row.compensates),
        (COMPENSATED_BY_TEXT, row.compensated_by),
    ):
        if not isinstance(other, str) or not other:
            continue
        url = f"#outbox-{other}" if other in on_page else outbox_url(story_id, other)
        links.append(CompensationLink(label, other, url))
    return tuple(links)


def _attempts(attempts: Any) -> str:
    if isinstance(attempts, int) and not isinstance(attempts, bool):
        return plural(attempts, "attempt")
    return f"{attempts} attempts"


def outbox_row(row: OutboxRow, story_id: str, on_page: Collection[str]) -> OutboxRowView:
    """One outbox row's cells. ``on_page`` holds the page's outbox ids (a Compensation id links
    ``#outbox-<id>`` only to one of them, exact match)."""
    redacted = row.redacted is True
    position_text, position_url, position_raw = _position(row, story_id)
    status = row.status
    imported = status == IMPORTED_UNSETTLED
    effect = row.effect
    return OutboxRowView(
        outbox_id=row.outbox_id,
        anchor=f"outbox-{row.outbox_id}",
        provider=row.provider,
        provider_url=provider_url(row.provider),
        value=None if redacted else _value_text(row.value),
        status=status,
        status_text=humanised(status) if status else EMPTY,
        imported_text=IMPORTED_UNSETTLED_TITLE if imported else None,
        imported_url=outbox_url(story_id, row.outbox_id) if imported else None,
        effect_text=REDACTED if redacted else effect_text(effect),
        redacted=redacted,
        position_text=position_text,
        position_url=position_url,
        membership=row.membership,
        membership_text=humanised(row.membership) if row.membership else EMPTY,
        contingent_text=CONTINGENT_TEXT if row.contingent is True and not redacted else None,
        compensation=() if redacted else _compensation(row, story_id, on_page),
        attempts_text=None if redacted else _attempts(row.attempts),
        created_at=row.created_at,
        claimed_at=row.claimed_at or None,
        sent_at=row.sent_at or None,
        outcome=None if redacted else outcome_cell(row.outcome),
        effect_json="" if redacted or effect is None else more_json(effect),
        position_json=more_json(position_raw),
    )


def outbox_rows(rows: list[OutboxRow], story_id: str) -> list[OutboxRowView]:
    """The page's parsed rows, in the Engine's order."""
    on_page = {row.outbox_id for row in rows}
    return [outbox_row(row, story_id, on_page) for row in rows]


def rejected_rows(unparsed: Mapping[str, Any]) -> list[Any]:
    """D15: the raw entries the lenient page couldn't read."""
    raw = unparsed.get("rows") if unparsed else None
    return rejected_entries(raw, OutboxRow.from_json) if raw is not None else []

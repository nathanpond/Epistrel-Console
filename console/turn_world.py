"""What one turn did to the world (#245): the play inspector's **World** strip, and the shared
State-change table rows History (#254) and the Effects trail (#255) reuse.

:func:`turn_world` reads a turn answer as a raw mapping on every path (the live answer through
``console.views.play._answer_mapping``, a polled operation's ``result.body``, a logged row's
``response``), so the three can't drift (#203's pattern). It reads:

- ``clock`` (``{before, after}``, #231's ``TurnClock``), worded by D6;
- ``state_effects`` and ``settled_effects`` (``StateEffect``, D10's :func:`effect_line` with
  ``units=None``: no page reads States to word a change);
- ``reconciliation_required`` (``RequiredEffect``), one "Not undone" line each, its outbox id in
  full (D11);
- ``reconciliation.count``, D12's :func:`reconciliation_link` (the text unlinked while
  ``inspect:reconciliation`` is unregistered, D19);
- ``output_viewpoint``, D4's :func:`viewpoint_line` for a character viewpoint.

``None`` and absent are the same for every field. Each list is parsed entry by entry (C2/D15):
an entry that doesn't parse, or a list field that isn't a list, is counted in one "couldn't be
read" line per list and kept raw (D15's :func:`rejected_entries`) under the strip's More. A
``clock`` or ``reconciliation`` that doesn't parse is left out, its raw JSON in More too. The
strip makes no Engine read: it shows exactly what the answer carries.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.utils.safestring import SafeString

from console.cast import OMNISCIENT
from console.effects import (
    DISPATCHED_NOTE,
    EffectLine,
    effect_line,
    outbox_list_notice_html,
    outbox_url,
    reconciliation_link,
    reconciliation_text,
)
from console.engine.models import (
    ReconciliationCount,
    RequiredEffect,
    StateEffect,
    TurnClock,
    rejected_entries,
)
from console.wording import humanised
from console.world import clock_change_text, state_value_text, viewpoint_line

STATE_CAPTION = "State changes this turn"
SETTLED_CAPTION = "Settled since the last turn"
REQUIRED_CAPTION = "External effects that stay listed for reconciliation"

# The "couldn't be read" line per list (singular, plural), and the More label of its raw entries.
UNREAD: Mapping[str, tuple[str, str, str]] = {
    "state_effects": ("State change", "State changes", "State changes"),
    "settled_effects": ("settled State change", "settled State changes", "Settled State changes"),
    "reconciliation_required": ("external effect", "external effects", "External effects"),
}
CLOCK_LABEL = "Clock"
RECONCILIATION_LABEL = "Reconciliation"


@dataclass(frozen=True)
class Note:
    """One D10 note. The dispatched note carries the full ``outbox_id`` the template renders
    after its text (the ``outbox_id`` filter), linked to ``url`` when it resolves (D11/D19)."""

    text: str
    outbox_id: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class MoreItem:
    """One "Label: text" item in a row's More; ``title`` holds a humanised value's raw form and
    ``code`` renders the text as a full id (the ``outbox_id`` filter, unlinked)."""

    label: str
    text: str
    title: str = ""
    code: bool = False


@dataclass(frozen=True)
class StateChangeRow:
    """One row of ``templates/console/_state_changes.html``: D10's columns (``line``), the notes
    in D10's order, the dispatched outbox id and its link, and the More items. ``status_title``
    and ``contingency_title`` are the raw values (``""``: the cell has no ``title``)."""

    line: EffectLine
    notes: tuple[Note, ...]
    outbox_id: str | None
    outbox_url: str | None
    more: tuple[MoreItem, ...]
    status_title: str = ""
    contingency_title: str = ""


@dataclass(frozen=True)
class RequiredLine:
    """One "Not undone" line: "<outbox id> · <provider> · <status> · <value>"."""

    outbox_id: str
    url: str | None
    provider: str
    status: str
    status_raw: str
    value: str | None


@dataclass(frozen=True)
class TurnWorld:
    """The strip's content, in its order. ``reconcile`` is (text, url), ``url`` "" while
    ``inspect:reconciliation`` is unregistered; ``raw_more`` is (label, raw values) per field
    with something that didn't parse."""

    clock: TurnClock | None
    clock_unchanged: bool
    state_rows: tuple[StateChangeRow, ...]
    settled_rows: tuple[StateChangeRow, ...]
    unread: tuple[str, ...]
    required: tuple[RequiredLine, ...]
    reconcile: tuple[str, str] | None
    outbox_notice: SafeString | str
    viewpoint: str
    raw_more: tuple[tuple[str, tuple[Any, ...]], ...]

    @property
    def clock_text(self) -> str:
        """D6's ``clock_change_text``: the strip's clock line as plain text."""
        if self.clock is None:
            return ""
        return clock_change_text(self.clock.before, self.clock.after)


def _parse_effect(entry: Any) -> StateEffect | None:
    return entry if isinstance(entry, StateEffect) else StateEffect.from_json(entry)


def _more(effect: StateEffect, line: EffectLine) -> tuple[MoreItem, ...]:
    items = [MoreItem("Source", humanised(effect.source), effect.source)] if effect.source else []
    items.extend(MoreItem(label, text, code=True) for label, text in line.more)
    if effect.kind:
        items.append(MoreItem("Kind", humanised(effect.kind), effect.kind))
    if effect.declared is not None:
        items.append(MoreItem("Declared", state_value_text(effect.declared, None)))
    if effect.minutes is not None:
        items.append(MoreItem("Minutes", f"{effect.minutes} min"))
    shows_change = (
        effect.old_value is not None or effect.new_value is not None or effect.delta is not None
    )
    if effect.value is not None and not shows_change:
        items.append(MoreItem("Value", state_value_text(effect.value, None)))
    return tuple(items)


def _row(effect: StateEffect, story_id: str, names: Mapping[str, str]) -> StateChangeRow:
    line = effect_line(effect, names, units=None)
    url = outbox_url(story_id, line.outbox_id)
    notes = tuple(
        Note(text, line.outbox_id, url) if text == DISPATCHED_NOTE else Note(text)
        for text in line.notes
    )
    return StateChangeRow(
        line=line,
        notes=notes,
        outbox_id=line.outbox_id,
        outbox_url=url,
        more=_more(effect, line),
        status_title=effect.status,
        contingency_title=effect.contingency if line.contingency else "",
    )


def state_change_rows(
    entries: Any, story_id: str, names: Mapping[str, str]
) -> tuple[tuple[StateChangeRow, ...], tuple[Any, ...]]:
    """(rows, raw entries that failed) for a State-change list: raw mappings and parsed
    ``StateEffect``s alike, in the given order. ``None`` is no list; a non-list value is one
    unparsed entry holding the raw value (D15's :func:`rejected_entries`)."""
    if entries is None:
        return (), ()
    if not isinstance(entries, list | tuple):
        return (), tuple(rejected_entries(entries, _parse_effect))
    listed = list(entries)
    parsed = (_parse_effect(entry) for entry in listed)
    rows = tuple(_row(effect, story_id, names) for effect in parsed if effect is not None)
    return rows, tuple(rejected_entries(listed, _parse_effect))


def _required(entries: Any, story_id: str) -> tuple[tuple[RequiredLine, ...], tuple[Any, ...]]:
    if entries is None:
        return (), ()
    if not isinstance(entries, list):
        return (), tuple(rejected_entries(entries, RequiredEffect.from_json))
    lines = []
    for entry in entries:
        found = RequiredEffect.from_json(entry)
        if found is None:
            continue
        lines.append(
            RequiredLine(
                outbox_id=found.outbox_id,
                url=outbox_url(story_id, found.outbox_id),
                provider=found.provider,
                status=humanised(found.status),
                status_raw=found.status,
                value=found.value,
            )
        )
    return tuple(lines), tuple(rejected_entries(entries, RequiredEffect.from_json))


def _present(value: Any) -> bool:
    """A list field shows the strip when non-null and non-empty (``{}`` included: malformed)."""
    if value is None:
        return False
    return not (isinstance(value, list) and not value)


def _unread_line(key: str, count: int) -> str:
    singular, plural, _ = UNREAD[key]
    return f"{count} {singular if count == 1 else plural} couldn't be read"


def _count(raw: Any) -> int | None:
    found = ReconciliationCount.from_json(raw)
    return found.count if found is not None else None


def turn_world(response: Any, story_id: str, names: Mapping[str, str]) -> TurnWorld | None:
    """The strip for one raw turn answer, or ``None`` when the answer carries no clock, no
    State change, settled change or Not-undone entry, and no non-zero reconciliation count (an
    M5-shaped answer, or a story with States and the clock off)."""
    if not isinstance(response, Mapping):
        return None
    raw_more: list[tuple[str, tuple[Any, ...]]] = []
    unread: list[str] = []

    clock_raw = response.get("clock")
    clock = TurnClock.from_json(clock_raw) if clock_raw is not None else None
    if clock_raw is not None and clock is None:
        raw_more.append((CLOCK_LABEL, (clock_raw,)))

    lists: dict[str, tuple[Any, ...]] = {}
    state_rows, lists["state_effects"] = state_change_rows(
        response.get("state_effects"), story_id, names
    )
    settled_rows, lists["settled_effects"] = state_change_rows(
        response.get("settled_effects"), story_id, names
    )
    required, lists["reconciliation_required"] = _required(
        response.get("reconciliation_required"), story_id
    )
    for key, failed in lists.items():
        if failed:
            unread.append(_unread_line(key, len(failed)))
            raw_more.append((UNREAD[key][2], failed))

    reconciliation_raw = response.get("reconciliation")
    count = _count(reconciliation_raw) if reconciliation_raw is not None else None
    if reconciliation_raw is not None and count is None:
        raw_more.append((RECONCILIATION_LABEL, (reconciliation_raw,)))

    shown = (
        clock_raw is not None
        or any(_present(response.get(key)) for key in UNREAD)
        or (reconciliation_raw is not None and count != 0)
    )
    if not shown:
        return None

    link = reconciliation_link(story_id, count)
    text = reconciliation_text(count)
    reconcile = link if link is not None else ((text, "") if text is not None else None)
    dispatched = any(row.outbox_id for row in (*state_rows, *settled_rows))
    viewpoint = response.get("output_viewpoint")
    return TurnWorld(
        clock=clock,
        clock_unchanged=clock is not None and clock.before.offset == clock.after.offset,
        state_rows=state_rows,
        settled_rows=settled_rows,
        unread=tuple(unread),
        required=required,
        reconcile=reconcile,
        outbox_notice=outbox_list_notice_html() if dispatched or required else "",
        viewpoint=(
            viewpoint_line(viewpoint, names)
            if isinstance(viewpoint, str) and viewpoint and viewpoint != OMNISCIENT
            else ""
        ),
        raw_more=tuple(raw_more),
    )

"""No-JavaScript row editing for a formset (#45's cast rows, lifted for #51's ingest rows).

A row is added or removed by a submit button on the same form: ``add`` appends a row, and
``remove`` (its value the row's index) deletes one; ``up`` and ``down`` (their value the row's
index) swap it with its neighbour (#54's pinned memories). The view re-binds the formset to
:func:`edit_rows`'s answer and re-renders it without validating.

A page with several formsets (#68's scenario editor; #64 convention 6) uses one ``op`` button
name instead: ``op=add:<prefix>``, ``op=remove:<prefix>-<n>`` and ``op=refresh`` ("Update
form": re-render from the posted state). :func:`parse_op` reads it and :func:`apply_op` edits
the named formset's rows the same way.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from django.http import QueryDict

ADD = "add"
REMOVE = "remove"
UP = "up"
DOWN = "down"
BUTTONS = (ADD, REMOVE, UP, DOWN)


def wants_edit(data: QueryDict) -> bool:
    """Whether the POST came from an add, remove or move button rather than the submit."""
    return any(button in data for button in BUTTONS)


def _index(data: QueryDict, button: str) -> int:
    try:
        return int(data.get(button, ""))
    except ValueError:
        return -1


def total_forms(data: QueryDict, prefix: str, max_rows: int) -> int:
    """The posted ``TOTAL_FORMS`` (1 when missing or malformed), clamped to ``0..max_rows + 1``."""
    try:
        total = int(data.get(f"{prefix}-TOTAL_FORMS", "1"))
    except ValueError:
        total = 1
    return max(0, min(total, max_rows + 1))


def rows(data: QueryDict, prefix: str, max_rows: int) -> list[dict[str, list[str]]]:
    """The posted rows, each ``{field: values}``, in order."""
    found = []
    for index in range(total_forms(data, prefix, max_rows)):
        start = f"{prefix}-{index}-"
        found.append({k[len(start) :]: data.getlist(k) for k in data if k.startswith(start)})
    return found


def edit_rows(
    data: QueryDict,
    prefix: str,
    *,
    max_rows: int,
    min_rows: int = 1,
    new_row: Mapping[str, Sequence[str]] | None = None,
) -> QueryDict:
    """The posted form with one row added (``add``; ``new_row`` its values), removed
    (``remove`` = its index) or moved (``up`` / ``down`` = its index), rows renumbered.
    Adding stops at ``max_rows``; the last ``min_rows`` rows are never removed. Other fields
    pass through unchanged."""
    posted = rows(data, prefix, max_rows)
    if ADD in data and len(posted) < max_rows:
        posted.append({name: list(values) for name, values in (new_row or {}).items()})
    elif REMOVE in data and len(posted) > min_rows:
        removed = _index(data, REMOVE)
        if 0 <= removed < len(posted):
            del posted[removed]
    elif UP in data or DOWN in data:
        moved = _index(data, UP if UP in data else DOWN)
        other = moved - 1 if UP in data else moved + 1
        if 0 <= moved < len(posted) and 0 <= other < len(posted):
            posted[moved], posted[other] = posted[other], posted[moved]
    edited = QueryDict(mutable=True)
    for key in data:
        if not key.startswith(f"{prefix}-") and key not in BUTTONS:
            edited.setlist(key, data.getlist(key))
    for index, row in enumerate(posted):
        for name, values in row.items():
            edited.setlist(f"{prefix}-{index}-{name}", values)
    edited[f"{prefix}-TOTAL_FORMS"] = str(len(posted))
    edited[f"{prefix}-INITIAL_FORMS"] = "0"
    edited[f"{prefix}-MIN_NUM_FORMS"] = str(min_rows)
    edited[f"{prefix}-MAX_NUM_FORMS"] = str(max_rows)
    return edited


OP = "op"
OP_ADD = "add"
OP_REMOVE = "remove"
OP_REFRESH = "refresh"


@dataclass(frozen=True)
class Op:
    """A posted ``op`` button: ``kind`` (``add``, ``remove``, ``refresh`` or any other value, such
    as ``save``), the formset ``prefix`` it names and the row ``index`` (``remove`` only)."""

    kind: str
    prefix: str = ""
    index: int | None = None

    @property
    def edits_rows(self) -> bool:
        return self.kind in (OP_ADD, OP_REMOVE, OP_REFRESH)


def parse_op(value: str | None, default: str = "save") -> Op:
    """``add:<prefix>`` / ``remove:<prefix>-<n>`` / any other value as a plain kind."""
    value = value or default
    kind, _, target = value.partition(":")
    if kind == OP_ADD and target:
        return Op(OP_ADD, target)
    if kind == OP_REMOVE and target:
        prefix, _, index = target.rpartition("-")
        if prefix and index.isdecimal():
            return Op(OP_REMOVE, prefix, int(index))
        return Op(OP_REFRESH)  # a malformed remove changes nothing
    if kind in (OP_ADD, OP_REMOVE):
        return Op(OP_REFRESH)
    return Op(kind)


def apply_op(
    data: QueryDict,
    op: Op,
    *,
    max_rows: Mapping[str, int],
    new_rows: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> QueryDict:
    """The posted form after ``op``: a row added to or removed from the formset ``op.prefix``
    names (at most ``max_rows[prefix]`` rows; none is kept), or unchanged for ``refresh`` or an
    unknown prefix. The ``op`` key itself is dropped."""
    edited: QueryDict
    if op.prefix in max_rows and op.kind in (OP_ADD, OP_REMOVE):
        request = data.copy()
        request.pop(OP, None)
        if op.kind == OP_ADD:
            request[ADD] = "1"
        else:
            request[REMOVE] = str(op.index)
        edited = edit_rows(
            request,
            op.prefix,
            max_rows=max_rows[op.prefix],
            min_rows=0,
            new_row=(new_rows or {}).get(op.prefix),
        )
    else:
        edited = data.copy()
        edited.pop(OP, None)
    return edited

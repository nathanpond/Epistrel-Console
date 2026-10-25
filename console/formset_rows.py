"""No-JavaScript row editing for a formset (#45's cast rows, lifted for #51's ingest rows).

A row is added or removed by a submit button on the same form: ``add`` appends a row, and
``remove`` (its value the row's index) deletes one; ``up`` and ``down`` (their value the row's
index) swap it with its neighbour (#54's pinned memories). The view re-binds the formset to
:func:`edit_rows`'s answer and re-renders it without validating.
"""

from collections.abc import Mapping, Sequence

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

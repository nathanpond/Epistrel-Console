"""No-JavaScript row editing for a formset (#45's cast rows, lifted for #51's ingest rows).

A row is added or removed by a submit button on the same form: ``add`` appends a row, and
``remove`` (its value the row's index) deletes one. The view re-binds the formset to
:func:`edit_rows`'s answer and re-renders it without validating.
"""

from collections.abc import Mapping, Sequence

from django.http import QueryDict

ADD = "add"
REMOVE = "remove"


def wants_edit(data: QueryDict) -> bool:
    """Whether the POST came from an add or remove button rather than the submit."""
    return ADD in data or REMOVE in data


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
    """The posted form with one row added (``add``; ``new_row`` its values) or removed
    (``remove`` = its index), rows renumbered. Adding stops at ``max_rows``; the last
    ``min_rows`` rows are never removed. Other fields pass through unchanged."""
    posted = rows(data, prefix, max_rows)
    if ADD in data and len(posted) < max_rows:
        posted.append({name: list(values) for name, values in (new_row or {}).items()})
    elif REMOVE in data and len(posted) > min_rows:
        try:
            removed = int(data.get(REMOVE, ""))
        except ValueError:
            removed = -1
        if 0 <= removed < len(posted):
            del posted[removed]
    edited = QueryDict(mutable=True)
    for key in data:
        if not key.startswith(f"{prefix}-") and key not in (ADD, REMOVE):
            edited.setlist(key, data.getlist(key))
    for index, row in enumerate(posted):
        for name, values in row.items():
            edited.setlist(f"{prefix}-{index}-{name}", values)
    edited[f"{prefix}-TOTAL_FORMS"] = str(len(posted))
    edited[f"{prefix}-INITIAL_FORMS"] = "0"
    edited[f"{prefix}-MIN_NUM_FORMS"] = str(min_rows)
    edited[f"{prefix}-MAX_NUM_FORMS"] = str(max_rows)
    return edited

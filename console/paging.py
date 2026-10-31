"""Shared Inspect read helpers (#187): page links and ids typed into the URL.

**C12: paging.** Canon's ``CURSOR_DROPPED``, ``CAST_UNAVAILABLE`` and ``_paging`` live here;
:func:`paging` takes the cursor parameter's name (``cursor``, ``after`` or ``after_id``). The
Canon tab imports them from here, unchanged in behaviour; #190/#192/#193 import, never copy.
Cursor validity (Canon's ``is_ulid`` check) stays with each page, since the Engine's cursors
differ per route; a page whose typed cursor it drops shows :data:`CURSOR_DROPPED`, and one whose
story read failed shows :data:`CAST_UNAVAILABLE`.

**C13: ids from the URL.** :func:`cast_id` trims and upper-cases a typed character id before
matching it against the cast's ids (CanonForm's rule, as ``forms.canon.entity_id`` applies it).
An id that still matches no cast member is kept as an extra option and sent if it passes
``is_ulid``; one that fails ``is_ulid`` is a field error (:data:`CHARACTER_INVALID`, defined here
once) and the page makes no call. Labels for kept options are each page's.
"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from django.http import QueryDict

from console.ids import is_ulid

CURSOR_DROPPED = "That page link was invalid — showing the first page"
CAST_UNAVAILABLE = "Cast names unavailable — showing ids"
CHARACTER_INVALID = (
    "Enter a 26-character character id (digits and capital letters, no I, L, O or U)"
)


def paging(
    params: QueryDict, cursor: str | None, next_cursor: str | None, *, param: str = "cursor"
) -> dict[str, Any]:
    """Convention 6's links: ``next_query`` keeps every filter, ``first_query`` drops the
    cursor parameter ``param``; ``paged`` when this page was reached by a (valid) cursor.
    ``params`` is copied, never mutated."""
    first = params.copy()
    first.pop(param, None)
    following = ""
    if next_cursor:
        after = first.copy()
        after[param] = next_cursor
        following = after.urlencode()
    return {"first_query": first.urlencode(), "next_query": following, "paged": bool(cursor)}


@dataclass(frozen=True)
class CastId:
    """A typed character id: ``value`` trimmed and upper-cased ("" for no filter); ``kept``
    when it is a valid id outside the cast (the page adds it as an extra option and sends it);
    ``error`` :data:`CHARACTER_INVALID` when it is not an id (no call)."""

    value: str
    kept: bool
    error: str


def cast_id(typed: str, cast_ids: Collection[str]) -> CastId:
    """C13's reading of a character id from the URL. An empty ``cast_ids`` (a failed cast read)
    keeps every valid id."""
    value = typed.strip().upper()
    if not value:
        return CastId("", False, "")
    if value in cast_ids:
        return CastId(value, False, "")
    if is_ulid(value):
        return CastId(value, True, "")
    return CastId(value, False, CHARACTER_INVALID)

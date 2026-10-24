"""Identifiers the Console mints: ULIDs for idempotency keys (one per form render, #43)."""

from ulid import ULID


def new_ulid() -> str:
    """A fresh ULID as its 26-character Crockford base32 string."""
    return str(ULID())

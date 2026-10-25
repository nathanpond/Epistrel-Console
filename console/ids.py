"""Identifiers the Console mints: ULIDs for idempotency keys (one per form render, #43), and the
Engine's own id pattern for ids an operator types (#70)."""

import re

from ulid import ULID

# The Engine's id pattern: 26 characters of Crockford base32, upper case.
ULID_PATTERN = re.compile(r"^[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{26}$")


def new_ulid() -> str:
    """A fresh ULID as its 26-character Crockford base32 string."""
    return str(ULID())


def is_ulid(value: str) -> bool:
    """Whether ``value`` is an id the Engine's ULID pattern accepts (case-sensitive)."""
    return bool(ULID_PATTERN.match(value))


class UlidConverter:
    """The ``<ulid:…>`` path converter (#77): an Engine id, so any other value is a Django 404
    before the view (and the Engine) is reached."""

    regex = "[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{26}"

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value

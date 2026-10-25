"""Hiding secrets in Engine payloads before the Console shows or stores them (#43, #44).

A key matches when its name, case-folded, is exactly one of :data:`REDACTED_NAMES`, so
``idempotency_key`` and ``api_key_fingerprint`` are kept.
"""

from typing import Any

MASK = "***"
REDACTED_NAMES = frozenset(
    {"api_key", "key", "secret", "password", "credential", "service_key", "authorization"}
)


def is_redacted(name: object) -> bool:
    return isinstance(name, str) and name.casefold() in REDACTED_NAMES


def redact(value: Any) -> Any:
    """A copy of ``value`` with every matching key's value replaced by ``***``, at any depth."""
    if isinstance(value, dict):
        return {k: MASK if is_redacted(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value

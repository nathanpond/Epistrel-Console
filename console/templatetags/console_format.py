"""Rendering Engine values that are not plain strings (health checks hold objects and numbers)."""

from typing import Any

from django import template

register = template.Library()

EMPTY = "—"
UNITS = {"_ms": "ms", "_s": "s"}


@register.filter
def check_value(value: Any) -> str:
    """A JSON value as readable text: ``{"alive": 2, "last_heartbeat_s": 2.2}`` →
    ``alive 2, last heartbeat 2.2 s``; ``null`` → ``—``."""
    if value is None:
        return EMPTY
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        if not value:
            return EMPTY
        return ", ".join(_pair(str(key), item) for key, item in value.items())
    if isinstance(value, list):
        return ", ".join(check_value(item) for item in value) if value else EMPTY
    return str(value)


def _pair(key: str, value: Any) -> str:
    for suffix, unit in UNITS.items():
        if key.endswith(suffix) and isinstance(value, int | float) and not isinstance(value, bool):
            return f"{key.removesuffix(suffix).replace('_', ' ')} {value} {unit}"
    return f"{key.replace('_', ' ')} {check_value(value)}"

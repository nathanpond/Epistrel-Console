"""Rendering Engine values that are not plain strings (health checks hold objects and numbers),
and the engine-wide ``engine_time`` / ``pretty_json`` filters (#43)."""

import json
from datetime import UTC, datetime
from typing import Any

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

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


@register.filter
def engine_time(value: Any) -> SafeString | str:
    """An Engine timestamp as ``YYYY-MM-DD HH:MM:SS UTC`` with the raw ISO string in ``title``.

    ``None`` renders ``—``; a value that is not an ISO timestamp renders as given (escaped).
    """
    if value is None or value == "":
        return EMPTY
    if isinstance(value, datetime):
        moment, raw = value, value.isoformat()
    else:
        raw = str(value)
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            return raw
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    shown = moment.strftime("%Y-%m-%d %H:%M:%S UTC")
    return format_html('<time datetime="{}" title="{}">{}</time>', raw, raw, shown)


@register.filter
def pretty_json(value: Any) -> str:
    """A JSON value indented by two spaces (put it in ``<pre>``); non-ASCII kept as is."""
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


@register.filter
def json_value(value: Any) -> str:
    """A JSON value on one line: ``null``, ``true``, ``"medium"``, ``{"model": null}`` (#50's
    profile-defaults table)."""
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)

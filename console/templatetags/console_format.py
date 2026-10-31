"""Rendering Engine values that are not plain strings (health checks hold objects and numbers),
and the engine-wide ``engine_time`` / ``pretty_json`` filters (#43); ``new_idempotency_key`` for a
page with one form per row (#143)."""

import json
from datetime import UTC, datetime
from typing import Any

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.ids import new_ulid
from console.renders import WarningLine, warning_lines
from console.wording import humanised

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


@register.filter
def humanise(value: Any) -> str:
    """An Engine enum value for display (#64 convention 17): ``very_cautious`` → ``very
    cautious``; ``None`` or blank → ``—``; an unknown value renders the same way, raw."""
    if value is None or value == "":
        return EMPTY
    return str(value).replace("_", " ")


@register.filter(name="humanised")
def humanised_filter(value: Any) -> str:
    """An enum value for an M5 page (#187 convention 16): ``very_cautious`` → ``Very cautious``
    (``console.wording.humanised``); ``None`` or blank → ``—``; a non-string through ``str()``.
    The template puts the raw value in ``title`` itself."""
    if value is None or value == "":
        return EMPTY
    return humanised(str(value))


@register.filter
def turn_warnings(value: Any) -> tuple[WarningLine, ...]:
    """#202: a turn report's ``warnings`` as Warnings lines with short ids and no Retrieval link
    (the operation detail page: no labels, no story)."""
    return warning_lines(value or (), {}, "")


MIB = 1024 * 1024
BYTE_UNITS = ("KiB", "MiB", "GiB", "TiB")


@register.filter
def human_bytes(value: Any) -> str:
    """A byte count for display (#70): "512 bytes", "1.5 KiB", "12.0 MiB" (one decimal);
    "—" for anything that is not a count."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return EMPTY
    if value < 1024:
        return f"{value} bytes"
    size = float(value)
    for unit in BYTE_UNITS:
        size /= 1024
        if size < 1024 or unit == BYTE_UNITS[-1]:
            return f"{size:.1f} {unit}"
    return EMPTY  # unreachable: the loop always returns


def mib(value: int) -> str:
    """A byte count in MiB with one decimal (#70's upload limits): "8.0 MiB"."""
    return f"{value / MIB:.1f} MiB"


@register.filter
def count(value: Any) -> str:
    """A count with thousands separators (#135 convention 5): ``12000`` → ``12,000``; "—" for
    anything that is not an integer."""
    if not isinstance(value, int) or isinstance(value, bool):
        return EMPTY
    return f"{value:,}"


@register.filter
def percent(value: Any) -> str:
    """A rate as a percentage with one decimal (#135 convention 5): ``0.1309`` → ``13.1%``; "—"
    for ``None`` or anything that is not a number (#149)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return EMPTY
    return f"{value * 100:.1f}%"


@register.simple_tag
def new_idempotency_key() -> str:
    """A fresh ULID per call (#43's per-render key, #135 convention 8), for a page that renders
    several write forms (#143's memory row buttons): ``{% new_idempotency_key as key %}``."""
    return new_ulid()

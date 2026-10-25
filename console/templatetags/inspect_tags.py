"""Reason text with backtick code spans (#135, :mod:`console.inspect`): ``code_spans`` for a
panel (escaped, then each `` `span` `` as ``<code>``), ``no_backticks`` for a ``title``; and
``engine_id`` for convention 2's short Engine id (#140)."""

import re
from typing import Any

from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe

from console.inspect import short_id

register = template.Library()

SPAN = re.compile(r"`([^`]+)`")


@register.filter
def code_spans(value: Any) -> SafeString:
    """``"Needs the `episodic` module"`` → ``Needs the <code>episodic</code> module``. The text
    is escaped first, so only the Console's ``<code>`` is markup."""
    return mark_safe(SPAN.sub(r"<code>\1</code>", escape(str(value))))  # noqa: S308 - escaped above


@register.filter
def no_backticks(value: Any) -> str:
    """The text with its backticks removed (autoescaped as usual where it is shown)."""
    return str(value).replace("`", "")


@register.filter
def engine_id(value: Any) -> SafeString | str:
    """Convention 2: an Engine id as ``<code title="<full id>">…<last 8></code>``; ``None`` or
    blank renders "—"."""
    if value is None or value == "":
        return "—"
    return format_html('<code title="{}">{}</code>', value, short_id(str(value)))

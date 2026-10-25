"""Reason text with backtick code spans (#135, :mod:`console.inspect`): ``code_spans`` for a
panel (escaped, then each `` `span` `` as ``<code>``), ``no_backticks`` for a ``title``;
``engine_id`` for convention 2's short Engine id (#140); ``decimal3`` for convention 5's
scores (#141); ``memory_action_url`` for a memory row's action links (#143)."""

import re
from typing import Any

from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe

from console.inspect import memory_action_url as _memory_action_url
from console.inspect import short_id
from console.retrieval import decimal3 as _decimal3

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


@register.filter
def decimal3(value: Any) -> str:
    """Convention 5: a score or similarity to 3 decimals (``0.81234`` → ``0.812``); "—" for
    ``None`` or anything that is not a number."""
    return _decimal3(value)


@register.simple_tag
def memory_action_url(action: str, story_id: str, memory_id: str, next_url: str) -> str:
    """#143: ``{% memory_action_url "quarantine" story_id m.id actions_next %}``, the link to a
    memory's action page with ``next`` (see :func:`console.inspect.memory_action_url`)."""
    return _memory_action_url(action, story_id, memory_id, next_url)

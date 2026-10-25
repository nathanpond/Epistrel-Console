"""Erasure template helpers.

- ``{% erase_url kind subject scope [story] as href %}``: the erasure form pre-filled for one
  subject (#86), or "" while ``/erasure/new/`` does not exist, so "Erase…" shows only once it
  does.
- ``{{ text|mark_erased }}`` (#87): history text with each exact ``[erased]`` span an erasure
  left marked ``<mark class="erased">``; the text itself is always escaped first.
- ``{{ text|code_spans }}`` (#87): boundary text with its backtick spans as ``<code>``, escaped
  first; any other markdown shows literally.
"""

import re
from typing import cast

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import SafeData, SafeString, mark_safe

from console import erasure

register = template.Library()

ERASED = "[erased]"  # what the Engine writes in place of each span that named the subject
ERASED_MARK = f'<mark class="erased">{ERASED}</mark>'
CODE_SPAN = re.compile(r"`([^`]+)`")


@register.simple_tag
def erase_url(kind: str, subject: str, scope: str, story: str = "") -> str:
    if kind not in erasure.SUBJECT_KINDS or scope not in erasure.SCOPE_LABELS or not subject:
        return ""
    shown = cast(erasure.ScopeName, scope)
    url = erasure.new_url(kind, subject, scope=shown, story=story or None)
    return url or ""


def _text(value: object) -> str | SafeData:
    if value is None:
        return ""
    return value if isinstance(value, SafeData) else str(value)


@register.filter
def mark_erased(value: object) -> SafeString:
    """Escape ``value`` (unless already safe), then mark each exact, case-sensitive
    ``[erased]``. ``[`` and ``]`` survive escaping, so the search runs on escaped text."""
    escaped = str(conditional_escape(_text(value)))
    return mark_safe(escaped.replace(ERASED, ERASED_MARK))  # noqa: S308 - escaped above


@register.filter
def code_spans(value: object) -> SafeString:
    """Escape ``value``, then turn each `` `span` `` into ``<code>span</code>``."""
    escaped = str(conditional_escape(_text(value)))
    return mark_safe(CODE_SPAN.sub(r"<code>\1</code>", escaped))  # noqa: S308 - escaped above

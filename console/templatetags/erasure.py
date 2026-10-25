"""``{% erase_url kind subject scope [story] as href %}``: the erasure form pre-filled for one
subject (#86), or "" while ``/erasure/new/`` does not exist, so "Erase…" shows only once it
does."""

from typing import cast

from django import template

from console import erasure

register = template.Library()


@register.simple_tag
def erase_url(kind: str, subject: str, scope: str, story: str = "") -> str:
    if kind not in erasure.SUBJECT_KINDS or scope not in erasure.SCOPE_LABELS or not subject:
        return ""
    shown = cast(erasure.ScopeName, scope)
    url = erasure.new_url(kind, subject, scope=shown, story=story or None)
    return url or ""

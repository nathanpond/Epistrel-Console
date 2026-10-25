"""``{% load reports %}``: ``{{ location|location_anchor:anchors }}`` is the anchor (no ``#``) a
report entry links to; ``anchors`` (optional) is the collection of anchors the page has, for the
nearest-ancestor fallback (``console.reports``)."""

from collections.abc import Collection
from typing import Any

from django import template

from console import reports
from console.engine.models import EntryLocation

register = template.Library()


@register.filter
def location_anchor(location: Any, anchors: Collection[str] | None = None) -> str:
    if not isinstance(location, EntryLocation):
        return ""
    known = anchors if isinstance(anchors, Collection) and not isinstance(anchors, str) else None
    return reports.location_anchor(location, known) or ""

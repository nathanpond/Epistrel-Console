"""``{% load reports %}``: ``{{ location|location_anchor:anchors }}`` is the anchor (no ``#``) a
report entry links to; ``anchors`` (optional) is the collection of anchors the page has, for the
nearest-ancestor fallback (``console.reports``). ``{{ source|report_items:"errors" }}`` is the
``_validation_report.html`` loop's items for a report or a scenario's ``ShownReport``."""

from collections.abc import Collection
from typing import Any

from django import template

from console import reports
from console.catalog.scenario_report import ShownEntry, ShownReport
from console.engine.models import EntryLocation

register = template.Library()


@register.filter
def location_anchor(location: Any, anchors: Collection[str] | None = None) -> str:
    if not isinstance(location, EntryLocation):
        return ""
    known = anchors if isinstance(anchors, Collection) and not isinstance(anchors, str) else None
    return reports.location_anchor(location, known) or ""


@register.filter
def report_items(source: Any, kind: str) -> list[ShownEntry]:
    """``kind`` ("errors" or "warnings") of ``source``: a :class:`ShownReport`'s own entries, or
    a :class:`ValidationReport`'s (or a ``CreationReport``'s, whose lists may be ``None``)
    wrapped as :class:`ShownEntry` with nothing added (the entry id shown, unlinked), so one
    loop renders both."""
    if isinstance(source, ShownReport):
        items: list[ShownEntry] = getattr(source, kind)
        return items
    entries = getattr(source, kind, None) or []
    return [ShownEntry(entry, other_entry=True) for entry in entries]

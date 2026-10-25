"""``{% impact_rows impact story_labels as rows %}`` and ``{% impact_more impact as n %}`` for
``console/_impact_report.html`` (``console.impact``)."""

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from django import template
from django.urls import NoReverseMatch, reverse

from console.engine.models import ImpactReport
from console.impact import ImpactRow, impact_rows, more_count, short_fingerprint

register = template.Library()


@register.simple_tag(name="impact_rows")
def impact_rows_tag(
    impact: ImpactReport, labels: Mapping[str, Any] | None = None
) -> list[ImpactRow]:
    return impact_rows(impact, labels if isinstance(labels, Mapping) else None)


@register.simple_tag(name="impact_more")
def impact_more_tag(impact: ImpactReport) -> int:
    return more_count(impact)


@register.filter
def short_fp(value: Any) -> str:
    return short_fingerprint(value if isinstance(value, str) else None)


@register.filter
def scenario_version_url(value: Any) -> str:
    """``<catalog id>@<version>`` (an unmet recommendation's ``recommended_by``) as that scenario
    version's page; "" when it does not parse or the route does not exist."""
    if not isinstance(value, str):
        return ""
    scenario_id, _, version = value.partition("@")
    if not scenario_id:
        return ""
    try:
        url = reverse("scenarios:detail", args=[scenario_id])
    except NoReverseMatch:
        return ""
    return f"{url}?{urlencode({'version': version})}" if version.isdigit() else url

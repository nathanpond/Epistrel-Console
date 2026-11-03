"""``{% impact_rows impact story_labels as rows %}`` and ``{% impact_more impact as n %}`` for
``console/_impact_report.html`` (``console.impact``), and ``|budget_warning`` for
``console/_budget_warnings.html`` (#234)."""

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils.safestring import SafeString

from console.engine.models import ImpactReport, ProfileValidationWarning
from console.forms.profile_schema_form import warning_text
from console.impact import ImpactRow, impact_rows, more_count, quality_class, short_fingerprint
from console.templatetags.inspect_tags import code_spans

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


@register.filter(name="quality_class")
def quality_class_filter(value: Any) -> str:
    """A quality status's badge class (#85): ``certified`` green, ``stale`` amber, else grey."""
    return quality_class(value if isinstance(value, str) else "")


@register.filter
def budget_warning(value: Any) -> SafeString | str:
    """A ``ProfileValidationWarning`` as #84's ``warning_text``, its setting path in ``<code>``
    (escaped first); "" for anything else."""
    if not isinstance(value, ProfileValidationWarning):
        return ""
    if value.code == "below_safety_minimum":
        return code_spans(warning_text(value))
    return code_spans(f"{value.code}: `{value.setting}`")

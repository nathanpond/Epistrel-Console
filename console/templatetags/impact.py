"""``{% impact_rows impact story_labels as rows %}``, ``{% impact_more impact as n %}`` and
(#310) ``{% certifications_lost impact story_labels as lost %}`` for
``console/_impact_report.html`` (``console.impact``), ``|budget_warning`` for
``console/_budget_warnings.html`` (#234), and E13's one template-side quality adapter,
``|quality_line`` (#295):
``{% include "console/_quality.html" with quality=status|quality_line %}``, used by
``_impact_report.html`` and the turn report; no other module defines one."""

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils.safestring import SafeString

from console.certification import QualityLine
from console.certification import quality_line as quality_line_of
from console.engine.models import FloorsMet, ImpactReport, ProfileValidationWarning, QualityStatus
from console.forms.profile_schema_form import warning_text
from console.impact import (
    CertificationLostRow,
    ImpactRow,
    certifications_lost,
    impact_rows,
    more_count,
    quality_class,
    short_fingerprint,
)
from console.templatetags.inspect_tags import code_spans

register = template.Library()


@register.simple_tag(name="impact_rows")
def impact_rows_tag(
    impact: ImpactReport, labels: Mapping[str, Any] | None = None
) -> list[ImpactRow]:
    return impact_rows(impact, labels if isinstance(labels, Mapping) else None)


@register.simple_tag(name="certifications_lost")
def certifications_lost_tag(
    impact: ImpactReport, labels: Mapping[str, Any] | None = None
) -> list[CertificationLostRow]:
    return certifications_lost(impact, labels if isinstance(labels, Mapping) else None)


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


@register.filter(name="quality_line")
def quality_line(value: Any) -> QualityLine | None:
    """E13: a ``QualityStatus``, ``FloorsMet`` or status string as ``console.certification
    .quality_line``; ``None`` (or "", an impact row without one) → ``None``."""
    if isinstance(value, QualityStatus | FloorsMet) or (isinstance(value, str) and value):
        return quality_line_of(value)
    return None


@register.filter
def budget_warning(value: Any) -> SafeString | str:
    """A ``ProfileValidationWarning`` as #84's ``warning_text``, its setting path in ``<code>``
    (escaped first); "" for anything else."""
    if not isinstance(value, ProfileValidationWarning):
        return ""
    if value.code == "below_safety_minimum":
        return code_spans(warning_text(value))
    return code_spans(f"{value.code}: `{value.setting}`")

"""E13's one template-side adapter (#295): ``{{ status|quality_line }}`` in
``console/templatetags/impact.py``, and ``_impact_report.html``'s "Quality after" through it."""

from django.template import engines
from django.template.loader import render_to_string
from django.utils.html import escape

from console.certification import quality_line
from console.engine.models import FloorsMet, ImpactReport, QualityStatus
from console.templatetags.impact import quality_line as quality_line_filter
from tests.profile_helpers import affected, impact


def test_the_filter_equals_the_function() -> None:
    status = QualityStatus(status="certified", certification_id="C1", floors_passed=["a"])
    floors = FloorsMet(fingerprint="sha256:" + "b" * 64, floors_met=["x"], status="stale")
    assert quality_line_filter(status) == quality_line(status)
    assert quality_line_filter(floors) == quality_line(floors)
    assert quality_line_filter("uncertified") == quality_line("uncertified")
    template = engines["django"].from_string("{% load impact %}{{ status|quality_line }}")
    assert template.render({"status": status}) == escape(str(quality_line(status)))


def test_none_gives_none_and_the_badge_renders_a_dash() -> None:
    assert quality_line_filter(None) is None
    assert quality_line_filter("") is None
    html = (
        engines["django"]
        .from_string(
            "{% load impact %}"
            '{% include "console/_quality.html" with quality=status|quality_line %}'
        )
        .render({"status": None})
    )
    assert html.strip() == "—"


def _impact(**changes: object) -> ImpactReport:
    found = ImpactReport.from_json(impact(affected(), **changes))
    assert found is not None
    return found


def test_the_impact_reports_quality_after_reads_uncertified() -> None:
    """**Complement:** the row's "Quality after" for ``uncertified`` is "Uncertified"."""
    html = render_to_string(
        "console/_impact_report.html",
        {"impact": _impact(quality_status_after={"status": "uncertified"}), "mode": "story"},
    )
    badge = '<span class="quality" title="uncertified">Uncertified</span>'
    assert f"<td>{badge}</td>" in html
    assert f'<dd class="quality-after">{badge}</dd>' in html


def test_an_impact_row_without_a_quality_reads_a_dash() -> None:
    html = render_to_string(
        "console/_impact_report.html",
        {
            "impact": ImpactReport.from_json(impact(affected(quality_status_after=None))),
            "mode": "story",
        },
    )
    assert "<td>—</td>" in html
    assert '<dd class="quality-after">—</dd>' in html

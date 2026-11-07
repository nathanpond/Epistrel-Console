"""The impact partial's M7 columns (#310, E8): Mode, Gap, the Backfill range and Certifications
lost, rendered directly through ``console/_impact_report.html``.

``M6_CASES`` are the M6 reports #84/#85's tests render. Their goldens in
``tests/fixtures/impact_m6/`` were captured from the partial before #310 edited it (E24); an M6
report renders identically apart from the new Mode and Gap header cells and their "—" cells.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import override_settings

from console.engine.choices import StoryLabel
from console.engine.models import ImpactReport
from tests.certification_helpers import unregistered_certification_pages
from tests.golden import normalise_html
from tests.profile_helpers import FP_AFTER, FP_BEFORE, STORY_A, STORY_B, affected, impact

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "impact_m6"
GUARANTEES_HREF = "/profiles/01JPRF00000000000000000002/?version=3"
UNAVAILABLE = (
    "This story has history the Engine can't backfill — it keeps its current configuration"
)


def _held() -> dict[str, Any]:
    """#84's held edit (``test_profiles_edit.held``)."""
    return impact(
        affected(),
        affected(
            STORY_B,
            capabilities_gained=["episodic"],
            capabilities_lost=[],
            backfill={
                "needed": True,
                "estimate": {"model_calls": 1200, "tokens": 345678, "duration_s": 75.0},
            },
            upgrade_unavailable=True,
        ),
        warnings=["polish_off"],
        guarantees_lost=["G-PIN-1"],
        recommendations_unmet=[
            {"capability": "polish", "recommended_by": "01JSCN0000000000000000000A@2"}
        ],
        affected_count=1003,
    )


def _no_followers() -> dict[str, Any]:
    return impact(
        warnings=["data_producing_increase"],
        fingerprint_before=FP_BEFORE,
        fingerprint_after=FP_AFTER,
        capabilities_after=["rolling_summary", "episodic"],
        quality_status_after={"status": "uncertified"},
    )


def _budget_and_gained() -> dict[str, Any]:
    return impact(
        affected(backfill={"needed": True, "estimate": None}),
        guarantees_gained=["G-EPI-1", "G-PIN-2"],
        budget_warnings=[
            {
                "code": "below_safety_minimum",
                "setting": "budgets.model_calls",
                "model_calls": 1,
                "minimum_required": 3,
                "reservation": {"generation": 1, "guardrail_input": 1, "guardrail_output": 1},
            }
        ],
    )


def _story_override() -> dict[str, Any]:
    """#85's override change: one story, in "story" mode."""
    return impact(
        affected(
            STORY_A,
            capabilities_gained=["episodic"],
            quality_status_after={"status": "stale", "invalidated_reason": "drift"},
        ),
        guarantees_lost=["G-PIN-1"],
        fingerprint_before=FP_BEFORE,
        fingerprint_after=FP_AFTER,
        capabilities_after=["episodic"],
        quality_status_after={"status": "certified"},
    )


def _labels() -> dict[str, StoryLabel]:
    return {
        STORY_A: StoryLabel(STORY_A, "Harbour"),
        STORY_B: StoryLabel(STORY_B, STORY_B, "couldn't read it"),
    }


# name -> (report, extra context)
M6_CASES: dict[str, tuple[Callable[[], dict[str, Any]], dict[str, Any]]] = {
    "held_profile_labels": (
        _held,
        {
            "mode": "profile",
            "labels": True,
            "guarantees_href": GUARANTEES_HREF,
            "upgrade_unavailable_text": UNAVAILABLE,
            "show_apply": True,
            "apply_url": "/profiles/01JPRF00000000000000000002/apply/",
            "signed_payload": "TOKEN",
            "cancel_url": "/profiles/01JPRF00000000000000000002/edit/",
            "idempotency_key": "01JKEY00000000000000000001",
            "csrf_token": "CSRF",
        },
    ),
    "held_profile": (_held, {"mode": "profile"}),
    "no_followers_profile": (_no_followers, {"mode": "profile"}),
    "budget_gained_profile_labels": (
        _budget_and_gained,
        {"mode": "profile", "labels": True, "guarantees_href": GUARANTEES_HREF},
    ),
    "story_override_story": (_story_override, {"mode": "story"}),
    "story_override_story_labels": (_story_override, {"mode": "story", "labels": True}),
    "held_story": (_held, {"mode": "story"}),
}


def render_partial(report: dict[str, Any], **context: Any) -> str:
    parsed = ImpactReport.from_json(report)
    assert parsed is not None
    if context.pop("labels", False):
        context["story_labels"] = _labels()
    return render_to_string("console/_impact_report.html", {"impact": parsed, **context})


def render_case(name: str) -> str:
    build, context = M6_CASES[name]
    return render_partial(build(), **dict(context))


NEW_HEADERS = "<th>Mode</th><th>Gap</th>"
NEW_CELLS = '<td class="mode">—</td><td class="gap">—</td>'


def without_new_columns(html: str) -> str:
    """``html`` normalised with #310's two header cells and their "—" cells taken out."""
    return normalise_html(html).replace(NEW_HEADERS, "").replace(NEW_CELLS, "")


@override_settings(ROOT_URLCONF="epistrel_console.urls")
@pytest.mark.parametrize("name", sorted(M6_CASES))
def test_m6_reports_render_unchanged(name: str) -> None:
    golden = normalise_html((GOLDEN_DIR / f"{name}.html").read_text(encoding="utf-8"))
    html = normalise_html(render_case(name))
    rows = golden.count('<tr id="impact-')
    # The only difference: one header pair (with a table) and one "—" pair per row.
    assert html.count(NEW_HEADERS) == (1 if rows else 0)
    assert html.count(NEW_CELLS) == rows
    assert "Certifications lost" not in html
    assert without_new_columns(html) == golden


# --- the M7 columns -------------------------------------------------------------------------------

M7_URLS = "tests.urls_with_m7_pages"
CERT_1 = "01JM7CT0000000000000000001"
CERT_2 = "01JM7CT0000000000000000002"
NEEDED = {
    "needed": True,
    "estimate": {"model_calls": 12, "tokens": 18000, "duration_s": 40.0},
    "range": {"from_turn": 1, "to_turn": 6},
}


def cells(html: str, story_id: str) -> str:
    row = html[html.index(f'<tr id="impact-{story_id}"') :]
    return row[: row.index("</tr>")]


def test_mode_gap_and_range_columns() -> None:
    report = impact(
        affected(STORY_A, upgrade_mode="backfill", queue_position=2, backfill=NEEDED),
        affected(
            STORY_B,
            upgrade_mode="forward_only",
            forward_only_gap={"capabilities": ["episodic"], "through_turn": 3},
            data_kept_dormant=True,
        ),
    )
    html = render_partial(report, mode="profile")
    assert "<th>Backfill</th><th>Mode</th><th>Gap</th><th>Upgrade</th>" in html
    first, second = cells(html, STORY_A), cells(html, STORY_B)
    assert '<td class="mode">Backfill · queue #2</td>' in first
    assert "12 model calls, 18,000 tokens, ~40 s (turns 1–6)" in first  # noqa: RUF001
    assert '<td class="gap">—</td>' in first
    assert '<td class="mode">Forward only</td>' in second
    assert '<td class="gap">No data for episodic through turn 3 · data kept dormant</td>' in second


def test_mode_and_range_edge_cells() -> None:
    one_turn = {**NEEDED, "range": {"from_turn": 1200, "to_turn": 1200}}
    report = impact(
        affected(STORY_A, queue_position=1, backfill=one_turn),
        affected(STORY_B, upgrade_mode="sideways", backfill={**NEEDED, "needed": False}),
    )
    html = render_partial(report, mode="profile")
    first, second = cells(html, STORY_A), cells(html, STORY_B)
    assert '<td class="mode">— · queue #1</td>' in first
    assert "(turn 1,200)" in first
    # An unknown mode is humanised (E23); a range on a backfill not needed is left out.
    assert '<td class="mode">Sideways</td>' in second
    assert '<td class="backfill">—</td>' in second


def lost_report() -> dict[str, Any]:
    return impact(
        affected(STORY_A),
        certifications_lost=[
            {"story_id": STORY_A, "certification_id": CERT_1, "from": "certified", "to": "stale"},
            {"story_id": None, "certification_id": CERT_2, "from": "certified", "to": "revoked"},
        ],
    )


def lost_block(html: str) -> str:
    block = html[html.index("<h4>Certifications lost</h4>") :]
    return block[: block.index("</ul>")]


@override_settings(ROOT_URLCONF=M7_URLS)
def test_certifications_lost_block() -> None:
    html = render_partial(lost_report(), mode="profile", labels=True)
    block = lost_block(html)
    # After the summary <dl>, before the per-story table.
    assert html.index('class="impact-summary"') < html.index("Certifications lost")
    assert html.index("Certifications lost") < html.index('class="affected-stories"')
    first, second = block.split("<li>")[1:]
    assert f'<a href="/stories/{STORY_A}/">Harbour <code>{STORY_A}</code></a>' in first
    assert '<span class="status-before" title="certified">Certified</span>' in first
    assert '<span class="status-after" title="stale">Stale</span>' in first
    assert f'<a class="certification-link" href="/engine/certifications/{CERT_1}/">' in first
    assert second.startswith("this profile's own configuration: ")
    # E23: a status QUALITY_TEXT lacks is humanised, the raw value in title.
    assert '<span class="status-after" title="revoked">Revoked</span>' in second
    assert f'href="/engine/certifications/{CERT_2}/"' in second


@override_settings(ROOT_URLCONF="epistrel_console.urls")
def test_certification_ids_are_plain_before_their_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Complement (D19): before #313 registered its page the id isn't linked."""
    unregistered_certification_pages(monkeypatch)
    block = lost_block(render_partial(lost_report(), mode="profile"))
    assert "certification-link" not in block
    assert f'<code title="{CERT_1}">' in block


def test_no_certifications_lost_no_block() -> None:
    html = render_partial(impact(affected(), certifications_lost=[]), mode="profile")
    assert "Certifications lost" not in html

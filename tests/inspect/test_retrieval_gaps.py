"""#332: Inspect → Retrieval's "Coverage gaps in this window" (FR-PROF-8): the answer's
``coverage_gaps`` as #330's turn-report lines (reused, not reworded), each linked to the Backfill
page's gap fill, with ``GAP_NOTE``; a malformed entry counted with #330's unread note. With no
gaps the page is main's (``golden/retrieval_no_gaps.html``, captured before the change) and the
read is the same one retrieve call."""

import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.test import Client, override_settings
from django.utils.html import escape

from console.engine.models import RetrievalBody
from console.retrieval import GAP_NOTE, gap_lines
from console.turn_reports import coverage_gap_lines, stored_gaps
from tests.fakes.engine import FakeEngine
from tests.golden import normalise_html
from tests.inspect.helpers import EFFECTIVE, STORY, STORY_PATH
from tests.inspect.test_retrieval import RETRIEVE, ask, engine, retrieve_calls  # noqa: F401
from tests.inspect.test_retrieval import fixture as retrieval_fixture

pytestmark = pytest.mark.django_db

M7_URLS = "tests.urls_with_m7_pages"
WITHOUT_BACKFILL = "tests.inspect.urls_without_backfill"
GOLDEN = Path(__file__).parent / "golden" / "retrieval_no_gaps.html"
GAP_FILL = f"/stories/{STORY}/backfill/#gap-fill"
PIPELINE = {"capability": "pipeline", "from_turn": 1, "to_turn": 6}
BAD = {"capability": "pipeline", "from_turn": "x", "to_turn": 6}
UNREAD_ONE = "1 gap entry couldn&#x27;t be read"


def with_gaps(engine: FakeEngine, gaps: Any, *, off_schema: bool = False) -> None:  # noqa: F811
    """The recorded retrieve answer with ``coverage_gaps`` injected; ``off_schema`` for a
    malformed entry the schema rejects (answered through ``side_effect``, unvalidated)."""
    body = retrieval_fixture()
    body["coverage_gaps"] = gaps
    if off_schema:
        engine.side_effect("GET", RETRIEVE, lambda request: httpx.Response(200, json=body))
    else:
        engine.respond("GET", RETRIEVE, 200, json=body)


def section(html: str) -> str:
    found = re.search(r'<section class="coverage-gaps">(.*?)</section>', html, re.S)
    assert found is not None
    return found.group(1)


def body_with(gaps: Any) -> RetrievalBody:
    body = retrieval_fixture()
    body["coverage_gaps"] = gaps
    parsed = RetrievalBody.from_json(body)
    assert parsed is not None
    return parsed


def test_section_lines_and_note(operator_client: Client, engine: FakeEngine) -> None:  # noqa: F811
    with_gaps(engine, [PIPELINE])
    with override_settings(ROOT_URLCONF=M7_URLS):
        html = ask(operator_client)
    gaps = section(html)
    assert "<h2>Coverage gaps in this window</h2>" in gaps
    assert (
        "<li><code>pipeline</code> has no data for turns 1\u20136 (a forward-only upgrade or a "
        f'backfill not yet run) — <a class="gap-fill" href="{GAP_FILL}">Fill the gap</a></li>'
    ) in gaps
    assert f'<p class="gap-note muted">{escape(GAP_NOTE)}</p>' in gaps
    assert "couldn" not in gaps
    # above the results: before Resolution
    assert html.index('<section class="coverage-gaps">') < html.index(
        '<section class="retrieval-resolution">'
    )


def test_section_line_without_backfill_has_no_link(
    operator_client: Client,
    engine: FakeEngine,  # noqa: F811
) -> None:
    """**Complement:** under a urlconf without ``stories:backfill`` (E24, D19) the line renders
    with no link."""
    with_gaps(engine, [PIPELINE])
    with override_settings(ROOT_URLCONF=WITHOUT_BACKFILL):
        html = ask(operator_client)
    gaps = section(html)
    assert "<li><code>pipeline</code> has no data for turns 1\u20136" in gaps
    assert "run)</li>" in gaps
    assert "gap-fill" not in gaps
    assert "Fill the gap" not in gaps


def test_lines_match_turn_report() -> None:
    """The explainer reuses #330's wording: the same gaps give the same ``GapLine`` texts."""
    gaps = [PIPELINE, {"capability": "nested_beliefs", "from_turn": 3, "to_turn": 3}, PIPELINE]
    with override_settings(ROOT_URLCONF=M7_URLS):
        expected = coverage_gap_lines(stored_gaps({"coverage_gaps": gaps}), STORY)
        got = gap_lines(STORY, body_with(gaps))
    assert got.lines == tuple(expected)
    assert len(got.lines) == 2  # #330's de-duplication
    assert got.unread == 0
    assert got.note == GAP_NOTE


def test_unread_entry_noted(operator_client: Client, engine: FakeEngine) -> None:  # noqa: F811
    with_gaps(engine, [PIPELINE, BAD], off_schema=True)
    with override_settings(ROOT_URLCONF=M7_URLS):
        html = ask(operator_client)
    gaps = section(html)
    assert gaps.count("<li>") == 1
    assert f'<p class="muted">{UNREAD_ONE}</p>' in gaps


def test_only_unread_entries_show_the_section_without_the_note(
    operator_client: Client,
    engine: FakeEngine,  # noqa: F811
) -> None:
    """The section shows for an unread count alone (a non-list value counts as 1), with no lines
    and no ``GAP_NOTE``."""
    with_gaps(engine, {"capability": "pipeline"}, off_schema=True)
    html = ask(operator_client)
    gaps = section(html)
    assert "<ul" not in gaps
    assert "gap-note" not in gaps
    assert f'<p class="muted">{UNREAD_ONE}</p>' in gaps


def test_gaps_show_when_episodic_is_skipped() -> None:
    """A gap is about stored data, so it doesn't depend on the episodic stage."""
    body = retrieval_fixture()
    body["skipped"] = ["episodic"]
    body["coverage_gaps"] = [PIPELINE]
    parsed = RetrievalBody.from_json(body)
    assert parsed is not None
    assert len(gap_lines(STORY, parsed).lines) == 1


@pytest.mark.parametrize("gaps", ["absent", []])
def test_no_gaps_renders_main(
    operator_client: Client,
    engine: FakeEngine,  # noqa: F811
    gaps: Any,
) -> None:
    """**Complement:** absent and ``[]`` ``coverage_gaps`` render main's page (E24 golden,
    normalised) with no section, from the same one retrieve read."""
    if gaps != "absent":
        with_gaps(engine, gaps)
    html = ask(operator_client)
    assert "coverage-gaps" not in html
    assert normalise_html(html) == GOLDEN.read_text().rstrip("\n")
    assert len(retrieve_calls(engine)) == 1
    assert [r.url.path for r in engine.requests] == MAIN_CALLS


MAIN_CALLS = [p.format(story_id=STORY) for p in (EFFECTIVE, STORY_PATH, RETRIEVE)]

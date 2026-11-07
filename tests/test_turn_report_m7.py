"""#330: coverage gaps (FR-PROF-8) and refunded probe calls (Engine ADR-180) in the turn report.

The gap helpers (``console.turn_reports``: ``coverage_gap_lines``, ``stored_gaps``,
``unread_gap_count``, ``GapLine``) word each gap and link it to the Backfill page's gap fill;
``_turn_report.html`` shows them in a "Coverage gaps" row after Warnings, on the Play inspector and
on the operation detail page. A ``probe: true`` model call is marked as a refunded probe, and the
heading counts them so "Model calls" less the probes equals the budget's "used".

The linked case runs under ``tests.urls_with_m7_pages`` (E24); the unlinked complement under this
module's own urlconf, the real one with ``stories:backfill`` filtered out (D19). "Renders as on
main" compares ``tests/turn_report/golden/turn_reports_main.json`` (main's render of every
turn-report fixture, captured before the change) through ``tests.golden.normalise_html``.
"""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, override_settings
from django.urls import URLPattern, URLResolver, include, path, reverse

from console.engine.client import EngineClient
from console.engine.models import CoverageGap, TurnReport
from console.engine_access import engine_client_for
from console.operation_log import turn_report_json
from console.turn_reports import (
    GAP_TEXT,
    GapLine,
    coverage_gap_lines,
    stored_gaps,
    unread_gap_count,
)
from console.views import inspect as inspect_views
from console.views import play
from epistrel_console import urls as real
from tests.fakes.engine import FakeEngine
from tests.fakes.guarantees import answer_guarantees
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.golden import normalise_html
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, structured
from tests.inspect.test_retrieval import RETRIEVE, ask
from tests.inspect.test_retrieval import fixture as retrieval_fixture
from tests.play.helpers import RESPOND, STORY, answer_page, post_turn, respond_json, story_json
from tests.test_quality_everywhere import inspector, log_turn, turn_dd

pytestmark = pytest.mark.django_db

M7_URLS = "tests.urls_with_m7_pages"
WITHOUT_BACKFILL = __name__  # this module's urlpatterns: the real ones less stories:backfill
GOLDEN = Path(__file__).parent / "turn_report" / "golden" / "turn_reports_main.json"
FIXTURES = Path(__file__).parent / "fixtures" / "engine"
GAP_FILL = f"/stories/{STORY}/backfill/#gap-fill"
CAUSE = "(a forward-only upgrade or a backfill not yet run)"
PROBE_TITLE = (
    "A response-format probe the endpoint rejected: sent, decided nothing, refunded to the turn "
    "budget"
)

story_patterns = (
    [p for p in real.story_patterns[0] if getattr(p, "name", None) != "backfill"],
    "stories",
)
urlpatterns: list[URLPattern | URLResolver] = [
    path("stories/", include(story_patterns)) if str(p.pattern) == "stories/" else p
    for p in real.urlpatterns
    if isinstance(p, URLPattern | URLResolver)
]


def gap(capability: Any, start: Any, end: Any) -> dict[str, Any]:
    return {"capability": capability, "from_turn": start, "to_turn": end}


PIPELINE = gap("pipeline", 1, 6)
NESTED = gap("nested_beliefs", 3, 3)


def gaps_of(*entries: dict[str, Any]) -> list[CoverageGap]:
    return stored_gaps({"coverage_gaps": list(entries)})


def render_report(report: TurnReport, **context: Any) -> str:
    return render_to_string("console/_turn_report.html", {"report": report, **context})


def gaps_dd(html: str) -> str:
    return turn_dd(html, "turn-coverage-gaps")


# --- the gap lines -------------------------------------------------------------------------------


def test_gap_lines_text_and_link() -> None:
    with override_settings(ROOT_URLCONF=M7_URLS):
        lines = coverage_gap_lines(gaps_of(PIPELINE, NESTED), STORY)
        report = TurnReport.from_json(respond_json())
        assert report is not None
        html = render_report(report, coverage_gaps=lines)
    assert [str(line.text) for line in lines] == [
        f"<code>pipeline</code> has no data for turns 1\u20136 {CAUSE}",
        f"<code>nested_beliefs</code> has no data for turn 3 {CAUSE}",
    ]
    assert [line.url for line in lines] == [GAP_FILL, GAP_FILL]
    assert '<ul class="coverage-gaps">' in gaps_dd(html)
    assert gaps_dd(html).count(f' — <a class="gap-fill" href="{GAP_FILL}">Fill the gap</a>') == 2
    # after Warnings' place, before Guardrail
    assert html.index("<dt>Coverage gaps</dt>") < html.index("<dt>Guardrail</dt>")


def test_gap_lines_without_the_backfill_page_have_no_link() -> None:
    """**Complement:** ``stories:backfill`` unregistered (this module's urlconf, D19): the url
    is "" and the line is its text alone, no " — " and no link."""
    with override_settings(ROOT_URLCONF=WITHOUT_BACKFILL):
        lines = coverage_gap_lines(gaps_of(PIPELINE), STORY)
        report = TurnReport.from_json(respond_json())
        assert report is not None
        html = render_report(report, coverage_gaps=lines)
    assert [line.url for line in lines] == [""]
    assert "Fill the gap" not in html
    assert gaps_dd(html).count(f"<li>{lines[0].text}</li>") == 1


def test_a_blank_story_id_gives_no_link() -> None:
    assert coverage_gap_lines(gaps_of(PIPELINE), "") == [
        GapLine(coverage_gap_lines(gaps_of(PIPELINE), "")[0].text, "")
    ]


def test_turn_numbers_take_thousands_separators_and_the_capability_is_escaped() -> None:
    (line,) = coverage_gap_lines(gaps_of(gap("<b>x</b>", 1000, 1200)), STORY)
    assert str(line.text) == (
        f"<code>&lt;b&gt;x&lt;/b&gt;</code> has no data for turns 1,000\u20131,200 {CAUSE}"
    )
    assert GAP_TEXT.startswith("{capability} has no data for {turns}")


def test_duplicate_gaps_shown_once() -> None:
    other = gap("consolidation", 1, 6)
    lines = coverage_gap_lines(gaps_of(PIPELINE, other, dict(PIPELINE), NESTED, other), STORY)
    assert [str(line.text).split("</code>")[0] for line in lines] == [
        "<code>pipeline",
        "<code>consolidation",
        "<code>nested_beliefs",
    ]
    # a duplicate is shown once, but it was read: it isn't counted unread
    assert unread_gap_count({"coverage_gaps": [PIPELINE, PIPELINE]}) == 0


# --- leniency ------------------------------------------------------------------------------------


def test_stored_gaps_lenient() -> None:
    answer = {"coverage_gaps": [PIPELINE, {"capability": "pipeline", "from_turn": "1"}]}
    assert stored_gaps(answer) == [CoverageGap("pipeline", 1, 6)]
    assert unread_gap_count(answer) == 1
    report = TurnReport.from_json(respond_json())
    assert report is not None
    html = render_report(
        report,
        coverage_gaps=coverage_gap_lines(stored_gaps(answer), STORY),
        coverage_gaps_unread=unread_gap_count(answer),
    )
    dd = gaps_dd(html)
    assert dd.count("<li>") == 1
    assert '<p class="muted">1 gap entry couldn&#x27;t be read</p>' in dd
    assert dd.index("</ul>") < dd.index('<p class="muted">')


@pytest.mark.parametrize(
    "entry",
    [
        "pipeline",
        None,
        gap("", 1, 2),
        gap("  ", 1, 2),
        gap(7, 1, 2),
        gap("pipeline", True, 2),
        gap("pipeline", 1, False),
        gap("pipeline", 1.0, 2),
        gap("pipeline", 5, 4),
        gap("pipeline", -1, 2),
        {"capability": "pipeline", "from_turn": 1},
    ],
)
def test_a_malformed_entry_is_skipped_and_counted(entry: Any) -> None:
    answer = {"coverage_gaps": [entry, NESTED]}
    assert stored_gaps(answer) == [CoverageGap("nested_beliefs", 3, 3)]
    assert unread_gap_count(answer) == 1


def test_only_unread_entries_still_give_the_row() -> None:
    answer: dict[str, Any] = {"coverage_gaps": ["x", 1]}
    report = TurnReport.from_json(respond_json())
    assert report is not None
    html = render_report(
        report, coverage_gaps=coverage_gap_lines(stored_gaps(answer), STORY), coverage_gaps_unread=2
    )
    assert gaps_dd(html).strip() == '<p class="muted">2 gap entries couldn&#x27;t be read</p>'


def test_a_value_that_isnt_a_list_is_one_unread_entry() -> None:
    answer = {"coverage_gaps": {"capability": "pipeline"}}
    assert stored_gaps(answer) == []
    assert unread_gap_count(answer) == 1


def test_an_absent_field_gives_no_row() -> None:
    """**Complement:** no ``coverage_gaps`` (or an empty list): no lines, nothing unread, no
    row."""
    answers: tuple[object, ...] = ({}, {"coverage_gaps": []}, None, "text")
    for answer in answers:
        assert stored_gaps(answer) == []
        assert unread_gap_count(answer) == 0
    report = TurnReport.from_json(respond_json())
    assert report is not None
    html = render_report(report, coverage_gaps=[], coverage_gaps_unread=0)
    assert "Coverage gaps" not in html
    assert "coverage-gaps" not in html


# --- the Play inspector and the operation detail page ---------------------------------------------


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine behind the Play page and its inspector."""
    fake = FakeEngine()
    answer_relations(fake)
    answer_guarantees(fake)
    answer_effective_config(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(play, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def with_gaps(*entries: Any) -> dict[str, Any]:
    """#292's ``respond_play`` turn with ``coverage_gaps`` added inline."""
    body = copy.deepcopy(respond_json())
    body["coverage_gaps"] = list(entries)
    return body


def reports(client: Client, engine: FakeEngine, body: dict[str, Any]) -> dict[str, str]:
    """The answer's report in the inspector (the logged row), the turn POST (the live answer)
    and the operation detail page."""
    row = log_turn(body)
    answer_page(engine)
    engine.respond("POST", RESPOND, 200, json=body)
    detail = client.get(reverse("operations:detail", args=[row.pk])).content.decode()
    return {"inspector": inspector(client), "turn": post_turn(client), "operation": detail}


def test_inspector_and_operation_detail_show_gaps(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = with_gaps(PIPELINE, NESTED)
    for where, html in reports(operator_client, engine, body).items():
        dd = gaps_dd(html)
        assert f"<code>pipeline</code> has no data for turns 1\u20136 {CAUSE}" in dd, where
        assert f"<code>nested_beliefs</code> has no data for turn 3 {CAUSE}" in dd, where
        assert dd.count(f'<a class="gap-fill" href="{GAP_FILL}">Fill the gap</a>') == 2, where
        assert "couldn" not in dd, where


def test_a_live_answer_counts_its_unread_entry(operator_client: Client, engine: FakeEngine) -> None:
    """The live answer reads the raw entries too: one that the model's lenient parse dropped is
    counted, the same as on the stored row."""
    body = with_gaps(PIPELINE, gap("pipeline", 9, 2))
    for where, html in reports(operator_client, engine, body).items():
        dd = gaps_dd(html)
        assert dd.count("<li>") == 1, where
        assert "1 gap entry couldn&#x27;t be read" in dd, where


def test_no_gaps_no_row_on_the_pages(operator_client: Client, engine: FakeEngine) -> None:
    for where, html in reports(operator_client, engine, with_gaps()).items():
        assert "Coverage gaps" not in html, where


def test_a_refused_turn_row_shows_no_gaps(operator_client: Client) -> None:
    """Only a 2xx answer's top-level gaps are read: a logged 503 whose ``error.details`` carry
    a report (and gaps) gets none on the operation detail page."""
    details = {**respond_json(), "coverage_gaps": [PIPELINE]}
    row = log_turn({"error": {"code": "model_unavailable", "message": "x", "details": details}})
    row.http_status, row.error_code = 503, "model_unavailable"
    row.save()
    html = operator_client.get(reverse("operations:detail", args=[row.pk])).content.decode()
    assert '<section class="turn-report">' in html
    assert "Coverage gaps" not in html


# --- probe calls ---------------------------------------------------------------------------------


def probe_call(**changes: Any) -> dict[str, Any]:
    call = dict(respond_json()["model_calls"][0])
    call.update(outcome="http_error", request_id="probe-1", **changes)
    return call


def report_with(*calls: dict[str, Any], used: int) -> TurnReport:
    body = copy.deepcopy(respond_json())
    body["model_calls"] = list(calls)
    body["budget"]["used"] = used
    report = TurnReport.from_json(body)
    assert report is not None
    return report


def rows(html: str) -> list[str]:
    return re.findall(r"<tr(?: class=\"probe\")?>\s*<td>.*?</tr>", html, re.S)


def test_probe_rows_and_heading() -> None:
    first = respond_json()["model_calls"][0]
    html = render_report(report_with(probe_call(probe=True), first, used=1))
    assert "<h4>Model calls (2) · 1 probe refunded</h4>" in html
    assert "used 1" in html
    probe_row, plain_row = rows(html)
    assert probe_row.startswith('<tr class="probe">')
    assert f'<td title="{PROBE_TITLE}">http_error · probe (refunded)</td>' in probe_row
    assert plain_row.startswith("<tr>")
    assert "<td>ok</td>" in plain_row
    assert "probe" not in plain_row


def test_two_probes_pluralise() -> None:
    first = respond_json()["model_calls"][0]
    html = render_report(
        report_with(probe_call(probe=True), probe_call(probe=True, attempt=2), first, used=1)
    )
    assert "<h4>Model calls (3) · 2 probes refunded</h4>" in html


def test_without_probes_the_heading_is_unchanged() -> None:
    """**Complement:** ``probe: false`` (or absent) is no probe: the plain heading and rows."""
    html = render_report(report_with(probe_call(probe=False), probe_call(), used=2))
    assert "<h4>Model calls (2)</h4>" in html
    assert 'class="probe"' not in html
    assert "refunded" not in html


def test_probe_count_reads_only_a_true_probe() -> None:
    from console.templatetags.console_format import probe_count

    calls = [{"probe": True}, {"probe": "true"}, {"probe": 1}, {}, {"probe": False}]
    assert probe_count(calls) == 1
    assert probe_count(None) == 0


# --- unchanged renders ---------------------------------------------------------------------------


def turn_report_fixtures() -> dict[str, TurnReport]:
    """Every engine fixture whose answer has a turn report (directly, or as an operation's
    ``result.body``), by path under ``tests/fixtures/engine/``."""
    found: dict[str, TurnReport] = {}
    for file in sorted(FIXTURES.glob("**/*.json")):
        data = json.loads(file.read_text(encoding="utf-8"))
        candidates = [data]
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            candidates.append(data["result"].get("body"))
        for candidate in candidates:
            raw = turn_report_json(candidate)
            report = TurnReport.from_json(raw) if raw is not None else None
            if report is not None:
                found[file.relative_to(FIXTURES).as_posix()] = report
                break
    return found


def test_m6_turn_reports_render_unchanged() -> None:
    """**Complement:** every existing turn-report fixture (no gaps, no probes) renders the panel
    as main did, with and without the new context values empty."""
    golden: dict[str, str] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    found = turn_report_fixtures()
    assert sorted(found) == sorted(golden)
    assert len(found) >= 5
    for name, report in found.items():
        assert not any(call.probe for call in report.model_calls), name
        assert normalise_html(render_report(report)) == golden[name], name
        empty = render_report(report, coverage_gaps=(), coverage_gaps_unread=0)
        assert normalise_html(empty) == golden[name], name


def test_retrieval_explainer_not_touched(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Complement:** a ``RetrievalBody`` with ``coverage_gaps`` renders Inspect → Retrieval
    with no ``coverage-gaps`` section: the explainer half is #332's (#332 removes or inverts
    this test when it lands)."""
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(inspect_views, "engine_client_for", client_with_fake)
    body = retrieval_fixture()
    body["coverage_gaps"] = [PIPELINE]
    fake.respond("GET", EFFECTIVE, 200, json=structured())
    fake.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=6))
    fake.respond("GET", RETRIEVE, 200, json=body)
    with override_settings(ENGINE_URL="http://engine.test"):
        html = ask(operator_client)
    assert '<section class="retrieval-resolution">' in html
    assert "coverage-gaps" not in html
    assert "has no data for" not in html

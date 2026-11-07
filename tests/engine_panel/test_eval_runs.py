"""#316: the Engine panel's Eval runs list and a run's page (NFR-QUAL-11, Engine #532; E11, E15,
E18, E22, E23, D17, D19; M4 epic #18's eval-run AC), over #292's ``m7/eval_runs.json``,
``eval_runs_bad_item.json`` and ``eval_run.json`` (one-off variants inline, E16)."""

import copy
import html as markup
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import NoReverseMatch
from django.utils import timezone

from console import certification
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.paging import CURSOR_DROPPED
from console.views import operations, story_pages
from console.views.eval_runs import NO_MORE_RUNS, NO_RUNS, OLD_REPORT, natural_key
from tests import test_eval_reports as reports
from tests.certification_helpers import unregistered_certification_pages
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

LIST = "/engine/evals/"
RUNS = "/admin/evals/runs"
RUN = "/admin/evals/runs/{run_id}"
COMMITTED = "full-4710ae26cb73-2026-11-01"
CERTIFIED = "full-4710ae26cb73-2026-11-05"
DETAIL = f"/engine/evals/{CERTIFIED}/"
FULL_FP = "sha256:4710ae26cb731c016d2b86c747e7d3e40e7e9b0a3cfda00bfe60118230039b4b"
FP_QUERY = "fingerprint=sha256%3A4710ae26cb731c016d2b86c747e7d3e40e7e9b0a3cfda00bfe60118230039b4b"
PASSED_BADGE = '<span class="verdict-badge verdict-pass" title="pass">Pass</span>'
FAILED_BADGE = '<span class="verdict-badge verdict-fail" title="fail">Fail</span>'


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def run(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7("eval_run")
    body.update(changes)
    return body


def listed(*items: dict[str, Any], next_cursor: str | None = None) -> dict[str, Any]:
    return {"items": list(items), "next_cursor": next_cursor}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def unvalidated(engine: FakeEngine, path: str, body: object) -> None:
    """Answer ``path`` with a body the schema wouldn't accept (raw shapes, open enums)."""
    engine.side_effect("GET", path, lambda _r: httpx.Response(200, json=body))


def reads(fake: FakeEngine) -> list[httpx.Request]:
    return [r for r in fake.requests if r.url.path.startswith(RUNS)]


def get(client: Client, url: str, params: dict[str, str] | None = None) -> str:
    response = client.get(url, params or {})
    assert response.status_code == 200
    return response.content.decode()


def body_rows(html: str, table: str = "eval-runs") -> list[str]:
    tbody = re.search(rf'<table class="{table}">.*?<tbody>(.*?)</tbody>', html, re.S)
    assert tbody is not None, table
    return re.findall(r"<tr[^>]*>.*?</tr>", tbody.group(1), re.S)


def cell(row: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row, re.S)
    assert found is not None, cls
    return found.group(1)


def dd(html: str, cls: str) -> str:
    found = re.search(rf'<dd class="{cls}">(.*?)</dd>', html, re.S)
    assert found is not None, cls
    return found.group(1)


def section(html: str, cls: str) -> str:
    found = re.search(rf'<section class="{cls}">(.*?)</section>', html, re.S)
    assert found is not None, cls
    return found.group(1)


def raw_rows(html: str, cls: str) -> list[tuple[str, str]]:
    return re.findall(
        r'<td class="key">(.*?)</td><td class="value">(.*?)</td>', section(html, cls), re.S
    )


def headers(html: str, table: str) -> list[str]:
    found = re.search(rf'<table class="{table}">\s*<thead>(.*?)</thead>', html, re.S)
    assert found is not None, table
    return re.findall(r"<th>(.*?)</th>", found.group(1))


# --- the list -------------------------------------------------------------------------------------


def test_list_one_read_and_columns(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUNS, 200, json=m7("eval_runs"))
    html = get(operator_client, LIST)
    (call,) = reads(engine)
    assert call.method == "GET"
    assert call.url.path == RUNS
    assert parse_qs(call.url.query.decode()) == {"limit": ["50"]}
    assert "<title>Eval runs · Engine</title>" in html
    assert '<a href="/engine/evals/" aria-current="page">Eval runs</a>' in html
    assert headers(html, "eval-runs") == [
        "Run",
        "Date",
        "Source",
        "Preset",
        "Model",
        "Fingerprint",
        "Host",
        "Verdict",
        "Metrics",
        "Sample size",
    ]
    assert '<p class="count">2 eval runs on this page</p>' in html
    committed, certified = body_rows(html)
    assert cell(committed, "run") == (
        f'<a class="eval-run" href="/engine/evals/{COMMITTED}/">{COMMITTED}</a>'
    )
    assert cell(committed, "date") == "2026-11-01"
    assert cell(committed, "source") == '<span title="committed">Committed report</span>'
    assert cell(certified, "source") == '<span title="certification">Certification run</span>'
    assert cell(committed, "preset") == '<span title="full">Full</span>'
    assert cell(committed, "model") == "claude-haiku-5-5"  # model_version null: model alone
    assert cell(committed, "fingerprint") == (
        f'<a class="certifications-link" href="/engine/certifications/?{FP_QUERY}">'
        f'<code class="hash" title="{FULL_FP}">4710ae26cb73</code></a>'
    )
    assert cell(committed, "host") == "Anthropic API"
    assert cell(committed, "verdict") == PASSED_BADGE
    assert cell(certified, "verdict") == FAILED_BADGE
    assert cell(committed, "metrics") == "—"  # metrics: null
    assert cell(certified, "metrics") == "2 metrics · 1 passed"
    assert cell(committed, "sample-size") == "345"


def test_unknown_source_is_humanised(operator_client: Client, engine: FakeEngine) -> None:
    odd = run(source="imported", preset="structured", verdict="insufficient_evidence")
    unvalidated(engine, RUNS, listed(odd))
    (row,) = body_rows(get(operator_client, LIST))
    assert cell(row, "source") == '<span title="imported">Imported</span>'
    assert cell(row, "preset") == '<span title="structured">Structured</span>'
    assert cell(row, "verdict") == (
        '<span class="verdict-badge verdict-other" title="insufficient_evidence">'
        "Insufficient evidence</span>"
    )


def test_null_facts_read_dash(operator_client: Client, engine: FakeEngine) -> None:
    bare = {"run_id": "bare", "source": "committed"}
    engine.respond("GET", RUNS, 200, json=listed(bare, run(verdict="error", metrics=[])))
    bare_row, error_row = body_rows(get(operator_client, LIST))
    for cls in ("date", "preset", "model", "fingerprint", "host", "verdict", "metrics"):
        assert cell(bare_row, cls) == "—", cls
    assert "verdict-badge" not in bare_row  # a null verdict is a plain "—"
    assert cell(error_row, "verdict") == (
        '<span class="verdict-badge verdict-other" title="error">Error</span>'
    )
    assert cell(error_row, "metrics") == "0 metrics · 0 passed"


def test_model_joins_model_and_version(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        RUNS,
        200,
        json=listed(
            run(model="claude-haiku-5-5", model_version="20261101"),
            run(model=None, model_version="20261101"),
        ),
    )
    both, version_only = body_rows(get(operator_client, LIST))
    assert cell(both, "model") == "claude-haiku-5-5 20261101"
    assert cell(version_only, "model") == "20261101"


def test_paging(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUNS, 200, json=listed(run(), next_cursor="page 2#x"))
    html = get(operator_client, LIST, {"stray": "x"})
    nxt = re.search(r'<a class="next-page" href="([^"]+)">Next page</a>', html)
    assert nxt is not None
    assert parse_qs(urlsplit(markup.unescape(nxt.group(1))).query) == {"cursor": ["page 2#x"]}
    assert "First page" not in html  # no cursor was sent
    html = get(operator_client, LIST, {"cursor": "page 2#x"})
    assert parse_qs(reads(engine)[-1].url.query.decode()) == {
        "cursor": ["page 2#x"],
        "limit": ["50"],
    }
    first = re.search(r'<a class="first-page" href="([^"]+)">First page</a>', html)
    assert first is not None
    assert markup.unescape(first.group(1)) == LIST  # "First page" drops the cursor


@pytest.mark.parametrize("cursor", ["x" * 201, "a\x07b"], ids=["overlong", "control"])
def test_a_bad_cursor_is_dropped(operator_client: Client, engine: FakeEngine, cursor: str) -> None:
    engine.respond("GET", RUNS, 200, json=m7("eval_runs"))
    html = get(operator_client, LIST, {"cursor": cursor})
    assert parse_qs(reads(engine)[0].url.query.decode()) == {"limit": ["50"]}
    assert CURSOR_DROPPED in html
    assert "First page" not in html


def test_a_blank_cursor_is_no_cursor(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUNS, 200, json=m7("eval_runs"))
    html = get(operator_client, LIST, {"cursor": "  "})
    assert parse_qs(reads(engine)[0].url.query.decode()) == {"limit": ["50"]}
    assert CURSOR_DROPPED not in html
    # Complement: a 200-character cursor is passed through.
    get(operator_client, LIST, {"cursor": "c" * 200})
    assert parse_qs(reads(engine)[1].url.query.decode())["cursor"] == ["c" * 200]


@pytest.mark.parametrize(
    ("params", "empty"),
    [({}, NO_RUNS), ({"cursor": "abc"}, NO_MORE_RUNS)],
    ids=["first-page", "cursor-page"],
)
def test_empty_texts(
    operator_client: Client, engine: FakeEngine, params: dict[str, str], empty: str
) -> None:
    engine.respond("GET", RUNS, 200, json=listed())
    html = get(operator_client, LIST, params)
    assert f'<p class="empty">{markup.escape(empty)}</p>' in html
    assert '<p class="count">' not in html
    assert ("First page" in html) == bool(params)


def test_bad_item_is_one_row(operator_client: Client, engine: FakeEngine) -> None:
    unvalidated(engine, RUNS, m7("eval_runs_bad_item"))  # not schema-valid
    html = get(operator_client, LIST)
    rows = body_rows(html)
    assert len(rows) == 3
    rejected = rows[2]
    assert 'class="rejected"' in rejected
    assert '<td colspan="10">This eval run couldn&#x27;t be read' in rejected
    assert "<summary>More</summary>" in rejected
    assert "&quot;run_id&quot;: 7" in rejected
    assert '<p class="notice rejected-notice">1 eval run couldn&#x27;t be read</p>' in html
    assert '<p class="count">2 eval runs on this page</p>' in html


def test_every_entry_rejected_shows_only_the_notice_and_raw(
    operator_client: Client, engine: FakeEngine
) -> None:
    unvalidated(engine, RUNS, listed({"run_id": 7}))
    html = get(operator_client, LIST)
    assert '<table class="eval-runs">' not in html
    assert '<details class="rejected-entry">' in html
    assert '<p class="empty">' not in html


def test_list_sample_size_column(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        RUNS,
        200,
        json=listed(run(sample_size=12345), run(sample_size=None), run(sample_size=0)),
    )
    big, null, zero = body_rows(get(operator_client, LIST))
    assert cell(big, "sample-size") == "12,345"
    assert cell(null, "sample-size") == "—"
    assert cell(zero, "sample-size") == "0"


def test_a_failed_list_read_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        RUNS,
        503,
        json={"error": {"code": "database_unavailable", "message": "down", "details": {}}},
    )
    response = operator_client.get(LIST)
    html = response.content.decode()
    assert 'class="engine-error' in html
    assert '<a href="/engine/evals/" aria-current="page">Eval runs</a>' in html


# --- the run page ---------------------------------------------------------------------------------


def test_detail_metrics_table(operator_client: Client, engine: FakeEngine) -> None:
    metrics = [
        *m7("eval_run")["metrics"],
        {
            "id": "NFR-QUAL-3.recall",
            "requirement": None,
            "value": 0.91234,
            "floor": 0.95,
            "sample_size": 1200,
            "verdict": "report_only",
        },
    ]
    engine.respond("GET", RUN, 200, json=run(metrics=metrics))
    html = get(operator_client, DETAIL)
    (call,) = reads(engine)
    assert call.url.path == f"{RUNS}/{CERTIFIED}"
    assert f"<title>Eval run {CERTIFIED} · Engine</title>" in html
    assert f"<h2>Eval run {CERTIFIED}</h2>" in html
    assert '<a href="/engine/evals/" aria-current="page">Eval runs</a>' in html
    assert headers(html, "eval-metrics") == [
        "Metric",
        "Requirement",
        "Value",
        "Floor",
        "Sample size",
        "Verdict",
    ]
    faithfulness, false_support, recall = body_rows(html, "eval-metrics")
    assert cell(faithfulness, "metric") == "NFR-QUAL-3.faithfulness"
    assert cell(faithfulness, "requirement") == "<code>NFR-QUAL-3</code>"
    assert cell(faithfulness, "value") == "0.84"
    assert cell(faithfulness, "floor") == "0.9"
    assert cell(faithfulness, "n") == "150"
    assert cell(faithfulness, "verdict") == FAILED_BADGE
    assert cell(false_support, "value") == "0"
    assert cell(false_support, "floor") == "0.02"
    assert cell(false_support, "verdict") == PASSED_BADGE
    assert cell(recall, "value") == "0.912"  # up to three decimals
    assert cell(recall, "n") == "1,200"
    assert cell(recall, "requirement") == "—"
    assert cell(recall, "verdict") == (
        '<span class="verdict-badge verdict-other" title="report_only">Report only</span>'
    )
    # The header facts, in the AC's order.
    labels = re.findall(r"<dt>(.*?)</dt>", section(html, "eval-run-facts"))
    assert labels == [
        "Date",
        "Source",
        "Preset",
        "Model",
        "Fingerprint",
        "Host",
        "Seeds",
        "Sample size",
        "Verdict",
    ]
    assert dd(html, "date") == "2026-11-05"
    assert dd(html, "source") == '<span title="certification">Certification run</span>'
    assert dd(html, "preset") == '<span title="full">Full</span>'
    assert dd(html, "model") == "claude-haiku-5-5"
    assert dd(html, "fingerprint") == (
        f'<code class="hash">{FULL_FP}</code> · <a class="certifications-link" '
        f'href="/engine/certifications/?{FP_QUERY}">Certifications for this configuration</a>'
    )
    assert dd(html, "host") == "Anthropic API"
    assert dd(html, "seeds") == "1, 2, 3"
    assert dd(html, "sample-size") == "150"
    assert dd(html, "verdict") == FAILED_BADGE
    headings = re.findall(r"<h3>(.*?)</h3>", html)
    assert headings == ["Metrics", "Suites", "Requirements"]
    assert '<a class="back-to-eval-runs" href="/engine/evals/">Back to eval runs</a>' in html


def test_old_report_without_metrics(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUN, 200, json=run(metrics=None))
    html = get(operator_client, DETAIL)
    assert f'<p class="old-report">{markup.escape(OLD_REPORT)}</p>' in html
    assert '<table class="eval-metrics">' not in html
    # Complement: the suites and requirements tables still render.
    assert raw_rows(html, "eval-suites") == [("quality", FAILED_BADGE), ("smoke", PASSED_BADGE)]
    assert raw_rows(html, "eval-requirements") == [("<code>NFR-QUAL-3</code>", FAILED_BADGE)]


def test_no_metrics_reads_none(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUN, 200, json=run(metrics=[], seeds=[], requirements={}))
    html = get(operator_client, DETAIL)
    assert '<p class="empty">None</p>' in section(html, "eval-metrics")
    assert '<p class="empty">None</p>' in section(html, "eval-requirements")
    assert dd(html, "seeds") == "None"  # E23: an empty list reads "None"


def test_raw_dicts(operator_client: Client, engine: FakeEngine) -> None:
    flat = {"NFR-QUAL-11": "pass", "NFR-QUAL-3": "fail", "NFR-QUAL-2": 0.5, "NFR-QUAL-20": 3}
    nested = {"b": {"x": [1, 2]}, "a": "pass", "c": True, "d": None}
    unvalidated(engine, RUN, run(requirements=flat, suites=nested))
    html = get(operator_client, DETAIL)
    assert raw_rows(html, "eval-requirements") == [
        ("<code>NFR-QUAL-2</code>", "0.5"),
        ("<code>NFR-QUAL-3</code>", FAILED_BADGE),
        ("<code>NFR-QUAL-11</code>", PASSED_BADGE),
        ("<code>NFR-QUAL-20</code>", "3"),
    ]
    assert raw_rows(html, "eval-suites") == [
        ("a", PASSED_BADGE),
        ("b", "<code>{&quot;x&quot;:[1,2]}</code>"),
        ("c", "<code>true</code>"),
        ("d", "<code>null</code>"),
    ]
    # A non-dict is kept whole under More; complement: none of these raises.
    unvalidated(engine, RUN, run(suites=["quality", "smoke"], requirements="pass"))
    html = get(operator_client, DETAIL)
    suites = section(html, "eval-suites")
    assert "<table" not in suites
    assert (
        "<summary>More</summary><pre>[\n  &quot;quality&quot;,\n  &quot;smoke&quot;\n]</pre>"
        in (suites)
    )
    assert "<summary>More</summary><pre>&quot;pass&quot;</pre>" in section(
        html, "eval-requirements"
    )


def test_a_long_raw_value_is_cut_with_the_full_text_in_title(
    operator_client: Client, engine: FakeEngine
) -> None:
    unvalidated(engine, RUN, run(suites={"long": {"k": "v" * 200}}))
    ((_, value),) = raw_rows(get(operator_client, DETAIL), "eval-suites")
    assert value.startswith('<code title="{&quot;k&quot;:&quot;vvv')
    assert value.endswith("…</code>")


def test_natural_key() -> None:
    keys = ["NFR-QUAL-11", "NFR-QUAL-3", "a10b2", "a10b10", "a9", "b", "A"]
    assert sorted(keys, key=natural_key) == [
        "A",
        "NFR-QUAL-3",
        "NFR-QUAL-11",
        "a9",
        "a10b2",
        "a10b10",
        "b",
    ]
    assert sorted(["x01", "x1"], key=natural_key) == ["x01", "x1"]  # ties by raw key


def test_run_id_is_quoted(operator_client: Client, engine: FakeEngine) -> None:
    odd = "run one#2"
    engine.respond(
        "GET", RUNS, 200, json=listed(run(run_id=odd), run(run_id="a/b"), run(run_id=" "))
    )
    quoted, slashed, blank = body_rows(get(operator_client, LIST))
    href = re.search(r'href="([^"]+)"', cell(quoted, "run"))
    assert href is not None
    assert href.group(1) == "/engine/evals/run%20one%232/"
    assert cell(slashed, "run") == "a/b"  # can't be reversed: plain text
    assert cell(blank, "run") == "—"
    engine.respond("GET", RUN, 200, json=run(run_id=odd))
    html = get(operator_client, href.group(1))
    call = reads(engine)[-1]
    assert call.url.raw_path == b"/admin/evals/runs/run%20one%232"  # encoded once
    assert f"<title>Eval run {odd} · Engine</title>" in html


@pytest.mark.parametrize(
    "run_id", ["x" * 201, "a%07b", "a%0Ab"], ids=["overlong", "bell", "newline"]
)
def test_overlong_or_control_run_id_404_no_call(
    operator_client: Client, engine: FakeEngine, run_id: str
) -> None:
    response = operator_client.get(f"/engine/evals/{run_id}/")
    assert response.status_code == 404
    assert engine.requests == []
    assert "No such eval run" in response.content.decode()


def test_a_200_character_run_id_is_read(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUN, 200, json=run(run_id="r" * 200))
    get(operator_client, f"/engine/evals/{'r' * 200}/")
    assert reads(engine)[0].url.path == f"{RUNS}/{'r' * 200}"


def test_not_found_text(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        RUN,
        404,
        json={"error": {"code": "eval_run_not_found", "message": "no", "details": {}}},
    )
    response = operator_client.get("/engine/evals/nope/")
    html = response.content.decode()
    assert response.status_code == 404
    assert '<p class="not-found">No such eval run</p>' in html
    assert "<title>Eval run nope · Engine</title>" in html
    assert '<a href="/engine/evals/" aria-current="page">Eval runs</a>' in html
    assert '<a class="back-to-eval-runs" href="/engine/evals/">Back to eval runs</a>' in html


def test_a_failed_detail_read_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        RUN,
        503,
        json={"error": {"code": "database_unavailable", "message": "down", "details": {}}},
    )
    html = operator_client.get(DETAIL).content.decode()
    assert 'class="engine-error' in html
    assert "No such eval run" not in html


def test_fingerprint_link_d19(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine.respond("GET", RUNS, 200, json=m7("eval_runs"))
    engine.respond("GET", RUN, 200, json=run())
    # Linked while engine:certifications resolves (#313 registered it; the M7 urlconf too).
    for urlconf in ("epistrel_console.urls", "tests.urls_with_m7_pages"):
        with override_settings(ROOT_URLCONF=urlconf):
            row = body_rows(get(operator_client, LIST))[0]
            detail = get(operator_client, DETAIL)
        assert 'class="certifications-link"' in cell(row, "fingerprint"), urlconf
        assert "Certifications for this configuration" in dd(detail, "fingerprint"), urlconf
    # Plain text while it doesn't (D19).
    unregistered_certification_pages(monkeypatch)
    row = body_rows(get(operator_client, LIST))[0]
    assert cell(row, "fingerprint") == f'<code class="hash" title="{FULL_FP}">4710ae26cb73</code>'
    assert dd(get(operator_client, DETAIL), "fingerprint") == f'<code class="hash">{FULL_FP}</code>'


def test_a_null_fingerprint_has_no_link(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUN, 200, json=run(fingerprint=None))
    html = get(operator_client, DETAIL)
    assert dd(html, "fingerprint") == "—"
    assert "Certifications for this configuration" not in html


# --- #18's AC, across the list and the run page ---------------------------------------------------


def test_18_ac_across_list_and_detail(operator_client: Client, engine: FakeEngine) -> None:
    page = m7("eval_runs")
    page["items"][1]["model_version"] = "20261101"
    page["items"][1]["sample_size"] = 1500
    engine.respond("GET", RUNS, 200, json=page)
    engine.respond("GET", RUN, 200, json=run(model_version="20261101", sample_size=1500))
    listing = get(operator_client, LIST)
    row = body_rows(listing)[1]
    assert CERTIFIED in row
    assert cell(row, "model") == "claude-haiku-5-5 20261101"
    assert cell(row, "sample-size") == "1,500"
    detail = get(operator_client, DETAIL)
    assert dd(detail, "seeds") == "1, 2, 3"
    assert dd(detail, "model") == "claude-haiku-5-5 20261101"
    assert dd(detail, "sample-size") == "1,500"
    floors = [cell(r, "floor") for r in body_rows(detail, "eval-metrics")]
    assert floors == ["0.9", "0.02"]
    # Complement: nothing is invented, and Seeds/Floor belong to the run page only.
    metrics = [{**m7("eval_run")["metrics"][0], "floor": None}]
    bare = {"seeds": None, "sample_size": None, "metrics": metrics}
    engine.respond("GET", RUNS, 200, json=listed(run(**bare)))
    engine.respond("GET", RUN, 200, json=run(**bare))
    listing = get(operator_client, LIST)
    (row,) = body_rows(listing)
    assert cell(row, "sample-size") == "—"
    assert "Seeds" not in headers(listing, "eval-runs")
    assert "Floor" not in headers(listing, "eval-runs")
    detail = get(operator_client, DETAIL)
    assert dd(detail, "seeds") == "—"
    assert dd(detail, "sample-size") == "—"
    (metric,) = body_rows(detail, "eval-metrics")
    assert cell(metric, "floor") == "—"


# --- D17, writes, and the Eval reports link -------------------------------------------------------


@pytest.mark.parametrize(("url", "route"), [(LIST, RUNS), (DETAIL, RUN)], ids=["list", "detail"])
def test_erasure_pending_storyless(
    operator_client: Client, engine: FakeEngine, url: str, route: str
) -> None:
    engine.respond(
        "GET",
        route,
        503,
        json={"error": {"code": "erasure_pending", "message": "held", "details": {}}},
        headers={"Retry-After": "5"},
    )
    response = operator_client.get(url)
    html = response.content.decode()
    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert '<p class="warning held-message" role="status">' in html
    assert "Delete</a>" not in html
    assert '<a href="/engine/evals/" aria-current="page">Eval runs</a>' in html


def test_pages_never_write(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUNS, 200, json=m7("eval_runs"))
    engine.respond("GET", RUN, 200, json=run())
    get(operator_client, LIST)
    get(operator_client, DETAIL)
    assert operator_client.head(LIST).status_code == 200
    assert operator_client.post(LIST).status_code == 405
    assert operator_client.post(DETAIL).status_code == 405
    assert engine.requests
    assert all(r.method == "GET" for r in engine.requests)


@pytest.fixture
def reports_engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """``tests/test_eval_reports.py``'s Engine for ``operations:evals``."""
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(operations, "engine_client_for", client_with_fake)
    monkeypatch.setattr(timezone, "now", lambda: reports.NOW)
    fake.respond("GET", "/stories", 200, json=reports.STORIES)
    fake.respond("GET", reports.YIELD, 200, json=reports.fixture("eval_yield"))
    fake.respond("GET", reports.SHADOW, 200, json=reports.fixture("eval_shadow_page1"))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def test_eval_reports_page_links_eval_runs(
    operator_client: Client, reports_engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = reports.page(operator_client)
    line = '<p class="eval-runs">Per-run results: <a href="/engine/evals/">Eval runs</a></p>'
    assert line in html
    assert html.index('<p class="scope"><a href="/operations/">← Operations</a></p>') < html.index(
        line
    )
    # Complement: the stale line is gone and the two sections' reads are unchanged.
    assert "aren't served by the Engine yet" not in html
    assert "aren&#x27;t served by the Engine yet" not in html
    assert len(reports.calls(reports_engine, reports.YIELD)) == 1
    assert len(reports.calls(reports_engine, reports.SHADOW)) == 1
    assert not [r for r in reports_engine.requests if r.url.path.startswith(RUNS)]

    # Unregistered (D19): plain text, no link.
    def unregistered(name: str, *args: Any, **kwargs: Any) -> str:
        raise NoReverseMatch(name)

    monkeypatch.setattr(certification, "reverse", unregistered)
    html = reports.page(operator_client)
    assert '<p class="eval-runs">Per-run results: Eval runs</p>' in html

"""The Engine panel's eval runs (#316; NFR-QUAL-11, Engine #532/#233; E11, E15, E18, E22, E23,
D17, D19; M4 epic #18's "List eval runs with floors, sample size, seeds, and model/version").

An eval run is one report the Engine serves from one of two sources: ``committed`` (a report
committed under the Engine's ``docs/evals``) or ``certification`` (a completed certification run's
report, #233). Certifications are recorded from these same reports. Eval runs come from the
Engine's CLI and certification runs; none is started from here (E18).

- **``engine:eval-runs``** (``/engine/evals/``, GET/HEAD): one ``list_eval_runs(cursor=…,
  limit=50)`` read through #296's ``engine_page``, newest first as the Engine sends them. The
  columns are Run (linked through ``eval_run_url``; a ``run_id`` that can't be reversed is plain
  text), Date, Source, Preset, Model ("<model> <model_version>"), Fingerprint (short, linked to
  the Certifications list filtered to it, D19), Host, Verdict, Metrics ("<n> metrics · <passed>
  passed") and Sample size. Seeds and floors are on the run page (they are per run and per
  metric), one click away. E22's string cursor is dropped with ``CURSOR_DROPPED`` only when blank,
  over 200 characters or holding a ``Cc`` control character. A rejected entry is D15's notice plus
  one spanning row.
- **``engine:eval-run``** (``evals/<str:run_id>/``, GET/HEAD): a ``run_id`` over 200 characters
  or holding a ``Cc`` control character is #43's not-found page (404) with no call; otherwise one
  ``get_eval_run`` read (the client quotes the id into the path once). The page shows the header
  facts, the Metrics table (or :data:`OLD_REPORT` for a report from before Engine #532), the
  Suites and Requirements tables (raw, E15: a dict is a key → value table naturally sorted by
  key; any other value is kept whole under More), and "Back to eval runs". 404
  ``eval_run_not_found`` is :data:`NOT_FOUND_TEXT` inside the frame, status 404.

Both pages mark Eval runs current (``console.nav.PAGE_PARENT``), handle a 503 ``erasure_pending``
with D17's storyless rule through the frame, show any other failed read as #43's panel inside the
frame, and write nothing.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_safe

from console.certification import certifications_url, eval_run_url
from console.engine.client import EngineClientError, EngineError
from console.engine.models import EvalMetric, EvalRun, EvalRunPage, rejected_entries
from console.engine_errors import ErrorPanel, log_engine_error
from console.impact import short_fingerprint
from console.paging import CURSOR_DROPPED
from console.raw import CELL_LIMIT, EMPTY, compact_json, more_json, number_text, plural
from console.views.story_pages import EnginePageContext, engine_page
from console.wording import humanised

LIST_TEMPLATE = "console/engine/eval_runs.html"
DETAIL_TEMPLATE = "console/engine/eval_run.html"
NOT_FOUND_PAGE = "console/engine_error_page.html"

EVAL_RUNS_PAGE = 50  # E22: the Engine's default, sent as ``limit``
CURSOR = "cursor"
ID_MAX = 200  # the Engine's ``cursor`` maxLength; a run id is a report path stem
LIST_COLUMNS = 10

# --- wording --------------------------------------------------------------------------------------

NO_RUNS = "No eval runs — the Engine serves committed reports and completed certification runs"
NO_MORE_RUNS = "No more eval runs after this one"
REJECTED_TEXT = "This eval run couldn't be read"
COUNT_SUFFIX = " on this page"
NEXT_PAGE = "Next page"
FIRST_PAGE = "First page"
OLD_REPORT = (
    "This report predates per-metric results (Engine #532) — see the suite and requirement "
    "verdicts below"
)
NOT_FOUND_TEXT = "No such eval run"
MALFORMED_TEXT = "No such eval run — the id in the address isn't an eval run id"
CERTIFICATIONS_TEXT = "Certifications for this configuration"
BACK_TEXT = "Back to eval runs"
NONE_TEXT = "None"

SOURCE_TEXT = {"committed": "Committed report", "certification": "Certification run"}
EVAL_PRESET_TEXT = {"lite": "Lite", "standard": "Standard", "full": "Full", "max": "Max"}
PASSED = "pass"
FAILED = "fail"
VERDICT_TEXT = {PASSED: "Pass", FAILED: "Fail"}
VERDICT_CLASS = {PASSED: "verdict-pass", FAILED: "verdict-fail"}
OTHER_VERDICT_CLASS = "verdict-other"

DIGITS = re.compile(r"([0-9]+)")


# --- shared cells ---------------------------------------------------------------------------------


def _blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def _present(value: str | None) -> str:
    """``value`` when it is a non-blank string, else ""."""
    return value if isinstance(value, str) and value.strip() else ""


def _dash() -> SafeString:
    return format_html("{}", EMPTY)


def enum_html(value: object, known: Mapping[str, str]) -> SafeString:
    """E23: a known value through its text map, an unknown one humanised; the raw value in
    ``title`` either way. Blank or null → "—"."""
    if not isinstance(value, str) or not value.strip():
        return _dash()
    return format_html('<span title="{}">{}</span>', value, known.get(value, humanised(value)))


def verdict_badge(value: object) -> SafeString:
    """A verdict as a badge with text (never colour alone): ``pass`` "Pass", ``fail`` "Fail",
    anything else humanised (``verdict-other``, ``error`` included); a null or blank verdict is a
    plain "—" with no badge."""
    if not isinstance(value, str) or not value.strip():
        return _dash()
    return format_html(
        '<span class="verdict-badge {}" title="{}">{}</span>',
        VERDICT_CLASS.get(value, OTHER_VERDICT_CLASS),
        value,
        VERDICT_TEXT.get(value, humanised(value)),
    )


def model_text(model: str | None, version: str | None) -> str:
    """Whichever of ``model`` and ``model_version`` is present, space-joined; "—" for neither."""
    parts = [p for p in (model, version) if isinstance(p, str) and p.strip()]
    return " ".join(parts) if parts else EMPTY


def _text(value: object) -> str:
    return EMPTY if _blank(value) else str(value)


def _id_problem(value: str) -> bool:
    """E22 / the detail guard: over 200 characters or holding a ``Cc`` control character."""
    return len(value) > ID_MAX or any(unicodedata.category(c) == "Cc" for c in value)


def run_link(run_id: str) -> SafeString:
    """The run id linked to ``engine:eval-run`` (``reverse`` encodes it once); plain text when it
    can't be reversed (a ``/`` in it, or the page unregistered, D19); "—" when blank."""
    if _blank(run_id):
        return _dash()
    url = eval_run_url(run_id)
    if url is None:
        return format_html("{}", run_id)
    return format_html('<a class="eval-run" href="{}">{}</a>', url, run_id)


def natural_key(key: str) -> tuple[tuple[object, ...], str]:
    """E23's natural sort: digit runs compared as numbers ("NFR-QUAL-3" before "NFR-QUAL-11"),
    ties broken by the raw key."""
    parts = DIGITS.split(key)  # text, digits, text, …: the positions always align
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts)), key


def _with_query(path: str, params: Mapping[str, str]) -> str:
    query = urlencode({k: v for k, v in params.items() if v})
    return f"{path}?{query}" if query else path


# --- the list -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalRunRow:
    """One list row, its cells pre-rendered (E23)."""

    run_id: str
    link: SafeString
    date: str
    source: SafeString
    preset: SafeString
    model: str
    fingerprint: str
    fingerprint_short: str
    fingerprint_url: str | None
    host: str
    verdict: SafeString
    metrics: str
    sample_size: str


def metrics_summary(metrics: list[EvalMetric] | None) -> str:
    """ "<n> metrics · <passed> passed" (only ``verdict == "pass"`` counts); "—" for ``null``."""
    if metrics is None:
        return EMPTY
    passed = sum(1 for m in metrics if m.verdict == PASSED)
    return f"{plural(len(metrics), 'metric')} · {passed:,} passed"


def eval_run_row(run: EvalRun) -> EvalRunRow:
    fingerprint = _present(run.fingerprint)
    return EvalRunRow(
        run_id=run.run_id,
        link=run_link(run.run_id),
        date=_text(run.date),
        source=enum_html(run.source, SOURCE_TEXT),
        preset=enum_html(run.preset, EVAL_PRESET_TEXT),
        model=model_text(run.model, run.model_version),
        fingerprint=fingerprint,
        fingerprint_short=short_fingerprint(fingerprint) or EMPTY,
        fingerprint_url=certifications_url(fingerprint=fingerprint) if fingerprint else None,
        host=_text(run.host),
        verdict=verdict_badge(run.verdict),
        metrics=metrics_summary(run.metrics),
        sample_size=number_text(run.sample_size),
    )


def rejected_runs(page: EvalRunPage) -> list[Any]:
    """The page's raw entries that didn't parse (D15), in the Engine's order."""
    unparsed = getattr(page, "unparsed", {}) or {}
    if "items" not in unparsed:
        return []
    return rejected_entries(unparsed["items"], EvalRun.from_json)


def _list_build(ctx: EnginePageContext) -> dict[str, Any]:
    request = ctx.request
    path = request.path
    typed = request.GET.get(CURSOR, "")
    cursor = typed if typed.strip() and not _id_problem(typed) else None
    page = ctx.client.list_eval_runs(cursor=cursor, limit=EVAL_RUNS_PAGE)
    rows = [eval_run_row(r) for r in page.items]
    rejected = rejected_runs(page)
    empty = ""
    if not rows and not rejected:
        empty = NO_MORE_RUNS if cursor else NO_RUNS
    return {
        "cursor_dropped": CURSOR_DROPPED if typed.strip() and cursor is None else None,
        "rows": rows,
        "rejected": rejected,
        "rejected_notice": (
            f"{plural(len(rejected), 'eval run')} couldn't be read" if rejected else ""
        ),
        "rejected_text": REJECTED_TEXT,
        "columns": LIST_COLUMNS,
        "count_text": plural(len(rows), "eval run") + COUNT_SUFFIX if rows else "",
        "empty_text": empty,
        "next_url": _with_query(path, {CURSOR: page.next_cursor}) if page.next_cursor else None,
        "first_url": path if cursor else None,
        "next_text": NEXT_PAGE,
        "first_text": FIRST_PAGE,
    }


@require_safe
def eval_runs(request: HttpRequest) -> HttpResponse:
    """``engine:eval-runs`` (module docstring)."""
    return engine_page(request, LIST_TEMPLATE, _list_build, current="engine:eval-runs")


# --- the run page ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricRow:
    """One metric's cells, in the Engine's order (E23); ``requirement`` is "" when null (the
    template shows "—")."""

    metric: str
    requirement: str
    value: str
    floor: str
    n: str
    verdict: SafeString


@dataclass(frozen=True)
class RawTable:
    """A raw ``suites`` / ``requirements`` value: ``rows`` (key, value cell) naturally sorted for
    a dict (empty → "None"), else ``more`` (the whole value's JSON)."""

    rows: list[tuple[str, SafeString]] | None = None
    more: str = ""


def metric_row(metric: EvalMetric) -> MetricRow:
    return MetricRow(
        metric=_text(metric.id),
        requirement="" if _blank(metric.requirement) else str(metric.requirement),
        value=number_text(metric.value),
        floor=number_text(metric.floor),
        n=number_text(metric.sample_size),
        verdict=verdict_badge(metric.verdict),
    )


def raw_cell(value: object) -> SafeString:
    """A string is a verdict badge (as the metrics'); a number follows E23's number rule; any
    other value (nested, bool, null) is compact JSON in ``<code>``, cut at about 120 characters
    with the full text in ``title``."""
    if isinstance(value, str):
        return verdict_badge(value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return format_html("{}", number_text(value))
    full = compact_json(value)
    shown = compact_json(value, CELL_LIMIT)
    if shown != full:
        return format_html('<code title="{}">{}</code>', full, shown)
    return format_html("<code>{}</code>", full)


def raw_table(value: object) -> RawTable:
    if not isinstance(value, dict):
        return RawTable(more=more_json(value))
    keys = sorted((str(k) for k in value), key=natural_key)
    by_key = {str(k): v for k, v in value.items()}
    return RawTable(rows=[(k, raw_cell(by_key[k])) for k in keys])


def seeds_text(seeds: list[int] | None) -> str:
    """ "1, 2, 3"; "—" for ``null``; "None" for an empty list (E23)."""
    if seeds is None:
        return EMPTY
    if not seeds:
        return NONE_TEXT
    return ", ".join(number_text(s) for s in seeds)


def _detail_context(run: EvalRun) -> dict[str, Any]:
    fingerprint = _present(run.fingerprint)
    return {
        "run": run,
        "date": _text(run.date),
        "source": enum_html(run.source, SOURCE_TEXT),
        "preset": enum_html(run.preset, EVAL_PRESET_TEXT),
        "model": model_text(run.model, run.model_version),
        "fingerprint": fingerprint,
        "certifications_url": (
            certifications_url(fingerprint=fingerprint) if fingerprint else None
        ),
        "certifications_text": CERTIFICATIONS_TEXT,
        "host": _text(run.host),
        "seeds": seeds_text(run.seeds),
        "sample_size": number_text(run.sample_size),
        "verdict": verdict_badge(run.verdict),
        "metrics": [metric_row(m) for m in run.metrics] if run.metrics is not None else None,
        "old_report": OLD_REPORT,
        "suites": raw_table(run.suites),
        "requirements": raw_table(run.requirements),
        "none_text": NONE_TEXT,
        "back_url": reverse("engine:eval-runs"),
        "back_text": BACK_TEXT,
    }


def _is_404(exc: EngineClientError) -> bool:
    return (
        isinstance(exc, EngineError) and exc.http_status == 404 and exc.code == "eval_run_not_found"
    )


@require_safe
def eval_run(request: HttpRequest, run_id: str) -> HttpResponse:
    """``engine:eval-run`` (module docstring)."""
    if _id_problem(run_id):
        context = {"engine_error": ErrorPanel(MALFORMED_TEXT)}
        return render(request, NOT_FOUND_PAGE, context, status=404)
    missing: list[bool] = []

    def build(ctx: EnginePageContext) -> dict[str, Any]:
        try:
            run = ctx.client.get_eval_run(run_id)
        except EngineClientError as exc:
            if not _is_404(exc):
                raise
            log_engine_error(ctx.request, exc)
            missing.append(True)
            return {
                "not_found": NOT_FOUND_TEXT,
                "back_url": reverse("engine:eval-runs"),
                "back_text": BACK_TEXT,
            }
        return _detail_context(run)

    response = engine_page(
        request, DETAIL_TEMPLATE, build, current="engine:eval-run", label=f"Eval run {run_id}"
    )
    if missing and response.status_code == 200:
        response.status_code = 404
    return response

"""The Engine panel's certification pages (#313; FR-PROF-15/16, Engine #233/#234; E7, E11, E13,
E15, E18, E22, E23, D17, D19, C13).

A certification is the Engine's record that an eval run against one configuration fingerprint
passed the floors it lists; failed floors and floors with insufficient evidence are recorded too.
Records are deployment-scoped and append-only: only ``status`` moves (``valid`` → ``stale``), when
a canary run measured a miss or an operator invalidated it (#315).

- **``engine:certifications``** (``/engine/certifications/``, GET/HEAD): one
  ``list_certifications`` read through #296's ``engine_page``. :class:`CertificationFilterForm`
  (a GET form, E22) filters by ``fingerprint`` (trimmed and lower-cased, ``sha256:`` + 64 hex)
  and ``profile_id`` (trimmed and upper-cased, a ULID); a blank value is no filter, and an
  invalid one shows its error and makes no call. No ``limit`` is sent (the Engine's 50,
  :data:`CERTIFICATIONS_PAGE`); a non-ULID ``cursor`` is dropped with ``CURSOR_DROPPED`` and
  the first page read. Rows newest first as the Engine sends them; a rejected entry is D15's
  notice plus one spanning row (E22). A failed read (a 422 after the form passed is schema
  drift) is #43's panel inside the frame.
- **``engine:certification``** (``certifications/<str:certification_id>/``, GET/HEAD): the id is
  trimmed and upper-cased (C13); a non-ULID is #43's not-found page (404) with no call. One
  ``get_certification`` read renders the status block (a stale one says why and when, and one
  made stale by a canary links the run, :data:`CANARY_NOTE` citing ``CANARY_LIST_REQUEST``
  once, E18), what it certifies, the three floors tables (each raw entry typed on its own; one
  that doesn't parse is one row with its JSON under More), the intervention types, the run and
  the recording. "Invalidate…" links ``engine:certification-invalidate`` (#315) only while that
  name resolves (D19) and the status is ``valid``. 404 ``certification_not_found`` is
  :data:`NOT_FOUND_TEXT` inside the frame, status 404.
- **``engine:certification-run``** (``certification-runs/<str:run_id>/``, GET/HEAD,
  ``?partial=1``): E7's ``certification_run`` job page. The id is checked as the certification's
  (a non-ULID is the 404 page with no call, on the partial too). The block is
  ``console/_job_poll.html`` around ``_certification_run_body.html`` through
  ``job_poll_context``, polling ``every 5s`` until ``completed`` or ``failed``. Any terminal read
  (page or partial) settles the request's ``queued`` #44 row found by the run's ``job_id``
  (``settle_job``, idempotent; a canary run has no row, and a null ``job_id`` settles nothing).
  The partial's failure order (E7): 503 ``erasure_pending`` → D17's held fragment in the block
  with no trigger; 404 ``certification_run_not_found`` → :data:`RUN_NOT_FOUND_TEXT` as a 200
  with no trigger; then M3's ``poll_failure``.

**The run's report** is raw (E15). Only the keys the Engine writes are summarised (``verdict``,
``suites``, ``metrics``, ``missed_floors`` and ``outcome`` should it appear); every other key goes
under More. ``CertificationRun`` has no ``outcome`` field at origin/main (the Engine keeps it in a
column): the page shows ``report["outcome"]`` when present, else "Missed" when ``missed_floors``
is non-empty, else no outcome line (it never guesses Held vs Inconclusive).

All three pages mark Certifications current (``console.nav.PAGE_PARENT``) and handle a 503
``erasure_pending`` with D17's storyless rule through the frame.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from django import forms
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.views.decorators.http import require_safe

from console.certification import (
    CANARY_LIST_REQUEST,
    INVALIDATED_REASON_TEXT,
    QualityLine,
    certification_run_url,
    certification_url,
    certifications_url,
)
from console.engine.client import EngineClientError, EngineError
from console.engine.models import (
    Certification,
    CertificationPage,
    CertificationRun,
    FloorResult,
    rejected_entries,
)
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.held import _ledger_url, held_text, is_held
from console.ids import is_ulid
from console.impact import duration_text, short_fingerprint
from console.inspect import short_id
from console.job_poll import POLL_TEMPLATE, poll_context, poll_failure, settle_job
from console.jobs import PARTIAL_PARAM, SLOW_POLL_TRIGGER, job_poll_context, job_state
from console.jobs import settle_error_code as _settle_code
from console.paging import CURSOR_DROPPED
from console.raw import EMPTY, compact_json, more_json, number_text, plural
from console.templatetags.console_format import engine_time
from console.templatetags.inspect_tags import engine_id
from console.templatetags.principals import principal_link
from console.views.profiles import detail_url
from console.views.story_pages import EnginePageContext, engine_page
from console.wording import humanised

LIST_TEMPLATE = "console/engine/certifications.html"
DETAIL_TEMPLATE = "console/engine/certification.html"
RUN_TEMPLATE = "console/engine/certification_run.html"
RUN_BODY_TEMPLATE = "console/engine/_certification_run_body.html"
NOT_FOUND_PAGE = "console/engine_error_page.html"

CERTIFICATIONS_PAGE = 50  # E22: the Engine's default; no ``limit`` is sent
CURSOR = "cursor"
FINGERPRINT = "fingerprint"
PROFILE_ID = "profile_id"
FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

# --- wording --------------------------------------------------------------------------------------

FINGERPRINT_ERROR = "Enter a full fingerprint (sha256:…)"
PROFILE_ID_ERROR = "Enter a profile id"
NO_CERTIFICATIONS = "No certifications"
NO_CERTIFICATIONS_MATCH = "No certifications match these filters"
NO_MORE_CERTIFICATIONS = "No more certifications after this one"
REJECTED_TEXT = "This certification couldn't be read"
COUNT_SUFFIX = " on this page"
FILTERED_SUFFIX = ", matching filters"
NEXT_PAGE = "Next page"
FIRST_PAGE = "First page"
FILTER_LINK = "filter"

VALID = "valid"
STALE = "stale"
CANARY = "canary"
STATUS_TEXT = {VALID: "Valid", STALE: "Stale"}
STATUS_CLASS = {VALID: "quality-certified", STALE: "quality-stale"}
CANARY_NOTE = "The Engine lists canary runs only through the certification they made stale"
CANARY_RUN_LINK = "the canary run"
ENGINE_REQUEST_LINK = "Engine request"
OTHER_CERTIFICATIONS = "Other certifications of this fingerprint"
INVALIDATE_LABEL = "Invalidate…"
NOT_FOUND_TEXT = "No such certification"
MALFORMED_TEXT = "No such certification — the id in the address isn't an Engine id"
FLOOR_REJECTED_TEXT = "This floor couldn't be read"
FLOOR_TABLES = (
    ("floors_passed", "Passed"),
    ("floors_failed", "Failed"),
    ("floors_insufficient", "Insufficient evidence"),
)
NONE_TEXT = "None"

RUN_NOT_FOUND_TEXT = "No such certification run"
RUN_MALFORMED_TEXT = "No such certification run — the id in the address isn't an Engine id"
NO_CERTIFICATION = "No certification was recorded — no floor passed, or the run failed"
KIND_TEXT = {"certification": "Requested certification", CANARY: "Canary check"}
SCHEDULED_TEXT = "Scheduled by the Engine"
VERDICT_TEXT = {"pass": "passed", "fail": "failed", "insufficient_evidence": "insufficient"}
OUTCOME_MISSED = "Missed"
REPORT_KEYS = ("verdict", "suites", "metrics", "missed_floors", "outcome")
ESTIMATE_LABELS = (
    ("model_calls", "Model calls"),
    ("tokens", "Tokens"),
    ("duration_s", "Duration"),
    ("cost", "Cost"),
)


def _reversed(name: str, *args: str) -> str | None:
    try:
        return reverse(name, args=list(args))
    except NoReverseMatch:
        return None


def _enum(value: object, known: Mapping[str, str] | None = None) -> SafeString:
    """E23: a known value through its text map, an unknown one humanised with the raw value in
    ``title``; blank → "—"."""
    if not isinstance(value, str) or not value.strip():
        return format_html("{}", EMPTY)
    if known is not None and value in known:
        return format_html('<span title="{}">{}</span>', value, known[value])
    return format_html('<span title="{}">{}</span>', value, humanised(value))


def _with_query(path: str, params: Mapping[str, str]) -> str:
    query = urlencode({k: v for k, v in params.items() if v})
    return f"{path}?{query}" if query else path


# --- shared: the profile a record names -----------------------------------------------------------


def profile_html(
    profile_id: str | None, profile_version: int | None, preset: str | None
) -> SafeString:
    """The one profile rendering (list cell, detail, run page): the humanised preset (raw in
    ``title``), then the short ``profile_id`` "@v<version>" linked to ``profiles:detail`` at that
    version; no version → no "@v" and no ``?version=``; a preset alone is plain text; neither →
    "—"."""
    parts: list[SafeString] = []
    if isinstance(preset, str) and preset.strip():
        parts.append(_enum(preset))
    if isinstance(profile_id, str) and profile_id:
        version = profile_version if isinstance(profile_version, int) else None
        suffix = f"@v{version}" if version is not None else ""
        parts.append(
            format_html(
                '<a class="profile-link" href="{}">{}{}</a>',
                detail_url(profile_id, version),
                engine_id(profile_id),
                suffix,
            )
        )
    if not parts:
        return format_html("{}", EMPTY)
    return format_html_join(" ", "{}", ((p,) for p in parts))


def _id_link(value: str, url: str | None) -> SafeString:
    shown = engine_id(value)
    return format_html('<a href="{}">{}</a>', url, shown) if url else format_html("{}", shown)


def certification_status(cert: Certification) -> QualityLine:
    """The status badge (rendered through ``_quality.html``): ``valid`` → "Valid"
    (``quality-certified``), ``stale`` → "Stale — <INVALIDATED_REASON_TEXT>" (``quality-stale``;
    plain "Stale" with no reason), anything else humanised with no class."""
    status, reason = cert.status, cert.invalidated_reason
    if status == STALE:
        text = STATUS_TEXT[STALE]
        title = status
        if reason:
            text += f" — {INVALIDATED_REASON_TEXT.get(reason, humanised(reason))}"
            title = f"{status} ({reason})"
        return QualityLine(text, STATUS_CLASS[STALE], title, None, "")
    if status == VALID:
        return QualityLine(STATUS_TEXT[VALID], STATUS_CLASS[VALID], status, None, "")
    return QualityLine(humanised(status) if status else EMPTY, "", status, None, "")


def _length(items: object) -> str:
    return f"{len(items):,}" if isinstance(items, list) else str(items)


# --- the list -------------------------------------------------------------------------------------


class CertificationFilterForm(forms.Form):
    """E22's GET filter: ``fingerprint`` (trimmed, lower-cased, ``sha256:`` + 64 hex) and
    ``profile_id`` (trimmed, upper-cased, a ULID). A blank value is no filter."""

    fingerprint = forms.CharField(required=False, label="Fingerprint", strip=True)
    profile_id = forms.CharField(required=False, label="Profile id", strip=True)

    def clean_fingerprint(self) -> str:
        value = str(self.cleaned_data.get("fingerprint") or "").strip().lower()
        if value and not FINGERPRINT_PATTERN.match(value):
            raise forms.ValidationError(FINGERPRINT_ERROR)
        return value

    def clean_profile_id(self) -> str:
        value = str(self.cleaned_data.get("profile_id") or "").strip().upper()
        if value and not is_ulid(value):
            raise forms.ValidationError(PROFILE_ID_ERROR)
        return value

    def filters(self) -> dict[str, str]:
        """The cleaned, non-blank filters (call after ``is_valid()``)."""
        return {
            k: self.cleaned_data[k] for k in (FINGERPRINT, PROFILE_ID) if self.cleaned_data.get(k)
        }


@dataclass(frozen=True)
class CertificationRow:
    """One list row, its cells pre-rendered (E23)."""

    id: str
    link: SafeString
    status: QualityLine
    profile: SafeString
    fingerprint: str
    fingerprint_short: str
    fingerprint_url: str
    floors: str
    tier: SafeString
    recorded: SafeString


def certification_row(
    cert: Certification, path: str, filters: Mapping[str, str]
) -> CertificationRow:
    """``cert`` as a list row; its fingerprint "filter" link keeps ``filters``' ``profile_id``,
    sets ``fingerprint`` and drops the cursor."""
    floors = (
        f"{_length(cert.floors_passed)} passed · {_length(cert.floors_failed)} failed · "
        f"{_length(cert.floors_insufficient)} insufficient"
    )
    recorded = format_html("{} · {}", _enum(cert.recorded_via), engine_time(cert.recorded_at))
    return CertificationRow(
        id=cert.id,
        link=_id_link(cert.id, certification_url(cert.id)),
        status=certification_status(cert),
        profile=profile_html(cert.profile_id, cert.profile_version, cert.preset),
        fingerprint=cert.fingerprint,
        fingerprint_short=short_fingerprint(cert.fingerprint) or EMPTY,
        fingerprint_url=_with_query(
            path, {PROFILE_ID: filters.get(PROFILE_ID, ""), FINGERPRINT: cert.fingerprint}
        ),
        floors=floors,
        tier=_enum(cert.model_tier),
        recorded=recorded,
    )


def rejected_certifications(page: CertificationPage) -> list[Any]:
    """The page's raw entries that didn't parse (D15), in the Engine's order."""
    unparsed = getattr(page, "unparsed", {}) or {}
    if "items" not in unparsed:
        return []
    return rejected_entries(unparsed["items"], Certification.from_json)


def _list_build(ctx: EnginePageContext) -> dict[str, Any]:
    request = ctx.request
    path = request.path
    form = CertificationFilterForm(request.GET)
    typed_cursor = request.GET.get(CURSOR, "").strip()
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    context: dict[str, Any] = {
        "filter_form": form,
        "filter_url": path,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "list_read": False,
    }
    if not form.is_valid():
        return context
    filters = form.filters()
    context["clear_url"] = path if filters else None
    try:
        page = ctx.client.list_certifications(
            fingerprint=filters.get(FINGERPRINT),
            profile_id=filters.get(PROFILE_ID),
            cursor=cursor,
        )
    except EngineClientError as exc:
        if is_held(exc):
            raise
        log_engine_error(request, exc)
        context["list_error"] = panel_for(exc)
        return context
    rows = [certification_row(c, path, filters) for c in page.items]
    rejected = rejected_certifications(page)
    count = ""
    if rows:
        count = plural(len(rows), "certification") + COUNT_SUFFIX
        count += FILTERED_SUFFIX if filters else ""
    empty = ""
    if not rows and not rejected:
        if cursor:
            empty = NO_MORE_CERTIFICATIONS
        else:
            empty = NO_CERTIFICATIONS_MATCH if filters else NO_CERTIFICATIONS
    context.update(
        list_read=True,
        rows=rows,
        rejected=rejected,
        rejected_notice=(
            f"{plural(len(rejected), 'certification')} couldn't be read" if rejected else ""
        ),
        rejected_text=REJECTED_TEXT,
        count_text=count,
        empty_text=empty,
        next_url=(
            _with_query(path, {**filters, CURSOR: page.next_cursor}) if page.next_cursor else None
        ),
        first_url=_with_query(path, filters) if cursor else None,
        next_text=NEXT_PAGE,
        first_text=FIRST_PAGE,
        filter_link=FILTER_LINK,
    )
    return context


@require_safe
def certifications(request: HttpRequest) -> HttpResponse:
    """``engine:certifications`` (module docstring)."""
    return engine_page(request, LIST_TEMPLATE, _list_build, current="engine:certifications")


# --- the detail -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class FloorRow:
    """One floor's cells (E23), or ``more`` (the raw entry's JSON) for one that didn't parse."""

    metric: str = EMPTY
    rule: SafeString = field(default_factory=lambda: format_html("{}", EMPTY))
    floor: str = EMPTY
    mean_sd: str = EMPTY
    n: str = EMPTY
    seeds: str = EMPTY
    upper_bound: str = EMPTY
    intervention: SafeString = field(default_factory=lambda: format_html("{}", EMPTY))
    more: str = ""


@dataclass(frozen=True)
class FloorTable:
    key: str
    heading: str
    count: str
    rows: list[FloorRow]


def mean_sd_text(mean: float | None, sd: float | None) -> str:
    """ "0.91 ± 0.02"; the mean alone without ``sd``; "—" without a mean."""
    if mean is None or isinstance(mean, bool):
        return EMPTY
    shown = number_text(mean)
    if sd is None or isinstance(sd, bool):
        return shown
    return f"{shown} ± {number_text(sd)}"


def floor_row(entry: object) -> FloorRow:
    """One raw floor entry, typed on its own (an entry that doesn't parse keeps its JSON)."""
    floor = FloorResult.from_json(entry)
    if floor is None:
        return FloorRow(more=more_json(entry))
    return FloorRow(
        metric=floor.metric_id,
        rule=_enum(floor.rule),
        floor=number_text(floor.floor),
        mean_sd=mean_sd_text(floor.mean, floor.sd),
        n=number_text(floor.n),
        seeds=number_text(floor.seeds),
        upper_bound=number_text(floor.upper_bound_95),
        intervention=_enum(floor.intervention_type),
    )


def floor_tables(cert: Certification) -> list[FloorTable]:
    """Passed, Failed, Insufficient evidence, in that order, each entry in the table of the list
    it came from (its own ``verdict`` isn't re-read); the count is the raw list length."""
    tables = []
    for key, heading in FLOOR_TABLES:
        raw = getattr(cert, key)
        entries = raw if isinstance(raw, list) else [raw]
        tables.append(FloorTable(key, heading, _length(raw), [floor_row(e) for e in entries]))
    return tables


def _names(values: object) -> SafeString:
    """An intervention-type list, humanised (raw in ``title``), ", "-joined; empty → "None"."""
    if not isinstance(values, list) or not values:
        return format_html("{}", NONE_TEXT if isinstance(values, list) else compact_json(values))
    return format_html_join(", ", "{}", ((_enum(v),) for v in values))


def _value_html(value: object) -> SafeString:
    """A ``sample_sizes`` / ``host`` value: a number through E23, a string as is, anything
    nested as JSON in ``<pre>``."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return format_html("{}", number_text(value))
    if isinstance(value, str):
        return format_html("{}", value or EMPTY)
    if value is None:
        return format_html("{}", EMPTY)
    return format_html("<pre>{}</pre>", more_json(value))


def _pairs(raw: object) -> list[tuple[str, SafeString]] | None:
    """A raw mapping's ``(key, value)`` pairs in the Engine's order; ``None`` for a non-dict."""
    if not isinstance(raw, dict):
        return None
    return [(str(k), _value_html(v)) for k, v in raw.items()]


def model_lines(models: object) -> list[SafeString] | None:
    """One line per purpose, sorted by key: "purpose → id" plus " (<reported_version>)" when set;
    a non-dict value is shown as text. ``None`` for a non-dict ``models`` (shown as JSON)."""
    if not isinstance(models, dict):
        return None
    lines = []
    for purpose in sorted(models, key=str):
        value = models[purpose]
        if isinstance(value, dict):
            model_id = value.get("id")
            version = value.get("reported_version")
            shown = format_html(
                "<code>{}</code>", model_id if isinstance(model_id, str) and model_id else EMPTY
            )
            if version not in (None, ""):
                shown = format_html("{} ({})", shown, version)
        else:
            shown = format_html("<code>{}</code>", compact_json(value))
        lines.append(format_html("{} → {}", purpose, shown))
    return lines


@dataclass(frozen=True)
class StatusBlock:
    """The detail page's status line: ``kind`` ``valid`` | ``stale`` | ``other``, the time
    ("" when none), the reason text, the canary run's link and whether the note shows."""

    kind: str
    text: str
    raw: str
    time: str | None
    reason_text: str
    canary_run_url: str | None
    canary_note: bool


def status_block(cert: Certification) -> StatusBlock:
    status = cert.status
    if status == VALID:
        return StatusBlock(VALID, STATUS_TEXT[VALID], status, cert.run_at or None, "", None, False)
    if status == STALE:
        reason = cert.invalidated_reason
        reason_text = INVALIDATED_REASON_TEXT.get(reason, humanised(reason)) if reason else ""
        canary = reason == CANARY
        run_url = certification_run_url(cert.invalidated_by_run_id) if canary else None
        return StatusBlock(
            STALE,
            STATUS_TEXT[STALE],
            status,
            cert.invalidated_at or None,
            reason_text,
            run_url,
            canary,
        )
    return StatusBlock(
        "other", humanised(status) if status else EMPTY, status, None, "", None, False
    )


def invalidate_url(cert: Certification, next_url: str) -> str | None:
    """#315's confirm page with its display-only query, while ``engine:certification-invalidate``
    resolves (D19) and the certification is ``valid``."""
    if cert.status != VALID:
        return None
    path = _reversed("engine:certification-invalidate", cert.id)
    if path is None:
        return None
    profile = humanised(cert.preset) if cert.preset else (cert.profile_id or "")
    return _with_query(path, {FINGERPRINT: cert.fingerprint, "profile": profile, "next": next_url})


def _detail_context(request: HttpRequest, cert: Certification) -> dict[str, Any]:
    return {
        "certification": cert,
        "status": status_block(cert),
        "canary_note": CANARY_NOTE,
        "canary_run_link": CANARY_RUN_LINK,
        "canary_list_request": CANARY_LIST_REQUEST,
        "engine_request_link": ENGINE_REQUEST_LINK,
        "other_certifications_url": certifications_url(fingerprint=cert.fingerprint),
        "other_certifications_text": OTHER_CERTIFICATIONS,
        "profile": profile_html(cert.profile_id, cert.profile_version, cert.preset),
        "preset": _enum(cert.preset),
        "models": model_lines(cert.models),
        "models_more": more_json(cert.models),
        "floor_tables": floor_tables(cert),
        "floor_rejected_text": FLOOR_REJECTED_TEXT,
        "none_text": NONE_TEXT,
        "covered": _names(cert.intervention_types_covered),
        "lacking": _names(cert.intervention_types_insufficient),
        "judge_calibrated": "Yes" if cert.judge_calibrated else "No",
        "seed_count": number_text(cert.seed_count),
        "sample_sizes": _pairs(cert.sample_sizes),
        "sample_sizes_more": more_json(cert.sample_sizes),
        "model_tier": _enum(cert.model_tier),
        "host": _pairs(cert.host),
        "host_more": more_json(cert.host),
        "recorded_via": format_html("{}", cert.recorded_via or EMPTY),
        "invalidate_url": invalidate_url(cert, request.path),
        "invalidate_label": INVALIDATE_LABEL,
    }


def _not_found_page(request: HttpRequest, text: str) -> HttpResponse:
    """#43's not-found page (404) for an id that isn't an Engine id: no Engine call."""
    return render(request, NOT_FOUND_PAGE, {"engine_error": ErrorPanel(text)}, status=404)


def _is_404(exc: EngineClientError, code: str) -> bool:
    return isinstance(exc, EngineError) and exc.http_status == 404 and exc.code == code


def _with_status(response: HttpResponse, flag: list[bool]) -> HttpResponse:
    if flag and response.status_code == 200:
        response.status_code = 404
    return response


@require_safe
def certification(request: HttpRequest, certification_id: str) -> HttpResponse:
    """``engine:certification`` (module docstring)."""
    cid = certification_id.strip().upper()
    if not is_ulid(cid):
        return _not_found_page(request, MALFORMED_TEXT)
    missing: list[bool] = []

    def build(ctx: EnginePageContext) -> dict[str, Any]:
        try:
            cert = ctx.client.get_certification(cid)
        except EngineClientError as exc:
            if not _is_404(exc, "certification_not_found"):
                raise
            log_engine_error(ctx.request, exc)
            missing.append(True)
            return {"not_found": NOT_FOUND_TEXT, "certification_id": cid}
        return _detail_context(ctx.request, cert)

    response = engine_page(
        request,
        DETAIL_TEMPLATE,
        build,
        current="engine:certification",
        label=f"Certification {short_id(cid)}",
    )
    return _with_status(response, missing)


# --- the run page ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricRow:
    """One ``report.metrics`` row (E23 cells), ``missed`` when ``missed_floors`` names it; a
    malformed entry has ``more``."""

    metric: str = EMPTY
    requirement: str = EMPTY
    value: str = EMPTY
    floor: str = EMPTY
    n: str = EMPTY
    verdict: SafeString = field(default_factory=lambda: format_html("{}", EMPTY))
    missed: bool = False
    more: str = ""


@dataclass(frozen=True)
class ReportSummary:
    """The run's ``report`` summarised by the keys the Engine writes; ``more`` is every other
    key's JSON ("" when none), ``raw_more`` a non-dict report's JSON."""

    verdict: SafeString | None = None
    suites: list[tuple[str, SafeString]] | None = None
    metrics: list[MetricRow] | None = None
    missed: list[str] = field(default_factory=list)
    outcome: str = ""
    outcome_raw: str = ""
    more: str = ""
    raw_more: str = ""


def verdict_html(value: object) -> SafeString:
    """``pass`` → "passed", ``fail`` → "failed", ``insufficient_evidence`` → "insufficient",
    anything else humanised (raw in ``title``)."""
    return _enum(value, VERDICT_TEXT)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value or EMPTY
    return EMPTY if value is None else compact_json(value)


def metric_row(entry: object, missed: Sequence[str]) -> MetricRow:
    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
        return MetricRow(more=more_json(entry))
    metric_id = entry["id"]
    return MetricRow(
        metric=metric_id,
        requirement=_text(entry.get("requirement")),
        value=number_text(entry.get("value")),
        floor=number_text(entry.get("floor")),
        n=number_text(entry.get("sample_size")),
        verdict=verdict_html(entry.get("verdict")),
        missed=metric_id in missed,
    )


def report_summary(report: object) -> ReportSummary | None:
    """``None`` for a null report; a non-dict one goes to More whole."""
    if report is None:
        return None
    if not isinstance(report, dict):
        return ReportSummary(raw_more=more_json(report))
    raw_missed = report.get("missed_floors")
    missed = [str(m) for m in raw_missed] if isinstance(raw_missed, list) else []
    suites = report.get("suites")
    metrics = report.get("metrics")
    outcome = report.get("outcome")
    outcome_text = ""
    if isinstance(outcome, str) and outcome.strip():
        outcome_text = humanised(outcome)
    elif missed:
        outcome_text = OUTCOME_MISSED
    rest = {k: v for k, v in report.items() if k not in REPORT_KEYS}
    return ReportSummary(
        verdict=verdict_html(report["verdict"]) if report.get("verdict") is not None else None,
        suites=(
            [(str(k), verdict_html(v)) for k, v in suites.items()]
            if isinstance(suites, dict)
            else None
        ),
        metrics=[metric_row(m, missed) for m in metrics] if isinstance(metrics, list) else None,
        missed=missed,
        outcome=outcome_text,
        outcome_raw=outcome if isinstance(outcome, str) else "",
        more=more_json(rest) if rest else "",
    )


@dataclass(frozen=True)
class EstimateLine:
    label: str
    value: str


def estimate_lines(estimate: object) -> tuple[list[EstimateLine] | None, str]:
    """The estimate's known lines (``None`` → "Estimate: —") and the other keys' JSON."""
    if not isinstance(estimate, dict):
        return None, more_json(estimate) if estimate not in (None, {}) else ""
    lines = []
    for key, label in ESTIMATE_LABELS:
        if key not in estimate:
            continue
        value = estimate[key]
        if key == "duration_s":
            numeric = isinstance(value, int | float) and not isinstance(value, bool)
            shown = duration_text(float(value)) if numeric else EMPTY
        elif key == "cost":
            number = number_text(value)
            shown = f"USD {number}" if number != EMPTY else EMPTY
        else:
            shown = number_text(value)
        lines.append(EstimateLine(label, shown))
    known = {k for k, _ in ESTIMATE_LABELS}
    rest = {k: v for k, v in estimate.items() if k not in known}
    return lines, more_json(rest) if rest else ""


def requested_line(run: CertificationRun) -> SafeString:
    when = engine_time(run.created_at)
    if run.kind == CANARY:
        return format_html("{} · {}", SCHEDULED_TEXT, when)
    return format_html("Requested by {} · {}", principal_link(run.requested_by or None), when)


def _run_view(run: CertificationRun) -> dict[str, Any]:
    state = job_state("certification_run", run.status)
    estimate, estimate_more = estimate_lines(run.estimate)
    story_url = _reversed("inspect:overview", run.story_id) if run.story_id else None
    recorded: SafeString | None = None
    if state.terminal and run.kind != CANARY:
        if run.certification_id:
            link = _id_link(run.certification_id, certification_url(run.certification_id))
            recorded = format_html("Recorded certification {}", link)
        else:
            recorded = format_html("{}", NO_CERTIFICATION)
    suites = run.suites if isinstance(run.suites, list) else []
    sample = run.sample
    return {
        "run": run,
        "kind": _enum(run.kind, KIND_TEXT),
        "fingerprint": run.fingerprint,
        "profile": profile_html(run.profile_id, run.profile_version, None),
        "story_link": (
            format_html('<a href="{}">{}</a>', story_url, engine_id(run.story_id))
            if story_url and run.story_id
            else (engine_id(run.story_id) if run.story_id else None)
        ),
        "suites": (
            format_html_join(", ", "<code>{}</code>", ((s,) for s in suites))
            if suites
            else format_html("{}", NONE_TEXT)
        ),
        "seeds": number_text(run.seeds),
        "sample": (f"{number_text(sample)} per suite" if isinstance(sample, int) else "every case"),
        "estimate": estimate,
        "estimate_more": estimate_more,
        "requested": requested_line(run),
        "report": report_summary(run.report) if state.succeeded else None,
        "recorded": recorded,
        "error": run.error,
        "facts": (),
    }


def _run_url(run_id: str) -> str:
    return reverse("engine:certification-run", args=[run_id])


def _settle(run: CertificationRun) -> None:
    """E7: any terminal read settles the request's ``queued`` #44 row (idempotent)."""
    state = job_state("certification_run", run.status)
    if state.terminal and run.job_id:
        settle_job(run.job_id, failed=state.failed, error_code=_settle_code(run.error))


def _block(run_id: str, run: CertificationRun) -> dict[str, Any]:
    base = _run_url(run_id)
    return {
        **job_poll_context(
            "certification_run",
            run.id,
            run.status,
            poll_url=f"{base}?{PARTIAL_PARAM}=1",
            refresh_url=base,
            body_template=RUN_BODY_TEMPLATE,
        ),
        **_run_view(run),
    }


def _empty_block(run_id: str, *, polling: bool, retrying: bool = False) -> dict[str, Any]:
    base = _run_url(run_id)
    return {
        **poll_context(
            poll_url=f"{base}?{PARTIAL_PARAM}=1",
            refresh_url=base,
            body_template=RUN_BODY_TEMPLATE,
            polling=polling,
            retrying=retrying,
            trigger=SLOW_POLL_TRIGGER,
        ),
        "run": None,
    }


def _partial(request: HttpRequest, run_id: str) -> HttpResponse:
    try:
        run = engine_client_for(request).get_certification_run(run_id)
    except EngineClientError as exc:
        if is_held(exc):
            log_engine_error(request, exc)
            held = {
                "held_text": held_text(exc.retry_after),
                "retry_url": _run_url(run_id),
                "ledger_url": _ledger_url(),
                "delete_url": "",
            }
            return render(
                request,
                POLL_TEMPLATE,
                {**_empty_block(run_id, polling=False), **held, "held": True},
            )
        if _is_404(exc, "certification_run_not_found"):
            log_engine_error(request, exc)
            context = {**_empty_block(run_id, polling=False), "not_found": RUN_NOT_FOUND_TEXT}
            return render(request, POLL_TEMPLATE, context)
        failure = poll_failure(request, exc)
        if failure.retry:
            return render(request, POLL_TEMPLATE, _empty_block(run_id, polling=True, retrying=True))
        context = {**_empty_block(run_id, polling=False), "poll_panel": failure.panel}
        return render(request, POLL_TEMPLATE, context)
    _settle(run)
    return render(request, POLL_TEMPLATE, _block(run_id, run))


@require_safe
def certification_run(request: HttpRequest, run_id: str) -> HttpResponse:
    """``engine:certification-run`` (module docstring)."""
    rid = run_id.strip().upper()
    if not is_ulid(rid):
        return _not_found_page(request, RUN_MALFORMED_TEXT)
    if request.GET.get(PARTIAL_PARAM) == "1":
        return _partial(request, rid)
    missing: list[bool] = []

    def build(ctx: EnginePageContext) -> dict[str, Any]:
        try:
            run = ctx.client.get_certification_run(rid)
        except EngineClientError as exc:
            if not _is_404(exc, "certification_run_not_found"):
                raise
            log_engine_error(ctx.request, exc)
            missing.append(True)
            return {"run_not_found": RUN_NOT_FOUND_TEXT, "run_id": rid}
        _settle(run)
        return {**_block(rid, run), "run_id": rid}

    response = engine_page(
        request,
        RUN_TEMPLATE,
        build,
        current="engine:certification-run",
        label=f"Certification run {short_id(rid)}",
    )
    return _with_status(response, missing)

"""The operation log (#44): every Engine operation this Console performed, with filters, and
one operation's full record with the Engine's current view of it on request. Also the eval
reports page (#149), under Operations."""

from dataclasses import dataclass
from typing import Any

from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import OperationRecord, StorySummary
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.evals import (
    BLANK_HINT,
    EVAL_RUNS_ISSUE,
    NO_SAMPLES,
    SAMPLE_LIMIT,
    SHAPE_TITLE,
    STORIES_UNAVAILABLE,
    ShadowSection,
    YieldSection,
    default_now,
    paging,
    sample_rows,
    shadow_section,
    yield_section,
)
from console.models import EngineOperation
from console.operation_log import (
    SANITIZE_KIND,
    input_violations,
    is_turn,
    output_violations,
    policy_ids,
    queued_job_id,
    queued_job_text,
)
from console.reactivation import OP_NAMES, OP_PARAM
from console.redaction import redact
from console.story_pause import check_url
from console.turn_reports import input_gate_text, stored_turn_report, violation_rows

PAGE_SIZE = 50
EMPTY = "—"


@dataclass(frozen=True)
class Filters:
    story: str
    kind: str
    outcome: str
    operator: str
    policy: str
    guardrail: bool
    principal: str = ""  # #64: the principal the call was made as
    entity_type: str = ""  # #64: with or without entity_id
    entity_id: str = ""

    @classmethod
    def from_request(cls, request: HttpRequest) -> Filters:
        params = request.GET
        outcome = params.get("outcome", "")
        return cls(
            story=params.get("story", ""),
            kind=params.get("kind", ""),
            outcome=outcome if outcome in EngineOperation.Outcome.values else "",
            operator=params.get("operator", ""),
            policy=params.get("policy", ""),
            guardrail=params.get("guardrail") == "1",
            principal=params.get("principal", ""),
            entity_type=params.get("entity_type", ""),
            entity_id=params.get("entity_id", "").strip(),
        )

    def apply(self, rows: QuerySet[EngineOperation]) -> QuerySet[EngineOperation]:
        if self.story:
            rows = rows.filter(story_id=self.story)
        if self.kind:
            rows = rows.filter(kind=self.kind)
        if self.outcome:
            rows = rows.filter(outcome=self.outcome)
        if self.operator:
            rows = rows.filter(operator_username=self.operator)
        if self.principal:
            rows = rows.filter(principal=self.principal)
        if self.entity_type:
            rows = rows.filter(entity_type=self.entity_type)
        if self.entity_id:
            rows = rows.filter(entity_id=self.entity_id)
        if self.guardrail:
            rows = rows.filter(is_guardrail_rejection=True)
        if self.policy:
            # Violations live in the stored JSON: matched here, over the guardrail rows only.
            guarded = rows.filter(is_guardrail_rejection=True).values_list("pk", "response")
            rows = rows.filter(
                pk__in=[pk for pk, resp in guarded if self.policy in policy_ids(resp)]
            )
        return rows


def _distinct(field: str) -> list[str]:
    values = EngineOperation.objects.exclude(**{field: ""}).values_list(field, flat=True)
    return sorted({value for value in values if value is not None})


def _seen_policies() -> list[str]:
    responses = EngineOperation.objects.filter(is_guardrail_rejection=True).values_list(
        "response", flat=True
    )
    return sorted({pid for response in responses for pid in policy_ids(response)})


@require_safe
def operation_list(request: HttpRequest) -> HttpResponse:
    filters = Filters.from_request(request)
    rows = filters.apply(EngineOperation.objects.all())
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    context = {
        "page": page,
        "filters": filters,
        "stories": _distinct("story_id"),
        "kinds": _distinct("kind"),
        "operators": _distinct("operator_username"),
        "principals": _distinct("principal"),
        "entity_types": _distinct("entity_type"),
        "outcomes": EngineOperation.Outcome.values,
        "policies": _seen_policies(),
    }
    return render(request, "console/operations.html", context)


def _viewpoint(row: EngineOperation) -> str | None:
    body = row.request.get("body") if isinstance(row.request, dict) else None
    viewpoint = body.get("output_viewpoint") if isinstance(body, dict) else None
    return viewpoint if isinstance(viewpoint, str) and viewpoint else None


def _queued_job_text(row: EngineOperation) -> str:
    """A queued rebuild's (#50) or consolidation's (#145) honest job message; "" for anything
    else."""
    job_id = queued_job_id(row)
    return queued_job_text(row.kind, job_id) if job_id is not None else ""


CHECK_KINDS = frozenset({"set_story_content_rating", *OP_NAMES})  # #80, #81


def _check_url(row: EngineOperation) -> str:
    """The rating or reactivation check a story row's 202 started (#80, #81), by its
    ``job_id``; "" for anything else. A reactivation carries ``?op=``."""
    if not row.job_id or row.entity_type != "story" or not row.entity_id:
        return ""
    if row.kind not in CHECK_KINDS:
        return ""
    params = {OP_PARAM: row.kind} if row.kind in OP_NAMES else {}
    return check_url(row.entity_id, row.job_id, **params)


# Kinds whose Engine record would show what the row itself never keeps (#144: the Engine's
# ``sanitized`` event holds the surrogate, and the memory read back holds its text).
NO_FETCH_KINDS = frozenset({SANITIZE_KIND})


def trace_url(row: EngineOperation) -> str:
    """#139: the lazy "Eval hooks & read set" block's URL for a turn row with an Engine
    operation id and a story; "" for any other row (or while the route isn't registered)."""
    if not (is_turn(row.response) and row.engine_operation_id and row.story_id):
        return ""
    try:
        return reverse("inspect:turn-trace", args=[row.story_id, row.engine_operation_id])
    except NoReverseMatch:
        return ""


def fetch_offered(row: EngineOperation) -> bool:
    """Whether the detail page offers the ``?fetch=1`` re-read of the Engine's record."""
    return row.kind not in NO_FETCH_KINDS


@require_safe
def operation_detail(request: HttpRequest, pk: int) -> HttpResponse:
    row = get_object_or_404(EngineOperation, pk=pk)
    context: dict[str, Any] = {
        "row": row,
        "is_turn": is_turn(row.response),
        "report": stored_turn_report(row.response),
        "input_violations": violation_rows(input_violations(row.response)),
        "input_gate": input_gate_text(row.response),
        "output_violations": violation_rows(output_violations(row.response)),
        "job_text": _queued_job_text(row),
        "check_url": _check_url(row),
        "fetch_offered": fetch_offered(row),
        "trace_url": trace_url(row),
    }
    if request.GET.get("fetch") == "1" and row.engine_operation_id and context["fetch_offered"]:
        fetched: OperationRecord | None = None
        fetch_error: ErrorPanel | None = None
        try:
            # A read (3 s timeout), never recorded.
            fetched = engine_client_for(request).get_operation(
                row.engine_operation_id, viewpoint=_viewpoint(row)
            )
        except EngineClientError as exc:
            log_engine_error(request, exc)
            fetch_error = panel_for(exc)
        result_body = redact(fetched.result.body) if fetched and fetched.result else None
        context.update(
            fetched=fetched,
            fetch_error=fetch_error,
            fetched_body=result_body,
            fetched_report=stored_turn_report(result_body),
        )
    return render(request, "console/operation_detail.html", context)


# --- eval reports (#149) -------------------------------------------------------------------------


def _stories(request: HttpRequest, client: EngineClient) -> list[StorySummary] | None:
    """The page's one ``list_stories()`` read (the story select and the samples' story names);
    ``None`` when it failed (a free-text story id, and stories shown as ids)."""
    try:
        return client.list_stories()
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


def _section_error(
    request: HttpRequest, exc: EngineClientError, section: YieldSection | ShadowSection
) -> None:
    """Convention 10 per section: a 422 beside the section's form, anything else #43's panel
    inside the section."""
    log_engine_error(request, exc)
    if isinstance(exc, EngineError) and exc.http_status == 422:
        section.window.apply_engine_error(exc)
    else:
        section.error = panel_for(exc)


def _read_yield(request: HttpRequest, client: EngineClient, section: YieldSection) -> None:
    if not section.window.valid:
        return
    try:
        section.report = client.get_escalation_yield(
            section.window.start.moment, section.window.end.moment
        )
    except EngineClientError as exc:
        _section_error(request, exc, section)


def _read_shadow(
    request: HttpRequest,
    client: EngineClient,
    section: ShadowSection,
    stories: list[StorySummary] | None,
) -> None:
    if not section.window.valid:
        return
    try:
        report = client.get_extraction_shadow(
            section.window.start.moment,
            section.window.end.moment,
            story_id=section.story or None,
            limit=SAMPLE_LIMIT,
            after=section.after,
        )
    except EngineClientError as exc:
        _section_error(request, exc, section)
        return
    section.report = report
    section.rows = sample_rows(report.items, stories)
    section.pages = paging(request.GET, section.window, report.next_cursor)


@require_safe
def evals(request: HttpRequest) -> HttpResponse:
    """``operations:evals``: the escalation-yield and claim-detection shadow reports over a
    window (#149). Reads, in sequence: the story list (even when a section has a form error),
    then each report whose window parsed; either failing still renders the other. GETs, never
    recorded."""
    client = engine_client_for(request)
    now = default_now(timezone.now())
    stories = _stories(request, client)
    escalation = yield_section(request.GET, now)
    shadow = shadow_section(request.GET, now, stories)
    _read_yield(request, client, escalation)
    _read_shadow(request, client, shadow, stories)
    context = {
        "escalation": escalation,
        "shadow": shadow,
        "blank_hint": BLANK_HINT,
        "no_samples": NO_SAMPLES,
        "shape_title": SHAPE_TITLE,
        "stories_unavailable": STORIES_UNAVAILABLE,
        "eval_runs_issue": EVAL_RUNS_ISSUE,
    }
    return render(request, "console/operations_evals.html", context)

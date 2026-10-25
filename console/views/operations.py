"""The operation log (#44): every Engine operation this Console performed, with filters, and
one operation's full record with the Engine's current view of it on request."""

from dataclasses import dataclass
from typing import Any

from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_safe

from console.engine.client import EngineClientError
from console.engine.models import OperationRecord
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.models import EngineOperation
from console.operation_log import (
    input_violations,
    is_turn,
    output_violations,
    policy_ids,
    rebuild_job_id,
    rebuild_job_text,
)
from console.redaction import redact
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


def _rebuild_job_text(row: EngineOperation) -> str:
    """A queued rebuild's honest job message (#50); "" for anything else."""
    job_id = rebuild_job_id(row.kind, row.http_status, row.response)
    return rebuild_job_text(job_id) if job_id is not None else ""


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
        "job_text": _rebuild_job_text(row),
    }
    if request.GET.get("fetch") == "1" and row.engine_operation_id:
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

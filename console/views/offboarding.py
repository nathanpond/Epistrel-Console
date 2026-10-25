"""Offboarding (#77): offboard a principal after a dry run, and follow the job to its report.

- ``/offboarding/new/`` (GET, ``?principal=`` and the form's own names pre-fill it): the plan
  form. A valid POST makes the **dry run** (``dry_run: true``, a fresh idempotency key) and
  renders the preview in place: the counts per type, the Engine's warnings, and the start
  button, which exists only there. A refresh re-POSTs the form: another dry run, never a run.
- ``/offboarding/start/`` (POST only): the start. It reads nothing from the POST but the signed
  payload (:mod:`console.twostage`, kind ``offboard``) the preview carried: the request body
  (``dry_run: false``, ``ignore_warnings`` true for "Apply anyway"), the idempotency key minted
  when the preview rendered, and the operator and acting principal it was made by. A payload
  that is expired, tampered with, another operator's or made while acting as someone else is
  refused with zero Engine calls. 202 → the job page; 200 ``applied: false`` → the preview
  again with the warnings it returned.
- ``/offboarding/<job_id>/`` and its ``progress/`` partial: the job, polled every 2 s
  (:mod:`console.job_poll`) until ``done`` or ``failed``, then its report per type. The first
  terminal read settles the start's ``queued`` operation-log row.
- ``/offboarding/``: the jobs this Console started, from the operation log (#44): ``offboard``
  rows that got a ``job_id`` (dry runs and errors have none), one per job.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Max
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from console import twostage
from console.acting import acting_principal
from console.engine.client import (
    EngineClientError,
    EngineError,
    EngineUnreachable,
    principal_for,
)
from console.engine.models import (
    OFFBOARDING_TYPES,
    OffboardingActions,
    OffboardingJob,
    OffboardingPlan,
    OffboardingQueued,
    OffboardingWarning,
)
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.entities import BY_LOOKUP_KEY, entity_info, entity_link
from console.forms.offboarding import RELATIONS, TITLES, OffboardingForm, form_data
from console.forms.principal import remember_principals
from console.ids import is_ulid, new_ulid
from console.job_poll import poll_context, poll_failure, settle_job
from console.models import EngineOperation
from console.templatetags.principals import principal_link
from console.views.principals import ENGINE_LOOKUP_KEYS, principal_url

KIND = "offboard"  # the client method, the operation log's kind and the two-stage kind
LIST_PAGE = 50
ALL_TYPES = (*OFFBOARDING_TYPES, RELATIONS)
OWNED_COUNT_KEYS = ("delete", "transfer", "keep")
RELATION_COUNT_KEYS = ("remove", "keep")
COUNT_TITLES = {"delete": "Delete", "transfer": "Transfer", "keep": "Keep", "remove": "Remove"}

START_LABEL = "Start offboarding (cannot be undone)"
APPLY_ANYWAY = "Apply anyway"
RETRY_LABEL = "Retry"
NOTHING = "Nothing to offboard — no entity would change"
IN_PROGRESS = "An offboarding is already running for {}"
NOT_FOUND = "No such offboarding, or its report expired (kept 90 days)"
OWN_PRINCIPAL = "You are offboarding your own principal."
ACTING_PRINCIPAL = "You are offboarding the principal you are acting as."
NOTHING_CHANGED = "Nothing was changed."
WAITING = "Waiting for a worker"
WARNING_TEXT = {
    "shared_story": "Will be deleted for everyone — others are related to it",
    "profile_in_use": "Will be retired — other owners' stories use it",
    "media_referenced": "Will be retired — other owners' records reference it",
    "default_profile": "The default profile — it will not be deleted",
}
SKIP_REASONS = {
    "gone": "Already gone",
    "not_owned": "No longer owned by them",
    "name_taken": "Target already has one with this name",
    "default_profile": "Default profile — never deleted",
    "busy": "A turn was running",
}
REPORT_SECTIONS = ("deleted", "transferred", "removed", "kept", "skipped")
SECTION_TITLES = {
    "deleted": "Deleted",
    "transferred": "Transferred",
    "removed": "Removed",
    "kept": "Kept",
    "skipped": "Skipped",
}
RETIRED_TYPES = frozenset({"media", "engine_profiles"})
RETIRED_TITLE = "Deleted (retired)"
# Engine errors placed on a form field, and the ones the start retries with the same payload.
FIELD_CODES = {
    "principal_reserved": "principal",
    "transfer_target_required": "transfer_to",
    "transfer_target_invalid": "transfer_to",
}
IN_PROGRESS_CODE = "offboarding_in_progress"
RETRY_CODES = frozenset({"original_in_flight_timeout", "database_unavailable"})
TERMINAL = frozenset({"done", "failed"})


# --- shared pieces --------------------------------------------------------------------------------


def _new_url(query: Mapping[str, str] | None = None) -> str:
    url = reverse("offboarding:new")
    return f"{url}?{urlencode(query)}" if query else url


def _job_url(job_id: str) -> str:
    return reverse("offboarding:job", args=[job_id])


def _type_title(key: str) -> str:
    return TITLES.get(key, key)


def _entity_type(name: str | None) -> str | None:
    """A registry type for an Engine type name: singular first (``story``), then the plural key
    (``stories``); ``None`` when neither matches."""
    if not name:
        return None
    if entity_info(name) is not None:
        return name
    info = BY_LOOKUP_KEY.get(name)
    return info.type if info is not None else None


def _id_link(type_name: str | None, entity_id: object) -> SafeString:
    """The id linked to its Console page (by a singular or plural type name), else ``<code>``."""
    return entity_link(_entity_type(type_name), str(entity_id))


@dataclass(frozen=True)
class InProgress:
    principal: str
    job_url: str | None

    @property
    def text(self) -> SafeString:
        return format_html(IN_PROGRESS, principal_link(self.principal))


def _in_progress(exc: EngineError, principal: str) -> InProgress:
    job_id = exc.details.get("job_id")
    url = _job_url(job_id) if isinstance(job_id, str) and is_ulid(job_id) else None
    return InProgress(principal, url)


# --- the counts and warnings (preview, job page) ------------------------------------------------


@dataclass(frozen=True)
class CountsTable:
    columns: list[str]  # count keys
    titles: list[str]
    rows: list[tuple[str, list[str]]]  # (type title, cells)


def counts_table(counts: Mapping[str, Mapping[str, int]]) -> CountsTable:
    """One row per type (fixed order, then relations, then types the Console does not know);
    the columns are the count keys (``delete``/``transfer``/``keep``, relations ``remove``),
    then any other key the Engine sent. A cell a type does not have is "—"."""
    columns = [*OWNED_COUNT_KEYS, "remove"]
    for by_key in counts.values():
        for key in by_key:
            if key not in columns:
                columns.append(key)
    types = [t for t in ALL_TYPES if t in counts] + [t for t in counts if t not in ALL_TYPES]
    rows = []
    for kind in types:
        by_key = counts[kind]
        cells = [str(by_key[c]) if c in by_key else "—" for c in columns]
        rows.append((_type_title(kind), cells))
    used = [c for c in columns if any(c in counts[t] for t in types)] or list(OWNED_COUNT_KEYS)
    keep = [i for i, c in enumerate(columns) if c in used]
    return CountsTable(
        columns=[columns[i] for i in keep],
        titles=[COUNT_TITLES.get(columns[i], columns[i]) for i in keep],
        rows=[(title, [cells[i] for i in keep]) for title, cells in rows],
    )


def nothing_to_do(counts: Mapping[str, Mapping[str, int]]) -> bool:
    """Every count is zero but ``keep``: no entity would change."""
    return all(n == 0 for by_key in counts.values() for k, n in by_key.items() if k != "keep")


@dataclass(frozen=True)
class WarningRow:
    kind: str
    text: str
    known: bool
    entity: SafeString
    principals: SafeString


def warning_rows(warnings: Sequence[OffboardingWarning]) -> list[WarningRow]:
    rows = []
    for w in warnings:
        others = format_html_join(", ", "{}", ((principal_link(p),) for p in w.principals))
        rows.append(
            WarningRow(
                w.kind,
                WARNING_TEXT.get(w.kind, w.kind),
                w.kind in WARNING_TEXT,
                _id_link(w.entity_type, w.entity_id),
                others,
            )
        )
    return rows


def _warnings_of(raw: object) -> list[OffboardingWarning]:
    items = raw if isinstance(raw, list) else []
    return [w for w in (OffboardingWarning.from_json(i) for i in items) if w is not None]


def _counts_of(raw: object) -> dict[str, dict[str, int]]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): {str(c): n for c, n in v.items() if isinstance(n, int)}
        for k, v in raw.items()
        if isinstance(v, dict)
    }


def actions_summary(request_body: Mapping[str, Any]) -> str:
    """The non-keep actions in type order ("stories: transfer, media: delete"), plus
    "→ <transfer_to>" when present."""
    actions = request_body.get("actions")
    parts: list[str] = []
    if isinstance(actions, dict):
        known = [t for t in ALL_TYPES if t in actions] + [t for t in actions if t not in ALL_TYPES]
        parts = [f"{t}: {actions[t]}" for t in known if actions[t] != "keep"]
    text = ", ".join(parts) or "—"
    target = request_body.get("transfer_to")
    return f"{text} → {target}" if isinstance(target, str) and target else text


# --- the form -------------------------------------------------------------------------------------


def _render_form(
    request: HttpRequest,
    form: OffboardingForm,
    *,
    engine_error: ErrorPanel | None = None,
    in_progress: InProgress | None = None,
    status: int = 200,
) -> HttpResponse:
    context = {
        "form": form,
        "engine_error": engine_error,
        "in_progress": in_progress,
        "index_url": reverse("offboarding:index"),
    }
    return render(request, "console/offboarding/form.html", context, status=status)


def _initial(request: HttpRequest) -> dict[str, str]:
    names = ("principal", "transfer_to", *(f"actions__{t}" for t in ALL_TYPES))
    return {name: request.GET[name] for name in names if name in request.GET}


def _place_engine_error(
    form: OffboardingForm, exc: EngineError, principal: str
) -> tuple[ErrorPanel | None, InProgress | None]:
    """Put a refusal where it belongs: a field (``principal_reserved``, ``transfer_target_*``),
    the in-progress notice, or the generic panel."""
    name = FIELD_CODES.get(exc.code)
    if name is not None:
        form.add_error(name, exc.message or exc.code)
        return None, None
    if exc.code == IN_PROGRESS_CODE:
        return None, _in_progress(exc, principal)
    return panel_for(exc), None


@require_http_methods(["GET", "HEAD", "POST"])
def offboarding_new(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return _render_form(request, OffboardingForm(initial=_initial(request)))
    form = OffboardingForm(request.POST)
    if not form.is_valid():
        return _render_form(request, form)
    remember_principals(form)
    principal: str = form.cleaned_data["principal"]
    actions = form.actions()
    target = form.transfer_target()
    client = engine_client_for(request)
    try:
        answer = client.offboard(
            principal,
            actions,
            target,
            dry_run=True,
            ignore_warnings=False,
            idempotency_key=new_ulid(),
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        panel, busy = _place_engine_error(form, exc, principal)
        return _render_form(request, form, engine_error=panel, in_progress=busy)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _render_form(request, form, engine_error=panel_for(exc))
    if isinstance(answer, OffboardingQueued):  # a dry run never queues; follow it if it did
        return redirect(_job_url(answer.job_id))
    body = _request_body(principal, actions, target)
    return _render_preview(request, body, answer.counts, answer.warnings, answer.config_revision)


def _request_body(
    principal: str, actions: OffboardingActions, target: str | None
) -> dict[str, Any]:
    """The start's request body (as the client sends it), ``ignore_warnings`` set later."""
    body: dict[str, Any] = {"principal": principal, "actions": actions.to_json()}
    if target is not None:
        body["transfer_to"] = target
    body["dry_run"] = False
    return body


# --- the preview ----------------------------------------------------------------------------------


def _sign_start(
    request: HttpRequest,
    body: Mapping[str, Any],
    counts: Mapping[str, Mapping[str, int]],
    warnings: Sequence[OffboardingWarning],
    config_revision: int | None,
) -> str:
    """The start's signed payload: the full body (``ignore_warnings`` true when the preview
    warns: "Apply anyway"), a key minted now, and who it was made by and as. ``counts`` and
    ``warnings`` re-render the preview if the start fails; ``config_revision`` is a record."""
    sent = {**body, "dry_run": False, "ignore_warnings": bool(warnings)}
    base = {
        "config_revision": config_revision,
        "operator": request.user.pk,
        "acting": acting_principal(request),
        "key": new_ulid(),
        "counts": dict(counts),
        "warnings": [dataclasses.asdict(w) for w in warnings],
    }
    return twostage.sign(KIND, str(body["principal"]), sent, base=base)


def _render_preview(
    request: HttpRequest,
    body: Mapping[str, Any],
    counts: Mapping[str, Mapping[str, int]],
    warnings: Sequence[OffboardingWarning],
    config_revision: int | None,
    *,
    offer: bool = True,
    token: str | None = None,
    engine_error: ErrorPanel | None = None,
    in_progress: InProgress | None = None,
) -> HttpResponse:
    """The dry run's result: counts, warnings and the one way to start. ``offer=False`` shows
    no start; ``token`` re-offers a payload as it was ("Retry": the same key)."""
    principal = str(body["principal"])
    nothing = nothing_to_do(counts)
    start_token = None
    label = APPLY_ANYWAY if warnings else START_LABEL
    if token is not None:
        start_token, label = token, RETRY_LABEL
    elif offer and not nothing:
        start_token = _sign_start(request, body, counts, warnings, config_revision)
    own = principal == principal_for(request.user)
    acting = principal == acting_principal(request)
    context = {
        "principal": principal,
        "transfer_to": body.get("transfer_to"),
        "summary": actions_summary(body),
        "counts": counts_table(counts),
        "warnings": warning_rows(warnings),
        "nothing": nothing,
        "nothing_text": NOTHING,
        "self_warning": OWN_PRINCIPAL if own else ACTING_PRINCIPAL if acting else "",
        "edit_url": _new_url(form_data(dict(body))),
        "token": start_token,
        "confirm_label": label,
        "confirm_url": reverse("offboarding:start"),
        "danger": True,
        "engine_error": engine_error,
        "in_progress": in_progress,
        "principal_url": principal_url(principal),
    }
    return render(request, "console/offboarding/preview.html", context)


# --- the start ------------------------------------------------------------------------------------


def _expired(request: HttpRequest) -> HttpResponse:
    messages.error(request, twostage.EXPIRED_TEXT)
    return redirect(_new_url())


@require_POST
def offboarding_start(request: HttpRequest) -> HttpResponse:
    try:
        payload = twostage.unsign(KIND, request.POST.get(twostage.CONFIRM_FIELD, ""))
    except twostage.ConfirmationExpired:
        return _expired(request)
    base = payload.base if isinstance(payload.base, dict) else {}
    body = payload.body if isinstance(payload.body, dict) else {}
    actions = OffboardingActions.from_json(body.get("actions"))
    key = base.get("key")
    target = body.get("transfer_to")
    if (
        base.get("operator") != request.user.pk
        or base.get("acting") != acting_principal(request)
        or body.get("principal") != payload.target_id
        or actions is None
        or not isinstance(key, str)
        or not (target is None or isinstance(target, str))
    ):
        return _expired(request)  # another operator's or acting principal's: handled alike
    principal = payload.target_id
    counts = _counts_of(base.get("counts"))
    warnings = _warnings_of(base.get("warnings"))
    revision = base.get("config_revision")
    revision = revision if isinstance(revision, int) else None
    client = engine_client_for(request)
    try:
        answer = client.offboard(
            principal,
            actions,
            target,
            dry_run=False,
            ignore_warnings=bool(body.get("ignore_warnings")),
            idempotency_key=key,
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        if exc.code in FIELD_CODES:
            form = OffboardingForm(form_data(body))
            form.is_valid()
            panel, _ = _place_engine_error(form, exc, principal)
            return _render_form(request, form, engine_error=panel)
        if exc.code == IN_PROGRESS_CODE:
            busy = _in_progress(exc, principal)
            return _render_preview(
                request, body, counts, warnings, revision, offer=False, in_progress=busy
            )
        retry = exc.http_status == 503 and exc.code in RETRY_CODES
        token = request.POST.get(twostage.CONFIRM_FIELD, "") if retry else None
        return _render_preview(
            request,
            body,
            counts,
            warnings,
            revision,
            offer=False,
            token=token,
            engine_error=panel_for(exc),
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        # No answer: nothing known written, and the same key replays safely.
        token = request.POST.get(twostage.CONFIRM_FIELD, "")
        retry_token = token if isinstance(exc, EngineUnreachable) else None
        return _render_preview(
            request,
            body,
            counts,
            warnings,
            revision,
            offer=False,
            token=retry_token,
            engine_error=panel_for(exc),
        )
    if isinstance(answer, OffboardingPlan):  # held by new warnings: preview them again
        return _render_preview(
            request, body, answer.counts, answer.warnings, answer.config_revision
        )
    messages.success(request, f"Offboarding {principal} started — job {answer.job_id}")
    return redirect(_job_url(answer.job_id))


# --- the job page ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportLine:
    id: SafeString
    details: list[tuple[str, SafeString]]  # (label, value)
    more: list[tuple[str, str]]  # keys the Console does not know


@dataclass(frozen=True)
class ReportType:
    key: str
    title: str
    entries: list[ReportLine]


@dataclass(frozen=True)
class ReportSection:
    key: str
    title: str
    types: list[ReportType]


@dataclass(frozen=True)
class RemainingGroup:
    title: str
    types: list[tuple[str, int, list[SafeString]]]  # (type title, count, linked ids)


@dataclass(frozen=True)
class ShownReport:
    sections: list[ReportSection]
    remaining: list[RemainingGroup]
    remaining_raw: Any = None  # a ``remaining`` the Console cannot read, shown as JSON
    more: dict[str, Any] = field(default_factory=dict)  # report keys the Console does not know

    @property
    def empty(self) -> bool:
        return not self.sections


KNOWN_ENTRY_KEYS = ("id", "entity_type", "to", "companion_of", "reason", "relations")


def _entry(section: str, type_key: str, raw: object) -> ReportLine:
    """One report line: a string id links under its type key; an object shows ``id`` (linked by
    its own ``entity_type`` when it has one, else the type key), ``to`` (a principal),
    ``companion_of``, ``reason`` (humanised when skipped) and ``relations``; other keys go
    under "More"."""
    type_name = ENGINE_LOOKUP_KEYS.get(type_key, type_key)
    if not isinstance(raw, dict):
        return ReportLine(_id_link(type_name, raw), [], [])
    own_type = raw.get("entity_type")
    linked_type = own_type if isinstance(own_type, str) else type_name
    entry_id = _id_link(linked_type, raw.get("id", "—"))
    details: list[tuple[str, SafeString]] = []
    if isinstance(own_type, str):
        details.append(("Type", format_html("{}", own_type)))
    if isinstance(raw.get("to"), str):
        details.append(("To", principal_link(raw["to"])))
    if "companion_of" in raw:
        details.append(("Companion of", _id_link("character", raw["companion_of"])))
    if "reason" in raw:
        reason = str(raw["reason"])
        shown = SKIP_REASONS.get(reason, reason) if section == "skipped" else reason
        details.append(("Reason", format_html("{} <code>{}</code>", shown, reason)))
    if isinstance(raw.get("relations"), list):
        names = ", ".join(str(r) for r in raw["relations"])
        details.append(("Relations", format_html("{}", names)))
    more = [(k, repr(v) if not isinstance(v, str) else v) for k, v in raw.items()]
    more = [(k, v) for k, v in more if k not in KNOWN_ENTRY_KEYS]
    return ReportLine(entry_id, details, more)


def _section(key: str, by_type: object) -> ReportSection | None:
    if not isinstance(by_type, dict):
        return None
    order = [t for t in ALL_TYPES if t in by_type] + [t for t in by_type if t not in ALL_TYPES]
    types = []
    for type_key in order:
        items = by_type[type_key]
        if not isinstance(items, list) or not items:
            continue
        title = _type_title(type_key)
        if key == "deleted" and type_key in RETIRED_TYPES:
            title = f"{title} — {RETIRED_TITLE.lower()}"
        types.append(ReportType(type_key, title, [_entry(key, type_key, i) for i in items]))
    if not types:
        return None
    return ReportSection(key, SECTION_TITLES.get(key, key), types)


def _remaining(raw: object) -> tuple[list[RemainingGroup], Any]:
    """``remaining``: ``owned``/``related`` ids by type (with ``*_counts``), each linked."""
    if raw is None:
        return [], None
    if not isinstance(raw, dict):
        return [], raw
    groups = []
    for side, title in (("owned", "Still owned"), ("related", "Still related")):
        ids = raw.get(side)
        counts = raw.get(f"{side}_counts")
        if not isinstance(ids, dict):
            continue
        counts = counts if isinstance(counts, dict) else {}
        order = [t for t in ALL_TYPES if t in ids] + [t for t in ids if t not in ALL_TYPES]
        types = []
        for type_key in order:
            listed = ids[type_key] if isinstance(ids[type_key], list) else []
            count = counts.get(type_key)
            count = count if isinstance(count, int) else len(listed)
            if count == 0 and not listed:
                continue
            type_name = ENGINE_LOOKUP_KEYS.get(type_key, type_key)
            types.append((_type_title(type_key), count, [_id_link(type_name, i) for i in listed]))
        if types:
            groups.append(RemainingGroup(title, types))
    known = {"owned", "related", "owned_counts", "related_counts"}
    extra = {k: v for k, v in raw.items() if k not in known}
    return groups, extra or None


def shown_report(report: Mapping[str, Any]) -> ShownReport:
    """The report in the fixed section order; empty sections and types hidden."""
    sections = [s for s in (_section(k, report.get(k)) for k in REPORT_SECTIONS) if s is not None]
    remaining, remaining_raw = _remaining(report.get("remaining"))
    more = {k: v for k, v in report.items() if k not in (*REPORT_SECTIONS, "remaining")}
    return ShownReport(sections, remaining, remaining_raw, more)


@dataclass(frozen=True)
class JobView:
    job: OffboardingJob
    terminal: bool
    progress_value: int | None  # None: indeterminate (total 0)
    progress_text: str
    current: SafeString | None
    counts: CountsTable
    actions: str
    report: ShownReport | None


def job_view(job: OffboardingJob) -> JobView:
    done, total = job.progress.done, job.progress.total
    current = None
    if job.current is not None and job.status not in TERMINAL:
        current = format_html(
            "{} {}", _type_title(job.current.type), _id_link(job.current.type, job.current.id)
        )
    return JobView(
        job=job,
        terminal=job.status in TERMINAL,
        progress_value=done if total else None,
        progress_text=f"{done}/{total}",
        current=current,
        counts=counts_table(job.counts),
        actions=actions_summary(job.request),
        report=shown_report(job.report) if job.report is not None else None,
    )


def _start_row(job_id: str) -> EngineOperation | None:
    return (
        EngineOperation.objects.filter(kind=KIND, job_id=job_id)
        .order_by("-created_at", "-id")
        .first()
    )


def _confirmed_warnings(job_id: str) -> list[WarningRow] | None:
    """The warnings the start was confirmed over, from its #44 row's 202 (``None``: no row)."""
    row = _start_row(job_id)
    if row is None:
        return None
    response = row.response if isinstance(row.response, dict) else {}
    return warning_rows(_warnings_of(response.get("warnings")))


@dataclass(frozen=True)
class Read:
    """One read of the job, for the page or its partial."""

    job: JobView | None
    polling: bool
    retrying: bool
    not_found: bool
    engine_error: ErrorPanel | None


def _read(request: HttpRequest, job_id: str) -> Read:
    try:
        job = engine_client_for(request).get_offboarding(job_id)
    except EngineClientError as exc:
        failure = poll_failure(request, exc)
        return Read(None, failure.retry, failure.retry, failure.not_found, failure.panel)
    view = job_view(job)
    if view.terminal:
        error_code = job.error.error_class if job.error is not None else "failed"
        settle_job(job_id, failed=job.status == "failed", error_code=error_code)
    return Read(view, not view.terminal, False, False, None)


def _poll_context(job_id: str, read: Read) -> dict[str, Any]:
    context = poll_context(
        poll_url=reverse("offboarding:progress", args=[job_id]),
        refresh_url=_job_url(job_id),
        body_template="console/offboarding/_job_status.html",
        polling=read.polling,
        retrying=read.retrying,
    )
    context.update(
        {
            "view": read.job,
            "not_found": read.not_found,
            "not_found_text": NOT_FOUND,
            "engine_error": read.engine_error,
            "waiting_text": WAITING,
            "nothing_changed": NOTHING_CHANGED,
        }
    )
    return context


@require_safe
def offboarding_job(request: HttpRequest, job_id: str) -> HttpResponse:
    read = _read(request, job_id)
    context = _poll_context(job_id, read)
    job = read.job.job if read.job is not None else None
    target = job.request.get("transfer_to") if job is not None else None
    context.update(
        {
            "job_id": job_id,
            "job": job,
            "transfer_to": target if isinstance(target, str) else None,
            "principal_url": principal_url(job.principal) if job is not None else None,
            "confirmed_warnings": _confirmed_warnings(job_id),
            "index_url": reverse("offboarding:index"),
        }
    )
    status = 404 if read.not_found else 200
    return render(request, "console/offboarding/job.html", context, status=status)


@require_safe
def offboarding_progress(request: HttpRequest, job_id: str) -> HttpResponse:
    """The polled partial: the job block again (with ``hx-trigger`` until it is terminal)."""
    read = _read(request, job_id)
    return render(request, "console/_job_poll.html", _poll_context(job_id, read))


# --- the list -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListRow:
    row: EngineOperation
    principal: str | None
    actions: str
    url: str


def _list_row(row: EngineOperation) -> ListRow:
    stored = row.request if isinstance(row.request, dict) else {}
    sent = stored.get("body")
    body: dict[str, Any] = sent if isinstance(sent, dict) else {}
    principal = body.get("principal")
    return ListRow(
        row,
        principal if isinstance(principal, str) else None,
        actions_summary(body),
        _job_url(str(row.job_id)),
    )


@require_safe
def offboarding_index(request: HttpRequest) -> HttpResponse:
    """The jobs this Console started: ``offboard`` rows with a ``job_id`` (the newest row per
    job), newest first, :data:`LIST_PAGE` per page. No Engine calls."""
    started = EngineOperation.objects.filter(kind=KIND, job_id__isnull=False).exclude(job_id="")
    newest = started.values("job_id").annotate(latest=Max("id")).values("latest")
    rows = EngineOperation.objects.filter(id__in=newest).order_by("-created_at", "-id")
    page = Paginator(rows, LIST_PAGE).get_page(request.GET.get("page"))
    context = {
        "rows": [_list_row(r) for r in page],
        "page": page,
        "new_url": _new_url(),
    }
    return render(request, "console/offboarding/index.html", context)

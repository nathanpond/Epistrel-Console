"""Upgrade a profile's pinned stories from its page, after their impact (#310, FR-PROF-7,8).

``profiles:upgrade-pinned`` (``/profiles/<id>/upgrade-pinned/``, GET/HEAD/POST) is E8's
two-stage write over ``POST /engine-profiles/{id}/upgrade-pinned``
(:meth:`~console.engine.client.EngineClient.upgrade_pinned_stories`):

- **GET**: one ``get_engine_profile`` (the name and latest version), then
  :class:`~console.forms.profiles.UpgradePinnedForm` (E14's upgrade mode, ``backfill`` initial,
  or ``?upgrade_mode=`` when valid), the line :data:`LINE` and "Show the impact". A deleted profile
  reads :data:`DELETED_TEXT` with no form (200). An id that isn't an Engine id (C13: trimmed and
  upper-cased first) is the 404 page with no call.
- **First stage** (a POST without ``twostage.CONFIRM_FIELD``): an invalid mode re-renders the form
  with the field's error and sends no upgrade. Otherwise the profile is read again (a deleted one
  shows :data:`DELETED_TEXT` and sends nothing: the route has no ``profile_deleted`` refusal), then
  ``upgrade_pinned_stories(ignore_warnings=False, upgrade_mode=<chosen>)`` under the form's key.
- **Held** (``applied: false``): :data:`HELD_TEXT`, the upgrade mode, the impact through
  ``console/_impact_report.html`` (``mode="profile"``, one ``story_labels`` call) and
  ``_two_stage.html`` ("Upgrade anyway" / Cancel to the profile page) carrying the token
  ``twostage.sign("upgrade-pinned", profile_id, {ignore_warnings: true, upgrade_mode}, base)``,
  with ``base`` = ``{name, current_version, report}`` (the report as JSON, so a failed confirm
  re-renders it with no read). "Change the upgrade mode" returns to the GET form with the mode
  pre-selected.
- **Confirm** (a POST with the token): unsigned (expired, tampered, another profile's or a body
  that isn't ours → ``EXPIRED_TEXT`` flash, a redirect to the form, zero calls), then the signed
  body re-sent under the partial's fresh key, with no read. A fresh ``applied: false`` re-renders
  the new report with :data:`REHELD_TEXT` and a new token.
- **Result** (``applied: true``, at either stage, rendered in place with E21's muted key tail):
  :func:`none_pinned_text` for an empty report; otherwise :func:`upgraded_text`, the read-only
  impact and, when the Engine queued any, the "Queued backfills" table (queue order, nulls last;
  the story, its Backfill page through ``console.upgrades.backfill_url`` (D19), the job, E7's
  backfill status and the queue position) above :data:`QUEUE_LINE`.
- **Refusals** render in-page: ``not_found`` → :data:`NOT_FOUND_TEXT` (404), ``profile_deleted``
  → :data:`DELETED_TEXT`, anything else #43's panel; none makes a second call. On a confirm, the
  panel sits above the same confirm (same token; the key kept on an unknown outcome, E21).

Recorded (#44) against the profile (``entity_type="engine_profile"``, E19).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from console import twostage
from console.confirm import posted_idempotency_key
from console.engine.choices import StoryLabel, story_labels
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import EngineProfile, ImpactReport, ProfileEdit, QueuedBackfill
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.forms.profiles import UPGRADE_MODES, UpgradePinnedForm
from console.forms.upgrades import UPGRADE_MODE_FIELD
from console.ids import is_ulid, new_ulid
from console.impact import report_story_ids, story_url
from console.jobs import JobKind, job_state
from console.raw import EMPTY, plural
from console.upgrades import backfill_url, upgrade_mode_text
from console.views.profile_edit import UPGRADE_UNAVAILABLE
from console.views.profiles import detail_url
from console.views.story_config import model_json

UpgradeMode = Literal["backfill", "forward_only"]
KIND = "upgrade-pinned"
TEMPLATE = "console/profiles/upgrade_pinned.html"
NOT_FOUND = "not_found"
PROFILE_DELETED = "profile_deleted"
BAD_RESPONSE = "bad_response"

LINE = (
    "Moves every story pinned to an older version of {name} to v{version}, after showing what "
    "changes for each"
)
DELETED_TEXT = "This profile is deleted — assign the stories another profile instead"
NOT_FOUND_TEXT = "No such profile"
MALFORMED_TEXT = "No such profile — the id in the address isn't an Engine id"
NONE_PINNED = "No story is pinned to an older version of {name} — nothing to upgrade"
HELD_TEXT = (
    "The Engine held this upgrade for confirmation: nothing was written. Review what it does to "
    "each pinned story under {mode}, then upgrade anyway or go back."
)
REHELD_TEXT = (
    "The Engine held this upgrade again: the report changed since you saw it, and nothing was "
    "written. Review it, then upgrade anyway or go back."
)
QUEUE_LINE = (
    "Each story keeps its previous version, unpaused, until its backfill starts; it pauses only "
    "while its own runs"
)
CONFIRM_LABEL = "Upgrade anyway"
CHANGE_MODE_LABEL = "Change the upgrade mode"


def heading(name: str) -> str:
    return f"Upgrade pinned stories — {name}"


def none_pinned_text(name: str) -> str:
    return NONE_PINNED.format(name=name)


def upgraded_text(count: int, version: object) -> str:
    """ "Upgraded <count> pinned stor(y|ies) to v<version>" (C11 plural)."""
    return f"Upgraded {plural(count, 'pinned story', 'pinned stories')} to v{version}"


def upgrade_url(profile_id: str, mode: str | None = None) -> str:
    """The form page, with ``mode`` pre-selected when given."""
    url = reverse("profiles:upgrade-pinned", args=[profile_id])
    return f"{url}?{urlencode({UPGRADE_MODE_FIELD: mode})}" if mode else url


@dataclass(frozen=True)
class Facts:
    """What the page names: the profile's id, name and latest version."""

    profile_id: str
    name: str
    current_version: int

    def base(self, report: ImpactReport) -> dict[str, Any]:
        return {
            "name": self.name,
            "current_version": self.current_version,
            "report": model_json(report),
        }


@dataclass(frozen=True)
class QueuedRow:
    story_id: str
    url: str
    name: str
    note: str
    backfill_url: str | None
    job_id: str
    status_text: str
    status_raw: str
    queue_text: str


def queued_rows(
    backfills: list[QueuedBackfill], labels: Mapping[str, StoryLabel]
) -> list[QueuedRow]:
    """The queued backfills by ascending ``queue_position``, nulls last, ties in the Engine's
    order (``sorted`` is stable)."""
    ordered = sorted(
        backfills,
        key=lambda b: (b.queue_position is None, b.queue_position or 0),
    )
    rows = []
    for backfill in ordered:
        label = labels.get(backfill.story_id)
        named = label is not None and label.ok and label.text != backfill.story_id
        state = job_state(JobKind.BACKFILL, backfill.status)
        rows.append(
            QueuedRow(
                backfill.story_id,
                story_url(backfill.story_id),
                label.text if named and label is not None else "",
                (label.error or "") if label is not None else "",
                backfill_url(backfill.story_id),
                backfill.job_id,
                state.text,
                backfill.status,
                f"{backfill.queue_position:,}" if backfill.queue_position is not None else EMPTY,
            )
        )
    return rows


# --- rendering ------------------------------------------------------------------------------------


def _page(request: HttpRequest, context: Mapping[str, Any], status: int = 200) -> HttpResponse:
    return render(request, TEMPLATE, dict(context), status=status)


def _frame(profile_id: str, name: str) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "profile_name": name,
        "heading": heading(name) if name else heading(profile_id),
        "detail_url": detail_url(profile_id),
    }


def _form_page(
    request: HttpRequest,
    facts: Facts,
    form: UpgradePinnedForm,
    *,
    key: str | None = None,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    return _page(
        request,
        {
            **_frame(facts.profile_id, facts.name),
            "stage": "form",
            "form": form,
            "line": LINE.format(name=facts.name, version=facts.current_version),
            "idempotency_key": key or new_ulid(),
            "engine_error": engine_error,
        },
    )


def _text_page(
    request: HttpRequest, profile_id: str, name: str, text: str, status: int = 200
) -> HttpResponse:
    return _page(request, {**_frame(profile_id, name), "stage": "text", "text": text}, status)


def _labels(client: EngineClient, report: ImpactReport, extra: list[str]) -> dict[str, StoryLabel]:
    ids = list(dict.fromkeys([*report_story_ids(report), *extra]))
    return story_labels(client, ids) if ids else {}


def _held_page(
    request: HttpRequest,
    client: EngineClient | None,
    facts: Facts,
    mode: str,
    report: ImpactReport,
    *,
    again: bool,
    token: str | None = None,
    key: str | None = None,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    """The report with "Upgrade anyway": a new token and a fresh key, unless a failed confirm
    re-renders the same ones (``token``/``key``); ``client`` ``None`` makes no label read."""
    if token is None:
        body = {"ignore_warnings": True, "upgrade_mode": mode}
        token = twostage.sign(KIND, facts.profile_id, body, facts.base(report))
    mode_text = upgrade_mode_text(mode)
    return _page(
        request,
        {
            **_frame(facts.profile_id, facts.name),
            "stage": "held",
            "held_text": REHELD_TEXT if again else HELD_TEXT.format(mode=mode_text),
            "upgrade_mode_text": mode_text,
            "impact": report,
            "story_labels": _labels(client, report, []) if client is not None else {},
            "mode": "profile",
            "guarantees_href": detail_url(facts.profile_id),
            "upgrade_unavailable_text": UPGRADE_UNAVAILABLE,
            "token": token,
            "confirm_label": CONFIRM_LABEL,
            "confirm_url": reverse("profiles:upgrade-pinned", args=[facts.profile_id]),
            "cancel_url": detail_url(facts.profile_id),
            "idempotency_key": key or new_ulid(),
            "change_mode_url": upgrade_url(facts.profile_id, mode),
            "change_mode_label": CHANGE_MODE_LABEL,
            "engine_error": engine_error,
        },
    )


def _result_page(
    request: HttpRequest,
    client: EngineClient,
    facts: Facts,
    mode: str,
    edit: ProfileEdit,
    key: str,
) -> HttpResponse:
    report = edit.impact
    context: dict[str, Any] = {
        **_frame(facts.profile_id, facts.name),
        "stage": "result",
        "upgrade_mode_text": upgrade_mode_text(mode),
        "key_tail": key,
    }
    if not report.affected_stories:
        context["result_text"] = none_pinned_text(facts.name)
        context["none_pinned"] = True
        return _page(request, context)
    version = edit.result.current_version if edit.result is not None else facts.current_version
    labels = _labels(client, report, [b.story_id for b in edit.backfills])
    context.update(
        {
            "result_text": upgraded_text(report.affected_count, version),
            "impact": report,
            "story_labels": labels,
            "mode": "profile",
            "guarantees_href": detail_url(facts.profile_id),
            "upgrade_unavailable_text": UPGRADE_UNAVAILABLE,
            "queued": queued_rows(edit.backfills, labels),
            "queue_line": QUEUE_LINE,
        }
    )
    return _page(request, context)


def _answered(
    request: HttpRequest,
    client: EngineClient,
    facts: Facts,
    mode: str,
    edit: ProfileEdit,
    key: str,
    *,
    again: bool,
) -> HttpResponse:
    if edit.applied:
        return _result_page(request, client, facts, mode, edit, key)
    return _held_page(request, client, facts, mode, edit.impact, again=again)


def _refusal(request: HttpRequest, exc: EngineClientError, facts: Facts) -> HttpResponse | None:
    """``not_found`` and ``profile_deleted`` as their in-page texts; ``None`` for the rest."""
    if isinstance(exc, EngineError) and exc.code == NOT_FOUND and exc.http_status == 404:
        return _text_page(request, facts.profile_id, facts.name, NOT_FOUND_TEXT, 404)
    if isinstance(exc, EngineError) and exc.code == PROFILE_DELETED:
        return _text_page(request, facts.profile_id, facts.name, DELETED_TEXT)
    return None


def _unknown(exc: EngineClientError) -> bool:
    """D8's unknown outcome: no answer, or one that didn't parse (the write may have run)."""
    return not isinstance(exc, EngineError) or exc.code == BAD_RESPONSE


# --- the view -------------------------------------------------------------------------------------


def _read_profile(
    request: HttpRequest, client: EngineClient, profile_id: str
) -> EngineProfile | HttpResponse:
    """The profile, or the page its read failure (404 ``not_found``: :data:`NOT_FOUND_TEXT`;
    anything else #43's panel) or its deletion shows."""
    try:
        profile = client.get_engine_profile(profile_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 404:
            return _text_page(request, profile_id, "", NOT_FOUND_TEXT, 404)
        frame = _frame(profile_id, "")
        return _page(request, {**frame, "stage": "error", "engine_error": panel_for(exc)})
    if profile.deleted_at is not None:
        return _text_page(request, profile_id, profile.name, DELETED_TEXT)
    return profile


@require_http_methods(["GET", "HEAD", "POST"])
def upgrade_pinned(request: HttpRequest, profile_id: str) -> HttpResponse:
    """``profiles:upgrade-pinned`` (module docstring)."""
    pid = profile_id.strip().upper()
    if not is_ulid(pid):
        context = {"engine_error": ErrorPanel(MALFORMED_TEXT)}
        return render(request, "console/engine_error_page.html", context, status=404)
    if request.method == "POST" and twostage.CONFIRM_FIELD in request.POST:
        return _confirm(request, pid)
    client = engine_client_for(request)
    if request.method != "POST":
        found = _read_profile(request, client, pid)
        if isinstance(found, HttpResponse):
            return found
        facts = Facts(pid, found.name, found.current_version)
        return _form_page(request, facts, UpgradePinnedForm(initial_mode=_mode_param(request)))
    form = UpgradePinnedForm(request.POST)
    valid = form.is_valid()
    found = _read_profile(request, client, pid)  # the page's facts (and a deletion check)
    if isinstance(found, HttpResponse):
        return found
    facts = Facts(pid, found.name, found.current_version)
    if not valid:  # E21: a local-validation failure mints a fresh key; no upgrade sent
        return _form_page(request, facts, form)
    mode = form.mode()
    key = posted_idempotency_key(request)
    try:
        edit = client.upgrade_pinned_stories(
            pid, ignore_warnings=False, upgrade_mode=_typed(mode), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        refused = _refusal(request, exc, facts)
        if refused is not None:
            return refused
        kept = key if _unknown(exc) else None
        return _form_page(request, facts, form, key=kept, engine_error=panel_for(exc))
    return _answered(request, client, facts, mode, edit, key, again=False)


def _mode_param(request: HttpRequest) -> str | None:
    value = request.GET.get(UPGRADE_MODE_FIELD)
    return value if value in UPGRADE_MODES else None


def _typed(mode: str) -> UpgradeMode:
    """The cleaned mode as the client's ``Literal`` (the form and the token check allow only
    those two)."""
    return cast(UpgradeMode, mode)


def _signed(request: HttpRequest, profile_id: str) -> tuple[Facts, str, ImpactReport] | None:
    """The confirm token's facts, mode and report; ``None`` when it is expired, tampered, for
    another profile or not one this view signed."""
    try:
        payload = twostage.unsign(KIND, request.POST.get(twostage.CONFIRM_FIELD, ""))
    except twostage.ConfirmationExpired:
        return None
    body, base = payload.body, payload.base
    if (
        payload.target_id != profile_id
        or not isinstance(body, dict)
        or body.get("ignore_warnings") is not True
        or body.get("upgrade_mode") not in UPGRADE_MODES
        or not isinstance(base, dict)
        or not isinstance(base.get("name"), str)
        or not isinstance(base.get("current_version"), int)
    ):
        return None
    report = ImpactReport.from_json(base.get("report"))
    if report is None:
        return None
    facts = Facts(profile_id, base["name"], base["current_version"])
    return facts, str(body["upgrade_mode"]), report


def _confirm(request: HttpRequest, profile_id: str) -> HttpResponse:
    """ "Upgrade anyway": the signed body re-sent under the partial's key; no read first."""
    signed = _signed(request, profile_id)
    if signed is None:
        messages.error(request, twostage.EXPIRED_TEXT)
        return redirect(upgrade_url(profile_id))
    facts, mode, report = signed
    token = request.POST.get(twostage.CONFIRM_FIELD, "")
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        edit = client.upgrade_pinned_stories(
            profile_id, ignore_warnings=True, upgrade_mode=_typed(mode), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        refused = _refusal(request, exc, facts)
        if refused is not None:
            return refused
        # E21: the same confirm under the panel; the key kept only on an unknown outcome. The
        # report comes from the token: no label read, no second call.
        return _held_page(
            request,
            None,
            facts,
            mode,
            report,
            again=False,
            token=token,
            key=key if _unknown(exc) else None,
            engine_error=panel_for(exc),
        )
    return _answered(request, client, facts, mode, edit, key, again=True)

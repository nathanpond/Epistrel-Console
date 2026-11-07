"""The World tab's Clock section and its settings page (#243; D1, D3, D6, D7, D8, D9, D14).

``inspect:clock`` (``/stories/<id>/inspect/world/clock/``) renders through
:func:`console.views.inspect.inspect_page` with ``Tab.WORLD``. The section is ungated (D1: the
Engine serves the clock read under every configuration), so it always opens. Reads per render:
the shell's effective-config and story reads, then one ``GET /stories/{id}/clock``. Errors stay
inside the section (convention 10): a 404 is the not-found line, anything else #43's panel
(``erasure_pending`` escapes to #87's held wrapper).

``inspect:clock-settings`` (``world/clock/settings/``, GET/HEAD/POST) is the settings form, below
the Clock section (``current="inspect:clock"``). GET makes the shell's reads and one
``get_clock``; the form is initialised from ``settings`` and carries a signed snapshot of them
(:class:`ClockSnapshot`: the seven patchable members, ``provider_id``/``external_value_name``
and the three read-only settings, display-only). A POST:

- reads the snapshot; a missing or bad one is replaced by one fresh ``get_clock`` (a failed one
  flashes :data:`UNREADABLE` and redirects to the section, no PATCH);
- ``op=add:<prefix>`` / ``op=remove:<prefix>-<n>`` re-renders the posted form, edited and
  unvalidated, with **no** Engine call beyond the shell's reads;
- ``op=save`` validates; an invalid form re-renders (no call, the posted key kept); an empty
  patch flashes "No changes — nothing was sent." (no call); otherwise one ``update_clock`` with
  the page's idempotency key (no expected revision: a configuration write, D8), then
  POST-redirect-GET to the section with "<members>: saved — Configuration revision now <n>",
  always.

D8's "nothing to change" rule: the clock read carries no configuration revision, so D8's
comparison doesn't apply (#270). D8 as amended by #329 drops it for the clock settings, and a
successful save always flashes the saved line, never ``ENGINE_NOTHING_CHANGED``. The Console's
own "untouched sends nothing" check (C9) stays first.

A refusal re-renders the form with every input and row kept. D8's key rule: a fresh key after an
answered refusal, the posted one after an unknown outcome (transport error, ``bad_response``).
Placement (pass 2): a ``pacing_table``/``bands`` field (bare, ``body.``-prefixed or indexed) goes
above that table; then D9's ``world_refusal`` (beside the field it names, else above the form;
``provider_unbound`` with its bindings link above the form); request-validation ``errors[]``
through ``apply_engine_errors``; anything else #43's panel above the form. No gate on the form
or the POST (D1).

``inspect:clock-advance`` (``world/clock/advance/``, GET/HEAD/POST, #252; D1, D6, D8, D9) is the
advance form, below the Clock section. GET makes the shell's reads and one ``get_clock``: "The
clock is at <clock_value>", the external check (an ``external`` clock gets D9's
``clock_external`` sentence in place of the form, which wins over the gate) and the unanchored
hint on ``to`` (``anchor: null`` only; a partial anchor relies on the Engine's
``clock_unanchored``). A failed read is #43's panel above a kept form. The submit is greyed with
D1's reason while ``m6_gate(CLOCK, …)`` is closed; the inputs stay editable. D8's hidden
``expected_revision`` is the shell's ``Story.timeline_revision`` (``ClockBody`` carries none),
absent when the story read failed.

A POST makes no ``get_clock`` (so no "is at" line, external check or hint): ``gate_refusal`` on
``minutes``/``to``/``reason`` first (a refusal re-renders, no write), then the form (invalid:
re-render, no write), then one ``advance_clock`` with the posted ``expected_revision`` and the
page's idempotency key. The result page (``clock_advanced.html``) renders on the POST response
and again at ``inspect:clock-advanced?report=<pk>`` from the #44 row (:func:`advanced_row`).
Refusals follow D8's re-render rule: an answered refusal mints a fresh key and refreshes the
revision from the POST's shell story read; local invalid input and an unknown outcome keep the
posted ones. Placement: D9's ``world_refusal`` (beside the field it names, else above the form;
``clock_backward`` with a "Rewind…" link to the story's History), then request-validation
``errors[]`` through ``apply_engine_errors``, then #43's panel.
"""

import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.clock import (
    advance_url,
    anchor_view,
    clock_off_notice,
    clock_url,
    external_names,
    flag_texts,
    read_only_lines,
    settings_rows,
    settings_url,
)
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import ClockAdvanced, WorldClock
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.clock import (
    EXTERNAL,
    ClockAdvanceForm,
    ClockSettingsForm,
    row_caps,
    saved_text,
)
from console.formset_rows import OP, apply_op, parse_op
from console.held import is_held
from console.ids import new_ulid
from console.inspect import CLOCK, SCHEDULED_STOPS, Tab, gate_refusal, m6_gate
from console.models import EngineOperation
from console.views.inspect import TEMPLATE_KEY, InspectContext, inspect_page
from console.wording import humanised
from console.world import (
    CLOCK_EXTERNAL_TEXT,
    EXPECTED_REVISION_FIELD,
    clock_change_text,
    config_revision_text,
    expected_revision,
    offset_text,
    slot_result,
    world_refusal,
)

CLOCK_TEMPLATE = "console/inspect/clock.html"
SETTINGS_TEMPLATE = "console/inspect/clock_settings.html"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
NO_CHANGES = "No changes — nothing was sent."
UNREADABLE = "The clock's settings could not be read; nothing was sent."
VERB_PHRASE = "change the clock's settings"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.inspect.clock_settings"
BAD_RESPONSE = "bad_response"
# The label of a refusal's link (D9's bindings page, convention 24's Config; a paused story's
# check otherwise).
REFUSAL_LINKS: Mapping[str, str] = {"provider_unbound": "Story bindings", "module_off": "Config"}

# The snapshot's members: the patchable ones, the external clock's two names, and the read-only
# three (display-only, never diffed).
PATCHABLE = (
    "mode",
    "pacing_table",
    "max_unprompted_skip_minutes",
    "stop_at_due_events",
    "time_awareness",
    "bands",
    "calendar",
)
SNAPSHOT_MEMBERS = (
    *PATCHABLE,
    "provider_id",
    "external_value_name",
    "grace_minutes",
    "time_statement_tolerance_minutes",
    "elapsed_tolerance_factor",
)
# A table field an Engine error names: bare, ``body.``-prefixed, or indexed (``.2.1``/``[2][1]``).
_TABLE_FIELD = re.compile(r"^(?:body\.)?(pacing_table|bands)(?:[.\[].*)?$")


# --- the Clock section --------------------------------------------------------------------------


def _clock_read_error(ctx: InspectContext, exc: EngineClientError) -> dict[str, Any]:
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if isinstance(exc, EngineError) and exc.http_status == 404:
        return {"not_found": NOT_FOUND}
    return {"clock_error": panel_for(exc)}


def _clock(ctx: InspectContext) -> dict[str, Any]:
    """The section: one ``get_clock``, then now, level, mode, anchor and settings."""
    context: dict[str, Any] = {
        "settings_url": settings_url(ctx.story_id),
        "advance_url": advance_url(ctx.story_id),
        "advance_gate": m6_gate(CLOCK, ctx.effective),  # D1: the advance's own gate
    }
    try:
        clock = ctx.client.get_clock(ctx.story_id)
    except EngineClientError as exc:
        context.update(_clock_read_error(ctx, exc))
        return context
    stops = m6_gate(SCHEDULED_STOPS, ctx.effective)
    provider, value_name = external_names(clock.settings)
    context.update(
        clock=clock,
        offset=f"{clock.offset:,}",
        level=humanised(clock.level),
        flags=flag_texts(ctx.effective),
        off_notice=clock_off_notice(ctx.effective),
        mode=humanised(clock.mode),
        external=clock.mode == EXTERNAL,
        provider_id=provider,
        external_value_name=value_name,
        anchor=anchor_view(clock.anchor, ctx.story_id),
        settings_rows=settings_rows(clock.settings, stops_closed=stops),
    )
    return context


@require_safe
def clock(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:clock``, the world clock and its settings (#243)."""
    return inspect_page(request, story_id, Tab.WORLD, CLOCK_TEMPLATE, _clock)


# --- the settings page --------------------------------------------------------------------------


@dataclass(frozen=True)
class ClockSnapshot:
    """The settings the form rendered from, signed into the page (story id included)."""

    story_id: str
    settings: dict[str, Any]

    @classmethod
    def of(cls, story_id: str, clock: WorldClock) -> ClockSnapshot:
        return cls(
            story_id, {name: getattr(clock.settings, name, None) for name in SNAPSHOT_MEMBERS}
        )

    def signed(self) -> str:
        return signing.dumps(
            {"story_id": self.story_id, "settings": self.settings},
            salt=SNAPSHOT_SALT,
            compress=True,
        )

    @classmethod
    def unsigned(cls, value: str | None, story_id: str) -> ClockSnapshot | None:
        try:
            raw = signing.loads(value or "", salt=SNAPSHOT_SALT)
        except signing.BadSignature:
            return None
        if not isinstance(raw, dict) or raw.get("story_id") != story_id:
            return None
        settings = raw.get("settings")
        if not isinstance(settings, dict) or any(m not in settings for m in SNAPSHOT_MEMBERS):
            return None
        return cls(story_id, settings)


def _page(
    ctx: InspectContext,
    snap: ClockSnapshot,
    form: ClockSettingsForm,
    key: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "form": form,
        "snapshot": snap.signed(),
        "idempotency_key": key,
        "action_url": settings_url(ctx.story_id),
        "back_url": clock_url(ctx.story_id),
        "read_only": read_only_lines(snap.settings),
        **extra,
    }


def _table_member(field: Any) -> str | None:
    match = _TABLE_FIELD.match(field) if isinstance(field, str) else None
    return match.group(1) if match else None


def _route_tables(exc: EngineError, form: ClockSettingsForm) -> EngineError | None:
    """Put a table's errors above that table; the error without them (``None``: nothing left)."""
    details = dict(exc.details) if isinstance(exc.details, Mapping) else {}
    member = _table_member(details.get("field"))
    if member is not None:
        form.table_error(member, exc.message)
        return None
    listed = details.get("errors")
    if not isinstance(listed, list):
        return exc
    kept = []
    for item in listed:
        named = _table_member(item.get("field")) if isinstance(item, dict) else None
        if named is None:
            kept.append(item)
            continue
        message = item.get("message")
        form.table_error(named, message if isinstance(message, str) else exc.message)
    if not kept:
        return None
    details["errors"] = kept
    return EngineError(exc.code, exc.message, details, exc.http_status, retry_after=exc.retry_after)


def _refused(
    ctx: InspectContext,
    exc: EngineClientError,
    snap: ClockSnapshot,
    form: ClockSettingsForm,
    posted_key: str,
) -> dict[str, Any]:
    """The form again, every input kept, with the refusal placed (module docstring)."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )
    if unknown or not isinstance(exc, EngineError):
        return _page(ctx, snap, form, posted_key, write_error=panel_for(exc))
    key = new_ulid()  # D8: an answered refusal is a new attempt
    rest = _route_tables(exc, form) if exc.http_status == 422 else exc
    if rest is None:
        return _page(ctx, snap, form, key)
    refusal = world_refusal(rest, ctx.story_id, VERB_PHRASE, ctx.effective)
    if refusal is not None:
        if refusal.field in form.fields and not refusal.url:
            form.add_error(refusal.field, refusal.text)
            return _page(ctx, snap, form, key)
        return _page(
            ctx,
            snap,
            form,
            key,
            refusal=refusal.text,
            refusal_url=refusal.url,
            refusal_link=REFUSAL_LINKS.get(rest.code, "See the check"),
        )
    if rest.http_status == 422 and apply_engine_errors(rest, form, formsets=form.table_rows()):
        return _page(ctx, snap, form, key)
    return _page(ctx, snap, form, key, write_error=panel_for(exc))


def _posted_snapshot(ctx: InspectContext) -> ClockSnapshot | HttpResponse:
    found = ClockSnapshot.unsigned(ctx.request.POST.get(SNAPSHOT_FIELD), ctx.story_id)
    if found is not None:
        return found
    try:
        return ClockSnapshot.of(ctx.story_id, ctx.client.get_clock(ctx.story_id))
    except EngineClientError as exc:
        if is_held(exc):
            raise
        log_engine_error(ctx.request, exc)
        messages.error(ctx.request, UNREADABLE)
        return redirect(clock_url(ctx.story_id))


def _settings(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    if request.method != "POST":
        try:
            read = ctx.client.get_clock(ctx.story_id)
        except EngineClientError as exc:
            return _clock_read_error(ctx, exc)
        snap = ClockSnapshot.of(ctx.story_id, read)
        form = ClockSettingsForm(original=snap.settings)
        return _page(ctx, snap, form, new_ulid())
    posted = _posted_snapshot(ctx)
    if isinstance(posted, HttpResponse):
        return posted
    snap = posted
    key = posted_idempotency_key(request)
    op = parse_op(request.POST.get(OP))
    if op.edits_rows:
        data: QueryDict = apply_op(request.POST, op, max_rows=row_caps(snap.settings))
        edited = ClockSettingsForm(data, original=snap.settings)
        edited.quiet()
        return _page(ctx, snap, edited, key)
    form = ClockSettingsForm(request.POST, original=snap.settings)
    if not form.is_valid():
        return _page(ctx, snap, form, key)
    patch = form.patch(snap.settings)
    if not patch:
        messages.info(request, NO_CHANGES)
        return redirect(clock_url(ctx.story_id))
    try:
        answer = ctx.client.update_clock(ctx.story_id, patch, idempotency_key=key)
    except EngineClientError as exc:
        return _refused(ctx, exc, snap, form, key)
    # D8 (amended by #329): no base to compare against, so the saved flash always shows.
    messages.success(
        request, f"{saved_text(patch)}: saved — {config_revision_text(answer.config_revision)}"
    )
    return redirect(clock_url(ctx.story_id))


@require_http_methods(["GET", "HEAD", "POST"])
def clock_settings(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:clock-settings``: the settings form, then ``PATCH /stories/{id}/clock``."""
    return inspect_page(
        request, story_id, Tab.WORLD, SETTINGS_TEMPLATE, _settings, current="inspect:clock"
    )


# --- the advance page (#252) --------------------------------------------------------------------

ADVANCE_TEMPLATE = "console/inspect/clock_advance.html"
ADVANCED_TEMPLATE = "console/inspect/clock_advanced.html"
ADVANCE_KIND = "advance_clock"
ADVANCE_VERB = "advance the clock"
REPORT_PARAM = "report"
REWIND_LINK = "Rewind…"
NO_REASON = "—"
# D1: the advance is gated on the clock at ``prompt_only`` (every field the form carries).
ADVANCE_GATED: Mapping[str, tuple[str, str | None]] = {
    "minutes": (CLOCK, "prompt_only"),
    "to": (CLOCK, "prompt_only"),
    "reason": (CLOCK, "prompt_only"),
}


def advance_page_url(story_id: str) -> str:
    return reverse("inspect:clock-advance", args=[story_id])


def advanced_url(story_id: str, pk: int) -> str:
    """``inspect:clock-advanced?report=<pk>``."""
    return f"{reverse('inspect:clock-advanced', args=[story_id])}?{REPORT_PARAM}={pk}"


def history_url(story_id: str) -> str:
    """The story's History (where the rewind controls live); "" while it doesn't resolve."""
    try:
        return reverse("stories:history", args=[story_id])
    except NoReverseMatch:
        return ""


def _story_revision(ctx: InspectContext) -> str:
    """D8: the shell's story read's ``timeline_revision`` ("" when that read failed)."""
    revision = ctx.story.timeline_revision if ctx.story is not None else None
    return str(revision) if isinstance(revision, int) and not isinstance(revision, bool) else ""


def advanced_row(pk: object, story_id: str) -> tuple[EngineOperation, ClockAdvanced] | None:
    """The #44 row ``pk`` and its answer when it is an ``ok`` ``advance_clock`` of this story
    whose stored response parses as ``ClockAdvanced``; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    if (
        row.kind != ADVANCE_KIND
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    parsed = ClockAdvanced.from_json(row.response)
    if parsed is None:
        return None
    return row, parsed


def _stored_reason(row: EngineOperation) -> str | None:
    stored = row.request if isinstance(row.request, dict) else {}
    body = stored.get("body")
    reason = body.get("reason") if isinstance(body, dict) else None
    return reason if isinstance(reason, str) and reason else None


def _advanced(
    ctx: InspectContext,
    result: ClockAdvanced,
    row: EngineOperation | None,
    *,
    reason: str | None,
    raw: Any,
) -> dict[str, Any]:
    """The result page's context (on the POST response, and for ``?report=``)."""
    story_id = ctx.story_id
    back = clock_url(story_id)
    return {
        TEMPLATE_KEY: ADVANCED_TEMPLATE,
        "advanced": result,
        "change_line": clock_change_text(result.before, result.after),
        "advanced_by": offset_text(result.after.offset - result.before.offset),
        "reason": reason or NO_REASON,
        "slot": slot_result(story_id, result, row, back),
        "replayed": row is not None and row.replayed,
        "again_url": advance_page_url(story_id),
        "back_url": back,
        "report_url": advanced_url(story_id, row.pk) if row is not None else None,
        "raw_answer": raw,
    }


def _advanced_report(ctx: InspectContext) -> dict[str, Any]:
    found = advanced_row(ctx.request.GET.get(REPORT_PARAM), ctx.story_id)
    if found is None:
        return {"missing": NOT_FOUND, "back_url": clock_url(ctx.story_id)}
    row, result = found
    return _advanced(ctx, result, row, reason=_stored_reason(row), raw=row.response)


@require_safe
def clock_advanced(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:clock-advanced?report=<pk>``: an advance's result page again, from the #44
    row."""
    return inspect_page(
        request,
        story_id,
        Tab.WORLD,
        ADVANCED_TEMPLATE,
        _advanced_report,
        current="inspect:clock",
    )


def _advance_refused(
    ctx: InspectContext,
    exc: EngineClientError,
    form: ClockAdvanceForm,
    page: dict[str, Any],
) -> dict[str, Any]:
    """A refused advance: the form again (D8's key and revision rule), the refusal placed."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )
    if unknown or not isinstance(exc, EngineError):
        page["write_error"] = panel_for(exc)  # the outcome is unknown: the posted key and revision
        return page
    page["idempotency_key"] = new_ulid()  # D8: an answered refusal is a new attempt
    revision = _story_revision(ctx)
    if revision:
        page["expected_revision"] = revision
    refusal = world_refusal(exc, ctx.story_id, ADVANCE_VERB, ctx.effective)
    if refusal is not None:
        url, link = refusal.url, REFUSAL_LINKS.get(exc.code, "See the check")
        if exc.code == "clock_backward":
            url, link = history_url(ctx.story_id), REWIND_LINK
        if refusal.field in form.fields and not refusal.url:
            form.add_error(refusal.field, refusal.text)
            if exc.code == "clock_backward":
                page["field_link"] = {"field": refusal.field, "url": url, "label": link}
        else:
            page.update(
                refusal=refusal.text,
                refusal_url=url,
                refusal_link=link if url or exc.code == "clock_backward" else "",
            )
        return page
    if exc.http_status == 422 and apply_engine_errors(exc, form):
        return page
    page["write_error"] = panel_for(exc)
    return page


def _advance(ctx: InspectContext) -> dict[str, Any]:
    request = ctx.request
    story_id = ctx.story_id
    page: dict[str, Any] = {
        "back_url": clock_url(story_id),
        "action_url": advance_page_url(story_id),
        "gate": m6_gate(CLOCK, ctx.effective),
        "idempotency_key": new_ulid(),  # #43: every render mints a fresh key
        "expected_revision": _story_revision(ctx),
    }
    if request.method != "POST":
        unanchored = False
        try:
            clock = ctx.client.get_clock(story_id)
        except EngineClientError as exc:
            if is_held(exc):
                raise
            log_engine_error(request, exc)
            page["read_error"] = panel_for(exc)
        else:
            if clock.mode == EXTERNAL:
                page["external"] = CLOCK_EXTERNAL_TEXT
                return page
            page["clock"] = clock
            unanchored = clock.anchor is None
        page["form"] = ClockAdvanceForm(unanchored=unanchored)
        return page
    form = ClockAdvanceForm(request.POST)
    page.update(
        form=form,
        idempotency_key=posted_idempotency_key(request),
        expected_revision=request.POST.get(EXPECTED_REVISION_FIELD, ""),
    )
    valid = form.is_valid()
    refused = gate_refusal(request.POST, ctx.effective, ADVANCE_GATED)
    if refused is not None:
        page["refusal"] = refused.reason
        return page
    if not valid:
        return page
    body = form.body()
    try:
        result = ctx.client.advance_clock(
            story_id,
            reason=body["reason"],
            minutes=body.get("minutes"),
            to=body.get("to"),
            expected_revision=expected_revision(request.POST),
            idempotency_key=posted_idempotency_key(request),
        )
    except EngineClientError as exc:
        return _advance_refused(ctx, exc, form, page)
    pk = getattr(ctx.client, "last_operation_id", None)
    row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
    raw = row.response if row is not None else dataclasses.asdict(result)
    return _advanced(ctx, result, row, reason=body["reason"], raw=raw)


@require_http_methods(["GET", "HEAD", "POST"])
def clock_advance(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:clock-advance``: the advance form, then ``POST /stories/{id}/clock/advance`` and
    the result page on the POST response (#252)."""
    return inspect_page(
        request, story_id, Tab.WORLD, ADVANCE_TEMPLATE, _advance, current="inspect:clock"
    )

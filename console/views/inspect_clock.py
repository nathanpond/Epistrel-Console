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
  POST-redirect-GET to the section with "<members>: saved — Configuration revision now <n>", or
  D8's ``ENGINE_NOTHING_CHANGED`` when the answer's ``config_revision`` equals the shell's.

The shell's effective-config read carries no ``config_revision`` at the recorded schema (the
Engine's ``EffectiveConfig`` has none), so the base revision is unknown and the usual saved flash
shows (D8's "base revision unknown" rule); :func:`base_revision` reads it should the Engine add
it.

A refusal re-renders the form with every input and row kept. D8's key rule: a fresh key after an
answered refusal, the posted one after an unknown outcome (transport error, ``bad_response``).
Placement (pass 2): a ``pacing_table``/``bands`` field (bare, ``body.``-prefixed or indexed) goes
above that table; then D9's ``world_refusal`` (beside the field it names, else above the form;
``provider_unbound`` with its bindings link above the form); request-validation ``errors[]``
through ``apply_engine_errors``; anything else #43's panel above the form. No gate on the form
or the POST (D1).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect
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
from console.engine.models import WorldClock
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.clock import (
    EXTERNAL,
    ClockSettingsForm,
    row_caps,
    saved_text,
)
from console.formset_rows import OP, apply_op, parse_op
from console.held import is_held
from console.ids import new_ulid
from console.inspect import SCHEDULED_STOPS, Tab, m6_gate
from console.views.inspect import InspectContext, inspect_page
from console.wording import humanised
from console.world import (
    ENGINE_NOTHING_CHANGED,
    config_revision_text,
    nothing_changed,
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


def base_revision(ctx: InspectContext) -> Any:
    """The shell's effective-config ``config_revision`` (D8's base), ``None`` while the Engine's
    ``EffectiveConfig`` carries none."""
    return getattr(ctx.config, "config_revision", None)


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
    if nothing_changed(answer.config_revision, base_revision(ctx)):
        messages.info(request, ENGINE_NOTHING_CHANGED)  # D8
    else:
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

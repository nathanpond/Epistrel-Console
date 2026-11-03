"""Change a State's definition (#265; D1, D8, D9, D14, D18), below the World tab's States section.

``inspect:state-definition`` (``/stories/<id>/inspect/world/state/<state_name:name>/definition/``,
GET/HEAD/POST) renders through :func:`console.views.inspect.inspect_page` with ``Tab.WORLD`` and
``current="inspect:states"``. The shell's own section gate is off (``gate_section=False``) so
that ``build`` checks ``module_gate(STATES, ctx.effective)`` itself (D1): with the gate closed a
GET renders the inline greyed panel, and a POST the reason (with the Config link) above the kept
inputs (the posted key and snapshot kept, #261's rule). Neither makes a State read or a
``PATCH``.

Reads per render, GET and POST: the shell's effective-config and story reads, then one
``get_state(story_id, name, viewpoint="omniscient")``; a 404 is convention 10's not-found page
with no form. The page shows the read's type, scope and owner, D18's
:data:`console.states.DEFINITION_UNREADABLE` notice, and "Last recorded definition" (the local
#44 query :func:`console.states.last_recorded_definition`; no Engine call).

The form (:class:`console.forms.states.StateDefinitionForm`) pre-fills only Visibility and Unit,
signed into ``snapshot``; a POST checks the snapshot first (``SNAPSHOT_INVALID`` alone, no
call), then the form, then the patch: an empty one makes **no** call and re-renders with
``NOTHING_CHANGED`` (C9, D14). Otherwise one ``update_state_definition`` with the page's
idempotency key (no expected revision, ``MUTATION_TIMEOUT_S``: a configuration write, D8), then
POST-redirect-GET to ``inspect:state`` flashing "<Member>: saved" per patched member and
"Configuration revision now <n>", or D8's ``ENGINE_NOTHING_CHANGED`` when the answer's
``config_revision`` equals the shell's (:func:`base_revision`; the Engine's ``EffectiveConfig``
carries none today, so the base is unknown and the saved lines show: #270). A 200 whose answer
doesn't parse flashes only :data:`UNREADABLE_SAVED`.

Refusals re-render with every input and the snapshot kept, by D8's key rule (a fresh key after an
answered refusal, the posted one after an unknown outcome). Order: this story's own wordings
(``rules_kind`` on Rules, ``unknown`` above the form), then ``apply_engine_errors`` with
:data:`ALIASES` for any other ``validation_error`` but ``immutable_field``/``reserved_state``,
then D9's ``world_refusal`` (verb phrase ``DEFINITION_VERB``, the read State's type and unit),
then #43's panel. A 404 is the not-found page; 503 ``erasure_pending`` escapes to #87's held
wrapper.
"""

from collections.abc import Mapping
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from console.cast import OMNISCIENT
from console.confirm import KEY_FIELD, posted_idempotency_key
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import StoryState
from console.engine_errors import EMPTY, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.states import (
    CONSTRAINT_MEMBERS,
    INTERPRETIVE_NOTE,
    NOTHING_CHANGED,
    RULES_KIND,
    SNAPSHOT_FIELD,
    SNAPSHOT_INVALID,
    StateDefinitionForm,
    read_definition_snapshot,
    sign_definition_snapshot,
)
from console.held import is_held
from console.ids import new_ulid
from console.inspect import STATES, Tab, is_module_off, module_gate
from console.redaction import MASK
from console.states import (
    SCOPE_LABELS,
    definition_unreadable_html,
    definition_url,
    last_recorded_definition,
    no_recorded_definition_text,
    state_url,
)
from console.views.inspect import InspectContext, inspect_page
from console.views.inspect_clock import base_revision
from console.views.inspect_state_declare import DefinitionRow, definition_rows
from console.wording import humanised
from console.world import (
    DEFINITION_VERB,
    ENGINE_NOTHING_CHANGED,
    config_revision_text,
    nothing_changed,
    world_refusal,
)

TEMPLATE = "console/inspect/state_definition.html"
SECTION = "inspect:states"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
BAD_RESPONSE = "bad_response"
HEADING = "Change {name}'s definition"
SAVED = "{member}: saved"
UNREADABLE_SAVED = "Saved — the Engine's answer couldn't be read"
UNKNOWN_MEMBER = "{field} isn't a State definition member"
AUTHORIZATION_REDACTED = "Hidden in the Console's log (redacted)"
# The label of a refusal's link: convention 24's Config, else the paused story's check.
REFUSAL_LINKS: Mapping[str, str] = {"module_off": "Config"}
PAUSED_LINK = "See the check"
# The ``validation_error`` reasons D9's ``world_refusal`` words (not ``apply_engine_errors``).
WORDED_REASONS = frozenset({"immutable_field", "reserved_state"})
# The Engine's dotted definition paths → this form's fields. A plain member (``unit``,
# ``aliases``, ``rules``, …) lands on its same-named field without one. ``initial_value`` is the
# new constraints' doing, so it goes on Constraints.
ALIASES: Mapping[str, str] = {
    **{f"constraints.{m}": "constraints" for m in CONSTRAINT_MEMBERS},
    "initial_value": "constraints",
    "authorization.sources": "authorization",
}


def _recorded(name: str, story_id: str) -> dict[str, Any]:
    """ "Last recorded definition": its heading and rows, or the "none recorded" line."""
    found = last_recorded_definition(story_id, name)
    if found is None:
        return {"recorded_none": no_recorded_definition_text(name)}
    rows = [
        DefinitionRow(row.member, row.label, AUTHORIZATION_REDACTED)
        if row.member == "authorization" and found.definition.get("authorization") == MASK
        else row
        for row in definition_rows(found.definition)
    ]
    return {"recorded_heading": found.heading, "definition_rows": rows}


def _identity(state: StoryState) -> list[tuple[str, str]]:
    """C3: the read's type, scope and owner."""
    scope = state.scope if isinstance(state.scope, str) else ""
    return [
        ("Type", humanised(state.type) if state.type else EMPTY),
        ("Scope", SCOPE_LABELS.get(scope) or (humanised(scope) if scope else EMPTY)),
        ("Owner", humanised(state.owner) if state.owner else EMPTY),
    ]


def _place(form: StateDefinitionForm, refusal: Any, code: str, page: dict[str, Any]) -> None:
    if refusal.field in form.fields and not refusal.url:
        form.add_error(refusal.field, refusal.text)
        return
    page.update(
        refusal=refusal.text,
        refusal_url=refusal.url,
        refusal_link=REFUSAL_LINKS.get(code, PAUSED_LINK) if refusal.url else "",
    )


def _refused(
    ctx: InspectContext,
    exc: EngineClientError,
    state: StoryState,
    form: StateDefinitionForm,
    page: dict[str, Any],
) -> dict[str, Any]:
    """A refused change: the form again (D8's key rule), the refusal placed."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and (exc.code == BAD_RESPONSE or exc.http_status >= 500)
    )
    if unknown or not isinstance(exc, EngineError):
        page["write_error"] = panel_for(exc)  # the outcome is unknown: the posted key
        return page
    if exc.http_status == 404:
        return {
            "state_name": page["state_name"],
            "missing": NOT_FOUND,
            "back_url": page["back_url"],
        }
    page["idempotency_key"] = new_ulid()  # D8: an answered refusal is a new attempt
    _rekey(form, page["idempotency_key"])
    details = exc.details if isinstance(exc.details, Mapping) else {}
    reason = details.get("reason")
    if exc.code == "validation_error":
        if reason == "rules_kind":
            form.add_error("rules", RULES_KIND)  # convention 24: this story's wordings first
            return page
        if reason == "unknown":
            field = details.get("field")
            shown = field.removeprefix("body.") if isinstance(field, str) and field else EMPTY
            page["refusal"] = UNKNOWN_MEMBER.replace("{field}", shown)
            return page
        if reason not in WORDED_REASONS and apply_engine_errors(exc, form, aliases=ALIASES):
            return page
    refusal = world_refusal(
        exc,
        ctx.story_id,
        DEFINITION_VERB,
        ctx.effective,
        field_map=ALIASES,
        value_type=state.type,
        unit=state.unit,
    )
    if refusal is not None:
        _place(form, refusal, exc.code, page)
        return page
    page["write_error"] = panel_for(exc)
    return page


def _rekey(form: StateDefinitionForm, key: str) -> None:
    """Rebind ``form`` to its posted data with the fresh idempotency key (errors kept)."""
    data = QueryDict(mutable=True)
    data.update(form.data)
    data[KEY_FIELD] = key
    form.data = data


def _saved(
    request: HttpRequest, ctx: InspectContext, patch: Mapping[str, Any], answer: Any
) -> None:
    if nothing_changed(answer.config_revision, base_revision(ctx)):
        messages.info(request, ENGINE_NOTHING_CHANGED)  # D8
        return
    for member in patch:
        messages.success(request, SAVED.replace("{member}", humanised(member)))
    messages.success(request, config_revision_text(answer.config_revision))


def _definition(name: str) -> Any:
    def build(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
        request = ctx.request
        story_id = ctx.story_id
        posted = request.method == "POST"
        page: dict[str, Any] = {
            "state_name": name,
            "heading": HEADING.replace("{name}", name),
            "back_url": state_url(story_id, name),
            "action_url": definition_url(story_id, name),
            "unreadable_notice": definition_unreadable_html(),
            "interpretive_note": INTERPRETIVE_NOTE,
        }
        closed = module_gate(STATES, ctx.effective)
        if closed is not None:
            if not posted:
                page["gate_unavailable"] = closed
            else:
                # D8: a gate refusal is local invalid input: no read, no write, inputs kept.
                signed = request.POST.get(SNAPSHOT_FIELD, "")
                page["form"] = StateDefinitionForm(
                    snapshot=read_definition_snapshot(signed, name),
                    signed_snapshot=signed,
                    idempotency_key=posted_idempotency_key(request),
                    initial=request.POST.dict(),
                )
                page["refusal"] = closed.reason
                page["refusal_url"] = reverse("stories:config", args=[story_id])
                page["refusal_link"] = REFUSAL_LINKS["module_off"]
            return page
        try:
            view = ctx.client.get_state(story_id, name, viewpoint=OMNISCIENT)
        except EngineClientError as exc:
            if is_held(exc) or is_module_off(exc):
                raise
            log_engine_error(request, exc)
            if isinstance(exc, EngineError) and exc.http_status == 404:
                page["missing"] = NOT_FOUND
            else:
                page["read_error"] = panel_for(exc)
            return page
        state = view.state
        page.update(identity=_identity(state), **_recorded(name, story_id))
        fresh = {"visibility": state.visibility, "unit": state.unit or None}
        fresh_signed = sign_definition_snapshot(name, state.visibility, state.unit)
        if not posted:
            page["form"] = StateDefinitionForm(snapshot=fresh, signed_snapshot=fresh_signed)
            return page
        key = posted_idempotency_key(request)
        page["idempotency_key"] = key
        signed = request.POST.get(SNAPSHOT_FIELD, "")
        snapshot = read_definition_snapshot(signed, name)
        if snapshot is None:
            # Checked first: SNAPSHOT_INVALID alone (no field errors), the posted key, no call.
            page["form"] = StateDefinitionForm(
                snapshot=fresh,
                signed_snapshot=fresh_signed,
                idempotency_key=key,
                initial=request.POST.dict(),
            )
            page["notice_error"] = SNAPSHOT_INVALID
            return page
        form = StateDefinitionForm(
            request.POST, snapshot=snapshot, signed_snapshot=signed, idempotency_key=key
        )
        page["form"] = form
        if not form.is_valid():
            return page
        patch = form.patch
        if not patch:
            page["nothing_changed"] = NOTHING_CHANGED  # C9: no call
            return page
        try:
            answer = ctx.client.update_state_definition(story_id, name, patch, idempotency_key=key)
        except EngineClientError as exc:
            if (
                isinstance(exc, EngineError)
                and exc.code == BAD_RESPONSE
                and 200 <= exc.http_status < 300
            ):
                log_engine_error(request, exc)
                messages.warning(request, UNREADABLE_SAVED)
                return redirect(state_url(story_id, name))
            return _refused(ctx, exc, state, form, page)
        _saved(request, ctx, patch, answer)
        return redirect(state_url(story_id, name))

    return build


@require_http_methods(["GET", "HEAD", "POST"])
def state_definition(request: HttpRequest, story_id: str, name: str) -> HttpResponse:
    """``inspect:state-definition``: the Change definition form, then ``PATCH
    /stories/{id}/states/{name}/definition`` and POST-redirect-GET to the State (#265)."""
    return inspect_page(
        request,
        story_id,
        Tab.WORLD,
        TEMPLATE,
        _definition(name),
        current=SECTION,
        gate_section=False,
    )

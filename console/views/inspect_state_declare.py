"""Declare a State between turns (#261; D1, D5, D7, D8, D9, D14), below the World tab's States
section.

``inspect:state-new`` (``/stories/<id>/inspect/world/states/new/``, GET/HEAD/POST) renders through
:func:`console.views.inspect.inspect_page` with ``Tab.WORLD`` and ``current="inspect:states"``
(D3). The shell's own section gate is off (``gate_section=False``) so that ``build`` checks
``module_gate(STATES, ctx.effective)`` itself (D1): with the gate closed a GET renders the inline
greyed panel, and a POST re-renders the posted inputs (no field errors; the posted key and
revision kept) with the reason above the form. Neither makes a ``POST …/states``.

Reads per render: the shell's effective-config and story reads only. D8's hidden
``expected_revision`` is the story read's ``timeline_revision`` (blank when that read failed, so
no header is sent).

A valid POST calls ``declare_state`` once with :attr:`StateDeclarationForm.definition`,
``expected_revision`` from the **posted** hidden field and the page's idempotency key (the client
waits ``turn_timeout(None)``). The result page (``state_declared.html``) renders on the POST
response and again at ``inspect:state-declared?report=<pk>`` from the #44 row (a ``declare_state``
of this story, outcome ``ok``, whose response parses as ``StateDeclared``); that page reads
nothing beyond the shell and is not gated.

Refusals are :func:`declaration_refusal`'s (#266 imports it with its own verb phrase).
"""

import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.confirm import KEY_FIELD, posted_idempotency_key
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import ProposalApproved, StateDeclared
from console.engine_errors import EMPTY, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors, engine_field_errors
from console.forms.states import (
    BOXES,
    CONSTRAINT_MEMBERS,
    STATE_NAME,
    StateDeclarationForm,
)
from console.held import is_held
from console.ids import new_ulid
from console.inspect import STATES, Tab, module_gate
from console.models import EngineOperation
from console.states import EXTERNAL_OWNER, set_url, state_url, states_url
from console.views.inspect import TEMPLATE_KEY, InspectContext, inspect_page
from console.wording import humanised
from console.world import (
    EXPECTED_REVISION_FIELD,
    UNDEFINED,
    expected_revision,
    slot_result,
    state_value_text,
    world_refusal,
)

DECLARE_TEMPLATE = "console/inspect/state_declare.html"
DECLARED_TEMPLATE = "console/inspect/state_declared.html"
SECTION = "inspect:states"
WRITE_KIND = "declare_state"
REPORT_PARAM = "report"
VERB_PHRASE = "declare a State"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
BAD_RESPONSE = "bad_response"
DECLARED_HEADING = "Declared {name}"
STORY_OWNER = "story"

ENTITY_UNKNOWN = "That entity isn't in this story"
PROVIDER_UNKNOWN = "No provider is registered as “{provider}”"
VALUE_UNKNOWN = (
    "{provider} owns no value named “{name}” — an external State takes its provider's value of "
    "its own name"
)

# The label of a refusal's link: convention 24's Config, else the paused story's check.
REFUSAL_LINKS: Mapping[str, str] = {"module_off": "Config"}
PAUSED_LINK = "See the check"
# The ``validation_error`` reasons D9's ``world_refusal`` words itself (before
# ``apply_engine_errors``, so ``reserved_state`` reads D9's sentence).
WORDED_REASONS = frozenset(
    {"reserved_state", "immutable_field", "event_settled", "unknown_character", "unknown_place"}
)
DEFINITION_PREFIX = "definition."
# Engine request paths (``body.`` already stripped) → this form's fields.
ALIASES: Mapping[str, str] = {
    **{f"definition.constraints.{m}": f"constraints_{m}" for m in CONSTRAINT_MEMBERS},
    **{f"constraints.{m}": f"constraints_{m}" for m in CONSTRAINT_MEMBERS},
    **{
        f"definition.{m}": m
        for m in (
            "name",
            "type",
            "scope",
            "initial_value",
            "aliases",
            "unit",
            "entity_id",
            "visibility",
            "owner",
            "provider_id",
            "silent",
            "rules",
            "authorization",
            "transfer",
            "reservation_expiry_minutes",
        )
    },
}


def new_url(story_id: str) -> str:
    return reverse("inspect:state-new", args=[story_id])


def declared_url(story_id: str, pk: int) -> str:
    """``inspect:state-declared?report=<pk>``."""
    return f"{reverse('inspect:state-declared', args=[story_id])}?{REPORT_PARAM}={pk}"


def providers_url() -> str | None:
    """Engine > Providers (``engine:providers``, #240), ``None`` while unregistered (D19)."""
    try:
        return reverse("engine:providers")
    except NoReverseMatch:
        return None


def _story_revision(ctx: InspectContext) -> int | None:
    """D8: the shell's story read's ``timeline_revision`` (``None`` when that read failed)."""
    revision = ctx.story.timeline_revision if ctx.story is not None else None
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else None


# --- the stored definition (``_state_definition.html``; #266 reuses it) ----------------------

# The members in the partial's order; any other member follows in the answer's order.
DEFINITION_ORDER = (
    "name",
    "type",
    "scope",
    "visibility",
    "owner",
    "provider_id",
    "external_value_name",
    "unit",
    "aliases",
    "entity_id",
    "constraints",
    "initial_value",
    "silent",
    "reservation_expiry_minutes",
    "rules",
    "authorization",
    "transfer",
    "created_by",
)
ENUM_MEMBERS = frozenset({"type", "scope", "visibility", "owner", "created_by"})
OBJECT_MEMBERS = frozenset({"constraints", "rules", "authorization", "transfer"})
EXTERNAL_VALUE_NOTE = "{value} (the provider's value of this name)"


@dataclasses.dataclass(frozen=True)
class DefinitionRow:
    """One ``<dt>``/``<dd>`` of ``_state_definition.html``. ``kind``: ``text``, ``code`` (an
    id), ``json`` (``value`` through ``pretty_json``) or ``undefined`` (D5's styled
    "undefined")."""

    member: str
    label: str
    text: str
    kind: str = "text"
    value: Any = None


def _scalar_text(value: Any) -> str:
    if value is None or value == "":
        return EMPTY
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _definition_row(member: str, definition: Mapping[str, Any]) -> DefinitionRow:
    label = humanised(member)
    if member not in definition:
        return DefinitionRow(member, label, EMPTY)
    value = definition[member]
    if member in ENUM_MEMBERS and isinstance(value, str) and value:
        return DefinitionRow(member, label, humanised(value))
    if member == "aliases" and isinstance(value, list):
        names = [str(a) for a in value]
        return DefinitionRow(member, label, ", ".join(names) if names else EMPTY)
    if member == "entity_id" and isinstance(value, str) and value:
        return DefinitionRow(member, label, value, "code")
    if member == "initial_value":
        if value is None:
            return DefinitionRow(member, label, UNDEFINED, "undefined")
        unit = definition.get("unit")
        state_type = definition.get("type")
        return DefinitionRow(
            member,
            label,
            state_value_text(
                value,
                state_type if isinstance(state_type, str) else None,
                unit if isinstance(unit, str) and unit else None,
            ),
        )
    if member == "external_value_name" and isinstance(value, str) and value:
        if definition.get("owner") == EXTERNAL_OWNER:
            return DefinitionRow(member, label, EXTERNAL_VALUE_NOTE.replace("{value}", value))
        return DefinitionRow(member, label, value)
    if isinstance(value, dict | list):
        if not value:
            return DefinitionRow(member, label, EMPTY)
        return DefinitionRow(member, label, "", "json", value)
    return DefinitionRow(member, label, _scalar_text(value))


def definition_rows(definition: Mapping[str, Any]) -> list[DefinitionRow]:
    """``_state_definition.html``'s rows for a stored definition (the Engine's full
    ``document()``): :data:`DEFINITION_ORDER`, then any other member in the answer's order.
    Member names and Engine enums humanised (C3), the initial value through D5 with the
    definition's unit (a present ``null`` reads "undefined"), objects through ``pretty_json``
    (an empty one, empty aliases and a missing or ``null`` member read "—")."""
    members = list(DEFINITION_ORDER) + [m for m in definition if m not in DEFINITION_ORDER]
    return [_definition_row(member, definition) for member in members]


# --- the result page ------------------------------------------------------------------------


def declared_context(
    ctx: InspectContext,
    answer: StateDeclared | ProposalApproved,
    row: EngineOperation | None,
    *,
    posted_name: str | None,
    raw: Any,
) -> dict[str, Any]:
    """The result page's context (on the POST response, and for ``?report=``). #266's approval
    passes its ``ProposalApproved`` and replaces ``heading``, ``proposal_lines`` and ``links``."""
    story_id = ctx.story_id
    definition = answer.definition
    stored = definition.get("name")
    name = stored if isinstance(stored, str) and stored else (posted_name or "")
    linkable = bool(STATE_NAME.fullmatch(name))
    back = states_url(story_id, None)
    return {
        TEMPLATE_KEY: DECLARED_TEMPLATE,
        "state_name": name,
        "heading": DECLARED_HEADING.replace("{name}", name),
        "proposal_lines": [],
        "declared": answer,
        "definition": definition,
        "definition_rows": definition_rows(definition),
        "slot": slot_result(story_id, answer, row, back),
        "state_url": state_url(story_id, name) if linkable else None,
        "set_url": (
            set_url(story_id, name)
            if linkable and definition.get("owner", STORY_OWNER) == STORY_OWNER
            else None
        ),
        "again_url": new_url(story_id),
        "back_url": back,
        "report_url": declared_url(story_id, row.pk) if row is not None else None,
        "raw_answer": raw,
    }


def declared_row(pk: object, story_id: str) -> tuple[EngineOperation, StateDeclared] | None:
    """The #44 row ``pk`` and its answer when it is an ``ok`` ``declare_state`` of this story
    whose stored response parses; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    if (
        row.kind != WRITE_KIND
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    parsed = StateDeclared.from_json(row.response)
    return None if parsed is None else (row, parsed)


def _report(ctx: InspectContext) -> dict[str, Any]:
    found = declared_row(ctx.request.GET.get(REPORT_PARAM), ctx.story_id)
    if found is None:
        return {"missing": NOT_FOUND, "back_url": states_url(ctx.story_id, None)}
    row, answer = found
    return declared_context(ctx, answer, row, posted_name=None, raw=row.response)


@require_safe
def state_declared(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:state-declared?report=<pk>``: a declaration's result page again, from the #44
    row."""
    return inspect_page(
        request,
        story_id,
        Tab.WORLD,
        DECLARED_TEMPLATE,
        _report,
        current=SECTION,
        gate_section=False,
    )


# --- refusals -------------------------------------------------------------------------------


def _refresh(ctx: InspectContext, form: Any) -> None:
    """D8: an answered refusal re-renders with a fresh key and the POST's own story revision."""
    data = form.data.copy()
    if KEY_FIELD in form.fields:
        data[KEY_FIELD] = new_ulid()
    revision = _story_revision(ctx)
    if revision is not None and EXPECTED_REVISION_FIELD in form.fields:
        data[EXPECTED_REVISION_FIELD] = str(revision)
    form.data = data


def _posted(form: Any, name: str) -> str | None:
    value = form.cleaned_data.get(name) if hasattr(form, "cleaned_data") else None
    return value if isinstance(value, str) and value else None


def _detail(details: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = details.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _own_wording(form: Any, exc: EngineError, details: Mapping[str, Any]) -> bool:
    """This story's wordings for a 422 ``validation_error`` (convention 24: first)."""
    if exc.code != "validation_error":
        return False
    field = details.get("field")
    member = (
        field.removeprefix("body.").removeprefix(DEFINITION_PREFIX)
        if isinstance(field, str)
        else None
    )
    reason = details.get("reason")
    provider = _posted(form, "provider_id") or _detail(details, "provider_id", "provider") or "—"
    if member == "entity_id" and reason == "unknown_entity" and "entity_id" in form.fields:
        form.add_error("entity_id", ENTITY_UNKNOWN)
        return True
    if member == "provider_id" and reason == "unknown_provider" and "provider_id" in form.fields:
        form.add_error("provider_id", PROVIDER_UNKNOWN.replace("{provider}", provider))
        return True
    if member == "external_value_name" and reason == "unknown_value" and "name" in form.fields:
        name = _posted(form, "name") or _detail(details, "external_value_name", "name") or "—"
        form.add_error(
            "name", VALUE_UNKNOWN.replace("{provider}", provider).replace("{name}", name)
        )
        return True
    return False


def _place_shape(form: Any, exc: EngineError) -> int:
    """A 422's shape errors on their fields: a path under ``rules``/``authorization``/
    ``transfer`` on that box (the sub-path prefixed to the message), any other through
    ``apply_engine_errors`` with :data:`ALIASES`."""
    placed = 0
    rest: list[dict[str, str]] = []
    failures = engine_field_errors(exc)
    for failure in failures:
        path = failure.path.removeprefix("body.").removeprefix(DEFINITION_PREFIX)
        head, _, sub = path.partition(".")
        message = failure.message or exc.message
        if head in BOXES and head in form.fields:
            form.add_error(head, f"{sub}: {message}" if sub else message)
            placed += 1
        else:
            rest.append({"field": failure.path, "message": message})
    if failures and not rest:
        return placed
    trimmed = exc
    if failures:
        details = {**exc.details, "errors": rest}
        trimmed = EngineError(exc.code, exc.message, details, exc.http_status)
    return placed + apply_engine_errors(trimmed, form, aliases=ALIASES)


def _place(form: Any, refusal: Any, code: str) -> dict[str, Any]:
    if refusal.field in form.fields and not refusal.url:
        form.add_error(refusal.field, refusal.text)
        return {}
    return {
        "refusal": refusal.text,
        "refusal_url": refusal.url,
        "refusal_link": REFUSAL_LINKS.get(code, PAUSED_LINK) if refusal.url else "",
    }


def declaration_refusal(
    ctx: InspectContext, exc: EngineClientError, form: Any, verb_phrase: str
) -> dict[str, Any] | HttpResponse:
    """A refused declaration (#261; #266's approval reuses it with its own ``verb_phrase``):
    the error placed on ``form`` or returned as context additions (``refusal``, ``refusal_url``,
    ``refusal_link``, ``write_error``) for the caller to merge. Order: this story's own wordings
    → the ``validation_error`` reasons D9 words → ``apply_engine_errors`` → the rest of
    ``world_refusal`` → #43's panel. An answered refusal rebinds the form to a fresh key and the
    POST's story revision (D8); an unknown outcome keeps the posted ones. Held (503
    ``erasure_pending``) re-raises to #87's wrapper. The ``HttpResponse`` half of the return type
    is reserved for a not-found page; no refusal of a declaration needs one today."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    unknown = isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and (exc.code == BAD_RESPONSE or exc.http_status >= 500)
    )
    if unknown or not isinstance(exc, EngineError):
        return {"write_error": panel_for(exc)}  # the outcome is unknown: posted key and revision
    _refresh(ctx, form)
    details = exc.details if isinstance(exc.details, Mapping) else {}
    if _own_wording(form, exc, details):
        return {}
    shape = exc.code == "validation_error" and details.get("reason") not in WORDED_REASONS
    if shape and _place_shape(form, exc):
        return {}
    refusal = world_refusal(exc, ctx.story_id, verb_phrase, ctx.effective, field_map=ALIASES)
    if refusal is not None:
        return _place(form, refusal, exc.code)
    return {"write_error": panel_for(exc)}


# --- the form page --------------------------------------------------------------------------


def _declare(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    story_id = ctx.story_id
    page: dict[str, Any] = {
        "back_url": states_url(story_id, None),
        "action_url": new_url(story_id),
        "providers_url": providers_url(),
    }
    closed = module_gate(STATES, ctx.effective)
    revision = _story_revision(ctx)
    if request.method != "POST":
        if closed is not None:
            page["gate_unavailable"] = closed
            return page
        page["form"] = StateDeclarationForm(initial_revision=revision)
        return page
    if closed is not None:
        # D8/C6: no write; the posted inputs (no field errors), key and revision kept.
        page["form"] = StateDeclarationForm(
            initial=request.POST.dict(),
            initial_revision=expected_revision(request.POST),
            idempotency_key=posted_idempotency_key(request),
        )
        page["refusal"] = closed.reason
        page["refusal_url"] = reverse("stories:config", args=[story_id])
        page["refusal_link"] = REFUSAL_LINKS["module_off"]
        return page
    form = StateDeclarationForm(request.POST, initial_revision=revision)
    page["form"] = form
    if not form.is_valid():
        return page
    try:
        answer = ctx.client.declare_state(
            story_id,
            form.definition,
            expected_revision=expected_revision(request.POST),
            idempotency_key=posted_idempotency_key(request),
        )
    except EngineClientError as exc:
        refused = declaration_refusal(ctx, exc, form, VERB_PHRASE)
        if isinstance(refused, HttpResponse):
            return refused
        page.update(refused)
        return page
    pk = getattr(ctx.client, "last_operation_id", None)
    row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
    raw = row.response if row is not None else dataclasses.asdict(answer)
    return declared_context(ctx, answer, row, posted_name=form.cleaned_data["name"], raw=raw)


@require_http_methods(["GET", "HEAD", "POST"])
def state_new(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:state-new``: the Declare a State form, then ``POST /stories/{id}/states`` and
    the result page on the POST response (#261)."""
    return inspect_page(
        request,
        story_id,
        Tab.WORLD,
        DECLARE_TEMPLATE,
        _declare,
        current=SECTION,
        gate_section=False,
    )

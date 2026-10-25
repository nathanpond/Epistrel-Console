"""Personas (#67): a principal's player identities, kept apart from the catalog.

- ``/personas/``: ``GET /personas`` with the GET filters (owner, one relation; an invalid filter
  makes no call), cursor-paged ("Next page" / "First page", both keeping the filters). A 422
  (``validation_error``, ``cursor_invalid``) shows the panel below the filter form.
- ``/personas/new/``: the create form, owner pre-filled from ``?owner=`` else the acting
  principal; ``POST /personas`` lands on the new persona's page.
- ``/personas/<id>/``: every field, and the note that stories keep their copy.
- ``/personas/<id>/edit/``: the persona's fields (no owner: transfer is #79). Save PATCHes the
  changed fields only, from the signed snapshot of the persona as read; no change makes no call.
- ``/personas/<id>/delete/``: the shared confirm page (no call), then ``DELETE``.

A 409 ``persona_name_taken`` shows beside Name, naming the owner: the submitted one on create,
the persona's (from the snapshot) on edit. Every write is recorded (#44) with entity
``persona``.
"""

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_safe

from console.acting import acting_principal
from console.confirm import ConfirmPage, confirm_action, posted_idempotency_key
from console.engine.client import EngineClientError, EngineError
from console.engine.models import Persona
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.persona import (
    PersonaBaseForm,
    PersonaCreateForm,
    PersonaEditForm,
    PersonaFilterForm,
    initial_of,
)
from console.forms.principal import clean_principal, remember_principals
from console.ids import new_ulid
from console.twostage import MAX_AGE_S

LIST_URL = "/personas/"
NAME_TAKEN = "persona_name_taken"
KEEP_COPY = "Stories that used this persona keep their copy."
KEEP_COPY_DELETE = "Stories that used it keep their copy."
NOTHING_CHANGED = "Nothing changed"
EXPIRED = "This form expired — your changes were not saved; check and submit again"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.personas.snapshot"
BACK = "back"
EDIT_TEMPLATE = "console/personas/edit.html"


def _local(request: HttpRequest, candidate: str | None) -> str:
    """``candidate`` when it is a same-origin URL, else the persona list."""
    value = candidate or ""
    allowed = url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return value if value and allowed else LIST_URL


def _detail_url(persona_id: str, back: str | None = None) -> str:
    url = reverse("personas:detail", args=[persona_id])
    return f"{url}?{urlencode({BACK: back})}" if back and back != LIST_URL else url


# --- list -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListRow:
    persona: Persona
    url: str
    owner_filter: str


def _owner_filter(form: PersonaFilterForm, owner: str) -> str:
    """``?owner=<owner>``, keeping the active relation filter."""
    params = {"owner": owner}
    if form.is_valid() and form.cleaned_data.get("relation"):
        params["relation"] = form.cleaned_data["relation"]
        if form.cleaned_data.get("relation_principal"):
            params["relation_principal"] = form.cleaned_data["relation_principal"]
    return f"{LIST_URL}?{urlencode(params)}"


@require_safe
def persona_list(request: HttpRequest) -> HttpResponse:
    form = PersonaFilterForm(request.GET)
    rows: list[ListRow] = []
    next_query = ""
    engine_error: ErrorPanel | None = None
    new_params: dict[str, str] = {"next": request.get_full_path()}
    if form.is_valid():
        if form.cleaned_data.get("owner"):
            new_params = {"owner": form.cleaned_data["owner"], **new_params}
        try:
            query = form.query()
            items, next_cursor = engine_client_for(request).list_personas(
                owner=query["owner"], relation=query["relation"], cursor=query["cursor"]
            )
        except EngineClientError as exc:
            log_engine_error(request, exc)
            engine_error = panel_for(exc)
        else:
            back = request.get_full_path()
            rows = [
                ListRow(p, _detail_url(p.id, back), _owner_filter(form, p.owner_principal))
                for p in items
            ]
            if next_cursor:
                params = request.GET.copy()
                params["cursor"] = next_cursor
                next_query = params.urlencode()
    first = request.GET.copy()
    first.pop("cursor", None)
    context = {
        "form": form,
        "rows": rows,
        "engine_error": engine_error,
        "next_query": next_query,
        "first_query": first.urlencode(),
        "paged": bool(request.GET.get("cursor")),
        "any_filter": form.any_filter,
        "new_url": f"{reverse('personas:new')}?{urlencode(new_params)}",
    }
    return render(request, "console/personas/list.html", context)


# --- detail ---------------------------------------------------------------------------------------


@require_safe
def persona_detail(request: HttpRequest, persona_id: str) -> HttpResponse:
    """The persona (an Engine error escapes to the shared panel, #43)."""
    persona = engine_client_for(request).get_persona(persona_id)
    back = _local(request, request.GET.get(BACK))
    delete_query = urlencode({"name": persona.name, "owner": persona.owner_principal, "next": back})
    context = {
        "persona": persona,
        "back_url": back,
        "keep_copy": KEEP_COPY,
        "edit_url": reverse("personas:edit", args=[persona_id]),
        "delete_url": f"{reverse('personas:delete', args=[persona_id])}?{delete_query}",
    }
    return render(request, "console/personas/detail.html", context)


# --- create ---------------------------------------------------------------------------------------


def _name_taken(exc: EngineClientError) -> bool:
    return isinstance(exc, EngineError) and exc.http_status == 409 and exc.code == NAME_TAKEN


def _write_refusal(
    request: HttpRequest, exc: EngineClientError, form: PersonaBaseForm
) -> ErrorPanel | None:
    """A refused write's 422 placed at its fields (the panel when none could be placed); any
    other error as the panel."""
    log_engine_error(request, exc)
    if isinstance(exc, EngineError) and exc.http_status == 422 and apply_engine_errors(exc, form):
        return None
    return panel_for(exc)


def _render_new(
    request: HttpRequest,
    form: PersonaCreateForm,
    cancel_url: str,
    *,
    name_taken: str = "",
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    context = {
        "create": True,
        "form": form,
        "cancel_url": cancel_url,
        "action_url": reverse("personas:new"),
        "idempotency_key": new_ulid(),  # minted at each render (#43)
        "name_taken": name_taken,
        "engine_error": engine_error,
    }
    return render(request, EDIT_TEMPLATE, context)


def _prefill_owner(request: HttpRequest) -> str:
    """``?owner=`` when it is a valid principal, else the acting principal."""
    try:
        return clean_principal(request.GET.get("owner"))
    except ValidationError:
        return acting_principal(request)


@require_http_methods(["GET", "HEAD", "POST"])
def persona_new(request: HttpRequest) -> HttpResponse:
    cancel_url = _local(request, request.POST.get("next") or request.GET.get("next"))
    if request.method != "POST":
        form = PersonaCreateForm(initial={"owner": _prefill_owner(request)})
        return _render_new(request, form, cancel_url)
    form = PersonaCreateForm(request.POST)
    if not form.is_valid():
        return _render_new(request, form, cancel_url)
    remember_principals(form)
    key = posted_idempotency_key(request)
    try:
        created = engine_client_for(request).create_persona(
            **form.create_kwargs(), idempotency_key=key
        )
    except EngineClientError as exc:
        if _name_taken(exc):
            log_engine_error(request, exc)
            owner = form.cleaned_data["owner"]
            return _render_new(request, form, cancel_url, name_taken=owner)
        panel = _write_refusal(request, exc, form)
        return _render_new(request, form, cancel_url, engine_error=panel)
    messages.success(
        request,
        f"Created persona {created.name} ({created.id}) — key {created.idempotency_key or key}",
    )
    return redirect(_detail_url(created.id))


# --- edit -----------------------------------------------------------------------------------------


def _snapshot(persona: Persona) -> str:
    return signing.dumps(asdict(persona), salt=SNAPSHOT_SALT, compress=True)


def _unsigned(value: str | None, persona_id: str) -> Persona | None:
    """The persona as the posted page read it; ``None`` when tampered, expired or another's."""
    try:
        raw: Any = signing.loads(value or "", salt=SNAPSHOT_SALT, max_age=MAX_AGE_S)
    except signing.BadSignature:
        return None
    persona = Persona.from_json(raw)
    return persona if persona is not None and persona.id == persona_id else None


def _render_edit(
    request: HttpRequest,
    persona: Persona,
    form: PersonaEditForm,
    *,
    name_taken: str = "",
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    detail = reverse("personas:detail", args=[persona.id])
    context = {
        "create": False,
        "persona": persona,
        "form": form,
        "cancel_url": detail,
        "action_url": reverse("personas:edit", args=[persona.id]),
        "idempotency_key": new_ulid(),
        "snapshot": _snapshot(persona),
        "name_taken": name_taken,
        "engine_error": engine_error,
    }
    return render(request, EDIT_TEMPLATE, context)


@require_http_methods(["GET", "HEAD", "POST"])
def persona_edit(request: HttpRequest, persona_id: str) -> HttpResponse:
    """GET: the persona's fields as a form (read errors escape to the shared panel). POST:
    validate, then PATCH the changed fields and go to the persona's page."""
    if request.method != "POST":
        persona = engine_client_for(request).get_persona(persona_id)
        return _render_edit(request, persona, PersonaEditForm(initial=initial_of(persona)))
    snapped = _unsigned(request.POST.get(SNAPSHOT_FIELD), persona_id)
    if snapped is None:  # not from a live page: re-read, keep the input, write nothing
        persona = engine_client_for(request).get_persona(persona_id)
        form = PersonaEditForm(request.POST)
        form.is_valid()
        form.add_error(None, EXPIRED)
        return _render_edit(request, persona, form)
    form = PersonaEditForm(request.POST)
    if not form.is_valid():
        return _render_edit(request, snapped, form)
    changes = form.changes(initial_of(snapped))
    detail = reverse("personas:detail", args=[persona_id])
    if not changes:
        messages.info(request, NOTHING_CHANGED)
        return redirect(detail)
    key = posted_idempotency_key(request)
    try:
        saved = engine_client_for(request).patch_persona(persona_id, changes, idempotency_key=key)
    except EngineClientError as exc:
        if _name_taken(exc):
            log_engine_error(request, exc)
            return _render_edit(request, snapped, form, name_taken=snapped.owner_principal)
        panel = _write_refusal(request, exc, form)
        return _render_edit(request, snapped, form, engine_error=panel)
    messages.success(request, f"Saved persona {saved.name} — key {saved.idempotency_key or key}")
    return redirect(detail)


# --- delete ---------------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def persona_delete(request: HttpRequest, persona_id: str) -> HttpResponse:
    """GET: the confirm page, naming the persona and owner from the query string (display only;
    without them, the id alone), no Engine call. POST: ``DELETE``, then the list it came from
    (an Engine error escapes to the shared panel)."""
    source = request.POST if request.method == "POST" else request.GET
    name = source.get("name", "")
    owner = source.get("owner", "")
    back = _local(request, source.get(BACK) or request.GET.get("next"))
    facts = [("Persona id", persona_id)]
    if owner:
        facts.append(("Owner", owner))
    page = ConfirmPage(
        title="Delete persona",
        question="Delete {name}?" if name else "Delete this persona?",
        emphasis=name,
        lines=(KEEP_COPY_DELETE,),
        confirm_label="Delete",
        cancel_url=reverse("personas:detail", args=[persona_id]),
        facts=tuple(facts),
        hidden={"name": name, "owner": owner, BACK: back},
        danger=True,
    )

    def perform(key: str) -> HttpResponse:
        engine_client_for(request).delete_persona(persona_id, idempotency_key=key)
        shown = f"{name} ({persona_id})" if name else persona_id
        messages.success(request, f"Deleted persona {shown} — key {key}")
        return redirect(back)

    return confirm_action(request, page, perform)

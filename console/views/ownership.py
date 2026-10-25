"""Owners and relations (#79): one panel on the page of every owned entity, and its writes.

The six owned types are the registry's (``console.entities``): stories, catalog characters,
scenarios, personas, media and engine profiles. Nothing in the Engine or the Console gates on
an owner or a relation; they are labels and filters.

- :func:`ownership_context` builds the panel (``templates/console/_ownership.html``, context
  ``ownership``) from one ``GET /{type}/{id}/relations``, made on its own: a failed read shows
  the error panel in place of the relations table (the owner then comes from the host page's
  own read) and the page still renders. On a companion scenario (``follows``) it is read-only,
  with "Follows *Character*".
- ``/ownership/<type>/<id>/owner/``: the panel's transfer form GETs it with ``?principal=…``
  (plus ``current``, ``name``, ``has_companion``, ``next``, display only). The principal is
  checked with zero calls: the confirm page, or the form again with its error. Confirm PUTs.
- ``/ownership/<type>/<id>/relations/add/``: the panel's add form POSTs here; an invalid one
  re-renders this page (GET-able) with the errors and the input kept, zero calls.
- ``/ownership/<type>/<id>/relations/remove/?relation=…&principal=…``: the confirm page (zero
  calls), whose POST reads the relation and principal from its hidden fields alone and DELETEs
  them (both path-quoted with nothing safe: a principal may hold ``/``). 404 is "Already
  removed".

Refusals with wording (409 ``persona_name_taken``, 423 ``story_busy``, 422
``companion_follows_character``) re-render the page that submitted at 200, the message above
the form and every value kept, under a fresh idempotency key; any other error shows #43's panel
there. Successes and "Already removed" redirect to ``next`` (when local, else the entity's page)
at ``#ownership``, adding ``panel=ownership`` for a story (the play view opens the panel then).
Every write is recorded (#44) as ``transfer_owner``, ``add_relation`` or ``remove_relation``
on the entity.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods

from console.confirm import posted_idempotency_key, safe_next
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import OwnerTransfer, RelationItem, RelationRecord
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.entities import REGISTRY, EntityType, entity_url, label
from console.forms.ownership import RelationForm, TransferForm, relation_suggestions
from console.forms.principal import remember_principals
from console.ids import new_ulid
from console.views.catalog_lifecycle import COMPANION_FOLLOWS, Follows, companion_message

PANEL_ID = "ownership"
PANEL_PARAM = "panel"  # ``?panel=ownership``: the play view opens its collapsed panel
STORY: EntityType = "story"
CHARACTER: EntityType = "character"
SCENARIO: EntityType = "scenario"
NAME_TAKEN = "persona_name_taken"
STORY_BUSY = "story_busy"
NOT_FOUND = 404
# The display data the panel's forms carry (shown, never trusted for the write).
NAME = "name"
CURRENT = "current"
HAS_COMPANION = "has_companion"
NEXT = "next"
HELD = "(held — erasure pending)"
COMPANION_MOVES = "Its companion scenario moves with it."
COMPANION_MOVED = "Its companion scenario moved with it."
ALREADY_REMOVED = "Already removed"
NAME_TAKEN_TEXT = "{owner} already has a persona with this name — rename one first."
MISSING_REMOVE = "Name the relation and the principal to remove."
TRANSFER_TEMPLATE = "console/ownership/transfer.html"
ADD_TEMPLATE = "console/ownership/add.html"
REMOVE_TEMPLATE = "console/ownership/remove.html"


@dataclass(frozen=True)
class Refusal:
    """A refused write shown above the form that sent it, in the shape #48's
    ``console/settings/_refusal.html`` renders (``console.views.story_settings.Refusal`` without
    the violations no ownership write can carry; importing it would cycle through the play view).
    """

    notice: str = ""
    line: str = ""
    facts: tuple[tuple[str, str], ...] = ()
    panel: ErrorPanel | None = None


def shown_name(entity_type: str, name: str | None, entity_id: str) -> str:
    """How the panel names the entity: its name; a story without one is held for an erasure
    (``name: null``), anything else unnamed (convention 17)."""
    if name:
        return name
    return HELD if entity_type == STORY else label(None, entity_id)


def media_name(caption: str | None, alt_text: str | None, media_id: str) -> str:
    """A media asset's name for the panel: its caption, then its alt text, then its id."""
    return caption or alt_text or media_id


# --- the panel ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationRow:
    item: RelationItem
    remove_url: str


@dataclass(frozen=True)
class OwnershipPanel:
    """What ``_ownership.html`` shows. ``rows`` is ``None`` (with ``error``) when the relations
    read failed. ``follows`` makes it read-only (a companion scenario). ``collapsible``: the
    play view's ``<details>``, ``open`` when the URL asks for it."""

    entity_type: EntityType
    entity_id: str
    name: str
    owner: str
    rows: tuple[RelationRow, ...] | None
    error: ErrorPanel | None
    follows: Follows | None
    has_companion: bool
    next_url: str
    transfer_url: str
    add_url: str
    transfer_form: TransferForm | None
    add_form: RelationForm | None
    suggestions: tuple[str, ...]
    datalist: str
    collapsible: bool
    open: bool
    idempotency_key: str = field(default_factory=new_ulid)

    @property
    def read_only(self) -> bool:
        return self.follows is not None


def _urls(entity_type: EntityType, entity_id: str) -> tuple[str, str, str]:
    """The transfer, add and remove URLs of one entity."""
    args = [entity_type, entity_id]
    return (
        reverse("ownership:owner", args=args),
        reverse("ownership:add", args=args),
        reverse("ownership:remove", args=args),
    )


def ownership_context(
    request: HttpRequest,
    client: EngineClient,
    entity_type: EntityType,
    entity_id: str,
    *,
    name: str | None,
    owner: str,
    has_companion: bool = False,
    follows: Follows | None = None,
    next_url: str | None = None,
) -> OwnershipPanel:
    """The panel of one entity: its relations read once (serially, apart from the host page's
    pool). ``name`` and ``owner`` come from the host page's own read: the name the forms carry
    for display, and the owner shown when the relations read fails. ``has_companion``: a
    character whose companion scenario moves with it. ``follows``: a companion scenario's
    character (the host page's label, one card read), which makes the panel read-only.
    ``next_url``: where the writes return (default: this page, query string included)."""
    transfer_url, add_url, remove_url = _urls(entity_type, entity_id)
    shown = shown_name(entity_type, name, entity_id)
    next_url = next_url or request.get_full_path()
    rows: tuple[RelationRow, ...] | None = None
    error: ErrorPanel | None = None
    names: list[str] = []
    try:
        relations = client.get_relations(entity_type, entity_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        error = panel_for(exc)
    else:
        owner = relations.owner_principal
        names = [item.relation for item in relations.items]
        rows = tuple(
            RelationRow(
                item,
                f"{remove_url}?"
                + urlencode(
                    {
                        "relation": item.relation,
                        "principal": item.principal,
                        NAME: shown,
                        NEXT: next_url,
                    }
                ),
            )
            for item in relations.items
        )
    datalist = f"dl-relations-{entity_type}"
    writable = follows is None
    return OwnershipPanel(
        entity_type=entity_type,
        entity_id=entity_id,
        name=shown,
        owner=owner,
        rows=rows,
        error=error,
        follows=follows,
        has_companion=has_companion and entity_type == CHARACTER,
        next_url=next_url,
        transfer_url=transfer_url,
        add_url=add_url,
        transfer_form=TransferForm(current=owner) if writable else None,
        add_form=RelationForm(datalist=datalist) if writable else None,
        suggestions=tuple(relation_suggestions(names)),
        datalist=datalist,
        collapsible=entity_type == STORY,
        open=request.GET.get(PANEL_PARAM) == PANEL_ID,
    )


# --- shared ---------------------------------------------------------------------------------------


def _entity_type(raw: str) -> EntityType:
    """The URL's type segment as an Engine ``EntityType``; anything else is 404 (no call)."""
    if raw not in REGISTRY:
        raise Http404("not an owned entity type")
    return cast(EntityType, raw)


def _source(request: HttpRequest) -> Mapping[str, str]:
    return request.POST if request.method == "POST" else request.GET


def _done_url(request: HttpRequest, entity_type: EntityType, entity_id: str) -> str:
    """Where a write returns: ``next`` when local, else the entity's page, at ``#ownership``;
    a story's with ``panel=ownership`` so the play view opens the panel."""
    fallback = entity_url(entity_type, entity_id) or "/"
    parts = urlsplit(safe_next(request, fallback))
    query = parts.query
    if entity_type == STORY:
        pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != PANEL_PARAM]
        query = urlencode([*pairs, (PANEL_PARAM, PANEL_ID)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, PANEL_ID))


def _cancel_url(request: HttpRequest, entity_type: EntityType, entity_id: str) -> str:
    return _done_url(request, entity_type, entity_id)


def _revision(result: OwnerTransfer | RelationRecord) -> str:
    """#43's suffix: the revision the answer carries, then the idempotency key."""
    if result.timeline_revision is not None:
        return f"timeline revision {result.timeline_revision}, "
    if result.config_revision is not None:
        return f"config revision {result.config_revision}, "
    return ""


def _suffix(result: OwnerTransfer | RelationRecord, key: str) -> str:
    return f"{_revision(result)}idempotency key {result.idempotency_key or key}"


def _busy(exc: EngineError) -> Refusal:
    """423 ``story_busy``: "A turn is running — try again in N s" (N from ``Retry-After``,
    left out when absent), and the operation when the details name one (#48's style)."""
    wait = f" in {exc.retry_after} s" if exc.retry_after else ""
    operation = exc.details.get("operation_id")
    facts = (("Operation", str(operation)),) if operation else ()
    return Refusal(
        notice=f"A turn is running — try again{wait}",
        line=f"HTTP {exc.http_status} {exc.code}: {exc.message}",
        facts=facts,
    )


def _follows_text(client: EngineClient, exc: EngineError, entity_id: str) -> SafeString:
    """#69's line for a stale form on a companion scenario, naming the character it follows
    (the error's ``character_id``, else one re-read of the scenario's ``companion_of``), linked;
    unlinked when neither names it."""
    character_id = exc.details.get("character_id")
    if not isinstance(character_id, str) or not character_id:
        try:
            character_id = client.get_scenario(entity_id).companion_of or ""
        except EngineClientError:
            character_id = ""
    return companion_message(client, character_id)


def refusal_for(
    client: EngineClient, exc: EngineClientError, entity_id: str, *, owner: str = ""
) -> Refusal:
    """A refused write as the page shows it: the worded refusals (``persona_name_taken`` names
    ``owner``, the transfer's target), else #43's panel."""
    if isinstance(exc, EngineError):
        if exc.http_status == 409 and exc.code == NAME_TAKEN:
            return Refusal(notice=NAME_TAKEN_TEXT.format(owner=owner or "The new owner"))
        if exc.http_status == 423 and exc.code == STORY_BUSY:
            return _busy(exc)
        if exc.http_status == 422 and exc.code == COMPANION_FOLLOWS:
            return Refusal(notice=_follows_text(client, exc, entity_id))
    return Refusal(panel=panel_for(exc))


def _base_context(
    request: HttpRequest, entity_type: EntityType, entity_id: str, name: str
) -> dict[str, Any]:
    info = REGISTRY[entity_type]
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "type_name": info.display_name,
        "name": shown_name(entity_type, name, entity_id),
        "raw_name": name,
        "next_url": safe_next(request, entity_url(entity_type, entity_id) or "/"),
        "cancel_url": _cancel_url(request, entity_type, entity_id),
        "idempotency_key": new_ulid(),
    }


# --- transfer -------------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def transfer_owner(request: HttpRequest, entity_type: str, entity_id: str) -> HttpResponse:
    """GET: with ``?principal=`` the confirm page (or the form with its error), zero calls;
    without, the form. POST: ``PUT …/owner``, then back with the flash."""
    kind = _entity_type(entity_type)
    source = _source(request)
    name = source.get(NAME, "")
    current = source.get(CURRENT, "")
    has_companion = kind == CHARACTER and source.get(HAS_COMPANION) == "1"
    bound = request.method == "POST" or "principal" in request.GET
    form = TransferForm(source if bound else None, current=current)
    context = _base_context(request, kind, entity_id, name)
    context.update(
        {
            "form": form,
            "current": current,
            "has_companion": has_companion,
            "companion_moves": COMPANION_MOVES if has_companion else "",
            "action_url": reverse("ownership:owner", args=[kind, entity_id]),
        }
    )
    if not bound or not form.is_valid():
        return render(request, TRANSFER_TEMPLATE, {**context, "confirm": False})
    target: str = form.cleaned_data["principal"]
    context.update({"confirm": True, "target": target})
    if request.method != "POST":
        return render(request, TRANSFER_TEMPLATE, context)
    remember_principals(form)
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        result = client.transfer_owner(kind, entity_id, target, idempotency_key=key)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        refusal = refusal_for(client, exc, entity_id, owner=target)
        return render(request, TRANSFER_TEMPLATE, {**context, "refusal": refusal})
    if not result.changed:
        messages.info(request, f"Already owned by {result.owner_principal} — nothing changed")
    else:
        moved = f" {COMPANION_MOVED}" if has_companion else ""
        messages.success(
            request,
            f"Transferred {context['name']} to {result.owner_principal} "
            f"(was {result.previous_owner}) — {_suffix(result, key)}.{moved}",
        )
    return redirect(_done_url(request, kind, entity_id))


# --- add ------------------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def add_relation(request: HttpRequest, entity_type: str, entity_id: str) -> HttpResponse:
    """GET: the add page (blank). POST: the panel's (or this page's) add form; an invalid one
    is this page with its errors (zero calls), a valid one ``POST …/relations``."""
    kind = _entity_type(entity_type)
    source = _source(request)
    name = source.get(NAME, "")
    datalist = f"dl-relations-{kind}"
    posted = request.method == "POST"
    form = RelationForm(request.POST if posted else None, datalist=datalist)
    context = _base_context(request, kind, entity_id, name)
    context.update(
        {
            "form": form,
            "datalist": datalist,
            "suggestions": relation_suggestions([]),
            "action_url": reverse("ownership:add", args=[kind, entity_id]),
        }
    )
    if not posted or not form.is_valid():
        return render(request, ADD_TEMPLATE, context)
    remember_principals(form)
    principal: str = form.cleaned_data["principal"]
    relation: str = form.cleaned_data["relation"]
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        record, created = client.add_relation(
            kind, entity_id, principal, relation, idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        refusal = refusal_for(client, exc, entity_id)
        return render(request, ADD_TEMPLATE, {**context, "refusal": refusal})
    if created:
        messages.success(
            request, f"Added {record.principal} as {record.relation} — {_suffix(record, key)}"
        )
    else:
        messages.info(
            request,
            f"{record.principal} is already {record.relation} — nothing changed "
            f"({_suffix(record, key)})",
        )
    return redirect(_done_url(request, kind, entity_id))


# --- remove ---------------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def remove_relation(request: HttpRequest, entity_type: str, entity_id: str) -> HttpResponse:
    """GET: the confirm page from the query string, zero calls. POST: ``DELETE`` the relation
    the hidden fields name (exactly as listed: no trim), then back with the flash."""
    kind = _entity_type(entity_type)
    source = _source(request)
    relation = source.get("relation", "")
    principal = source.get("principal", "")
    if not relation or not principal:
        return HttpResponseBadRequest(MISSING_REMOVE)
    context = _base_context(request, kind, entity_id, source.get(NAME, ""))
    context.update(
        {
            "relation": relation,
            "principal": principal,
            "action_url": reverse("ownership:remove", args=[kind, entity_id]),
        }
    )
    if request.method != "POST":
        return render(request, REMOVE_TEMPLATE, context)
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        client.remove_relation(kind, entity_id, relation, principal, idempotency_key=key)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == NOT_FOUND:
            messages.info(request, ALREADY_REMOVED)
            return redirect(_done_url(request, kind, entity_id))
        refusal = refusal_for(client, exc, entity_id)
        return render(request, REMOVE_TEMPLATE, {**context, "refusal": refusal})
    messages.success(request, f"Removed {principal} as {relation} — idempotency key {key}")
    return redirect(_done_url(request, kind, entity_id))

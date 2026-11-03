"""A story's Providers page and bind form (#247): the story's ids at each external-data provider.

- ``/stories/<id>/providers/`` (``stories:providers``): every registered provider with the
  story's binding to it (or none), then any binding to a provider no longer registered. Reads,
  in order: ``get_story``, ``list_provider_bindings``, ``list_providers``. A failed story or
  bindings read is "Story not found" (404) or #43's panel with no table; a failed registry read
  keeps the bindings (by name, no links, no Bind) with a notice and the panel. Status is
  computed here against the current manifest: "Bound", "Not bound", "Incomplete — missing …"
  (binding keys the ids don't cover: exactly what ``binding_incomplete`` would refuse) or
  "Provider not registered". The Engine serves no read of which providers a story needs
  (``required_providers`` isn't on the settings read), so the page says so once.
- ``/stories/<id>/providers/<name>/bind/`` (``stories:provider-bind``): :class:`BindingForm`
  with one input per ``{binding.<key>}`` of the provider's manifest (a JSON box when there are
  none, D14). Each GET reads the story, its bindings and ``get_provider(name)``; the page carries
  the keys and existing ids in a signed snapshot (salt :data:`SNAPSHOT_SALT`), so a POST that
  fails validation, an unchanged one and a refusal re-render with no read. A valid, changed
  POST makes one ``PUT …/binding`` with the whole map (it replaces the binding) and the page's
  idempotency key; no expected revision (D8's configuration-write rule). D8's re-render rule:
  local invalid input and a transport error/timeout/unreadable answer keep the posted key; an
  answered refusal mints a fresh one. Success is PRG to ``stories:providers#binding-<name>``.

Every read and the PUT re-raise a 503 ``erasure_pending`` so ``HeldStoryMiddleware`` renders the
held page (both URLs carry the story id). No "unbind": the Engine has no delete-binding route.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_safe

from console.confirm import posted_idempotency_key
from console.effects import provider_url
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import (
    Provider,
    ProviderBinding,
    ProviderBindings,
    ProviderList,
    Story,
    rejected_entries,
)
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors, engine_field_errors
from console.forms.bindings import JSON_FIELD, BindingForm, field_name
from console.forms.profile_schema_form import warning_flash_text
from console.held import is_held
from console.ids import new_ulid
from console.providers import binding_keys, is_provider_name
from console.templatetags.inspect_tags import no_backticks
from console.twostage import MAX_AGE_S
from console.wording import archived_refusal, write_refusal

PAGE_TEMPLATE = "console/stories/providers.html"
BIND_TEMPLATE = "console/stories/provider_bind.html"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.bindings.snapshot"
VERB_PHRASE = "bind a provider"
EMPTY = "—"
NO_IDS = "no ids"
BAD_RESPONSE = "bad_response"
BINDING_INCOMPLETE = "binding_incomplete"
VALIDATION_ERROR = "validation_error"
IDS_PATH = "external_ids"

STORY_NOT_FOUND = "Story not found"
BOUND = "Bound"
NOT_BOUND = "Not bound"
NOT_REGISTERED = "Provider not registered"
UNREAD_BINDING = "This binding couldn't be read"
UNREAD_PROVIDER = "This provider couldn't be read"
NEEDS_NOTICE = (
    "The Engine doesn't report which providers this story needs. A turn that needs an unbound "
    "one is refused with `provider_unbound`, naming it."
)
REGISTRY_UNREAD = "The provider registry couldn't be read — binding is unavailable"
NO_PROVIDERS = "No providers are registered — register one in the Engine panel"
EXISTING_UNREAD = "The existing binding couldn't be read — saving replaces it"
NO_CHANGES = "No changes — nothing was sent"
NEEDS_THIS_ID = "The provider's manifest needs this id"


def not_registered_text(name: str) -> str:
    return f"No provider named “{name}” is registered"


def gone_text(name: str) -> str:
    return f"{name} is no longer registered"


def incomplete_text(missing: list[str]) -> str:
    return "Incomplete — missing " + ", ".join(f"`{k}`" for k in missing)


def now_needs_text(missing: list[str]) -> str:
    keys = ", ".join(f"`{k}`" for k in missing)
    return f"The provider's manifest now also needs: {keys} — reload to get its inputs"


def ids_count_text(n: int) -> str:
    return "1 id" if n == 1 else f"{n} ids"


def bindings_count_text(n: int) -> str:
    return "1 binding" if n == 1 else f"{n} bindings"


def substitutes_html(keys: list[str]) -> SafeString:
    """ "This provider's manifest substitutes: <code>k1</code>, <code>k2</code>" (or "no ids")."""
    if not keys:
        return format_html("This provider's manifest substitutes: {}", NO_IDS)
    listed = format_html_join(", ", "<code>{}</code>", ((k,) for k in keys))
    return format_html("This provider's manifest substitutes: {}", listed)


def _raw(entry: Any) -> str:
    return json.dumps(entry, indent=2, ensure_ascii=False, default=str)


def _story_label(story: Story) -> str:
    return story.name or story.id


# --- the list -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One row of the Providers table. ``unread``: the raw JSON of an entry that didn't parse
    (``kind`` says whose: ``"binding"`` or ``"provider"``)."""

    name: str = ""
    url: str | None = None
    ids: tuple[tuple[str, str], ...] | None = None  # None: no binding
    bound_at: str = ""
    needs: tuple[str, ...] | None = None  # None: unknown (registry unread / orphan)
    status: str = ""
    action: str = ""
    action_url: str = ""
    unread: str | None = None
    kind: str = ""

    @property
    def status_title(self) -> str:
        return no_backticks(self.status)


def _binding_ids(binding: ProviderBinding | None) -> tuple[tuple[str, str], ...] | None:
    if binding is None:
        return None
    return tuple((str(k), str(v)) for k, v in binding.external_ids.items())


def _provider_row(story_id: str, provider: Provider, binding: ProviderBinding | None) -> Row:
    keys = binding_keys(provider.manifest)
    if binding is None:
        status = NOT_BOUND
    else:
        missing = [k for k in keys if k not in binding.external_ids]
        status = incomplete_text(missing) if missing else BOUND
    return Row(
        name=provider.name,
        url=provider_url(provider.name),
        ids=_binding_ids(binding),
        bound_at=binding.bound_at if binding is not None else "",
        needs=tuple(keys),
        status=status,
        action="Change ids" if binding is not None else "Bind",
        action_url=reverse("stories:provider-bind", args=[story_id, provider.name]),
    )


def _binding_row(binding: ProviderBinding, status: str) -> Row:
    return Row(
        name=binding.provider,
        ids=_binding_ids(binding),
        bound_at=binding.bound_at,
        status=status,
    )


def _rejected(
    unparsed: Mapping[str, Any] | None, key: str, parse: Callable[[Any], Any]
) -> list[Any]:
    if not unparsed or key not in unparsed:
        return []
    return rejected_entries(unparsed[key], parse)


def rows_for(story_id: str, bindings: ProviderBindings, registry: ProviderList | None) -> list[Row]:
    """The table: registered providers (registry order), unreadable registry entries, bindings to
    unregistered providers (bindings order), then unreadable binding entries (D15). Without the
    registry: the bindings by name (Status "Bound", no links, no action), then the unreadable
    ones."""
    bad_bindings = [
        Row(unread=_raw(e), kind="binding")
        for e in _rejected(bindings.unparsed, "bindings", ProviderBinding.from_json)
    ]
    by_name: dict[str, ProviderBinding] = {}
    for b in bindings.bindings:
        by_name.setdefault(b.provider, b)
    if registry is None:
        return [*(_binding_row(b, BOUND) for b in bindings.bindings), *bad_bindings]
    bad_providers = [
        Row(unread=_raw(e), kind="provider")
        for e in _rejected(registry.unparsed, "providers", Provider.from_json)
    ]
    registered = {p.name for p in registry.providers}
    orphan_status = EMPTY if bad_providers else NOT_REGISTERED
    orphans = [b for b in bindings.bindings if b.provider not in registered]
    return [
        *(_provider_row(story_id, p, by_name.get(p.name)) for p in registry.providers),
        *bad_providers,
        *(_binding_row(b, orphan_status) for b in orphans),
        *bad_bindings,
    ]


def _is_404(exc: EngineClientError) -> bool:
    return isinstance(exc, EngineError) and exc.http_status == 404


def _read[T](request: HttpRequest, read: Callable[[], T]) -> T | EngineClientError:
    """``read()``, or the error it raised (logged); a held story re-raises."""
    try:
        return read()
    except EngineClientError as exc:
        if is_held(exc):
            raise
        log_engine_error(request, exc)
        return exc


def _story_missing(request: HttpRequest, story_id: str, template: str) -> HttpResponse:
    context = {"story_id": story_id, "not_found": STORY_NOT_FOUND}
    return render(request, template, context, status=404)


def _read_failed(
    request: HttpRequest, story_id: str, template: str, exc: EngineClientError, **extra: Any
) -> HttpResponse:
    """A failed story or bindings read: "Story not found" (404) or the panel, nothing else."""
    if _is_404(exc):
        return _story_missing(request, story_id, template)
    context = {"story_id": story_id, "engine_error": panel_for(exc), **extra}
    return render(request, template, context)


@require_safe
def providers(request: HttpRequest, story_id: str) -> HttpResponse:
    """GET: three reads (story, bindings, registry); no write."""
    client = engine_client_for(request)
    story = _read(request, lambda: client.get_story(story_id))
    if isinstance(story, EngineClientError):
        return _read_failed(request, story_id, PAGE_TEMPLATE, story)
    header = {
        "story": story,
        "story_label": _story_label(story),
        "archived_note": archived_refusal(VERB_PHRASE) if story.status == "archived" else "",
    }
    bindings = _read(request, lambda: client.list_provider_bindings(story_id))
    if isinstance(bindings, EngineClientError):
        return _read_failed(request, story_id, PAGE_TEMPLATE, bindings, **header)
    read = _read(request, client.list_providers)
    registry: ProviderList | None = None
    registry_error: ErrorPanel | None = None
    if isinstance(read, EngineClientError):
        registry_error = panel_for(read)
    else:
        registry = read
    registry_empty = (
        registry is not None
        and not registry.providers
        and not _rejected(registry.unparsed, "providers", Provider.from_json)
    )
    context = {
        **header,
        "story_id": story_id,
        "rows": rows_for(story_id, bindings, registry),
        "count_text": bindings_count_text(len(bindings.bindings)),
        "needs_notice": NEEDS_NOTICE,
        "registry_error": registry_error,
        "registry_unread": REGISTRY_UNREAD if registry_error is not None else "",
        "registry_empty": NO_PROVIDERS if registry_empty else "",
        "unread_binding": UNREAD_BINDING,
        "unread_provider": UNREAD_PROVIDER,
        "not_bound": NOT_BOUND,
        "no_ids": NO_IDS,
        "empty": EMPTY,
    }
    return render(request, PAGE_TEMPLATE, context)


# --- the bind form --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """What the bind form rendered from, signed: the story, the provider's manifest keys and the
    story's ids there (``None``: no binding; ``unread``: its entry didn't parse)."""

    story_id: str
    story_name: str
    story_status: str
    provider: str
    keys: list[str]
    existing: dict[str, str] | None
    unread: bool = False

    def signed(self) -> str:
        payload = {
            "story_id": self.story_id,
            "story_name": self.story_name,
            "story_status": self.story_status,
            "provider": self.provider,
            "keys": self.keys,
            "existing": self.existing,
            "unread": self.unread,
        }
        return signing.dumps(payload, salt=SNAPSHOT_SALT, compress=True)

    @classmethod
    def unsigned(cls, value: str | None, story_id: str, provider: str) -> Snapshot | None:
        """The posted snapshot, or ``None`` when missing, expired, tampered or another page's."""
        try:
            raw = signing.loads(value or "", salt=SNAPSHOT_SALT, max_age=MAX_AGE_S)
        except signing.BadSignature:
            return None
        if not isinstance(raw, dict):
            return None
        if raw.get("story_id") != story_id or raw.get("provider") != provider:
            return None
        keys, existing = raw.get("keys"), raw.get("existing")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            return None
        if existing is not None and not (
            isinstance(existing, dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in existing.items())
        ):
            return None
        name, status = raw.get("story_name"), raw.get("story_status")
        if not isinstance(name, str) or not isinstance(status, str):
            return None
        return cls(story_id, name, status, provider, keys, existing, raw.get("unread") is True)

    @property
    def label(self) -> str:
        return self.story_name or self.story_id

    @property
    def archived(self) -> bool:
        return self.story_status == "archived"


@dataclass
class Refusal:
    """What a refused PUT adds above the re-rendered form."""

    above: list[str] = field(default_factory=list)
    above_url: str = ""
    engine_error: ErrorPanel | None = None
    keep_key: bool = False
    status: int = 200


def _existing(bindings: ProviderBindings, name: str) -> tuple[dict[str, str] | None, bool]:
    """The story's ids at ``name`` (``None``: no binding) and whether an unreadable entry names
    it (which counts as no binding)."""
    for b in bindings.bindings:
        if b.provider == name:
            return dict(b.external_ids), False
    bad = _rejected(bindings.unparsed, "bindings", ProviderBinding.from_json)
    return None, any(isinstance(e, dict) and e.get("provider") == name for e in bad)


def _missing_provider(request: HttpRequest, story_id: str, name: str, **extra: Any) -> HttpResponse:
    context = {"story_id": story_id, "name": name, "missing": not_registered_text(name), **extra}
    return render(request, BIND_TEMPLATE, context, status=404)


def _read_snapshot(
    request: HttpRequest, client: EngineClient, story_id: str, name: str
) -> Snapshot | HttpResponse:
    """The three reads, as a snapshot, or the page a failed read renders (no form)."""
    story = _read(request, lambda: client.get_story(story_id))
    if isinstance(story, EngineClientError):
        return _read_failed(request, story_id, BIND_TEMPLATE, story, name=name)
    header = {"story_label": _story_label(story), "provider_link": provider_url(name)}
    bindings = _read(request, lambda: client.list_provider_bindings(story_id))
    if isinstance(bindings, EngineClientError):
        return _read_failed(request, story_id, BIND_TEMPLATE, bindings, name=name, **header)
    provider = _read(request, lambda: client.get_provider(name))
    if isinstance(provider, EngineClientError):
        if _is_404(provider):
            return _missing_provider(request, story_id, name, **header)
        context = {"story_id": story_id, "name": name, "engine_error": panel_for(provider)}
        return render(request, BIND_TEMPLATE, {**context, **header})
    existing, unread = _existing(bindings, name)
    return Snapshot(
        story_id,
        story.name or "",
        str(story.status),
        name,
        binding_keys(provider.manifest),
        existing,
        unread,
    )


def _render_form(
    request: HttpRequest,
    snap: Snapshot,
    form: BindingForm,
    key: str,
    refusal: Refusal | None = None,
) -> HttpResponse:
    refusal = refusal or Refusal()
    context = {
        "story_id": snap.story_id,
        "story_label": snap.label,
        "name": snap.provider,
        "provider_link": provider_url(snap.provider),
        "archived_note": archived_refusal(VERB_PHRASE) if snap.archived else "",
        "substitutes": substitutes_html(snap.keys),
        "existing_unread": EXISTING_UNREAD if snap.unread else "",
        "form": form,
        "snapshot": snap.signed(),
        "idempotency_key": key,
        "button": "Save ids" if snap.existing is not None else "Bind",
        "above": refusal.above,
        "above_url": refusal.above_url,
        "engine_error": refusal.engine_error,
        "providers_url": reverse("stories:providers", args=[snap.story_id]),
    }
    return render(request, BIND_TEMPLATE, context, status=refusal.status)


def _id_member(path: str) -> str | None:
    """``external_ids.<key>[.<rest>]`` → ``<key>``; anything else → ``None``."""
    head, dot, rest = path.partition(".")
    if head != IDS_PATH or not dot:
        return None
    return rest.split(".", 1)[0]


def _place_validation(exc: EngineError, form: BindingForm) -> bool:
    """A 422 ``validation_error``'s paths onto the form: ``body.external_ids.<key>`` on
    ``id__<key>`` (or the JSON box), anything else above the form. ``False``: nothing placed."""
    aliases: dict[str, str] = {}
    for failure in engine_field_errors(exc):
        path = failure.path.removeprefix("body.")
        if form.json_mode and (path == IDS_PATH or path.startswith(IDS_PATH + ".")):
            aliases[path] = JSON_FIELD
            continue
        key = _id_member(path)
        if key is not None and field_name(key) in form.fields:
            aliases[path] = field_name(key)
    return apply_engine_errors(exc, form, aliases=aliases) > 0


def _refusal(
    request: HttpRequest, exc: EngineClientError, form: BindingForm, story_id: str
) -> Refusal:
    """Route a refused PUT (module docstring); the key half of D8 is ``keep_key``."""
    if is_held(exc):
        raise exc
    log_engine_error(request, exc)
    if not isinstance(exc, EngineError) or exc.code == BAD_RESPONSE:
        return Refusal(engine_error=panel_for(exc), keep_key=True)  # D8: outcome unknown
    details = exc.details
    if exc.http_status == 422 and exc.code == BINDING_INCOMPLETE:
        listed = details.get("missing")
        missing = [k for k in listed if isinstance(k, str)] if isinstance(listed, list) else []
        unknown: list[str] = []
        for key in missing:
            if field_name(key) in form.fields and not form.json_mode:
                form.add_error(field_name(key), NEEDS_THIS_ID)
            else:
                unknown.append(key)
        if unknown:
            return Refusal(above=[now_needs_text(unknown)])
        if missing:
            return Refusal()
    if exc.http_status == 422 and exc.code == VALIDATION_ERROR and _place_validation(exc, form):
        return Refusal()
    if exc.http_status == 404:
        gone = details.get("provider")
        if isinstance(gone, str) and gone:
            return Refusal(above=[gone_text(gone)])
        return Refusal(above=[STORY_NOT_FOUND], status=404)
    worded = write_refusal(exc, story_id, VERB_PHRASE)
    if worded is not None:
        return Refusal(above=[worded.text], above_url=worded.url)
    return Refusal(engine_error=panel_for(exc))


def _flash_success(request: HttpRequest, name: str, answer: Any, key: str) -> None:
    messages.success(
        request,
        f"Bound to {name} — {ids_count_text(len(answer.external_ids))}, idempotency key "
        f"{answer.idempotency_key or key}",
    )
    for warning in answer.warnings:
        messages.warning(request, "Binding stored, but: " + warning_flash_text(warning))
    if answer.activated_required_providers:
        names = ", ".join(answer.activated_required_providers)
        messages.info(request, f"This binding made the story's required providers live: {names}")


@require_http_methods(["GET", "POST"])
def provider_bind(request: HttpRequest, story_id: str, name: str) -> HttpResponse:
    """GET: three reads, then the form. POST: validate (from the snapshot); unchanged → no
    call; else one ``bind_provider``."""
    if not is_provider_name(name):
        return _missing_provider(request, story_id, name)
    client = engine_client_for(request)
    if request.method != "POST":
        read = _read_snapshot(request, client, story_id, name)
        if isinstance(read, HttpResponse):
            return read
        form = BindingForm(keys=read.keys, existing=read.existing)
        return _render_form(request, read, form, new_ulid())

    snap = Snapshot.unsigned(request.POST.get(SNAPSHOT_FIELD), story_id, name)
    if snap is None:  # missing, expired or tampered: read again
        read = _read_snapshot(request, client, story_id, name)
        if isinstance(read, HttpResponse):
            return read
        snap = read
    form = BindingForm(request.POST, keys=snap.keys, existing=snap.existing)
    key = posted_idempotency_key(request)
    if not form.is_valid():
        return _render_form(request, snap, form, key)  # D8: nothing sent
    providers_url = reverse("stories:providers", args=[story_id])
    if form.unchanged():
        messages.info(request, NO_CHANGES)
        return redirect(providers_url)
    try:
        answer = client.bind_provider(story_id, name, form.external_ids(), idempotency_key=key)
    except EngineClientError as exc:
        refusal = _refusal(request, exc, form, story_id)
        return _render_form(request, snap, form, key if refusal.keep_key else new_ulid(), refusal)
    _flash_success(request, name, answer, key)
    return redirect(f"{providers_url}#binding-{name}")

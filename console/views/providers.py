"""The deployment's external-data providers in the Engine panel (#240; writes #246).

- ``/engine/providers/`` (``engine:providers``): every registered provider in the Engine's order
  (by name) with the deployment's ``config_revision``; one ``list_providers()`` read. Entries the
  Console couldn't read get one "couldn't be read" row each, at their raw position (D15's
  display rule through ``rejected_entries``); the count line counts parsed providers only.
- ``/engine/providers/<name>/`` (``engine:provider``): one provider's whole registration, its
  values, the ``{binding.<key>}`` ids a story binds, the ``{secret.<NAME>}`` references it
  resolves from the Engine's environment, and its manifest verbatim; one ``get_provider(name)``
  read. A name failing ``is_provider_name`` is a 404 with no call.
- ``/engine/provider-registration/`` (``engine:provider-new``),
  ``/engine/providers/<name>/replace/`` (``engine:provider-edit``) and
  ``/engine/providers/<name>/delete/`` (``engine:provider-delete``): register, replace and delete
  (#246; see the section's comment below).

The Engine stores and returns only secret references, never a value, and the pages show its
manifest exactly as answered. Errors: an Engine 404 ``not_found`` is the not-found line, a 503
``erasure_pending`` #87's held fragment in this page's frame with no story links (D17's storyless
rule), anything else #43's panel; every error is logged. Display rows are built here
(humanised enum text, C3, with the raw value for ``title``); ``console/providers.py`` reads the
manifest.
"""

import json
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.confirm import (
    ConfirmPage,
    confirm_action,
    confirm_query,
    posted_idempotency_key,
    safe_next,
)
from console.effects import provider_url
from console.engine.client import EngineClientError, EngineError
from console.engine.models import Provider, ProviderDetail, rejected_entries
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.providers import (
    SPEC_FIELDS,
    ProviderForm,
    example_manifest,
    same_spec,
    schema_defaults,
)
from console.held import held_text, is_held
from console.ids import new_ulid
from console.providers import binding_keys, is_provider_name, manifest_values, secret_names
from console.providers import secret_env_var as env_var
from console.wording import humanised
from console.world import ENGINE_NOTHING_CHANGED, nothing_changed

EMPTY = "—"
LAST_KNOWN = "last_known"
DEFAULT_FRESHNESS = 60
NOT_FOUND = "not_found"
READ_ONLY = "Read-only"
WRITE_CAPABLE = "Write-capable"
UNREAD_TEXT = "This provider couldn't be read"
EMPTY_TEXT = "No providers are registered."
NO_BINDINGS = "None — a story binds no ids to this provider"
BINDINGS_NOTE = "Each story using this provider binds these on its Providers page"
SECRETS_NOTE = "The Engine stores only these references; secret values never leave its environment."


def not_found_text(name: str) -> str:
    return f"No provider named “{name}” is registered"


def count_text(n: int) -> str:
    return "1 provider" if n == 1 else f"{n} providers"


@dataclass(frozen=True)
class Enum:
    """An enum value as shown (C3) and its raw value for ``title``."""

    text: str
    raw: str


def _enum(value: str) -> Enum:
    return Enum(humanised(value) if value else EMPTY, value)


def _freshness(minutes: int | None) -> str:
    """ "10 min", or "60 min (default)" when the Engine's value is null."""
    if minutes is None:
        return f"{DEFAULT_FRESHNESS} min (default)"
    return f"{minutes} min"


def _yes_no(value: bool | None) -> str:
    if value is None:
        return EMPTY
    return "yes" if value else "no"


# --- the list -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListRow:
    """One provider's row; ``unread`` (the raw entry as JSON) for an entry that didn't parse."""

    name: str = ""
    url: str | None = None
    trust_tier: Enum | None = None
    transport: Enum | None = None
    writes: str = ""
    confirmation: Enum | None = None
    unavailability: Enum | None = None
    freshness: str = ""
    values: str = EMPTY
    updated_at: str = ""
    unread: str | None = None


def _list_row(p: Provider) -> ListRow:
    names = [v.name for v in manifest_values(p.manifest, p.transport)]
    return ListRow(
        name=p.name,
        url=provider_url(p.name),
        trust_tier=_enum(p.trust_tier),
        transport=_enum(p.transport),
        writes=WRITE_CAPABLE if p.write_capable else READ_ONLY,
        confirmation=_enum(p.confirmation) if p.write_capable and p.confirmation else None,
        unavailability=_enum(p.unavailability),
        freshness=(
            f" · fresh for {_freshness(p.freshness_minutes)}"
            if p.unavailability == LAST_KNOWN
            else ""
        ),
        values=", ".join(names) if names else EMPTY,
        updated_at=p.updated_at,
    )


def _unread_row(raw: Any) -> ListRow:
    return ListRow(unread=json.dumps(raw, indent=2, ensure_ascii=False, default=str))


def list_rows(providers: list[Provider], unparsed: dict[str, Any]) -> list[ListRow]:
    """The parsed providers in the Engine's order, with a "couldn't be read" row for each raw
    entry ``Provider.from_json`` rejects, at its position in the raw list (D15)."""
    if "providers" not in unparsed:
        return [_list_row(p) for p in providers]
    raw = unparsed["providers"]
    rejected = rejected_entries(raw, Provider.from_json)
    if not isinstance(raw, list):
        return [*(_list_row(p) for p in providers), *(_unread_row(r) for r in rejected)]
    rejected_ids = {id(r) for r in rejected}
    parsed = iter(providers)
    rows: list[ListRow] = []
    for entry in raw:
        if id(entry) in rejected_ids:
            rows.append(_unread_row(entry))
        else:
            provider = next(parsed, None)
            if provider is not None:
                rows.append(_list_row(provider))
    return rows


def _held_context(request: HttpRequest, exc: EngineError) -> dict[str, str]:
    """#87's held fragment without story links: the held text and Retry."""
    return {"held_text": held_text(exc.retry_after), "retry_url": request.get_full_path()}


@require_safe
def provider_list(request: HttpRequest) -> HttpResponse:
    """``GET /admin/external-providers``: exactly one Engine call."""
    context: dict[str, Any] = {}
    try:
        page = engine_client_for(request).list_providers()
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if is_held(exc):
            context["held"] = _held_context(request, exc)
            return render(request, "console/engine/providers.html", context, status=503)
        context["engine_error"] = panel_for(exc)
        return render(request, "console/engine/providers.html", context)
    context.update(
        config_revision=page.config_revision,
        rows=list_rows(page.providers, dict(page.unparsed or {})),
        count_text=count_text(len(page.providers)),
        empty_text=EMPTY_TEXT,
        unread_text=UNREAD_TEXT,
    )
    return render(request, "console/engine/providers.html", context)


# --- one provider ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistrationRow:
    """A Registration row: its label and text, the raw enum value for ``title`` (``""``: none),
    an Engine timestamp to render through ``engine_time``, or a whole-row ``line``."""

    label: str = ""
    text: str = ""
    raw: str = ""
    time: str = ""
    code: bool = False
    line: str = ""


@dataclass(frozen=True)
class SecretRow:
    reference: str
    env_var: str


def _enum_row(label: str, value: str | None) -> RegistrationRow:
    if value is None:
        return RegistrationRow(label, EMPTY)
    return RegistrationRow(label, humanised(value) if value else EMPTY, raw=value)


def registration_rows(p: ProviderDetail) -> list[RegistrationRow]:
    return [
        RegistrationRow("Name", p.name, code=True),
        _enum_row("Trust tier", p.trust_tier),
        _enum_row("Transport", p.transport),
        RegistrationRow("Write-capable", _yes_no(p.write_capable)),
        _enum_row("Confirmation", p.confirmation),
        RegistrationRow("Dedupes dispatch", _yes_no(p.dedupes_dispatch)),
        RegistrationRow("Returns resulting values", _yes_no(p.returns_resulting_values)),
        _enum_row("When unavailable", p.unavailability),
        RegistrationRow("Freshness window", _freshness(p.freshness_minutes)),
        RegistrationRow("Confirm wait (max)", f"{p.confirm_wait_max_seconds} s"),
        RegistrationRow("Created", time=p.created_at),
        RegistrationRow("Updated", time=p.updated_at),
        RegistrationRow(line=f"Last changed at config revision {p.changed_at_revision}"),
        RegistrationRow(line=f"Read at config revision {p.config_revision}"),
    ]


def _missing(request: HttpRequest, name: str) -> HttpResponse:
    context = {"name": name, "missing": True, "not_found_text": not_found_text(name)}
    return render(request, "console/engine/provider.html", context, status=404)


@require_safe
def provider_detail(request: HttpRequest, name: str) -> HttpResponse:
    """``GET /admin/external-providers/{name}``: no call for a malformed name, else exactly
    one."""
    if not is_provider_name(name):
        return _missing(request, name)
    context: dict[str, Any] = {"name": name}
    try:
        provider = engine_client_for(request).get_provider(name)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 404 and exc.code == NOT_FOUND:
            return _missing(request, name)
        if is_held(exc):
            context["held"] = _held_context(request, exc)
            return render(request, "console/engine/provider.html", context, status=503)
        context["engine_error"] = panel_for(exc)
        return render(request, "console/engine/provider.html", context)
    context.update(
        provider=provider,
        registration=registration_rows(provider),
        values=manifest_values(provider.manifest, provider.transport),
        binding_keys=binding_keys(provider.manifest),
        bindings_note=BINDINGS_NOTE,
        no_bindings=NO_BINDINGS,
        secrets=[SecretRow(f"{{secret.{s}}}", env_var(s)) for s in secret_names(provider.manifest)],
        secrets_note=SECRETS_NOTE,
        delete_query=confirm_query(name, request.get_full_path()),
    )
    return render(request, "console/engine/provider.html", context)


# --- writes (#246): register, replace, delete -----------------------------------------------------
#
# Register (``engine:provider-new``) and Replace (``engine:provider-edit``) share one form and one
# refusal path. Every valid, changed submission makes exactly one write; an invalid form, an
# unchanged replacement and every GET make none. D8's re-render rule: local invalid input keeps the
# posted idempotency key; an answered Engine refusal mints a fresh one; a transport failure, timeout
# or unreadable answer keeps the posted key (a resubmit replays). Provider writes send no expected
# revision. Replace compares against the signed snapshot the page rendered from (salt
# :data:`SNAPSHOT_SALT`), re-read once when it is missing or tampered; D8's "nothing to change"
# compares the answer's ``config_revision`` with the snapshot's.

SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.providers.snapshot"
BAD_RESPONSE = "bad_response"
PROVIDER_EXISTS = "provider_exists"
INVALID_MANIFEST = "invalid_manifest"
PROVIDER_IN_USE = "provider_in_use"
HOST_NOT_ALLOWED = "host_not_allowed"
IN_USE_LISTED_MAX = 100
MANIFEST_PATH = "body.manifest"
MANIFEST_REFUSED = "The Engine refused this manifest"
NO_CHANGES = "No changes — nothing was sent"
HOST_HINT = " — add the host to `provider_allowed_hosts` in Deployment settings"
APPLIES = (
    "It applies from the next operation that resolves the provider; a turn already under way "
    "keeps what it resolved."
)
CHANGED_ELSEWHERE = (
    " The deployment's configuration also changed elsewhere since you loaded this page; see "
    "Config changes"
)
DELETE_QUESTION = (
    "Delete {name} from the registry? Stories bound to it keep their binding, but nothing can "
    "read or write its values until it is registered again."
)


def exists_text(name: str) -> str:
    return f"A provider named “{name}” is already registered — open it to replace its registration"


def gone_text(name: str) -> str:
    return f"{name} is no longer registered — register it again"


def _settings_hosts_url() -> str:
    return reverse("engine:settings") + "#settings-external-providers"


@dataclass(frozen=True)
class ManifestFailure:
    """One ``invalid_manifest`` line: the member (``""``: the manifest itself), the Engine's
    message, its reason (``title``) and, for ``host_not_allowed``, the settings link."""

    member: str
    message: str
    reason: str = ""
    settings_url: str = ""
    hint: str = ""


@dataclass(frozen=True)
class Snapshot:
    """What a Replace page rendered from: the name, the read's ten members, its revision."""

    name: str
    spec: dict[str, Any]
    config_revision: int

    def signed(self) -> str:
        return signing.dumps(
            {"name": self.name, "spec": self.spec, "config_revision": self.config_revision},
            salt=SNAPSHOT_SALT,
            compress=True,
        )

    @classmethod
    def unsigned(cls, value: str, name: str) -> Snapshot | None:
        """The posted snapshot, or ``None`` when missing, tampered or not this provider's."""
        try:
            raw = signing.loads(value, salt=SNAPSHOT_SALT)
        except signing.BadSignature:
            return None
        if not isinstance(raw, dict) or raw.get("name") != name:
            return None
        spec, revision = raw.get("spec"), raw.get("config_revision")
        if not isinstance(spec, dict) or set(spec) != set(SPEC_FIELDS):
            return None
        if not isinstance(revision, int) or isinstance(revision, bool):
            return None
        return cls(name, spec, revision)

    @classmethod
    def of(cls, provider: ProviderDetail) -> Snapshot:
        return cls(
            provider.name,
            {member: getattr(provider, member) for member in SPEC_FIELDS},
            provider.config_revision,
        )


def _member(path: str) -> str:
    """``body.manifest.values.x.read.url`` → ``values.x.read.url``; ``body``/``body.manifest``
    → ``""`` (the manifest itself)."""
    if path in ("body", MANIFEST_PATH):
        return ""
    if path.startswith(MANIFEST_PATH + "."):
        return path[len(MANIFEST_PATH) + 1 :]
    return path.removeprefix("body.")


def _is_manifest_path(path: str) -> bool:
    return path in ("body", MANIFEST_PATH) or path.startswith(
        (MANIFEST_PATH + ".", MANIFEST_PATH + "[")
    )


def _failure(member: str, message: str, reason: Any) -> ManifestFailure:
    reason_text = reason if isinstance(reason, str) else ""
    if reason_text == HOST_NOT_ALLOWED:
        return ManifestFailure(member, message, reason_text, _settings_hosts_url(), HOST_HINT)
    return ManifestFailure(member, message, reason_text)


def _failures(exc: EngineError) -> list[tuple[str, str, Any]]:
    """``(path, message, reason)`` per failure: ``details.errors[]`` (reason: the entry's
    ``reason``, else its ``type``), else ``details.field`` with the error's message."""
    details = exc.details
    found: list[tuple[str, str, Any]] = []
    listed = details.get("errors")
    if isinstance(listed, list):
        for item in listed:
            if isinstance(item, dict) and isinstance(item.get("field"), str):
                message = item.get("message")
                found.append(
                    (
                        item["field"],
                        message if isinstance(message, str) and message else exc.message,
                        item.get("reason") or item.get("type"),
                    )
                )
    if not found and isinstance(details.get("field"), str):
        found.append((details["field"], exc.message, details.get("reason")))
    return found


@dataclass
class Refusal:
    """What a refused write adds to the re-rendered form."""

    manifest_failures: list[ManifestFailure]
    name_exists_url: str = ""
    name_taken: str = ""
    above: str = ""  # a line above the form (Replace's 404)
    above_url: str = ""
    engine_error: Any = None
    held: dict[str, str] | None = None
    keep_key: bool = False


def _place_422(exc: EngineError, form: ProviderForm) -> list[ManifestFailure] | None:
    """A 422's failures: manifest paths become lines, the rest go through
    ``apply_engine_errors``. ``None`` when nothing was placeable."""
    failures = _failures(exc)
    lines = [
        _failure(_member(path), message, reason)
        for path, message, reason in failures
        if _is_manifest_path(path)
    ]
    rest = [
        {"field": path, "message": message}
        for path, message, _ in failures
        if not _is_manifest_path(path)
    ]
    if not failures and exc.code == INVALID_MANIFEST:
        lines = [_failure("", exc.message, exc.details.get("reason"))]
    if rest:
        apply_engine_errors(
            EngineError(exc.code, exc.message, {"errors": rest}, exc.http_status), form
        )
    if lines:
        form.add_error("manifest", MANIFEST_REFUSED)
    if not lines and not rest:
        return None
    return lines


def _refusal(
    request: HttpRequest, exc: EngineClientError, form: ProviderForm, name: str | None
) -> Refusal:
    """Route a refused register/replace (``name``: the replaced provider, ``None`` when
    registering)."""
    log_engine_error(request, exc)
    if not isinstance(exc, EngineError) or exc.code == BAD_RESPONSE:
        return Refusal([], engine_error=panel_for(exc), keep_key=True)  # D8: outcome unknown
    if is_held(exc):
        return Refusal([], held=_held_context(request, exc))
    if exc.http_status == 409 and exc.code == PROVIDER_EXISTS and "name" in form.fields:
        taken = exc.details.get("provider")
        shown = taken if isinstance(taken, str) and taken else form.cleaned_data.get("name", "")
        form.add_error("name", exists_text(shown))
        return Refusal([], name_exists_url=provider_url(shown) or "", name_taken=shown)
    if name is not None and exc.http_status == 404:
        return Refusal([], above=gone_text(name), above_url=reverse("engine:provider-new"))
    if exc.http_status == 422:
        lines = _place_422(exc, form)
        if lines is not None:
            return Refusal(lines)
    return Refusal([], engine_error=panel_for(exc))


def _render_form(
    request: HttpRequest,
    form: ProviderForm,
    *,
    name: str | None,
    key: str | None = None,
    snapshot: Snapshot | None = None,
    refusal: Refusal | None = None,
    status: int = 200,
) -> HttpResponse:
    refusal = refusal or Refusal([])
    context: dict[str, Any] = {
        "form": form,
        "name": name,
        "registering": name is None,
        "idempotency_key": key or new_ulid(),
        "snapshot": snapshot.signed() if snapshot is not None else "",
        "manifest_failures": refusal.manifest_failures,
        "name_exists_url": refusal.name_exists_url,
        "name_exists_text": exists_text(refusal.name_taken) if refusal.name_taken else "",
        "name_taken": refusal.name_taken,
        "above": refusal.above,
        "above_url": refusal.above_url,
        "engine_error": refusal.engine_error,
        "held": refusal.held,
        "example": example_manifest() if name is None else None,
    }
    if refusal.held is not None:
        status = 503
    return render(request, "console/engine/provider_form.html", context, status=status)


def _key_for(refusal: Refusal, posted: str) -> str | None:
    return posted if refusal.keep_key else None


@require_http_methods(["GET", "HEAD", "POST"])
def provider_new(request: HttpRequest) -> HttpResponse:
    """GET: the empty form (no call). POST: validate, then one ``register_provider``."""
    if request.method != "POST":
        return _render_form(
            request, ProviderForm(registering=True, initial=schema_defaults()), name=None
        )
    form = ProviderForm(request.POST, registering=True, initial=schema_defaults())
    key = posted_idempotency_key(request)
    if not form.is_valid():
        return _render_form(request, form, name=None, key=key)  # D8: nothing sent
    try:
        written = engine_client_for(request).register_provider(
            form.registration(), idempotency_key=key
        )
    except EngineClientError as exc:
        refusal = _refusal(request, exc, form, None)
        return _render_form(request, form, name=None, key=_key_for(refusal, key), refusal=refusal)
    messages.success(
        request,
        f"Registered {written.name} — config revision {written.config_revision}, "
        f"idempotency key {written.idempotency_key or key}",
    )
    return redirect(reverse("engine:provider", args=[written.name]))


def _read_error(request: HttpRequest, exc: EngineClientError, name: str) -> HttpResponse:
    """A failed read on a page with no form yet: the not-found line, held or the panel."""
    log_engine_error(request, exc)
    if isinstance(exc, EngineError) and exc.http_status == 404 and exc.code == NOT_FOUND:
        return _missing(request, name)
    context: dict[str, Any] = {"name": name}
    if is_held(exc):
        context["held"] = _held_context(request, exc)
        return render(request, "console/engine/provider.html", context, status=503)
    context["engine_error"] = panel_for(exc)
    return render(request, "console/engine/provider.html", context)


@require_http_methods(["GET", "HEAD", "POST"])
def provider_edit(request: HttpRequest, name: str) -> HttpResponse:
    """GET: one ``get_provider`` read, as a form. POST: validate; unchanged → no call; else one
    ``replace_provider`` with all ten members."""
    if not is_provider_name(name):
        return _missing(request, name)
    client = engine_client_for(request)
    if request.method != "POST":
        try:
            provider = client.get_provider(name)
        except EngineClientError as exc:
            return _read_error(request, exc, name)
        read = Snapshot.of(provider)
        form = ProviderForm(registering=False, initial=read.spec)
        return _render_form(request, form, name=name, snapshot=read)

    posted = Snapshot.unsigned(request.POST.get(SNAPSHOT_FIELD, ""), name)
    initial = posted.spec if posted is not None else {}
    form = ProviderForm(request.POST, registering=False, initial=initial)
    key = posted_idempotency_key(request)
    if not form.is_valid():
        return _render_form(request, form, name=name, key=key, snapshot=posted)  # D8
    snapshot = posted
    if snapshot is None:  # missing or tampered: read once
        try:
            snapshot = Snapshot.of(client.get_provider(name))
        except EngineClientError as exc:
            refusal = _refusal(request, exc, form, name)
            status = 404 if refusal.above else 200
            return _render_form(request, form, name=name, key=key, refusal=refusal, status=status)
    spec = form.spec()
    if same_spec(spec, snapshot.spec):
        messages.info(request, NO_CHANGES)
        return redirect(reverse("engine:provider", args=[name]))
    try:
        saved = client.replace_provider(name, spec, idempotency_key=key)
    except EngineClientError as exc:
        refusal = _refusal(request, exc, form, name)
        return _render_form(
            request, form, name=name, key=_key_for(refusal, key), snapshot=snapshot, refusal=refusal
        )
    if nothing_changed(saved.config_revision, snapshot.config_revision):
        messages.info(request, ENGINE_NOTHING_CHANGED)  # D8
        return redirect(reverse("engine:provider", args=[name]))
    text = (
        f"Saved {name} — config revision {saved.config_revision}, idempotency key "
        f"{saved.idempotency_key or key}. {APPLIES}"
    )
    if saved.config_revision > snapshot.config_revision + 1:
        text += CHANGED_ELSEWHERE
    messages.success(request, text)
    return redirect(reverse("engine:provider", args=[name]))


@dataclass(frozen=True)
class InUseStory:
    id: str
    url: str


def _in_use(stories: object) -> list[InUseStory] | None:
    if not isinstance(stories, list):
        return None
    return [
        InUseStory(str(s), reverse("stories:detail", args=[s]) if isinstance(s, str) and s else "")
        for s in stories
    ]


@require_http_methods(["GET", "HEAD", "POST"])
def provider_delete(request: HttpRequest, name: str) -> HttpResponse:
    """GET: the confirm page (no call). POST: one ``delete_provider``."""
    if not is_provider_name(name):
        return _missing(request, name)
    detail = reverse("engine:provider", args=[name])
    page = ConfirmPage(
        title="Delete provider",
        question=DELETE_QUESTION,
        emphasis=name,
        confirm_label="Delete",
        cancel_url=safe_next(request, detail),
        facts=(("Provider", name),),
        danger=True,
    )

    def perform(key: str) -> HttpResponse:
        try:
            engine_client_for(request).delete_provider(name, idempotency_key=key)
        except EngineClientError as exc:
            if not isinstance(exc, EngineError):
                raise
            if exc.http_status == 409 and exc.code == PROVIDER_IN_USE:
                log_engine_error(request, exc)
                stories = _in_use(exc.details.get("stories"))
                context = {
                    "name": name,
                    "stories": stories,
                    "at_most": stories is not None and len(stories) == IN_USE_LISTED_MAX,
                }
                return render(
                    request, "console/engine/provider_delete_refused.html", context, status=409
                )
            if exc.http_status == 404:
                log_engine_error(request, exc)
                messages.warning(
                    request,
                    f"{name} isn't registered (it may already have been deleted) — nothing changed",
                )
                return redirect(reverse("engine:providers"))
            if is_held(exc):
                log_engine_error(request, exc)
                held = {"name": name, "held": _held_context(request, exc)}
                return render(request, "console/engine/provider.html", held, status=503)
            raise
        messages.success(request, f"Deleted {name} — idempotency key {key}")
        return redirect(reverse("engine:providers"))

    return confirm_action(request, page, perform)

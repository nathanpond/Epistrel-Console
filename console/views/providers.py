"""The deployment's external-data providers, read-only, in the Engine panel (#240).

- ``/engine/providers/`` (``engine:providers``): every registered provider in the Engine's order
  (by name) with the deployment's ``config_revision``; one ``list_providers()`` read. Entries the
  Console couldn't read get one "couldn't be read" row each, at their raw position (D15's
  display rule through ``rejected_entries``); the count line counts parsed providers only.
- ``/engine/providers/<name>/`` (``engine:provider``): one provider's whole registration, its
  values, the ``{binding.<key>}`` ids a story binds, the ``{secret.<NAME>}`` references it
  resolves from the Engine's environment, and its manifest verbatim; one ``get_provider(name)``
  read. A name failing ``is_provider_name`` is a 404 with no call.

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

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

from console.effects import provider_url
from console.engine.client import EngineClientError, EngineError
from console.engine.models import Provider, ProviderDetail, rejected_entries
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.held import held_text, is_held
from console.providers import binding_keys, is_provider_name, manifest_values, secret_names
from console.providers import secret_env_var as env_var
from console.wording import humanised

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
    )
    return render(request, "console/engine/provider.html", context)

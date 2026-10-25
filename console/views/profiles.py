"""Engine profiles (#78): browse every profile, its settings at any version, and its versions.

- ``/profiles/``: ``GET /engine-profiles`` with the GET filters (owner, one relation, "Show
  deleted"; an invalid filter makes no call), cursor-paged ("Next page" keeps the filters, Apply
  drops the cursor). The deployment default (``/admin/settings``) is marked "default"; when that
  read fails the list warns instead. A 422 (a stale cursor) shows #43's panel with "Start over".
- ``/profiles/<id>/`` (``?version=N``): the header and the settings laid out by the profile
  schema (``console.profile_schema``). The profile, the deployment settings and the schema (cached
  60 s) are read concurrently on the fan-out pool. An invalid ``?version`` is ignored (latest,
  with a flash); an unknown one is the Engine's 404 panel (the profile read's error escapes).
- ``/profiles/<id>/versions/``: every version, newest first (the Engine sends oldest first),
  each linking to ``?version=N``; ``?viewed=N`` highlights the one the detail page showed.

The owner and relations panel is #79's. #84 adds Edit (hidden on a deleted profile; "Edit latest
(vM)" on a past version), Copy (at the version viewed) and Delete (disabled for the deployment
default); its views are ``console.views.profile_edit``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from console.engine.client import EngineClient, EngineClientError
from console.engine.models import DeploymentSettings, EngineProfile, ProfileSchema, ProfileVersion
from console.engine.pool import fan_out
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.forms.profiles import ProfileFilterForm
from console.profile_schema import get_profile_schema_cached, settings_view
from console.views.ownership import ownership_context

DEFAULT_UNREAD = "Couldn't read the deployment default"
SCHEMA_UNREAD = "Couldn't read the profile schema — settings shown raw"
INVALID_VERSION = "Ignored invalid version"
DELETED_NO_EDIT = "Deleted profiles can't be edited — copy it to change it"
DEFAULT_NO_DELETE = "Deployment default — choose another default first"
VERSION_PARAM = "version"
VIEWED_PARAM = "viewed"


def detail_url(profile_id: str, version: int | None = None) -> str:
    """The profile's page, at ``version`` when given."""
    url = reverse("profiles:detail", args=[profile_id])
    return f"{url}?{urlencode({VERSION_PARAM: version})}" if version is not None else url


def stories_url(profile_id: str) -> str:
    """``/stories/?profile=<id>``: the stories running under the profile (any version)."""
    return f"{reverse('stories:list')}?{urlencode({'profile': profile_id})}"


def _positive(raw: str | None) -> int | None:
    """``raw`` as an integer of at least 1, else ``None``."""
    if raw is None:
        return None
    try:
        number = int(raw.strip())
    except ValueError:
        return None
    return number if number >= 1 else None


# --- list -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileRow:
    profile: EngineProfile
    url: str
    is_default: bool


def _deleted_default_note(client: EngineClient, default_id: str, request: HttpRequest) -> str:
    """ "The deployment default <id> is deleted", when it is (one read; a failed read says
    nothing more, the list already shows what it can)."""
    try:
        default = client.get_engine_profile(default_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return ""
    return default_id if default.deleted_at is not None else ""


@require_safe
def profile_list(request: HttpRequest) -> HttpResponse:
    form = ProfileFilterForm(request.GET)
    rows: list[ProfileRow] = []
    next_query = ""
    engine_error: ErrorPanel | None = None
    default_warning = ""
    deleted_default = ""
    if form.is_valid():
        client = engine_client_for(request)
        try:
            items, next_cursor = client.list_engine_profiles(**form.list_query())
        except EngineClientError as exc:
            log_engine_error(request, exc)
            engine_error = panel_for(exc)
        else:
            default_id: str | None = None
            try:
                default_id = client.get_deployment_settings().default_engine_profile_id
            except EngineClientError as exc:
                log_engine_error(request, exc)
                default_warning = DEFAULT_UNREAD
            rows = [ProfileRow(p, detail_url(p.id), p.id == default_id) for p in items]
            if default_id and not form.show_deleted and not any(r.is_default for r in rows):
                deleted_default = _deleted_default_note(client, default_id, request)
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
        "show_deleted": form.show_deleted,
        "default_warning": default_warning,
        "deleted_default": deleted_default,
    }
    return render(request, "console/profiles/list.html", context)


# --- detail ---------------------------------------------------------------------------------------


def _concurrently(reads: dict[str, Callable[[], Any]]) -> dict[str, Any]:
    """Each read's value or :class:`EngineClientError`, run on the fan-out pool."""
    return fan_out(lambda name: reads[name](), reads)


@require_safe
def profile_detail(request: HttpRequest, profile_id: str) -> HttpResponse:
    raw = request.GET.get(VERSION_PARAM)
    version = _positive(raw)
    if raw is not None and version is None:
        messages.warning(request, INVALID_VERSION)
    client = engine_client_for(request)
    results = _concurrently(
        {
            "profile": lambda: client.get_engine_profile(profile_id, version=version),
            "settings": client.get_deployment_settings,
            "schema": lambda: get_profile_schema_cached(client),
        }
    )
    profile = results["profile"]
    if isinstance(profile, EngineClientError):
        raise profile  # the Engine's panel (#43): 404 for an unknown profile or version
    deployment = results["settings"]
    warnings: list[str] = []
    defaults: dict[str, Any] | None = None
    default_id: str | None = None
    if isinstance(deployment, DeploymentSettings):
        default_id = deployment.default_engine_profile_id
        raw_defaults = deployment.profile_defaults
        defaults = raw_defaults if isinstance(raw_defaults, dict) else {}
    else:
        log_engine_error(request, deployment)
        warnings.append(DEFAULT_UNREAD)
    schema = results["schema"]
    if not isinstance(schema, ProfileSchema):
        log_engine_error(request, schema)
        warnings.append(SCHEMA_UNREAD)
        schema = None
    context = {
        "profile": profile,
        "view": settings_view(schema, profile.settings, defaults),
        "warnings": warnings,
        "is_default": default_id == profile.id,
        "is_latest": profile.version == profile.current_version,
        "latest_url": detail_url(profile.id),
        "versions_url": (
            f"{reverse('profiles:versions', args=[profile.id])}?"
            f"{urlencode({VIEWED_PARAM: profile.version})}"
        ),
        "stories_url": stories_url(profile.id),
        # #84: Edit (the latest version), Copy (the version viewed) and Delete.
        "edit_url": reverse("profiles:edit", args=[profile.id]),
        "copy_url": (
            f"{reverse('profiles:copy', args=[profile.id])}?"
            f"{urlencode({VERSION_PARAM: profile.version})}"
        ),
        "delete_url": (
            f"{reverse('profiles:delete', args=[profile.id])}?"
            f"{urlencode({'name': profile.name, 'next': detail_url(profile.id)})}"
        ),
        "deleted_no_edit": DELETED_NO_EDIT,
        "default_no_delete": DEFAULT_NO_DELETE,
        # #79: the owner and relations panel, its relations read on its own.
        "ownership": ownership_context(
            request,
            client,
            "engine_profile",
            profile.id,
            name=profile.name,
            owner=profile.owner_principal,
        ),
    }
    return render(request, "console/profiles/detail.html", context)


# --- versions -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionRow:
    version: ProfileVersion
    url: str
    latest: bool
    viewed: bool


@require_safe
def profile_versions(request: HttpRequest, profile_id: str) -> HttpResponse:
    client = engine_client_for(request)
    results = _concurrently(
        {
            "profile": lambda: client.get_engine_profile(profile_id),
            "versions": lambda: client.list_engine_profile_versions(profile_id),
        }
    )
    for name in ("profile", "versions"):
        if isinstance(results[name], EngineClientError):
            raise results[name]
    profile: EngineProfile = results["profile"]
    versions: list[ProfileVersion] = results["versions"]
    viewed = _positive(request.GET.get(VIEWED_PARAM))
    rows = [
        VersionRow(
            v,
            detail_url(profile.id, v.version),
            v.version == profile.current_version,
            v.version == viewed,
        )
        for v in reversed(versions)
    ]
    context = {"profile": profile, "rows": rows, "detail_url": detail_url(profile.id)}
    return render(request, "console/profiles/versions.html", context)

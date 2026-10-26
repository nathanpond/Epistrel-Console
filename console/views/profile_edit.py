"""Create, edit, copy and delete engine profiles (#84), validated and impact-checked first.

- ``/profiles/new/``: name, owner and the settings form generated from the profile schema
  (``console.forms.profile_schema_form``), every setting unset. **Validate** posts the settings to
  ``POST /engine-profiles/<deployment default>/validate`` (the Engine validates the body's
  settings whatever id the path names); **Save** validates the same way, then ``POST
  /engine-profiles``. When ``/admin/settings`` can't be read, Validate is disabled and Save lets
  the Engine check.
- ``/profiles/<id>/edit/``: the latest version's stored keys pre-filled. Save re-reads the
  profile (version check against the signed page facts), validates, then ``PUT``s with
  ``ignore_warnings: false``. ``applied: false`` renders the impact report with "Apply anyway"
  (``/profiles/<id>/apply/``: a signed payload, a fresh idempotency key, the same re-read and
  version check, then ``ignore_warnings: true``).
- ``/profiles/<id>/copy/?version=N``: name and owner, then ``POST …/copy?version=N``.
- ``/profiles/<id>/delete/``: #43's confirm page, then ``DELETE``; the answer's warnings are
  flashed. The deployment default's 409 (``details.reason`` ``is_default``) links to the
  deployment settings.

The page facts (the loaded version, name and settings, the deployment's ``profile_defaults`` and
default profile id, the schema's digest) travel signed (#64 convention 3's helper), so an
invalid form, an unchanged one and a stale schema make no Engine call. Invalid validation
blocks Save with zero writes; warnings alone never block.
"""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_POST

from console import twostage
from console.acting import acting_principal
from console.confirm import ConfirmPage, confirm_action, posted_idempotency_key, shown_name
from console.engine.choices import StoryLabel, story_labels
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import (
    DeploymentSettings,
    EngineProfile,
    ImpactReport,
    ProfileEdit,
    ProfileSchema,
    ProfileValidation,
)
from console.engine.pool import fan_out
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, is_htmx, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.principal import remember_principals
from console.forms.profile_schema_form import (
    SCHEMA_CHANGED,
    PlacedStories,
    schema_digest,
    validation_banner,
    warning_text,
)
from console.forms.profiles import (
    ProfileCopyForm,
    ProfileCreateForm,
    ProfileEditForm,
    copy_name,
)
from console.ids import new_ulid
from console.merge_patch import _same
from console.profile_schema import get_profile_schema_cached
from console.views.profiles import DELETED_NO_EDIT, detail_url

FORM_KIND = "profile-form"
APPLY_KIND = "profile-apply"
SNAPSHOT_FIELD = "snapshot"
ACTION_FIELD = "action"
VALIDATE = "validate"
EDIT_TEMPLATE = "console/profiles/edit.html"
FORM_TEMPLATE = "console/profiles/_form.html"
INVALID_PROFILE = "invalid_profile"
PROFILE_DELETED = "profile_deleted"
IS_DEFAULT = "is_default"
LIST_CAP = 25

EXPIRED = "This form expired — your changes were not saved; check and submit again"
NO_CHANGE = "No change"
DELETED_ON_SAVE = "This profile was deleted — copy it to change it"
DEFAULT_UNREAD = "Can't read the deployment default — Save will let the Engine check"
MOVED = (
    "This profile changed since you loaded it (now version {version}). Your edits are below; "
    "reload to discard them."
)
DELETE_LINE = "Stories using it keep running on their pinned version; it can't be assigned again."
IS_DEFAULT_TEXT = "The deployment default can't be deleted — choose another default first"
UPGRADE_UNAVAILABLE = (
    "This story has history and Lite can't backfill — it keeps its current configuration"
)


# --- page facts -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class FormFacts:
    """What the form page was rendered from: the profile as loaded (``version`` ``None`` on a
    create), the deployment's ``profile_defaults`` (``None``: unread) and default profile id,
    and the schema's digest."""

    profile_id: str = ""
    version: int | None = None
    name: str = ""
    owner: str = ""
    preset: bool = False
    settings: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] | None = None
    default_id: str | None = None
    digest: str = ""

    def signed(self) -> str:
        return twostage.sign(FORM_KIND, self.profile_id, None, asdict(self))

    @classmethod
    def unsigned(cls, token: str, profile_id: str) -> FormFacts | None:
        """The facts a posted page carried; ``None`` when tampered, expired or another's."""
        try:
            payload = twostage.unsign(FORM_KIND, token)
        except twostage.ConfirmationExpired:
            return None
        raw = payload.base
        if payload.target_id != profile_id or not isinstance(raw, dict):
            return None
        try:
            facts = cls(**raw)
        except TypeError:
            return None
        return facts if isinstance(facts.settings, dict) else None


def _deployment(result: object, request: HttpRequest) -> tuple[dict[str, Any] | None, str | None]:
    """``(profile_defaults, default profile id)`` from a settings read (``None``s when it
    failed)."""
    if isinstance(result, DeploymentSettings):
        defaults = result.profile_defaults if isinstance(result.profile_defaults, dict) else {}
        return defaults, result.default_engine_profile_id
    if isinstance(result, EngineClientError):
        log_engine_error(request, result)
    return None, None


def _reads(reads: Mapping[str, Callable[[], Any]]) -> dict[str, Any]:
    """Each read's value or ``EngineClientError``, run concurrently on the fan-out pool."""
    return fan_out(lambda name: reads[name](), list(reads))


def _schema(result: object) -> ProfileSchema:
    """The schema read's value; its error escapes to the shared panel (no form without it)."""
    if isinstance(result, EngineClientError):
        raise result
    if not isinstance(result, ProfileSchema):
        raise TypeError("the schema read answered no ProfileSchema")
    return result


def edit_url(profile_id: str) -> str:
    return reverse("profiles:edit", args=[profile_id])


# --- rendering ------------------------------------------------------------------------------------


@dataclass
class Shown:
    """Everything above the form besides the form itself."""

    banner: str = ""
    banner_ok: bool = False
    engine_error: ErrorPanel | None = None
    moved: str = ""
    stories: PlacedStories | None = None
    labels: dict[str, StoryLabel] = field(default_factory=dict)


def _render(
    request: HttpRequest,
    facts: FormFacts,
    form: ProfileEditForm,
    shown: Shown | None = None,
) -> HttpResponse:
    create = facts.version is None
    shown = shown or Shown()
    context = {
        "create": create,
        "facts": facts,
        "form": form,
        "nodes": form.shown(),
        "snapshot": facts.signed(),
        "idempotency_key": new_ulid(),  # minted at each render (#43)
        "action_url": reverse("profiles:new") if create else edit_url(facts.profile_id),
        "cancel_url": reverse("profiles:index") if create else detail_url(facts.profile_id),
        "validate_disabled": create and facts.default_id is None,
        "default_unread": DEFAULT_UNREAD if create and facts.default_id is None else "",
        "shown": shown,
        "story_rows": _story_rows(shown),
        "profile_rows": _profile_rows(shown.stories),
    }
    template = FORM_TEMPLATE if is_htmx(request) else EDIT_TEMPLATE
    return render(request, template, context)


@dataclass(frozen=True)
class LinkRow:
    text: str
    url: str
    note: str = ""


def _story_rows(shown: Shown) -> list[LinkRow]:
    if shown.stories is None:
        return []
    rows: list[LinkRow] = []
    for story_id in shown.stories.stories:
        label = shown.labels.get(story_id)
        rows.append(
            LinkRow(
                label.text if label is not None else story_id,
                reverse("stories:detail", args=[story_id]),
                label.error or "" if label is not None else "",
            )
        )
    return rows


def _profile_rows(stories: PlacedStories | None) -> list[LinkRow]:
    """``details.profiles`` (``<id>@<version>``) linked to that version's page."""
    if stories is None:
        return []
    rows: list[LinkRow] = []
    for item in stories.profiles:
        profile_id, _, raw = item.partition("@")
        version = int(raw) if raw.isdigit() else None
        rows.append(LinkRow(item, detail_url(profile_id, version)))
    return rows


def _with_labels(client: EngineClient, shown: Shown) -> Shown:
    if shown.stories is not None and shown.stories.stories:
        shown.labels = story_labels(client, shown.stories.stories)
    return shown


# --- validate -------------------------------------------------------------------------------------


def _validate(
    request: HttpRequest,
    client: EngineClient,
    target_id: str,
    form: ProfileEditForm,
    shown: Shown,
) -> ProfileValidation | None:
    """``POST …/validate`` with the form's settings, placed on the form; ``None`` when the
    Engine could not answer (the panel is set) or refused the shape (placed)."""
    try:
        result = client.validate_engine_profile(target_id, form.settings())
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if (
            isinstance(exc, EngineError)
            and exc.http_status == 422
            and form.place_validation_error(exc)
        ):
            shown.banner = validation_banner(ProfileValidation(False, [], []), form.problem_count)
            return None
        shown.engine_error = panel_for(exc)
        return None
    form.place_validation(result)
    shown.banner = validation_banner(result, form.problem_count)
    shown.banner_ok = result.valid
    return result


def _warning_suffix(result: ProfileValidation | None) -> str:
    if result is None or not result.warnings:
        return ""
    return " Warnings: " + "; ".join(warning_text(w) for w in result.warnings)


# --- create ---------------------------------------------------------------------------------------


def _new_facts(request: HttpRequest, client: EngineClient) -> tuple[FormFacts, ProfileSchema]:
    results = _reads(
        {
            "settings": client.get_deployment_settings,
            "schema": lambda: get_profile_schema_cached(client),
        }
    )
    schema = _schema(results["schema"])
    defaults, default_id = _deployment(results["settings"], request)
    facts = FormFacts(defaults=defaults, default_id=default_id, digest=schema_digest(schema))
    return facts, schema


def _create_form(
    data: Any, schema: ProfileSchema, facts: FormFacts, **kw: Any
) -> ProfileCreateForm:
    return ProfileCreateForm(data, schema=schema, stored=None, defaults=facts.defaults, **kw)


@require_http_methods(["GET", "HEAD", "POST"])
def profile_new(request: HttpRequest) -> HttpResponse:
    client = engine_client_for(request)
    if request.method != "POST":
        facts, schema = _new_facts(request, client)
        form = _create_form(None, schema, facts, initial={"owner": acting_principal(request)})
        return _render(request, facts, form)
    posted = FormFacts.unsigned(request.POST.get(SNAPSHOT_FIELD, ""), "")
    if posted is None:
        facts, schema = _new_facts(request, client)
        form = _create_form(request.POST, schema, facts)
        form.is_valid()
        form.add_error(None, EXPIRED)
        return _render(request, facts, form)
    facts = posted
    schema = get_profile_schema_cached(client)
    form = _create_form(request.POST, schema, facts)
    if schema_digest(schema) != facts.digest:
        form.is_valid()
        form.add_error(None, SCHEMA_CHANGED)
        return _render(request, replace(facts, digest=schema_digest(schema)), form)
    if not form.is_valid():
        return _render(request, facts, form)
    remember_principals(form)
    shown = Shown()
    result: ProfileValidation | None = None
    if facts.default_id is not None:
        result = _validate(request, client, facts.default_id, form, shown)
        if request.POST.get(ACTION_FIELD) == VALIDATE or result is None or not result.valid:
            return _render(request, facts, form, shown)
    elif request.POST.get(ACTION_FIELD) == VALIDATE:
        return _render(request, facts, form, shown)
    name = form.cleaned_data["name"]
    key = posted_idempotency_key(request)
    try:
        created = client.create_engine_profile(
            name=name,
            settings=form.settings(),
            owner=form.cleaned_data["owner"],
            idempotency_key=key,
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        _write_refused(exc, form, shown)
        return _render(request, facts, form, _with_labels(client, shown))
    messages.success(
        request,
        f"Created {created.name} ({created.id}), version {created.current_version}"
        + _warning_suffix(result),
    )
    return redirect(detail_url(created.id))


def _write_refused(exc: EngineClientError, form: ProfileEditForm, shown: Shown) -> None:
    """A refused create or edit: ``invalid_profile`` placed (its stories for the panel), a
    ``validation_error`` at its fields, anything else as #43's panel."""
    if isinstance(exc, EngineError) and exc.http_status == 422:
        if exc.code == INVALID_PROFILE:
            placed = form.place_invalid_profile(exc)
            if placed.stories or placed.profiles:
                shown.stories = placed
            problems = form.problem_count
            invalid = ProfileValidation(False, [], [])
            shown.banner = validation_banner(invalid, problems) if problems else ""
            shown.banner_ok = False
            return
        if form.place_validation_error(exc) or apply_engine_errors(exc, form):
            return
    shown.engine_error = panel_for(exc)


# --- edit -----------------------------------------------------------------------------------------


def _edit_facts(
    profile: EngineProfile, results: Mapping[str, Any], request: HttpRequest
) -> FormFacts:
    schema = _schema(results["schema"])
    defaults, default_id = _deployment(results["settings"], request)
    return FormFacts(
        profile_id=profile.id,
        version=profile.current_version,
        name=profile.name,
        owner=profile.owner_principal,
        preset=profile.preset,
        settings=dict(profile.settings),
        defaults=defaults,
        default_id=default_id,
        digest=schema_digest(schema),
    )


def _edit_form(data: Any, schema: ProfileSchema, facts: FormFacts, **kw: Any) -> ProfileEditForm:
    return ProfileEditForm(
        data, schema=schema, stored=facts.settings, defaults=facts.defaults, **kw
    )


def _load_edit(
    request: HttpRequest, client: EngineClient, profile_id: str
) -> tuple[EngineProfile, FormFacts, ProfileSchema]:
    results = _reads(
        {
            "profile": lambda: client.get_engine_profile(profile_id),
            "settings": client.get_deployment_settings,
            "schema": lambda: get_profile_schema_cached(client),
        }
    )
    profile = results["profile"]
    if isinstance(profile, EngineClientError):
        raise profile
    facts = _edit_facts(profile, results, request)
    return profile, facts, _schema(results["schema"])


def _deleted(request: HttpRequest, profile_id: str, text: str) -> HttpResponse:
    messages.error(request, text)
    return redirect(detail_url(profile_id))


@require_http_methods(["GET", "HEAD", "POST"])
def profile_edit(request: HttpRequest, profile_id: str) -> HttpResponse:
    client = engine_client_for(request)
    if request.method != "POST":
        profile, facts, schema = _load_edit(request, client, profile_id)
        if profile.deleted_at is not None:
            return _deleted(request, profile_id, DELETED_NO_EDIT)
        form = _edit_form(None, schema, facts, initial={"name": profile.name})
        return _render(request, facts, form)
    posted = FormFacts.unsigned(request.POST.get(SNAPSHOT_FIELD, ""), profile_id)
    if posted is None:
        _, facts, schema = _load_edit(request, client, profile_id)
        form = _edit_form(request.POST, schema, facts)
        form.is_valid()
        form.add_error(None, EXPIRED)
        return _render(request, facts, form)
    facts = posted
    schema = get_profile_schema_cached(client)
    form = _edit_form(request.POST, schema, facts)
    if schema_digest(schema) != facts.digest:
        form.is_valid()
        form.add_error(None, SCHEMA_CHANGED)
        return _render(request, replace(facts, digest=schema_digest(schema)), form)
    if not form.is_valid():
        return _render(request, facts, form)
    shown = Shown()
    if request.POST.get(ACTION_FIELD) == VALIDATE:
        _validate(request, client, profile_id, form, shown)
        return _render(request, facts, form, shown)
    name, settings = form.cleaned_data["name"], form.settings()
    if name == facts.name and _same(settings, facts.settings):
        messages.info(request, NO_CHANGE)
        return redirect(detail_url(profile_id))
    stale = _version_check(request, client, facts, form, shown)
    if stale is not None:
        return stale
    result = _validate(request, client, profile_id, form, shown)
    if result is None or not result.valid:
        return _render(request, facts, form, shown)
    return _put(
        request,
        client,
        facts,
        form,
        shown,
        ignore_warnings=False,
        key=posted_idempotency_key(request),
        validation=result,
    )


def _version_check(
    request: HttpRequest,
    client: EngineClient,
    facts: FormFacts,
    form: ProfileEditForm,
    shown: Shown,
) -> HttpResponse | None:
    """Re-read the profile (3 s): a failed read is the panel, a deleted profile a redirect, a
    moved version the banner; ``None`` when Save may go on."""
    try:
        current = client.get_engine_profile(facts.profile_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        shown.engine_error = panel_for(exc)
        return _render(request, facts, form, shown)
    if current.deleted_at is not None:
        return _deleted(request, facts.profile_id, DELETED_ON_SAVE)
    if current.current_version != facts.version:
        shown.moved = MOVED.format(version=current.current_version)
        return _render(request, facts, form, shown)
    return None


def _posted_data(request: HttpRequest) -> dict[str, list[str]]:
    """The edit form's posted fields (not the tokens), to rebuild it after "Apply anyway"."""
    skip = {"csrfmiddlewaretoken", "idempotency_key", ACTION_FIELD}
    return {k: request.POST.getlist(k) for k in request.POST if k not in skip}


def _put(
    request: HttpRequest,
    client: EngineClient,
    facts: FormFacts,
    form: ProfileEditForm,
    shown: Shown,
    *,
    ignore_warnings: bool,
    key: str,
    validation: ProfileValidation | None,
    data: Mapping[str, list[str]] | None = None,
) -> HttpResponse:
    name, settings = form.cleaned_data["name"], form.settings()
    try:
        edit = client.update_engine_profile(
            facts.profile_id,
            name=name,
            settings=settings,
            ignore_warnings=ignore_warnings,
            idempotency_key=key,
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 422 and exc.code == PROFILE_DELETED:
            return _deleted(request, facts.profile_id, DELETED_ON_SAVE)
        _write_refused(exc, form, shown)
        return _render(request, facts, form, _with_labels(client, shown))
    if edit.applied:
        messages.success(request, _applied_text(edit, facts, name) + _warning_suffix(validation))
        return redirect(detail_url(facts.profile_id))
    token = twostage.sign(
        APPLY_KIND,
        facts.profile_id,
        {"name": name, "settings": settings},
        {
            "facts": facts.signed(),
            "data": dict(data) if data is not None else _posted_data(request),
            "warnings": [warning_text(w) for w in validation.warnings] if validation else [],
        },
    )
    return _impact_page(request, client, facts, edit.impact, token, _validation_texts(validation))


def _validation_texts(validation: ProfileValidation | None) -> list[str]:
    return [warning_text(w) for w in validation.warnings] if validation else []


def _applied_text(edit: ProfileEdit, facts: FormFacts, name: str) -> str:
    """ "Saved — version N" (and how many stories follow it), "Renamed — no new version", or
    "No change — the Engine found nothing to save"."""
    result = edit.result
    version = result.current_version if result is not None else None
    if version is not None and version != facts.version:
        text = f"Saved — version {version}"
        count = edit.impact.affected_count
        if count == 1:
            text += "; 1 story follows it from its next turn"
        elif count > 1:
            text += f"; {count} stories follow it from their next turn"
        return text
    if name != facts.name:
        return "Renamed — no new version"
    return "No change — the Engine found nothing to save"


# --- impact ---------------------------------------------------------------------------------------


def _impact_page(
    request: HttpRequest,
    client: EngineClient,
    facts: FormFacts,
    impact: ImpactReport,
    token: str,
    warnings: list[str],
) -> HttpResponse:
    ids = [s.story_id for s in impact.affected_stories]
    context = {
        "facts": facts,
        "impact": impact,
        "story_labels": story_labels(client, ids) if ids else {},
        "mode": "profile",
        # #57: lost guarantees link the profile page's table at the version loaded.
        "guarantees_href": detail_url(facts.profile_id, facts.version),
        "apply_url": reverse("profiles:apply", args=[facts.profile_id]),
        "signed_payload": token,
        "cancel_url": edit_url(facts.profile_id),
        "show_apply": True,
        "idempotency_key": new_ulid(),  # the confirming POST's own key (#64 convention 3)
        "validation_warnings": warnings,
        "upgrade_unavailable_text": UPGRADE_UNAVAILABLE,
    }
    return render(request, "console/profiles/impact.html", context)


@require_POST
def profile_apply(request: HttpRequest, profile_id: str) -> HttpResponse:
    """ "Apply anyway": the signed edit resent with ``ignore_warnings: true`` after the same
    re-read and version check; an expired or tampered payload makes no Engine call."""
    try:
        payload = twostage.unsign(APPLY_KIND, request.POST.get(twostage.CONFIRM_FIELD, ""))
    except twostage.ConfirmationExpired:
        messages.error(request, twostage.EXPIRED_TEXT)
        return redirect(edit_url(profile_id))
    base = payload.base if isinstance(payload.base, dict) else {}
    facts = FormFacts.unsigned(str(base.get("facts", "")), profile_id)
    raw_data = base.get("data")
    if payload.target_id != profile_id or facts is None or not isinstance(raw_data, dict):
        messages.error(request, twostage.EXPIRED_TEXT)
        return redirect(edit_url(profile_id))
    data = QueryDict(mutable=True)
    for key, values in raw_data.items():
        data.setlist(str(key), [str(v) for v in values] if isinstance(values, list) else [])
    client = engine_client_for(request)
    schema = get_profile_schema_cached(client)
    form = _edit_form(data, schema, facts)
    shown = Shown()
    if schema_digest(schema) != facts.digest:
        form.is_valid()
        form.add_error(None, SCHEMA_CHANGED)
        return _render(request, replace(facts, digest=schema_digest(schema)), form, shown)
    if not form.is_valid():
        return _render(request, facts, form, shown)
    stale = _version_check(request, client, facts, form, shown)
    if stale is not None:
        return stale
    warnings = base.get("warnings")
    shown_warnings = [str(w) for w in warnings] if isinstance(warnings, list) else []
    response = _put(
        request,
        client,
        facts,
        form,
        shown,
        ignore_warnings=True,
        key=posted_idempotency_key(request),
        validation=None,
        data={str(k): list(v) for k, v in raw_data.items() if isinstance(v, list)},
    )
    if shown_warnings and response.status_code == 302:
        messages.info(request, "Warnings: " + "; ".join(shown_warnings))
    return response


# --- copy -----------------------------------------------------------------------------------------


def _version_param(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        number = int(raw.strip())
    except ValueError:
        return None
    return number if number >= 1 else None


@require_http_methods(["GET", "HEAD", "POST"])
def profile_copy(request: HttpRequest, profile_id: str) -> HttpResponse:
    """GET: the copy form after one profile read at the viewed version (for the default name).
    POST: ``POST …/copy?version=N`` (the viewed version, always sent), then the copy's page."""
    client = engine_client_for(request)
    version = _version_param(request.GET.get("version"))
    if request.method != "POST":
        profile = client.get_engine_profile(profile_id, version=version)
        form = ProfileCopyForm(
            initial={"name": copy_name(profile.name), "owner": acting_principal(request)}
        )
        return _render_copy(request, profile_id, profile.name, profile.version, form)
    source = request.POST.get("source_name", "") or profile_id
    form = ProfileCopyForm(request.POST)
    if version is None or not form.is_valid():
        return _render_copy(request, profile_id, source, version, form)
    remember_principals(form)
    key = posted_idempotency_key(request)
    try:
        copied = client.copy_engine_profile(
            profile_id,
            name=form.cleaned_data["name"],
            owner=form.cleaned_data["owner"],
            version=version,
            idempotency_key=key,
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        panel = None
        if not (
            isinstance(exc, EngineError)
            and exc.http_status == 422
            and apply_engine_errors(exc, form)
        ):
            panel = panel_for(exc)
        return _render_copy(request, profile_id, source, version, form, engine_error=panel)
    messages.success(
        request,
        f"Copied {source} v{version} to {copied.name} ({copied.id}), "
        f"version {copied.current_version}",
    )
    return redirect(detail_url(copied.id))


def _render_copy(
    request: HttpRequest,
    profile_id: str,
    source: str,
    version: int | None,
    form: ProfileCopyForm,
    *,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    action = reverse("profiles:copy", args=[profile_id])
    if version is not None:
        action = f"{action}?{urlencode({'version': version})}"
    context = {
        "profile_id": profile_id,
        "source": source,
        "version": version,
        "form": form,
        "action_url": action,
        "cancel_url": detail_url(profile_id, version),
        "idempotency_key": new_ulid(),
        "engine_error": engine_error,
    }
    return render(request, "console/profiles/copy.html", context)


# --- delete ---------------------------------------------------------------------------------------


def delete_query(name: str, next_url: str) -> str:
    return urlencode({"name": name, "next": next_url})


def _delete_flash(client: EngineClient, name: str, profile_id: str, answer: Any) -> SafeString:
    """The delete's answer: both counts, then the first 25 stories still running under it and
    scenarios recommending it, linked, "…and N more"."""
    shown = f"{name} ({profile_id})" if name else profile_id
    parts = [
        format_html(
            "Deleted {}. Stories using it: {}; scenarios recommending it: {}.",
            shown,
            answer.stories_using,
            answer.scenarios_recommending,
        )
    ]
    stories = [w.story_id for w in answer.warnings if w.kind == "profile_in_use" and w.story_id]
    scenarios = [
        w.scenario_id for w in answer.warnings if w.kind == "profile_recommended" and w.scenario_id
    ]
    if stories:
        labels = story_labels(client, stories[:LIST_CAP])
        links = format_html_join(
            ", ",
            '<a href="{}">{}</a>',
            (
                (reverse("stories:detail", args=[s]), labels[s].text if s in labels else s)
                for s in stories[:LIST_CAP]
            ),
        )
        parts.append(
            format_html(
                " Still running under it: {}{}.", links, _more(answer.stories_using, LIST_CAP)
            )
        )
    if scenarios:
        links = format_html_join(
            ", ",
            '<a href="{}"><code>{}</code></a>',
            ((reverse("scenarios:detail", args=[s]), s) for s in scenarios[:LIST_CAP]),
        )
        parts.append(
            format_html(
                " Recommended by: {}{}.", links, _more(answer.scenarios_recommending, LIST_CAP)
            )
        )
    return SafeString("".join(parts))


def _more(total: int, shown: int) -> str:
    return f", …and {total - shown} more" if total > shown else ""


@require_http_methods(["GET", "HEAD", "POST"])
def profile_delete(request: HttpRequest, profile_id: str) -> HttpResponse:
    """GET: the confirm page (no Engine call). POST: ``DELETE``, then the (now deleted)
    profile's page with the answer flashed."""
    name = shown_name(request)
    page = ConfirmPage(
        title="Delete engine profile",
        question="Delete {name}?" if name else "Delete this profile?",
        emphasis=name,
        lines=(DELETE_LINE,),
        confirm_label="Delete",
        cancel_url=detail_url(profile_id),
        facts=(("Profile id", profile_id),),
        hidden={"name": name},
        danger=True,
    )

    def perform(key: str) -> HttpResponse:
        client = engine_client_for(request)
        try:
            answer = client.delete_engine_profile(profile_id, idempotency_key=key)
        except EngineError as exc:
            if exc.http_status == 409 and exc.details.get("reason") == IS_DEFAULT:
                log_engine_error(request, exc)
                messages.error(
                    request,
                    format_html(
                        '{} (<a href="{}">deployment settings</a>).',
                        IS_DEFAULT_TEXT,
                        reverse("engine:settings"),
                    ),
                )
                return redirect(detail_url(profile_id))
            raise
        messages.success(request, _delete_flash(client, name, profile_id, answer))
        return redirect(detail_url(profile_id))

    return confirm_action(request, page, perform)

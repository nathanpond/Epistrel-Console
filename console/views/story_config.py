"""A story's effective configuration, its profile assignment and its overrides (#85).

- ``/stories/<id>/config/``: ``GET …/effective-config`` (3 s; a failure is #43's panel for the
  page), then on #41's pool the running profile version (``GET /engine-profiles/{id}?version=N``,
  the profile column), every page of ``GET /engine-profiles`` (the assign select, under #64's
  500-item cap), the profile schema (cached) and the story (its name). Each read that fails
  degrades its part only. The select posts to ``…/config/profile/``.
- ``/stories/<id>/config/overrides/``: GET the overrides form (#84's schema form rooted at
  ``overrides``, :class:`~console.forms.story_overrides.OverridesForm`); POST previews: only the
  merge patch is sent, ``POST …/impact-preview {overrides}``; nothing changed is "Nothing to
  preview" with zero calls; a 422 is placed at the fields.
- ``…/config/profile/`` (POST): ``POST …/impact-preview {profile_id}``.
- The preview page **is** the impact step (#64 convention 3): its Apply posts a signed payload
  (kinds ``story_overrides`` / ``story_profile``; ``base`` = overrides revision, profile id and
  version, the fingerprint before and whether the preview warned) to ``…/apply/``, which sends
  ``PATCH …/profile-overrides`` (``expected_revision`` in the body) or ``PUT …/engine-profile``
  with ``ignore_warnings`` = the preview warned. A fresh ``applied: false`` shows the impact
  again with "Apply anyway". Applied: back to Config with the new fingerprint flashed.

The form page's facts (the overrides as loaded and their revision, the profile version's
settings, the deployment defaults, the schema digest) travel signed, so an invalid form, an
unchanged one and a stale schema make no Engine call.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from console import twostage
from console.confirm import posted_idempotency_key
from console.engine.choices import PROFILES_MAX, error_text
from console.engine.client import (
    ENGINE_PROFILES_PAGE,
    EngineClient,
    EngineClientError,
    EngineError,
)
from console.engine.models import (
    EffectiveConfig,
    EngineProfile,
    ImpactReport,
    ProfileSchema,
    ProfileValidation,
    Story,
    StoryProfileWrite,
)
from console.engine.pool import fan_out
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.forms.profile_schema_form import (
    SCHEMA_CHANGED,
    missing_text,
    schema_digest,
    shown_value,
    undelivered_text,
)
from console.forms.story_overrides import OverridesForm
from console.ids import new_ulid
from console.impact import quality_class, short_fingerprint
from console.profile_schema import get_profile_schema_cached
from console.story_config import badge_for, config_layers, config_url
from console.views.profiles import detail_url
from console.views.story_settings import Refusal, refusal_for

OVERRIDES_FORM_KIND = "story_overrides_form"
OVERRIDES_KIND = "story_overrides"
PROFILE_KIND = "story_profile"
SNAPSHOT_FIELD = "snapshot"
PROFILE_FIELD = "profile_id"
INVALID_PROFILE = "invalid_profile"
PROFILE_DELETED = "profile_deleted"
REVISION_CONFLICT = "revision_conflict"
OVERRIDES_REVISION = "overrides_revision"
STORY_ARCHIVED = "story_archived"

NOTHING_TO_PREVIEW = "Nothing to preview"
EXPIRED = "This form expired — your changes were not sent; check and submit again"
OVERRIDES_MOVED = "Overrides changed since you loaded this page — reload"
STORY_CHANGED = "The story changed — reload"
DELETED_PICK = "That profile was deleted — pick another"
PICK_ONE = "Pick a profile"
PROFILES_UNAVAILABLE = "Profiles unavailable — reload"
TOO_MANY = "Too many to list — enter the id"
ARCHIVED_APPLY = "Archived — unarchive to apply"
ARCHIVED_REFUSED = "This story is archived — unarchive it to change its configuration"
NO_CHANGE = "No change — the story already runs this configuration"
KEPT = "Applied, but this story keeps its previous configuration (Lite can't backfill history)"
CANT_RUN = "This story's overrides can't run on {profile}"


# --- reads ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileOption:
    id: str
    label: str
    disabled: bool = False


@dataclass(frozen=True)
class ProfileSelect:
    """The assign select's options, or why there is none: ``error`` (the list read failed) or
    ``too_many`` (a free-text id input instead)."""

    options: tuple[ProfileOption, ...] = ()
    error: str = ""
    too_many: bool = False


def profile_select(client: EngineClient, current_id: str) -> ProfileSelect:
    """Every page of ``GET /engine-profiles`` (deleted included, so a deleted current profile
    keeps its name), up to :data:`PROFILES_MAX`: non-deleted profiles labelled "<name> ·
    v<current_version> · <id[:6]>", the current one disabled "(current)"; a deleted current one
    first, disabled, "<name> · deleted (current)"."""
    found: list[EngineProfile] = []
    cursor: str | None = None
    try:
        while True:
            items, cursor = client.list_engine_profiles(
                include_deleted=True, cursor=cursor, limit=ENGINE_PROFILES_PAGE
            )
            found.extend(items)
            if len(found) > PROFILES_MAX:
                return ProfileSelect(too_many=True)
            if not cursor:
                break
    except EngineClientError:
        return ProfileSelect(error=PROFILES_UNAVAILABLE)
    options: list[ProfileOption] = []
    current = next((p for p in found if p.id == current_id), None)
    if current is not None and current.deleted_at is not None:
        options.append(ProfileOption(current.id, f"{current.name} · deleted (current)", True))
    for profile in found:
        if profile.deleted_at is not None:
            continue
        label = f"{profile.name} · v{profile.current_version} · {profile.id[:6]}"
        is_current = profile.id == current_id
        options.append(
            ProfileOption(profile.id, f"{label} (current)" if is_current else label, is_current)
        )
    return ProfileSelect(tuple(options))


def _reads(reads: Mapping[str, Callable[[], Any]]) -> dict[str, Any]:
    return fan_out(lambda name: reads[name](), list(reads))


@dataclass(frozen=True)
class Loaded:
    """Config's reads: the effective configuration and, each ``None`` when it failed, the
    running profile version, the schema and the story."""

    config: EffectiveConfig
    profile: EngineProfile | None
    profile_error: str
    schema: ProfileSchema | None
    story: Story | None


def _load(
    request: HttpRequest, client: EngineClient, story_id: str, *, select: bool = False
) -> tuple[Loaded, ProfileSelect | None]:
    config = client.get_effective_config(story_id)  # a failure is the page's panel
    reads: dict[str, Callable[[], Any]] = {
        "profile": lambda: client.get_engine_profile(
            config.profile.id, version=config.profile.version
        ),
        "schema": lambda: get_profile_schema_cached(client),
        "story": lambda: client.get_story(story_id),
    }
    if select:
        reads["profiles"] = lambda: profile_select(client, config.profile.id)
    results = _reads(reads)
    for value in results.values():
        if isinstance(value, EngineClientError):
            log_engine_error(request, value)
    profile = results["profile"]
    profile_error = ""
    if isinstance(profile, EngineClientError):
        profile_error = (
            f"Couldn't read profile {config.profile.id} v{config.profile.version} "
            f"({error_text(profile)}): its column shows “unavailable”"
        )
        profile = None
    schema = results["schema"]
    story = results["story"]
    loaded = Loaded(
        config,
        profile if isinstance(profile, EngineProfile) else None,
        profile_error,
        schema if isinstance(schema, ProfileSchema) else None,
        story if isinstance(story, Story) else None,
    )
    if not select:
        return loaded, None
    chosen = results["profiles"]
    if not isinstance(chosen, ProfileSelect):
        chosen = ProfileSelect(error=PROFILES_UNAVAILABLE)
    return loaded, chosen


def _story_name(story: Story | None, story_id: str) -> str:
    return story.name if story is not None and story.name else story_id


# --- Config ---------------------------------------------------------------------------------------


@dataclass
class AssignState:
    """The assign section after a refused preview or apply: the chosen profile pre-selected and
    what to show above the select."""

    chosen: str = ""
    error: str = ""
    lines: list[str] = field(default_factory=list)
    refusal: Refusal | None = None


def _config_page(
    request: HttpRequest,
    client: EngineClient,
    story_id: str,
    assign: AssignState | None = None,
) -> HttpResponse:
    loaded, select = _load(request, client, story_id, select=True)
    config = loaded.config
    profile_settings = loaded.profile.settings if loaded.profile is not None else None
    context = {
        "story_id": story_id,
        "story_name": _story_name(loaded.story, story_id),
        "config": config,
        "badge": badge_for(config),
        "quality_class": quality_class(config.quality_status.status),
        "layers": config_layers(config, profile_settings, loaded.schema),
        "profile": loaded.profile,
        "profile_url": detail_url(config.profile.id, config.profile.version),
        "profile_error": loaded.profile_error,
        "select": select,
        "too_many_text": TOO_MANY,
        "assign": assign or AssignState(),
        "assign_url": reverse("stories:config-profile", args=[story_id]),
        "overrides_url": reverse("stories:config-overrides", args=[story_id]),
        "settings_url": reverse("stories:settings", args=[story_id]),
        "story_url": reverse("stories:detail", args=[story_id]),
        "history_url": reverse("stories:history", args=[story_id]),
    }
    return render(request, "console/stories/config.html", context)


@require_safe
def config_page(request: HttpRequest, story_id: str) -> HttpResponse:
    return _config_page(request, engine_client_for(request), story_id)


# --- the preview page -----------------------------------------------------------------------------


def upgrade_unavailable(impact: ImpactReport) -> bool:
    return any(story.upgrade_unavailable for story in impact.affected_stories)


@dataclass(frozen=True)
class Preview:
    """What the preview page needs besides the impact: the write to sign and how to label it."""

    kind: str
    story_id: str
    body: dict[str, Any]
    base: dict[str, Any]
    heading: str
    lines: tuple[str, ...] = ()
    patch_text: str = ""
    archived: bool = False
    again: bool = False  # a fresh ``applied: false`` on Apply: "Apply anyway"


def _preview_page(request: HttpRequest, preview: Preview, impact: ImpactReport) -> HttpResponse:
    warned = bool(impact.warnings) or preview.again
    base = {
        **preview.base,
        "warned": warned,
        "fingerprint_before": impact.fingerprint_before,
        "upgrade_unavailable": upgrade_unavailable(impact),
    }
    token = twostage.sign(preview.kind, preview.story_id, preview.body, base)
    apply_name = (
        "config-overrides-apply" if preview.kind == OVERRIDES_KIND else "config-profile-apply"
    )
    back = (
        reverse("stories:config-overrides", args=[preview.story_id])
        if preview.kind == OVERRIDES_KIND
        else config_url(preview.story_id)
    )
    context = {
        "story_id": preview.story_id,
        "preview": preview,
        "impact": impact,
        "mode": "story",
        "token": token,
        "apply_url": reverse(f"stories:{apply_name}", args=[preview.story_id]),
        "apply_label": "Apply anyway" if warned else "Apply",
        "cancel_url": back,
        "config_url": config_url(preview.story_id),
        "idempotency_key": new_ulid(),  # the confirming POST's own key (#64 convention 3)
        "archived_note": ARCHIVED_APPLY if preview.archived else "",
    }
    return render(request, "console/stories/config_preview.html", context)


def _archived(client: EngineClient, story_id: str) -> bool:
    """Whether the story is archived (#48's story read); unknown counts as not archived (the
    Engine refuses an archived story's write anyway)."""
    try:
        return client.get_story(story_id).status == "archived"
    except EngineClientError:
        return False


# --- overrides ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OverridesFacts:
    """What the overrides form was rendered from."""

    story_id: str
    revision: int
    overrides: dict[str, Any]
    profile: dict[str, Any] | None
    defaults: dict[str, Any]
    digest: str
    profile_id: str = ""
    profile_version: int = 0
    fingerprint: str = ""

    def signed(self) -> str:
        return twostage.sign(OVERRIDES_FORM_KIND, self.story_id, None, asdict(self))

    @classmethod
    def unsigned(cls, token: str, story_id: str) -> OverridesFacts | None:
        try:
            payload = twostage.unsign(OVERRIDES_FORM_KIND, token)
        except twostage.ConfirmationExpired:
            return None
        raw = payload.base
        if payload.target_id != story_id or not isinstance(raw, dict):
            return None
        try:
            facts = cls(**raw)
        except TypeError:
            return None
        return facts if isinstance(facts.overrides, dict) else None


@dataclass
class FormShown:
    engine_error: ErrorPanel | None = None
    refusal: Refusal | None = None
    lines: list[str] = field(default_factory=list)
    notice: str = ""
    profile_error: str = ""


def _overrides_form(data: Any, schema: ProfileSchema, facts: OverridesFacts) -> OverridesForm:
    return OverridesForm(
        data,
        schema=schema,
        overrides=facts.overrides,
        profile=facts.profile,
        defaults=facts.defaults,
    )


def _render_form(
    request: HttpRequest,
    facts: OverridesFacts,
    form: OverridesForm,
    shown: FormShown | None = None,
) -> HttpResponse:
    context = {
        "story_id": facts.story_id,
        "facts": facts,
        "form": form,
        "nodes": form.shown(),
        "snapshot": facts.signed(),
        "shown": shown or FormShown(),
        "action_url": reverse("stories:config-overrides", args=[facts.story_id]),
        "config_url": config_url(facts.story_id),
        "revision_text": revision_text(facts.revision),
    }
    return render(request, "console/stories/config_overrides.html", context)


def revision_text(revision: int) -> str:
    return "0 (no overrides)" if revision == 0 else str(revision)


def _fresh_facts(
    request: HttpRequest, client: EngineClient, story_id: str
) -> tuple[OverridesFacts, ProfileSchema, str]:
    loaded, _ = _load(request, client, story_id)
    if loaded.schema is None:
        get_profile_schema_cached(client)  # the form can't be built: let its error surface
    schema = loaded.schema
    assert schema is not None  # noqa: S101 - the read above raised otherwise
    config = loaded.config
    facts = OverridesFacts(
        story_id=story_id,
        revision=config.overrides.revision,
        overrides=dict(config.overrides.settings),
        profile=dict(loaded.profile.settings) if loaded.profile is not None else None,
        defaults=dict(config.deployment_defaults),
        digest=schema_digest(schema),
        profile_id=config.profile.id,
        profile_version=config.profile.version,
        fingerprint=config.fingerprint,
    )
    return facts, schema, loaded.profile_error


@require_http_methods(["GET", "HEAD", "POST"])
def overrides_page(request: HttpRequest, story_id: str) -> HttpResponse:
    """GET: the form. POST: the merge patch previewed (``impact-preview {overrides}``)."""
    client = engine_client_for(request)
    if request.method != "POST":
        facts, schema, profile_error = _fresh_facts(request, client, story_id)
        form = _overrides_form(None, schema, facts)
        return _render_form(request, facts, form, FormShown(profile_error=profile_error))
    posted = OverridesFacts.unsigned(request.POST.get(SNAPSHOT_FIELD, ""), story_id)
    if posted is None:
        facts, schema, _ = _fresh_facts(request, client, story_id)
        form = _overrides_form(request.POST, schema, facts)
        form.is_valid()
        form.add_error(None, EXPIRED)
        return _render_form(request, facts, form)
    facts = posted
    schema = get_profile_schema_cached(client)
    form = _overrides_form(request.POST, schema, facts)
    if schema_digest(schema) != facts.digest:
        form.is_valid()
        form.add_error(None, SCHEMA_CHANGED)
        return _render_form(request, _with_digest(facts, schema), form)
    if not form.is_valid():
        return _render_form(request, facts, form)
    patch = form.patch()
    if not patch:
        return _render_form(request, facts, form, FormShown(notice=NOTHING_TO_PREVIEW))
    try:  # recorded (#44), so in the request's own thread: the pool only reads
        impact = client.impact_preview(story_id, overrides=patch)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _render_form(request, facts, form, _refused(exc, form, ""))
    preview = Preview(
        OVERRIDES_KIND,
        story_id,
        patch,
        {
            "revision": facts.revision,
            "profile_id": facts.profile_id,
            "profile_version": facts.profile_version,
            "facts": facts.signed(),
            "data": _posted_data(request.POST),
        },
        "Preview: change this story's overrides",
        patch_text=json.dumps(patch, indent=2, ensure_ascii=False),
        archived=_archived(client, story_id),
    )
    return _preview_page(request, preview, impact)


def _with_digest(facts: OverridesFacts, schema: ProfileSchema) -> OverridesFacts:
    return OverridesFacts(**{**asdict(facts), "digest": schema_digest(schema)})


def _posted_data(data: QueryDict) -> dict[str, list[str]]:
    skip = {"csrfmiddlewaretoken", "idempotency_key"}
    return {k: data.getlist(k) for k in data if k not in skip}


def overrides_lines(value: Any) -> list[str]:
    """``details.overrides`` as lines: an object one line per flattened leaf ("`path`: value"),
    a list one line per item (an object's ``setting``/``path`` and its message, else raw JSON)."""
    lines: list[str] = []
    if isinstance(value, dict):

        def walk(item: Any, path: str) -> None:
            if isinstance(item, dict) and item:
                for key, child in item.items():
                    walk(child, f"{path}.{key}" if path else str(key))
            else:
                lines.append(f"{path}: {shown_value(item)}")

        walk(value, "")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                where = item.get("setting") or item.get("path")
                message = item.get("message") or item.get("text")
                if isinstance(where, str):
                    lines.append(f"{where}: {message}" if message else where)
                    continue
            lines.append(shown_value(item))
    return lines


def _refused(exc: EngineClientError, form: OverridesForm, key: str) -> FormShown:
    """A refused preview or apply on the overrides form: 422s placed at the fields (else the
    panel), the revision conflicts and archived story in the AC's words, busy in #48's."""
    shown = FormShown()
    if isinstance(exc, EngineError):
        if exc.http_status == 422 and exc.code == INVALID_PROFILE:
            placed = form.place_invalid_profile(exc)
            shown.lines = overrides_lines(exc.details.get("overrides"))
            if placed.stories or placed.extra:
                shown.engine_error = panel_for(exc)
            return shown
        if exc.http_status == 422 and form.place_validation_error(exc):
            return shown
        if exc.code == REVISION_CONFLICT:
            reason = exc.details.get("reason")
            form.add_error(None, OVERRIDES_MOVED if reason == OVERRIDES_REVISION else STORY_CHANGED)
            return shown
        if exc.code == STORY_ARCHIVED:
            shown.refusal = Refusal(notice=ARCHIVED_REFUSED, line=_line(exc))
            return shown
        if exc.code in ("story_busy", "story_paused"):
            shown.refusal = refusal_for(exc, key)
            return shown
    shown.engine_error = panel_for(exc)
    return shown


def _line(exc: EngineError) -> str:
    return f"HTTP {exc.http_status} {exc.code}: {exc.message}"


def _flash_applied(request: HttpRequest, write: StoryProfileWrite, base: Mapping[str, Any]) -> None:
    """After an applied write: kept (``upgrade_unavailable`` and the fingerprint unchanged),
    no change, or the new fingerprint."""
    result = write.result
    if result is None:
        messages.info(request, NO_CHANGE)
        return
    kept = upgrade_unavailable(write.impact) and result.fingerprint == (
        write.impact.fingerprint_before
    )
    if kept:
        messages.warning(request, KEPT)
        return
    unchanged = (
        result.overrides.revision == base.get("revision")
        and result.profile.id == base.get("profile_id")
        and result.profile.version == base.get("profile_version")
    )
    if unchanged:
        messages.info(request, NO_CHANGE)
        return
    messages.success(
        request,
        format_html(
            'Applied — fingerprint <code title="{}">{}</code>; takes effect from the next turn',
            result.fingerprint,
            short_fingerprint(result.fingerprint),
        ),
    )


def _unsigned_write(request: HttpRequest, kind: str, story_id: str) -> twostage.Payload | None:
    try:
        payload = twostage.unsign(kind, request.POST.get(twostage.CONFIRM_FIELD, ""))
    except twostage.ConfirmationExpired:
        return None
    if payload.target_id != story_id or not isinstance(payload.base, dict):
        return None
    if not isinstance(payload.body, dict):
        return None
    return payload


@require_POST
def overrides_apply(request: HttpRequest, story_id: str) -> HttpResponse:
    """The preview's Apply: ``PATCH …/profile-overrides`` with the signed patch,
    ``expected_revision`` the revision shown and ``ignore_warnings`` = the preview warned."""
    payload = _unsigned_write(request, OVERRIDES_KIND, story_id)
    if payload is None:
        messages.error(request, twostage.EXPIRED_TEXT)
        return redirect(reverse("stories:config-overrides", args=[story_id]))
    base: dict[str, Any] = payload.base
    revision = base.get("revision")
    client = engine_client_for(request)
    key = posted_idempotency_key(request)
    try:
        write = client.patch_profile_overrides(
            story_id,
            payload.body,
            expected_revision=revision if isinstance(revision, int) else None,
            ignore_warnings=base.get("warned") is True,
            idempotency_key=key,
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _apply_refused(request, client, story_id, base, exc, key)
    if not write.applied:
        preview = Preview(
            OVERRIDES_KIND,
            story_id,
            payload.body,
            {k: v for k, v in base.items() if k not in ("warned", "fingerprint_before")},
            "Preview: change this story's overrides",
            patch_text=json.dumps(payload.body, indent=2, ensure_ascii=False),
            archived=_archived(client, story_id),
            again=True,
        )
        return _preview_page(request, preview, write.impact)
    _flash_applied(request, write, base)
    return redirect(config_url(story_id))


def _apply_refused(
    request: HttpRequest,
    client: EngineClient,
    story_id: str,
    base: Mapping[str, Any],
    exc: EngineClientError,
    key: str,
) -> HttpResponse:
    """A refused overrides apply: the form re-rendered from the signed payload with the
    refusal in place (a stale or tampered form token: a fresh form)."""
    facts = OverridesFacts.unsigned(str(base.get("facts", "")), story_id)
    raw = base.get("data")
    if facts is None or not isinstance(raw, dict):
        messages.error(request, twostage.EXPIRED_TEXT)
        return redirect(reverse("stories:config-overrides", args=[story_id]))
    data = QueryDict(mutable=True)
    for name, values in raw.items():
        data.setlist(str(name), [str(v) for v in values] if isinstance(values, list) else [])
    schema = get_profile_schema_cached(client)
    form = _overrides_form(data, schema, facts)
    form.is_valid()
    return _render_form(request, facts, form, _refused(exc, form, key))


# --- assign a profile -----------------------------------------------------------------------------


@require_POST
def profile_preview(request: HttpRequest, story_id: str) -> HttpResponse:
    """The assign select's preview: ``impact-preview {profile_id}`` beside a read of the profile
    (its name and latest version for the page)."""
    client = engine_client_for(request)
    chosen = request.POST.get(PROFILE_FIELD, "").strip()
    if not chosen:
        return _config_page(request, client, story_id, AssignState(error=PICK_ONE))
    try:  # recorded (#44), so in the request's own thread: the pool only reads
        impact = client.impact_preview(story_id, profile_id=chosen)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        found = _reads({"profile": lambda: client.get_engine_profile(chosen)})["profile"]
        name = found.name if isinstance(found, EngineProfile) else chosen
        return _config_page(request, client, story_id, _assign_refused(exc, chosen, "", name))
    results = _reads(
        {
            "profile": lambda: client.get_engine_profile(chosen),
            "config": lambda: client.get_effective_config(story_id),
            "archived": lambda: _archived(client, story_id),
        }
    )
    profile = results["profile"]
    name = profile.name if isinstance(profile, EngineProfile) else chosen
    config = results["config"]
    version = f" (v{profile.current_version})" if isinstance(profile, EngineProfile) else ""
    base: dict[str, Any] = {"name": name}
    if isinstance(config, EffectiveConfig):
        base.update(
            revision=config.overrides.revision,
            profile_id=config.profile.id,
            profile_version=config.profile.version,
        )
    preview = Preview(
        PROFILE_KIND,
        story_id,
        {"profile_id": chosen},
        base,
        f"Preview: assign {name}",
        lines=(f"Runs {name} at its latest version{version} from the next turn",),
        archived=results["archived"] is True,
    )
    return _preview_page(request, preview, impact)


def _assign_refused(exc: EngineClientError, chosen: str, key: str, name: str = "") -> AssignState:
    state = AssignState(chosen=chosen)
    if isinstance(exc, EngineError):
        if exc.http_status == 422 and exc.code == PROFILE_DELETED:
            state.error = DELETED_PICK
            return state
        if exc.http_status == 422 and exc.code == INVALID_PROFILE and "overrides" in exc.details:
            state.error = f"{CANT_RUN.format(profile=name or chosen)}: {exc.message}"
            state.lines = overrides_lines(exc.details.get("overrides"))
            return state
        if exc.code == REVISION_CONFLICT:
            state.error = STORY_CHANGED
            return state
        if exc.code == STORY_ARCHIVED:
            state.refusal = Refusal(notice=ARCHIVED_REFUSED, line=_line(exc))
            return state
        if exc.code in ("story_busy", "story_paused"):
            state.refusal = refusal_for(exc, key)
            return state
        if exc.http_status == 422 and exc.code == INVALID_PROFILE:
            result = ProfileValidation.from_json(
                {
                    "valid": False,
                    "missing_dependencies": exc.details.get("missing_dependencies") or [],
                    "undelivered": exc.details.get("undelivered") or [],
                }
            )
            state.error = exc.message
            if result is not None:
                state.lines = [missing_text(m) for m in result.missing_dependencies] + [
                    f"{u.setting}: {undelivered_text(u.module, u.level)}"
                    for u in result.undelivered
                ]
            return state
    state.refusal = Refusal(panel=panel_for(exc))
    return state


@require_POST
def profile_apply(request: HttpRequest, story_id: str) -> HttpResponse:
    """The preview's Apply: ``PUT …/engine-profile`` (no expected revision) with
    ``ignore_warnings`` = the preview warned."""
    payload = _unsigned_write(request, PROFILE_KIND, story_id)
    if payload is None:
        messages.error(request, twostage.EXPIRED_TEXT)
        return redirect(config_url(story_id))
    base: dict[str, Any] = payload.base
    chosen = str(payload.body.get("profile_id", ""))
    client = engine_client_for(request)
    key = posted_idempotency_key(request)
    try:
        write = client.assign_engine_profile(
            story_id, chosen, ignore_warnings=base.get("warned") is True, idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        name = str(base.get("name") or chosen)
        return _config_page(request, client, story_id, _assign_refused(exc, chosen, key, name))
    if not write.applied:
        preview = Preview(
            PROFILE_KIND,
            story_id,
            payload.body,
            {k: v for k, v in base.items() if k not in ("warned", "fingerprint_before")},
            f"Preview: assign {base.get('name') or chosen}",
            archived=_archived(client, story_id),
            again=True,
        )
        return _preview_page(request, preview, write.impact)
    _flash_applied(request, write, base)
    return redirect(config_url(story_id))

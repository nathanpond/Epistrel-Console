"""A story's effective configuration, its profile assignment and its overrides (#85).

- ``/stories/<id>/config/``: ``GET …/effective-config`` (3 s; a failure is #43's panel for the
  page), then on #41's pool the running profile version (``GET /engine-profiles/{id}?version=N``,
  the profile column), every page of ``GET /engine-profiles`` (the assign select, under #64's
  500-item cap), the profile schema (cached) and the story (its name). Each read that fails
  degrades its part only. The select posts to ``…/config/profile/``. #57 reads the story's
  guarantee table in the same wave (its failure stays in the Guarantees section).
- ``/stories/<id>/config/overrides/``: GET the overrides form (#84's schema form rooted at
  ``overrides``, :class:`~console.forms.story_overrides.OverridesForm`); POST previews: only the
  merge patch is sent, ``POST …/impact-preview {overrides}``; nothing changed is "Nothing to
  preview" with zero calls; a 422 is placed at the fields.
- ``…/config/profile/`` (POST): ``POST …/impact-preview {profile_id}``.
- ``…/config/version/`` (POST, #63): the Profile version select. A version change has no preview
  (``impact-preview`` can't preview a version): it sends ``PUT …/engine-profile {profile_id,
  version, ignore_warnings: false}`` at once, and a held ``applied: false`` shows the preview page
  with "Apply anyway" (kind ``story_profile``, the body carrying ``version``).
- The preview page **is** the impact step (#64 convention 3): its Apply posts a signed payload
  (kinds ``story_overrides`` / ``story_profile``; ``base`` = overrides revision, profile id and
  version, the fingerprint before and whether the preview warned) to ``…/apply/``, which sends
  ``PATCH …/profile-overrides`` (``expected_revision`` in the body) or ``PUT …/engine-profile``
  with ``ignore_warnings`` = the preview warned. A fresh ``applied: false`` shows the impact
  again with "Apply anyway". Applied: back to Config with the new fingerprint flashed.

#307 (E13, E14):

- Config also reads ``GET /engine-profiles?preset=true`` (one page) beside the full list;
  :func:`with_presets` puts the presets first (``preset_order``): the preset table (version,
  ``_quality.html`` badge, a "Guarantees" link to the profile page's ``#guarantees``, a one-click
  Preview button posting ``profile_id``) and the "Presets" optgroup, then "Other profiles". The
  select and the id input post ``profile_choice``; a failed preset read is
  :data:`PRESETS_UNAVAILABLE` with the full list's presets grouped first instead.
- A preview whose report needs a backfill (:func:`needs_upgrade_mode`) signs ``upgrade_choice``
  and the report itself into ``base`` and asks "Earlier turns" (``UpgradeModeForm``, joined to the
  confirm form by ``form="config-confirm"``); the confirm sends the chosen ``upgrade_mode``, and a
  missing or invalid one re-renders the signed preview with no Engine call.
- A write that started a backfill (``write.backfill``, the 202) flashes :data:`BACKFILL_STARTED`
  and goes to the Backfill page (Config while it is unregistered); an applied write whose
  impact names a ``forward_only_gap`` appends :func:`gap_sentence` to the applied flash.

The form page's facts (the overrides as loaded and their revision, the profile version's
settings, the deployment defaults, the schema digest) travel signed, so an invalid form, an
unchanged one and a stale schema make no Engine call.
"""

import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from datetime import datetime
from typing import Any, Final, Literal, cast

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from console import twostage
from console.canon import utc_text
from console.certification import QualityLine, quality_line
from console.confirm import posted_idempotency_key
from console.engine.choices import PROFILES_MAX, error_text
from console.engine.client import (
    ENGINE_PROFILES_PAGE,
    EngineClient,
    EngineClientError,
    EngineError,
)
from console.engine.models import (
    AffectedStory,
    EffectiveConfig,
    EngineProfile,
    ForwardOnlyGap,
    GuaranteeTable,
    ImpactReport,
    ProfileRef,
    ProfileSchema,
    ProfileValidation,
    ProfileVersion,
    Story,
    StoryProfileWrite,
    TurnSpan,
    json_name,
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
from console.forms.upgrades import DEFAULT_UPGRADE_MODE, UpgradeModeForm
from console.guarantees import CONFIG_SCOPE
from console.guarantees import section as guarantees_section
from console.ids import new_ulid
from console.impact import backfill_text, quality_class, short_fingerprint, turns_text
from console.profile_schema import get_profile_schema_cached
from console.story_config import badge_for, config_layers, config_url
from console.upgrades import backfill_url, gap_fill_url, preset_label, preset_order
from console.views.profiles import detail_url
from console.views.story_settings import Refusal, refusal_for
from console.wording import write_refusal

OVERRIDES_FORM_KIND = "story_overrides_form"
OVERRIDES_KIND = "story_overrides"
PROFILE_KIND = "story_profile"
SNAPSHOT_FIELD = "snapshot"
PROFILE_FIELD = "profile_id"
CHOICE_FIELD = (
    "profile_choice"  # #307: the select's / id input's name (the buttons post profile_id)
)
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
PRESETS_UNAVAILABLE = "Presets unavailable — reload"  # #307
NO_PRESETS = "No presets"
TOO_MANY = "Too many to list — enter the id"
ARCHIVED_APPLY = "Archived — unarchive to apply"
ARCHIVED_REFUSED = "This story is archived — unarchive it to change its configuration"
NO_CHANGE = "No change — the story already runs this configuration"
KEPT = (
    "Applied, but this story keeps its previous configuration: the Engine can't backfill its "
    "history for the modules this version adds"
)
CANT_RUN = "This story's overrides can't run on {profile}"

# #307: the upgrade mode's outcomes.
UpgradeMode = Literal["backfill", "forward_only"]
BACKFILL_STARTED = (
    "Backfill started — the story is paused{rederives}; the new configuration applies when it "
    "completes"
)
FOLLOW_BACKFILL = "Follow the backfill"
FORWARD_ONLY_GAP = (
    "Turns through {through} keep a coverage gap for: {capabilities} — fill it from the {page}"
)
BACKFILL_PAGE = "Backfill page"

# #63: the Profile version section.
VERSION_KIND = "story_version"
VERSION_FIELD = "version"
LATEST: Final = "latest"
VERSION_MAX = 2**31 - 1
VERSION_VERB = "change its profile version"
VERSIONS_UNAVAILABLE = "Versions unavailable — reload"
VERSION_DELETED = "This profile is deleted — assign another profile to change versions"
PICK_VERSION = "Pick a version"
SAME_VERSION = "Already on that version"
NO_SUCH_VERSION = "That profile has no version {version}"
VERSION_PLACEHOLDER = "— pick a version —"
LATEST_LABEL = "Latest — follows the profile's edits"
VERSION_FIELD_PATH = "body.version"


# --- reads ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileOption:
    id: str
    label: str
    disabled: bool = False


@dataclass(frozen=True)
class PresetRow:
    """One preset in the Config page's preset table (#307, E13): its id, ``preset_label``, its
    latest version, its quality badge (``None``: "—"), its guarantee table's URL and whether
    the story runs it (no Preview button then)."""

    id: str
    label: str
    version: int
    quality: QualityLine | None
    guarantees_url: str
    current: bool


@dataclass(frozen=True)
class ProfileSelect:
    """The assign select's options, or why there is none: ``error`` (the list read failed) or
    ``too_many`` (a free-text id input instead). ``found`` is every profile the list read gave
    (the fallback grouping's input). #307: ``presets`` (the preset table, from its own read) and
    ``preset_options`` (the "Presets" optgroup) with ``presets_error`` when that read failed;
    ``options`` is then the "Other profiles" optgroup."""

    options: tuple[ProfileOption, ...] = ()
    error: str = ""
    too_many: bool = False
    found: tuple[EngineProfile, ...] = ()
    presets: tuple[PresetRow, ...] = ()
    preset_options: tuple[ProfileOption, ...] = ()
    presets_error: str = ""


def _profile_options(
    found: Sequence[EngineProfile], current_id: str, skip: Collection[str] = ()
) -> tuple[ProfileOption, ...]:
    """Today's options: a deleted current profile first, then every non-deleted profile not in
    ``skip``, in the list's order."""
    options: list[ProfileOption] = []
    current = next((p for p in found if p.id == current_id), None)
    if current is not None and current.deleted_at is not None:
        options.append(ProfileOption(current.id, f"{current.name} · deleted (current)", True))
    for profile in found:
        if profile.deleted_at is not None or profile.id in skip:
            continue
        label = f"{profile.name} · v{profile.current_version} · {profile.id[:6]}"
        is_current = profile.id == current_id
        options.append(
            ProfileOption(profile.id, f"{label} (current)" if is_current else label, is_current)
        )
    return tuple(options)


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
    return ProfileSelect(_profile_options(found, current_id), found=tuple(found))


def preset_row(profile: EngineProfile, current_id: str) -> PresetRow:
    return PresetRow(
        profile.id,
        preset_label(profile),
        profile.current_version,
        quality_line(profile.quality) if profile.quality is not None else None,
        reverse("profiles:detail", args=[profile.id]) + "#guarantees",
        profile.id == current_id,
    )


def _preset_option(profile: EngineProfile, current_id: str) -> ProfileOption:
    label = f"{preset_label(profile)} · v{profile.current_version}"
    is_current = profile.id == current_id
    return ProfileOption(profile.id, f"{label} (current)" if is_current else label, is_current)


def with_presets(select: ProfileSelect, presets: object, current_id: str) -> ProfileSelect:
    """#307 (E13): the select with the presets first. ``presets`` is the ``?preset=true`` read's
    answer (``(items, cursor)``) or its failure. Read: the preset rows in :func:`preset_order`
    (non-deleted ``preset: true`` items only) form the table and the "Presets" optgroup. Failed:
    :data:`PRESETS_UNAVAILABLE`, and the full list's ``preset: true`` rows are grouped first
    instead. "Other profiles" leaves out every preset id either read named."""
    error = ""
    read: list[EngineProfile] = []
    if isinstance(presets, tuple) and presets and isinstance(presets[0], list):
        read = [
            p
            for p in presets[0]
            if isinstance(p, EngineProfile) and p.preset is True and p.deleted_at is None
        ]
    else:
        error = PRESETS_UNAVAILABLE
    listed = [p for p in select.found if p.preset is True and p.deleted_at is None]
    grouped = preset_order(read if not error else listed)
    rows = tuple(preset_row(p, current_id) for p in grouped) if not error else ()
    skip = {p.id for p in read} | {p.id for p in select.found if p.preset is True}
    options = select.options
    if select.found:
        options = _profile_options(select.found, current_id, skip)
    return replace(
        select,
        options=options,
        presets=rows,
        preset_options=tuple(_preset_option(p, current_id) for p in grouped),
        presets_error=error,
    )


def _reads(reads: Mapping[str, Callable[[], Any]]) -> dict[str, Any]:
    return fan_out(lambda name: reads[name](), list(reads))


@dataclass(frozen=True)
class Loaded:
    """Config's reads: the effective configuration and, each ``None`` when it failed, the
    running profile version, the schema and the story; on the Config page also the guarantee
    table or its failure (#57). The story's ``settings`` also feed the guarantee rows' story
    setting (#169: the Needs suffix and the Status reason); no extra read."""

    config: EffectiveConfig
    profile: EngineProfile | None
    profile_error: str
    schema: ProfileSchema | None
    story: Story | None
    guarantees: GuaranteeTable | EngineClientError | None = None
    versions: list[ProfileVersion] | EngineClientError | None = None  # #63, Config only


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
        reads["presets"] = lambda: client.list_engine_profiles(  # #307: one page, cursor ignored
            preset=True, limit=ENGINE_PROFILES_PAGE
        )
        reads["guarantees"] = lambda: client.get_story_guarantees(story_id)
        if not config.profile.deleted:  # #63: a deleted profile offers no version form
            reads["versions"] = lambda: client.list_engine_profile_versions(config.profile.id)
    results = _reads(reads)
    for name, value in results.items():
        if isinstance(value, EngineClientError) and name != "guarantees":  # logged by its section
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
        results.get("guarantees"),
        results.get("versions"),
    )
    if not select:
        return loaded, None
    chosen = results["profiles"]
    if not isinstance(chosen, ProfileSelect):
        chosen = ProfileSelect(error=PROFILES_UNAVAILABLE)
    return loaded, with_presets(chosen, results["presets"], config.profile.id)


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


Choice = int | str  # a version number or ``LATEST``


@dataclass
class VersionState:
    """The Profile version section after a refused change (#63): the posted choice
    pre-selected, a line above the form, one under the select, the overrides lines and a
    refusal."""

    chosen: str = ""
    error: str = ""
    select_error: str = ""
    lines: list[str] = field(default_factory=list)
    refusal: Refusal | None = None


@dataclass(frozen=True)
class VersionOption:
    value: str
    label: str
    disabled: bool = False


@dataclass(frozen=True)
class VersionSelect:
    """The Profile version section: ``deleted`` (no form), ``error`` (the versions read
    failed) or the options with the signed token, a fresh idempotency key and whether the story
    is archived."""

    options: tuple[VersionOption, ...] = ()
    error: str = ""
    deleted: bool = False
    archived: bool = False
    token: str = ""
    key: str = ""


def pin_text(ref: ProfileRef, latest: int | None) -> str:
    """Config's pin clause: "pinned at v1 (latest is v2)", "pinned at v2 (the latest)" or
    "follows latest"; without the latest clause when the profile read failed."""
    if not ref.pinned:
        return "follows latest"
    text = f"pinned at v{ref.version}"
    if latest is None:
        return text
    return f"{text} (latest is v{latest})" if ref.version < latest else f"{text} (the latest)"


def current_choice(ref: ProfileRef) -> Choice:
    return ref.version if ref.pinned else LATEST


def version_label(version: ProfileVersion) -> str:
    """ "v2 · 2026-10-28 14:03:05 UTC · npond" (`` · <created_by>`` left out when empty)."""
    label = f"v{version.version} · {utc_text(version.created_at)}"
    return f"{label} · {version.created_by}" if version.created_by else label


def version_options(versions: list[ProfileVersion], ref: ProfileRef) -> tuple[VersionOption, ...]:
    """ "Latest" then each version newest first, the story's current choice "(current)" and
    disabled; a pinned number missing from the list added as "vN (current)" in its place."""
    current = current_choice(ref)
    options = [
        VersionOption(LATEST, f"{LATEST_LABEL} (current)", True)
        if current == LATEST
        else VersionOption(LATEST, LATEST_LABEL)
    ]
    labels = {v.version: version_label(v) for v in versions}
    if isinstance(current, int) and current not in labels:
        labels[current] = f"v{current}"
    for number in sorted(labels, reverse=True):
        if number == current:
            options.append(VersionOption(str(number), f"{labels[number]} (current)", True))
        else:
            options.append(VersionOption(str(number), labels[number]))
    return tuple(options)


def _version_select(loaded: Loaded, story_id: str) -> VersionSelect:
    config = loaded.config
    if config.profile.deleted:
        return VersionSelect(deleted=True)
    versions = loaded.versions
    if not isinstance(versions, list):
        return VersionSelect(error=VERSIONS_UNAVAILABLE)
    profile = loaded.profile
    base = {
        "current": current_choice(config.profile),
        "latest": profile.current_version if profile is not None else None,
        "profile_id": config.profile.id,
        "profile_version": config.profile.version,
        "pinned": config.profile.pinned,
        "revision": config.overrides.revision,
        "name": profile.name if profile is not None else config.profile.id,
    }
    return VersionSelect(
        version_options(versions, config.profile),
        archived=loaded.story is not None and loaded.story.status == "archived",
        token=twostage.sign(VERSION_KIND, story_id, {}, base),
        key=new_ulid(),
    )


def _config_page(
    request: HttpRequest,
    client: EngineClient,
    story_id: str,
    assign: AssignState | None = None,
    version: VersionState | None = None,
) -> HttpResponse:
    loaded, select = _load(request, client, story_id, select=True)
    config = loaded.config
    latest = loaded.profile.current_version if loaded.profile is not None else None
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
        "no_presets_text": NO_PRESETS,
        "assign": assign or AssignState(),
        "assign_url": reverse("stories:config-profile", args=[story_id]),
        "pin_text": pin_text(config.profile, latest),
        "versions": _version_select(loaded, story_id),
        "version": version or VersionState(),
        "version_url": reverse("stories:config-version", args=[story_id]),
        "version_placeholder": VERSION_PLACEHOLDER,
        "version_deleted_text": VERSION_DELETED,
        "archived_apply": ARCHIVED_APPLY,
        "confirm_field": twostage.CONFIRM_FIELD,
        "overrides_url": reverse("stories:config-overrides", args=[story_id]),
        "settings_url": reverse("stories:settings", args=[story_id]),
        "story_url": reverse("stories:detail", args=[story_id]),
        "history_url": reverse("stories:history", args=[story_id]),
    }
    if loaded.guarantees is not None:
        name = loaded.profile.name if loaded.profile is not None else ""
        context.update(
            guarantees_section(
                request,
                loaded.guarantees,
                "story",
                profile_name=name,
                note=CONFIG_SCOPE,
                story_settings=loaded.story.settings if isinstance(loaded.story, Story) else None,
            )
        )
    return render(request, "console/stories/config.html", context)


@require_safe
def config_page(request: HttpRequest, story_id: str) -> HttpResponse:
    return _config_page(request, engine_client_for(request), story_id)


# --- the preview page -----------------------------------------------------------------------------


def upgrade_unavailable(impact: ImpactReport) -> bool:
    return any(story.upgrade_unavailable for story in impact.affected_stories)


def needs_upgrade_mode(impact: ImpactReport) -> bool:
    """#307 (E14): whether the confirm step asks backfill-or-forward-only: any affected story's
    ``backfill.needed`` (exactly when the Engine reads ``upgrade_mode``)."""
    return any(story.backfill.needed for story in impact.affected_stories)


def story_entry(impact: ImpactReport, story_id: str) -> AffectedStory | None:
    """The story's own ``affected_stories`` entry (``story_id`` match), else the first."""
    stories = impact.affected_stories
    return next((s for s in stories if s.story_id == story_id), stories[0] if stories else None)


def backfill_line(impact: ImpactReport, story_id: str) -> str:
    """The line under the upgrade-mode radios: "<backfill_text> · turns a\u2013b" (the turns
    left out when the Engine gave no range)."""
    entry = story_entry(impact, story_id)
    if entry is None:
        return ""
    turns = turns_text(entry.backfill.range)
    text = backfill_text(entry.backfill)
    return f"{text} · {turns}" if turns else text


def model_json(value: Any) -> Any:
    """A parsed model back as the JSON it was read from (each field under its ``json_name``), so
    ``Model.from_json`` rebuilds it; lists and dicts recursively, anything else as is."""
    if is_dataclass(value) and not isinstance(value, type):
        return {json_name(f): model_json(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, list):
        return [model_json(item) for item in value]
    if isinstance(value, dict):
        return {key: model_json(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    return value


SIGNED_PREVIEW_KEYS: Final = ("impact", "heading", "lines", "patch_text", "again")
"""What a choice-bearing preview signs into ``base`` so a missing or invalid mode re-renders it
with no Engine call (E8's one-URL rule)."""
DERIVED_KEYS: Final = ("warned", "fingerprint_before", "upgrade_choice", *SIGNED_PREVIEW_KEYS)
"""``base`` keys :func:`_preview_page` derives from its report; a held re-render drops them."""


def _kept(base: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in base.items() if k not in DERIVED_KEYS}


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


CONFIRM_FORM_ID = "config-confirm"


def _upgrade_form(data: Any = None, initial: str = DEFAULT_UPGRADE_MODE) -> UpgradeModeForm:
    return UpgradeModeForm(data, form_id=CONFIRM_FORM_ID, initial_mode=initial)


def _preview_page(
    request: HttpRequest,
    preview: Preview,
    impact: ImpactReport,
    *,
    upgrade_form: UpgradeModeForm | None = None,
    key: str = "",
) -> HttpResponse:
    """The impact step. #307: when :func:`needs_upgrade_mode`, ``upgrade_choice`` and the
    preview itself are signed into ``base`` and (unless archived, E21) the upgrade-mode radios
    join the confirm form; ``upgrade_form`` is a bound one to re-show (its posted value and
    errors), ``key`` the posted key to keep."""
    upgrade_choice = needs_upgrade_mode(impact)
    # A needed backfill is one of the Engine's warning kinds (FR-PROF-7), so its confirm always
    # sends ``ignore_warnings`` even when a report's ``warnings`` came back empty.
    warned = bool(impact.warnings) or preview.again or upgrade_choice
    base = {
        **preview.base,
        "warned": warned,
        "fingerprint_before": impact.fingerprint_before,
        "upgrade_unavailable": upgrade_unavailable(impact),
        "upgrade_choice": upgrade_choice,
    }
    if upgrade_choice:
        base.update(
            impact=model_json(impact),
            heading=preview.heading,
            lines=list(preview.lines),
            patch_text=preview.patch_text,
            again=preview.again,
        )
    token = twostage.sign(preview.kind, preview.story_id, preview.body, base)
    apply_name = (
        "config-overrides-apply" if preview.kind == OVERRIDES_KIND else "config-profile-apply"
    )
    back = (
        reverse("stories:config-overrides", args=[preview.story_id])
        if preview.kind == OVERRIDES_KIND
        else config_url(preview.story_id)
    )
    shown_form = None
    if upgrade_choice and not preview.archived:
        shown_form = upgrade_form if upgrade_form is not None else _upgrade_form()
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
        "guarantees_href": config_url(preview.story_id),  # #57: lost guarantees link their rows
        "idempotency_key": key or new_ulid(),  # the confirming POST's own key (#64 convention 3)
        "archived_note": ARCHIVED_APPLY if preview.archived else "",
        "upgrade_form": shown_form,
        "confirm_form_id": CONFIRM_FORM_ID if shown_form is not None else "",
        "backfill_line": backfill_line(impact, preview.story_id) if shown_form else "",
    }
    return render(request, "console/stories/config_preview.html", context)


def _chosen_mode(
    request: HttpRequest, client: EngineClient, payload: twostage.Payload, key: str
) -> tuple[UpgradeMode | None, HttpResponse | None]:
    """A confirm's ``upgrade_mode`` (#307): ``(None, None)`` when the preview offered no choice;
    ``(mode, None)`` when it did and the POST carries a valid one; else ``(None, page)``, the
    same preview re-rendered from the token with the field's error and the posted key, and no
    Engine write. A token whose signed report can't be rebuilt is treated as expired."""
    base: dict[str, Any] = payload.base
    if base.get("upgrade_choice") is not True:
        return None, None
    form = _upgrade_form(request.POST)
    if form.is_valid():
        return cast(UpgradeMode, form.mode()), None
    impact = ImpactReport.from_json(base.get("impact"))
    if impact is None:
        messages.error(request, twostage.EXPIRED_TEXT)
        return None, redirect(config_url(payload.target_id))
    lines = base.get("lines")
    preview = Preview(
        payload.kind,
        payload.target_id,
        payload.body,
        _kept(base),
        str(base.get("heading") or ""),
        lines=tuple(str(line) for line in lines) if isinstance(lines, list) else (),
        patch_text=str(base.get("patch_text") or ""),
        archived=_archived(client, payload.target_id),
        again=base.get("again") is True,
    )
    return None, _preview_page(request, preview, impact, upgrade_form=form, key=key)


def _posted_mode(request: HttpRequest) -> str:
    """The posted mode when it is a valid one (a held re-render's initial), else backfill."""
    form = _upgrade_form(request.POST)
    return form.mode() if form.is_valid() else DEFAULT_UPGRADE_MODE


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


def _guarantee_ids(ids: list[str]) -> SafeString:
    return format_html_join(", ", "<code>{}</code>", ((g,) for g in ids))


def _flash_applied(
    request: HttpRequest,
    write: StoryProfileWrite,
    base: Mapping[str, Any],
    *,
    version_change: bool = False,
    story_id: str = "",
) -> None:
    """After an applied write: kept (``upgrade_unavailable`` and the fingerprint unchanged),
    no change, or the new fingerprint (after a version change, the new pin first), then the
    impact's guarantees gained and lost (#63). ``pinned`` joins the no-change comparison only
    when ``base`` carries it (a version change's base does). #307: a ``forward_only_gap`` on the
    story's impact entry appends :func:`gap_sentence` to whichever of these it is."""
    level, text = _applied_text(write, base, version_change=version_change)
    entry = story_entry(write.impact, story_id)
    gap = gap_sentence(entry.forward_only_gap, story_id) if entry is not None else None
    if gap is not None:
        text = format_html("{}{} {}", text, "" if str(text).endswith(".") else ".", gap)
    level(request, text)


def gap_sentence(gap: ForwardOnlyGap | None, story_id: str) -> SafeString | None:
    """ "Turns through 3 keep a coverage gap for: <code>episodic</code> — fill it from the
    Backfill page" ("Backfill page" linked through ``gap_fill_url``, #309's ``#gap-fill``;
    plain text while unregistered, D19); ``None`` with no gap, ``through_turn`` < 1 or no
    capabilities."""
    if gap is None or gap.through_turn < 1 or not gap.capabilities:
        return None
    url = gap_fill_url(story_id)
    page = format_html('<a href="{}">{}</a>', url, BACKFILL_PAGE) if url else BACKFILL_PAGE
    capabilities = format_html_join(", ", "<code>{}</code>", ((c,) for c in gap.capabilities))
    return format_html(
        FORWARD_ONLY_GAP, through=gap.through_turn, capabilities=capabilities, page=page
    )


def _applied_text(
    write: StoryProfileWrite, base: Mapping[str, Any], *, version_change: bool
) -> tuple[Callable[[HttpRequest, str], None], str]:
    result = write.result
    if result is None:
        return messages.info, NO_CHANGE
    kept = upgrade_unavailable(write.impact) and result.fingerprint == (
        write.impact.fingerprint_before
    )
    if kept:
        return messages.warning, KEPT
    unchanged = (
        result.overrides.revision == base.get("revision")
        and result.profile.id == base.get("profile_id")
        and result.profile.version == base.get("profile_version")
        and ("pinned" not in base or result.profile.pinned == base["pinned"])
    )
    if unchanged:
        return messages.info, NO_CHANGE
    text = format_html(
        'Applied — fingerprint <code title="{}">{}</code>; takes effect from the next turn',
        result.fingerprint,
        short_fingerprint(result.fingerprint),
    )
    if version_change:
        pin = (
            f"Now pinned at v{result.profile.version}."
            if result.profile.pinned
            else f"Now follows the latest version (v{result.profile.version})."
        )
        text = format_html("{} {}", pin, text)
    impact = write.impact
    if impact.guarantees_gained or impact.guarantees_lost:
        text = format_html("{}.", text)
    for label, ids in (("gained", impact.guarantees_gained), ("lost", impact.guarantees_lost)):
        if ids:
            text = format_html("{} Guarantees {}: {}.", text, label, _guarantee_ids(ids))
    return messages.success, text


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
    mode, page = _chosen_mode(request, client, payload, key)
    if page is not None:
        return page
    try:
        write = client.patch_profile_overrides(
            story_id,
            payload.body,
            expected_revision=revision if isinstance(revision, int) else None,
            ignore_warnings=base.get("warned") is True,
            idempotency_key=key,
            upgrade_mode=mode,
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _apply_refused(request, client, story_id, base, exc, key)
    if write.backfill is not None:  # the 202: a backfill started, not a held change
        return _backfill_started(request, story_id, write, base, key)
    if not write.applied:
        preview = Preview(
            OVERRIDES_KIND,
            story_id,
            payload.body,
            _kept(base),
            "Preview: change this story's overrides",
            patch_text=json.dumps(payload.body, indent=2, ensure_ascii=False),
            archived=_archived(client, story_id),
            again=True,
        )
        return _held_again(request, preview, write.impact)
    _flash_applied(request, write, base, story_id=story_id)
    return redirect(config_url(story_id))


def _held_again(request: HttpRequest, preview: Preview, impact: ImpactReport) -> HttpResponse:
    """A fresh ``applied: false`` on Apply: the new report with "Apply anyway", the radios (if
    any) pre-set to the posted valid mode."""
    form = _upgrade_form(initial=_posted_mode(request))
    return _preview_page(request, preview, impact, upgrade_form=form)


def _backfill_started(
    request: HttpRequest,
    story_id: str,
    write: StoryProfileWrite,
    base: Mapping[str, Any],
    key: str,
) -> HttpResponse:
    """#307: a confirmed write that started a backfill (the 202): :data:`BACKFILL_STARTED`,
    " · Follow the backfill" (plain text while ``stories:backfill`` is unregistered, D19) and
    E21's key tail; then the Backfill page, else Config."""
    entry = story_entry(write.impact, story_id)
    span = entry.backfill.range if entry is not None else None
    if span is None:
        span = _signed_span(base, story_id)
    turns = turns_text(span)
    text = BACKFILL_STARTED.format(rederives=f" while it re-derives {turns}" if turns else "")
    url = backfill_url(story_id)
    follow = format_html('<a href="{}">{}</a>', url, FOLLOW_BACKFILL) if url else FOLLOW_BACKFILL
    messages.success(
        request,
        format_html(
            "{} · {} — idempotency key <code>{}</code>",
            text,
            follow,
            write.idempotency_key or key,
        ),
    )
    return redirect(url or config_url(story_id))


def _signed_span(base: Mapping[str, Any], story_id: str) -> TurnSpan | None:
    """The backfill range the preview's report carried (signed in ``base``)."""
    impact = ImpactReport.from_json(base.get("impact"))
    entry = story_entry(impact, story_id) if impact is not None else None
    return entry.backfill.range if entry is not None else None


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
    chosen = (
        request.POST.get(PROFILE_FIELD, "").strip()  # a preset row's Preview button (#307)
        or request.POST.get(CHOICE_FIELD, "").strip()  # the select or the id input
    )
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
    version = _signed_version(payload.body)  # #63: a version change's body carries it
    client = engine_client_for(request)
    key = posted_idempotency_key(request)
    name = str(base.get("name") or chosen)
    mode, page = _chosen_mode(request, client, payload, key)
    if page is not None:
        return page
    try:
        write = client.assign_engine_profile(
            story_id,
            chosen,
            ignore_warnings=base.get("warned") is True,
            idempotency_key=key,
            version=version,
            upgrade_mode=mode,
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if version is not None:
            state = _version_refused(exc, story_id, version, name, base.get("latest"))
            return _config_page(request, client, story_id, version=state)
        return _config_page(request, client, story_id, _assign_refused(exc, chosen, key, name))
    if write.backfill is not None:  # the 202: a backfill started, not a held change
        return _backfill_started(request, story_id, write, base, key)
    if not write.applied:
        kept = _kept(base)
        if version is not None:
            preview = _version_preview(client, story_id, payload.body, kept, version)
        else:
            preview = Preview(
                PROFILE_KIND,
                story_id,
                payload.body,
                kept,
                f"Preview: assign {name}",
                archived=_archived(client, story_id),
                again=True,
            )
        return _held_again(request, preview, write.impact)
    _flash_applied(request, write, base, version_change=version is not None, story_id=story_id)
    return redirect(config_url(story_id))


# --- change the profile version (#63) -------------------------------------------------------------


def _signed_version(body: Mapping[str, Any]) -> int | Literal["latest"] | None:
    """A signed body's ``version`` (``None`` when it carries none: an assign)."""
    value = body.get(VERSION_FIELD)
    if value == LATEST:
        return LATEST
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def parse_version(raw: str | None) -> int | Literal["latest"] | None:
    """The posted choice, stripped: ``latest`` (any case), or ASCII digits from 1 to 2**31 - 1
    ("02" is 2); anything else is ``None``."""
    value = (raw or "").strip()
    if value.lower() == LATEST:
        return LATEST
    if not re.fullmatch(r"[0-9]+", value):
        return None
    number = int(value)
    return number if 1 <= number <= VERSION_MAX else None


def _latest_of(base: Mapping[str, Any]) -> int | None:
    latest = base.get("latest")
    return latest if isinstance(latest, int) and not isinstance(latest, bool) else None


def version_heading(version: Choice, latest: int | None) -> str:
    if version == LATEST:
        shown = f" (v{latest})" if latest is not None else ""
        return f"Preview: follow the latest version{shown}"
    return f"Preview: pin to v{version}"


def version_line(version: Choice, name: str, latest: int | None) -> str:
    if version == LATEST:
        shown = f" (v{latest})" if latest is not None else ""
        return f"Follows {name}'s latest version{shown} from the next turn"
    return f"Runs {name} v{version} from the next turn"


def _version_preview(
    client: EngineClient,
    story_id: str,
    body: dict[str, Any],
    base: dict[str, Any],
    version: Choice,
) -> Preview:
    latest = _latest_of(base)
    name = str(base.get("name") or body.get("profile_id", ""))
    return Preview(
        PROFILE_KIND,
        story_id,
        body,
        base,
        version_heading(version, latest),
        lines=(version_line(version, name, latest),),
        archived=_archived(client, story_id),
        again=True,
    )


def _version_target(version: Choice, name: str, latest: Any) -> str:
    if version == LATEST:
        return f"{name} v{latest}" if isinstance(latest, int) else f"{name}'s latest version"
    return f"{name} v{version}"


def _version_refused(
    exc: EngineClientError, story_id: str, version: Choice, name: str, latest: Any
) -> VersionState:
    """A refused version change, in the Profile version section with the choice kept."""
    state = VersionState(chosen=str(version))
    if isinstance(exc, EngineError):
        if (
            exc.http_status == 422
            and exc.code == "validation_error"
            and exc.details.get("field") == VERSION_FIELD_PATH
        ):
            state.select_error = NO_SUCH_VERSION.format(version=version)
            return state
        if exc.code == PROFILE_DELETED:
            state.error = DELETED_PICK
            return state
        if exc.http_status == 422 and exc.code == INVALID_PROFILE and "overrides" in exc.details:
            target = _version_target(version, name, latest)
            state.error = f"{CANT_RUN.format(profile=target)}: {exc.message}"
            state.lines = overrides_lines(exc.details.get("overrides"))
            return state
        if exc.code in (STORY_ARCHIVED, "story_paused", "story_busy", REVISION_CONFLICT):
            refusal = write_refusal(exc, story_id, VERSION_VERB)
            if refusal is not None:
                state.refusal = Refusal(
                    notice=refusal.text, notice_url=refusal.url, line=_line(exc)
                )
                return state
    state.refusal = Refusal(panel=panel_for(exc))
    return state


@require_POST
def version_change(request: HttpRequest, story_id: str) -> HttpResponse:
    """The Profile version form: ``PUT …/engine-profile {profile_id, version,
    ignore_warnings: false}`` at once (no preview); a local refusal makes no write."""
    client = engine_client_for(request)
    raw = request.POST.get(VERSION_FIELD, "")
    try:
        payload = twostage.unsign(VERSION_KIND, request.POST.get(twostage.CONFIRM_FIELD, ""))
    except twostage.ConfirmationExpired:
        payload = None
    if payload is None or payload.target_id != story_id or not isinstance(payload.base, dict):
        state = VersionState(chosen=raw.strip(), error=EXPIRED)
        return _config_page(request, client, story_id, version=state)
    base: dict[str, Any] = payload.base
    version = parse_version(raw)
    if version is None:
        return _config_page(request, client, story_id, version=VersionState(error=PICK_VERSION))
    if version == base.get("current"):
        state = VersionState(chosen=str(version), error=SAME_VERSION)
        return _config_page(request, client, story_id, version=state)
    profile_id = str(base.get("profile_id", ""))
    name = str(base.get("name") or profile_id)
    key = posted_idempotency_key(request)
    try:
        write = client.assign_engine_profile(
            story_id, profile_id, ignore_warnings=False, idempotency_key=key, version=version
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        state = _version_refused(exc, story_id, version, name, base.get("latest"))
        return _config_page(request, client, story_id, version=state)
    body = {"profile_id": profile_id, VERSION_FIELD: version}
    if write.backfill is not None:  # #307's guard: a first stage that started a backfill
        return _backfill_started(request, story_id, write, base, key)
    if not write.applied:
        return _preview_page(
            request, _version_preview(client, story_id, body, dict(base), version), write.impact
        )
    _flash_applied(request, write, base, version_change=True, story_id=story_id)
    return redirect(config_url(story_id))

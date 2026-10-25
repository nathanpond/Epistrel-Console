"""Catalog scenarios (#68): the list, a scenario's detail and versions, and its editor.

- ``/catalog/scenarios/``: ``GET /catalog/scenarios`` with #65's filters plus "Include
  companions" (:class:`console.forms.catalog.ScenarioFilterForm`), cursor-paged. A companion is
  marked "Companion of <character>" under its name: one card read per distinct character on the
  page, on the shared fan-out pool (a failed read shows the id).
- ``/catalog/scenarios/<id>/``: the latest published version in ``full`` (``?view=draft``,
  ``?version=N``, ``?projection=card`` as #65); a never-published entry shows its draft; on the
  full latest view the draft is read too, to say when it differs.
- ``/catalog/scenarios/<id>/versions/``: the versions, newest first.
- ``/catalog/scenarios/new/`` and ``<id>/edit/``: the editor (:mod:`console.forms.scenario`).
  The page carries a signed snapshot (the draft as rendered, its revision, the role ids and the
  two lookups' options), so a refused form, a row button and "Update form" make no Engine call.
  Save PATCHes the changed groups with ``draft_revision``; create POSTs the non-empty groups.
  #71: the Media section (cover, gallery, videos; :mod:`console.forms.media_picker`) is sent
  whole when it changed, and the detail page shows the media by slot through the proxy.
"""

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_safe

from console.acting import acting_principal
from console.confirm import posted_idempotency_key
from console.draft_stale import DRAFT_STALE, stale_banner
from console.engine.choices import (
    ProfileChoices,
    RatingChoices,
    engine_profile_choices,
    error_text,
    rating_choices,
)
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import CatalogVersion, Scenario, ScenarioCard
from console.engine.pool import fan_out
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.entities import label
from console.forms.catalog import ScenarioFilterForm
from console.forms.media_picker import (
    SCENARIO_SECTIONS,
    MediaSections,
    media_ids,
    media_op,
    scenario_media,
)
from console.forms.principal import remember_principals
from console.forms.scenario import (
    OWNER,
    ScenarioEditor,
    apply_row_op,
    draft_to_write,
    editor_data,
    retemplated,
)
from console.formset_rows import OP, OP_REFRESH, parse_op
from console.ids import new_ulid
from console.media_slots import (
    LARGE,
    SMALL,
    ShownMedia,
    SlotInfo,
    info_for,
    infos_from_snapshot,
    infos_to_snapshot,
    slot_infos,
)
from console.views.catalog_lifecycle import SCENARIO as LIFECYCLE_ENTRY
from console.views.catalog_lifecycle import Follows, follows_label, lifecycle_for

LIST_URL = "/catalog/scenarios/"
NOT_PUBLISHED = "not_published"
DRAFT = "draft"
CARD = "card"
ARCHIVED = "archived"
ENTRY_ARCHIVED = "entry_archived"
NEVER_PUBLISHED = "Draft — never published"
ARCHIVED_NOTE = "Archived — unarchive to edit"
NO_CHANGES = "No changes"
NO_REVISION = "The page carried no draft revision — reload it and retry."
DESCRIPTION_SHORT = 200
# The content groups "Draft differs from published" compares (#65's rule; not the envelope).
COMPARED_GROUPS = (
    "public_card",
    "metadata",
    "world",
    "cast",
    "starting_state",
    "drivers",
    "story_settings_defaults",
    "recommendation",
    "visual_style",
    "creation_rules",
)


# --- shared ---------------------------------------------------------------------------------------


def _listing_values(request: HttpRequest, client: EngineClient) -> tuple[list[str] | None, str]:
    """``/admin/settings``'s listing-status values, or ``None`` and the error text."""
    try:
        return client.get_deployment_settings().listing_status_values, ""
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None, error_text(exc)


@dataclass(frozen=True)
class CompanionLabel:
    """How a page names the character a companion scenario belongs to: its card's name (or the
    id, with ``error``, when the read failed) and its detail URL."""

    character_id: str
    text: str
    url: str
    error: str = ""


def companion_labels(client: EngineClient, ids: list[str]) -> dict[str, CompanionLabel]:
    """One ``GET /catalog/characters/{id}?projection=card`` per distinct id, on the fan-out
    pool; a failed read is labelled by the id."""
    distinct = list(dict.fromkeys(i for i in ids if i))
    results = fan_out(
        lambda character_id: client.get_catalog_character_card(character_id), distinct
    )
    labels: dict[str, CompanionLabel] = {}
    for character_id in distinct:
        url = reverse("catalog_characters:detail", args=[character_id])
        result = results[character_id]
        if isinstance(result, EngineClientError):
            labels[character_id] = CompanionLabel(
                character_id, character_id, url, error_text(result)
            )
        else:
            labels[character_id] = CompanionLabel(
                character_id, label(result.name, character_id), url
            )
    return labels


# --- list -----------------------------------------------------------------------------------------


def detail_link(scenario_id: str, back: str) -> str:
    url = reverse("scenarios:detail", args=[scenario_id])
    return f"{url}?{urlencode({'back': back})}"


def filtered(**params: str) -> str:
    return f"{LIST_URL}?{urlencode(params)}"


@dataclass(frozen=True)
class ListRow:
    item: Any  # ScenarioListItem
    name: str
    url: str
    label_links: list[tuple[str, str]]
    owner_filter: str
    companion: CompanionLabel | None


@require_safe
def scenario_list(request: HttpRequest) -> HttpResponse:
    client = engine_client_for(request)
    ratings = rating_choices(client, include_retired=True)
    listing_values, listing_error = _listing_values(request, client)
    form = ScenarioFilterForm(request.GET, ratings=ratings, listing_values=listing_values)
    rows: list[ListRow] = []
    next_query = ""
    engine_error: ErrorPanel | None = None
    searching = False
    if form.is_valid():
        query = form.query()
        searching = bool(query["q"])
        try:
            items, next_cursor = client.list_scenarios(**query)
        except EngineClientError as exc:
            log_engine_error(request, exc)
            engine_error = panel_for(exc)
        else:
            back = request.get_full_path()
            companions = companion_labels(client, [i.companion_of or "" for i in items])
            rows = [
                ListRow(
                    item,
                    label(item.name, item.id),
                    detail_link(item.id, back),
                    [(name, filtered(label=name)) for name in item.labels],
                    filtered(owner=item.owner_principal),
                    companions.get(item.companion_of or ""),
                )
                for item in items
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
        "searching": searching,
        "engine_error": engine_error,
        "ratings": ratings,
        "rating_error": error_text(ratings.error) if ratings.error is not None else "",
        "listing_error": listing_error,
        "next_query": next_query,
        "first_query": first.urlencode(),
        "paged": bool(request.GET.get("cursor")),
        "any_filter": form.any_filter,
    }
    return render(request, "console/catalog/scenarios/list.html", context)


# --- detail ---------------------------------------------------------------------------------------


def safe_back(request: HttpRequest) -> str:
    """``?back=`` when it is a local path under the scenario list, else the list itself."""
    back = request.GET.get("back") or ""
    allowed = url_has_allowed_host_and_scheme(
        back, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return back if allowed and back.startswith(LIST_URL) else LIST_URL


def _query(params: QueryDict, **changes: str | None) -> str:
    query = params.copy()
    for key, value in changes.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return f"?{query.urlencode()}"


def _blank(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _normalised(value: object) -> object:
    """``value`` with ``null``, ``""``, ``[]`` and ``{}`` dropped from objects; lists kept."""
    if isinstance(value, dict):
        return {k: _normalised(v) for k, v in value.items() if not _blank(_normalised(v))}
    if isinstance(value, list):
        return [_normalised(v) for v in value]
    return value


def comparable(entry: Scenario) -> object:
    raw = asdict(entry)
    return _normalised({group: raw[group] for group in COMPARED_GROUPS})


def draft_differs(latest: Scenario, draft: Scenario) -> bool:
    return comparable(latest) != comparable(draft)


def _heading(
    entry: Scenario | ScenarioCard,
    *,
    never_published: bool,
    draft: bool,
    version: str | None,
    card: bool,
) -> str:
    suffix = " (public card view)" if card else ""
    if never_published:
        return f"{NEVER_PUBLISHED}{suffix}"
    if draft:
        revision = getattr(entry, "draft_revision", None)
        return f"Draft, revision {revision}" if revision is not None else "Draft"
    current = getattr(entry, "current_version", entry.version)
    if version is not None:
        latest = " (latest)" if entry.version is not None and entry.version == current else ""
        return f"Version {entry.version}{'' if card else latest}"
    return f"Version {entry.version} (latest)"


@dataclass(frozen=True)
class Named:
    """A lookup's answer for one id: its ``name`` when known (``None``: show the id)."""

    id: str
    name: str | None


def _rating_name(client: EngineClient, rating_id: str | None) -> Named | None:
    if not rating_id:
        return None
    choices = rating_choices(client, current=rating_id, include_retired=True)
    names = dict(choices.choices)
    shown = names.get(rating_id)
    return Named(rating_id, shown if shown and not shown.endswith(" (missing)") else None)


def _profile_name(client: EngineClient, profile_id: str | None) -> Named | None:
    if not profile_id:
        return None
    choices = engine_profile_choices(client, current=profile_id)
    shown = dict(choices.choices).get(profile_id)
    return Named(profile_id, shown if shown and not shown.endswith(" (missing)") else None)


def _profile_url(profile_id: str) -> str:
    try:
        return reverse("profiles:detail", args=[profile_id])
    except NoReverseMatch:  # until #78 builds the profile pages
        return ""


def _media_ids(entry: Scenario | ScenarioCard) -> list[str]:
    if isinstance(entry, ScenarioCard):
        card = entry.media
        ids = [card.cover.media_id] if card.cover else []
        return [*ids, *(m.media_id for m in (*card.gallery, *card.videos))]
    return media_ids(scenario_media(entry))


def shown_media(entry: Scenario | ScenarioCard, infos: dict[str, SlotInfo]) -> list[ShownMedia]:
    """The cover (up to 320 px), the gallery (120 px) and the videos (caption below: the
    scenario's, else the asset's), by slot (#71). The card read carries alt text and captions;
    the full read only ids, so the looked-up asset's alt text is used."""
    shown: list[ShownMedia] = []
    if isinstance(entry, ScenarioCard):
        card = entry.media
        if card.cover is not None:
            shown.append(
                ShownMedia(
                    "Cover", info_for(infos, card.cover.media_id), LARGE, card.cover.alt_text
                )
            )
        for n, item in enumerate(card.gallery, start=1):
            info = info_for(infos, item.media_id)
            shown.append(ShownMedia(f"Gallery {n}", info, SMALL, item.alt_text))
        for n, item in enumerate(card.videos, start=1):
            info = info_for(infos, item.media_id)
            shown.append(ShownMedia(f"Video {n}", info, LARGE, item.alt_text, caption=item.caption))
        return shown
    media = entry.public_card.media
    if media.cover:
        info = info_for(infos, media.cover)
        shown.append(ShownMedia("Cover", info, LARGE, info.alt_text))
    for n, media_id in enumerate(media.gallery, start=1):
        info = info_for(infos, media_id)
        shown.append(ShownMedia(f"Gallery {n}", info, SMALL, info.alt_text))
    for n, video in enumerate(media.videos, start=1):
        info = info_for(infos, video.media_id)
        shown.append(ShownMedia(f"Video {n}", info, LARGE, info.alt_text, caption=video.caption))
    return shown


@require_safe
def scenario_detail(request: HttpRequest, scenario_id: str) -> HttpResponse:
    client = engine_client_for(request)
    params = request.GET
    draft = params.get("view") == DRAFT
    version = None if draft else (params.get("version") or None)
    card = params.get("projection") == CARD
    context: dict[str, Any] = {
        "scenario_id": scenario_id,
        "back_url": safe_back(request),
        "card": card,
        "versions_url": reverse("scenarios:versions", args=[scenario_id]),
        "edit_url": reverse("scenarios:edit", args=[scenario_id]),
        "card_query": _query(params, projection=CARD),
        "full_query": _query(params, projection=None),
        "draft_query": _query(params, view=DRAFT, version=None),
    }
    read = client.get_scenario_card if card else client.get_scenario
    never_published = False
    entry: Scenario | ScenarioCard
    try:
        try:
            entry = read(scenario_id, version=version, draft=draft)
        except EngineError as exc:
            latest = not draft and version is None
            if not (latest and exc.http_status == 422 and exc.code == NOT_PUBLISHED):
                raise
            entry = read(scenario_id, draft=True)
            never_published = True
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context["engine_error"] = panel_for(exc)
        return render(request, "console/catalog/scenarios/detail.html", context)
    differs = False
    if isinstance(entry, Scenario) and not (draft or never_published) and version is None:
        try:
            current_draft = client.get_scenario(scenario_id, draft=True)
        except EngineClientError as exc:
            log_engine_error(request, exc)  # the note is left out
        else:
            differs = draft_differs(entry, current_draft)
    follows: Follows | None = None
    if entry.companion_of:
        companion = companion_labels(client, [entry.companion_of])[entry.companion_of]
        context["companion"] = companion
        follows = Follows(companion.character_id, companion.text, companion.url)
    if isinstance(entry, Scenario):
        context["lifecycle"] = lifecycle_for(
            request,
            client,
            LIFECYCLE_ENTRY,
            scenario_id,
            name=entry.public_card.name,
            status=entry.status,
            listing_status=entry.listing_status,
            follows=follows,
        )
    context["media"] = shown_media(entry, slot_infos(client, _media_ids(entry)))
    if not isinstance(entry, ScenarioCard):
        context["rating"] = _rating_name(client, entry.metadata.content_rating_id)
        profile_id = entry.recommendation.engine_profile_id
        context["profile"] = _profile_name(client, profile_id)
        context["profile_url"] = _profile_url(profile_id) if profile_id else ""
    context.update(
        {
            "entry": entry,
            "full": isinstance(entry, Scenario),
            "is_draft": draft or never_published,
            "never_published": never_published,
            "is_archived": entry.status == ARCHIVED,
            "name": label(
                entry.name if isinstance(entry, ScenarioCard) else entry.public_card.name,
                scenario_id,
            ),
            "heading": _heading(
                entry, never_published=never_published, draft=draft, version=version, card=card
            ),
            "differs": differs,
            "short": DESCRIPTION_SHORT,
        }
    )
    return render(request, "console/catalog/scenarios/detail.html", context)


# --- versions ------------------------------------------------------------------------------------


@require_safe
def scenario_versions(request: HttpRequest, scenario_id: str) -> HttpResponse:
    client = engine_client_for(request)
    context: dict[str, Any] = {
        "scenario_id": scenario_id,
        "detail_url": reverse("scenarios:detail", args=[scenario_id]),
    }
    try:
        versions: list[CatalogVersion] = client.list_scenario_versions(scenario_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context["engine_error"] = panel_for(exc)
        return render(request, "console/catalog/scenarios/versions.html", context)
    name: str | None = None
    latest = max((v.version for v in versions), default=None)
    try:
        card_entry = client.get_scenario_card(scenario_id)
    except EngineClientError as exc:
        if not (isinstance(exc, EngineError) and exc.code == NOT_PUBLISHED):
            log_engine_error(request, exc)
            context["card_error"] = panel_for(exc)
    else:
        name = label(card_entry.name, scenario_id)
        latest = card_entry.version
    context.update(
        {
            "name": name,
            "versions": sorted(versions, key=lambda v: v.version, reverse=True),
            "latest": latest,
        }
    )
    return render(request, "console/catalog/scenarios/versions.html", context)


# --- create and edit ------------------------------------------------------------------------------

EDIT_TEMPLATE = "console/catalog/scenarios/edit.html"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.catalog_scenario"
DRAFT_REVISION = "draft_revision"


@dataclass(frozen=True)
class EditSnapshot:
    """What the editor posts back, signed: the draft as rendered (the merge patch's original),
    its revision and header facts, the cast's role ids (the role selects' options) and the two
    lookups' options or why they could not be read. A create page carries one too
    (``scenario_id`` empty)."""

    scenario_id: str = ""
    draft_revision: int | None = None
    original: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    name: str | None = None
    owner: str = ""
    current_version: int | None = None
    companion_of: str | None = None
    roles: list[str] = field(default_factory=list)
    ratings: list[tuple[str, str]] = field(default_factory=list)
    ratings_error: str = ""
    profiles: list[tuple[str, str]] = field(default_factory=list)
    profiles_note: str = ""
    # #71: the Media section as rendered (ids only) and each slot's looked-up metadata.
    media_original: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)

    def signed(self) -> str:
        return signing.dumps(asdict(self), salt=SNAPSHOT_SALT, compress=True)

    @classmethod
    def unsigned(cls, value: str | None, scenario_id: str) -> EditSnapshot | None:
        try:
            raw = signing.loads(value or "", salt=SNAPSHOT_SALT)
        except signing.BadSignature:
            return None
        if not isinstance(raw, dict) or raw.get("scenario_id") != scenario_id:
            return None
        try:
            raw["ratings"] = [(str(k), str(v)) for k, v in raw["ratings"]]
            raw["profiles"] = [(str(k), str(v)) for k, v in raw["profiles"]]
            return cls(**raw)
        except KeyError, TypeError, ValueError:
            return None

    @property
    def archived(self) -> bool:
        return self.status == ARCHIVED

    @property
    def rating(self) -> str | None:
        value = (self.original.get("metadata") or {}).get("content_rating_id")
        return value if isinstance(value, str) else None

    def rating_choices(self) -> RatingChoices:
        if self.ratings_error:
            return RatingChoices([], EngineClientError(self.ratings_error))
        return RatingChoices(list(self.ratings))

    def profile_choices(self) -> ProfileChoices:
        return ProfileChoices(list(self.profiles), note=self.profiles_note)


def _lookups(client: EngineClient, *, rating: str | None, profile: str | None) -> dict[str, Any]:
    ratings = rating_choices(client, current=rating)
    profiles = engine_profile_choices(client, current=profile)
    return {
        "ratings": list(ratings.choices),
        "ratings_error": ratings.unavailable_text if ratings.error is not None else "",
        "profiles": list(profiles.choices),
        "profiles_note": profiles.unavailable_text,
    }


def _role_ids(cast: object) -> list[str]:
    roles = cast.get("roles") if isinstance(cast, dict) else None
    ids = (r.get("id") for r in roles or [] if isinstance(r, dict))
    return list(dict.fromkeys(i for i in ids if isinstance(i, str) and i))


def _read_snapshot(client: EngineClient, scenario_id: str) -> EditSnapshot:
    """Read the draft (its errors escape), the two lookups and each attached asset (#71)."""
    draft = client.get_scenario(scenario_id, draft=True)
    original = draft_to_write(draft)
    media_original = scenario_media(draft)
    return EditSnapshot(
        scenario_id=scenario_id,
        draft_revision=draft.draft_revision,
        original=original,
        status=draft.status,
        name=draft.public_card.name,
        owner=draft.owner_principal,
        current_version=draft.current_version,
        companion_of=draft.companion_of,
        roles=_role_ids(draft.cast),
        media_original=media_original,
        media=infos_to_snapshot(slot_infos(client, media_ids(media_original))),
        **_lookups(
            client,
            rating=original["metadata"]["content_rating_id"],
            profile=original["recommendation"]["engine_profile_id"],
        ),
    )


def _edit_url(scenario_id: str) -> str:
    return reverse("scenarios:edit", args=[scenario_id])


def _editor(snap: EditSnapshot, data: QueryDict, *, create: bool) -> ScenarioEditor:
    return ScenarioEditor(
        data,
        ratings=snap.rating_choices(),
        profiles=snap.profile_choices(),
        roles=snap.roles,
        create=create,
        current_rating=snap.rating,
    )


def _sections(snap: EditSnapshot, data: QueryDict | None = None) -> MediaSections:
    """The Media section (#71), bound to ``data`` (``None``: the draft's, shown without
    validating)."""
    sections = MediaSections(
        data if data is not None else QueryDict(),
        SCENARIO_SECTIONS,
        original=snap.media_original,
        infos=infos_from_snapshot(snap.media),
        disabled=snap.archived,
    )
    if data is None:
        sections.quiet()
    return sections


def _render_edit(
    request: HttpRequest,
    snap: EditSnapshot,
    editor: ScenarioEditor,
    sections: MediaSections | None = None,
    *,
    create: bool = False,
    stale: str = "",
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    scenario_id = snap.scenario_id
    if sections is None:
        sections = _sections(snap)
    context: dict[str, Any] = {
        "media_sections": sections,
        "media": sections.by_path(),
        "create": create,
        "snap": snap,
        "editor": editor,
        "form": editor.form,
        "sections": editor.sections(),
        "row_sets": editor.row_sets(),
        "scenario_id": scenario_id,
        "name": label(snap.name, scenario_id) if not create else "",
        "snapshot": snap.signed(),
        "idempotency_key": new_ulid(),
        "stale": stale,
        "engine_error": engine_error,
        "rating_note": snap.ratings_error,
        "archived_note": ARCHIVED_NOTE if snap.archived else "",
    }
    if create:
        context["action_url"] = reverse("scenarios:new")
    else:
        # The banner names a companion's character by id: the edit page reads nothing for it.
        context["lifecycle"] = lifecycle_for(
            request,
            None,
            LIFECYCLE_ENTRY,
            scenario_id,
            name=snap.name,
            status=snap.status,
            listing_status="",
            follows=follows_label(snap.companion_of) if snap.companion_of else None,
        )
        context["action_url"] = _edit_url(scenario_id)
        context["detail_url"] = reverse("scenarios:detail", args=[scenario_id])
        context["versions_url"] = reverse("scenarios:versions", args=[scenario_id])
    return render(request, EDIT_TEMPLATE, context)


def _rerender(
    request: HttpRequest, snap: EditSnapshot, *, create: bool, media: QueryDict | None = None
) -> HttpResponse:
    """A row button, a media row button (``media``: the posted state it edited) or "Update
    form": the posted state, edited, shown without validating; no Engine call."""
    if media is not None:
        data = retemplated(media)
    else:
        op = parse_op(request.POST.get(OP))
        data = (
            apply_row_op(request.POST, op) if op.kind != OP_REFRESH else retemplated(request.POST)
        )
    data.pop(OP, None)
    editor = _editor(snap, data, create=create)
    editor.quiet()
    sections = _sections(snap, data)
    sections.quiet()
    return _render_edit(request, snap, editor, sections, create=create)


@require_http_methods(["GET", "HEAD", "POST"])
def scenario_new(request: HttpRequest) -> HttpResponse:
    """``/catalog/scenarios/new/``: the empty editor (owner pre-filled with the acting
    principal); Create POSTs ``/catalog/scenarios`` and lands on the edit page."""
    if request.method == "POST":
        return _create(request)
    client = engine_client_for(request)
    snap = EditSnapshot(**_lookups(client, rating=None, profile=None))
    data = editor_data({})
    data[OWNER] = acting_principal(request)
    editor = _editor(snap, data, create=True)
    editor.quiet()
    return _render_edit(request, snap, editor, create=True)


def _create(request: HttpRequest) -> HttpResponse:
    snap = EditSnapshot.unsigned(request.POST.get(SNAPSHOT_FIELD), "")
    if snap is None:  # not from the page: read the lookups again
        client = engine_client_for(request)
        snap = EditSnapshot(**_lookups(client, rating=None, profile=None))
    edited = media_op(request.POST, SCENARIO_SECTIONS)
    if edited is not None:
        return _rerender(request, snap, create=True, media=edited)
    if parse_op(request.POST.get(OP)).edits_rows:
        return _rerender(request, snap, create=True)
    editor = _editor(snap, request.POST, create=True)
    sections = _sections(snap, request.POST)
    if not all([editor.is_valid(), sections.is_valid()]):
        return _render_edit(request, snap, editor, sections, create=True)
    remember_principals(editor.form)
    client = engine_client_for(request)
    body = sections.patch(editor.create_body(), sections.create_parts(sections.changed()))
    try:
        created = client.create_scenario(body, idempotency_key=posted_idempotency_key(request))
    except EngineError as exc:
        log_engine_error(request, exc)
        if exc.http_status in (413, 422) and exc.code != ENTRY_ARCHIVED:
            editor.place_error(exc, extra=sections.place_error(exc))
            return _render_edit(request, snap, editor, sections, create=True)
        return _render_edit(
            request, snap, editor, sections, create=True, engine_error=panel_for(exc)
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _render_edit(
            request, snap, editor, sections, create=True, engine_error=panel_for(exc)
        )
    messages.success(request, f"Created at draft revision {created.draft_revision}")
    return redirect(_edit_url(created.id))


@require_http_methods(["GET", "HEAD", "POST"])
def scenario_edit(request: HttpRequest, scenario_id: str) -> HttpResponse:
    """``/catalog/scenarios/<id>/edit/``: the draft in the editor; Save PATCHes the changed
    groups and stays on the page."""
    if request.method == "POST":
        return _edit_post(request, scenario_id)
    client = engine_client_for(request)
    try:
        snap = _read_snapshot(client, scenario_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context = {"scenario_id": scenario_id, "engine_error": panel_for(exc), "create": False}
        return render(request, EDIT_TEMPLATE, context)
    editor = _editor(snap, editor_data(snap.original), create=False)
    editor.quiet()
    if snap.archived:
        editor.disable()
    return _render_edit(request, snap, editor)


def _posted_int(request: HttpRequest, name: str) -> int | None:
    value = request.POST.get(name, "")
    return int(value) if value.isdecimal() and int(value) >= 1 else None


def _edit_post(request: HttpRequest, scenario_id: str) -> HttpResponse:
    snap = EditSnapshot.unsigned(request.POST.get(SNAPSHOT_FIELD), scenario_id)
    if snap is None:  # not from the page: start again from the Engine's draft
        return redirect(_edit_url(scenario_id))
    edited = media_op(request.POST, SCENARIO_SECTIONS)
    if edited is not None:
        return _rerender(request, snap, create=False, media=edited)
    if parse_op(request.POST.get(OP)).edits_rows:
        return _rerender(request, snap, create=False)
    # An archived entry's controls are disabled; a forced POST goes on, so the Engine's own
    # refusal (422 entry_archived) is what the operator sees.
    editor = _editor(snap, request.POST, create=False)
    sections = _sections(snap, request.POST)
    if not all([editor.is_valid(), sections.is_valid()]):
        return _render_edit(request, snap, editor, sections)
    draft_revision = _posted_int(request, DRAFT_REVISION) or snap.draft_revision
    if draft_revision is None:
        editor.form.add_error(None, NO_REVISION)
        return _render_edit(request, snap, editor, sections)
    # #71: a changed Media section is sent whole; an unchanged one is not sent.
    patch = sections.patch(editor.edit_patch(snap.original), sections.changed())
    if not patch:
        messages.info(request, NO_CHANGES)
        return redirect(_edit_url(scenario_id))
    client = engine_client_for(request)
    try:
        saved = client.patch_scenario(
            scenario_id,
            patch,
            draft_revision=draft_revision,
            idempotency_key=posted_idempotency_key(request),
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        if exc.code == DRAFT_STALE:
            stale = stale_banner(exc, draft_revision=draft_revision)
            return _render_edit(request, snap, editor, sections, stale=stale)
        if exc.http_status in (413, 422) and exc.code != ENTRY_ARCHIVED:
            editor.place_error(exc, extra=sections.place_error(exc))
            return _render_edit(request, snap, editor, sections)
        return _render_edit(request, snap, editor, sections, engine_error=panel_for(exc))
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _render_edit(request, snap, editor, sections, engine_error=panel_for(exc))
    messages.success(request, f"Saved — draft revision {saved.draft_revision}.")
    return redirect(_edit_url(scenario_id))

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
- #74: Validate (``POST <id>/validate/``, a dry run: the HTMX answer is the report swapped into
  ``#report``, without JS a "Validation — <name>" page) and Publish (``POST <id>/publish/``
  from the detail page; "Save & publish" on the edit page saves first, then publishes the
  revision the save produced). A published report is shown after the redirect from its
  operation-log row (``?report=<pk>``). A companion scenario's roles keep their id, kind, fill
  and character (pinned server-side to the signed original; no role is added or removed).
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from console.acting import acting_principal
from console.catalog.cast_table import cast_table, character_ids, rating_ids
from console.catalog.character_labels import CharacterLabel, character_labels
from console.catalog.scenario_report import (
    ShownReport,
    draft_heading,
    report_from_publish_row,
    role_characters,
    shown_report,
    version_heading,
)
from console.confirm import posted_idempotency_key
from console.draft_stale import DRAFT_STALE, stale_banner
from console.engine.choices import (
    CharacterChoices,
    ProfileChoices,
    RatingChoices,
    engine_profile_choices,
    error_text,
    kept_ratings,
    published_character_choices,
    rating_choices,
)
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import CatalogVersion, Scenario, ScenarioCard, ScenarioPublished
from console.engine_access import RecordingEngineClient, engine_client_for
from console.engine_errors import ErrorPanel, is_htmx, log_engine_error, panel_for
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
    RATING_FIELD,
    ScenarioEditor,
    apply_row_op,
    draft_to_write,
    editor_data,
    retemplated,
)
from console.forms.scenario_cast import CastChoices, companion_role_op
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
from console.views.ownership import ownership_context
from console.views.rating_suggestion import suggest_box

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
# #74: validate and publish.
PUBLISH_INVALID = "publish_invalid"
RATING_RETIRED = "rating_retired"
CHARACTER_NOT_PUBLISHED = "character_not_published"
ARCHIVED_PUBLISH = "Archived — unarchive to publish"
RATING_RETIRED_TEXT = "The scenario's content rating is retired — choose another"
ACTION_PUBLISH = "publish"
PUBLISH_KEY = "publish_idempotency_key"
REPORT_PARAM = "report"
NEXT = "next"
FACTS_FIELD = "facts"
FACTS_SALT = "console.scenario_facts"
VERSION = "version"
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


# The companion marker names its character as #73's role characters do.
CompanionLabel = CharacterLabel
companion_labels = character_labels


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
    if back.startswith(LIST_URL) and url_has_allowed_host_and_scheme(
        back, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return back
    return LIST_URL


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


def _rating_names(client: EngineClient, ids: list[str]) -> dict[str, str]:
    """The names of ``ids`` (the card's rating and the roles' eligibility ratings, #73) from
    one ratings read; an id the Engine does not list, or a failed read, has none."""
    if not ids:
        return {}
    choices = rating_choices(client, include_retired=True)
    return {k: v for k, v in choices.choices if k in ids}


def _rating_name(names: dict[str, str], rating_id: str | None) -> Named | None:
    if not rating_id:
        return None
    return Named(rating_id, names.get(rating_id))


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


@dataclass(frozen=True)
class PageFacts:
    """What Validate and Publish post back about the page, signed (#74): the scenario's name,
    its roles' catalog characters (``{catalog_id: role_id}``, a report's entry links) and, for a
    companion, its character and that character's name. Missing or tampered: the id alone."""

    scenario_id: str
    name: str | None = None
    characters: dict[str, str] = field(default_factory=dict)
    companion_of: str | None = None
    character_name: str = ""

    def signed(self) -> str:
        return signing.dumps(asdict(self), salt=FACTS_SALT, compress=True)

    @classmethod
    def unsigned(cls, value: str | None, scenario_id: str) -> PageFacts:
        try:
            raw = signing.loads(value or "", salt=FACTS_SALT)
            if isinstance(raw, dict) and raw.get("scenario_id") == scenario_id:
                return cls(**raw)
        except signing.BadSignature, TypeError:
            pass
        return cls(scenario_id)

    @property
    def character(self) -> str:
        return self.character_name or self.companion_of or ""


@dataclass(frozen=True)
class ValidateBox:
    """The detail page's Validate (#74): its label and what it sends (the draft revision shown,
    or the version shown)."""

    label: str
    draft_revision: int | None = None
    version: int | None = None


@dataclass(frozen=True)
class ScenarioPublishBox:
    """The detail page's "Publish saved draft" (#74): the draft revision it publishes (``None``
    with ``disabled`` when the draft read failed), a note, and a fresh idempotency key."""

    draft_revision: int | None
    disabled: str = ""
    note: str = ""
    key: str = field(default_factory=new_ulid)


def pinned_note(character: str, pin: int | None) -> str:
    """A companion's pin: "pinned to v2", or "not pinned until <character> is published"."""
    if pin is not None:
        return f"pinned to v{pin}"
    return f"not pinned until {character} is published"


@require_safe
def scenario_detail(request: HttpRequest, scenario_id: str) -> HttpResponse:
    return _detail_page(request, scenario_id, request.GET, origin=request.get_full_path())


def _detail_page(
    request: HttpRequest,
    scenario_id: str,
    params: QueryDict,
    *,
    origin: str,
    report_pk: int | None = None,
    refusal: str = "",
    publish_error: ErrorPanel | None = None,
) -> HttpResponse:
    """The detail page for ``params`` (its query); #74's Publish re-renders it with the
    refused publish's report (``report_pk``), its wording (``refusal``) or error panel."""
    client = engine_client_for(request)
    draft = params.get("view") == DRAFT
    version = None if draft else (params.get("version") or None)
    card = params.get("projection") == CARD
    edit_url = reverse("scenarios:edit", args=[scenario_id])
    context: dict[str, Any] = {
        "scenario_id": scenario_id,
        "back_url": safe_back(request),
        "card": card,
        "versions_url": reverse("scenarios:versions", args=[scenario_id]),
        "edit_url": edit_url,
        "card_query": _query(params, projection=CARD),
        "full_query": _query(params, projection=None),
        "draft_query": _query(params, view=DRAFT, version=None),
        "next": origin,
        "refusal": refusal,
        "publish_error": publish_error,
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
    shown_draft = draft or never_published
    # #74: the draft revision Publish names: the draft itself, or the draft read for "differs".
    draft_revision: int | None = None
    draft_error = ""
    if isinstance(entry, Scenario) and shown_draft:
        draft_revision = entry.draft_revision
    elif isinstance(entry, Scenario) and version is None:
        try:
            current_draft = client.get_scenario(scenario_id, draft=True)
        except EngineClientError as exc:
            log_engine_error(request, exc)  # the note is left out; Publish shows the error
            draft_error = error_text(exc)
        else:
            differs = draft_differs(entry, current_draft)
            draft_revision = current_draft.draft_revision
    follows: Follows | None = None
    companion: CompanionLabel | None = None
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
        # #82: the rating suggestion's trigger, at the version shown.
        context["suggestion"] = suggest_box(
            "scenario",
            scenario_id,
            name=entry.public_card.name,
            current_version=entry.current_version,
            shown=None if shown_draft else entry.version,
        )
        # #79: the owner and relations panel, read-only on a companion (its "Follows" line
        # reuses the companion label read above).
        context["ownership"] = ownership_context(
            request,
            client,
            "scenario",
            scenario_id,
            name=entry.public_card.name,
            owner=entry.owner_principal,
            follows=follows,
        )
        if companion is not None:
            context["pinned"] = pinned_note(companion.text, entry.pinned_character_version)
        characters = role_characters(asdict(entry.cast))
        facts = PageFacts(
            scenario_id,
            entry.public_card.name,
            characters,
            entry.companion_of,
            companion.text if companion is not None else "",
        )
        context["facts"] = facts.signed()
        if shown_draft:
            context["validate"] = ValidateBox(
                f"Validate draft (revision {entry.draft_revision})",
                draft_revision=entry.draft_revision,
            )
        elif entry.version is not None:
            context["validate"] = ValidateBox(f"Validate v{entry.version}", version=entry.version)
        if version is None:
            context["publish"] = _publish_box(entry, draft_revision, draft_error, differs)
        if not shown_draft and entry.version is not None:
            # #75: start a story from the version shown (disabled while archived).
            query = urlencode({"scenario": scenario_id, "version": entry.version})
            context["start_url"] = f"{reverse('stories:from-scenario')}?{query}"
        pk = report_pk if report_pk is not None else params.get(REPORT_PARAM)
        row = report_from_publish_row(pk, scenario_id) if pk else None
        if row is not None:
            context["report"] = shown_report(
                row.report,
                scenario_id=scenario_id,
                heading=row.heading,
                characters=characters,
                anchor_base=edit_url,
            )
    context["media"] = shown_media(entry, slot_infos(client, _media_ids(entry)))
    if not isinstance(entry, ScenarioCard):
        rating_id = entry.metadata.content_rating_id
        names = _rating_names(client, [*([rating_id] if rating_id else []), *rating_ids(entry)])
        context["rating"] = _rating_name(names, rating_id)
        context["cast"] = cast_table(entry, character_labels(client, character_ids(entry)), names)
        profile_id = entry.recommendation.engine_profile_id
        context["profile"] = _profile_name(client, profile_id)
        context["profile_url"] = _profile_url(profile_id) if profile_id else ""
    context.update(
        {
            "entry": entry,
            "full": isinstance(entry, Scenario),
            "is_draft": shown_draft,
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


def _publish_box(
    entry: Scenario, draft_revision: int | None, draft_error: str, differs: bool
) -> ScenarioPublishBox:
    """Publish on the latest and draft views: disabled when the draft read failed; on the
    latest view a draft equal to it is noted (publishing it makes an identical version)."""
    if draft_revision is None:
        error = f"The draft could not be read: {draft_error or 'no draft revision'}"
        return ScenarioPublishBox(None, disabled=error)
    note = ""
    if entry.version is not None and not differs:
        note = (
            f"The draft equals v{entry.version}; publishing makes an identical v{entry.version + 1}"
        )
    return ScenarioPublishBox(draft_revision, note=note)


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
    its revision and header facts, and the lookups' options or why they could not be read: the
    content ratings (the main select's, and the roles' eligibility ones from the same read), the
    engine profiles and the published catalog characters (#73's role selects). A create page
    carries one too (``scenario_id`` empty)."""

    scenario_id: str = ""
    draft_revision: int | None = None
    original: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    name: str | None = None
    owner: str = ""
    current_version: int | None = None
    companion_of: str | None = None
    ratings: list[tuple[str, str]] = field(default_factory=list)
    eligibility_ratings: list[tuple[str, str]] = field(default_factory=list)
    ratings_error: str = ""
    profiles: list[tuple[str, str]] = field(default_factory=list)
    profiles_note: str = ""
    characters: list[tuple[str, str]] = field(default_factory=list)
    characters_note: str = ""
    # #71: the Media section as rendered (ids only) and each slot's looked-up metadata.
    media_original: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)
    # #74: a companion's pin and its character's name (one card read; the id when it failed).
    pinned_character_version: int | None = None
    character_name: str = ""

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
            for name in ("ratings", "eligibility_ratings", "profiles", "characters"):
                raw[name] = [(str(k), str(v)) for k, v in raw[name]]
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

    @property
    def character(self) -> str:
        """A companion's character, as the page names it."""
        return self.character_name or self.companion_of or ""

    def facts(self) -> PageFacts:
        return PageFacts(
            self.scenario_id,
            self.name,
            role_characters(self.original.get("cast")),
            self.companion_of,
            self.character_name,
        )

    def cast_choices(self) -> CastChoices:
        return CastChoices(
            CharacterChoices(list(self.characters), note=self.characters_note),
            list(self.eligibility_ratings),
            self.ratings_error,
            companion=self.companion_of is not None,
        )


def _cast_refs(original: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The catalog character ids and eligibility rating ids the draft's roles name."""
    characters: list[str] = []
    ratings: list[str] = []
    for role in (original.get("cast") or {}).get("roles") or []:
        character = role.get("character") or {}
        if character.get("catalog_id"):
            characters.append(character["catalog_id"])
        ratings.extend((role.get("eligibility") or {}).get("content_ratings") or [])
    return characters, ratings


def _lookups(
    client: EngineClient,
    *,
    rating: str | None,
    profile: str | None,
    original: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The editor's lookups: one ratings read (retired included) narrowed for the main select
    and for the roles' eligibility (#73), the engine profiles and the published catalog
    characters, each saved id kept."""
    characters, eligibility = _cast_refs(original or {})
    ratings = rating_choices(client, current=rating, include_retired=True)
    profiles = engine_profile_choices(client, current=profile)
    published = published_character_choices(client, current=characters)
    return {
        "ratings": kept_ratings(ratings, [rating or ""]),
        "eligibility_ratings": kept_ratings(ratings, eligibility),
        "ratings_error": ratings.unavailable_text if ratings.error is not None else "",
        "profiles": list(profiles.choices),
        "profiles_note": profiles.unavailable_text,
        "characters": list(published.choices),
        "characters_note": published.unavailable_text,
    }


def _read_snapshot(client: EngineClient, scenario_id: str) -> EditSnapshot:
    """Read the draft (its errors escape), the two lookups and each attached asset (#71)."""
    draft = client.get_scenario(scenario_id, draft=True)
    original = draft_to_write(draft)
    media_original = scenario_media(draft)
    character_name = ""
    if draft.companion_of:  # #74: one card read names the companion's character
        found = character_labels(client, [draft.companion_of])[draft.companion_of]
        character_name = "" if found.error else found.text
    return EditSnapshot(
        scenario_id=scenario_id,
        draft_revision=draft.draft_revision,
        original=original,
        status=draft.status,
        name=draft.public_card.name,
        owner=draft.owner_principal,
        current_version=draft.current_version,
        companion_of=draft.companion_of,
        media_original=media_original,
        media=infos_to_snapshot(slot_infos(client, media_ids(media_original))),
        pinned_character_version=draft.pinned_character_version,
        character_name=character_name,
        **_lookups(
            client,
            rating=original["metadata"]["content_rating_id"],
            profile=original["recommendation"]["engine_profile_id"],
            original=original,
        ),
    )


def _edit_url(scenario_id: str) -> str:
    return reverse("scenarios:edit", args=[scenario_id])


def _editor(snap: EditSnapshot, data: QueryDict, *, create: bool) -> ScenarioEditor:
    return ScenarioEditor(
        data,
        ratings=snap.rating_choices(),
        profiles=snap.profile_choices(),
        create=create,
        current_rating=snap.rating,
        original=snap.original,
        cast_choices=snap.cast_choices(),
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
    unsaved: bool = False,
    report: ShownReport | None = None,
    refusal: str = "",
) -> HttpResponse:
    """The editor page. ``unsaved``: a re-render of posted input (Validate then notes that it
    checks the saved draft only); ``report``/``refusal``: a refused publish's (#74)."""
    scenario_id = snap.scenario_id
    if sections is None:
        sections = _sections(snap)
    if snap.companion_of and RATING_FIELD in editor.form.fields:
        # A companion's rating is its own, copied at creation (set before the fields render).
        editor.form.fields[RATING_FIELD].help_text = (
            f"Copied from {snap.character} at creation; a different rating is a "
            "rating_looser warning"
        )
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
        "publish_idempotency_key": new_ulid(),
        "unsaved": unsaved,
        "report": report,
        "refusal": refusal,
    }
    if create:
        context["action_url"] = reverse("scenarios:new")
    else:
        # The banner names a companion's character as the snapshot's card read did (#74).
        context["lifecycle"] = lifecycle_for(
            request,
            None,
            LIFECYCLE_ENTRY,
            scenario_id,
            name=snap.name,
            status=snap.status,
            listing_status="",
            follows=(
                follows_label(snap.companion_of, snap.character_name or None)
                if snap.companion_of
                else None
            ),
        )
        context["action_url"] = _edit_url(scenario_id)
        context["detail_url"] = reverse("scenarios:detail", args=[scenario_id])
        context["versions_url"] = reverse("scenarios:versions", args=[scenario_id])
        context["validate_url"] = reverse("scenarios:validate", args=[scenario_id])
        context["next"] = _edit_url(scenario_id)
        context["facts"] = snap.facts().signed()
        context["archived_publish"] = ARCHIVED_PUBLISH if snap.archived else ""
        if snap.companion_of:
            character = snap.character
            context["companion_name"] = character
            context["companion_url"] = reverse(
                "catalog_characters:detail", args=[snap.companion_of]
            )
            context["pinned"] = pinned_note(character, snap.pinned_character_version)
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
        # #74: a companion's roles are neither added nor removed (a tampered button is ignored).
        fixed = snap.companion_of is not None and companion_role_op(op.prefix)
        data = (
            apply_row_op(request.POST, op)
            if op.kind != OP_REFRESH and not fixed
            else retemplated(request.POST)
        )
    data.pop(OP, None)
    editor = _editor(snap, data, create=create)
    editor.quiet()
    sections = _sections(snap, data)
    sections.quiet()
    return _render_edit(request, snap, editor, sections, create=create, unsaved=True)


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
    # #74: after a refused (or successful) publish, its report, read from the operation log.
    report: ShownReport | None = None
    refusal = ""
    row = report_from_publish_row(request.GET.get(REPORT_PARAM), scenario_id)
    if row is not None:
        report = shown_report(
            row.report,
            scenario_id=scenario_id,
            heading=row.heading,
            characters=role_characters(snap.original.get("cast")),
        )
        if row.version is None:
            editor.place_report(row.report, scenario_id)
            refusal = _report_refusal(row.report, snap.facts())
    return _render_edit(request, snap, editor, report=report, refusal=refusal)


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
    # #74: Save (``op=save``) or Save & publish (``action=publish``).
    publish = request.POST.get("action") == ACTION_PUBLISH
    # An archived entry's controls are disabled; a forced POST goes on, so the Engine's own
    # refusal (422 entry_archived) is what the operator sees.
    editor = _editor(snap, request.POST, create=False)
    sections = _sections(snap, request.POST)

    def again(**kwargs: Any) -> HttpResponse:
        return _render_edit(request, snap, editor, sections, unsaved=True, **kwargs)

    if not all([editor.is_valid(), sections.is_valid()]):
        return again()
    draft_revision = _posted_int(request, DRAFT_REVISION) or snap.draft_revision
    if draft_revision is None:
        editor.form.add_error(None, NO_REVISION)
        return again()
    # #71: a changed Media section is sent whole; an unchanged one is not sent.
    patch = sections.patch(editor.edit_patch(snap.original), sections.changed())
    if not patch and not publish:
        messages.info(request, NO_CHANGES)
        return redirect(_edit_url(scenario_id))
    client = engine_client_for(request)
    saved: int | None = None
    if patch:
        try:
            result = client.patch_scenario(
                scenario_id,
                patch,
                draft_revision=draft_revision,
                idempotency_key=posted_idempotency_key(request),
            )
        except EngineError as exc:
            log_engine_error(request, exc)
            if exc.code == DRAFT_STALE:
                return again(stale=stale_banner(exc, draft_revision=draft_revision))
            if exc.http_status in (413, 422) and exc.code != ENTRY_ARCHIVED:
                editor.place_error(exc, extra=sections.place_error(exc))
                return again()
            return again(engine_error=panel_for(exc))
        except EngineClientError as exc:
            log_engine_error(request, exc)
            return again(engine_error=panel_for(exc))
        saved = result.draft_revision
        if not publish:
            messages.success(request, f"Saved — draft revision {saved}.")
            return redirect(_edit_url(scenario_id))
        draft_revision = saved
    return _publish_from_edit(request, client, snap, draft_revision, saved, again)


def _publish_from_edit(
    request: HttpRequest,
    client: RecordingEngineClient,
    snap: EditSnapshot,
    draft_revision: int,
    saved: int | None,
    again: Callable[..., HttpResponse],
) -> HttpResponse:
    """Save & publish's second call: publish the revision the save produced (``saved``; the
    page's own when nothing changed). A refusal after a save says it was saved; the form is
    shown again from a fresh draft read, the report's errors at their fields."""
    scenario_id = snap.scenario_id
    edit_url = _edit_url(scenario_id)
    facts = snap.facts()
    try:
        published = client.publish_scenario(
            scenario_id,
            draft_revision=draft_revision,
            idempotency_key=posted_idempotency_key(request, PUBLISH_KEY),
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        if saved is not None:
            messages.warning(request, f"Saved as draft revision {saved} — not published")
        if exc.code == DRAFT_STALE:
            messages.warning(request, moved_text(exc))
            return redirect(edit_url)
        if exc.code == PUBLISH_INVALID and client.last_operation_id is not None:
            return redirect(_with_params(edit_url, **{REPORT_PARAM: client.last_operation_id}))
        if saved is None and exc.code not in (RATING_RETIRED, ENTRY_ARCHIVED):
            refusal = refusal_text(exc, facts)
            return again(refusal=refusal, engine_error=None if refusal else panel_for(exc))
        return _refused_after_save(request, client, scenario_id, exc, facts)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if saved is None:
            return again(engine_error=panel_for(exc))
        messages.warning(
            request, f"Saved as draft revision {saved} — not published: {error_text(exc)}"
        )
        return redirect(edit_url)
    return _published_redirect(request, client, scenario_id, published, edit_url, facts)


def _refused_after_save(
    request: HttpRequest,
    client: EngineClient,
    scenario_id: str,
    exc: EngineError,
    facts: PageFacts,
) -> HttpResponse:
    """A publish refused for a reason a field can show (``rating_retired`` lands on the rating
    select) or the page names: the editor from a fresh draft read, with the refusal."""
    try:
        fresh = _read_snapshot(client, scenario_id)
    except EngineClientError as read_error:
        log_engine_error(request, read_error)
        messages.error(request, refusal_text(exc, facts) or f"{exc.code}: {exc.message}")
        return redirect(_edit_url(scenario_id))
    editor = _editor(fresh, editor_data(fresh.original), create=False)
    editor.validated_quietly()
    if fresh.archived:
        editor.disable()
    if exc.code == RATING_RETIRED and RATING_FIELD in editor.form.fields:
        editor.form.add_error(RATING_FIELD, RATING_RETIRED_TEXT)
    refusal = refusal_text(exc, facts)
    return _render_edit(
        request,
        fresh,
        editor,
        refusal=refusal,
        engine_error=None if refusal else panel_for(exc),
    )


# --- validate and publish (#74) -------------------------------------------------------------------


def moved_text(error: EngineError) -> str:
    """A 409 ``draft_stale`` on Validate or Publish."""
    current = error.details.get("current_draft_revision")
    now = f" to revision {current}" if isinstance(current, int) else ""
    return f"The draft moved{now} since this page loaded — review and retry"


def _character_first(character_id: str, name: str) -> SafeString:
    url = reverse("catalog_characters:edit", args=[character_id])
    return format_html('Publish <a href="{}">{}</a> first', url, name or character_id)


def _report_refusal(report: Any, facts: PageFacts) -> str:
    """The "Publish <character> first" wording when a companion's report says its character
    has no published version (the Engine reports it inside ``publish_invalid``)."""
    if facts.companion_of and any(e.code == CHARACTER_NOT_PUBLISHED for e in report.errors):
        return _character_first(facts.companion_of, facts.character)
    return ""


def refusal_text(error: EngineError, facts: PageFacts) -> str:
    """The page's wording for a refused publish (``""``: the generic error panel)."""
    if error.code == RATING_RETIRED:
        return RATING_RETIRED_TEXT
    if error.code == ENTRY_ARCHIVED:
        return ARCHIVED_PUBLISH
    if error.code == CHARACTER_NOT_PUBLISHED:
        character_id = error.details.get("character_id")
        if not isinstance(character_id, str) or not character_id:
            character_id = facts.companion_of or ""
        if character_id:
            name = facts.character if character_id == facts.companion_of else character_id
            return _character_first(character_id, name)
    return ""


def _with_params(url: str, **params: object) -> str:
    """``url`` (a local path, maybe with a query) with ``params`` set."""
    parts = urlsplit(url)
    query = QueryDict(parts.query, mutable=True)
    for key, value in params.items():
        query[key] = str(value)
    return urlunsplit(("", "", parts.path, query.urlencode(), ""))


def _origin(request: HttpRequest, scenario_id: str) -> str:
    """The posted ``next`` when it is a local page of this scenario, else its detail page."""
    detail = reverse("scenarios:detail", args=[scenario_id])
    candidate = request.POST.get(NEXT) or ""
    if candidate.startswith(detail) and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return detail


def _published_redirect(
    request: HttpRequest,
    client: RecordingEngineClient,
    scenario_id: str,
    published: ScenarioPublished,
    origin: str,
    facts: PageFacts,
) -> HttpResponse:
    """Flash the version (a companion's pin from a re-read of the latest, dropped when that read
    fails) and go back to the origin with the report (``?report=<pk>``)."""
    report = client.last_operation_id
    text = f"Published v{published.version} (draft revision {published.draft_revision})"
    if facts.companion_of:
        try:
            latest = client.get_scenario(scenario_id)
        except EngineClientError as exc:
            log_engine_error(request, exc)
        else:
            if latest.pinned_character_version is not None:
                text += f"; pinned to {facts.character} v{latest.pinned_character_version}"
    messages.success(request, text)
    if report is None:
        return redirect(origin)
    return redirect(_with_params(origin, **{REPORT_PARAM: report}))


@require_POST
def scenario_publish(request: HttpRequest, scenario_id: str) -> HttpResponse:
    """``/catalog/scenarios/<id>/publish/``: the detail page's Publish of the saved draft (the
    revision the page read). One click, no confirm page; a refusal re-renders the detail page
    with the report or the wording."""
    origin = _origin(request, scenario_id)
    facts = PageFacts.unsigned(request.POST.get(FACTS_FIELD), scenario_id)
    draft_revision = _posted_int(request, DRAFT_REVISION)
    if draft_revision is None:
        messages.error(request, NO_REVISION)
        return redirect(origin)
    client = engine_client_for(request)
    params = QueryDict(urlsplit(origin).query)
    try:
        published = client.publish_scenario(
            scenario_id,
            draft_revision=draft_revision,
            idempotency_key=posted_idempotency_key(request, PUBLISH_KEY),
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        if exc.code == DRAFT_STALE:
            messages.warning(request, moved_text(exc))
            return redirect(origin)
        refusal = refusal_text(exc, facts)
        report_pk: int | None = None
        if exc.code == PUBLISH_INVALID:
            report_pk = client.last_operation_id
            row = report_from_publish_row(report_pk, scenario_id) if report_pk else None
            refusal = _report_refusal(row.report, facts) if row is not None else ""
        return _detail_page(
            request,
            scenario_id,
            params,
            origin=origin,
            report_pk=report_pk,
            refusal=refusal,
            publish_error=None if refusal or report_pk else panel_for(exc),
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _detail_page(
            request, scenario_id, params, origin=origin, publish_error=panel_for(exc)
        )
    return _published_redirect(request, client, scenario_id, published, origin, facts)


VALIDATE_PAGE = "console/catalog/scenarios/validate.html"
VALIDATE_PARTIAL = "console/catalog/scenarios/_validate_result.html"


@require_POST
def scenario_validate(request: HttpRequest, scenario_id: str) -> HttpResponse:
    """``/catalog/scenarios/<id>/validate/``: a dry run of the saved draft (``draft_revision``:
    the one the page shows) or of version ``version``. Nothing is saved, whatever else the form
    posted. Under HTMX the report is the answer (swapped into ``#report``); without JS a page
    "Validation — <name>" with a link back."""
    origin = _origin(request, scenario_id)
    facts = PageFacts.unsigned(request.POST.get(FACTS_FIELD), scenario_id)
    version = _posted_int(request, VERSION)
    draft_revision = None if version is not None else _posted_int(request, DRAFT_REVISION)
    htmx = is_htmx(request)
    edit_url = _edit_url(scenario_id)
    context: dict[str, Any] = {
        "scenario_id": scenario_id,
        "name": label(facts.name, scenario_id),
        "back_url": origin,
    }
    client = engine_client_for(request)
    try:
        result = client.validate_scenario(
            scenario_id, draft_revision=draft_revision, version=version
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        if exc.code == DRAFT_STALE:
            messages.warning(request, moved_text(exc))
            if htmx:
                response = HttpResponse()
                response["HX-Redirect"] = origin
                return response
            return redirect(origin)
        context["engine_error"] = panel_for(exc)
        return render(request, VALIDATE_PARTIAL if htmx else VALIDATE_PAGE, context)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context["engine_error"] = panel_for(exc)
        return render(request, VALIDATE_PARTIAL if htmx else VALIDATE_PAGE, context)
    # Under HTMX on the edit page the editor's anchors are on the page itself.
    same_page = htmx and urlsplit(origin).path == edit_url
    context["shown"] = shown_report(
        result.report,
        scenario_id=scenario_id,
        heading=version_heading(version) if version is not None else draft_heading(draft_revision),
        characters=facts.characters,
        anchor_base="" if same_page else edit_url,
        linked=version is None,
    )
    return render(request, VALIDATE_PARTIAL if htmx else VALIDATE_PAGE, context)

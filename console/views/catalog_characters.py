"""Catalog characters (#65): the list, a character's detail and its published versions.

- ``/catalog/characters/``: ``GET /catalog/characters`` with the GET filter form
  (:class:`console.forms.catalog.CatalogFilterForm`; an invalid filter makes no list call),
  cursor-paged ("Next page" / "First page", both keeping the filters). Two lookups per page,
  each degrading only its own select: the content ratings (:func:`rating_choices`) and
  ``/admin/settings`` (the listing-status values).
- ``/catalog/characters/<id>/``: the latest published version in ``full``; ``?view=draft`` the
  draft, ``?version=N`` version N (``view=draft`` wins); ``?projection=card`` re-reads the same
  view as the public card. A never-published entry (latest read 422 ``not_published``) shows its
  draft. On the full latest view the draft is read too, to say when it differs.
- ``/catalog/characters/<id>/versions/``: the versions, newest first, and the card for the name.

Images go through the media proxy (``/media/<id>/content/``, :mod:`console.views.media_proxy`).
#71: the detail page shows each attached image by slot (looked up per asset, so an id that no
longer reads says so); the editor carries the Images and Reference images sections
(:mod:`console.forms.media_picker`), each sent whole when it changed.
"""

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core import signing
from django.forms.utils import ErrorDict
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from console.acting import acting_principal
from console.catalog.readiness import Readiness, missing_for_publish
from console.confirm import posted_idempotency_key
from console.draft_stale import CHARACTER, stale_banner
from console.engine.choices import RatingChoices, error_text, rating_choices
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import (
    CardImage,
    CatalogCharacter,
    CatalogCharacterCard,
    CatalogVersion,
    CharacterImage,
    CharacterPublished,
    Disposition,
)
from console.engine_access import RecordingEngineClient, engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.entities import label
from console.forms.catalog import CatalogFilterForm
from console.forms.catalog_character import (
    CLEAR_IDENTITY,
    OWNER,
    CharacterForm,
    draft_to_write,
    flatten,
)
from console.forms.media_picker import (
    CHARACTER_SECTIONS,
    REFERENCES,
    MediaSections,
    character_media,
    media_ids,
    media_op,
)
from console.forms.principal import remember_principals
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
from console.reports import LabelledReport, publish_reports
from console.views.catalog_lifecycle import CHARACTER as LIFECYCLE_ENTRY
from console.views.catalog_lifecycle import lifecycle_for

LIST_URL = "/catalog/characters/"
NOT_PUBLISHED = "not_published"
DRAFT = "draft"
CARD = "card"
NEVER_PUBLISHED = "Draft — never published"
# The five axes in the Engine's order (``Dispositions``).
DISPOSITION_AXES = ("threat_response", "dominance", "honesty", "warmth", "caution")
# The content fields "Draft differs from published" compares (#65); everything else (revision,
# timestamps, status, owner, listing status) is ignored.
COMPARED_FIELDS = (
    "name",
    "public_description",
    "images",
    "labels",
    "content_rating",
    "aliases",
    "definition",
    "visual_identity",
)


# --- list -----------------------------------------------------------------------------------------


def _listing_values(request: HttpRequest, client: EngineClient) -> tuple[list[str] | None, str]:
    """``/admin/settings``'s listing-status values, or ``None`` and the error text."""
    try:
        return client.get_deployment_settings().listing_status_values, ""
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None, error_text(exc)


def detail_link(character_id: str, back: str) -> str:
    """The detail URL carrying ``back`` (the list's path and query) for "Back to list"."""
    url = reverse("catalog_characters:detail", args=[character_id])
    return f"{url}?{urlencode({'back': back})}"


def filtered(**params: str) -> str:
    return f"{LIST_URL}?{urlencode(params)}"


@dataclass(frozen=True)
class ListRow:
    item: Any  # CatalogCharacterListItem
    name: str
    url: str
    label_links: list[tuple[str, str]]
    owner_filter: str


@require_safe
def character_list(request: HttpRequest) -> HttpResponse:
    client = engine_client_for(request)
    ratings = rating_choices(client, include_retired=True)
    listing_values, listing_error = _listing_values(request, client)
    form = CatalogFilterForm(request.GET, ratings=ratings, listing_values=listing_values)
    rows: list[ListRow] = []
    next_query = ""
    engine_error: ErrorPanel | None = None
    searching = False
    if form.is_valid():
        query = form.query()
        searching = bool(query["q"])
        try:
            page = client.list_catalog_characters(**query)
        except EngineClientError as exc:
            log_engine_error(request, exc)
            engine_error = panel_for(exc)
        else:
            back = request.get_full_path()
            rows = [
                ListRow(
                    item,
                    label(item.name, item.id),
                    detail_link(item.id, back),
                    [(label, filtered(label=label)) for label in item.labels],
                    filtered(owner=item.owner_principal),
                )
                for item in page.items
            ]
            if page.next_cursor:
                params = request.GET.copy()
                params["cursor"] = page.next_cursor
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
    return render(request, "console/catalog/characters/list.html", context)


# --- detail ---------------------------------------------------------------------------------------


def safe_back(request: HttpRequest) -> str:
    """``?back=`` when it is a local path under the character list, else the list itself."""
    back = request.GET.get("back") or ""
    allowed = url_has_allowed_host_and_scheme(
        back, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return back if allowed and back.startswith(LIST_URL) else LIST_URL


def _query(params: QueryDict, **changes: str | None) -> str:
    """``params`` with ``changes`` applied (``None`` drops a key), as ``?…`` (or ``?`` alone)."""
    query = params.copy()
    for key, value in changes.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return f"?{query.urlencode()}"


def _shown(
    slot: str,
    image: CharacterImage | CardImage,
    infos: dict[str, SlotInfo],
    size: str,
    audience: str | None = None,
) -> ShownMedia:
    detail = image if isinstance(image, CharacterImage) else None
    return ShownMedia(slot, info_for(infos, image.media_id), size, image.alt_text, detail, audience)


def _images(
    entry: CatalogCharacter | CatalogCharacterCard, infos: dict[str, SlotInfo]
) -> list[ShownMedia]:
    """The primary image (up to 320 px) and the gallery (120 px), in order; a card's have only
    the id and alt text (#71)."""
    images = entry.images
    gallery: list[CharacterImage | CardImage] = list(images.gallery)
    shown = [_shown("Primary", images.primary, infos, LARGE)] if images.primary else []
    shown += [
        _shown(f"Gallery {n}", image, infos, SMALL) for n, image in enumerate(gallery, start=1)
    ]
    return shown


def _reference_images(entry: CatalogCharacter, infos: dict[str, SlotInfo]) -> list[ShownMedia]:
    """Each reference image, labelled "Reference — <audience>"."""
    identity = entry.visual_identity
    if identity is None:
        return []
    return [
        _shown(f"Reference — {r.audience or 'no audience'}", r, infos, SMALL, r.audience)
        for r in identity.reference_images
    ]


def _attached_ids(entry: CatalogCharacter | CatalogCharacterCard) -> list[str]:
    images = entry.images
    ids = [images.primary.media_id] if images.primary else []
    ids += [image.media_id for image in images.gallery]
    identity = getattr(entry, "visual_identity", None)
    if identity is not None:
        ids += [r.media_id for r in identity.reference_images]
    return ids


def _dispositions(entry: CatalogCharacter) -> list[tuple[str, Disposition | None]]:
    dispositions = entry.definition.dispositions
    return [
        (axis.replace("_", " "), getattr(dispositions, axis) if dispositions else None)
        for axis in DISPOSITION_AXES
    ]


def _blank(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _normalised(value: object) -> object:
    """``value`` with ``null``, ``""``, ``[]`` and ``{}`` dropped from objects (a missing key
    equals any of them); list order kept."""
    if isinstance(value, dict):
        return {k: _normalised(v) for k, v in value.items() if not _blank(_normalised(v))}
    if isinstance(value, list):
        return [_normalised(v) for v in value]
    return value


def comparable(entry: CatalogCharacter) -> object:
    """The content "Draft differs from published" compares: images by media id, slot and
    audience; the rating by id; the rest as the Engine gives it."""
    raw = asdict(entry)
    data: dict[str, object] = {name: raw[name] for name in COMPARED_FIELDS}
    images = entry.images
    data["images"] = {
        "primary": images.primary.media_id if images.primary else None,
        "gallery": [g.media_id for g in images.gallery],
    }
    data["content_rating"] = entry.content_rating.id if entry.content_rating else None
    if entry.visual_identity is not None:
        identity = dict(raw["visual_identity"])
        identity["reference_images"] = [
            [r.media_id, r.audience] for r in entry.visual_identity.reference_images
        ]
        data["visual_identity"] = identity
    return _normalised(data)


def draft_differs(latest: CatalogCharacter, draft: CatalogCharacter) -> bool:
    return comparable(latest) != comparable(draft)


def _heading(
    entry: CatalogCharacter | CatalogCharacterCard,
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
        if card:  # a card does not say which version is the latest
            latest = ""
        return f"Version {entry.version}{latest}"
    return f"Version {entry.version} (latest)"


def _publish_box(
    request: HttpRequest,
    client: EngineClient,
    entry: CatalogCharacter,
    draft_revision: int | None,
    draft_error: str,
) -> PublishBox:
    """The detail page's Publish form (latest and draft views): one companion draft read for
    the checkbox; disabled when the draft read failed or the entry is archived."""
    if entry.status == ARCHIVED:
        return PublishBox(draft_revision, None, "", disabled=ARCHIVED_PUBLISH)
    if draft_revision is None:
        error = f"The draft could not be read: {draft_error or 'no draft revision'}"
        return PublishBox(None, None, "", error=error, disabled=error)
    companion_revision, companion_error = _companion_revision(request, client, entry.id)
    return PublishBox(draft_revision, companion_revision, companion_error)


@require_safe
def character_detail(request: HttpRequest, character_id: str) -> HttpResponse:
    client = engine_client_for(request)
    params = request.GET
    draft = params.get("view") == DRAFT
    version = None if draft else (params.get("version") or None)
    card = params.get("projection") == CARD
    back = safe_back(request)
    context: dict[str, Any] = {
        "character_id": character_id,
        "back_url": back,
        "card": card,
        "versions_url": reverse("catalog_characters:versions", args=[character_id]),
        "card_query": _query(params, projection=CARD),
        "full_query": _query(params, projection=None),
        "draft_query": _query(params, view=DRAFT, version=None),
    }
    read = client.get_catalog_character_card if card else client.get_catalog_character
    never_published = False
    entry: CatalogCharacter | CatalogCharacterCard
    try:
        try:
            entry = read(character_id, version=version, draft=draft)
        except EngineError as exc:
            latest = not draft and version is None
            if not (latest and exc.http_status == 422 and exc.code == NOT_PUBLISHED):
                raise
            entry = read(character_id, draft=True)
            never_published = True
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context["engine_error"] = panel_for(exc)
        return render(request, "console/catalog/characters/detail.html", context)
    differs = False
    shown_draft = draft or never_published
    # The draft revision Publish (#66) names: the draft itself, or the draft read for "differs".
    draft_revision: int | None = None
    draft_error = ""
    if isinstance(entry, CatalogCharacter) and shown_draft:
        draft_revision = entry.draft_revision
    elif isinstance(entry, CatalogCharacter) and version is None:
        try:
            current_draft = client.get_catalog_character(character_id, draft=True)
        except EngineClientError as exc:
            log_engine_error(request, exc)  # the note is left out; Publish shows the error
            draft_error = error_text(exc)
        else:
            differs = draft_differs(entry, current_draft)
            draft_revision = current_draft.draft_revision
    if isinstance(entry, CatalogCharacter) and version is None:
        context["publish"] = _publish_box(request, client, entry, draft_revision, draft_error)
    context["reports"] = [
        r
        for r in publish_reports(params.get(REPORT_PARAM), entity_id=character_id)
        if r.has_findings
    ]
    context["edit_url"] = reverse("catalog_characters:edit", args=[character_id])
    infos = slot_infos(client, _attached_ids(entry))
    context.update(
        {
            "entry": entry,
            "full": isinstance(entry, CatalogCharacter),
            "is_draft": shown_draft,
            "never_published": never_published,
            "name": label(entry.name, character_id),
            "heading": _heading(
                entry, never_published=never_published, draft=draft, version=version, card=card
            ),
            "differs": differs,
            "images": _images(entry, infos),
        }
    )
    context["is_archived"] = entry.status == ARCHIVED
    if isinstance(entry, CatalogCharacter):
        context["references"] = _reference_images(entry, infos)
        context["dispositions"] = _dispositions(entry)
        context["lifecycle"] = lifecycle_for(
            request,
            client,
            LIFECYCLE_ENTRY,
            character_id,
            name=entry.name,
            status=entry.status,
            listing_status=entry.listing_status,
            has_companion=entry.companion_id is not None,
        )
    return render(request, "console/catalog/characters/detail.html", context)


# --- versions ------------------------------------------------------------------------------------


@require_safe
def character_versions(request: HttpRequest, character_id: str) -> HttpResponse:
    client = engine_client_for(request)
    context: dict[str, Any] = {
        "character_id": character_id,
        "detail_url": reverse("catalog_characters:detail", args=[character_id]),
    }
    try:
        versions: list[CatalogVersion] = client.list_catalog_character_versions(character_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context["engine_error"] = panel_for(exc)
        return render(request, "console/catalog/characters/versions.html", context)
    name: str | None = None
    latest = max((v.version for v in versions), default=None)
    try:
        card_entry = client.get_catalog_character_card(character_id)
    except EngineClientError as exc:
        if not (isinstance(exc, EngineError) and exc.code == NOT_PUBLISHED):
            log_engine_error(request, exc)
            context["card_error"] = panel_for(exc)
    else:
        name = label(card_entry.name, character_id)
        latest = card_entry.version
    context.update(
        {
            "name": name,
            "versions": sorted(versions, key=lambda v: v.version, reverse=True),
            "latest": latest,
        }
    )
    return render(request, "console/catalog/characters/versions.html", context)


# --- create, edit and publish (#66) ---------------------------------------------------------------

EDIT_TEMPLATE = "console/catalog/characters/edit.html"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.catalog_character"
ACTION_SAVE = "save"
ACTION_PUBLISH = "publish"
WITH_COMPANION = "with_companion"
DRAFT_REVISION = "draft_revision"
COMPANION_REVISION = "companion_draft_revision"
PUBLISH_KEY = "publish_idempotency_key"
REPORT_PARAM = "report"
COMPANION_PARAM = "companion"  # ``?companion=1``: keep the box ticked after a redirect
PUBLISH_INVALID = "publish_invalid"
DRAFT_STALE = "draft_stale"
ARCHIVED = "archived"
ARCHIVED_NOTE = "Archived — unarchive to save or publish."
ARCHIVED_PUBLISH = "Archived — unarchive to publish."
NO_CHANGES = "No changes"
NO_REVISION = "The page carried no draft revision — reload it and retry."
NO_COMPANION_REVISION = "The companion's draft revision is unknown — reload the page and retry."


@dataclass(frozen=True)
class EditSnapshot:
    """What the edit form posts back, signed: the draft as rendered (its write shape, the merge
    patch's original), its revision and header facts, the companion draft's revision (or why it
    could not be read) and the rating select's options (or why they could not be read). A
    refused form re-renders from it, so an invalid form makes no Engine call. A create page
    carries one too (``character_id`` empty), for the rating options."""

    character_id: str = ""
    draft_revision: int | None = None
    original: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    name: str | None = None
    owner: str = ""
    current_version: int | None = None
    companion_revision: int | None = None
    companion_error: str = ""
    ratings: list[tuple[str, str]] = field(default_factory=list)
    ratings_error: str = ""
    companion_id: str | None = None  # #69: the archived banner's unarchive link names it
    # #71: the media sections as rendered (ids only) and each slot's looked-up metadata.
    media_original: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)
    # #72: what the saved draft still needs before a publish (``Readiness.to_json``).
    readiness: dict[str, Any] = field(default_factory=dict)

    def signed(self) -> str:
        return signing.dumps(asdict(self), salt=SNAPSHOT_SALT, compress=True)

    @classmethod
    def unsigned(cls, value: str | None, character_id: str) -> EditSnapshot | None:
        try:
            raw = signing.loads(value or "", salt=SNAPSHOT_SALT)
        except signing.BadSignature:
            return None
        if not isinstance(raw, dict) or raw.get("character_id") != character_id:
            return None
        try:
            raw["ratings"] = [(str(k), str(v)) for k, v in raw["ratings"]]
            return cls(**raw)
        except KeyError, TypeError, ValueError:
            return None

    @property
    def archived(self) -> bool:
        return self.status == ARCHIVED

    @property
    def rating(self) -> str | None:
        metadata = self.original.get("metadata") or {}
        value = metadata.get("content_rating_id")
        return value if isinstance(value, str) else None

    def rating_choices(self) -> RatingChoices:
        if self.ratings_error:
            return RatingChoices([], EngineClientError(self.ratings_error))
        return RatingChoices(list(self.ratings))


def _companion_revision(
    request: HttpRequest, client: EngineClient, character_id: str
) -> tuple[int | None, str]:
    """The companion draft's revision, or ``None`` and why it could not be read."""
    try:
        companion = client.get_catalog_character_companion(character_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None, error_text(exc)
    if companion.draft_revision is None:
        return None, "the companion's draft has no revision"
    return companion.draft_revision, ""


def _ratings_snapshot(ratings: RatingChoices) -> dict[str, Any]:
    return {
        "ratings": list(ratings.choices),
        "ratings_error": ratings.unavailable_text if ratings.error is not None else "",
    }


def _read_snapshot(request: HttpRequest, client: EngineClient, character_id: str) -> EditSnapshot:
    """Read the draft (its errors escape), the companion draft, the ratings and each attached
    asset (#71)."""
    draft = client.get_catalog_character(character_id, draft=True)
    original = draft_to_write(draft)
    media_original = character_media(draft)
    infos = slot_infos(client, media_ids(media_original))
    companion_revision, companion_error = _companion_revision(request, client, character_id)
    rating = original["metadata"]["content_rating_id"]
    ratings = rating_choices(client, current=rating)
    readiness = missing_for_publish(
        draft, ratings, require_primary_image=_require_primary_image(request, client)
    )
    return EditSnapshot(
        character_id=character_id,
        draft_revision=draft.draft_revision,
        original=original,
        status=draft.status,
        name=draft.name,
        owner=draft.owner_principal,
        current_version=draft.current_version,
        companion_revision=companion_revision,
        companion_error=companion_error,
        companion_id=draft.companion_id,
        media_original=media_original,
        media=infos_to_snapshot(infos),
        readiness=readiness.to_json(),
        **_ratings_snapshot(ratings),
    )


def _require_primary_image(request: HttpRequest, client: EngineClient) -> bool | None:
    """The deployment's ``require_primary_image`` (#72's readiness banner); ``None`` when the
    read failed (the banner then leaves the primary image out)."""
    try:
        return client.get_deployment_settings().require_primary_image
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


def _edit_url(character_id: str, **params: str) -> str:
    url = reverse("catalog_characters:edit", args=[character_id])
    return f"{url}?{urlencode(params)}" if params else url


def _detail_url(character_id: str, report: int | None = None) -> str:
    url = reverse("catalog_characters:detail", args=[character_id])
    return f"{url}?{urlencode({REPORT_PARAM: report})}" if report is not None else url


def _form(snap: EditSnapshot, *, create: bool, **kwargs: Any) -> CharacterForm:
    form = CharacterForm(
        ratings=snap.rating_choices(), create=create, current_rating=snap.rating, **kwargs
    )
    if snap.archived:
        for each in form.fields.values():
            each.disabled = True
    return form


def _sections(snap: EditSnapshot, data: QueryDict | None = None) -> MediaSections:
    """The Images and Reference images sections (#71), bound to ``data`` (``None``: the draft's,
    shown without validating)."""
    sections = MediaSections(
        data if data is not None else QueryDict(),
        CHARACTER_SECTIONS,
        original=snap.media_original,
        infos=infos_from_snapshot(snap.media),
        disabled=snap.archived,
    )
    if data is None:
        sections.quiet()
    return sections


def _quiet(form: CharacterForm) -> CharacterForm:
    """Show a bound form's state without validating it (a media row button)."""
    quieted: Any = form
    quieted._errors = ErrorDict()
    return form


def _rerender(
    request: HttpRequest, snap: EditSnapshot, data: QueryDict, *, create: bool
) -> HttpResponse:
    """A media row button (``console.forms.media_picker.media_op``): the posted state, edited,
    shown without validating; no Engine call."""
    form = _quiet(_form(snap, create=create, data=data))
    sections = _sections(snap, data)
    sections.quiet()
    with_companion = data.get(WITH_COMPANION) == "on"
    return _render_edit(request, snap, form, sections, create=create, with_companion=with_companion)


def _render_edit(
    request: HttpRequest,
    snap: EditSnapshot,
    form: CharacterForm,
    sections: MediaSections | None = None,
    *,
    create: bool = False,
    with_companion: bool = False,
    stale: str = "",
    engine_error: ErrorPanel | None = None,
    reports: list[LabelledReport] | None = None,
    status: int = 200,
) -> HttpResponse:
    character_id = snap.character_id
    if sections is None:
        sections = _sections(snap)
    context: dict[str, Any] = {
        "create": create,
        "snap": snap,
        "form": form,
        "media_sections": sections,
        "media": sections.by_path(),
        "character_id": character_id,
        "name": label(snap.name, character_id) if not create else "",
        "snapshot": snap.signed(),
        "idempotency_key": new_ulid(),
        "publish_idempotency_key": new_ulid(),
        "with_companion": with_companion and snap.companion_revision is not None,
        "stale": stale,
        "engine_error": engine_error,
        "reports": reports or [],
        "anchors": [*form.anchors(), *sections.anchors()],
        "rating_note": snap.ratings_error,
        "archived_note": ARCHIVED_NOTE if snap.archived else "",
    }
    if not create:
        context["readiness"] = Readiness.from_json(snap.readiness)
        context["lifecycle"] = lifecycle_for(
            request,
            None,  # the edit page reads nothing for its banner
            LIFECYCLE_ENTRY,
            character_id,
            name=snap.name,
            status=snap.status,
            listing_status="",
            has_companion=snap.companion_id is not None,
        )
        context["detail_url"] = reverse("catalog_characters:detail", args=[character_id])
        context["versions_url"] = reverse("catalog_characters:versions", args=[character_id])
        context["action_url"] = reverse("catalog_characters:edit", args=[character_id])
    else:
        context["action_url"] = reverse("catalog_characters:new")
    return render(request, EDIT_TEMPLATE, context, status=status)


@require_http_methods(["GET", "HEAD", "POST"])
def character_new(request: HttpRequest) -> HttpResponse:
    """``/catalog/characters/new/``: the empty form (owner pre-filled with the acting
    principal); Create POSTs ``/catalog/characters`` and lands on the edit page."""
    if request.method == "POST":
        return _create(request)
    client = engine_client_for(request)
    snap = EditSnapshot(**_ratings_snapshot(rating_choices(client)))
    form = _form(snap, create=True, initial={OWNER: acting_principal(request)})
    return _render_edit(request, snap, form, create=True)


def _create(request: HttpRequest) -> HttpResponse:
    snap = EditSnapshot.unsigned(request.POST.get(SNAPSHOT_FIELD), "")
    if snap is None:  # not from the page: read the ratings again
        snap = EditSnapshot(**_ratings_snapshot(rating_choices(engine_client_for(request))))
    edited = media_op(request.POST, CHARACTER_SECTIONS)
    if edited is not None:
        return _rerender(request, snap, edited, create=True)
    form = _form(snap, create=True, data=request.POST)
    sections = _sections(snap, request.POST)
    if not all([form.is_valid(), sections.is_valid()]):
        return _render_edit(request, snap, form, sections, create=True)
    changed = sections.changed()
    if not sections.require_audiences(changed):
        return _render_edit(request, snap, form, sections, create=True)
    remember_principals(form)
    client = engine_client_for(request)
    body = sections.patch(form.create_body(), sections.create_parts(changed))
    try:
        created = client.create_catalog_character(
            body, idempotency_key=posted_idempotency_key(request)
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        if exc.http_status in (413, 422):
            form.place_error(exc, extra=sections.place_error(exc))
            return _render_edit(request, snap, form, sections, create=True)
        return _render_edit(request, snap, form, sections, create=True, engine_error=panel_for(exc))
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _render_edit(request, snap, form, sections, create=True, engine_error=panel_for(exc))
    messages.success(
        request,
        f"Created {label(created.name, created.id)} — draft revision {created.draft_revision}.",
    )
    return redirect(_edit_url(created.id))


@require_http_methods(["GET", "HEAD", "POST"])
def character_edit(request: HttpRequest, character_id: str) -> HttpResponse:
    """``/catalog/characters/<id>/edit/``: the draft in the structured form. Save
    (``action=save``) PATCHes the changed fields; Save & publish (``action=publish``) saves the
    same way (skipped when nothing changed), then publishes the revision that save produced."""
    if request.method == "POST":
        return _edit_post(request, character_id)
    client = engine_client_for(request)
    try:
        snap = _read_snapshot(request, client, character_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context = {"character_id": character_id, "engine_error": panel_for(exc), "create": False}
        return render(request, EDIT_TEMPLATE, context)
    form = _form(snap, create=False, initial=flatten(snap.original))
    sections = _sections(snap)
    reports = publish_reports(request.GET.get(REPORT_PARAM), entity_id=character_id)
    for found in reports:
        if found.entity == CHARACTER:
            form.place_report(found.report)
            sections.place_report(found.report)
    ticked = request.GET.get(COMPANION_PARAM) == "1"
    return _render_edit(request, snap, form, sections, with_companion=ticked, reports=reports)


def _posted_int(request: HttpRequest, name: str) -> int | None:
    value = request.POST.get(name, "")
    return int(value) if value.isdecimal() and int(value) >= 1 else None


def _publish_flash(published: CharacterPublished, with_companion: bool) -> str:
    how = "its draft published" if with_companion else "previous content re-pinned"
    return f"Published v{published.version} (companion v{published.companion.version}, {how})"


def _published_redirect(
    request: HttpRequest,
    client: RecordingEngineClient,
    character_id: str,
    published: CharacterPublished,
    with_companion: bool,
) -> HttpResponse:
    """The success flash, and the detail page (``?report=<pk>`` when either report has
    warnings or checks the deployment cannot run)."""
    messages.success(request, _publish_flash(published, with_companion))
    reports = (published.report, published.companion.report)
    findings = any(r.warnings or r.unavailable for r in reports)
    report = client.last_operation_id if findings else None
    return redirect(_detail_url(character_id, report))


def _edit_post(request: HttpRequest, character_id: str) -> HttpResponse:
    snap = EditSnapshot.unsigned(request.POST.get(SNAPSHOT_FIELD), character_id)
    if snap is None:  # not from the page: start again from the Engine's draft
        return redirect(_edit_url(character_id))
    if snap.archived:
        messages.error(request, ARCHIVED_NOTE)
        return redirect(_edit_url(character_id))
    edited = media_op(request.POST, CHARACTER_SECTIONS)
    if edited is not None:
        return _rerender(request, snap, edited, create=False)
    action = request.POST.get("action", ACTION_SAVE)
    with_companion = action == ACTION_PUBLISH and request.POST.get(WITH_COMPANION) == "on"
    form = _form(snap, create=False, data=request.POST)
    sections = _sections(snap, request.POST)

    def again(**kwargs: Any) -> HttpResponse:
        return _render_edit(request, snap, form, sections, with_companion=with_companion, **kwargs)

    if not all([form.is_valid(), sections.is_valid()]):
        return again()
    draft_revision = _posted_int(request, DRAFT_REVISION) or snap.draft_revision
    companion_revision = _posted_int(request, COMPANION_REVISION) or snap.companion_revision
    if draft_revision is None or (with_companion and companion_revision is None):
        form.add_error(None, NO_REVISION if draft_revision is None else NO_COMPANION_REVISION)
        return again()
    # #71: each changed media section is sent whole; "Clear visual identity" wins.
    cleared = bool(form.cleaned_data.get(CLEAR_IDENTITY))
    changed = sections.changed(skip=[REFERENCES] if cleared else [])
    if not sections.require_audiences(changed):
        return again()
    patch = sections.patch(form.edit_patch(snap.original), changed)
    if not patch and action != ACTION_PUBLISH:
        messages.info(request, NO_CHANGES)
        return redirect(_edit_url(character_id))
    client = engine_client_for(request)
    saved: int | None = None
    if patch:
        try:
            result = client.patch_catalog_character(
                character_id,
                patch,
                draft_revision=draft_revision,
                idempotency_key=posted_idempotency_key(request),
            )
        except EngineError as exc:
            log_engine_error(request, exc)
            if exc.code == DRAFT_STALE:
                return again(stale=stale_banner(exc, draft_revision=draft_revision))
            if exc.http_status in (413, 422) and exc.code != "entry_archived":
                form.place_error(exc, extra=sections.place_error(exc))
                return again()
            return again(engine_error=panel_for(exc))
        except EngineClientError as exc:
            log_engine_error(request, exc)
            return again(engine_error=panel_for(exc))
        saved = result.draft_revision
        if action != ACTION_PUBLISH:
            messages.success(request, f"Saved — draft revision {saved}.")
            return redirect(_edit_url(character_id))
        draft_revision = saved
    not_published = f"Saved as draft revision {saved}, but not published."
    if with_companion and saved is not None:
        # A character save can move the companion's draft: publish with its current revision.
        companion_revision, companion_error = _companion_revision(request, client, character_id)
        if companion_revision is None:
            messages.warning(
                request, f"{not_published} The companion read failed: {companion_error}"
            )
            return redirect(_edit_url(character_id))
    try:
        published = client.publish_catalog_character(
            character_id,
            draft_revision=draft_revision,
            companion_draft_revision=companion_revision if with_companion else None,
            idempotency_key=posted_idempotency_key(request, PUBLISH_KEY),
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        if saved is not None:
            messages.warning(request, not_published)
        if exc.code == PUBLISH_INVALID:
            report = client.last_operation_id
            params = {REPORT_PARAM: str(report)} if report is not None else {}
            if with_companion:
                params[COMPANION_PARAM] = "1"
            return redirect(_edit_url(character_id, **params))
        if exc.code == DRAFT_STALE:
            stale = stale_banner(
                exc,
                draft_revision=draft_revision,
                companion_draft_revision=companion_revision if with_companion else None,
            )
            if saved is None:
                return again(stale=stale)
            messages.warning(request, stale)
            params = {COMPANION_PARAM: "1"} if with_companion else {}
            return redirect(_edit_url(character_id, **params))
        if saved is None:
            return again(engine_error=panel_for(exc))
        messages.error(request, f"Engine error {exc.http_status} {exc.code}: {exc.message}")
        return redirect(_edit_url(character_id))
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if saved is None:
            return again(engine_error=panel_for(exc))
        messages.warning(request, f"{not_published} {error_text(exc)}")
        return redirect(_edit_url(character_id))
    return _published_redirect(request, client, character_id, published, with_companion)


@require_POST
def character_publish(request: HttpRequest, character_id: str) -> HttpResponse:
    """``/catalog/characters/<id>/publish/``: the detail page's plain Publish of the saved
    draft (the revision the page read), with the companion's draft when ticked."""
    detail = _detail_url(character_id)
    draft_revision = _posted_int(request, DRAFT_REVISION)
    with_companion = request.POST.get(WITH_COMPANION) == "on"
    companion_revision = _posted_int(request, COMPANION_REVISION) if with_companion else None
    if draft_revision is None or (with_companion and companion_revision is None):
        messages.error(request, NO_REVISION if draft_revision is None else NO_COMPANION_REVISION)
        return redirect(detail)
    client = engine_client_for(request)
    try:
        published = client.publish_catalog_character(
            character_id,
            draft_revision=draft_revision,
            companion_draft_revision=companion_revision,
            idempotency_key=posted_idempotency_key(request, PUBLISH_KEY),
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        if exc.code == PUBLISH_INVALID:
            report = client.last_operation_id
            params = {REPORT_PARAM: str(report)} if report is not None else {}
            if with_companion:
                params[COMPANION_PARAM] = "1"
            messages.error(request, "Not published: the draft has errors.")
            return redirect(_edit_url(character_id, **params))
        if exc.code == DRAFT_STALE:
            messages.warning(
                request,
                stale_banner(
                    exc,
                    draft_revision=draft_revision,
                    companion_draft_revision=companion_revision,
                ),
            )
            return redirect(detail)
        messages.error(
            request, f"Not published: Engine error {exc.http_status} {exc.code}: {exc.message}"
        )
        return redirect(detail)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        messages.error(request, f"Not published: {error_text(exc)}")
        return redirect(detail)
    return _published_redirect(request, client, character_id, published, with_companion)


@dataclass(frozen=True)
class PublishBox:
    """The detail page's Publish form: the draft revision it publishes (``None`` when the draft
    read failed, with ``error``), the companion's (``None`` disables the checkbox, with
    ``companion_error``), a fresh idempotency key, and why it is disabled."""

    draft_revision: int | None
    companion_revision: int | None
    companion_error: str
    error: str = ""
    disabled: str = ""
    key: str = field(default_factory=new_ulid)

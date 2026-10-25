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
"""

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_safe

from console.engine.choices import error_text, rating_choices
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import (
    CardImage,
    CatalogCharacter,
    CatalogCharacterCard,
    CatalogVersion,
    CharacterImage,
    Disposition,
)
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.entities import label
from console.forms.catalog import CatalogFilterForm

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


@dataclass(frozen=True)
class Shown:
    """One image as the page shows it: the full detail when the read had it (``detail``),
    else only the card's id and alt text."""

    slot: str
    media_id: str
    alt_text: str | None
    detail: CharacterImage | None = None
    audience: str | None = None

    @property
    def is_video(self) -> bool:
        mime = self.detail.mime_type if self.detail is not None else None
        return bool(mime and mime.startswith("video/"))

    @property
    def restricted(self) -> bool:
        return self.audience == "restricted"


def _images(entry: CatalogCharacter | CatalogCharacterCard) -> list[Shown]:
    """The primary image and the gallery, in order (a card's have no detail)."""
    primary: list[CharacterImage | CardImage] = (
        [entry.images.primary] if entry.images.primary is not None else []
    )
    gallery: list[CharacterImage | CardImage] = list(entry.images.gallery)
    slots = [("primary", image) for image in primary]
    slots += [(f"gallery {n}", image) for n, image in enumerate(gallery, start=1)]
    return [
        Shown(slot, image.media_id, image.alt_text, image)
        if isinstance(image, CharacterImage)
        else Shown(slot, image.media_id, image.alt_text)
        for slot, image in slots
    ]


def _reference_images(entry: CatalogCharacter) -> list[Shown]:
    identity = entry.visual_identity
    if identity is None:
        return []
    return [
        Shown(f"reference {n}", r.media_id, r.alt_text, r, r.audience)
        for n, r in enumerate(identity.reference_images, start=1)
    ]


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
    if isinstance(entry, CatalogCharacter) and not (draft or version or never_published):
        try:
            differs = draft_differs(entry, client.get_catalog_character(character_id, draft=True))
        except EngineClientError as exc:
            log_engine_error(request, exc)  # the note is left out; nothing else changes
    shown_draft = draft or never_published
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
            "images": _images(entry),
        }
    )
    if isinstance(entry, CatalogCharacter):
        context["references"] = _reference_images(entry)
        context["dispositions"] = _dispositions(entry)
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

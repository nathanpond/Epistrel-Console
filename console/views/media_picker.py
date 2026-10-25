"""The media picker and the slot-row partial the catalog editors' media sections use (#71).

- ``/media/picker/?field=<slot>&kind=<image|video>&index=<n>&cursor=&picker_owner=``: a panel of
  live media from ``GET /media`` (``limit=50``, newest first; every owner unless the owner filter
  is set), with Next/First paging, an owner filter, Refresh, Close and "Upload new…" (#70's upload
  in a new tab, so unsaved edits survive). Each tile shows the thumbnail (a video's poster, else
  "[video]"), the id and the depicted subjects as ``kind`` + the id's last 6 characters; an id
  already in the target list (the list's inputs travel with the request) is marked "in use".
  Picking a tile swaps the slot row for the partial below with that id, which drops the panel.
  ``close=1`` answers an empty panel with no Engine call.
- ``/media/slot-row/?field=<slot>&index=<n>&kind=<image|video>&media_id=<id>``: one slot row,
  with the asset looked up (``GET /media/{id}``) for its thumbnail and metadata; also loaded when
  the id input changes. The row's own inputs (audience, caption, order, removal) travel with the
  request and are kept. A malformed id is shown with its error and no Engine call.

Both validate ``field`` (``^[a-z_]+(__[a-z_]+)*$`` and one of the editors' slot fields),
``index`` (an integer below 20, list slots only) and ``kind`` (the slot's own): anything else is a
400 with no Engine call.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from console.engine.client import EngineClientError
from console.engine.models import MediaAsset
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.forms.media_picker import (
    DELETE,
    ORDER,
    MediaSlotField,
    SlotRow,
    audience_choices,
    form_field_name,
    known_fields,
)
from console.forms.principal import clean_principal
from console.ids import is_ulid
from console.media_slots import SlotInfo, slot_infos
from console.reports import field_anchor
from console.views.media import content_url

PAGE = 50
INDEX_MAX = 20  # a row index the partials accept: below this


@dataclass(frozen=True)
class Slot:
    """A validated ``field``/``index``/``kind``."""

    field: str
    index: int | None
    kind: str

    @property
    def path(self) -> str:
        path = self.field.replace("__", ".")
        return path if self.index is None else f"{path}[{self.index}]"

    @property
    def anchor(self) -> str:
        return field_anchor(self.path)

    def name(self, part: str) -> str:
        return self.field if self.index is None else f"{self.field}-{self.index}-{part}"


def _slot(params: QueryDict) -> Slot | None:
    """The slot the request names, or ``None`` when anything is malformed."""
    name = params.get("field", "")
    kind = params.get("kind", "")
    if not form_field_name(name):
        return None
    known = known_fields().get(name)
    if known is None or kind != known[0]:
        return None
    _, limit = known
    raw = params.get("index")
    if limit is None:
        return Slot(name, None, kind) if raw in (None, "", "0") else None
    if raw is None or not raw.isdecimal() or int(raw) >= INDEX_MAX:
        return None
    return Slot(name, int(raw), kind)


def _bad_request() -> HttpResponse:
    return HttpResponseBadRequest("Not a media slot: field, index and kind must name one.")


# --- the picker -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    asset: MediaAsset
    thumbnail: str | None  # the proxied image or poster; ``None``: "[video]"
    subjects: list[tuple[str, str]]  # (kind, …last 6)
    in_use: bool
    pick_url: str


def _in_use(params: QueryDict, slot: Slot) -> set[str]:
    """The ids the target list (or single slot) holds, from the inputs sent along."""
    if slot.index is None:
        value = (params.get(slot.field) or "").strip().upper()
        return {value} if value else set()
    start, end = f"{slot.field}-", "-media_id"
    return {
        (params.get(key) or "").strip().upper()
        for key in params
        if key.startswith(start) and key.endswith(end) and params.get(key)
    }


def _thumbnail(asset: MediaAsset) -> str | None:
    if asset.kind == "image":
        return content_url(asset.id, image=True)
    if asset.poster_media_id:
        return content_url(asset.poster_media_id, image=True)
    return None


def _pick_url(slot: Slot, media_id: str) -> str:
    params: dict[str, str] = {"field": slot.field, "kind": slot.kind, "media_id": media_id}
    if slot.index is not None:
        params["index"] = str(slot.index)
    return f"{reverse('media:slot-row')}?{urlencode(params)}"


def _picker_query(slot: Slot, **extra: str) -> str:
    params: dict[str, str] = {"field": slot.field, "kind": slot.kind}
    if slot.index is not None:
        params["index"] = str(slot.index)
    params.update({k: v for k, v in extra.items() if v})
    return f"{reverse('media:picker')}?{urlencode(params)}"


@require_safe
def media_picker(request: HttpRequest) -> HttpResponse:
    params = request.GET
    if params.get("close") == "1":
        return HttpResponse("")
    slot = _slot(params)
    if slot is None:
        return _bad_request()
    owner = (params.get("picker_owner") or "").strip(" \t")
    owner_error = ""
    if owner:
        try:
            owner = clean_principal(owner, allow_system=True)
        except ValidationError as exc:
            owner_error = " ".join(exc.messages)
    tiles: list[Tile] = []
    next_url = ""
    engine_error: ErrorPanel | None = None
    if not owner_error:
        client = engine_client_for(request)
        try:
            items, next_cursor = client.list_media(
                kind=slot.kind, owner=owner or None, cursor=params.get("cursor") or None, limit=PAGE
            )
        except EngineClientError as exc:
            log_engine_error(request, exc)
            engine_error = panel_for(exc)
        else:
            used = _in_use(params, slot)
            tiles = [
                Tile(
                    asset,
                    _thumbnail(asset),
                    [(s.kind, f"…{s.id[-6:]}") for s in asset.depicted_subjects],
                    asset.id in used,
                    _pick_url(slot, asset.id),
                )
                for asset in items
            ]
            if next_cursor:
                next_url = _picker_query(slot, cursor=next_cursor, picker_owner=owner)
    context: dict[str, Any] = {
        "slot": slot,
        "tiles": tiles,
        "owner": owner,
        "owner_error": owner_error,
        "engine_error": engine_error,
        "next_url": next_url,
        "first_url": _picker_query(slot, picker_owner=owner) if params.get("cursor") else "",
        "refresh_url": _picker_query(slot),
        "close_url": f"{reverse('media:picker')}?close=1",
        "upload_url": f"{reverse('media:upload')}?{urlencode({'kind': slot.kind})}",
        "include": f"#{slot.anchor}" if slot.index is None else f"#{_list_anchor(slot)}",
    }
    return render(request, "console/media/_picker.html", context)


def _list_anchor(slot: Slot) -> str:
    return field_anchor(slot.field.replace("__", "."))


# --- the slot row ---------------------------------------------------------------------------------


@require_safe
def media_slot_row(request: HttpRequest) -> HttpResponse:
    params = request.GET
    slot = _slot(params)
    if slot is None:
        return _bad_request()
    raw = params.get("media_id")
    if raw is None:
        raw = params.get(slot.name("media_id"), "")
    errors: tuple[str, ...] = ()
    info: SlotInfo | None = None
    try:
        media_id = MediaSlotField().clean(raw)
    except ValidationError as exc:
        media_id = raw.strip().upper()
        errors = tuple(exc.messages)
    if media_id and not errors and is_ulid(media_id):
        info = slot_infos(engine_client_for(request), [media_id])[media_id]
    audience: str | None = None
    caption: str | None = None
    if slot.field == "visual_identity__reference_images":
        audience = (params.get(slot.name("audience")) or "").strip()
    if slot.kind == "video" and slot.index is not None:
        caption = params.get(slot.name("caption")) or ""
    row = SlotRow(
        field=slot.field,
        index=slot.index,
        kind=slot.kind,
        path=slot.path,
        media_id=media_id,
        info=info,
        audience=audience,
        audience_choices=audience_choices(audience) if audience is not None else (),
        caption=caption,
        order=(params.get(slot.name(ORDER)) or "").strip()
        or (str(slot.index + 1) if slot.index is not None else ""),
        deleted=bool(params.get(slot.name(DELETE))),
        errors=errors,
        can_up=slot.index is not None and slot.index > 0,
        can_down=slot.index is not None,
    )
    return render(request, "console/media/_slot_row.html", {"row": row})

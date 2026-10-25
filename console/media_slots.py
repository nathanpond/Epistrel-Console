"""Media attached to catalog entries (#71): each slot's metadata, and how pages show a slot.

The detail and edit pages look up every attached id with #70's per-asset read (``GET /media/{id}``
on the fan-out pool, one de-duplicated batch per render: :func:`slot_infos`). A failed read shows
"<id> unavailable (<status> <code>)", never a broken image alone; a retired asset shows normally
with a "retired" tag. The edit page carries what it looked up in its signed snapshot
(:meth:`SlotInfo.as_dict`), so a refused form or a row button re-renders with no Engine call; an
id typed since then shows without metadata until the slot-row partial looks it up.

Every byte goes through the proxy (#64 convention 11): ``/media/<id>/content/`` (``?img=1`` for an
``<img>``, so an Engine error answers an SVG naming the id).
"""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

from django.urls import reverse

from console.engine.choices import MediaLookup, media_lookups
from console.engine.client import EngineClient
from console.engine.models import CharacterImage
from console.views.media import content_url, duration

TIMES = "\N{MULTIPLICATION SIGN}"


@dataclass(frozen=True)
class SlotInfo:
    """What a page knows of one attached asset: its metadata (``state`` set) or the read's
    ``error``; neither when it was not looked up (an id typed since the page was read)."""

    media_id: str
    state: str | None = None
    error: str | None = None
    kind: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    alt_text: str | None = None
    caption: str | None = None
    poster_media_id: str | None = None

    @classmethod
    def from_lookup(cls, lookup: MediaLookup) -> SlotInfo:
        asset = lookup.asset
        if asset is None:
            return cls(lookup.media_id, error=lookup.error or "unreadable")
        return cls(
            asset.id,
            state=asset.state,
            kind=asset.kind,
            mime_type=asset.mime_type,
            width=asset.width,
            height=asset.height,
            duration_ms=asset.duration_ms,
            alt_text=asset.alt_text,
            caption=asset.caption,
            poster_media_id=asset.poster_media_id,
        )

    @classmethod
    def from_dict(cls, raw: object) -> SlotInfo | None:
        """The snapshot's copy back (``None`` for anything malformed)."""
        if not isinstance(raw, Mapping) or not isinstance(raw.get("media_id"), str):
            return None
        names = {f.name for f in fields(cls)}
        try:
            return cls(**{k: v for k, v in raw.items() if k in names})
        except TypeError:
            return None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def known(self) -> bool:
        return self.state is not None or self.error is not None

    @property
    def unavailable(self) -> bool:
        return self.error is not None

    @property
    def unavailable_text(self) -> str:
        return f"unavailable ({self.error})" if self.error is not None else ""

    @property
    def retired(self) -> bool:
        return self.state == "retired"

    @property
    def is_video(self) -> bool:
        return self.kind == "video" or bool(self.mime_type and self.mime_type.startswith("video/"))

    @property
    def dimensions(self) -> str:
        if self.width is None or self.height is None:
            return ""
        return f"{self.width} {TIMES} {self.height} px"

    @property
    def metadata(self) -> str:
        """``image/png · 512 x 768 px`` (a multiplication sign, not x; a video adds its
        duration ``m:ss.s``); "" when unknown."""
        parts = [p for p in (self.mime_type, self.dimensions) if p]
        if self.is_video and self.duration_ms is not None:
            parts.append(duration(self.duration_ms))
        return " · ".join(parts)

    @property
    def content_url(self) -> str:
        return content_url(self.media_id)

    @property
    def image_url(self) -> str:
        return content_url(self.media_id, image=True)

    @property
    def poster_url(self) -> str | None:
        return content_url(self.poster_media_id, image=True) if self.poster_media_id else None

    @property
    def detail_url(self) -> str:
        return reverse("media:detail", args=[self.media_id])


def slot_infos(client: EngineClient, media_ids: Iterable[str]) -> dict[str, SlotInfo]:
    """One ``GET /media/{id}`` per distinct id on the fan-out pool; never raises."""
    ids = [m for m in dict.fromkeys(media_ids) if m]
    return {m: SlotInfo.from_lookup(lookup) for m, lookup in media_lookups(client, ids).items()}


def infos_from_snapshot(raw: object) -> dict[str, SlotInfo]:
    """The ``{id: SlotInfo}`` an edit snapshot carries (malformed entries dropped)."""
    if not isinstance(raw, Mapping):
        return {}
    found = (SlotInfo.from_dict(v) for v in raw.values())
    return {info.media_id: info for info in found if info is not None}


def infos_to_snapshot(infos: Mapping[str, SlotInfo]) -> dict[str, dict[str, Any]]:
    return {media_id: info.as_dict() for media_id, info in infos.items()}


# --- detail pages ---------------------------------------------------------------------------------

LARGE = "large"  # a primary image or cover: at most 320 px wide
SMALL = "small"  # a gallery or reference image: 120 px


@dataclass(frozen=True)
class ShownMedia:
    """One attached asset as a detail page shows it: its slot label, the looked-up ``info``, the
    alt text the read gave (``None``: the asset's), the projection's per-image ``detail`` (a
    character's ``full`` read), the reference image's ``audience`` and a video's ``caption``."""

    slot: str
    info: SlotInfo
    size: str = SMALL
    alt_text: str | None = None
    detail: CharacterImage | None = None
    audience: str | None = None
    caption: str | None = None

    @property
    def media_id(self) -> str:
        return self.info.media_id

    @property
    def alt(self) -> str:
        """The ``<img alt>``: the response's alt text, else "" (a scenario's full read has
        none, so its builder passes the looked-up asset's)."""
        return self.alt_text or ""

    @property
    def is_video(self) -> bool:
        mime = self.detail.mime_type if self.detail is not None else None
        return self.info.is_video or bool(mime and mime.startswith("video/"))

    @property
    def restricted(self) -> bool:
        return self.audience == "restricted"

    @property
    def shown_caption(self) -> str | None:
        """A video's caption: the entry's, else the asset's (from the lookup)."""
        return self.caption or self.info.caption


def info_for(infos: Mapping[str, SlotInfo], media_id: str) -> SlotInfo:
    return infos.get(media_id) or SlotInfo(media_id)

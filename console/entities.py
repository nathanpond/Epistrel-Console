"""The entity registry (#64, M3 convention 8): every Engine entity type the Console names.

Keys are the Engine's ``EntityType`` strings (``story``, ``character`` = a catalog character,
``scenario``, ``persona``, ``media``, ``engine_profile``). The operation log's ``entity_type``
column and ``@operation(entity=…)`` use the same strings. Each type carries:

- ``engine_root``: its collection path at the Engine (``/catalog/characters``);
- ``url_name``: the Console page of one entity (``<namespace>:detail``, one argument: the id);
- ``display_name``: how the Console names the type ("Character");
- ``lookup_key``: the Engine's plural key for it (``characters``), as lookups and offboarding
  reports name the type.

Use :func:`entity_url` / :func:`entity_link` (and the ``entity_link`` template filter in
``console.templatetags.entities``) to link an entity: a type whose page does not exist yet, or a
type the registry does not know, renders as the id in ``<code>``, unlinked. :func:`unnamed_label`
is convention 17's "(unnamed) …<last 6 id chars>".
"""

from dataclasses import dataclass
from typing import Literal, get_args

from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

EntityType = Literal["story", "character", "scenario", "persona", "media", "engine_profile"]
ENTITY_TYPES: tuple[EntityType, ...] = get_args(EntityType)


@dataclass(frozen=True)
class EntityInfo:
    type: EntityType
    engine_root: str
    url_name: str
    display_name: str
    lookup_key: str


REGISTRY: dict[str, EntityInfo] = {
    info.type: info
    for info in (
        EntityInfo("story", "/stories", "stories:detail", "Story", "stories"),
        EntityInfo(
            "character",
            "/catalog/characters",
            "catalog_characters:detail",
            "Character",
            "characters",
        ),
        EntityInfo("scenario", "/catalog/scenarios", "scenarios:detail", "Scenario", "scenarios"),
        EntityInfo("persona", "/personas", "personas:detail", "Persona", "personas"),
        EntityInfo("media", "/media", "media:detail", "Media", "media"),
        EntityInfo(
            "engine_profile",
            "/engine-profiles",
            "profiles:detail",
            "Engine profile",
            "engine_profiles",
        ),
    )
}
BY_LOOKUP_KEY: dict[str, EntityInfo] = {info.lookup_key: info for info in REGISTRY.values()}


def entity_info(entity_type: str | None) -> EntityInfo | None:
    """The registry entry for ``entity_type``, or ``None`` for an unknown (or absent) type."""
    return REGISTRY.get(entity_type) if entity_type else None


def display_name(entity_type: str | None) -> str:
    """The type's display name; an unknown type is shown as given ("—" when absent)."""
    info = entity_info(entity_type)
    if info is not None:
        return info.display_name
    return entity_type or "—"


def entity_url(entity_type: str | None, entity_id: str | None) -> str | None:
    """The Console page of one entity, or ``None`` when the type is unknown, the id is blank or
    the type's page does not exist yet (its URL name does not reverse)."""
    info = entity_info(entity_type)
    if info is None or not entity_id:
        return None
    try:
        return reverse(info.url_name, args=[entity_id])
    except NoReverseMatch:
        return None


def entity_link(entity_type: str | None, entity_id: str | None) -> SafeString:
    """``<a href="…"><code>id</code></a>``, or ``<code>id</code>`` when there is no page for it
    (:func:`entity_url`); "—" for a blank id."""
    if not entity_id:
        return format_html("{}", "—")
    url = entity_url(entity_type, entity_id)
    if url is None:
        return format_html("<code>{}</code>", entity_id)
    return format_html('<a href="{}"><code>{}</code></a>', url, entity_id)


def unnamed_label(entity_id: str) -> str:
    """Convention 17: how an entity without a name is shown, "(unnamed) …<last 6 id chars>"."""
    return f"(unnamed) …{entity_id[-6:]}"


def label(name: str | None, entity_id: str) -> str:
    """An entity's name, else :func:`unnamed_label`."""
    return name if name else unnamed_label(entity_id)

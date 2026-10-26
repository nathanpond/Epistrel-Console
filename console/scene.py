"""The Scene tab's and the entity page's rows (#58): a :class:`~console.engine.models.SceneBody`
and an :class:`~console.engine.models.EntityProfileBody` as the templates show them.

**Links.** Every entity (the location, the present, a relationship's other end, the places a
placement is within, its container, an inventory item) links ``inspect:entity`` carrying
``as_of`` and ``viewpoint``: the *response's* resolved turn and viewpoint (``turn`` for the scene,
``as_of`` for a profile), so a link never follows the head by omission. An id the ULID route
can't take renders unlinked. A fact id links #138's lineage view (:func:`console.ledger.
fact_link`); a predicate its row on the Predicates tab (:func:`console.predicates.row_url`).

**Names.** Names and kinds come from the response alone (#135 convention 2: no extra reads). A
name of "" (the Engine's fallback for an id with no entity row) shows "(unnamed)"; a ``kind`` is
a free string, humanised ("place" → "Place") with the raw value in ``title``.

**Viewpoints.** ``source_fact_id`` is shown only when the response's ``viewpoint`` is
``omniscient`` (the only viewpoint the Engine gives it to); a character viewpoint gets no Source
fact column at all.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode

from django.urls import NoReverseMatch, reverse

from console.canon import OPEN
from console.cast import OMNISCIENT, Character, character_labels, character_of
from console.engine.models import (
    EntityBody,
    EntityProfileBody,
    InventoryBody,
    PlacementBody,
    RelationshipBody,
    SceneBody,
)
from console.ids import is_ulid
from console.ledger import FactLink, fact_link
from console.predicates import row_url

ENTITY_URL = "inspect:entity"
SCENE_URL = "inspect:scene"
OMNISCIENT_LABEL = "Omniscient"
UNNAMED = "(unnamed)"
EMPTY = "—"
ARROWS: Mapping[str, str] = {"out": "→", "in": "←"}
M4_CLOCK = "In M4 every turn's story-time is the story's start (the world clock arrives in M6)."
NONE_VISIBLE = "None visible to this viewpoint"
LOCATION_UNKNOWN = "Location unknown to this viewpoint"
ENTITY_NOT_FOUND = "That entity isn't one of this story's"


def link_query(as_of: int | str | None, viewpoint: str | None) -> str:
    """``as_of=…&viewpoint=…``, each only when given (``""`` when neither is)."""
    params: dict[str, str] = {}
    if as_of is not None and as_of != "":
        params["as_of"] = str(as_of)
    if viewpoint:
        params["viewpoint"] = viewpoint
    return urlencode(params)


def entity_url(story_id: str, entity_id: str, as_of: int, viewpoint: str) -> str | None:
    """The entity's page at ``as_of`` and ``viewpoint``; ``None`` for an id the route can't
    take (not a ULID)."""
    try:
        url = reverse(ENTITY_URL, args=[story_id, entity_id])
    except NoReverseMatch:
        return None
    return f"{url}?{link_query(as_of, viewpoint)}"


def scene_url(story_id: str, as_of: int | str | None, viewpoint: str | None) -> str:
    """The Scene tab, carrying ``as_of`` and ``viewpoint`` when given (the entity page's
    "← Scene")."""
    url = reverse(SCENE_URL, args=[story_id])
    query = link_query(as_of, viewpoint)
    return f"{url}?{query}" if query else url


def kind_label(kind: str) -> str:
    """Convention 5: ``place`` → ``Place``, ``living_thing`` → ``Living thing``; "—" for ""."""
    shown = kind.replace("_", " ").strip()
    return shown[:1].upper() + shown[1:] if shown else EMPTY


def viewpoint_header(viewpoint: str, cast: Sequence[Character] | None) -> str:
    """The header's "as …": the cast member's label (no player suffix), "Omniscient", or the
    raw value (a character the cast doesn't name, or the cast unknown)."""
    if viewpoint == OMNISCIENT:
        return OMNISCIENT_LABEL
    character = character_of(viewpoint)
    if character is not None and cast is not None:
        return character_labels(cast).get(character, viewpoint)
    return viewpoint


@dataclass(frozen=True)
class EntityLink:
    """An entity as shown: its name (``""`` → "(unnamed)"), kind and the link to its page."""

    id: str
    name: str
    kind: str
    kind_label: str
    url: str | None


def entity_link(story_id: str, entity: EntityBody, as_of: int, viewpoint: str) -> EntityLink:
    return EntityLink(
        id=entity.entity_id,
        name=entity.name,
        kind=entity.kind,
        kind_label=kind_label(entity.kind),
        url=entity_url(story_id, entity.entity_id, as_of, viewpoint),
    )


# --- the scene -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneView:
    header: str  # "Turn 3 · v2 · as Mara"
    location: EntityLink | None
    present: list[EntityLink]


def scene_header(turn: int, variant: int | None, label: str) -> str:
    """ "Turn N · vM · as <label>", the variant left out when ``None`` (turn 0)."""
    parts = [f"Turn {turn}"]
    if variant is not None:
        parts.append(f"v{variant}")
    parts.append(f"as {label}")
    return " · ".join(parts)


def scene_view(story_id: str, body: SceneBody, cast: Sequence[Character] | None) -> SceneView:
    """The scene's header, location and present rows, in the Engine's order (the location
    entity is never filtered out of ``present``)."""

    def link(entity: EntityBody) -> EntityLink:
        return entity_link(story_id, entity, body.turn, body.viewpoint)

    return SceneView(
        header=scene_header(body.turn, body.variant, viewpoint_header(body.viewpoint, cast)),
        location=link(body.location) if body.location is not None else None,
        present=[link(entity) for entity in body.present],
    )


# --- an entity's profile ------------------------------------------------------------------------


@dataclass(frozen=True)
class QualifierCell:
    """One ``key=value`` of a relationship's qualifiers. ``kind`` is ``string`` (in ``<code>``),
    ``id`` (convention 2's short id, unlinked) or ``json`` (numbers, booleans, null, lists and
    objects, as compact JSON)."""

    key: str
    kind: str
    text: str


def qualifier_cells(qualifiers: Mapping[str, Any]) -> list[QualifierCell]:
    """The qualifiers in the Engine's key order."""
    cells = []
    for key, value in qualifiers.items():
        if isinstance(value, str):
            cells.append(QualifierCell(key, "id" if is_ulid(value) else "string", value))
        else:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
            cells.append(QualifierCell(key, "json", text))
    return cells


def valid_text(valid_from: int | None, valid_to: int | None) -> str:
    """Convention 4: ``[0, …) min``."""
    start = OPEN if valid_from is None else str(valid_from)
    end = OPEN if valid_to is None else str(valid_to)
    return f"[{start}, {end}) min"


@dataclass(frozen=True)
class RelationshipRow:
    arrow: str | None  # "→" / "←"; ``None`` for a direction the Console doesn't know
    direction: str
    predicate: str
    predicate_id: str
    predicate_url: str | None
    other: EntityLink
    qualifiers: list[QualifierCell]
    valid: str
    source: FactLink | None


@dataclass(frozen=True)
class PlacementView:
    """``location`` with ``within`` (nearest first), or ``container``; both ``None`` (which the
    Engine never sends) reads "—"; both set shows the location form, then the container."""

    location: EntityLink | None
    within: list[EntityLink]
    container: EntityLink | None
    valid: str
    source: FactLink | None


@dataclass(frozen=True)
class InventoryRow:
    item: EntityLink
    source: FactLink | None


@dataclass(frozen=True)
class ProfileView:
    """``_entity_profile.html``'s parts. ``omniscient``: show the Source fact column and cells."""

    omniscient: bool
    relationships: list[RelationshipRow]
    placement: PlacementView | None
    inventory: list[InventoryRow]


def _predicate_url(story_id: str, predicate_id: str) -> str | None:
    try:
        return row_url(story_id, predicate_id)
    except NoReverseMatch:
        return None


def profile_view(
    story_id: str, profile: EntityProfileBody, link_as_of: int, link_viewpoint: str
) -> ProfileView:
    """The profile's three sections, every entity linked at ``link_as_of`` / ``link_viewpoint``
    (the Scene tab passes the response's own; #152 passes ``omniscient`` and the retrieval
    turn)."""
    omniscient = profile.viewpoint == OMNISCIENT

    def link(entity: EntityBody) -> EntityLink:
        return entity_link(story_id, entity, link_as_of, link_viewpoint)

    def source(fact_id: str | None) -> FactLink | None:
        return fact_link(story_id, fact_id) if fact_id else None

    def relationship(r: RelationshipBody) -> RelationshipRow:
        return RelationshipRow(
            arrow=ARROWS.get(r.direction),
            direction=r.direction,
            predicate=r.predicate,
            predicate_id=r.predicate_id,
            predicate_url=_predicate_url(story_id, r.predicate_id),
            other=link(r.other),
            qualifiers=qualifier_cells(r.qualifiers),
            valid=valid_text(r.valid_from, r.valid_to),
            source=source(r.source_fact_id),
        )

    def placement(p: PlacementBody) -> PlacementView:
        return PlacementView(
            location=link(p.location) if p.location is not None else None,
            within=[link(entity) for entity in p.within],
            container=link(p.container) if p.container is not None else None,
            valid=valid_text(p.valid_from, p.valid_to),
            source=source(p.source_fact_id),
        )

    def held(item: InventoryBody) -> InventoryRow:
        return InventoryRow(link(item.item), source(item.source_fact_id))

    return ProfileView(
        omniscient=omniscient,
        relationships=[relationship(r) for r in profile.relationships],
        placement=placement(profile.placement) if profile.placement is not None else None,
        inventory=[held(item) for item in profile.inventory],
    )


def raw(body: SceneBody | EntityProfileBody) -> dict[str, Any]:
    """Convention 13's "More": the response as the Engine sent it (the models keep the schema's
    field names), the full ids included."""
    return asdict(body)

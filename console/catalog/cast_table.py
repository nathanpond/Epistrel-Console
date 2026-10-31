"""A scenario's cast as the detail page's tables show it (#73; #74 reuses ``_cast_table.html``):
one row per role (its character named through :mod:`console.catalog.character_labels`, its
starting location by the world's name, an eligibility summary) with its seeds nested, and the
world seeds in their own table. #205 adds the locations' topology (lies in, adjoins) and the
opening's remote channels, named through the same location lookup (:func:`location_label`)."""

from collections.abc import Mapping
from dataclasses import dataclass

from console.catalog.character_labels import CharacterLabel
from console.engine.models import (
    Location,
    RoleEligibility,
    Scenario,
    ScenarioCast,
    ScenarioChannel,
    ScenarioRole,
    Seed,
)
from console.wording import humanised

ANYONE = "anyone"
ANY_AGE = "any"
LATEST = "latest"
DEFAULT_BARRIER = "Door open (default)"
NO_BARRIER = "No barrier"


@dataclass(frozen=True)
class CharacterCell:
    """A role's character: its label and the version it is pinned to ("@3" or "latest")."""

    label: CharacterLabel
    version: str


@dataclass(frozen=True)
class LocationCell:
    """A role's starting location: its id and the world's name for it (``None``: undeclared)."""

    id: str
    name: str | None


def location_names(scenario: Scenario) -> dict[str, str]:
    """The world's location names by id (the first location with an id wins)."""
    names: dict[str, str] = {}
    for loc in scenario.world.locations:
        names.setdefault(loc.id, loc.name)
    return names


def location_label(location_id: str, names: Mapping[str, str]) -> LocationCell:
    """A location named by its id: "Name (``id``)", or ``id`` (undeclared) when ``names``
    lacks it."""
    return LocationCell(location_id, names.get(location_id))


@dataclass(frozen=True)
class Barrier:
    """An adjacency's barrier as shown: humanised (``none`` reads "No barrier") with the raw
    value as its title; unset (or ``null``) "Door open (default)" with no title."""

    text: str
    raw: str | None


def barrier_text(value: str | None) -> Barrier:
    if not value:
        return Barrier(DEFAULT_BARRIER, None)
    return Barrier(NO_BARRIER if value == "none" else humanised(value), value)


@dataclass(frozen=True)
class Adjoin:
    place: LocationCell
    barrier: Barrier


@dataclass(frozen=True)
class LocationTopology:
    """A Locations table row (#205): the location, the place it lies in and the places its own
    ``adjacent_to`` names, as stored."""

    location: Location
    lies_in: LocationCell | None
    adjoins: list[Adjoin]


def location_topology(scenario: Scenario) -> list[LocationTopology]:
    names = location_names(scenario)
    return [
        LocationTopology(
            loc,
            location_label(loc.located_in, names) if loc.located_in else None,
            [
                Adjoin(location_label(a.location, names), barrier_text(a.barrier))
                for a in loc.adjacent_to
            ],
        )
        for loc in scenario.world.locations
    ]


@dataclass(frozen=True)
class Participant:
    id: str
    declared: bool


@dataclass(frozen=True)
class ChannelLine:
    """A remote channel (#205): its id and participants as stored (duplicates kept), each
    marked when it is no role of the cast."""

    id: str
    participants: list[Participant]


def channel_lines(cast: ScenarioCast, channels: list[ScenarioChannel]) -> list[ChannelLine]:
    roles = {role.id for role in cast.roles}
    return [
        ChannelLine(c.id, [Participant(p, p in roles) for p in c.participants]) for c in channels
    ]


@dataclass(frozen=True)
class CastRow:
    role: ScenarioRole
    character: CharacterCell | None
    location: LocationCell | None
    eligibility: str
    seeds: list[Seed]


@dataclass(frozen=True)
class CastTable:
    max_characters_at_start: int | None
    rows: list[CastRow]
    world_seeds: list[Seed]


def character_ids(scenario: Scenario) -> list[str]:
    """The distinct catalog characters the roles name, in role order."""
    ids = (r.character.catalog_id for r in scenario.cast.roles if r.character is not None)
    return list(dict.fromkeys(ids))


def rating_ids(scenario: Scenario) -> list[str]:
    """The distinct content ratings the roles' eligibility names."""
    ids = (
        rating
        for role in scenario.cast.roles
        if role.eligibility is not None
        for rating in role.eligibility.content_ratings
    )
    return list(dict.fromkeys(ids))


def _age(value: object) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else ANY_AGE


def eligibility_summary(
    eligibility: RoleEligibility | None, rating_names: Mapping[str, str]
) -> str:
    """ "labels: a, b · ratings: Teen, Mature · ages 18\u2013any"; nothing set: "anyone"."""
    if eligibility is None:
        return ANYONE
    parts: list[str] = []
    if eligibility.labels:
        parts.append(f"labels: {', '.join(eligibility.labels)}")
    if eligibility.content_ratings:
        names = (rating_names.get(r, r) for r in eligibility.content_ratings)
        parts.append(f"ratings: {', '.join(names)}")
    ages = eligibility.age_range
    if isinstance(ages, list) and len(ages) == 2 and ages != [None, None]:
        parts.append(f"ages {_age(ages[0])}\u2013{_age(ages[1])}")
    return " · ".join(parts) or ANYONE


def cast_table(
    scenario: Scenario,
    characters: Mapping[str, CharacterLabel],
    rating_names: Mapping[str, str],
) -> CastTable:
    """``scenario``'s cast and world seeds as the tables show them. ``characters`` are the role
    characters' labels (a missing one is labelled by its id), ``rating_names`` the eligibility
    ratings' names (a missing one shows its id)."""
    locations = location_names(scenario)
    rows: list[CastRow] = []
    for role in scenario.cast.roles:
        character: CharacterCell | None = None
        if role.character is not None:
            catalog_id = role.character.catalog_id
            shown = characters.get(catalog_id) or CharacterLabel(catalog_id, catalog_id, "")
            version = role.character.version
            character = CharacterCell(shown, f"@{version}" if version is not None else LATEST)
        location = (
            location_label(role.starting_location, locations) if role.starting_location else None
        )
        rows.append(
            CastRow(
                role,
                character,
                location,
                eligibility_summary(role.eligibility, rating_names),
                list(role.seeded_memories),
            )
        )
    return CastTable(
        scenario.cast.max_characters_at_start,
        rows,
        list(scenario.starting_state.seeded_memories),
    )

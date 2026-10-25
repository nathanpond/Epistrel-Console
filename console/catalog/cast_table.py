"""A scenario's cast as the detail page's tables show it (#73; #74 reuses ``_cast_table.html``):
one row per role (its character named through :mod:`console.catalog.character_labels`, its
starting location by the world's name, an eligibility summary) with its seeds nested, and the
world seeds in their own table."""

from collections.abc import Mapping
from dataclasses import dataclass

from console.catalog.character_labels import CharacterLabel
from console.engine.models import RoleEligibility, Scenario, ScenarioRole, Seed

ANYONE = "anyone"
ANY_AGE = "any"
LATEST = "latest"


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
    locations = {loc.id: loc.name for loc in scenario.world.locations}
    rows: list[CastRow] = []
    for role in scenario.cast.roles:
        character: CharacterCell | None = None
        if role.character is not None:
            catalog_id = role.character.catalog_id
            shown = characters.get(catalog_id) or CharacterLabel(catalog_id, catalog_id, "")
            version = role.character.version
            character = CharacterCell(shown, f"@{version}" if version is not None else LATEST)
        location = (
            LocationCell(role.starting_location, locations.get(role.starting_location))
            if role.starting_location
            else None
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

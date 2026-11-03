"""A scenario's cast as the detail page's tables show it (#73; #74 reuses ``_cast_table.html``):
one row per role (its character named through :mod:`console.catalog.character_labels`, its
starting location by the world's name, an eligibility summary) with its seeds nested, and the
world seeds in their own table. #205 adds the locations' topology (lies in, adjoins) and the
opening's remote channels, named through the same location lookup (:func:`location_label`).
#206 adds the cast's groups, each role's relationships as a table, and each seed's confidence
and nested content. #267 adds the starting States table (:func:`state_rows`) and each role's initial
State values (:func:`initial_value_lines`), values formatted by D5. #268 adds the starting clock
(:func:`clock_view`, :func:`anchor_text`) and the scheduled events (:func:`event_rows`), places
named through the same :func:`location_label`."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from console.catalog.character_labels import CharacterLabel
from console.effects import provider_url
from console.engine.models import (
    Location,
    RoleEligibility,
    RoleRelationship,
    Scenario,
    ScenarioAnchor,
    ScenarioCast,
    ScenarioChannel,
    ScenarioClock,
    ScenarioRole,
    ScenarioScheduledEvent,
    ScenarioState,
    Seed,
)
from console.wording import humanised
from console.world import NUMBER, offset_text, state_value_text

ANYONE = "anyone"
ANY_AGE = "any"
LATEST = "latest"
DEFAULT_BARRIER = "Door open (default)"
NO_BARRIER = "No barrier"
DEFAULT_CONFIDENCE = "Believed (default)"  # an unset belief or nested seed (structured genesis)
DEFAULT_ATTRIBUTED = "Known (default)"
CONFIDENT_KINDS = ("belief", "nested")


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
class GroupLine:
    """A cast group (#206): its name and members as stored (duplicates kept)."""

    name: str
    members: list[str]


def group_lines(cast: ScenarioCast) -> list[GroupLine]:
    return [GroupLine(name, list(members)) for name, members in (cast.groups or {}).items()]


@dataclass(frozen=True)
class RelationshipRow:
    """A relationships table row (#206): ``trust`` a number (shown to 2 decimals) or ``None``
    (by name); ``trust_raw`` the JSON of a stored non-number; ``known_by`` the stored audience
    (``None``: its two roles, the default)."""

    to_role: str
    name: str
    trust: float | None
    trust_raw: str
    known_by: list[str] | None


def relationship_row(relationship: RoleRelationship) -> RelationshipRow:
    trust: object = relationship.trust
    number = isinstance(trust, int | float) and not isinstance(trust, bool)
    return RelationshipRow(
        relationship.to_role,
        relationship.name,
        float(trust) if number else None,  # type: ignore[arg-type]
        "" if number or trust is None else json.dumps(trust),
        list(relationship.audience) if relationship.audience else None,
    )


@dataclass(frozen=True)
class SeedLine:
    """A seed as its table shows it (#206): ``confidence`` worded (C3, unknown values too), or
    "Believed (default)" for an unset belief or nested seed, else "—"; ``attributed`` the
    nested content's confidence worded, or "Known (default)"."""

    seed: Seed
    confidence: str
    attributed: str


def seed_line(seed: Seed) -> SeedLine:
    if seed.confidence:
        confidence = humanised(seed.confidence)
    else:
        confidence = DEFAULT_CONFIDENCE if seed.kind in CONFIDENT_KINDS else "—"
    nested = seed.nested
    attributed = (
        humanised(nested.attributed_confidence)
        if nested is not None and nested.attributed_confidence
        else DEFAULT_ATTRIBUTED
    )
    return SeedLine(seed, confidence, attributed)


@dataclass(frozen=True)
class CastRow:
    role: ScenarioRole
    character: CharacterCell | None
    location: LocationCell | None
    eligibility: str
    seeds: list[SeedLine]
    relationships: list[RelationshipRow]
    initial_values: list[str]


@dataclass(frozen=True)
class CastTable:
    max_characters_at_start: int | None
    rows: list[CastRow]
    world_seeds: list[SeedLine]
    groups: list[GroupLine]


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
                [seed_line(s) for s in role.seeded_memories],
                [relationship_row(r) for r in role.relationships],
                initial_value_lines(role, scenario.starting_state.states),
            )
        )
    return CastTable(
        scenario.cast.max_characters_at_start,
        rows,
        [seed_line(s) for s in scenario.starting_state.seeded_memories],
        group_lines(scenario.cast),
    )


# --- #267: the starting States ------------------------------------------------------------------

STORY_DEFAULT = "Story (default)"
VISIBLE_DEFAULT = "Player visible (default)"
UNDECLARED_STATE = " (undeclared State)"
NOT_PER_CHARACTER = " (not a per-character State)"
SCOPE_LABELS = {"character": "Per character"}  # the editor's choice label
CHARACTER_SCOPE = "character"
EXTERNAL = "external"


@dataclass(frozen=True)
class EnumCell:
    """An Engine enum as shown (C3), ``raw`` its value for the cell's ``title`` (``None``: unset,
    the default's label shown without one)."""

    text: str
    raw: str | None


@dataclass(frozen=True)
class EntityCell:
    """A State's ``entity_id`` and what it names: "role", "location", "entity" or ``None``
    (undeclared)."""

    id: str
    kind: str | None


@dataclass(frozen=True)
class OwnerCell:
    """The owner: ``external`` with its provider (linked when the provider page resolves, D19)
    and value name, each ``None`` when missing; else an :class:`EnumCell`."""

    cell: EnumCell
    external: bool
    provider: str | None
    provider_url: str | None
    value_name: str | None


@dataclass(frozen=True)
class StateRow:
    """A States table row: ``state`` the parsed entry, or ``None`` with ``raw`` its JSON."""

    state: ScenarioState | None
    raw: str = ""
    type: EnumCell | None = None
    scope: EnumCell | None = None
    visibility: EnumCell | None = None
    owner: OwnerCell | None = None
    initial: str = ""
    constraints: str = "—"
    aliases: str = "—"
    entity: EntityCell | None = None
    more: list[tuple[str, str]] | None = None
    objects: list[tuple[str, Any]] | None = None


def _enum(value: str | None, default: str, labels: Mapping[str, str] | None = None) -> EnumCell:
    if not value:
        return EnumCell(default, None)
    return EnumCell((labels or {}).get(value) or humanised(value), value)


def constraints_text(state: ScenarioState) -> str:
    """ "min 0 · max 100 · non-negative · tolerance 2 · one of rain, sun" (only those set;
    numbers by D5 without a unit), or "—"."""
    c = state.constraints
    if c is None:
        return "—"
    parts: list[str] = []
    if c.min is not None:
        parts.append(f"min {state_value_text(c.min, NUMBER)}")
    if c.max is not None:
        parts.append(f"max {state_value_text(c.max, NUMBER)}")
    if c.non_negative:
        parts.append("non-negative")
    if c.tolerance is not None:
        parts.append(f"tolerance {state_value_text(c.tolerance, NUMBER)}")
    if c.allowed_values:
        parts.append(f"one of {', '.join(c.allowed_values)}")
    return " · ".join(parts) or "—"


def _kinds(cast: ScenarioCast, scenario: Scenario) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for role in cast.roles:
        kinds.setdefault(role.id, "role")
    for loc in scenario.world.locations:
        kinds.setdefault(loc.id, "location")
    for ent in scenario.world.entities:
        kinds.setdefault(ent.id, "entity")
    return kinds


def state_row(entry: Any, kinds: Mapping[str, str]) -> StateRow:
    state = ScenarioState.from_json(entry)
    if state is None:
        return StateRow(None, raw=json.dumps(entry, ensure_ascii=False))
    owner = state.owner
    owner_cell = OwnerCell(
        _enum(owner, STORY_DEFAULT),
        owner == EXTERNAL,
        state.provider_id or None,
        provider_url(state.provider_id),
        state.external_value_name or None,
    )
    more: list[tuple[str, str]] = []
    if state.silent is not None:
        more.append(("Silent", "Yes" if state.silent else "No"))
    if state.reservation_expiry_minutes is not None:
        more.append(("Reservation expiry", f"{state.reservation_expiry_minutes} min"))
    objects = [
        (label, value)
        for label, value in (
            ("Rules", state.rules),
            ("Authorization", state.authorization),
            ("Transfer", state.transfer),
        )
        if value is not None
    ]
    return StateRow(
        state,
        type=_enum(state.type, ""),
        scope=_enum(state.scope, STORY_DEFAULT, SCOPE_LABELS),
        visibility=_enum(state.visibility, VISIBLE_DEFAULT),
        owner=owner_cell,
        initial=state_value_text(state.initial_value, state.type, state.unit),
        constraints=constraints_text(state),
        aliases=", ".join(state.aliases) if state.aliases else "—",
        entity=EntityCell(state.entity_id, kinds.get(state.entity_id)) if state.entity_id else None,
        more=more or None,
        objects=objects or None,
    )


def state_rows(states: Sequence[Any], cast: ScenarioCast, scenario: Scenario) -> list[StateRow]:
    """The detail page's States table: one row per stored entry, in order."""
    kinds = _kinds(cast, scenario)
    return [state_row(entry, kinds) for entry in states]


def initial_value_lines(role: ScenarioRole, states: Sequence[Any]) -> list[str]:
    """A role's "Initial State values", in the box's key order: "<name>: <D5 text>" with the
    first State of that name's type and unit; "(undeclared State)" after compact JSON for a
    name no State has, "(not a per-character State)" for one that isn't character-scoped."""
    declared: dict[str, Mapping[str, Any]] = {}
    for entry in states:
        if isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
            declared.setdefault(entry["name"], entry)
    lines: list[str] = []
    for name, value in (role.state_initial_values or {}).items():
        state = declared.get(name)
        if state is None:
            lines.append(f"{name}: {json.dumps(value, ensure_ascii=False)}{UNDECLARED_STATE}")
            continue
        kind = state.get("type") if isinstance(state.get("type"), str) else None
        unit = state.get("unit") if isinstance(state.get("unit"), str) else None
        text = f"{name}: {state_value_text(value, kind, unit)}"
        if state.get("scope") != CHARACTER_SCOPE:
            text += NOT_PER_CHARACTER
        lines.append(text)
    return lines


# --- #268: the starting clock and the scheduled events ------------------------------------------

UNANCHORED = "Unanchored (default)"
PACED_DEFAULT = "Paced (default)"
EXACT_DEFAULT = "Exact (default)"
SKIP_DEFAULT = "1,440 min (default)"  # ``ClockSettings``' default
STOP_DEFAULT = "Yes (default)"
NO_END = "no end"
UNDECLARED = " (undeclared)"
GROUP = " (group)"
WORLD = "world"
AUDIENCE_LEVELS = frozenset({"self", "acquaintances", "perceivable", "public"})


def anchor_text(anchor: ScenarioAnchor | None) -> str:
    """ "year 2026 · month 3 · day 14 · Saturday · 06:00 · band morning" (only the parts set),
    or "Unanchored (default)"."""
    if anchor is None:
        return UNANCHORED
    parts = [
        f"year {anchor.year}" if anchor.year is not None else "",
        f"month {anchor.month}" if anchor.month is not None else "",
        f"day {anchor.day}" if anchor.day is not None else "",
        anchor.weekday or "",
        anchor.time or "",
        f"band {anchor.band}" if anchor.band else "",
    ]
    return " · ".join(p for p in parts if p) or UNANCHORED


@dataclass(frozen=True)
class ClockView:
    """The detail page's Clock ``<dl>``: ``clock`` the parsed clock, or ``None`` with ``raw``
    its JSON (a stored clock that doesn't parse)."""

    clock: ScenarioClock | None
    raw: str = ""
    anchor: str = ""
    mode: EnumCell | None = None
    time_awareness: EnumCell | None = None
    skip: str = ""
    stop: str = ""
    pacing: list[tuple[str, str, str]] | None = None
    bands: list[tuple[str, str, str]] | None = None


def _three(row: Sequence[object], null: str = "") -> tuple[str, str, str]:
    """A pacing or band row's three cells as text: a missing cell blank, a ``null`` second cell
    (a pacing row's "to") ``null``'s text."""
    cells = [(null if i == 1 else "") if v is None else str(v) for i, v in enumerate(row[:3])]
    cells += [""] * (3 - len(cells))
    return cells[0], cells[1], cells[2]


def clock_view(raw: Any) -> ClockView | None:
    """``starting_state.clock`` as the detail page shows it; ``None`` (read "—") when no clock
    or an empty one is stored."""
    if raw is None or raw == {}:
        return None
    clock = ScenarioClock.from_json(raw)
    if clock is None:
        return ClockView(None, raw=json.dumps(raw, ensure_ascii=False, indent=2))
    skip = clock.max_unprompted_skip_minutes
    stop = clock.stop_at_due_events
    pacing = [_three(row, NO_END) for row in clock.pacing_table or []]
    bands = [_three(row) for row in clock.bands or []]
    return ClockView(
        clock,
        anchor=anchor_text(clock.anchor),
        mode=_enum(clock.mode, PACED_DEFAULT),
        time_awareness=_enum(clock.time_awareness, EXACT_DEFAULT),
        skip=SKIP_DEFAULT if skip is None else f"{skip:,} min",
        stop=STOP_DEFAULT if stop is None else ("Yes" if stop else "No"),
        pacing=pacing or None,
        bands=bands or None,
    )


@dataclass(frozen=True)
class EventRow:
    """A Scheduled events table row: ``event`` the parsed entry, or ``None`` with ``raw`` its
    JSON. ``planned`` is D6's offset text ("+02h 10m", ``planned_title`` "130 min") or "at
    18:00"; roles are marked when undeclared (#205's rule)."""

    event: ScenarioScheduledEvent | None
    raw: str = ""
    planned: str = ""
    planned_title: str = ""
    subject: Participant | None = None
    owner: Participant | None = None
    participants: list[Participant] | None = None
    place: LocationCell | None = None
    audience: list[str] | None = None
    disclosure: EnumCell | None = None


def _audience_label(token: str, roles: set[str], groups: set[str]) -> str:
    if token in roles or token == WORLD or token in AUDIENCE_LEVELS:
        return token
    if token in groups:
        return f"{token}{GROUP}"
    return f"{token}{UNDECLARED}"


def event_rows(events: Sequence[Any], scenario: Scenario) -> list[EventRow]:
    """The detail page's Scheduled events table: one row per stored entry, in order."""
    roles = {role.id for role in scenario.cast.roles}
    groups = set((scenario.cast.groups or {}).keys())
    names = location_names(scenario)
    rows: list[EventRow] = []
    for entry in events:
        event = ScenarioScheduledEvent.from_json(entry)
        if event is None:
            rows.append(EventRow(None, raw=json.dumps(entry, ensure_ascii=False)))
            continue
        planned = event.planned
        timed = isinstance(planned, str)
        owner = event.owner
        rows.append(
            EventRow(
                event,
                planned=f"at {planned}" if timed else offset_text(planned),
                planned_title="" if timed else f"{planned} min",
                subject=Participant(event.subject, event.subject in roles)
                if event.subject
                else None,
                owner=Participant(owner, owner == WORLD or owner in roles) if owner else None,
                participants=[Participant(p, p in roles) for p in event.participants or []],
                place=location_label(event.place, names) if event.place else None,
                audience=[_audience_label(a, roles, groups) for a in event.audience or []],
                disclosure=_enum(event.disclosure, "—"),
            )
        )
    return rows

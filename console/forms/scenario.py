"""The scenario editor (#68): create and edit every part of a scenario's draft; the cast and the
world seeds are #73's :class:`~console.forms.scenario_cast.CastForms`, the media #71's.

One flat :class:`ScenarioForm` whose field names are the Engine's write paths joined with ``__``
(#64 convention 6; the one alias is ``metadata.content_rating_id`` → ``metadata__content_rating``,
as #66), plus one formset per list of objects or multi-line items:

- ``loc``: ``world.locations``, :class:`LocationForm` (id, name, description, lies in);
- ``adj``: each location's ``adjacent_to`` as one flat edge list, :class:`AdjacencyForm` (from,
  to, barrier; #205), each row stored on its "from" location;
- ``chan``: ``starting_state.channels``, :class:`ChannelForm` (id, participants; #205);
- ``ent``: ``world.entities``, :class:`EntityForm` (id, name, kind, aliases, description);
- ``pol``: ``story_settings_defaults.policies``,
  :class:`~console.forms.policy.ScenarioPolicyForm`;
- ``hook``, ``obj``, ``mile``, ``end``: ``drivers.plot_hooks``, ``objectives``, ``milestones``
  and ``endings``, :class:`DriverItemForm` (one textarea each, so an item may span lines).

:class:`ScenarioEditor` holds the form and the formsets, always bound: to the POST, or (on a GET)
to :func:`editor_data` of the draft, so a first render, a re-render after a row button and a
refused save all read the same state. A row is added or removed by an ``op`` button
(``console.formset_rows``) with no Engine call; a fully blank row is dropped.

The sections Lite stores but does not use yet (``starting_state``'s states, clock, scheduled
events, predicates, closed sets and rules) and ``drivers.planned_reveals`` are JSON boxes
(:class:`~console.forms.fields.JsonBoxField`), parsed and type-checked before any call;
``creation_rules.bounds`` too. ``public_card.media`` is #71's Media section
(``console.forms.media_picker``), which the views set into the patch whole. The cast (#73) is
sent whole when its normalised form changed; the world seeds travel inside ``starting_state``
(a list, so the merge patch replaces it whole). The role selects (viewpoint, roles present) and
the cast's holder and audience choices offer the role rows' ids in the current form state.
List-valued checkbox fields are written through :func:`keep_order` against the draft's value
(C9): compared as sets, sent in the stored order with new entries appended.

Values are normalised the same way for the draft (:func:`draft_to_write`) and the form
(:meth:`ScenarioEditor.write_doc`) before they are compared: CRLF → LF and surrounding whitespace
stripped on text, ``""``/``null``/absent equal, empty optional row keys left out. Create sends the
non-empty groups and the owner; edit sends the RFC 7386 diff (``console.merge_patch``) of the
changed groups, arrays whole, a cleared field ``null``.

The local bounds copy the Engine's lengths and counts; patterns (ids, provider and capability
names, overridable paths) are left to its 422, which :meth:`ScenarioEditor.place_error` puts on
the field the path names.
"""

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.forms.boundfield import BoundField
from django.forms.utils import ErrorDict, ErrorList
from django.http import QueryDict

from console import merge_patch
from console.engine.choices import CharacterChoices, ProfileChoices, RatingChoices
from console.engine.client import EngineError
from console.engine.models import Scenario, ValidationReport
from console.engine_form_errors import engine_field_errors
from console.forms.catalog_character import (
    LabelsField,
    LinesField,
    TextField,
    norm_labels,
    norm_lines,
    norm_text,
    prune,
)
from console.forms.fields import JsonBoxField
from console.forms.policy import (
    DEFAULT_RULE_TYPE,
    RULE_TEMPLATES,
    ScenarioPolicyForm,
    retemplate,
    rule_template_text,
)
from console.forms.principal import PrincipalField
from console.forms.scenario_cast import (
    CastChoices,
    CastForms,
    cast_data,
    cast_to_write,
    new_row,
    normalize_cast,
    pin_companion_slots,
    posted_role_ids,
    prepared,
    row_caps,
    seed_to_write,
)
from console.formset_rows import OP_ADD, Op, apply_op
from console.reports import field_anchor
from console.story_settings import (
    BOOLEAN_SELECT,
    LABELS,
    NULL_DEFAULT_TEXT,
    SETTING_CHOICES,
    engine_default_choice,
    kept_choices,
    select_bool,
    tie_break_label,
)
from console.wording import humanised

SEP = "__"
BODY_PREFIX = "body."
OWNER = "owner"
RATING_FIELD = "metadata__content_rating"
RATING_PATH = "metadata.content_rating_id"
PROFILE_FIELD = "recommendation__engine_profile_id"
LOCATION_FIELD = "starting_state__opening__starting_location"
ROLES_FIELD = "starting_state__opening__roles_present"
VIEWPOINT_FIELD = "story_settings_defaults__default_output_viewpoint"
NOT_SET = ""
NONE_LABEL = "— none —"
DEFAULT_LABEL = "(deployment default)"
OTHER_LABEL = "(other)"
MISSING_SUFFIX = " (missing)"
UNKNOWN_SUFFIX = " (unknown)"
NO_ROLES = "Add roles (Cast) first"
NO_LOCATIONS = "Add locations (World) first"
DEFERRED_NOTE = "Stored by Lite but not used until a later Engine milestone"
DRAFT_TOO_LARGE = "Draft exceeds the Engine's 1 MiB limit"
CAST_LIMIT_EXCEEDED = "cast_limit_exceeded"
COMPANION_SLOTS_FIXED = "companion_slots_fixed"
CAST_LIMIT_PATH = "cast.max_characters_at_start"

# The Engine's bounds (``ScenarioCreate`` and its groups).
NAME_MAX = 120
TEXT_MAX = 8000
PROMPT_MAX = 2000
ID_MAX = 40
ALIAS_MAX, ALIASES_MAX = 60, 20
ROWS_MAX = 100  # locations, entities and each driver list
POLICIES_MAX = 50
EVENT_TYPE_MAX, EVENT_TYPES_MAX = 60, 50
NAME_LIST_ITEM_MAX, NAME_LISTS_MAX = 64, 50  # capabilities, required providers
OVERRIDABLE_MAX, OVERRIDABLE_ITEMS = 200, 100
ROLES_PRESENT_MAX = 32
ADJACENT_MAX = 100  # a location's adjacent_to
ADJACENCY_ROWS_MAX = 1000  # the adj row set's own cap
CHANNELS_MAX, CHANNEL_ID_MAX, PARTICIPANTS_MAX = 100, 60, 32
RETENTION_MIN, RETENTION_MAX = 1, 3650

# --- the schema's enums (a test compares them with ``openapi.json``) ------------------------------

ENTITY_KINDS = ("person", "place", "organization", "item", "event", "other")
BARRIERS = ("none", "door_open", "door_closed", "window", "thin_wall", "wall")
SETTING_ENUMS: dict[str, tuple[str, ...]] = {
    "ambiguous_retcon_timing": ("include_if_possible", "ask_author"),
    "backfill_conflict_policy": ("last_mention", "first_mention", "ask"),
    "interaction_mode": ("sequential", "simultaneous"),
    "player_authority": ("own_character", "co_author"),
    "turn_concurrency": ("reject", "queue"),
    "player_view": ("character", "story"),
    "sequence_order": ("request", "director"),
}
# Engine M5's defaults (#198): C7 choices (#197) instead of "(deployment default)" and " (unknown)".
C7_SETTINGS = frozenset({"player_view", "sequence_order"})
# Each C7 field's "Engine default (<text>)" (the tie-break and interjections included).
C7_DEFAULTS = {
    "player_view": "character",
    "sequence_order": NULL_DEFAULT_TEXT["sequence_order"],
    "simultaneous_tie_break": NULL_DEFAULT_TEXT["simultaneous_tie_break"],
    "reactive_interjections": "off",
}
# The two boolean defaults, sent as ``true``/``false`` and edited as a select.
BOOLEAN_SETTINGS = (
    "story_settings_defaults__allow_new_arrivals",
    "story_settings_defaults__reactive_interjections",
)
TIE_BREAK_FIELD = "story_settings_defaults__simultaneous_tie_break"
BOOLEAN_CHOICES = [(NOT_SET, DEFAULT_LABEL), ("true", "Yes"), ("false", "No")]

# The deferred JSON boxes: field name → (top type, empty value).
DEFERRED_BOXES: dict[str, tuple[type, Any]] = {
    "starting_state__states": (list, []),
    "starting_state__clock": (dict, None),
    "starting_state__scheduled_events": (list, []),
    "starting_state__predicates": (list, []),
    "starting_state__closed_sets": (list, []),
    "starting_state__rules": (list, []),
}
OTHER_BOXES: dict[str, tuple[type, Any]] = {
    "drivers__planned_reveals": (list, []),
    "creation_rules__bounds": (dict, None),
}
BOXES = {**DEFERRED_BOXES, **OTHER_BOXES}

# One-per-line string lists (an Engine index lands as "Line N").
LINE_FIELDS = (
    "world__event_types",
    "story_settings_defaults__required_providers",
    "recommendation__capabilities",
    "creation_rules__overridable",
)

# Row formsets: prefix → (Engine path, row cap).
LOCATIONS, ENTITIES, POLICIES = "loc", "ent", "pol"
ADJACENCY, CHANNELS = "adj", "chan"  # #205: no Engine path of their own in ROW_PATHS
CHANNELS_PATH = "starting_state.channels"
ADJACENCY_ANCHOR = "f-world-adjacency"
DRIVER_LISTS = {
    "hook": "drivers.plot_hooks",
    "obj": "drivers.objectives",
    "mile": "drivers.milestones",
    "end": "drivers.endings",
}
ROW_PATHS = {
    LOCATIONS: "world.locations",
    ENTITIES: "world.entities",
    POLICIES: "story_settings_defaults.policies",
    **DRIVER_LISTS,
}
ROW_CAPS = {
    LOCATIONS: ROWS_MAX,
    ENTITIES: ROWS_MAX,
    POLICIES: POLICIES_MAX,
    ADJACENCY: ADJACENCY_ROWS_MAX,
    CHANNELS: CHANNELS_MAX,
}
ROW_CAPS.update(dict.fromkeys(DRIVER_LISTS, ROWS_MAX))
NEW_ROWS: dict[str, dict[str, list[str]]] = {
    POLICIES: {"rule_type": [DEFAULT_RULE_TYPE], "rule": [rule_template_text(DEFAULT_RULE_TYPE)]},
}
_INDEXED = re.compile(r"^(?P<base>[^\[\]]+)\[(?P<index>\d+)\](?:\.(?P<rest>.+))?$")
_ADJACENCY_PATH = re.compile(
    r"^world\.locations\[(?P<location>\d+)\]\.adjacent_to\[(?P<entry>\d+)\](?:\.(?P<rest>.+))?$"
)
_PARTICIPANT = re.compile(r"^participants\[(?P<index>\d+)\]$")

# #205's wording.
LIES_IN_HELP = "Seeded as canon by a structured story; Lite stores it but doesn't use it."
THIS_PLACE_SUFFIX = " (this place)"
LIES_IN_ITSELF = "A place can't lie in itself"
BARRIER_DEFAULT = "door open"  # C7's "Engine default (door open)"
NO_BARRIER = "No barrier"  # ``none``: a specific label, never mistaken for the blank
BARRIER_CHOICES = [(b, NO_BARRIER if b == "none" else humanised(b)) for b in BARRIERS]
ADJACENCY_HELP = "Adjacency is symmetric: declare each pair once."
CHOOSE_BOTH = "Choose both places"
ADJOINS_ITSELF = "A place can't adjoin itself"
PAIR_DECLARED = "Already declared in row {row} (adjacency is symmetric)"
NO_SUCH_LOCATION = "No location row has this id"
TOO_MANY_ADJACENT = f"At most {ADJACENT_MAX} adjacent places per location"
CHANNELS_HELP = (
    "Channels open when the story starts (a phone call, a radio net). A structured story puts "
    "each participant on the channel."
)
CHANNEL_NEEDS_ID = "A channel needs its id"
CHANNEL_ID_USED = "Already used by row {row}"
TOO_MANY_PARTICIPANTS = f"At most {PARTICIPANTS_MAX} participants"


# --- the draft as the form sees it ----------------------------------------------------------------


def _rows(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Row objects with their empty optional keys left out."""
    return [{k: v for k, v in item.items() if v not in (None, "", [])} for item in items]


def _items(values: Iterable[object]) -> list[str]:
    """A driver list: each item's text normalised, blanks dropped (order and repeats kept)."""
    return [text for text in (norm_text(v) for v in values) if text]


def keep_order(stored: Sequence[str], chosen: Iterable[str]) -> list[str]:
    """C9 (#205): the ``stored`` entries still chosen, in stored order (duplicates kept), then
    the newly chosen ones in ``chosen``'s order: a list-valued checkbox field written this way
    compares with the draft's value as a set, so an untouched one sends no change."""
    picked = list(dict.fromkeys(chosen))
    wanted, known = set(picked), set(stored)
    return [s for s in stored if s in wanted] + [c for c in picked if c not in known]


def _adjacency(location: str, barrier: str | None) -> dict[str, Any]:
    """One ``adjacent_to`` entry; an unset (or explicit ``null``) barrier left out."""
    return {"location": location, **({"barrier": barrier} if barrier else {})}


def adjacency_entries(
    location_ids: Sequence[str], rows: Iterable[tuple[int, str]]
) -> dict[int, list[int]]:
    """#205's sent-index map: sent location ``n`` → the adjacency rows (``(row index, from)``
    pairs, in row order) that make up its ``adjacent_to``, entry ``a`` being the list's
    ``a``-th. A row attaches to the first location with its ``from`` id; one naming none is
    left out. Pure, so the write and both error placements read the same map."""
    first: dict[str, int] = {}
    for n, location_id in enumerate(location_ids):
        first.setdefault(location_id, n)
    entries: dict[int, list[int]] = {}
    for index, origin in rows:
        if origin in first:
            entries.setdefault(first[origin], []).append(index)
    return entries


def _policy(policy: Any) -> dict[str, Any]:
    return {
        "name": norm_text(policy.name),
        "applies_to": list(policy.applies_to) if policy.applies_to else None,
        "rule": policy.rule,
        "age_condition": policy.age_condition,
        "message": norm_text(policy.message),
    }


def draft_to_write(draft: Scenario) -> dict[str, Any]:
    """The draft's groups the editor edits, in the write shape and normalised (unset values
    ``None``): the merge patch's original and the form's first state. The card's media is left
    out (#71's)."""
    world = draft.world
    state = draft.starting_state
    opening = state.opening
    drivers = draft.drivers
    settings = draft.story_settings_defaults
    return {
        "public_card": {
            "name": norm_text(draft.public_card.name),
            "public_description": norm_text(draft.public_card.public_description),
        },
        "metadata": {
            "labels": norm_labels(draft.metadata.labels),
            "content_rating_id": draft.metadata.content_rating_id,
        },
        "world": {
            "description": norm_text(world.description),
            "locations": _rows(
                {
                    "id": loc.id,
                    "name": norm_text(loc.name),
                    "description": norm_text(loc.description),
                    "located_in": loc.located_in,
                    "adjacent_to": [_adjacency(a.location, a.barrier) for a in loc.adjacent_to],
                }
                for loc in world.locations
            ),
            "entities": _rows(
                {
                    "id": ent.id,
                    "name": norm_text(ent.name),
                    "kind": ent.kind,
                    "aliases": norm_lines(ent.aliases),
                    "description": norm_text(ent.description),
                }
                for ent in world.entities
            ),
            "event_types": norm_lines(world.event_types) or None,
        },
        "cast": cast_to_write(draft.cast, companion=draft.companion_of is not None),
        "starting_state": {
            "seeded_memories": [seed_to_write(s) for s in state.seeded_memories],
            "opening": {
                "narration": norm_text(opening.narration),
                "starting_location": opening.starting_location,
                "roles_present": list(dict.fromkeys(opening.roles_present)),
            },
            "states": state.states,
            "clock": state.clock,
            "scheduled_events": state.scheduled_events,
            "predicates": state.predicates,
            "closed_sets": state.closed_sets,
            "rules": state.rules,
            "channels": [
                {"id": channel.id, "participants": list(channel.participants)}
                for channel in state.channels
            ],
        },
        "drivers": {
            "director_guidance": norm_text(drivers.director_guidance),
            "plot_hooks": _items(drivers.plot_hooks),
            "objectives": _items(drivers.objectives),
            "milestones": _items(drivers.milestones),
            "endings": _items(drivers.endings),
            "planned_reveals": drivers.planned_reveals,
        },
        "story_settings_defaults": {
            "allow_new_arrivals": settings.allow_new_arrivals,
            "reactive_interjections": settings.reactive_interjections,
            "simultaneous_tie_break": settings.simultaneous_tie_break,
            **{name: getattr(settings, name) for name in SETTING_ENUMS},
            "audit_retention_days": settings.audit_retention_days,
            "default_output_viewpoint": settings.default_output_viewpoint,
            "policies": _rows(_policy(p) for p in settings.policies),
            "required_providers": norm_lines(settings.required_providers),
        },
        "recommendation": {
            "engine_profile_id": draft.recommendation.engine_profile_id,
            "capabilities": norm_lines(draft.recommendation.capabilities),
        },
        "visual_style": {
            "prompt": norm_text(draft.visual_style.prompt),
            "negative_prompt": norm_text(draft.visual_style.negative_prompt),
        },
        "creation_rules": {
            "overridable": norm_lines(draft.creation_rules.overridable),
            "bounds": draft.creation_rules.bounds,
        },
    }


def drop_empty(value: Any) -> Any:
    """Create's rule: ``None``, empty objects and empty lists left out at every object level."""
    if not isinstance(value, Mapping):
        return value
    out: dict[str, Any] = {}
    for key, item in value.items():
        item = drop_empty(item)
        if item is None or item == {} or item == []:
            continue
        out[key] = item
    return out


def _get(doc: Mapping[str, Any], path: str) -> Any:
    node: Any = doc
    for key in path.split("."):
        node = node.get(key) if isinstance(node, Mapping) else None
    return node


def _box_text(value: Any) -> str:
    """A JSON box's text: indented, key order kept (#68)."""
    return json.dumps(value, indent=2, ensure_ascii=False)


def editor_data(doc: Mapping[str, Any]) -> QueryDict:
    """``doc`` (:func:`draft_to_write`'s shape) as the editor's POST would carry it, every row
    in its formset: what a GET binds the editor to."""
    data = QueryDict(mutable=True)

    def put(name: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, list):
            data.setlist(name, [str(v) for v in value])
        else:
            data[name] = str(value)

    for name in main_field_names():
        value = _get(doc, RATING_PATH if name == RATING_FIELD else name.replace(SEP, "."))
        if name in BOXES:
            empty = BOXES[name][1]
            put(name, None if value is None or value == empty else _box_text(value))
        elif name in LINE_FIELDS:
            put(name, "\n".join(value or []))
        elif name == "metadata__labels":
            put(name, ", ".join(value or []))
        elif name in BOOLEAN_SETTINGS:
            put(name, None if value is None else ("true" if value else "false"))
        else:
            put(name, value)
    locations: list[Mapping[str, Any]] = list(_get(doc, ROW_PATHS[LOCATIONS]) or [])
    rows: dict[str, list[dict[str, Any]]] = {
        LOCATIONS: [{k: v for k, v in loc.items() if k != "adjacent_to"} for loc in locations],
        ADJACENCY: [
            {"from": loc.get("id"), "to": entry.get("location"), "barrier": entry.get("barrier")}
            for loc in locations
            for entry in loc.get("adjacent_to") or []
        ],
        CHANNELS: [
            {"id": channel.get("id"), "participants": channel.get("participants") or []}
            for channel in _get(doc, CHANNELS_PATH) or []
        ],
        ENTITIES: [
            {**e, "aliases": "\n".join(e.get("aliases") or [])}
            for e in _get(doc, ROW_PATHS[ENTITIES]) or []
        ],
        POLICIES: [
            {
                "name": p.get("name"),
                "applies_to": p.get("applies_to") or [],
                "rule_type": _rule_type(p.get("rule")),
                "rule": _box_text(p.get("rule")),
                "age_condition": "on" if p.get("age_condition") else None,
                "message": p.get("message"),
            }
            for p in _get(doc, ROW_PATHS[POLICIES]) or []
        ],
    }
    for prefix, path in DRIVER_LISTS.items():
        rows[prefix] = [{"text": item} for item in _get(doc, path) or []]
    for prefix, items in rows.items():
        for index, row in enumerate(items):
            for key, value in row.items():
                put(f"{prefix}-{index}-{key}", value)
        data.update(_management(prefix, len(items)))
    for name, values in cast_data(doc).items():
        data.setlist(name, values)
    return data


def _rule_type(rule: object) -> str:
    kind = rule.get("type") if isinstance(rule, Mapping) else None
    return kind if isinstance(kind, str) else DEFAULT_RULE_TYPE


def _management(prefix: str, total: int) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": str(ROW_CAPS[prefix]),
    }


def retemplated(data: QueryDict) -> QueryDict:
    """The posted state for a re-render: each policy row's rule box re-templated to its rule
    type when blank or still another type's template (:func:`~console.forms.policy.retemplate`)."""
    out = data.copy()
    try:
        total = int(out.get(f"{POLICIES}-TOTAL_FORMS", "0"))
    except ValueError:
        total = 0
    for index in range(max(0, min(total, POLICIES_MAX + 1))):
        rule_type = out.get(f"{POLICIES}-{index}-rule_type") or DEFAULT_RULE_TYPE
        if rule_type not in RULE_TEMPLATES:
            continue
        name = f"{POLICIES}-{index}-rule"
        out[name] = retemplate(out.get(name, ""), rule_type)
    return out


# --- row forms ------------------------------------------------------------------------------------


def _description() -> TextField:
    return TextField(
        label="Description",
        max_length=TEXT_MAX,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "cols": 50}),
    )


def _raw(form: forms.Form, name: str) -> str:
    """A row field's posted value, stripped (``""`` when absent)."""
    value = form.data.get(form.add_prefix(name)) if form.is_bound else None
    return value.strip() if isinstance(value, str) else ""


def _place_choices(
    ids: Iterable[str], blank: str, keep: str, own: str = ""
) -> list[tuple[str, str]]:
    """A location select's options: ``blank``, then ``ids``; a posted ``keep`` they lack is
    kept, labelled "<id> (this place)" when it is the row's ``own`` id, else "<id> (missing)"."""
    choices = [(NOT_SET, blank), *((v, v) for v in ids)]
    if keep and keep not in {v for v, _ in choices}:
        choices.append((keep, f"{keep}{THIS_PLACE_SUFFIX if keep == own else MISSING_SUFFIX}"))
    return choices


class LocationForm(forms.Form):
    """A location row. ``locations``: the location rows' ids in the current form state, which
    "Lies in" offers (all but this row's own)."""

    id = forms.CharField(label="Id", max_length=ID_MAX, help_text="a-z, 0-9 and _")
    name = TextField(label="Name", max_length=NAME_MAX)
    description = _description()
    located_in = forms.ChoiceField(label="Lies in", required=False, help_text=LIES_IN_HELP)

    def __init__(self, *args: Any, locations: Sequence[str] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        own = _raw(self, "id")
        field = self.fields["located_in"]
        if isinstance(field, forms.ChoiceField):
            others = [v for v in locations if v != own]
            field.choices = _place_choices(others, NONE_LABEL, _raw(self, "located_in"), own)

    def clean(self) -> dict[str, Any] | None:
        data = super().clean() or {}
        located_in = data.get("located_in")
        if located_in and located_in == data.get("id"):
            self.add_error("located_in", LIES_IN_ITSELF)
        return data

    def row(self) -> dict[str, Any]:
        """The row in schema order (``adjacent_to`` is the editor's, from the ``adj`` rows)."""
        data = self.cleaned_data
        return _rows(
            [
                {
                    "id": data["id"],
                    "name": data["name"],
                    "description": data["description"],
                    "located_in": data.get("located_in") or None,
                }
            ]
        )[0]


class AdjacencyForm(forms.Form):
    """An ``adj`` row (#205): one ``adjacent_to`` entry of its ``from`` location.
    ``locations``: the location rows' ids in the current form state."""

    to = forms.ChoiceField(label="To", required=False)
    barrier = forms.ChoiceField(label="Barrier", required=False)

    def __init__(self, *args: Any, locations: Sequence[str] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        blank = NONE_LABEL if locations else NO_LOCATIONS
        for name in ("from", "to"):
            field = self.fields[name]
            if isinstance(field, forms.ChoiceField):
                field.choices = _place_choices(locations, blank, _raw(self, name))
                if not locations:
                    field.help_text = NO_LOCATIONS
        barrier = self.fields["barrier"]
        if isinstance(barrier, forms.ChoiceField):
            barrier.choices = [
                engine_default_choice(BARRIER_DEFAULT),
                *kept_choices(BARRIER_CHOICES, _raw(self, "barrier") or None),
            ]

    @property
    def blank(self) -> bool:
        """Neither place chosen: dropped, whatever its barrier."""
        return not _raw(self, "from") and not _raw(self, "to")

    def entry(self) -> dict[str, Any]:
        data = self.cleaned_data
        return _adjacency(data["to"], data.get("barrier") or None)


# ``from`` is a Python keyword: set as a field right after the class (posted as adj-<n>-from).
AdjacencyForm.base_fields = {
    "from": forms.ChoiceField(label="From", required=False),
    **AdjacencyForm.base_fields,
}


class AdjacencyFormSet(BaseFormSet):  # type: ignore[type-arg]
    """The ``adj`` rows' cross-row checks (#205), on save only; a row shows its first failing
    check. Only rows that pass count as declared and toward a ``from``'s cap."""

    def clean(self) -> None:
        locations = set(self.form_kwargs.get("locations") or ())
        declared: dict[frozenset[str], int] = {}
        per_origin: Counter[str] = Counter()
        for index, form in enumerate(self.forms):
            data = getattr(form, "cleaned_data", None)
            if not data or form.errors:
                continue
            origin, to = data.get("from") or "", data.get("to") or ""
            if not origin and not to:
                continue
            pair = frozenset((origin, to))
            if not origin or not to:
                form.add_error("to" if origin else "from", CHOOSE_BOTH)
            elif origin == to:
                form.add_error("to", ADJOINS_ITSELF)
            elif pair in declared:
                form.add_error("to", PAIR_DECLARED.format(row=declared[pair] + 1))
            elif origin not in locations:
                form.add_error("from", NO_SUCH_LOCATION)
            elif per_origin[origin] >= ADJACENT_MAX:
                form.add_error("from", TOO_MANY_ADJACENT)
            else:
                declared[pair] = index
                per_origin[origin] += 1


class ChannelForm(forms.Form):
    """A ``chan`` row (#205): a starting remote channel. ``roles``: the role rows' ids in the
    current form state; a ticked id matching none stays ticked as "<id> (missing)"."""

    id = forms.CharField(label="Id", max_length=CHANNEL_ID_MAX, required=False)
    participants = forms.MultipleChoiceField(
        label="Participants", required=False, widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args: Any, roles: Sequence[str] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["participants"]
        if isinstance(field, forms.MultipleChoiceField):
            field.choices = _kept([(v, v) for v in roles], self._ticked())
            if not roles:
                field.help_text = NO_ROLES

    def _ticked(self) -> list[str]:
        if not self.is_bound or not hasattr(self.data, "getlist"):
            return []
        return [v for v in self.data.getlist(self.add_prefix("participants")) if v]

    @property
    def blank(self) -> bool:
        """A blank (or whitespace-only) id and nothing ticked: dropped."""
        return not _raw(self, "id") and not self._ticked()

    def channel(self, stored: Mapping[str, Sequence[str]]) -> dict[str, Any]:
        """The channel sent: its participants in the form's order, through :func:`keep_order`
        against the first stored channel with this id (C9)."""
        data = self.cleaned_data
        channel_id: str = data["id"]
        field = self.fields["participants"]
        ticked = set(data.get("participants") or [])
        chosen = [v for v, _ in getattr(field, "choices", []) if v in ticked]
        previous = stored.get(channel_id)
        return {
            "id": channel_id,
            "participants": keep_order(previous, chosen) if previous is not None else chosen,
        }


class ChannelFormSet(BaseFormSet):  # type: ignore[type-arg]
    """The ``chan`` rows' checks (#205), on save only."""

    def clean(self) -> None:
        seen: dict[str, int] = {}
        for index, form in enumerate(self.forms):
            data = getattr(form, "cleaned_data", None)
            if data is None:
                continue
            ticked = data.get("participants") or []
            if "id" not in form.errors:
                channel_id = data.get("id") or ""
                if not channel_id:
                    if ticked:
                        form.add_error("id", CHANNEL_NEEDS_ID)
                elif channel_id in seen:
                    form.add_error("id", CHANNEL_ID_USED.format(row=seen[channel_id] + 1))
                else:
                    seen[channel_id] = index
            if len(set(ticked)) > PARTICIPANTS_MAX:
                form.add_error("participants", TOO_MANY_PARTICIPANTS)


class EntityForm(forms.Form):
    id = forms.CharField(label="Id", max_length=ID_MAX, help_text="a-z, 0-9 and _")
    name = TextField(label="Name", max_length=NAME_MAX)
    kind = forms.ChoiceField(
        label="Kind",
        required=False,
        choices=[(NOT_SET, OTHER_LABEL), *((k, k) for k in ENTITY_KINDS)],
    )
    aliases = LinesField(
        label="Aliases",
        max_items=ALIASES_MAX,
        item_max=ALIAS_MAX,
        help_text=f"One per line, up to {ALIASES_MAX}, each up to {ALIAS_MAX} characters",
        widget=forms.Textarea(attrs={"rows": 2, "cols": 30}),
    )
    description = _description()

    def row(self) -> dict[str, Any]:
        data = self.cleaned_data
        return _rows(
            [
                {
                    "id": data["id"],
                    "name": data["name"],
                    "kind": data.get("kind") or None,
                    "aliases": data.get("aliases") or [],
                    "description": data["description"],
                }
            ]
        )[0]


class DriverItemForm(forms.Form):
    text = TextField(
        label="Item",
        max_length=TEXT_MAX,
        widget=forms.Textarea(attrs={"rows": 2, "cols": 80}),
        help_text=f"Up to {TEXT_MAX} characters; may span lines",
    )


def _formset(
    form: type[forms.Form], cap: int, base: type[BaseFormSet[Any]] = BaseFormSet
) -> type[BaseFormSet[Any]]:
    return formset_factory(
        form, formset=base, extra=0, max_num=cap, validate_max=True, absolute_max=cap + 1
    )


FORMSETS: dict[str, type[BaseFormSet[Any]]] = {
    LOCATIONS: _formset(LocationForm, ROWS_MAX),
    ADJACENCY: _formset(AdjacencyForm, ADJACENCY_ROWS_MAX, AdjacencyFormSet),
    CHANNELS: _formset(ChannelForm, CHANNELS_MAX, ChannelFormSet),
    ENTITIES: _formset(EntityForm, ROWS_MAX),
    POLICIES: _formset(ScenarioPolicyForm, POLICIES_MAX),
    **{prefix: _formset(DriverItemForm, ROWS_MAX) for prefix in DRIVER_LISTS},
}


# --- the main form --------------------------------------------------------------------------------


def _text(label: str, max_length: int, *, rows: int = 0) -> TextField:
    widget: forms.Widget = (
        forms.Textarea(attrs={"rows": rows, "cols": 80}) if rows else forms.TextInput()
    )
    return TextField(
        label=label,
        max_length=max_length,
        required=False,
        widget=widget,
        help_text=f"Up to {max_length} characters",
    )


def _lines(label: str, max_items: int, item_max: int, extra: str = "") -> LinesField:
    hint = f"One per line, up to {max_items}, each up to {item_max} characters"
    return LinesField(
        label=label,
        max_items=max_items,
        item_max=item_max,
        help_text=f"{hint}; {extra}" if extra else hint,
    )


def _box(name: str, label: str, *, deferred: bool) -> JsonBoxField:
    top, empty = BOXES[name]
    shape = "a JSON object" if top is dict else "a JSON array"
    blank = "blank: none" if top is dict else "blank: []"
    hint = f"{shape} ({blank})"
    return JsonBoxField(
        top,
        empty=empty,
        required=False,
        label=label,
        help_text=f"{DEFERRED_NOTE}. {hint}" if deferred else hint,
        widget=forms.Textarea(
            attrs={"rows": 6, "cols": 80, "spellcheck": "false", "class": "monospace"}
        ),
    )


def _enum_choices(values: tuple[str, ...]) -> list[tuple[str, str]]:
    return [(NOT_SET, DEFAULT_LABEL), *((v, v.replace("_", " ")) for v in values)]


def _enum(label: str, values: tuple[str, ...]) -> forms.ChoiceField:
    return forms.ChoiceField(label=label, required=False, choices=_enum_choices(values))


def _c7(name: str, choices: list[tuple[str, str]]) -> forms.ChoiceField:
    """An M5 default (#198): C7's "Engine default (<text>)" first, then #197's choices."""
    return forms.ChoiceField(
        label=LABELS[name],
        required=False,
        choices=[engine_default_choice(C7_DEFAULTS[name]), *choices],
    )


def _fields() -> dict[str, forms.Field]:
    """Every field but the owner, in display order."""
    settings = "story_settings_defaults"
    fields: dict[str, forms.Field] = {
        "public_card__name": _text("Name", NAME_MAX),
        "public_card__public_description": _text("Public description", TEXT_MAX, rows=4),
        "metadata__labels": LabelsField(
            label="Labels",
            required=False,
            help_text="Comma-separated, up to 30; A-Z, a-z, 0-9, _, - and spaces",
        ),
        RATING_FIELD: forms.ChoiceField(label="Content rating", required=False),
        "world__description": _text("Description", TEXT_MAX, rows=4),
        "world__event_types": _lines(
            "Event types", EVENT_TYPES_MAX, EVENT_TYPE_MAX, "the story's own occasion words"
        ),
        "starting_state__opening__narration": _text("Narration", TEXT_MAX, rows=4),
        LOCATION_FIELD: forms.ChoiceField(label="Starting location", required=False),
        ROLES_FIELD: forms.MultipleChoiceField(
            label="Roles present",
            required=False,
            widget=forms.CheckboxSelectMultiple,
        ),
        f"{settings}__allow_new_arrivals": forms.ChoiceField(
            label="Allow new arrivals", required=False, choices=BOOLEAN_CHOICES
        ),
        f"{settings}__interaction_mode": _enum(
            "Interaction mode", SETTING_ENUMS["interaction_mode"]
        ),
        f"{settings}__player_authority": _enum(
            "Player authority", SETTING_ENUMS["player_authority"]
        ),
        f"{settings}__player_view": _c7("player_view", SETTING_CHOICES["player_view"]),
        f"{settings}__sequence_order": _c7("sequence_order", SETTING_CHOICES["sequence_order"]),
        TIE_BREAK_FIELD: _c7("simultaneous_tie_break", SETTING_CHOICES["simultaneous_tie_break"]),
        f"{settings}__reactive_interjections": _c7("reactive_interjections", BOOLEAN_SELECT),
        f"{settings}__turn_concurrency": _enum(
            "Turn concurrency", SETTING_ENUMS["turn_concurrency"]
        ),
        f"{settings}__ambiguous_retcon_timing": _enum(
            "Ambiguous retcon timing", SETTING_ENUMS["ambiguous_retcon_timing"]
        ),
        f"{settings}__backfill_conflict_policy": _enum(
            "Backfill conflict policy", SETTING_ENUMS["backfill_conflict_policy"]
        ),
        f"{settings}__audit_retention_days": forms.IntegerField(
            label="Audit retention (days)",
            required=False,
            min_value=RETENTION_MIN,
            max_value=RETENTION_MAX,
            help_text=f"{RETENTION_MIN} to {RETENTION_MAX}; blank: the deployment default",
            widget=forms.TextInput(attrs={"inputmode": "numeric", "size": 6}),
        ),
        VIEWPOINT_FIELD: forms.ChoiceField(label="Default output viewpoint", required=False),
        f"{settings}__required_providers": _lines(
            "Required providers", NAME_LISTS_MAX, NAME_LIST_ITEM_MAX
        ),
        PROFILE_FIELD: forms.ChoiceField(label="Engine profile", required=False),
        "recommendation__capabilities": _lines("Capabilities", NAME_LISTS_MAX, NAME_LIST_ITEM_MAX),
        "visual_style__prompt": _text("Prompt", PROMPT_MAX, rows=3),
        "visual_style__negative_prompt": _text("Negative prompt", PROMPT_MAX, rows=3),
        "creation_rules__overridable": _lines(
            "Overridable paths", OVERRIDABLE_ITEMS, OVERRIDABLE_MAX, "dotted paths"
        ),
        "creation_rules__bounds": _box("creation_rules__bounds", "Bounds", deferred=False),
        "drivers__director_guidance": _text("Director guidance", TEXT_MAX, rows=4),
        "drivers__planned_reveals": _box(
            "drivers__planned_reveals", "Planned reveals", deferred=False
        ),
        "starting_state__states": _box("starting_state__states", "States", deferred=True),
        "starting_state__clock": _box("starting_state__clock", "Clock", deferred=True),
        "starting_state__scheduled_events": _box(
            "starting_state__scheduled_events", "Scheduled events", deferred=True
        ),
        "starting_state__predicates": _box(
            "starting_state__predicates", "Predicates", deferred=True
        ),
        "starting_state__closed_sets": _box(
            "starting_state__closed_sets", "Closed sets", deferred=True
        ),
        "starting_state__rules": _box("starting_state__rules", "Rules", deferred=True),
    }
    return fields


def main_field_names() -> list[str]:
    return list(_fields())


def field_path(name: str) -> str:
    """The Engine path a main field mirrors (its report anchor's source)."""
    return RATING_PATH if name == RATING_FIELD else name.replace(SEP, ".")


SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Card", "public_card", ("public_card__name", "public_card__public_description")),
    ("Metadata", "metadata", ("metadata__labels", RATING_FIELD)),
    ("World", "world", ("world__description", "world__event_types")),
    ("Opening", "starting_state.opening", tuple(f for f in main_field_names() if "opening" in f)),
    (
        "Story-setting defaults",
        "story_settings_defaults",
        tuple(f for f in main_field_names() if f.startswith("story_settings_defaults")),
    ),
    ("Recommendation", "recommendation", (PROFILE_FIELD, "recommendation__capabilities")),
    ("Visual style", "visual_style", ("visual_style__prompt", "visual_style__negative_prompt")),
    ("Creation rules", "creation_rules", ("creation_rules__overridable", "creation_rules__bounds")),
    ("Drivers", "drivers", ("drivers__director_guidance", "drivers__planned_reveals")),
    ("Deferred", "starting_state", tuple(DEFERRED_BOXES)),
)


def _kept(
    choices: list[tuple[str, str]], keep: Iterable[str], suffix: str = MISSING_SUFFIX
) -> list[tuple[str, str]]:
    """``choices`` plus each of ``keep`` it lacks, labelled "<value> (missing)"."""
    known = {value for value, _ in choices}
    extra = [(v, f"{v}{suffix}") for v in dict.fromkeys(keep) if v and v not in known]
    return [*choices, *extra]


class ScenarioForm(forms.Form):
    """The main form. ``ratings``/``profiles`` are the two lookups' options (a failed rating read
    leaves the field out and keeps ``current_rating``; a failed or too-long profile read makes
    the profile a free-text id). ``locations`` and ``roles`` are the ids the starting-location
    and role selects offer (the form's own location and role rows). ``create`` adds the
    owner."""

    def __init__(
        self,
        data: QueryDict,
        *,
        ratings: RatingChoices,
        profiles: ProfileChoices,
        locations: list[str],
        roles: list[str],
        create: bool,
        current_rating: str | None = None,
    ) -> None:
        super().__init__(data)
        self.create = create
        self.ratings = ratings
        self.profiles = profiles
        self.current_rating = current_rating
        if create:
            self.fields[OWNER] = PrincipalField(
                label="Owner", help_text="The principal the scenario belongs to"
            )
        self.fields.update(_fields())
        self._choices(RATING_FIELD, [(NOT_SET, NONE_LABEL), *ratings.choices], ok=ratings.ok)
        if not ratings.ok:
            del self.fields[RATING_FIELD]
        if profiles.ok:
            choices = _kept(profiles.choices, [data.get(PROFILE_FIELD, "")])
            self._choices(PROFILE_FIELD, [(NOT_SET, NONE_LABEL), *choices], ok=True)
        else:
            self.fields[PROFILE_FIELD] = TextField(
                label="Engine profile id",
                required=False,
                max_length=64,
                help_text=profiles.unavailable_text,
            )
        for name, values in (
            (LOCATION_FIELD, locations),
            (VIEWPOINT_FIELD, roles),
        ):
            options = _kept([(v, v) for v in values], [data.get(name, "")])
            blank = NONE_LABEL if values else (NO_LOCATIONS if name == LOCATION_FIELD else NO_ROLES)
            self._choices(name, [(NOT_SET, blank), *options], ok=True)
            if not values:
                self.fields[name].help_text = blank
        roles_present = self.fields[ROLES_FIELD]
        if isinstance(roles_present, forms.MultipleChoiceField):
            roles_present.choices = _kept([(v, v) for v in roles], data.getlist(ROLES_FIELD))
            if not roles:
                roles_present.help_text = NO_ROLES
        for name in SETTING_ENUMS:
            field_name = f"story_settings_defaults__{name}"
            field = self.fields[field_name]
            if not isinstance(field, forms.ChoiceField):
                continue
            if name in C7_SETTINGS:
                field.choices = [
                    engine_default_choice(C7_DEFAULTS[name]),
                    *kept_choices(SETTING_CHOICES[name], data.get(field_name) or None),
                ]
            else:
                field.choices = _kept(
                    _enum_choices(SETTING_ENUMS[name]), [data.get(field_name, "")], UNKNOWN_SUFFIX
                )
        tie_break = self.fields[TIE_BREAK_FIELD]
        if isinstance(tie_break, forms.ChoiceField):
            tie_break.choices = [
                engine_default_choice(C7_DEFAULTS["simultaneous_tie_break"]),
                *kept_choices(
                    SETTING_CHOICES["simultaneous_tie_break"],
                    data.get(TIE_BREAK_FIELD) or None,
                    tie_break_label,
                ),
            ]
        for name, field in self.fields.items():
            if name != OWNER:
                field.widget.attrs["id"] = field_anchor(field_path(name))

    def _choices(self, name: str, choices: list[tuple[str, str]], *, ok: bool) -> None:
        field = self.fields.get(name)
        if ok and isinstance(field, forms.ChoiceField):
            field.choices = choices

    def clean_starting_state__opening__roles_present(self) -> list[str]:
        value: list[str] = self.cleaned_data.get(ROLES_FIELD) or []
        if len(value) > ROLES_PRESENT_MAX:
            raise forms.ValidationError(f"At most {ROLES_PRESENT_MAX} roles.")
        return value

    def rows(self, names: Iterable[str]) -> list[BoundField]:
        return [self[name] for name in names if name in self.fields]


# --- the editor -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    title: str
    anchor: str
    fields: list[BoundField]


@dataclass(frozen=True)
class Rows:
    """One formset as the template shows it: its title, prefix, forms and whether Add shows."""

    title: str
    prefix: str
    formset: BaseFormSet[Any]
    can_add: bool
    anchor: str
    help: str = ""  # #205: shown under the title


def posted_ids(data: QueryDict, prefix: str, field: str = "id") -> list[str]:
    """The non-blank ``field`` values of a formset's posted rows, de-duplicated, in order."""
    try:
        total = int(data.get(f"{prefix}-TOTAL_FORMS", "0"))
    except ValueError:
        total = 0
    values = (
        (data.get(f"{prefix}-{index}-{field}") or "").strip()
        for index in range(max(0, min(total, ROW_CAPS[prefix] + 1)))
    )
    return list(dict.fromkeys(v for v in values if v))


class ScenarioEditor:
    """The form and its formsets, bound to ``data`` (see the module docstring)."""

    def __init__(
        self,
        data: QueryDict,
        *,
        ratings: RatingChoices,
        profiles: ProfileChoices,
        create: bool,
        current_rating: str | None = None,
        original: Mapping[str, Any] | None = None,
        cast_choices: CastChoices | None = None,
    ) -> None:
        # A companion's slots are the signed original's whatever was posted (#74).
        if cast_choices is not None and cast_choices.companion and original:
            data = pin_companion_slots(data, original)
        # The cast's management forms filled and its audiences re-homed (#73): every part of
        # the editor, and the page it renders, reads the same state.
        data = prepared(data)
        for prefix in (ADJACENCY, CHANNELS):  # #205's row sets: a missing count, no rows
            if f"{prefix}-TOTAL_FORMS" not in data:
                data.update(_management(prefix, 0))
        self.data = data
        self.create = create
        self.original: Mapping[str, Any] = original or {}
        locations = posted_ids(data, LOCATIONS)
        roles = posted_role_ids(data)
        self.form = ScenarioForm(
            data,
            ratings=ratings,
            profiles=profiles,
            locations=locations,
            roles=roles,
            create=create,
            current_rating=current_rating,
        )
        form_kwargs: dict[str, dict[str, Any]] = {
            LOCATIONS: {"locations": locations},
            ADJACENCY: {"locations": locations},
            CHANNELS: {"roles": roles},
        }
        self.formsets: dict[str, BaseFormSet[Any]] = {
            prefix: formset(data, prefix=prefix, form_kwargs=form_kwargs.get(prefix))
            for prefix, formset in FORMSETS.items()
        }
        self.cast = CastForms(
            data,
            original=original or {},
            choices=cast_choices or CastChoices(CharacterChoices([])),
            locations=locations,
        )

    # --- validation ------------------------------------------------------------------------

    def is_valid(self) -> bool:
        results = [
            self.form.is_valid(),
            *(fs.is_valid() for fs in self.formsets.values()),
            self.cast.is_valid(),
        ]
        return all(results)

    def quiet(self) -> None:
        """Show the bound state without validating it (a re-render after a row button)."""
        # Django validates lazily on ``errors``: setting them empty renders the state as is.
        quieted: Any = self.form
        quieted._errors = ErrorDict()
        for formset in self.formsets.values():
            quieted = formset
            quieted._errors = []
            quieted._non_form_errors = ErrorList()
            for form in formset.forms:
                quieted = form
                quieted._errors = ErrorDict()
        self.cast.quiet()

    def disable(self) -> None:
        """Render every input disabled (an archived scenario); the posted state is unchanged."""
        for form in [self.form, *(f for fs in self.formsets.values() for f in fs.forms)]:
            for field in form.fields.values():
                field.widget.attrs["disabled"] = True
        self.cast.disable()

    def sent(self, prefix: str) -> list[forms.Form]:
        """The rows of ``prefix`` that are sent, in order (blank rows dropped): the Engine's list
        index is the index here."""
        return [
            form
            for form in self.formsets[prefix].forms
            if form.has_changed()
            and getattr(form, "cleaned_data", None)
            and not getattr(form, "blank", False)
        ]

    def adjacency(self) -> dict[int, list[forms.Form]]:
        """Sent location ``n`` → the ``adj`` rows of its ``adjacent_to``, entry by entry
        (:func:`adjacency_entries` of the bound rows)."""
        rows = self.formsets[ADJACENCY].forms
        location_ids = [f.cleaned_data.get("id") or "" for f in self.sent(LOCATIONS)]
        sent = {id(form) for form in self.sent(ADJACENCY)}
        pairs = [
            (index, form.cleaned_data.get("from") or "")
            for index, form in enumerate(rows)
            if id(form) in sent
        ]
        return {
            n: [rows[index] for index in indices]
            for n, indices in adjacency_entries(location_ids, pairs).items()
        }

    def _stored_channels(self) -> dict[str, list[str]]:
        """The draft's participants of the first channel with each id."""
        stored: dict[str, list[str]] = {}
        for channel in _get(self.original, CHANNELS_PATH) or []:
            if isinstance(channel, Mapping) and isinstance(channel.get("id"), str):
                stored.setdefault(channel["id"], list(channel.get("participants") or []))
        return stored

    # --- what is sent ----------------------------------------------------------------------

    def write_doc(self) -> dict[str, Any]:
        """The cleaned editor in :func:`draft_to_write`'s shape."""
        data = self.form.cleaned_data
        doc: dict[str, Any] = {}
        for name, value in data.items():
            if name == OWNER:
                continue
            if name == RATING_FIELD:
                doc.setdefault("metadata", {})["content_rating_id"] = value or None
                continue
            if name in BOOLEAN_SETTINGS:
                value = select_bool(value)
            elif name == "world__event_types":
                value = value or None
            elif name in (LOCATION_FIELD, VIEWPOINT_FIELD, PROFILE_FIELD) or name.startswith(
                "story_settings_defaults__"
            ):
                value = value if value not in ("", None) else None
            if name == ROLES_FIELD:  # C9: the draft's order kept, new roles last
                field = self.form.fields[ROLES_FIELD]
                ticked = set(value)
                chosen = [v for v, _ in getattr(field, "choices", []) if v in ticked]
                stored = _get(self.original, "starting_state.opening.roles_present")
                value = keep_order(stored, chosen) if isinstance(stored, list) else chosen
            *groups, leaf = name.split(SEP)
            node = doc
            for group in groups:
                node = node.setdefault(group, {})
            node[leaf] = value
        if RATING_FIELD not in self.form.fields:  # the ratings read failed: kept as it was
            doc.setdefault("metadata", {})["content_rating_id"] = self.form.current_rating
        locations = [f.row() for f in self.sent(LOCATIONS)]  # type: ignore[attr-defined]
        for n, rows in self.adjacency().items():
            locations[n]["adjacent_to"] = [f.entry() for f in rows]  # type: ignore[attr-defined]
        doc.setdefault("world", {})["locations"] = locations
        doc["world"]["entities"] = [f.row() for f in self.sent(ENTITIES)]  # type: ignore[attr-defined]
        doc.setdefault("story_settings_defaults", {})["policies"] = [
            f.policy()  # type: ignore[attr-defined]
            for f in self.sent(POLICIES)
        ]
        for prefix, path in DRIVER_LISTS.items():
            leaf = path.split(".")[1]
            doc.setdefault("drivers", {})[leaf] = [
                f.cleaned_data["text"] for f in self.sent(prefix)
            ]
        doc["cast"] = self.cast.cast_body()
        doc.setdefault("starting_state", {})["seeded_memories"] = self.cast.world_seeds()
        stored = self._stored_channels()
        doc["starting_state"]["channels"] = [
            f.channel(stored)  # type: ignore[attr-defined]
            for f in self.sent(CHANNELS)
        ]
        return doc

    def create_body(self) -> dict[str, Any]:
        """``POST /catalog/scenarios``' body: the non-empty groups and the owner."""
        body: dict[str, Any] = drop_empty(self.write_doc())
        # #206: ``cast.groups`` is exempt (a group's ``[]`` members survive; ``null`` is left out).
        groups = self.cast.cast_body().get("groups") or {}
        groups = {name: members for name, members in groups.items() if members is not None}
        if groups:
            body.setdefault("cast", {})["groups"] = groups
        body[OWNER] = self.form.cleaned_data[OWNER]
        return body

    def edit_patch(self, original: Mapping[str, Any]) -> dict[str, Any]:
        """The merge patch from ``original`` (the draft rendered) to the editor; ``{}`` when
        nothing changed. The cast is not diffed: when :func:`normalize_cast` of it differs from
        the original's, the whole ``cast`` object is sent (#73)."""
        before = dict(original)
        after = self.write_doc()
        original_cast, cast = before.pop("cast", None), after.pop("cast")
        patch = merge_patch.diff(prune(before), prune(after))
        if normalize_cast(original_cast) != normalize_cast(cast):
            patch["cast"] = cast
        return patch

    # --- Engine errors ---------------------------------------------------------------------

    def place_error(
        self, error: EngineError, *, extra: Callable[[str, str, str], bool] | None = None
    ) -> int:
        """Place a 422/413's failures at their fields (rows by the index sent); the rest become
        non-field errors naming the path. ``extra`` (``path, message, code``) is offered each
        failure first and places it elsewhere (#71's media slots) when it answers ``True``.
        Returns how many landed on a field."""
        form = self.form
        if error.code == COMPANION_SLOTS_FIXED:
            details = error.details
            self.cast.place_fixed(details.get("role"), details.get("field"), error.message)
            return 1
        if error.code == "draft_too_large" or error.http_status == 413:
            form.add_error(None, DRAFT_TOO_LARGE)
            return 0
        failures = engine_field_errors(error)
        if not failures:
            if error.code == CAST_LIMIT_EXCEEDED:
                self.cast.place(CAST_LIMIT_PATH, error.message)
                return 1
            if error.code == "rating_retired" and RATING_FIELD in form.fields:
                form.add_error(RATING_FIELD, error.message)
                return 1
            form.add_error(None, f"{error.code}: {error.message}")
            return 0
        placed = 0
        for failure in failures:
            path = failure.path.removeprefix(BODY_PREFIX)
            message = failure.message or error.message
            placed_elsewhere = extra is not None and extra(failure.path, message, error.code)
            if placed_elsewhere or self._place(path, message):
                placed += 1
            else:
                form.add_error(None, f"{path}: {message}")
        return placed

    def validated_quietly(self) -> None:
        """Validate, then drop the errors (#74): a page shown from the saved draft can take
        Engine errors at its fields (``add_error`` needs ``cleaned_data``) without showing the
        Console's own checks of a draft it did not edit."""
        forms_: list[forms.Form] = [
            self.form,
            *(f for fs in self.formsets.values() for f in fs.forms),
            self.cast.limit,
            *(f for fs in self.cast._formsets() for f in fs.forms),
        ]
        for form in forms_:
            form.full_clean()  # even after quiet(), which marks a form as validated
        self.quiet()

    def place_report(self, report: ValidationReport, scenario_id: str) -> int:
        """Place a refused publish's errors (#74: ``publish_invalid``'s report) at the fields
        their locations name, when the entry is this scenario's; the rest stay in the report
        shown beside the form. Returns how many landed."""
        self.validated_quietly()
        placed = 0
        for entry in report.errors:
            location = entry.location
            if not location.field or (location.entry and location.entry != scenario_id):
                continue
            message = f"{entry.code}: {entry.rule}" if entry.rule else entry.code
            try:
                landed = self._place(location.field, message)
            except KeyError:  # a row the Console's own checks refused: the report shows it
                landed = False
            placed += landed
        return placed

    def _place(self, path: str, message: str) -> bool:
        form = self.form
        if self.cast.place(path, message):
            return True
        if path == RATING_PATH and RATING_FIELD in form.fields:
            form.add_error(RATING_FIELD, message)
            return True
        adjacency = _ADJACENCY_PATH.match(path)
        if adjacency:
            return self._place_adjacency(
                int(adjacency["location"]),
                int(adjacency["entry"]),
                adjacency["rest"],
                path,
                message,
            )
        if path == CHANNELS_PATH:
            self.formsets[CHANNELS].non_form_errors().append(message)
            return True
        match = _INDEXED.match(path)
        if match and match["base"] == CHANNELS_PATH:
            return self._place_channel(int(match["index"]), match["rest"], path, message)
        if match:
            base, index, rest = match["base"], int(match["index"]), match["rest"]
            for prefix, row_path in ROW_PATHS.items():
                if base == row_path:
                    return self._place_row(prefix, index, rest, path, message)
            name = base.replace(".", SEP)
            if name in LINE_FIELDS and name in form.fields and not rest:
                form.add_error(name, f"Line {index + 1}: {message}")
                return True
        for name in BOXES:
            box = name.replace(SEP, ".")
            if path == box:
                form.add_error(name, message)
                return True
            if path.startswith((f"{box}.", f"{box}[")) and name in form.fields:
                form.add_error(name, f"{path}: {message}")
                return True
        candidate = path.replace(".", SEP)
        base = (match["base"] if match else path).replace(".", SEP)
        for name in (candidate, base):
            if name in form.fields and name != OWNER:
                form.add_error(name, message if name == candidate else f"{path}: {message}")
                return True
        return False

    def _place_adjacency(
        self, location: int, entry: int, rest: str | None, path: str, message: str
    ) -> bool:
        """``world.locations[n].adjacent_to[a]…`` on the ``adj`` row that produced it."""
        rows = self.adjacency().get(location, [])
        if entry >= len(rows):
            return False
        row = rows[entry]
        if rest is None:
            row.add_error(None, message)
        elif rest in ("location", "barrier"):
            row.add_error("to" if rest == "location" else "barrier", message)
        else:
            row.add_error(None, f"{path}: {message}")
        return True

    def _place_channel(self, index: int, rest: str | None, path: str, message: str) -> bool:
        """``starting_state.channels[c]…`` on the sent ``chan`` row ``c``; a participant's
        error prefixed with its id."""
        sent = self.sent(CHANNELS)
        if index >= len(sent):
            return False
        row = sent[index]
        participant = _PARTICIPANT.match(rest or "")
        if rest is None:
            row.add_error(None, message)
        elif rest in ("id", "participants"):
            row.add_error(rest, message)
        elif participant:
            sent_ids = row.channel(self._stored_channels())["participants"]  # type: ignore[attr-defined]
            p = int(participant["index"])
            row.add_error(
                "participants",
                f"{sent_ids[p]}: {message}" if p < len(sent_ids) else f"{path}: {message}",
            )
        else:
            row.add_error(None, f"{path}: {message}")
        return True

    def _place_row(
        self, prefix: str, index: int, rest: str | None, path: str, message: str
    ) -> bool:
        sent = self.sent(prefix)
        if index >= len(sent):
            return False
        row = sent[index]
        if prefix in DRIVER_LISTS:
            row.add_error("text", message)
            return True
        if rest is None:
            row.add_error(None, message)
            return True
        name = re.split(r"[.\[]", rest, maxsplit=1)[0]
        if name == "type" or rest.startswith("rule"):
            name = "rule"
        if name in row.fields:
            row.add_error(name, message if rest == name else f"{path}: {message}")
        else:
            row.add_error(None, f"{path}: {message}")
        return True

    # --- rendering -------------------------------------------------------------------------

    def sections(self) -> dict[str, Section]:
        return {
            anchor.replace(".", "_"): Section(title, field_anchor(anchor), self.form.rows(names))
            for title, anchor, names in SECTIONS
        }

    def row_sets(self) -> dict[str, Rows]:
        titles = {
            LOCATIONS: "Locations",
            ENTITIES: "Entities",
            POLICIES: "Story policies",
            "hook": "Plot hooks",
            "obj": "Objectives",
            "mile": "Milestones",
            "end": "Endings",
            ADJACENCY: "Adjacent places",
            CHANNELS: "Remote channels",
        }
        anchors = {ADJACENCY: ADJACENCY_ANCHOR, CHANNELS: field_anchor(CHANNELS_PATH)}
        helps = {ADJACENCY: ADJACENCY_HELP, CHANNELS: CHANNELS_HELP}
        return {
            prefix: Rows(
                titles[prefix],
                prefix,
                formset,
                len(formset.forms) < ROW_CAPS[prefix],
                anchors.get(prefix) or field_anchor(ROW_PATHS[prefix]),
                helps.get(prefix, ""),
            )
            for prefix, formset in self.formsets.items()
        }


def apply_row_op(data: QueryDict, op: Op) -> QueryDict:
    """The posted state after a row button (``console.formset_rows.apply_op``), re-templated.
    The cast's rows (#73) are named by their prefixes too: ``role``, ``role-<n>-seed``,
    ``wseed`` and #206's ``grp``."""
    new_rows: dict[str, Mapping[str, list[str]]] = dict(NEW_ROWS) if op.kind == OP_ADD else {}
    if op.kind == OP_ADD:
        cast_row = new_row(data, op.prefix)
        if cast_row is not None:
            new_rows[op.prefix] = cast_row
    caps = {**ROW_CAPS, **row_caps(op.prefix)}
    return retemplated(apply_op(data, op, max_rows=caps, new_rows=new_rows))

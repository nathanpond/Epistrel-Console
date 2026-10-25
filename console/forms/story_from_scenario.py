"""Start a story from a scenario (#75): the step-2 form, built from the scenario's roles.

One dynamic Django form. Each role is a row whose fields are prefixed ``slot-<role_id>-`` (an
NPC role) or ``player-<role_id>-`` (a player role):

- a **locked** NPC role has no fields: its character shows read-only and no slot is sent;
- a **default** or **open** NPC role has a ``mode`` radio: "Keep *name*" (default roles only;
  the slot is omitted), "Catalog character" (``catalog_id`` plus an optional ``version``),
  "Ad-hoc" (``adhoc_name``, ``adhoc_definition``, ``adhoc_age``, ``adhoc_fallback``,
  ``adhoc_deflection``) and, for a role that is not ``required``, "Leave empty" (omitted);
- a **player** role (whatever its fill: a fixed one is the Engine's ``player_role_fixed``) has a
  ``principal`` and a persona: ``persona_mode`` "Saved" (``persona_id``) or "Ad-hoc"
  (``persona_name``, ``persona_gender``, ``persona_age``, ``persona_description``); an optional
  player role adds ``assign`` "No player" (no ``players`` entry).

Overrides are ``ovr-<n>``, one per path of the scenario's ``creation_rules.overridable`` (by
index), typed by :data:`OVERRIDE_TYPES` (copied from the recorded schema's
``StorySettingsDefaults``): enum → select, bool → "(default)/Yes/No", int → number, a plain
string → text, anything else (a list, an object, a subtree, an unknown path) → a JSON box. Each
is pre-filled with the scenario's value and sent, keyed by the path as written, only when it
differs from it: a scalar cleared to "(default)" or blank over a value sends ``null``; a blank
JSON box sends nothing. ``cast.max_characters_at_start`` gets no field of its own: the "Cast
limit" field is sent as the top-level ``max_characters_at_start`` when changed.

The body always carries ``"projection": "full"`` and the pinned ``scenario.version``.
:func:`data_from_body` rebuilds the form's data from a sent body ("Back", and a 422 after
"Create anyway"). :meth:`StoryFromScenarioForm.place_refusal` puts a 422 at the role row,
field or top of the form it names.
"""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from django import forms
from django.forms import BoundField

from console.engine.choices import CharacterChoices, ProfileChoices, RatingChoices
from console.engine.client import EngineError
from console.engine.models import CreationReport, RoleEligibility, Scenario, ScenarioRole
from console.engine_form_errors import engine_field_errors
from console.forms.principal import PrincipalField
from console.ids import is_ulid

PLAYER_KIND = "player"
LOCKED = "locked"
DEFAULT = "default"

KEEP = "keep"
CATALOG = "catalog"
ADHOC = "adhoc"
EMPTY = "empty"
ASSIGN = "player"
NO_PLAYER = "none"
SAVED = "saved"

ADHOC_NAME_MAX = 80
ADHOC_DEFINITION_MAX = 20000
AGE_MIN = 0
AGE_MAX = 150
ADHOC_FALLBACK_MAX = 500
ADHOC_DEFLECTION_MAX = 2000
PERSONA_NAME_MAX = 120
PERSONA_GENDER_MAX = 40
PERSONA_DESCRIPTION_MAX = 4000
VERSION_MAX = 2147483647
CAST_LIMIT_MIN = 1
CAST_LIMIT_MAX = 1000
STORY_NAME_MAX = 200
OVERRIDE_TEXT_MAX = 200
SETUP_SHORT = 200

CAST_LIMIT_PATH = "cast.max_characters_at_start"
BODY_PREFIX = "body."
OVERRIDES_PREFIX = "overrides."
TOP_FIELDS = (
    "content_rating_id",
    "engine_profile_id",
    "max_characters_at_start",
    "name",
    "owner",
    "strict",
)

# The Engine's 422 codes whose place is a role row (by the report's ``location.role``, else
# ``details.role``, else a ``slots.<role>.`` / ``players.<role>.`` field prefix).
ROLE_CODES = frozenset(
    {
        "slot_required",
        "locked_slot_reassigned",
        "player_role_fixed",
        "persona_required",
        "entry_archived",
        "not_published",
        "eligibility_failed",
    }
)
# Role-less codes and the field they belong beside (``None``: the top of the form).
CODE_FIELDS: dict[str, str | None] = {
    "cast_limit_exceeded": "max_characters_at_start",
    "rating_check_failed": "content_rating_id",
    "rating_retired": "content_rating_id",
    "profile_deleted": "engine_profile_id",
    "budget_below_safety_minimum": "engine_profile_id",
    "viewpoint_unresolved": None,
    "creation_conflicts": None,
    "guardrail_violation": None,
    "seed_unnormalizable": None,
}
OVERRIDE_NOT_ALLOWED = "override_not_allowed"
VALIDATION_ERROR = "validation_error"

# A slot's / player's Engine sub-path → the row's field.
SLOT_ALIASES = {
    "catalog_id": "catalog_id",
    "version": "version",
    "ad_hoc": "mode",
    "ad_hoc.name": "adhoc_name",
    "ad_hoc.definition": "adhoc_definition",
    "ad_hoc.true_age": "adhoc_age",
    "ad_hoc.safe_fallback_text": "adhoc_fallback",
    "ad_hoc.deflection_style": "adhoc_deflection",
}
PLAYER_ALIASES = {
    "principal": "principal",
    "persona": "persona_mode",
    "persona.id": "persona_id",
    "persona.ad_hoc": "persona_mode",
    "persona.ad_hoc.name": "persona_name",
    "persona.ad_hoc.gender": "persona_gender",
    "persona.ad_hoc.age": "persona_age",
    "persona.ad_hoc.description": "persona_description",
}

OverrideKind = Literal["enum", "bool", "int", "text", "json"]


@dataclass(frozen=True)
class OverrideType:
    kind: OverrideKind
    options: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


SETTINGS = "story_settings_defaults."
# ``StorySettingsDefaults``' scalar fields, as the recorded schema types them. ``policies`` and
# ``required_providers`` (lists), the subtree itself and any other path get a JSON box.
OVERRIDE_TYPES: dict[str, OverrideType] = {
    f"{SETTINGS}allow_new_arrivals": OverrideType("bool"),
    f"{SETTINGS}ambiguous_retcon_timing": OverrideType(
        "enum", ("include_if_possible", "ask_author")
    ),
    f"{SETTINGS}audit_retention_days": OverrideType("int", minimum=1, maximum=3650),
    f"{SETTINGS}backfill_conflict_policy": OverrideType(
        "enum", ("last_mention", "first_mention", "ask")
    ),
    f"{SETTINGS}default_output_viewpoint": OverrideType("text"),
    f"{SETTINGS}interaction_mode": OverrideType("enum", ("sequential", "simultaneous")),
    f"{SETTINGS}player_authority": OverrideType("enum", ("own_character", "co_author")),
    f"{SETTINGS}turn_concurrency": OverrideType("enum", ("reject", "queue")),
}
JSON_TYPE = OverrideType("json")
BOOL_CHOICES = [("", "(default)"), ("true", "Yes"), ("false", "No")]


# --- what the page looked up ----------------------------------------------------------------


@dataclass(frozen=True)
class RoleName:
    """A locked or default role's character as the page names it: its card's name, or the id
    with the read's ``error``; ``archived`` from the card's status."""

    catalog_id: str
    version: int | None
    text: str
    error: str = ""
    archived: bool = False


@dataclass(frozen=True)
class PersonaOptions:
    """A player row's persona select: ``(id, "name (age) …<last 6>")``, or the ``note`` saying
    why there is no select (the read failed, too many, no valid principal)."""

    choices: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.note


@dataclass(frozen=True)
class Lookups:
    """Everything the form's selects and labels need (read by the view)."""

    ratings: RatingChoices
    profiles: ProfileChoices
    characters: CharacterChoices
    personas: Mapping[str, PersonaOptions]  # by player role id
    role_names: Mapping[str, RoleName]  # by role id (locked and default roles)


@dataclass(frozen=True)
class OverrideRow:
    index: int
    path: str
    name: str  # the form field, ``ovr-<index>``
    type: OverrideType
    current: Any  # the scenario's value at the path (None when absent)


@dataclass(frozen=True)
class RoleRow:
    """One role as the page renders it."""

    role: ScenarioRole
    is_player: bool
    character: RoleName | None
    eligibility: str
    setup_short: str
    setup_cut: bool
    fields: dict[str, BoundField]
    errors: list[str]
    persona_note: str = ""


# --- helpers -----------------------------------------------------------------------------------


def slot_prefix(role_id: str) -> str:
    return f"slot-{role_id}-"


def player_prefix(role_id: str) -> str:
    return f"player-{role_id}-"


def is_player(role: ScenarioRole) -> bool:
    return role.kind == PLAYER_KIND


def age_text(age_range: object) -> str:
    """An eligibility age range: "any", "18+", "up to 12" or "12\u201317"."""
    low = high = None
    if isinstance(age_range, list | tuple) and len(age_range) == 2:
        low, high = age_range
    if not isinstance(low, int) and not isinstance(high, int):
        return "any"
    if not isinstance(high, int):
        return f"{low}+"
    if not isinstance(low, int):
        return f"up to {high}"
    return f"{low}\u2013{high}"


def eligibility_text(eligibility: RoleEligibility | None, rating_names: Mapping[str, str]) -> str:
    """ "labels: a, b · ratings: Teen · ages: 18+"; nothing set: "anyone"."""
    if eligibility is None:
        return "anyone"
    parts: list[str] = []
    if eligibility.labels:
        parts.append(f"labels: {', '.join(eligibility.labels)}")
    if eligibility.content_ratings:
        names = (rating_names.get(r, r) for r in eligibility.content_ratings)
        parts.append(f"ratings: {', '.join(names)}")
    ages = age_text(eligibility.age_range)
    if ages != "any":
        parts.append(f"ages: {ages}")
    return " · ".join(parts) if parts else "anyone"


def scenario_value(scenario: Scenario, path: str) -> Any:
    """The scenario's value at a dotted path, ``None`` when absent."""
    node: Any = asdict(scenario)
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def override_rows(scenario: Scenario) -> list[OverrideRow]:
    """The override fields: one per ``creation_rules.overridable`` path but the cast limit (the
    "Cast limit" field covers it), named by the path's index."""
    return [
        OverrideRow(
            index,
            path,
            f"ovr-{index}",
            OVERRIDE_TYPES.get(path, JSON_TYPE),
            scenario_value(scenario, path),
        )
        for index, path in enumerate(scenario.creation_rules.overridable)
        if path != CAST_LIMIT_PATH
    ]


def _strict_json(text: str) -> Any:
    def refuse(name: str) -> Any:
        raise ValueError(f"{name} is not valid JSON")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate key {key!r}")
            out[key] = value
        return out

    return json.loads(text, parse_constant=refuse, object_pairs_hook=pairs)


def _shown(kind: OverrideKind, value: Any) -> Any:
    """A value as its override widget shows it."""
    if kind == "bool":
        return "" if value is None else ("true" if value else "false")
    if kind == "json":
        return "" if value is None else json.dumps(value, indent=2, ensure_ascii=False)
    return "" if value is None else value


def _lf(value: object) -> str:
    return value.replace("\r\n", "\n") if isinstance(value, str) else ""


# --- the form ---------------------------------------------------------------------------------


class StoryFromScenarioForm(forms.Form):
    """Step 2's form for ``scenario`` (the pinned version) with ``lookups``' options."""

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        scenario: Scenario,
        lookups: Lookups,
        acting: str,
    ) -> None:
        super().__init__(data)
        self.scenario = scenario
        self.lookups = lookups
        self.overrides = override_rows(scenario)
        self.role_errors: dict[str, list[str]] = {}
        self.top_errors: list[str] = []
        for role in scenario.cast.roles:
            if is_player(role):
                self._player_fields(role, acting)
            elif role.fill != LOCKED:
                self._slot_fields(role)
        self._top_fields(acting)

    # --- building ---------------------------------------------------------------------------

    def _slot_fields(self, role: ScenarioRole) -> None:
        p = slot_prefix(role.id)
        modes: list[tuple[str, str]] = []
        if role.fill == DEFAULT:
            name = self.lookups.role_names.get(role.id)
            modes.append((KEEP, f"Keep {name.text if name else 'the default character'}"))
        modes += [(CATALOG, "Catalog character"), (ADHOC, "Ad-hoc")]
        if not role.required:
            modes.append((EMPTY, "Leave empty"))
        initial = KEEP if role.fill == DEFAULT else (CATALOG if role.required else EMPTY)
        self.fields[f"{p}mode"] = forms.ChoiceField(
            choices=modes, initial=initial, widget=forms.RadioSelect, label="Fill with"
        )
        characters = self.lookups.characters
        if characters.ok:
            current = (self.data.get(f"{p}catalog_id") or "") if self.is_bound else ""
            choices = [("", "—"), *characters.choices]
            if current and current not in {v for v, _ in choices}:
                choices.append((current, f"{current} (not listed)"))
            widget: forms.Widget = forms.Select(choices=choices)
        else:
            widget = forms.TextInput(attrs={"placeholder": "catalog character id"})
        self.fields[f"{p}catalog_id"] = forms.CharField(
            required=False,
            label="Catalog character",
            widget=widget,
            help_text="" if characters.ok else characters.unavailable_text,
        )
        self.fields[f"{p}version"] = forms.IntegerField(
            required=False,
            min_value=1,
            max_value=VERSION_MAX,
            label="Version",
            help_text="blank = latest published",
        )
        self.fields[f"{p}adhoc_name"] = forms.CharField(
            required=False, max_length=ADHOC_NAME_MAX, label="Name"
        )
        self.fields[f"{p}adhoc_definition"] = forms.CharField(
            required=False,
            label="Definition",
            widget=forms.Textarea(attrs={"rows": 6, "maxlength": ADHOC_DEFINITION_MAX}),
        )
        self.fields[f"{p}adhoc_age"] = forms.IntegerField(
            required=False, min_value=AGE_MIN, max_value=AGE_MAX, label="True age"
        )
        self.fields[f"{p}adhoc_fallback"] = forms.CharField(
            required=False,
            max_length=ADHOC_FALLBACK_MAX,
            label="Safe fallback text",
            help_text="optional",
        )
        self.fields[f"{p}adhoc_deflection"] = forms.CharField(
            required=False,
            label="Deflection style",
            help_text="optional",
            widget=forms.Textarea(attrs={"rows": 2, "maxlength": ADHOC_DEFLECTION_MAX}),
        )

    def _player_fields(self, role: ScenarioRole, acting: str) -> None:
        p = player_prefix(role.id)
        if not role.required:
            self.fields[f"{p}assign"] = forms.ChoiceField(
                choices=[(ASSIGN, "Assign a player"), (NO_PLAYER, "No player")],
                initial=ASSIGN,
                widget=forms.RadioSelect,
                label="Player",
            )
        self.fields[f"{p}principal"] = PrincipalField(
            required=False, label="Principal", initial=acting
        )
        options = self.lookups.personas.get(role.id, PersonaOptions())
        initial_mode = SAVED if (options.choices or not options.ok) else ADHOC
        self.fields[f"{p}persona_mode"] = forms.ChoiceField(
            choices=[(SAVED, "Saved persona"), (ADHOC, "Ad-hoc persona")],
            initial=initial_mode,
            widget=forms.RadioSelect,
            label="Persona",
        )
        if options.ok:
            current = (self.data.get(f"{p}persona_id") or "") if self.is_bound else ""
            choices = [("", "—"), *options.choices]
            if current and current not in {v for v, _ in choices}:
                choices.append((current, f"{current} (not listed)"))
            widget: forms.Widget = forms.Select(choices=choices)
        else:
            widget = forms.TextInput(attrs={"placeholder": "persona id"})
        self.fields[f"{p}persona_id"] = forms.CharField(
            required=False, label="Saved persona", widget=widget, help_text=options.note
        )
        self.fields[f"{p}persona_name"] = forms.CharField(
            required=False, max_length=PERSONA_NAME_MAX, label="Name"
        )
        self.fields[f"{p}persona_gender"] = forms.CharField(
            required=False, max_length=PERSONA_GENDER_MAX, label="Gender", help_text="optional"
        )
        self.fields[f"{p}persona_age"] = forms.IntegerField(
            required=False, min_value=AGE_MIN, max_value=AGE_MAX, label="Age"
        )
        self.fields[f"{p}persona_description"] = forms.CharField(
            required=False,
            label="Description",
            widget=forms.Textarea(attrs={"rows": 4, "maxlength": PERSONA_DESCRIPTION_MAX}),
        )

    def _override_field(self, row: OverrideRow) -> forms.Field:
        kind = row.type.kind
        label = row.path
        initial = _shown(kind, row.current)
        if kind == "bool":
            return forms.ChoiceField(
                choices=BOOL_CHOICES, required=False, initial=initial, label=label
            )
        if kind == "enum":
            options = [(v, v) for v in row.type.options]
            if isinstance(row.current, str) and row.current not in row.type.options:
                options.append((row.current, f"{row.current} (unknown)"))
            return forms.ChoiceField(
                choices=[("", "(default)"), *options],
                required=False,
                initial=initial,
                label=label,
            )
        if kind == "int":
            return forms.IntegerField(
                required=False,
                min_value=row.type.minimum,
                max_value=row.type.maximum,
                initial=initial,
                label=label,
            )
        if kind == "text":
            return forms.CharField(
                required=False, max_length=OVERRIDE_TEXT_MAX, initial=initial, label=label
            )
        return forms.CharField(
            required=False,
            initial=initial,
            label=label,
            help_text="JSON; blank = the scenario's value",
            widget=forms.Textarea(attrs={"rows": 4, "cols": 80, "spellcheck": "false"}),
        )

    def _top_fields(self, acting: str) -> None:
        for row in self.overrides:
            self.fields[row.name] = self._override_field(row)
        ratings = self.lookups.ratings
        if ratings.ok:
            self.fields["content_rating_id"] = forms.ChoiceField(
                choices=[("", "(scenario's rating)"), *ratings.choices],
                required=False,
                label="Content rating",
            )
        profiles = self.lookups.profiles
        if profiles.ok:
            self.fields["engine_profile_id"] = forms.ChoiceField(
                choices=[("", "(Engine default)"), *profiles.choices],
                required=False,
                label="Engine profile",
            )
        else:
            self.fields["engine_profile_id"] = forms.CharField(
                required=False,
                max_length=64,
                label="Engine profile id",
                help_text=profiles.unavailable_text,
            )
        self.fields["max_characters_at_start"] = forms.IntegerField(
            required=False,
            min_value=CAST_LIMIT_MIN,
            max_value=CAST_LIMIT_MAX,
            initial=self.scenario.cast.max_characters_at_start,
            label="Cast limit",
            help_text="max_characters_at_start; blank = the scenario's or the deployment's",
        )
        self.fields["name"] = forms.CharField(
            required=False,
            max_length=STORY_NAME_MAX,
            label="Story name",
            widget=forms.TextInput(
                attrs={"placeholder": self.scenario.public_card.name or "(scenario's name)"}
            ),
        )
        self.fields["owner"] = PrincipalField(label="Owner", initial=acting)
        self.fields["strict"] = forms.BooleanField(
            required=False, label="Strict (refuse on conflicts)"
        )

    # --- validating ---------------------------------------------------------------------------

    def _value(self, name: str) -> Any:
        return self.cleaned_data.get(name)

    def _need(self, name: str, message: str) -> None:
        value = self._value(name)
        if (value is None or value == "") and name not in self.errors:
            self.add_error(name, message)

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        for role in self.scenario.cast.roles:
            if is_player(role):
                self._clean_player(role)
            elif role.fill != LOCKED:
                self._clean_slot(role)
        for row in self.overrides:
            if row.type.kind == "json":
                text = (self._value(row.name) or "").strip()
                if text:
                    try:
                        _strict_json(text)
                    except ValueError as exc:
                        self.add_error(row.name, f"Enter valid JSON: {exc}")
        return cleaned

    def _clean_slot(self, role: ScenarioRole) -> None:
        p = slot_prefix(role.id)
        mode = self._value(f"{p}mode")
        if mode == CATALOG:
            value = (self._value(f"{p}catalog_id") or "").strip()
            if not value:
                self._need(f"{p}catalog_id", "Choose a catalog character.")
            elif not is_ulid(value):
                self.add_error(f"{p}catalog_id", "Enter a catalog character id (a ULID).")
        elif mode == ADHOC:
            self._need(f"{p}adhoc_name", "Enter a name.")
            self._need(f"{p}adhoc_definition", "Enter a definition.")
            self._need(f"{p}adhoc_age", "Enter a true age.")
            definition = _lf(self._value(f"{p}adhoc_definition"))
            if len(definition) > ADHOC_DEFINITION_MAX:
                self.add_error(
                    f"{p}adhoc_definition",
                    f"Ensure this value has at most {ADHOC_DEFINITION_MAX} characters "
                    f"(it has {len(definition)}).",
                )
            deflection = _lf(self._value(f"{p}adhoc_deflection"))
            if len(deflection) > ADHOC_DEFLECTION_MAX:
                self.add_error(
                    f"{p}adhoc_deflection",
                    f"Ensure this value has at most {ADHOC_DEFLECTION_MAX} characters "
                    f"(it has {len(deflection)}).",
                )

    def _clean_player(self, role: ScenarioRole) -> None:
        p = player_prefix(role.id)
        if self._value(f"{p}assign") == NO_PLAYER:
            return
        if not self._value(f"{p}principal") and f"{p}principal" not in self.errors:
            self.add_error(f"{p}principal", "Enter the player's principal.")
        if self._value(f"{p}persona_mode") == SAVED:
            value = (self._value(f"{p}persona_id") or "").strip()
            if not value:
                self._need(f"{p}persona_id", "Choose a saved persona, or switch to ad-hoc.")
            elif not is_ulid(value):
                self.add_error(f"{p}persona_id", "Enter a persona id (a ULID).")
        else:
            self._need(f"{p}persona_name", "Enter a name.")
            self._need(f"{p}persona_age", "Enter an age.")
            self._need(f"{p}persona_description", "Enter a description.")
            description = _lf(self._value(f"{p}persona_description"))
            if len(description) > PERSONA_DESCRIPTION_MAX:
                self.add_error(
                    f"{p}persona_description",
                    f"Ensure this value has at most {PERSONA_DESCRIPTION_MAX} characters "
                    f"(it has {len(description)}).",
                )

    # --- the body -------------------------------------------------------------------------------

    def _slot(self, role: ScenarioRole) -> dict[str, Any] | None:
        p = slot_prefix(role.id)
        mode = self._value(f"{p}mode")
        if mode == CATALOG:
            slot: dict[str, Any] = {"catalog_id": self._value(f"{p}catalog_id").strip()}
            if self._value(f"{p}version") is not None:
                slot["version"] = self._value(f"{p}version")
            return slot
        if mode == ADHOC:
            ad_hoc: dict[str, Any] = {
                "name": self._value(f"{p}adhoc_name"),
                "definition": _lf(self._value(f"{p}adhoc_definition")),
                "true_age": self._value(f"{p}adhoc_age"),
            }
            if self._value(f"{p}adhoc_fallback"):
                ad_hoc["safe_fallback_text"] = self._value(f"{p}adhoc_fallback")
            if self._value(f"{p}adhoc_deflection"):
                ad_hoc["deflection_style"] = _lf(self._value(f"{p}adhoc_deflection"))
            return {"ad_hoc": ad_hoc}
        return None  # keep (a default role) or leave empty: the slot is omitted

    def _player(self, role: ScenarioRole) -> dict[str, Any] | None:
        p = player_prefix(role.id)
        if self._value(f"{p}assign") == NO_PLAYER:
            return None
        persona: dict[str, Any]
        if self._value(f"{p}persona_mode") == SAVED:
            persona = {"id": self._value(f"{p}persona_id").strip()}
        else:
            fields: dict[str, Any] = {
                "name": self._value(f"{p}persona_name"),
                "age": self._value(f"{p}persona_age"),
                "description": _lf(self._value(f"{p}persona_description")),
            }
            if self._value(f"{p}persona_gender"):
                fields["gender"] = self._value(f"{p}persona_gender")
            persona = {"ad_hoc": fields}
        return {"principal": self._value(f"{p}principal"), "persona": persona}

    def _override(self, row: OverrideRow) -> tuple[bool, Any]:
        """``(send, value)`` for one override field."""
        raw = self._value(row.name)
        kind = row.type.kind
        if kind == "json":
            text = (raw or "").strip()
            if not text:
                return False, None
            value = _strict_json(text)
        elif kind == "bool":
            value = None if not raw else raw == "true"
        else:
            value = raw if raw not in ("", None) else None
        return value != row.current, value

    def body(self) -> dict[str, Any]:
        """The ``StoryFromScenario`` body for a valid form, scenario version pinned."""
        scenario = self.scenario
        body: dict[str, Any] = {
            "scenario": {"id": scenario.id, "version": scenario.version},
            "projection": "full",
        }
        slots: dict[str, Any] = {}
        players: dict[str, Any] = {}
        for role in scenario.cast.roles:
            if is_player(role):
                player = self._player(role)
                if player is not None:
                    players[role.id] = player
            elif role.fill != LOCKED:
                slot = self._slot(role)
                if slot is not None:
                    slots[role.id] = slot
        if slots:
            body["slots"] = slots
        if players:
            body["players"] = players
        overrides: dict[str, Any] = {}
        for row in self.overrides:
            send, value = self._override(row)
            if send:
                overrides[row.path] = value
        if overrides:
            body["overrides"] = overrides
        for name in ("content_rating_id", "engine_profile_id", "name"):
            value = (self._value(name) or "").strip() if name in self.fields else ""
            if value:
                body[name] = value
        limit = self._value("max_characters_at_start")
        if limit is not None and limit != scenario.cast.max_characters_at_start:
            body["max_characters_at_start"] = limit
        body["owner"] = self._value("owner")
        body["strict"] = bool(self._value("strict"))
        return body

    # --- rendering ------------------------------------------------------------------------------

    def role_rows(self, rating_names: Mapping[str, str]) -> list[RoleRow]:
        rows: list[RoleRow] = []
        for role in self.scenario.cast.roles:
            player = is_player(role)
            prefix = player_prefix(role.id) if player else slot_prefix(role.id)
            bound = {
                name.removeprefix(prefix): self[name]
                for name in self.fields
                if name.startswith(prefix)
            }
            setup = role.setup_description or ""
            options = self.lookups.personas.get(role.id) if player else None
            rows.append(
                RoleRow(
                    role=role,
                    is_player=player,
                    character=self.lookups.role_names.get(role.id),
                    eligibility=eligibility_text(role.eligibility, rating_names),
                    setup_short=setup[:SETUP_SHORT],
                    setup_cut=len(setup) > SETUP_SHORT,
                    fields=bound,
                    errors=self.role_errors.get(role.id, []),
                    persona_note=options.note if options is not None else "",
                )
            )
        return rows

    def override_fields(self) -> list[tuple[OverrideRow, BoundField]]:
        return [(row, self[row.name]) for row in self.overrides]

    # --- placing a refusal ----------------------------------------------------------------------

    def _role_ids(self) -> set[str]:
        return {role.id for role in self.scenario.cast.roles}

    def _role_error(self, role_id: str, message: str) -> None:
        if role_id in self._role_ids():
            self.role_errors.setdefault(role_id, []).append(message)
        else:
            self.top_errors.append(f"Role {role_id}: {message}")

    def _override_field_for(self, path: str) -> str | None:
        for prefix in (BODY_PREFIX + OVERRIDES_PREFIX, OVERRIDES_PREFIX, ""):
            if path.startswith(prefix):
                rest = path[len(prefix) :]
                for row in self.overrides:
                    if row.path == rest:
                        return row.name
        return None

    def _field_for(self, path: str) -> tuple[str | None, str | None]:
        """``(field name, role id)`` an Engine path names on this form (either may be
        ``None``)."""
        path = path.removeprefix(BODY_PREFIX)
        if path in TOP_FIELDS and path in self.fields:
            return path, None
        override = self._override_field_for(path)
        if override is not None:
            return override, None
        for root, prefix, aliases in (
            ("slots.", slot_prefix, SLOT_ALIASES),
            ("players.", player_prefix, PLAYER_ALIASES),
        ):
            if path.startswith(root):
                role_id, _, rest = path[len(root) :].partition(".")
                name = f"{prefix(role_id)}{aliases.get(rest, '')}" if rest in aliases else None
                return (name if name in self.fields else None), role_id
        return None, None

    def _place_at(self, path: str, message: str) -> None:
        name, role_id = self._field_for(path)
        if name is not None:
            self.add_error(name, message)
        elif role_id is not None:
            self._role_error(role_id, f"{path.removeprefix(BODY_PREFIX)}: {message}")
        else:
            self.top_errors.append(f"{path.removeprefix(BODY_PREFIX)}: {message}")

    def place_refusal(self, exc: EngineError) -> CreationReport | None:
        """Put a 422 refusal where it belongs on this (validated) form; returns its
        ``details.report`` (shown below the form), if any.

        A role code lands on the role rows its report errors name (``location.role``), else on
        ``details.role``, else on the role a ``slots.<role>.`` / ``players.<role>.`` field names;
        ``override_not_allowed`` on its override field; a ``validation_error`` at
        ``details.field``; the other codes beside their field (:data:`CODE_FIELDS`) or at the
        top. A role or path the form lacks lands at the top, naming it."""
        details = exc.details
        report = CreationReport.from_json(details.get("report"))
        headline = f"The Engine refused the story: {exc.code} — {exc.message}"
        self.top_errors.append(headline)
        code = exc.code
        errors = [e for e in (report.errors or []) if e.code] if report is not None else []
        if code == VALIDATION_ERROR:
            failures = engine_field_errors(exc)
            for failure in failures:
                self._place_at(failure.path, failure.message or exc.message)
            if not failures:
                self.top_errors.append(exc.message)
            return report
        if code == OVERRIDE_NOT_ALLOWED:
            placed = False
            for entry in errors:
                path = entry.location.field or ""
                name = self._override_field_for(path) if path else None
                if name is not None:
                    self.add_error(name, f"{entry.code}: {entry.rule or 'not overridable'}")
                    placed = True
                elif path:
                    self.top_errors.append(f"{entry.code}: {path} is not overridable")
                    placed = True
            field_path = details.get("field")
            if not placed and isinstance(field_path, str):
                self._place_at(field_path, exc.message)
            return report
        if code in ROLE_CODES:
            placed = False
            for entry in errors:
                if entry.location.role:
                    text = entry.text or entry.rule or exc.message
                    self._role_error(entry.location.role, f"{entry.code}: {text}")
                    placed = True
            role = details.get("role")
            field_path = details.get("field")
            if not placed and isinstance(role, str) and role:
                self._role_error(role, f"{code}: {exc.message}")
            elif not placed and isinstance(field_path, str):
                self._place_at(field_path, f"{code}: {exc.message}")
            return report
        target = CODE_FIELDS.get(code)
        if target is not None and target in self.fields:
            self.add_error(target, f"{code}: {exc.message}")
        return report


# --- a sent body back to form data ----------------------------------------------------------


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def data_from_body(body: Mapping[str, Any], scenario: Scenario) -> dict[str, str]:
    """The form data that sends ``body`` again (bar a fresh key): "Back" from the not-created
    page, and a 422 in reply to "Create anyway", rebuild step 2 with it."""
    data: dict[str, str] = {}
    slots = body.get("slots") or {}
    players = body.get("players") or {}
    for role in scenario.cast.roles:
        if is_player(role):
            p = player_prefix(role.id)
            player = players.get(role.id)
            if not isinstance(player, dict):
                data[f"{p}assign"] = NO_PLAYER
                data[f"{p}persona_mode"] = ADHOC
                continue
            data[f"{p}assign"] = ASSIGN
            data[f"{p}principal"] = _text(player.get("principal"))
            persona = player.get("persona") or {}
            if isinstance(persona, dict) and "id" in persona:
                data[f"{p}persona_mode"] = SAVED
                data[f"{p}persona_id"] = _text(persona["id"])
            else:
                fields = persona.get("ad_hoc") if isinstance(persona, dict) else None
                fields = fields if isinstance(fields, dict) else {}
                data[f"{p}persona_mode"] = ADHOC
                data[f"{p}persona_name"] = _text(fields.get("name"))
                data[f"{p}persona_gender"] = _text(fields.get("gender"))
                data[f"{p}persona_age"] = _text(fields.get("age"))
                data[f"{p}persona_description"] = _text(fields.get("description"))
        elif role.fill != LOCKED:
            p = slot_prefix(role.id)
            slot = slots.get(role.id)
            if not isinstance(slot, dict):
                if role.fill == DEFAULT:
                    data[f"{p}mode"] = KEEP
                else:
                    data[f"{p}mode"] = CATALOG if role.required else EMPTY
            elif isinstance(slot.get("ad_hoc"), dict):
                ad_hoc = slot["ad_hoc"]
                data[f"{p}mode"] = ADHOC
                data[f"{p}adhoc_name"] = _text(ad_hoc.get("name"))
                data[f"{p}adhoc_definition"] = _text(ad_hoc.get("definition"))
                data[f"{p}adhoc_age"] = _text(ad_hoc.get("true_age"))
                data[f"{p}adhoc_fallback"] = _text(ad_hoc.get("safe_fallback_text"))
                data[f"{p}adhoc_deflection"] = _text(ad_hoc.get("deflection_style"))
            else:
                data[f"{p}mode"] = CATALOG
                data[f"{p}catalog_id"] = _text(slot.get("catalog_id"))
                data[f"{p}version"] = _text(slot.get("version"))
    overrides = body.get("overrides") or {}
    for row in override_rows(scenario):
        value = overrides.get(row.path, row.current)
        data[row.name] = _text(_shown(row.type.kind, value))
    for name in ("content_rating_id", "engine_profile_id", "name", "owner"):
        data[name] = _text(body.get(name))
    limit = body.get("max_characters_at_start", scenario.cast.max_characters_at_start)
    data["max_characters_at_start"] = _text(limit)
    if body.get("strict"):
        data["strict"] = "on"
    return data

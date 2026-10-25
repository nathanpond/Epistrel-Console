"""The scenario editor's Cast and World seeds (#73), composed by #68's
:class:`~console.forms.scenario.ScenarioEditor`: one form, one Save, one PATCH.

Row prefixes (#64 convention 6; renumbered on every re-render by ``console.formset_rows``):

- ``role-<n>-``: a role (:class:`RoleForm`), in the ``role`` formset;
- ``role-<n>-seed-<m>-``: a seed of role ``n`` (:class:`SeedForm`), in that role's own seed
  formset (management form ``role-<n>-seed-TOTAL_FORMS``);
- ``wseed-<m>-``: a world seed (``starting_state.seeded_memories``).

:class:`CastForms` builds the write: :meth:`~CastForms.cast_body` is the whole ``cast`` object
(the editor sends it whole when :func:`normalize_cast` of it differs from the signed original's)
and :meth:`~CastForms.world_seeds` the world seeds (sent inside ``starting_state``, whose list
the merge patch replaces whole). A row with blank id, label and setup and no seeds is dropped,
as is a seed with blank text; the sent-index → row map those drops leave is what
:meth:`~CastForms.place` reads an Engine error's indexes through.

A seed keeps its Engine id (a hidden field) only when the signed original holds that id (the
set is global: role and world seeds) and only on its first occurrence; any other posted id is
stripped, so the Engine mints a new one.

The save rules (duplicate role ids, the player role's count and fill, character against fill,
undeclared roles and locations) are not checked here: the Engine's 422 is what the operator
sees, every ``details.errors`` entry placed at its row. Only bounds, JSON box types and the
required text of a non-blank row are checked before the call.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.forms.utils import ErrorDict, ErrorList
from django.http import QueryDict

from console.engine.choices import CharacterChoices
from console.engine.models import ScenarioCast, ScenarioRole, Seed
from console.forms.catalog_character import LabelsField, TextField, norm_labels, norm_text
from console.forms.fields import JsonBoxField
from console.reports import field_anchor

ROLE = "role"
SEED = "seed"
WSEED = "wseed"
MAX_FIELD = "cast__max_characters_at_start"
MAX_PATH = "cast.max_characters_at_start"
CAST_PATH = "cast"
WORLD_SEEDS_PATH = "starting_state.seeded_memories"
WORLD = "world"
UNSET = ""
UNSET_LABEL = "(unset)"
NOT_SET_LABEL = "(not set)"
NONE_LABEL = "(none)"
CHOOSE_LABEL = "— choose a character —"
MISSING_SUFFIX = " (missing)"
NEW_SEED = "new"

# The Engine's bounds (``ScenarioCast``, ``ScenarioRole``, ``Seed``, ``RoleEligibility``).
ROLES_MAX = 32
SEEDS_MAX = 200  # per role, and the world seeds
AUDIENCE_MAX = 40
RELATIONSHIPS_MAX = 64
TEXT_MAX = 8000
LABEL_MAX = 120
ID_MAX = 40
CAST_MIN, CAST_MAX = 1, 1000
AGE_MIN, AGE_MAX = 0, 150
ELIGIBILITY_LABELS_MAX = 30
ELIGIBILITY_RATINGS_MAX = 50
VERSION_MIN, VERSION_MAX = 1, 2_147_483_647

# The schema's enums (a test compares them with ``openapi.json``).
ROLE_KINDS = ("npc", "player")
FILLS = ("open", "default", "locked")
WITH_CHARACTER = ("default", "locked")
PARTICIPATION = ("primary", "reactive", "silent")
DISCLOSURES = ("open", "guarded", "secret")
SEED_KINDS = ("canon", "belief", "nested", "scheduled")

ROLE_ID = re.compile(r"^[a-z0-9_]{1,40}$")
_SEED_PREFIX = re.compile(rf"^{ROLE}-(?P<role>\d+)-{SEED}$")
_INDEXED = re.compile(r"^\[(?P<index>\d+)\](?:\.(?P<rest>.+))?$")
CHARACTER_ID = "character__catalog_id"
CHARACTER_VERSION = "character__version"
AGE_MIN_FIELD = "eligibility__age_range_min"
AGE_MAX_FIELD = "eligibility__age_range_max"
LABELS_FIELD = "eligibility__labels"
RATINGS_FIELD = "eligibility__content_ratings"
ROLE_BOXES: dict[str, tuple[type, Any]] = {
    "relationships": (list, []),
    "name_overrides": (dict, None),
    "state_initial_values": (dict, None),
}


def seed_prefix(role_index: int) -> str:
    return f"{ROLE}-{role_index}-{SEED}"


def _blank(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _choices(values: Iterable[str], blank: str) -> list[tuple[str, str]]:
    return [(UNSET, blank), *((v, v) for v in values)]


def _kept(choices: list[tuple[str, str]], keep: Iterable[str]) -> list[tuple[str, str]]:
    """``choices`` plus each of ``keep`` it lacks, labelled "<value> (missing)"."""
    known = {value for value, _ in choices}
    extra = [(v, f"{v}{MISSING_SUFFIX}") for v in dict.fromkeys(keep) if v and v not in known]
    return [*choices, *extra]


# --- the draft as the form sees it ----------------------------------------------------------------


def seed_to_write(seed: Seed) -> dict[str, Any]:
    """A seed in the write shape: every key present (unset ``None``), ``id`` only when it has
    one."""
    out: dict[str, Any] = {"id": seed.id} if seed.id else {}
    out.update(
        {
            "text": norm_text(seed.text),
            "holder": seed.holder,
            "audience": list(seed.audience) if seed.audience else None,
            "disclosure": seed.disclosure,
            "kind": seed.kind,
            "valid_time": seed.valid_time,
        }
    )
    return out


def _age_range(value: object) -> list[int | None] | None:
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    ends = [v if isinstance(v, int) and not isinstance(v, bool) else None for v in value]
    return None if ends == [None, None] else ends


def _eligibility(
    labels: list[str], ratings: list[str], age_range: list[int | None] | None
) -> dict[str, Any] | None:
    """An eligibility with its empty keys left out; ``None`` when nothing is set."""
    out: dict[str, Any] = {}
    if labels:
        out["labels"] = labels
    if ratings:
        out["content_ratings"] = ratings
    if age_range is not None:
        out["age_range"] = age_range
    return out or None


def _character(catalog_id: str, version: int | None, *, companion: bool) -> dict[str, Any]:
    """A role's character. A blank version is left out (the latest published at story
    creation): the Engine refuses an explicit ``null`` (``role_version_null``), except on a
    companion, whose locked role names its pin ``null`` before the character's first publish."""
    out: dict[str, Any] = {"catalog_id": catalog_id}
    if version is not None or companion:
        out["version"] = version
    return out


def role_to_write(role: ScenarioRole, *, companion: bool = False) -> dict[str, Any]:
    eligibility = role.eligibility
    character = role.character
    return {
        "id": role.id,
        "kind": role.kind,
        "public_label": norm_text(role.public_label),
        "setup_description": norm_text(role.setup_description),
        "required": role.required,
        "fill": role.fill,
        "character": (
            None
            if character is None
            else _character(character.catalog_id, character.version, companion=companion)
        ),
        "eligibility": (
            None
            if eligibility is None
            else _eligibility(
                norm_labels(eligibility.labels),
                list(dict.fromkeys(eligibility.content_ratings)),
                _age_range(eligibility.age_range),
            )
        ),
        "starting_location": role.starting_location,
        "participation": role.participation,
        "seeded_memories": [seed_to_write(s) for s in role.seeded_memories],
        "relationships": [asdict(r) for r in role.relationships],
        "name_overrides": role.name_overrides,
        "state_initial_values": role.state_initial_values,
    }


def cast_to_write(cast: ScenarioCast, *, companion: bool = False) -> dict[str, Any]:
    """The draft's cast in the write shape (the merge patch's original and the form's first
    state)."""
    return {
        "max_characters_at_start": cast.max_characters_at_start,
        "roles": [role_to_write(r, companion=companion) for r in cast.roles],
    }


ROLE_DEFAULTS: dict[str, Any] = {
    "fill": "open",
    "required": True,
    "relationships": [],
    "seeded_memories": [],
}


def _compact(value: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if not _blank(v)}


def _norm_seed(seed: object) -> object:
    return _compact(seed) if isinstance(seed, Mapping) else seed


def _norm_role(role: object) -> object:
    if not isinstance(role, Mapping):
        return role
    out: dict[str, Any] = {**ROLE_DEFAULTS, **{k: v for k, v in role.items() if v is not None}}
    seeds = out.get("seeded_memories")
    out["seeded_memories"] = [_norm_seed(s) for s in seeds] if isinstance(seeds, list) else seeds
    for key in ("character", "eligibility"):
        if isinstance(out.get(key), Mapping):
            out[key] = _compact(out[key])
    return _compact(out)


def normalize_cast(cast: object) -> dict[str, Any]:
    """``cast`` with the Engine's defaults filled (``fill: open``, ``required: true``, no
    relationships, no seeds) and null or empty optionals dropped, at the cast, role, seed,
    character and eligibility levels (never inside a JSON box): equal for a draft's cast and an
    untouched form's."""
    if not isinstance(cast, Mapping):
        return {}
    roles = cast.get("roles")
    out: dict[str, Any] = {
        "max_characters_at_start": cast.get("max_characters_at_start"),
        "roles": [_norm_role(r) for r in roles] if isinstance(roles, list) else roles,
    }
    return _compact(out)


def seed_ids(doc: Mapping[str, Any]) -> set[str]:
    """Every seed id in ``doc`` (the write shape): the roles' and the world's."""
    found: set[str] = set()
    cast = doc.get("cast") if isinstance(doc.get("cast"), Mapping) else {}
    seeds: list[Any] = []
    for role in (cast or {}).get("roles") or []:
        if isinstance(role, Mapping):
            seeds.extend(role.get("seeded_memories") or [])
    state = doc.get("starting_state")
    if isinstance(state, Mapping):
        seeds.extend(state.get("seeded_memories") or [])
    for seed in seeds:
        if isinstance(seed, Mapping) and isinstance(seed.get("id"), str):
            found.add(seed["id"])
    return found


# --- the form's POST state from a document --------------------------------------------------------


def _box_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _management(prefix: str, total: int, cap: int) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": str(cap),
    }


def _seed_data(prefix: str, seeds: list[Any], role_ids: set[str]) -> dict[str, list[str]]:
    data: dict[str, list[str]] = {}
    for m, seed in enumerate(seeds):
        if not isinstance(seed, Mapping):
            continue
        at = f"{prefix}-{m}-"
        audience = [a for a in seed.get("audience") or [] if isinstance(a, str)]
        ticked = [a for a in audience if a in role_ids or a == WORLD]
        other = [a for a in audience if a not in ticked]
        for name, value in (
            ("id", seed.get("id")),
            ("text", seed.get("text")),
            ("holder", seed.get("holder")),
            ("audience_other", ", ".join(other) if other else None),
            ("disclosure", seed.get("disclosure")),
            ("kind", seed.get("kind")),
            (
                "valid_time",
                None if seed.get("valid_time") is None else _box_text(seed["valid_time"]),
            ),
        ):
            if value is not None:
                data[f"{at}{name}"] = [str(value)]
        if ticked:
            data[f"{at}audience"] = ticked
    for key, value in _management(prefix, len(seeds), SEEDS_MAX).items():
        data[key] = [value]
    return data


def cast_data(doc: Mapping[str, Any]) -> dict[str, list[str]]:
    """``doc``'s cast and world seeds (the write shape) as the editor's POST carries them: what
    #68's ``editor_data`` adds for them."""
    cast = doc.get("cast")
    cast = cast if isinstance(cast, Mapping) else {}
    roles = [r for r in cast.get("roles") or [] if isinstance(r, Mapping)]
    role_ids = {r["id"] for r in roles if isinstance(r.get("id"), str)}
    data: dict[str, list[str]] = {}
    if cast.get("max_characters_at_start") is not None:
        data[MAX_FIELD] = [str(cast["max_characters_at_start"])]
    for n, role in enumerate(roles):
        at = f"{ROLE}-{n}-"
        character = role.get("character") if isinstance(role.get("character"), Mapping) else None
        eligibility = role.get("eligibility")
        eligibility = eligibility if isinstance(eligibility, Mapping) else {}
        ages = _age_range(eligibility.get("age_range")) or [None, None]
        values: dict[str, Any] = {
            "id": role.get("id"),
            "kind": role.get("kind"),
            "public_label": role.get("public_label"),
            "setup_description": role.get("setup_description"),
            "required": "on" if role.get("required", True) else None,
            "fill": role.get("fill") or "open",
            CHARACTER_ID: character.get("catalog_id") if character else None,
            CHARACTER_VERSION: character.get("version") if character else None,
            LABELS_FIELD: ", ".join(eligibility.get("labels") or []) or None,
            AGE_MIN_FIELD: ages[0],
            AGE_MAX_FIELD: ages[1],
            "starting_location": role.get("starting_location"),
            "participation": role.get("participation"),
        }
        for name, (_, empty) in ROLE_BOXES.items():
            value = role.get(name)
            values[name] = None if value is None or value == empty else _box_text(value)
        for name, value in values.items():
            if value is not None:
                data[f"{at}{name}"] = [str(value)]
        ratings = [r for r in eligibility.get("content_ratings") or [] if isinstance(r, str)]
        if ratings:
            data[f"{at}{RATINGS_FIELD}"] = ratings
        data.update(_seed_data(seed_prefix(n), list(role.get("seeded_memories") or []), role_ids))
    for key, value in _management(ROLE, len(roles), ROLES_MAX).items():
        data[key] = [value]
    state = doc.get("starting_state")
    world = state.get("seeded_memories") if isinstance(state, Mapping) else None
    data.update(_seed_data(WSEED, list(world or []), role_ids))
    return data


# --- a companion's fixed slots (#74) --------------------------------------------------------------

# What a companion role keeps (the Engine's ``companion_slots.SLOT_FIELDS`` and the id), as posted.
SLOT_FIELDS = ("id", "kind", "fill", CHARACTER_ID, CHARACTER_VERSION)


def pin_companion_slots(data: QueryDict, original: Mapping[str, Any]) -> QueryDict:
    """``data`` with a companion's role rows pinned to the signed ``original``: the same number
    of roles, each keeping its id, kind, fill and character (version included). Whatever was
    posted for them (a tampered form) is replaced, so the patch echoes the slots unchanged;
    every other role field is the posted one."""
    out = data.copy()
    roles = [r for r in ((original.get("cast") or {}).get("roles") or []) if isinstance(r, Mapping)]
    out[f"{ROLE}-TOTAL_FORMS"] = str(len(roles))
    for n, role in enumerate(roles):
        character = role.get("character") if isinstance(role.get("character"), Mapping) else None
        values = {
            "id": role.get("id"),
            "kind": role.get("kind"),
            "fill": role.get("fill") or "open",
            CHARACTER_ID: character.get("catalog_id") if character else None,
            CHARACTER_VERSION: character.get("version") if character else None,
        }
        for name, value in values.items():
            key = f"{ROLE}-{n}-{name}"
            if value is None:
                out.pop(key, None)
            else:
                out[key] = str(value)
    return out


def companion_role_op(prefix: str) -> bool:
    """Whether a row button names a companion's roles themselves (adding or removing one),
    which a companion refuses (its seeds' buttons stay)."""
    return prefix == ROLE


# --- row editing ----------------------------------------------------------------------------------


def _total(data: QueryDict, prefix: str, cap: int) -> int:
    try:
        total = int(data.get(f"{prefix}-TOTAL_FORMS", "0"))
    except ValueError:
        total = 0
    return max(0, min(total, cap + 1))


def posted_role_ids(data: QueryDict) -> list[str]:
    """The posted role rows' non-blank ids, de-duplicated, in order: what the holder, audience,
    viewpoint and roles-present choices offer."""
    values = (
        (data.get(f"{ROLE}-{n}-id") or "").strip() for n in range(_total(data, ROLE, ROLES_MAX))
    )
    return list(dict.fromkeys(v for v in values if v))


def row_caps(prefix: str) -> dict[str, int]:
    """The row cap of each cast formset an ``op`` may name (a role's seeds by its prefix)."""
    caps = {ROLE: ROLES_MAX, WSEED: SEEDS_MAX}
    if _SEED_PREFIX.match(prefix):
        caps[prefix] = SEEDS_MAX
    return caps


def new_row(data: QueryDict, prefix: str) -> dict[str, list[str]] | None:
    """A new row's values for ``op=add:<prefix>``: a role starts as an ``npc``, required,
    ``open``, with no seeds; a role's seed is held by and known to that role (when its id is a
    valid role id), a world seed by and to ``world``. ``None`` for a prefix that is not the
    cast's."""
    if prefix == ROLE:
        management = _management(SEED, 0, SEEDS_MAX)
        return {
            "kind": ["npc"],
            "required": ["on"],
            "fill": ["open"],
            **{key: [value] for key, value in management.items()},
        }
    if prefix == WSEED:
        return {"holder": [WORLD], "audience": [WORLD]}
    match = _SEED_PREFIX.match(prefix)
    if match is None:
        return None
    role_id = (data.get(f"{ROLE}-{match['role']}-id") or "").strip()
    return {"holder": [role_id], "audience": [role_id]} if ROLE_ID.match(role_id) else {}


def _fill_management(data: QueryDict, prefix: str, cap: int) -> None:
    """Add each of ``prefix``'s management keys ``data`` lacks (a missing count: no rows)."""
    for key, value in _management(prefix, 0, cap).items():
        if key not in data:
            data[key] = value


def prepared(data: QueryDict) -> QueryDict:
    """``data`` with every cast formset's management form present (a missing one: no rows),
    and each seed's ticked audience narrowed to the current choices (role ids and ``world``):
    a token matching none (``self``, a renamed role) moves to "Other audience"."""
    out = data.copy()
    for prefix, cap in ((ROLE, ROLES_MAX), (WSEED, SEEDS_MAX)):
        _fill_management(out, prefix, cap)
    role_ids = set(posted_role_ids(out))
    prefixes = [WSEED]
    for n in range(_total(out, ROLE, ROLES_MAX)):
        prefix = seed_prefix(n)
        prefixes.append(prefix)
        _fill_management(out, prefix, SEEDS_MAX)
    for prefix in prefixes:
        for m in range(_total(out, prefix, SEEDS_MAX)):
            name = f"{prefix}-{m}-audience"
            ticked = out.getlist(name)
            moved = [a for a in ticked if a not in role_ids and a != WORLD]
            if not moved:
                continue
            out.setlist(name, [a for a in ticked if a not in moved])
            other_name = f"{prefix}-{m}-audience_other"
            typed = [t.strip() for t in (out.get(other_name) or "").split(",") if t.strip()]
            out[other_name] = ", ".join(dict.fromkeys([*moved, *typed]))
    return out


# --- the forms ------------------------------------------------------------------------------------


def _textarea(rows: int, cols: int = 60) -> forms.Textarea:
    return forms.Textarea(attrs={"rows": rows, "cols": cols})


def _box(top: type, empty: Any, label: str) -> JsonBoxField:
    shape = "a JSON object" if top is dict else "a JSON array"
    blank = "blank: none" if top is dict else "blank: []"
    return JsonBoxField(
        top,
        empty=empty,
        required=False,
        label=label,
        help_text=f"{shape} ({blank})",
        widget=forms.Textarea(
            attrs={"rows": 3, "cols": 50, "spellcheck": "false", "class": "monospace"}
        ),
    )


class _RowForm(forms.Form):
    """A cast row: dropped when :attr:`blank` (then never validated), anchored by ``path``."""

    path = ""

    @property
    def blank(self) -> bool:
        raise NotImplementedError

    def raw(self, name: str) -> str:
        value = self.data.get(self.add_prefix(name)) if self.data else None
        return value.strip() if isinstance(value, str) else ""

    def full_clean(self) -> None:
        if self.is_bound and self.blank:
            self._errors = ErrorDict()
            self.cleaned_data = {}
            return
        super().full_clean()

    def _anchor(self) -> None:
        for name, f in self.fields.items():
            if not isinstance(f.widget, forms.HiddenInput):
                f.widget.attrs["id"] = field_anchor(f"{self.path}.{name.replace('__', '.')}")


class SeedForm(_RowForm):
    """One seed (a role's or the world's). ``roles`` are the current role ids (the holder and
    audience choices, with ``world``); ``original_ids`` the signed original's seed ids (only
    those are shown, and kept, as the seed's id)."""

    id = forms.CharField(required=False, widget=forms.HiddenInput)
    text = TextField(
        label="Text",
        max_length=TEXT_MAX,
        required=False,
        widget=_textarea(2),
        help_text=f"Up to {TEXT_MAX} characters",
    )
    holder = forms.ChoiceField(label="Holder", required=False)
    audience = forms.MultipleChoiceField(
        label="Audience", required=False, widget=forms.CheckboxSelectMultiple
    )
    audience_other = forms.CharField(
        label="Other audience",
        required=False,
        help_text="Comma-separated (e.g. self)",
        widget=forms.TextInput(attrs={"size": 20}),
    )
    disclosure = forms.ChoiceField(
        label="Disclosure", required=False, choices=_choices(DISCLOSURES, UNSET_LABEL)
    )
    kind = forms.ChoiceField(
        label="Kind", required=False, choices=_choices(SEED_KINDS, UNSET_LABEL)
    )
    valid_time = _box(dict, None, "Valid time")

    def __init__(
        self,
        *args: Any,
        roles: list[str],
        original_ids: frozenset[str] = frozenset(),
        path: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.path = path
        self.original_ids = original_ids
        offered = [*roles, WORLD]
        holder = self.fields["holder"]
        if isinstance(holder, forms.ChoiceField):
            holder.choices = _kept(_choices(offered, UNSET_LABEL), [self.raw("holder")])
        audience = self.fields["audience"]
        if isinstance(audience, forms.MultipleChoiceField):
            audience.choices = [(v, v) for v in offered]
        self.offered = offered
        for name, values in (("disclosure", DISCLOSURES), ("kind", SEED_KINDS)):
            choice = self.fields[name]
            if isinstance(choice, forms.ChoiceField):
                choice.choices = _kept(_choices(values, UNSET_LABEL), [self.raw(name)])
        self._anchor()

    @property
    def blank(self) -> bool:
        return not self.raw("text")

    @property
    def shown_id(self) -> str:
        """The seed's id beside the row: the original's, or "new" before its first save."""
        posted = self.raw("id")
        return posted if posted in self.original_ids else NEW_SEED

    def clean_audience_other(self) -> list[str]:
        text: str = self.cleaned_data.get("audience_other") or ""
        return list(dict.fromkeys(t.strip() for t in text.split(",") if t.strip()))

    def clean(self) -> dict[str, Any] | None:
        data = super().clean() or {}
        if not data.get("text") and "text" not in self.errors:
            self.add_error("text", "A seed needs its text.")
        ticked = [v for v in self.offered if v in (data.get("audience") or [])]
        audience = list(dict.fromkeys([*ticked, *(data.get("audience_other") or [])]))
        if len(audience) > AUDIENCE_MAX:
            self.add_error("audience", f"At most {AUDIENCE_MAX} audience entries.")
        data["audience_sent"] = audience or None
        return data

    def seed(self, seed_id: str | None) -> dict[str, Any]:
        data = self.cleaned_data
        out: dict[str, Any] = {"id": seed_id} if seed_id else {}
        out.update(
            {
                "text": data["text"],
                "holder": data.get("holder") or None,
                "audience": data.get("audience_sent"),
                "disclosure": data.get("disclosure") or None,
                "kind": data.get("kind") or None,
                "valid_time": data.get("valid_time"),
            }
        )
        return out


if TYPE_CHECKING:
    _SeedFormSetBase = BaseFormSet[SeedForm]
else:  # Django's BaseFormSet is generic in its stubs only
    _SeedFormSetBase = BaseFormSet


class _SeedFormSet(_SeedFormSetBase):
    def get_form_kwargs(self, index: int | None) -> dict[str, Any]:
        kwargs = dict(self.form_kwargs)
        base = kwargs.pop("base_path", "")
        if index is not None:
            kwargs["path"] = f"{base}[{index}]"
        return kwargs


SeedFormSet = formset_factory(
    SeedForm,
    formset=_SeedFormSet,
    extra=0,
    max_num=SEEDS_MAX,
    validate_max=True,
    absolute_max=SEEDS_MAX + 1,
)


@dataclass(frozen=True)
class CastChoices:
    """What the role selects offer: published catalog characters (or why not: a raw id input),
    the eligibility ratings (or the note shown beside a comma-separated id input) and whether
    the scenario is a companion (its locked role's ``null`` version pin)."""

    characters: CharacterChoices
    ratings: list[tuple[str, str]] = field(default_factory=list)
    ratings_note: str = ""
    companion: bool = False


class RoleForm(_RowForm):
    """One role. The character select and version input are fields only when the posted fill
    is ``default`` or ``locked``; otherwise ``character: null`` is sent."""

    id = forms.CharField(label="Id", max_length=ID_MAX, required=False, help_text="a-z, 0-9 and _")
    kind = forms.ChoiceField(label="Kind", choices=[(k, k) for k in ROLE_KINDS])
    public_label = TextField(label="Public label", max_length=LABEL_MAX, required=False)
    setup_description = TextField(
        label="Setup description", max_length=TEXT_MAX, required=False, widget=_textarea(3)
    )
    required = forms.BooleanField(label="Required", required=False)
    fill = forms.ChoiceField(label="Fill", choices=[(f, f) for f in FILLS])

    def __init__(
        self,
        *args: Any,
        choices: CastChoices,
        locations: list[str],
        has_seeds: bool = False,
        path: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.path = path
        self.has_seeds = has_seeds
        self.companion = choices.companion
        for name, values in (("kind", ROLE_KINDS), ("fill", FILLS)):
            select = self.fields[name]
            if isinstance(select, forms.ChoiceField):
                select.choices = _kept([(v, v) for v in values], [self.raw(name)])
        if self.shows_character:
            self._character_fields(choices.characters)
        self.fields[LABELS_FIELD] = LabelsField(
            label="Eligible labels",
            required=False,
            help_text=f"Comma-separated, up to {ELIGIBILITY_LABELS_MAX}; a character needs all",
        )
        if choices.ratings_note:
            self.fields[RATINGS_FIELD] = forms.CharField(
                label="Eligible content ratings",
                required=False,
                help_text=f"Comma-separated rating ids. {choices.ratings_note}",
            )
        else:
            posted = (
                self.data.getlist(self.add_prefix(RATINGS_FIELD))
                if isinstance(self.data, QueryDict)
                else []
            )
            self.fields[RATINGS_FIELD] = forms.MultipleChoiceField(
                label="Eligible content ratings",
                required=False,
                choices=_kept(list(choices.ratings), posted),
                widget=forms.SelectMultiple(attrs={"size": 4}),
                help_text="A character needs one of them (none selected: any)",
            )
        for name, label in ((AGE_MIN_FIELD, "Minimum age"), (AGE_MAX_FIELD, "Maximum age")):
            self.fields[name] = forms.IntegerField(
                label=label,
                required=False,
                min_value=AGE_MIN,
                max_value=AGE_MAX,
                help_text="blank: open",
                widget=forms.TextInput(attrs={"inputmode": "numeric", "size": 4}),
            )
        self.fields["starting_location"] = forms.ChoiceField(
            label="Starting location",
            required=False,
            choices=_kept(_choices(locations, NONE_LABEL), [self.raw("starting_location")]),
        )
        self.fields["participation"] = forms.ChoiceField(
            label="Participation",
            required=False,
            choices=_kept(_choices(PARTICIPATION, NOT_SET_LABEL), [self.raw("participation")]),
        )
        for name, (top, empty) in ROLE_BOXES.items():
            label = name.replace("_", " ").capitalize()
            self.fields[name] = _box(top, empty, label)
        self._anchor()

    @property
    def shows_character(self) -> bool:
        return self.raw("fill") in WITH_CHARACTER

    @property
    def fixed_character(self) -> str:
        """A companion slot's character as its read-only block shows it: ``<id>@v<N>``,
        ``<id> (not pinned yet)``, or empty for a role with none (#74)."""
        catalog_id = self.raw(CHARACTER_ID) if self.shows_character else ""
        if not catalog_id:
            return ""
        version = self.raw(CHARACTER_VERSION)
        return f"{catalog_id}@v{version}" if version else f"{catalog_id} (not pinned yet)"

    def slot_errors(self) -> list[str]:
        """The errors on a companion slot's fixed fields (shown in its read-only block)."""
        return [str(e) for name in SLOT_FIELDS if name in self.errors for e in self.errors[name]]

    def _character_fields(self, characters: CharacterChoices) -> None:
        if characters.ok:
            self.fields[CHARACTER_ID] = forms.ChoiceField(
                label="Character",
                required=False,
                choices=_kept(
                    [(UNSET, CHOOSE_LABEL), *characters.choices], [self.raw(CHARACTER_ID)]
                ),
            )
        else:
            self.fields[CHARACTER_ID] = forms.CharField(
                label="Character (catalog id)",
                required=False,
                max_length=64,
                help_text=characters.unavailable_text,
            )
        self.fields[CHARACTER_VERSION] = forms.IntegerField(
            label="Version",
            required=False,
            min_value=VERSION_MIN,
            max_value=VERSION_MAX,
            help_text="blank: the latest published",
            widget=forms.TextInput(attrs={"inputmode": "numeric", "size": 6}),
        )

    @property
    def blank(self) -> bool:
        typed = (self.raw("id"), self.raw("public_label"), self.raw("setup_description"))
        return not any(typed) and not self.has_seeds

    def clean_eligibility__content_ratings(self) -> list[str]:
        value = self.cleaned_data.get(RATINGS_FIELD) or []
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",")]
        ratings = list(dict.fromkeys(v for v in value if v))
        if len(ratings) > ELIGIBILITY_RATINGS_MAX:
            raise forms.ValidationError(f"At most {ELIGIBILITY_RATINGS_MAX} ratings.")
        return ratings

    def clean_eligibility__labels(self) -> list[str]:
        labels: list[str] = self.cleaned_data.get(LABELS_FIELD) or []
        if len(labels) > ELIGIBILITY_LABELS_MAX:
            raise forms.ValidationError(f"At most {ELIGIBILITY_LABELS_MAX} labels.")
        return labels

    def clean_relationships(self) -> list[Any]:
        value: list[Any] = self.cleaned_data.get("relationships") or []
        if len(value) > RELATIONSHIPS_MAX:
            raise forms.ValidationError(f"At most {RELATIONSHIPS_MAX} relationships.")
        return value

    def clean(self) -> dict[str, Any] | None:
        data = super().clean() or {}
        for name in ("id", "public_label", "setup_description"):
            if not data.get(name) and name not in self.errors:
                self.add_error(name, "This field is required.")
        return data

    def role(self, seeds: list[dict[str, Any]]) -> dict[str, Any]:
        data = self.cleaned_data
        character: dict[str, Any] | None = None
        catalog_id = (data.get(CHARACTER_ID) or "").strip() if self.shows_character else ""
        if catalog_id:
            version = data.get(CHARACTER_VERSION)
            character = _character(catalog_id, version, companion=self.companion)
        low, high = data.get(AGE_MIN_FIELD), data.get(AGE_MAX_FIELD)
        return {
            "id": data["id"].strip(),
            "kind": data["kind"],
            "public_label": data["public_label"],
            "setup_description": data["setup_description"],
            "required": bool(data.get("required")),
            "fill": data["fill"],
            "character": character,
            "eligibility": _eligibility(
                data.get(LABELS_FIELD) or [],
                data.get(RATINGS_FIELD) or [],
                None if low is None and high is None else [low, high],
            ),
            "starting_location": data.get("starting_location") or None,
            "participation": data.get("participation") or None,
            "seeded_memories": seeds,
            "relationships": data.get("relationships") or [],
            "name_overrides": data.get("name_overrides"),
            "state_initial_values": data.get("state_initial_values"),
        }


if TYPE_CHECKING:
    _RoleFormSetBase = BaseFormSet[RoleForm]
else:
    _RoleFormSetBase = BaseFormSet


class _RoleFormSet(_RoleFormSetBase):
    """Roles; ``form_kwargs["rows"]`` holds each form's own ``has_seeds`` and ``path``."""

    def get_form_kwargs(self, index: int | None) -> dict[str, Any]:
        kwargs = dict(self.form_kwargs)
        rows: list[dict[str, Any]] = kwargs.pop("rows", [])
        if index is not None and index < len(rows):
            kwargs.update(rows[index])
        return kwargs


RoleFormSet = formset_factory(
    RoleForm,
    formset=_RoleFormSet,
    extra=0,
    max_num=ROLES_MAX,
    validate_max=True,
    absolute_max=ROLES_MAX + 1,
)


class CastLimitForm(forms.Form):
    cast__max_characters_at_start = forms.IntegerField(
        label="Most characters at start",
        required=False,
        min_value=CAST_MIN,
        max_value=CAST_MAX,
        help_text=(f"{CAST_MIN} to {CAST_MAX}, player slots included; blank: (deployment default)"),
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "size": 6, "id": field_anchor(MAX_PATH)}
        ),
    )


# --- the cast as a whole --------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedRows:
    prefix: str
    formset: BaseFormSet[SeedForm]
    can_add: bool


@dataclass(frozen=True)
class RoleRow:
    index: int
    form: RoleForm
    seeds: SeedRows

    @property
    def anchor(self) -> str:
        role_id = self.form.raw("id")
        return f"role-{role_id}" if ROLE_ID.match(role_id) else f"{ROLE}-{self.index}"


@dataclass
class _Built:
    cast: dict[str, Any]
    world: list[dict[str, Any]]
    roles: list[int]  # sent role index → role form index
    seeds: dict[int, list[int]]  # role form index → its sent seeds' form indexes
    world_rows: list[int]
    sent: dict[tuple[int, int] | int, dict[str, Any]]  # a seed form's sent seed


class CastForms:
    """The Cast section (the cast limit, the role rows and each role's seed rows) and the World
    seeds block, bound to ``data``. ``original`` is the signed original (the write shape; only
    its seed ids are read), ``locations`` the location rows' ids in the current form state."""

    def __init__(
        self,
        data: QueryDict,
        *,
        original: Mapping[str, Any],
        choices: CastChoices,
        locations: list[str],
    ) -> None:
        self.data = data
        self.role_ids = posted_role_ids(self.data)
        self.original_ids = frozenset(seed_ids(original))
        seed_kwargs = {"roles": self.role_ids, "original_ids": self.original_ids}
        self.limit = CastLimitForm(self.data)
        total = _total(self.data, ROLE, ROLES_MAX)
        self.seed_sets: list[BaseFormSet[SeedForm]] = [
            SeedFormSet(
                self.data,
                prefix=seed_prefix(n),
                form_kwargs={**seed_kwargs, "base_path": f"cast.roles[{n}].seeded_memories"},
            )
            for n in range(total)
        ]
        rows = [{"has_seeds": self._has_seeds(n), "path": f"cast.roles[{n}]"} for n in range(total)]
        self.roles: BaseFormSet[RoleForm] = RoleFormSet(
            self.data,
            prefix=ROLE,
            form_kwargs={"choices": choices, "locations": locations, "rows": rows},
        )
        self.world = SeedFormSet(
            self.data, prefix=WSEED, form_kwargs={**seed_kwargs, "base_path": WORLD_SEEDS_PATH}
        )
        self.companion = choices.companion
        self.cast_errors: list[str] = []
        self.world_errors: list[str] = []
        self._built: _Built | None = None

    def _has_seeds(self, n: int) -> bool:
        prefix = seed_prefix(n)
        return any(
            (self.data.get(f"{prefix}-{m}-text") or "").strip()
            for m in range(_total(self.data, prefix, SEEDS_MAX))
        )

    # --- validation ------------------------------------------------------------------------

    def _formsets(self) -> list[BaseFormSet[Any]]:
        return [self.roles, *self.seed_sets, self.world]

    def is_valid(self) -> bool:
        results = [self.limit.is_valid(), *(fs.is_valid() for fs in self._formsets())]
        return all(results)

    def quiet(self) -> None:
        quieted: Any = self.limit
        quieted._errors = ErrorDict()
        for formset in self._formsets():
            quieted = formset
            quieted._errors = []
            quieted._non_form_errors = ErrorList()
            for form in formset.forms:
                quieted = form
                quieted._errors = ErrorDict()

    def disable(self) -> None:
        forms_ = [self.limit, *(f for fs in self._formsets() for f in fs.forms)]
        for form in forms_:
            for f in form.fields.values():
                f.widget.attrs["disabled"] = True

    # --- what is sent ----------------------------------------------------------------------

    def _build(self) -> _Built:
        if self._built is not None:
            return self._built
        seen: set[str] = set()
        sent: dict[tuple[int, int] | int, dict[str, Any]] = {}

        def kept_id(form: SeedForm) -> str | None:
            posted = (form.cleaned_data.get("id") or "").strip()
            if posted in self.original_ids and posted not in seen:
                seen.add(posted)
                return posted
            return None  # not the original's, or a repeat: the Engine mints one

        roles: list[dict[str, Any]] = []
        role_rows: list[int] = []
        seed_rows: dict[int, list[int]] = {}
        for n, form in enumerate(self.roles.forms):
            if form.blank:
                continue
            seeds: list[dict[str, Any]] = []
            seed_rows[n] = []
            for m, seed_form in enumerate(self.seed_sets[n].forms):
                if seed_form.blank:
                    continue
                seed = seed_form.seed(kept_id(seed_form))
                sent[(n, m)] = seed
                seeds.append(seed)
                seed_rows[n].append(m)
            roles.append(form.role(seeds))
            role_rows.append(n)
        world: list[dict[str, Any]] = []
        world_rows: list[int] = []
        for m, seed_form in enumerate(self.world.forms):
            if seed_form.blank:
                continue
            seed = seed_form.seed(kept_id(seed_form))
            sent[m] = seed
            world.append(seed)
            world_rows.append(m)
        cast = {
            "max_characters_at_start": self.limit.cleaned_data.get(MAX_FIELD),
            "roles": roles,
        }
        self._built = _Built(cast, world, role_rows, seed_rows, world_rows, sent)
        return self._built

    def cast_body(self) -> dict[str, Any]:
        """The whole ``cast`` object (blank rows dropped; seed ids kept as the module says)."""
        return self._build().cast

    def world_seeds(self) -> list[dict[str, Any]]:
        """``starting_state.seeded_memories``."""
        return self._build().world

    # --- Engine errors ---------------------------------------------------------------------

    def place(self, path: str, message: str) -> bool:
        """Place an Engine failure at ``path`` (``body.`` stripped) when it is the cast's or the
        world seeds': at its row and field (rows by the index sent), else at the top of Cast or
        of World seeds. ``False`` for any other path."""
        if path == MAX_PATH:
            self.limit.add_error(MAX_FIELD, message)
            return True
        if path == CAST_PATH or path.startswith((f"{CAST_PATH}.", f"{CAST_PATH}[")):
            if not self._place_role(path, message):
                self.cast_errors.append(f"{path}: {message}")
            return True
        if path == WORLD_SEEDS_PATH or path.startswith(f"{WORLD_SEEDS_PATH}["):
            rest = path.removeprefix(WORLD_SEEDS_PATH)
            match = _INDEXED.match(rest)
            built = self._build()
            if match and int(match["index"]) < len(built.world_rows):
                m = built.world_rows[int(match["index"])]
                self._place_seed(self.world.forms[m], built.sent[m], match["rest"], path, message)
            else:
                self.world_errors.append(f"{path}: {message}" if rest else message)
            return True
        return False

    def place_fixed(self, role_id: object, path: object, message: str) -> None:
        """A 422 ``companion_slots_fixed`` (#74): at the row of role ``role_id`` (naming the
        slot field ``path``), else at the top of Cast."""
        field_path = path.removeprefix("body.") if isinstance(path, str) else ""
        text = f"{field_path}: {message}" if field_path else message
        for form in self.roles.forms:
            if isinstance(role_id, str) and role_id and form.raw("id") == role_id:
                form.add_error(None, text)
                return
        self.cast_errors.append(text)

    def _place_role(self, path: str, message: str) -> bool:
        match = _INDEXED.match(path.removeprefix("cast.roles"))
        if not path.startswith("cast.roles[") or match is None:
            return False
        built = self._build()
        index = int(match["index"])
        if index >= len(built.roles):
            return False
        n = built.roles[index]
        form = self.roles.forms[n]
        rest = match["rest"]
        if rest is None:
            form.add_error(None, message)
            return True
        if rest.startswith("seeded_memories["):
            seed = _INDEXED.match(rest.removeprefix("seeded_memories"))
            rows = built.seeds.get(n, [])
            if seed is None or int(seed["index"]) >= len(rows):
                form.add_error(None, f"{path}: {message}")
                return True
            m = rows[int(seed["index"])]
            self._place_seed(
                self.seed_sets[n].forms[m], built.sent[(n, m)], seed["rest"], path, message
            )
            return True
        form.add_error(*_role_field(form, rest, path, message))
        return True

    @staticmethod
    def _place_seed(
        form: SeedForm, sent: dict[str, Any], rest: str | None, path: str, message: str
    ) -> None:
        if rest is None:
            form.add_error(None, message)
            return
        name = re.split(r"[.\[]", rest, maxsplit=1)[0]
        if name == "audience":
            token = re.match(r"^audience\[(\d+)\]", rest)
            audience = sent.get("audience") or []
            if token and int(token[1]) < len(audience):
                form.add_error("audience", f"{audience[int(token[1])]}: {message}")
            else:
                form.add_error("audience", message if rest == name else f"{path}: {message}")
            return
        if name in form.fields and name != "id":
            form.add_error(name, message if rest == name else f"{path}: {message}")
        else:
            form.add_error(None, f"{path}: {message}")

    # --- rendering -------------------------------------------------------------------------

    def rows(self) -> list[RoleRow]:
        return [
            RoleRow(
                n,
                form,
                SeedRows(
                    seed_prefix(n),
                    self.seed_sets[n],
                    len(self.seed_sets[n].forms) < SEEDS_MAX,
                ),
            )
            for n, form in enumerate(self.roles.forms)
        ]

    @property
    def can_add_role(self) -> bool:
        return len(self.roles.forms) < ROLES_MAX

    @property
    def world_rows(self) -> SeedRows:
        return SeedRows(WSEED, self.world, len(self.world.forms) < SEEDS_MAX)


def _role_field(form: RoleForm, rest: str, path: str, message: str) -> tuple[str | None, str]:
    """Where on a role row a failure at ``rest`` (the path below ``cast.roles[n]``) lands."""
    name = re.split(r"[.\[]", rest, maxsplit=1)[0]
    if name == "character":
        target = CHARACTER_VERSION if rest == "character.version" else CHARACTER_ID
        if target in form.fields:
            exact = rest in ("character", "character.catalog_id", "character.version")
            return target, message if exact else f"{path}: {message}"
        return None, f"{path}: {message}"
    if name == "eligibility":
        sub = rest.removeprefix("eligibility.")
        if sub.startswith("labels"):
            return LABELS_FIELD, message if sub == "labels" else f"{path}: {message}"
        if sub.startswith("content_ratings"):
            return RATINGS_FIELD, message if sub == "content_ratings" else f"{path}: {message}"
        if sub.startswith("age_range"):
            target = AGE_MAX_FIELD if sub == "age_range[1]" else AGE_MIN_FIELD
            return target, message if sub in ("age_range", "age_range[0]", "age_range[1]") else (
                f"{path}: {message}"
            )
        return None, f"{path}: {message}"
    if name in ROLE_BOXES:
        return name, message if rest == name else f"{path}: {message}"
    if name in form.fields:
        return name, message if rest == name else f"{path}: {message}"
    return None, f"{path}: {message}"

"""The Merge entities picker (#306; FR-PROP-7; E4, E5, C4, C13).

**The candidates** (:func:`merge_candidates`), in this order and de-duplicated by id:

1. the kept ids: each ``?entity=`` id of a GET (:func:`url_entity_ids`: trimmed, upper-cased,
   kept when it passes ``is_ulid``), or each posted tick of a POST that passes ``is_ulid``;
2. the omniscient scene's ``location``, then its ``present`` entities, in the Engine's order;
3. the cast, in cast order.

Labels: a scene entity reads "<name> (<kind>)" (``console.scene.kind_label``, as the Scene tab
shows it; "(unnamed)" for a blank name); a cast member reads its C4 label verbatim (" (player)" /
" (minor)" included) then " (character)"; a kept id found in neither reads "<short id> (not in
the scene)" when the scene answered, "<short id> (from the link)" when it didn't. ``source`` is
``"url"``, ``"scene"`` or ``"cast"`` (a CSS class on the row).

**The form** (:class:`MergeEntitiesForm`, mixing in E4's ``RewriteModesForm``):

- ``entity_ids``: a checkbox per candidate (the kept ids pre-ticked on a GET). A posted tick
  that is no candidate (on a POST the candidates are the posted ULID ticks and the cast) is
  :data:`ENTITY_ID_INVALID`.
- ``other_ids``: a textarea, one id per line; each non-blank line is trimmed and upper-cased,
  and a line failing ``is_ulid`` is "Line <n>: " + :data:`ENTITY_ID_INVALID` (``n`` the physical
  1-based line, blank lines counted), one error per bad line.
- ``survivor``: a radio over every candidate plus each valid typed id ("<short id> (typed)"),
  initially the first pre-ticked candidate. One outside the chosen set, or none, is
  :data:`SURVIVOR_NOT_CHOSEN`. A typed id can be picked only once the page re-renders.

The chosen set is the valid ticks then the valid typed ids, de-duplicated, in that order. Fewer
than two is E5's ``too_few`` sentence (``console.rewrites.MERGE_INVALID_TEXT``) and more than ten
:data:`MERGE_TOO_MANY` (the Engine's ``maxItems``), both on ``entity_ids``. Every error shows at
once. :meth:`MergeEntitiesForm.body` is ``{"entity_ids": [survivor, *the others in chosen
order], **modes()}``: ``visibility`` (the Engine's default ``objective``) and
``identity_proposal_id`` (the Console has no identity proposals) are never sent.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from typing import cast as cast_type

from django import forms

from console.engine.models import EntityBody, SceneBody
from console.forms.rewrites import RewriteModesForm
from console.ids import is_ulid
from console.inspect import short_id
from console.rewrites import MERGE_INVALID_TEXT
from console.scene import kind_label

ENTITY_ID_INVALID = "Enter a 26-character entity id (digits and capital letters, no I, L, O or U)"
SURVIVOR_HELP = "The survivor keeps its id; each other entity becomes the same as it"
SURVIVOR_NOT_CHOSEN = "Pick the survivor from the entities you're merging"
MERGE_TOO_MANY = "At most 10 entities can be merged at once"
TOO_FEW = MERGE_INVALID_TEXT["too_few"]
MERGE_MAX = 10  # the Engine's ``MergeRequest.entity_ids`` ``maxItems``
MERGE_MIN = 2
OTHER_IDS_LABEL = "Other entity ids — one per line"
SURVIVOR_LABEL = "Survives (keeps its id)"
LINE_ERROR = "Line {n}: {text}"
UNNAMED = "(unnamed)"
CHARACTER_SUFFIX = " (character)"
NOT_IN_SCENE = "{id} (not in the scene)"
FROM_LINK = "{id} (from the link)"
TYPED = "{id} (typed)"
URL = "url"
SCENE = "scene"
CAST = "cast"
TYPED_SOURCE = "typed"


@dataclass(frozen=True)
class Candidate:
    """One entity the picker offers: its id, its label and where it came from."""

    id: str
    label: str
    source: str

    @property
    def short(self) -> str:
        return short_id(self.id)


def url_entity_ids(values: Iterable[str]) -> tuple[list[str], bool]:
    """The ``?entity=`` ids (C13: trimmed, upper-cased), de-duplicated, those passing
    ``is_ulid``; and whether any non-blank value failed it."""
    kept: list[str] = []
    invalid = False
    for value in values:
        shown = value.strip().upper()
        if not shown:
            continue
        if is_ulid(shown):
            if shown not in kept:
                kept.append(shown)
        else:
            invalid = True
    return kept, invalid


def _scene_label(entity: EntityBody) -> str:
    return f"{entity.name or UNNAMED} ({kind_label(entity.kind)})"


def merge_candidates(
    cast: Mapping[str, str] | None, scene: SceneBody | None, kept_ids: Sequence[str]
) -> list[Candidate]:
    """The picker's candidates (module docstring). ``cast`` is each cast member's C4 label in
    cast order (``None``: the story read failed); ``scene`` the omniscient scene at the head
    (``None``: not read, or the read failed)."""
    scene_entities: list[EntityBody] = []
    if scene is not None:
        if scene.location is not None:
            scene_entities.append(scene.location)
        scene_entities.extend(scene.present)
    scene_labels: dict[str, str] = {}
    for entity in scene_entities:
        scene_labels.setdefault(entity.entity_id, _scene_label(entity))
    cast_labels = {cid: f"{label}{CHARACTER_SUFFIX}" for cid, label in (cast or {}).items()}

    found: dict[str, Candidate] = {}
    for entity_id in kept_ids:
        if entity_id in found:
            continue
        label = scene_labels.get(entity_id) or cast_labels.get(entity_id)
        if label is None:
            template = NOT_IN_SCENE if scene is not None else FROM_LINK
            label = template.format(id=short_id(entity_id))
        found[entity_id] = Candidate(entity_id, label, URL)
    for entity_id, label in scene_labels.items():
        found.setdefault(entity_id, Candidate(entity_id, label, SCENE))
    for entity_id, label in cast_labels.items():
        found.setdefault(entity_id, Candidate(entity_id, label, CAST))
    return list(found.values())


def _typed_lines(text: str) -> tuple[list[str], list[str]]:
    """The textarea's valid ids (trimmed, upper-cased, in order) and one error per bad line."""
    ids: list[str] = []
    errors: list[str] = []
    for n, line in enumerate(text.splitlines(), start=1):
        shown = line.strip().upper()
        if not shown:
            continue
        if is_ulid(shown):
            ids.append(shown)
        else:
            errors.append(LINE_ERROR.format(n=n, text=ENTITY_ID_INVALID))
    return ids, errors


@dataclass(frozen=True)
class PickerRow:
    """One row of the hand-rendered picker: the candidate (or a typed id, ``ticked`` then
    meaningless) and whether its checkbox and its survivor radio are checked."""

    candidate: Candidate
    ticked: bool
    survives: bool

    @property
    def typed(self) -> bool:
        return self.candidate.source == TYPED_SOURCE


class MergeEntitiesForm(RewriteModesForm):
    """The picker (module docstring): ``entity_ids``, ``other_ids``, ``survivor`` and E4's
    modes."""

    entity_ids = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        error_messages={"invalid_choice": ENTITY_ID_INVALID},
    )
    # Not stripped: "Line <n>" counts the physical lines, leading blank ones included.
    other_ids = forms.CharField(
        required=False, strip=False, widget=forms.Textarea, label=OTHER_IDS_LABEL
    )
    survivor = forms.ChoiceField(
        required=False,
        widget=forms.RadioSelect,
        label=SURVIVOR_LABEL,
        help_text=SURVIVOR_HELP,
        error_messages={"invalid_choice": SURVIVOR_NOT_CHOSEN},
    )

    def __init__(
        self,
        candidates: Sequence[Candidate],
        data: Mapping[str, Any] | None = None,
        *,
        preticked: Sequence[str] = (),
    ) -> None:
        initial: dict[str, Any] = {}
        ticked = [c.id for c in candidates if c.id in preticked]
        if ticked:
            initial = {"entity_ids": ticked, "survivor": ticked[0]}
        super().__init__(data, initial=initial)
        self.candidates = list(candidates)
        known = {c.id for c in self.candidates}
        typed: list[str] = []
        if data is not None:
            typed, _ = _typed_lines(str(data.get("other_ids") or ""))
        self.typed = [
            Candidate(t, TYPED.format(id=short_id(t)), TYPED_SOURCE)
            for t in dict.fromkeys(typed)
            if t not in known
        ]
        ids = [(c.id, c.label) for c in self.candidates]
        entity_field = cast_type(forms.MultipleChoiceField, self.fields["entity_ids"])
        survivor_field = cast_type(forms.ChoiceField, self.fields["survivor"])
        entity_field.choices = ids
        survivor_field.choices = [*ids, *((c.id, c.label) for c in self.typed)]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        known = {c.id for c in self.candidates}
        # From the bound value, so one bad tick (the field's own error) still counts the rest.
        ticked = [t for t in self._value("entity_ids") if t in known and is_ulid(t)]
        typed, line_errors = _typed_lines(cleaned.get("other_ids") or "")
        for error in line_errors:
            self.add_error("other_ids", error)
        chosen = list(dict.fromkeys([*ticked, *typed]))
        if len(chosen) < MERGE_MIN:
            self.add_error("entity_ids", TOO_FEW)
        elif len(chosen) > MERGE_MAX:
            self.add_error("entity_ids", MERGE_TOO_MANY)
        survivor = cleaned.get("survivor") or ""
        if "survivor" not in self.errors and survivor not in chosen:
            self.add_error("survivor", SURVIVOR_NOT_CHOSEN)
        self.chosen = chosen
        return cleaned

    def body(self) -> dict[str, Any]:
        """The ``MergeRequest`` (a valid form): the survivor first, then the others in chosen
        order, with E4's modes."""
        survivor = self.cleaned_data["survivor"]
        others = [entity_id for entity_id in self.chosen if entity_id != survivor]
        return {"entity_ids": [survivor, *others], **self.modes()}

    def _value(self, name: str) -> list[str]:
        value = self[name].value()
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]

    @property
    def rows(self) -> list[PickerRow]:
        """The candidates' rows, in order, then the typed ids' (radio only)."""
        ticked = set(self._value("entity_ids"))
        survivor = self._value("survivor")
        chosen = survivor[0] if survivor else ""
        return [
            PickerRow(c, c.id in ticked, c.id == chosen) for c in [*self.candidates, *self.typed]
        ]

"""Story forms (#45): the story list filter and the new-story form with its cast. They copy only
the bounds the recorded schema states (#43)."""

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator
from django.forms import formset_factory
from django.forms.formsets import BaseFormSet

# ``console.story_settings`` and ``console.participation`` are imported where used: this package
# loads under ``console.engine_access`` (via ``console.acting``), which they reach in turn
# through ``console.wording``.

# --- Story list filter (#45) ------------------------------------------------------------------

STATUS_CHOICES = [
    ("", "All"),
    ("active", "Active"),
    ("archived", "Archived"),
    ("paused", "Paused"),
    ("blocked_by_erasure", "Held"),  # held until a pending erasure is re-applied (#87)
]


class StoryFilterForm(forms.Form):
    """``?status=``: an unknown value is ignored (all stories, no ``status`` sent)."""

    status = forms.ChoiceField(choices=STATUS_CHOICES, required=False)

    def chosen_status(self) -> str | None:
        if self.is_valid():
            return self.cleaned_data["status"] or None
        return None

    def status_label(self, status: str) -> str:
        """The filter option's label for ``status`` ("Held" for ``blocked_by_erasure``)."""
        return dict(STATUS_CHOICES).get(status, status)


# --- New story (#45): POST /stories with a blank story's StorySpec ----------------------------

STORY_NAME_MAX = 200
CHARACTER_NAME_MAX = 80
DEFINITION_MAX = 20_000
SAFE_FALLBACK_MAX = 500
DEFLECTION_STYLE_MAX = 2_000
TRUE_AGE_MIN, TRUE_AGE_MAX = 0, 150
CAST_MIN, CAST_MAX = 1, 20
VIEWPOINT_MAX = 80  # a cast member's name

# Engine M5's creation settings (#198), in form order: each one's C7 "Engine default (<text>)".
CREATION_SETTINGS: dict[str, str] = {
    "player_authority": "own character",
    "player_view": "character",
    "interaction_mode": "sequential",
    "sequence_order": "request order",
    "simultaneous_tie_break": "request order",
    "reactive_interjections": "off",
}
CREATION_HELP = {"player_authority": "Lite stories get this as a prompt instruction only."}


def _creation_field(name: str) -> forms.ChoiceField:
    """C7: "Engine default (<text>)" first (sends nothing), then #197's explicit choices."""
    from console.story_settings import (
        LABELS,
        SETTING_CHOICES,
        engine_default_choice,
        select_bool,
    )

    choices = [engine_default_choice(CREATION_SETTINGS[name]), *SETTING_CHOICES[name]]
    common: dict[str, Any] = {
        "label": LABELS[name],
        "required": False,
        "choices": choices,
        "help_text": CREATION_HELP.get(name, ""),
    }
    if name == "reactive_interjections":
        return forms.TypedChoiceField(coerce=select_bool, empty_value=None, **common)
    return forms.ChoiceField(**common)


class StoryForm(forms.Form):
    """The story's own fields. ``content_rating_id`` choices come from the Engine's
    non-retired ratings; the initial value is the deployment default."""

    name = forms.CharField(
        label="Story name",
        max_length=STORY_NAME_MAX,
        required=False,
        help_text="Optional.",
    )
    content_rating_id = forms.ChoiceField(label="Content rating")
    viewpoint = forms.CharField(
        label="Default output viewpoint",
        max_length=VIEWPOINT_MAX,
        required=False,
        help_text="A character's name. Blank: the Engine picks the sole player character.",
        widget=forms.TextInput(attrs={"list": "cast-names"}),
    )

    def __init__(self, *args: Any, ratings: Sequence[tuple[str, str]], **kwargs: Any) -> None:
        """``ratings``: the select's ``(id, name)`` options, in the Engine's display order."""
        super().__init__(*args, **kwargs)
        field = self.fields["content_rating_id"]
        assert isinstance(field, forms.ChoiceField)  # noqa: S101 - declared above
        field.choices = list(ratings)
        for name in CREATION_SETTINGS:
            self.fields[name] = _creation_field(name)

    def settings(self) -> dict[str, Any]:
        """The chosen creation settings (``CreationSettings``), the viewpoint first; a field
        left on its Engine default is omitted. Call after ``is_valid()``."""
        data = self.cleaned_data
        chosen: dict[str, Any] = {}
        if data["viewpoint"]:
            chosen["default_output_viewpoint"] = data["viewpoint"]
        for name in CREATION_SETTINGS:
            if data.get(name) not in ("", None):
                chosen[name] = data[name]
        return chosen


ROLE_FIELD = "participation_role"
FIXED_FIELD = "participation_fixed"


class CharacterForm(forms.Form):
    """One ``CharacterSpec``. A fully blank extra row is dropped (``empty_permitted``)."""

    name = forms.CharField(max_length=CHARACTER_NAME_MAX)
    definition = forms.CharField(
        max_length=DEFINITION_MAX, widget=forms.Textarea(attrs={"rows": 4})
    )
    true_age = forms.IntegerField(
        label="True age",
        validators=[MinValueValidator(TRUE_AGE_MIN), MaxValueValidator(TRUE_AGE_MAX)],
        widget=forms.NumberInput(attrs={"min": TRUE_AGE_MIN, "max": TRUE_AGE_MAX}),
    )
    is_player = forms.BooleanField(label="Player character", required=False)
    safe_fallback_text = forms.CharField(
        label="Safe-fallback text",
        max_length=SAFE_FALLBACK_MAX,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    deflection_style = forms.CharField(
        label="Deflection style",
        max_length=DEFLECTION_STYLE_MAX,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="How the character refuses or changes the subject (FR-GUARD-7).",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """C8 (#61): the row ends with its participation role and "Fixed"."""
        from console.participation import FIXED_LABEL, FIXED_TITLE, role_choices

        super().__init__(*args, **kwargs)
        self.fields[ROLE_FIELD] = forms.ChoiceField(
            label="Role",
            required=False,
            choices=role_choices(engine_default=True),
            help_text="Leave on Engine default for the player's character.",
        )
        self.fields[FIXED_FIELD] = forms.BooleanField(
            label=FIXED_LABEL, required=False, help_text=FIXED_TITLE
        )

    def clean(self) -> dict[str, Any] | None:
        """C8 (#61): a role or "Fixed" on the player's row, or "Fixed" with no role, is a
        local error on the role field (at most one per row; no call is made)."""
        from console.participation import PLAYER_NO_ROLE, participation_error

        cleaned = super().clean()
        data = self.cleaned_data
        role = data.get(ROLE_FIELD) or None
        fixed = bool(data.get(FIXED_FIELD))
        if data.get("is_player") and (role or fixed):
            error: str | None = PLAYER_NO_ROLE
        else:
            error = participation_error(role, fixed)
        if error:
            self.add_error(ROLE_FIELD, error)
        return cleaned

    def spec(self) -> dict[str, Any]:
        """This row as a ``CharacterSpec``: blank optional texts are omitted."""
        data = self.cleaned_data
        spec: dict[str, Any] = {
            "name": data["name"],
            "definition": data["definition"],
            "true_age": data["true_age"],
            "is_player": data["is_player"],
        }
        for optional in ("safe_fallback_text", "deflection_style"):
            if data[optional]:
                spec[optional] = data[optional]
        if data[ROLE_FIELD]:
            spec["participation"] = {"role": data[ROLE_FIELD], "fixed": data[FIXED_FIELD]}
        return spec


# ``BaseFormSet`` is generic only in django-stubs, not at runtime.
if TYPE_CHECKING:
    CharacterFormSetBase = BaseFormSet[CharacterForm]
else:
    CharacterFormSetBase = BaseFormSet


class BaseCharacterFormSet(CharacterFormSetBase):
    def sent_forms(self) -> list[CharacterForm]:
        """The rows that become ``characters[]``, in order (fully blank extra rows dropped), so
        the Engine's ``characters[i]`` is ``sent_forms()[i]``. Call after ``is_valid()``."""
        return [form for form in self.forms if form.has_changed() or form.empty_permitted is False]


# formset_factory builds a subclass of ``formset=``; the stubs type it as the generic base.
CharacterFormSet = cast(
    type[BaseCharacterFormSet],
    formset_factory(
        CharacterForm,
        formset=BaseCharacterFormSet,
        extra=0,
        min_num=CAST_MIN,
        validate_min=True,
        max_num=CAST_MAX,
        validate_max=True,
    ),
)

CHARACTERS_PREFIX = "characters"


def story_spec(story: StoryForm, characters: BaseCharacterFormSet) -> dict[str, Any]:
    """The ``POST /stories`` body: blank name omitted, the rating always sent, ``settings`` only
    when one is chosen (the viewpoint, or a creation setting off its Engine default)."""
    data = story.cleaned_data
    spec: dict[str, Any] = {}
    if data["name"]:
        spec["name"] = data["name"]
    spec["content_rating_id"] = data["content_rating_id"]
    spec["characters"] = [form.spec() for form in characters.sent_forms()]
    settings = story.settings()
    if settings:
        spec["settings"] = settings
    return spec


class CreationErrorPaths(Mapping[str, str]):
    """Where a ``POST /stories`` 422 belongs (#198): ``settings.<key>`` beside its field
    (``default_output_viewpoint`` and ``viewpoint_unresolved`` beside the viewpoint), and
    ``characters[i]``, ``characters[i].participation`` and its ``.role`` / ``.fixed`` on row
    ``i``'s role field. Anything else (a bare ``settings``, a setting the form doesn't show)
    falls to #43's "<path>: <message>" non-field error."""

    _FIXED: Mapping[str, str] = {
        **{f"settings.{name}": name for name in CREATION_SETTINGS},
        "settings.default_output_viewpoint": "viewpoint",
        "viewpoint_unresolved": "viewpoint",
    }
    _ROW = re.compile(
        rf"^{CHARACTERS_PREFIX}\[(?P<index>\d+)\](?:\.participation(?:\.(?:role|fixed))?)?$"
    )

    def __getitem__(self, path: str) -> str:
        if path in self._FIXED:
            return self._FIXED[path]
        match = self._ROW.match(path)
        if match is None:
            raise KeyError(path)
        return f"{CHARACTERS_PREFIX}[{match['index']}].{ROLE_FIELD}"

    def __iter__(self) -> Iterator[str]:
        return iter(self._FIXED)

    def __len__(self) -> int:
        return len(self._FIXED)


CREATION_ERROR_PATHS = CreationErrorPaths()

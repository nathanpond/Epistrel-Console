"""Story forms (#45): the story list filter and the new-story form with its cast. They copy only
the bounds the recorded schema states (#43)."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator
from django.forms import formset_factory
from django.forms.formsets import BaseFormSet

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
    """The ``POST /stories`` body: blank name and viewpoint omitted, the rating always sent."""
    data = story.cleaned_data
    spec: dict[str, Any] = {}
    if data["name"]:
        spec["name"] = data["name"]
    spec["content_rating_id"] = data["content_rating_id"]
    spec["characters"] = [form.spec() for form in characters.sent_forms()]
    if data["viewpoint"]:
        spec["settings"] = {"default_output_viewpoint": data["viewpoint"]}
    return spec

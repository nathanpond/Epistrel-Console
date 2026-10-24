"""Console forms. Each copies only the bounds the Engine's recorded schema states, so input they
reject makes no Engine call and everything else is left to the Engine's 422 (#43)."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator
from django.forms import formset_factory
from django.forms.formsets import BaseFormSet

from epistrel_console.engine_config import parse_url

# --- Story list filter (#45) ------------------------------------------------------------------

STATUS_CHOICES = [
    ("", "All"),
    ("active", "Active"),
    ("archived", "Archived"),
    ("paused", "Paused"),
]


class StoryFilterForm(forms.Form):
    """``?status=``: an unknown value is ignored (all stories, no ``status`` sent)."""

    status = forms.ChoiceField(choices=STATUS_CHOICES, required=False)

    def chosen_status(self) -> str | None:
        if self.is_valid():
            return self.cleaned_data["status"] or None
        return None


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


# --- Model configuration (#46): PUT /admin/model-config/{purpose} and its api-key ------------

# ConfigReplacement / ModelParams / ApiKeyBody bounds, as the recorded schema states them.
BASE_URL_MAX = 2048
MODEL_MAX = 200
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.0, 2.0
TOP_P_MIN, TOP_P_MAX = 0.0, 1.0
MAX_TOKENS_MIN, MAX_TOKENS_MAX = 1, 4000
TIMEOUT_MAX = 600.0  # and above 0 (exclusive)
STOP_MAX = 4
SEED_MIN, SEED_MAX = 0, 2_147_483_647
API_KEY_MAX = 512


def _http_url(value: str) -> None:
    """An ``http(s)://`` URL with a host (#29's parser), so single-label hosts work."""
    _, reason = parse_url(value)
    if reason:
        raise forms.ValidationError(f"Enter a URL that {reason}.")


def _above_zero(value: float) -> None:
    if value <= 0:
        raise forms.ValidationError("Ensure this value is greater than 0.")


class ModelConfigForm(forms.Form):
    """A purpose's own ``base_url``, ``model`` and params. Blank inherits (``null`` on
    ``default``). Each field's placeholder shows what a blank field inherits."""

    base_url = forms.CharField(
        label="Base URL", max_length=BASE_URL_MAX, required=False, validators=[_http_url]
    )
    model = forms.CharField(label="Model", max_length=MODEL_MAX, required=False)
    temperature = forms.FloatField(
        label="temperature",
        required=False,
        validators=[MinValueValidator(TEMPERATURE_MIN), MaxValueValidator(TEMPERATURE_MAX)],
    )
    top_p = forms.FloatField(
        label="top_p",
        required=False,
        validators=[MinValueValidator(TOP_P_MIN), MaxValueValidator(TOP_P_MAX)],
    )
    max_tokens = forms.IntegerField(
        label="max_tokens",
        required=False,
        validators=[MinValueValidator(MAX_TOKENS_MIN), MaxValueValidator(MAX_TOKENS_MAX)],
    )
    timeout = forms.FloatField(
        label="timeout (seconds)",
        required=False,
        validators=[_above_zero, MaxValueValidator(TIMEOUT_MAX)],
    )
    stop = forms.CharField(
        label="stop",
        required=False,
        widget=forms.Textarea(attrs={"rows": STOP_MAX}),
        help_text="One sequence per line.",
    )
    seed = forms.IntegerField(
        label="seed",
        required=False,
        validators=[MinValueValidator(SEED_MIN), MaxValueValidator(SEED_MAX)],
    )

    PARAM_FIELDS = ("temperature", "top_p", "max_tokens", "timeout", "stop", "seed")

    def __init__(
        self, *args: Any, placeholders: dict[str, str] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        for name, text in (placeholders or {}).items():
            self.fields[name].widget.attrs["placeholder"] = text

    def clean_stop(self) -> list[str] | None:
        """One sequence per line: lines trimmed, blank lines dropped; none → ``None``."""
        text: str = self.cleaned_data["stop"] or ""
        lines = [line.strip() for line in text.splitlines()]
        kept = [line for line in lines if line]
        if len(kept) > STOP_MAX:
            raise forms.ValidationError(f"At most {STOP_MAX} stop sequences.")
        return kept or None

    def replacement(self, *, is_default: bool) -> dict[str, Any]:
        """The ``ConfigReplacement`` body. A blank field is absent (it inherits), or ``null``
        on ``default`` (unset); params go as one object, only when one is given (always on
        ``default``)."""
        data = self.cleaned_data
        body: dict[str, Any] = {}
        for name in ("base_url", "model"):
            value = data[name] or None
            if value is not None or is_default:
                body[name] = value
        params = {
            name: data[name] for name in self.PARAM_FIELDS if data[name] is not None or is_default
        }
        if params:
            body["params"] = params
        return body


class ApiKeyForm(forms.Form):
    """The key for ``PUT …/api-key``. Never rendered back (``render_value=False``)."""

    api_key = forms.CharField(
        label="API key",
        max_length=API_KEY_MAX,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}, render_value=False),
    )

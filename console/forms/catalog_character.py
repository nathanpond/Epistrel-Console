"""The catalog character's structured form (#66): create and edit every field the Engine defines.

One flat Django form whose field names are the Engine's write paths joined with ``__`` (#64
convention 6: ``definition.dispositions.warmth.nuance`` → ``definition__dispositions__warmth__
nuance``; the one alias is ``metadata.content_rating_id`` → ``metadata__content_rating``). Its
sections (``<fieldset>``s) are the card, the definition, the metadata and the visual identity;
images are #71's. Each widget's id is the field's report anchor (``console.reports
.field_anchor``), so a report entry links to it.

Values are normalised before anything is compared or sent, the same way for the form and for
the draft it edits (:func:`draft_to_write`): CRLF → LF, surrounding whitespace stripped, a blank
text is unset, one-per-line lists trimmed with blanks dropped and duplicates removed, labels also
lower-cased (the Engine stores them so). A stored value differing only by that is no change.

- Create (:meth:`CharacterForm.create_body`) omits blank optionals (aliases and labels when
  empty, an empty visual identity, an empty ``adapter_params``) but always sends the four lists a
  publish needs present (likes, dislikes, mannerisms, sample dialogue), as ``[]`` when empty, and
  the owner.
- Edit (:meth:`CharacterForm.edit_patch`) is the RFC 7386 diff (``console.merge_patch``) of the
  form against the draft it rendered: changed fields only, a cleared field or an unset
  disposition axis as ``null``, an emptied list as ``[]``, ``adapter_params`` keys removed as
  ``null``, "Clear visual identity" as ``visual_identity: null`` (it wins over the visual
  identity's fields). An empty diff is "No changes": no call.

The bounds are the schema's (#43): string lengths, ``true_age`` 0 to 150, ``seed`` 0 to
2^32 - 1, list caps and item lengths, the label pattern. Alias rules the Engine checks (an alias
equal to the name, duplicates ignoring case) are left to its 422, placed on aliases.

Engine errors land on their fields (:func:`field_for_path`, :meth:`CharacterForm.place_error`,
:meth:`CharacterForm.place_report`); a list index reads "Line N: …".
"""

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from django import forms
from django.core.exceptions import ValidationError
from django.forms.boundfield import BoundField

from console import merge_patch
from console.engine.choices import RatingChoices
from console.engine.client import EngineError
from console.engine.models import CatalogCharacter, Disposition, ReportEntry, ValidationReport
from console.engine_form_errors import engine_field_errors
from console.forms.fields import JsonBoxField
from console.forms.principal import PrincipalField
from console.reports import field_anchor

SEP = "__"
BODY_PREFIX = "body."
RATING_FIELD = "metadata__content_rating"
RATING_PATH = "metadata.content_rating_id"
CLEAR_IDENTITY = "clear_visual_identity"
OWNER = "owner"
NOT_SET = ""
NONE_LABEL = "— none —"
NOT_SET_LABEL = "— not set —"
REQUIRED_TO_PUBLISH = "required to publish"
ADAPTER_PARAMS_MAX = 8192  # bytes of the compact JSON
SEED_MAX = 2**32 - 1
TRUE_AGE_MAX = 150
LABELS_MAX = 30
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")
NUANCE_MAX = 500
NUANCE_NEEDS_SETTING = "Pick a setting for this nuance."
ADAPTER_PARAMS_OBJECT = "Adapter params must be a JSON object with finite numbers."
ADAPTER_PARAMS_NULL = "Adapter params can't hold null values."
ADAPTER_PARAMS_SIZE = f"Adapter params are over {ADAPTER_PARAMS_MAX // 1024} KiB."
DRAFT_TOO_LARGE = "The draft is over 256 KiB."

# The disposition settings, as the schema's enums (a test compares them with ``openapi.json``).
ThreatResponse = Literal["flees", "yields", "deflects", "stands", "attacks"]
Dominance = Literal["very_submissive", "submissive", "balanced", "dominant", "very_dominant"]
Honesty = Literal["very_deceitful", "deceitful", "balanced", "honest", "very_honest"]
Warmth = Literal["cold", "cool", "neutral", "warm", "very_warm"]
Caution = Literal["reckless", "bold", "balanced", "careful", "very_cautious"]
DISPOSITION_SETTINGS: dict[str, tuple[str, ...]] = {
    "threat_response": get_args(ThreatResponse),
    "dominance": get_args(Dominance),
    "honesty": get_args(Honesty),
    "warmth": get_args(Warmth),
    "caution": get_args(Caution),
}
AXES = tuple(DISPOSITION_SETTINGS)

# Engine paths (after ``body.``) the form does not mirror as written (#66's error aliases).
PATH_ALIASES = {
    "name": "public_card.name",
    "public_description": "public_card.public_description",
    "content_rating": RATING_PATH,
    "content_rating_id": RATING_PATH,
    "aliases": "metadata.aliases",
    "labels": "metadata.labels",
}
_INDEXED = re.compile(r"^(?P<base>[^\[\]]+)\[(?P<index>\d+)\](?P<rest>.*)$")


# --- normalisation --------------------------------------------------------------------------------


def norm_text(value: object) -> str | None:
    """CRLF → LF, surrounding whitespace stripped; blank (or not a string) → ``None``."""
    if not isinstance(value, str):
        return None
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return text or None


def norm_lines(values: Iterable[object] | None) -> list[str]:
    """A string list as one-per-line lists keep it: each item trimmed, blanks dropped,
    duplicates removed (first kept)."""
    items = (norm_text(v) for v in (values or ()))
    return list(dict.fromkeys(item for item in items if item))


def norm_labels(values: Iterable[object] | None) -> list[str]:
    """Labels as the Engine stores them: trimmed, lower-cased, distinct."""
    return norm_lines(v.lower() if isinstance(v, str) else v for v in (values or ()))


# --- the draft as the form sees it ----------------------------------------------------------------


def _disposition(value: Disposition | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"setting": value.setting, "nuance": norm_text(value.nuance)}


def draft_to_write(draft: CatalogCharacter) -> dict[str, Any]:
    """The flat read (``name``, ``public_description``, ``aliases``, ``labels``,
    ``content_rating.id``, ``definition``, ``visual_identity``) in the write shape
    (``public_card.*``, ``metadata.*`` with ``content_rating_id``, ``definition``,
    ``visual_identity``), normalised. Unset values are ``None``; images and reference images
    are left out (#71's). It feeds the form's initial and the merge-patch original."""
    d = draft.definition
    likes = d.likes_dislikes
    style = d.interaction_style
    dispositions = d.dispositions
    identity = draft.visual_identity
    return {
        "public_card": {
            "name": norm_text(draft.name),
            "public_description": norm_text(draft.public_description),
        },
        "definition": {
            "true_age": d.true_age,
            "appearance": norm_text(d.appearance),
            "personality": norm_text(d.personality),
            "values_motivations": norm_text(d.values_motivations),
            "background_flavor": norm_text(d.background_flavor),
            "likes_dislikes": {
                "likes": norm_lines(likes.likes if likes else ()),
                "dislikes": norm_lines(likes.dislikes if likes else ()),
            },
            "dispositions": {
                axis: _disposition(getattr(dispositions, axis) if dispositions else None)
                for axis in AXES
            },
            "interaction_style": {
                "speech_style": norm_text(style.speech_style if style else None),
                "vocabulary": norm_text(style.vocabulary if style else None),
                "mannerisms": norm_lines(style.mannerisms if style else ()),
                "sample_dialogue": norm_lines(style.sample_dialogue if style else ()),
            },
            "deflection_style": norm_text(d.deflection_style),
            "safe_fallback_text": norm_text(d.safe_fallback_text),
        },
        "metadata": {
            "aliases": norm_lines(draft.aliases),
            "labels": norm_labels(draft.labels),
            "content_rating_id": draft.content_rating.id if draft.content_rating else None,
        },
        "visual_identity": None
        if identity is None
        else {
            "base_appearance_prompt": norm_text(identity.base_appearance_prompt),
            "negative_prompt": norm_text(identity.negative_prompt),
            "restricted_details_prompt": norm_text(identity.restricted_details_prompt),
            "preferred_image_model": norm_text(identity.preferred_image_model),
            "seed": identity.seed,
            "adapter_params": identity.adapter_params or None,
        },
    }


def prune(value: Any) -> Any:
    """``value`` with ``None`` dropped from objects (an unset field is absent); lists and empty
    objects kept. ``adapter_params`` holds no ``None`` (the form refuses JSON ``null``)."""
    if isinstance(value, Mapping):
        return {k: prune(v) for k, v in value.items() if v is not None}
    return value


def _drop_empty(value: Any, *, keep: frozenset[str] = frozenset(), path: str = "") -> Any:
    """Create's rule: empty objects and empty lists dropped, except the lists in ``keep``."""
    if not isinstance(value, Mapping):
        return value
    out: dict[str, Any] = {}
    for key, item in value.items():
        where = f"{path}.{key}" if path else key
        item = _drop_empty(item, keep=keep, path=where)
        if (item == {} or item == []) and where not in keep:
            continue
        out[key] = item
    return out


def flatten(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The form's initial from a write-shaped document (:func:`draft_to_write`)."""
    flat: dict[str, Any] = {}

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for key, value in node.items():
            name = f"{prefix}{SEP}{key}" if prefix else key
            if prefix == f"definition{SEP}dispositions":
                flat[f"{name}{SEP}setting"] = value["setting"] if value else NOT_SET
                flat[f"{name}{SEP}nuance"] = value.get("nuance") if value else None
            elif key == "adapter_params" or not isinstance(value, Mapping):
                flat[name] = value
            else:
                walk(value, name)

    walk({k: v for k, v in doc.items() if v is not None}, "")
    flat[RATING_FIELD] = flat.pop(f"metadata{SEP}content_rating_id", None) or NOT_SET
    return flat


# --- fields -------------------------------------------------------------------------------------


class TextField(forms.CharField):
    """A text field cleaned to the normalised value (``None`` when blank)."""

    def to_python(self, value: Any) -> str | None:
        return norm_text(super().to_python(value))


class LinesField(forms.CharField):
    """A one-per-line list: trimmed, blanks dropped, de-duplicated; at most ``max_items``
    items of ``item_max`` characters ("Line N: …")."""

    widget = forms.Textarea(attrs={"rows": 4, "cols": 80})

    def __init__(self, *, max_items: int, item_max: int, **kwargs: Any) -> None:
        self.max_items = max_items
        self.item_max = item_max
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def prepare_value(self, value: Any) -> Any:
        return "\n".join(value) if isinstance(value, list) else value

    def to_python(self, value: Any) -> list[str]:  # type: ignore[override]
        text = super().to_python(value) or ""
        return norm_lines(text.replace("\r\n", "\n").split("\n"))

    def validate(self, value: Any) -> None:
        errors = [
            ValidationError(f"Line {n}: longer than {self.item_max} characters.", code="item")
            for n, item in enumerate(value, start=1)
            if len(item) > self.item_max
        ]
        if len(value) > self.max_items:
            errors.append(ValidationError(f"At most {self.max_items} lines.", code="max_items"))
        if errors:
            raise ValidationError(errors)


class LabelsField(forms.CharField):
    """Labels, comma-separated: trimmed, lower-cased, distinct; each one of ``A-Z a-z 0-9 _ -``
    and spaces, 1 to 40 characters; at most 30."""

    def prepare_value(self, value: Any) -> Any:
        return ", ".join(value) if isinstance(value, list) else value

    def to_python(self, value: Any) -> list[str]:  # type: ignore[override]
        text = super().to_python(value) or ""
        return norm_labels(text.split(","))

    def validate(self, value: Any) -> None:
        errors = [
            ValidationError(
                f"“{label}” is not a label: 1 to 40 of A-Z, a-z, 0-9, _, - and spaces.",
                code="pattern",
            )
            for label in value
            if not LABEL_PATTERN.match(label)
        ]
        if len(value) > LABELS_MAX:
            errors.append(ValidationError(f"At most {LABELS_MAX} labels.", code="max_items"))
        if errors:
            raise ValidationError(errors)


def _refuse_constant(name: str) -> Any:
    raise ValidationError(ADAPTER_PARAMS_OBJECT, code="not_object")


def _check_json(value: Any) -> None:
    """Refuse a JSON ``null`` at any depth and a non-finite number (``1e999`` parses to inf)."""
    if value is None:
        raise ValidationError(ADAPTER_PARAMS_NULL, code="null")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError(ADAPTER_PARAMS_OBJECT, code="not_object")
    children = (
        value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    )
    for child in children:
        _check_json(child)


def compact_size(value: Any) -> int:
    """``adapter_params``' size: the UTF-8 bytes of ``json.dumps(v, separators=(",", ":"))``."""
    return len(json.dumps(value, separators=(",", ":")).encode("utf-8"))


class AdapterParamsField(JsonBoxField):
    """``visual_identity.adapter_params``: a JSON object, finite numbers, no ``null`` anywhere
    (a merge patch can't store one), at most 8 KiB compact. Blank (or ``{}``) cleans to
    ``None``. Shown as ``json.dumps(v, indent=2, sort_keys=True)``."""

    widget = forms.Textarea(
        attrs={"rows": 8, "cols": 80, "spellcheck": "false", "class": "monospace"}
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(dict, empty=None, **kwargs)

    def prepare_value(self, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)

    def to_python(self, value: Any) -> Any:
        text = forms.CharField.to_python(self, value)
        if text is None or text in self.empty_values:
            return None
        try:
            parsed = json.loads(text, parse_constant=_refuse_constant)
        except ValueError as exc:
            raise ValidationError(f"Enter valid JSON: {exc}", code="invalid_json") from exc
        if not isinstance(parsed, dict):
            raise ValidationError(ADAPTER_PARAMS_OBJECT, code="not_object")
        _check_json(parsed)
        if compact_size(parsed) > ADAPTER_PARAMS_MAX:
            raise ValidationError(ADAPTER_PARAMS_SIZE, code="too_large")
        return parsed or None  # ``{}`` sets nothing: the same as blank


# --- the form -----------------------------------------------------------------------------------

_PROSE = {"rows": 4, "cols": 80}
_SHORT = {"rows": 2, "cols": 80}


def _text(label: str, max_length: int, *, rows: dict[str, int] | None, publish: bool) -> TextField:
    widget: forms.Widget = forms.Textarea(attrs=dict(rows)) if rows else forms.TextInput()
    hint = f"Up to {max_length} characters"
    return TextField(
        label=label,
        max_length=max_length,
        required=False,
        widget=widget,
        help_text=f"{hint}; {REQUIRED_TO_PUBLISH}" if publish else hint,
    )


def _lines(label: str, max_items: int, item_max: int, *, extra: str = "") -> LinesField:
    hint = f"One per line, up to {max_items}, each up to {item_max} characters"
    return LinesField(
        label=label,
        max_items=max_items,
        item_max=item_max,
        help_text=f"{hint}. {extra}".strip() if extra else hint,
    )


def _fields() -> dict[str, forms.Field]:
    """Every content field, in display order (the dispositions are added after)."""
    vi_hint = "required to publish once the visual identity is set"
    fields: dict[str, forms.Field] = {
        "public_card__name": _text("Name", 120, rows=None, publish=True),
        "public_card__public_description": _text(
            "Public description", 4000, rows=_PROSE, publish=True
        ),
        "definition__true_age": forms.IntegerField(
            label="True age",
            required=False,
            min_value=0,
            max_value=TRUE_AGE_MAX,
            help_text=f"0 to {TRUE_AGE_MAX}; {REQUIRED_TO_PUBLISH}",
            widget=forms.TextInput(attrs={"inputmode": "numeric", "size": 4}),
        ),
        "definition__appearance": _text("Appearance", 4000, rows=_PROSE, publish=True),
        "definition__personality": _text("Personality", 4000, rows=_PROSE, publish=True),
        "definition__values_motivations": _text(
            "Values and motivations", 4000, rows=_PROSE, publish=True
        ),
        "definition__background_flavor": _text(
            "Background flavour", 4000, rows=_PROSE, publish=False
        ),
        "definition__likes_dislikes__likes": _lines("Likes", 30, 200),
        "definition__likes_dislikes__dislikes": _lines("Dislikes", 30, 200),
        "definition__interaction_style__speech_style": _text(
            "Speech style", 2000, rows=_SHORT, publish=True
        ),
        "definition__interaction_style__vocabulary": _text(
            "Vocabulary", 2000, rows=_SHORT, publish=True
        ),
        "definition__interaction_style__mannerisms": _lines(
            "Mannerisms", 20, 500, extra="An item can't span lines."
        ),
        "definition__interaction_style__sample_dialogue": _lines(
            "Sample dialogue", 20, 1000, extra="An item can't span lines."
        ),
        "definition__deflection_style": _text("Deflection style", 2000, rows=_PROSE, publish=False),
        "definition__safe_fallback_text": _text(
            "Safe fallback text", 500, rows=_PROSE, publish=False
        ),
        "metadata__aliases": _lines("Aliases", 20, 60),
        "metadata__labels": LabelsField(
            label="Labels",
            required=False,
            help_text=f"Comma-separated, up to {LABELS_MAX}; A-Z, a-z, 0-9, _, - and spaces",
        ),
        RATING_FIELD: forms.ChoiceField(
            label="Content rating", required=False, help_text=REQUIRED_TO_PUBLISH
        ),
        "visual_identity__preferred_image_model": TextField(
            label="Preferred image model",
            max_length=200,
            required=False,
            help_text=f"Up to 200 characters; {vi_hint}",
        ),
        "visual_identity__base_appearance_prompt": TextField(
            label="Base appearance prompt",
            max_length=2000,
            required=False,
            widget=forms.Textarea(attrs=dict(_PROSE)),
            help_text=f"Up to 2000 characters; {vi_hint}",
        ),
        "visual_identity__negative_prompt": TextField(
            label="Negative prompt",
            max_length=2000,
            required=False,
            widget=forms.Textarea(attrs=dict(_PROSE)),
            help_text=f"Up to 2000 characters; {vi_hint}",
        ),
        "visual_identity__restricted_details_prompt": _text(
            "Restricted details prompt", 2000, rows=_PROSE, publish=False
        ),
        "visual_identity__seed": forms.IntegerField(
            label="Seed",
            required=False,
            min_value=0,
            max_value=SEED_MAX,
            help_text=f"0 to {SEED_MAX}; {vi_hint}",
            widget=forms.TextInput(attrs={"inputmode": "numeric", "size": 12}),
        ),
        "visual_identity__adapter_params": AdapterParamsField(
            label="Adapter params",
            required=False,
            help_text="A JSON object, up to 8 KiB; no null values",
        ),
    }
    return fields


def axis_field(axis: str, part: str) -> str:
    return f"definition{SEP}dispositions{SEP}{axis}{SEP}{part}"


def field_path(name: str) -> str:
    """The Engine path a form field mirrors (its report anchor's source)."""
    if name == RATING_FIELD:
        return RATING_PATH
    return name.replace(SEP, ".")


SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Card", "public_card", ("public_card__name", "public_card__public_description")),
    (
        "Definition",
        "definition",
        (
            "definition__true_age",
            "definition__appearance",
            "definition__personality",
            "definition__values_motivations",
            "definition__background_flavor",
            "definition__likes_dislikes__likes",
            "definition__likes_dislikes__dislikes",
        ),
    ),
    (
        "Interaction style",
        "definition.interaction_style",
        (
            "definition__interaction_style__speech_style",
            "definition__interaction_style__vocabulary",
            "definition__interaction_style__mannerisms",
            "definition__interaction_style__sample_dialogue",
            "definition__deflection_style",
            "definition__safe_fallback_text",
        ),
    ),
    ("Metadata", "metadata", ("metadata__aliases", "metadata__labels", RATING_FIELD)),
    (
        "Visual identity",
        "visual_identity",
        (
            "visual_identity__preferred_image_model",
            "visual_identity__base_appearance_prompt",
            "visual_identity__negative_prompt",
            "visual_identity__restricted_details_prompt",
            "visual_identity__seed",
            "visual_identity__adapter_params",
        ),
    ),
)
LIST_FIELDS = frozenset(
    {
        "definition__likes_dislikes__likes",
        "definition__likes_dislikes__dislikes",
        "definition__interaction_style__mannerisms",
        "definition__interaction_style__sample_dialogue",
        "metadata__aliases",
        "metadata__labels",
    }
)
# Create always sends these, as ``[]`` when empty (a publish needs them present).
ALWAYS_SENT = frozenset(
    {
        "definition.likes_dislikes.likes",
        "definition.likes_dislikes.dislikes",
        "definition.interaction_style.mannerisms",
        "definition.interaction_style.sample_dialogue",
    }
)


@dataclass(frozen=True)
class Row:
    """One field as the form template shows it, with the report entries placed on it."""

    field: BoundField
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AxisRow:
    axis: str
    label: str
    anchor: str
    setting: BoundField
    nuance: BoundField
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Section:
    title: str
    anchor: str
    rows: tuple[Row, ...]


def field_for_path(path: str, fields: Mapping[str, Any]) -> tuple[str, int | None] | None:
    """The form field an Engine path names, and the list index it names (``None`` for none),
    or ``None`` when the form has no such field. ``body.`` is stripped; the aliases are
    :data:`PATH_ALIASES`; a disposition axis lands on its setting."""
    path = path.removeprefix(BODY_PREFIX)
    index: int | None = None
    match = _INDEXED.match(path)
    if match:
        if match["rest"]:
            return None  # inside a list item (a reference image's audience): no field
        path, index = match["base"], int(match["index"])
    path = PATH_ALIASES.get(path, path)
    name = RATING_FIELD if path == RATING_PATH else path.replace(".", SEP)
    for candidate in (name, f"{name}{SEP}setting"):
        if candidate in fields:
            return candidate, index
    return None


def _indexed(name: str, index: int | None, message: str) -> str:
    if index is None:
        return message
    word = "Label" if name == "metadata__labels" else "Line"
    return f"{word} {index + 1}: {message}"


def entry_text(entry: ReportEntry) -> str:
    """A report entry as it shows beside its field: its code and the rule applied."""
    return f"{entry.code}: {entry.rule}" if entry.rule else entry.code


class CharacterForm(forms.Form):
    """The create/edit form. ``ratings`` is the content-rating select's options (from
    ``rating_choices``; when that read failed the field is left out and ``current_rating`` is
    kept unchanged). ``create`` adds the owner; edit adds "Clear visual identity"."""

    def __init__(
        self,
        *args: Any,
        ratings: RatingChoices,
        create: bool,
        current_rating: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.create = create
        self.ratings = ratings
        self.current_rating = current_rating
        self.report_notes: dict[str, list[str]] = {}
        built = _fields()
        for axis, settings in DISPOSITION_SETTINGS.items():
            label = axis.replace("_", " ").capitalize()
            choices = [(NOT_SET, NOT_SET_LABEL), *((s, s.replace("_", " ")) for s in settings)]
            built[axis_field(axis, "setting")] = forms.ChoiceField(
                label=f"{label}: setting",
                required=False,
                choices=choices,
                help_text=REQUIRED_TO_PUBLISH,
            )
            built[axis_field(axis, "nuance")] = TextField(
                label=f"{label}: nuance",
                max_length=NUANCE_MAX,
                required=False,
                widget=forms.TextInput(attrs={"size": 60, "aria-label": f"{label}: nuance"}),
                help_text=f"Up to {NUANCE_MAX} characters",
            )
        if create:
            self.fields[OWNER] = PrincipalField(
                label="Owner", help_text="The principal the entry belongs to"
            )
        self.fields.update(built)
        rating = self.fields[RATING_FIELD]
        if ratings.ok and isinstance(rating, forms.ChoiceField):
            rating.choices = [(NOT_SET, NONE_LABEL), *ratings.choices]
        else:
            del self.fields[RATING_FIELD]
        if not create:
            self.fields[CLEAR_IDENTITY] = forms.BooleanField(
                label="Clear visual identity",
                required=False,
                help_text="Removes the whole visual identity (its reference images too); the "
                "fields above are then ignored",
            )
        for name, field in self.fields.items():
            if name not in (OWNER, CLEAR_IDENTITY):
                field.widget.attrs["id"] = field_anchor(field_path(name))

    # --- validation ------------------------------------------------------------------------

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        for axis in AXES:
            setting = cleaned.get(axis_field(axis, "setting"))
            nuance = cleaned.get(axis_field(axis, "nuance"))
            if nuance and not setting:
                self.add_error(axis_field(axis, "nuance"), NUANCE_NEEDS_SETTING)
        return cleaned

    # --- what is sent ----------------------------------------------------------------------

    def write_doc(self) -> dict[str, Any]:
        """The cleaned form in the write shape (unset values ``None``), like
        :func:`draft_to_write`."""
        data = self.cleaned_data
        doc: dict[str, Any] = {}
        for name, value in data.items():
            if name in (OWNER, CLEAR_IDENTITY) or name.startswith(f"definition{SEP}dispositions"):
                continue
            if name == RATING_FIELD:
                doc.setdefault("metadata", {})["content_rating_id"] = value or None
                continue
            *groups, leaf = name.split(SEP)
            node = doc
            for group in groups:
                node = node.setdefault(group, {})
            node[leaf] = value
        if RATING_FIELD not in self.fields:  # the ratings read failed: the rating is kept
            doc.setdefault("metadata", {})["content_rating_id"] = self.current_rating
        dispositions: dict[str, Any] = {}
        for axis in AXES:
            setting = data.get(axis_field(axis, "setting"))
            nuance = data.get(axis_field(axis, "nuance"))
            dispositions[axis] = {"setting": setting, "nuance": nuance} if setting else None
        doc.setdefault("definition", {})["dispositions"] = dispositions
        if data.get(CLEAR_IDENTITY):
            doc["visual_identity"] = None
        return doc

    def create_body(self) -> dict[str, Any]:
        """``POST /catalog/characters``' body: blank optionals omitted, the four lists a
        publish needs always sent, the owner always."""
        body: dict[str, Any] = _drop_empty(prune(self.write_doc()), keep=ALWAYS_SENT)
        body[OWNER] = self.cleaned_data[OWNER]
        return body

    def edit_patch(self, original: Mapping[str, Any]) -> dict[str, Any]:
        """The merge patch from ``original`` (:func:`draft_to_write` of the draft rendered) to
        the form: ``{}`` when nothing changed."""
        before = prune(original)
        after = prune(self.write_doc())
        if after.get("visual_identity") == {} and "visual_identity" not in before:
            del after["visual_identity"]  # nothing to set on an entry that has none
        return merge_patch.diff(before, after)

    # --- Engine errors ---------------------------------------------------------------------

    def place_error(self, error: EngineError) -> int:
        """Place a 422/413's field failures (``details.errors[]`` or ``details.field``) at
        their fields; the others become non-field errors naming the path. A ``rating_retired``
        without a path lands on the rating, a ``draft_too_large`` is the form's. Returns how many
        landed on a field."""
        if error.code == "draft_too_large":
            self.add_error(None, DRAFT_TOO_LARGE)
            return 0
        failures = engine_field_errors(error)
        if not failures and error.code == "rating_retired" and RATING_FIELD in self.fields:
            self.add_error(RATING_FIELD, error.message)
            return 1
        placed = 0
        for failure in failures:
            message = failure.message or error.message
            found = field_for_path(failure.path, self.fields)
            if found is None:
                self.add_error(None, f"{failure.path.removeprefix(BODY_PREFIX)}: {message}")
                continue
            name, index = found
            self.add_error(name, _indexed(name, index, message))
            placed += 1
        if not failures:
            self.add_error(None, f"{error.code}: {error.message}")
        return placed

    def place_report(self, report: ValidationReport) -> int:
        """Note each report error beside its field (the report itself shows them all); an entry
        whose path matches no field stays in the report only. Returns how many were placed."""
        placed = 0
        for entry in report.errors:
            found = field_for_path(entry.location.field or "", self.fields)
            if found is None:
                continue
            name, index = found
            self.report_notes.setdefault(name, []).append(_indexed(name, index, entry_text(entry)))
            placed += 1
        return placed

    # --- rendering -------------------------------------------------------------------------

    def _row(self, name: str) -> Row:
        return Row(self[name], tuple(self.report_notes.get(name, ())))

    def sections(self) -> list[Section]:
        return [
            Section(
                title, field_anchor(anchor), tuple(self._row(n) for n in names if n in self.fields)
            )
            for title, anchor, names in SECTIONS
        ]

    def axes(self) -> list[AxisRow]:
        rows = []
        for axis in AXES:
            setting, nuance = axis_field(axis, "setting"), axis_field(axis, "nuance")
            notes = (*self.report_notes.get(setting, ()), *self.report_notes.get(nuance, ()))
            rows.append(
                AxisRow(
                    axis,
                    axis.replace("_", " "),
                    field_anchor(f"definition.dispositions.{axis}"),
                    self[setting],
                    self[nuance],
                    tuple(notes),
                )
            )
        return rows

    def anchors(self) -> list[str]:
        """Every report anchor the form renders (fields, axes and sections), for the report's
        nearest-ancestor links."""
        found = [
            field_anchor(field_path(n)) for n in self.fields if n not in (OWNER, CLEAR_IDENTITY)
        ]
        found += [field_anchor(f"definition.dispositions.{axis}") for axis in AXES]
        found += [field_anchor(a) for _, a, _ in SECTIONS]
        found += [
            field_anchor("definition.dispositions"),
            field_anchor("definition.likes_dislikes"),
        ]
        return found

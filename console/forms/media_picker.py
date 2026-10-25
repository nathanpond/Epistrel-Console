"""Media slots on the catalog editors (#71): a character's images and reference images, a
scenario's cover, gallery and videos.

Each slot is a media-id text input (typed or pasted ids are accepted: trimmed, upper-cased, and
checked against the Engine's ULID pattern), a thumbnail through the proxy, the asset's metadata,
and a "Choose…" button that loads the picker (``console.views.media_picker``). The primary image
and the cover are plain fields (``public_card__images__primary``, ``public_card__media__cover``);
each list is a Django formset with ``can_order`` and ``can_delete``, prefixed by its Engine path
joined with ``__`` (:data:`CHARACTER_SECTIONS`, :data:`SCENARIO_SECTIONS`).

A section is sent whole or not at all (:class:`MediaSections`): ``public_card.images`` (primary
and gallery), ``visual_identity.reference_images`` and ``public_card.media`` (cover, gallery and
videos) are each normalised to ids only (``{media_id, audience}`` for a reference image,
``{media_id, caption}`` for a video) and compared, order included, with the draft the page
rendered. A changed section overrides the merge-patch diff's subtree at its path; an unchanged one
is not sent. A removed primary or cover is ``null``, an emptied list ``[]``, a blank caption
``null`` (the asset's caption shows). Create sends a section only when it holds something.

The Console checks, before any call: the list limits ("At most 20 gallery images", "At most 10
reference images", "At most 20 videos"), duplicates within one list ("Already in this list (row
N)"; the primary or cover may also be in its gallery, and reference images are left to the
Engine), a row with an audience or caption but no id, a caption over 500 characters, and, when
the reference section changed, every reference image's audience ("Choose perceivable or
restricted"; a stored image with no audience doesn't block unrelated saves). A section a POST
does not carry (no management form) is taken as the draft's, unchanged.

Row buttons are non-saving submits with no Engine call (:func:`media_op`): ``op=add:<prefix>``
adds a row, ``op=up:<prefix>-<n>`` / ``op=down:<prefix>-<n>`` move one, and
``op=remove:<field>`` empties the primary or cover; a list row is removed by its "Remove" box.

Engine refusals land at their slot (:meth:`MediaSections.place`): the Engine's list index maps to
the row sent at that index (blank and removed rows dropped, in order). ``media_retired`` reads
"This asset is retired — choose another", or "No such asset — choose another" when its
``details.reason`` is ``unknown`` (#164); a refusal or report entry that names only a media id
marks every slot holding it.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar, cast
from urllib.parse import urlencode

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet, formset_factory
from django.forms.utils import ErrorDict, ErrorList
from django.http import QueryDict
from django.urls import reverse

from console.engine.client import EngineError
from console.engine.models import CatalogCharacter, ReportEntry, Scenario, ValidationReport
from console.forms.catalog_character import norm_text
from console.formset_rows import ADD, DOWN, UP, edit_rows
from console.ids import is_ulid
from console.media_slots import SlotInfo
from console.reports import field_anchor

SEP = "__"
BODY_PREFIX = "body."
OP = "op"
ORDER = "ORDER"
DELETE = "DELETE"
IMAGE, VIDEO = "image", "video"
KINDS = (IMAGE, VIDEO)
CAPTION_MAX = 500
LIST_MAX = 20
REFERENCES_MAX = 10
AUDIENCES = ("perceivable", "restricted")
CHOOSE_LABEL = "— choose —"
MEDIA_ID_TEXT = "Not a media id (26-character ULID)"
AUDIENCE_TEXT = "Choose perceivable or restricted"
CAPTION_TEXT = f"At most {CAPTION_MAX} characters"
NEEDS_ID_TEXT = "Choose a media asset or remove this row"
DUPLICATE_TEXT = "Already in this list (row {n})"
RETIRED_TEXT = "This asset is retired — choose another"
UNKNOWN_TEXT = "No such asset — choose another"
MEDIA_RETIRED = "media_retired"
# ``a_b__c_d``: lowercase letters and underscores. One character class, no nested repeat, so
# matching is linear (the earlier ``[a-z_]+(__[a-z_]+)*`` accepted the same strings but could
# backtrack exponentially).
FIELD_NAME = re.compile(r"^[a-z_]+$")
# ``gallery[3].audience`` and the ``gallery.3.audience`` spelling of a validation error's path.
_INDEX = re.compile(r"^(?:\[(?P<a>\d+)\]|\.(?P<b>\d+))(?:\.(?P<rest>.+))?$")


def refusal_text(code: str, message: str, reason: object = None) -> str:
    """A refusal's text at its slot: ``media_retired`` in the Console's words, by
    ``details.reason`` (the Engine answers an unknown id as ``media_retired``, ``reason:
    unknown``); any other code the Engine's ``message``."""
    if code != MEDIA_RETIRED:
        return message
    return UNKNOWN_TEXT if reason == "unknown" else RETIRED_TEXT


# --- fields and row forms -------------------------------------------------------------------------


class MediaSlotField(forms.CharField):
    """A media id: trimmed and upper-cased, then the Engine's ULID pattern; blank is ``""``."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        kwargs.setdefault("label", "Media id")
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> str:
        return str(super().to_python(value) or "").upper()

    def validate(self, value: Any) -> None:
        super().validate(value)
        if value and not is_ulid(value):
            raise ValidationError(MEDIA_ID_TEXT, code="media_id")


class SlotForm(forms.Form):
    """One list row: its media id (the reference and video rows add their own field)."""

    media_id = MediaSlotField()
    extras: ClassVar[tuple[str, ...]] = ()

    def raw(self, name: str) -> str:
        return str(self.data.get(self.add_prefix(name)) or "").strip()

    @property
    def is_blank(self) -> bool:
        """No id, no audience, no caption: a row the formset ignores."""
        return not any(self.raw(name) for name in ("media_id", *self.extras))

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if not self.raw("media_id") and any(self.raw(name) for name in self.extras):
            self.add_error("media_id", NEEDS_ID_TEXT)
        return cleaned

    def value(self) -> Any:
        return self.cleaned_data["media_id"]


class ImageSlotForm(SlotForm):
    pass


class ReferenceImageForm(SlotForm):
    """A reference image: its id and its audience, which has no default."""

    audience = forms.ChoiceField(label="Audience", required=False)
    extras = ("audience",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        choices = [("", CHOOSE_LABEL), *((a, a) for a in AUDIENCES)]
        stored = self.raw("audience")
        if stored and stored not in AUDIENCES:  # an audience the Engine added later: kept
            choices.append((stored, f"{stored} (unknown)"))
        field = self.fields["audience"]
        if isinstance(field, forms.ChoiceField):
            field.choices = choices

    def value(self) -> Any:
        return {
            "media_id": self.cleaned_data["media_id"],
            "audience": self.cleaned_data.get("audience") or None,
        }


class VideoForm(SlotForm):
    """A video and its caption (blank: the asset's caption shows)."""

    caption = forms.CharField(
        label="Caption",
        required=False,
        max_length=CAPTION_MAX,
        error_messages={"max_length": CAPTION_TEXT},
    )
    extras = ("caption",)

    def value(self) -> Any:
        return {
            "media_id": self.cleaned_data["media_id"],
            "caption": norm_text(self.cleaned_data.get("caption")),
        }


if TYPE_CHECKING:
    _FormSetBase = BaseFormSet[SlotForm]
else:  # Django's BaseFormSet is generic in its stubs only
    _FormSetBase = BaseFormSet


class SlotFormSet(_FormSetBase):
    """A list of slots: the rows sent are :meth:`sent_forms`; ``clean`` enforces the limit and,
    when ``distinct``, refuses an id already in the list."""

    limit: ClassVar[int] = LIST_MAX
    over_limit: ClassVar[str] = ""
    distinct: ClassVar[bool] = True

    def _order(self, form: SlotForm, index: int) -> tuple[int, int]:
        order = form.cleaned_data.get(ORDER) if hasattr(form, "cleaned_data") else None
        return (order if isinstance(order, int) else index + 1, index)

    def _kept(self) -> list[tuple[int, SlotForm]]:
        """``(rendered index, form)`` of the rows not removed nor blank, in rendered order."""
        return [
            (index, form)
            for index, form in enumerate(self.forms)
            if hasattr(form, "cleaned_data")
            and not form.cleaned_data.get(DELETE)
            and not form.is_blank
        ]

    def sent_forms(self) -> list[SlotForm]:
        """The rows sent, in the order sent: the Engine's list index is the index here. Blank or
        tied ``ORDER`` values keep the rendered order."""
        kept = sorted(self._kept(), key=lambda pair: self._order(pair[1], pair[0]))
        return [form for _, form in kept]

    def clean(self) -> None:
        kept = self._kept()
        if self.distinct:
            seen: dict[str, int] = {}
            for index, form in kept:
                media_id = form.cleaned_data.get("media_id")
                if not media_id:
                    continue
                if media_id in seen:
                    form.add_error("media_id", DUPLICATE_TEXT.format(n=seen[media_id]))
                else:
                    seen[media_id] = index + 1
        if len(kept) > self.limit:
            raise ValidationError(self.over_limit, code="too_many")

    def values(self) -> list[Any]:
        return [form.value() for form in self.sent_forms()]


def _formset(
    form: type[SlotForm], *, limit: int, over_limit: str, distinct: bool
) -> type[SlotFormSet]:
    base = type(
        f"{form.__name__}Base",
        (SlotFormSet,),
        {"limit": limit, "over_limit": over_limit, "distinct": distinct},
    )
    built = formset_factory(
        form,
        formset=base,
        extra=1,
        can_order=True,
        can_delete=True,
        max_num=limit + 1,
        absolute_max=limit + 5,
    )
    return cast(type[SlotFormSet], built)


ImageSlotFormSet = _formset(
    ImageSlotForm, limit=LIST_MAX, over_limit=f"At most {LIST_MAX} gallery images", distinct=True
)
ReferenceImageFormSet = _formset(
    ReferenceImageForm,
    limit=REFERENCES_MAX,
    over_limit=f"At most {REFERENCES_MAX} reference images",
    distinct=False,
)
VideoFormSet = _formset(
    VideoForm, limit=LIST_MAX, over_limit=f"At most {LIST_MAX} videos", distinct=True
)
# #72: a character added to a running story shows ``images`` (one ordered list of images).
StoryImageFormSet = _formset(
    ImageSlotForm, limit=LIST_MAX, over_limit=f"At most {LIST_MAX} images", distinct=True
)


# --- the sections ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListSpec:
    """One list of slots: its formset prefix (the Engine path joined with ``__``), title and
    the kind each slot takes."""

    prefix: str
    title: str
    kind: str
    formset: type[SlotFormSet]

    @property
    def path(self) -> str:
        return self.prefix.replace(SEP, ".")

    @property
    def leaf(self) -> str:
        return self.path.rsplit(".", 1)[1]

    @property
    def limit(self) -> int:
        return self.formset.limit


@dataclass(frozen=True)
class SingleSpec:
    """The primary image or the cover: one plain field."""

    name: str
    title: str
    kind: str = IMAGE

    @property
    def path(self) -> str:
        return self.name.replace(SEP, ".")

    @property
    def leaf(self) -> str:
        return self.path.rsplit(".", 1)[1]


@dataclass(frozen=True)
class SectionSpec:
    """A section sent whole: its Engine path, title, and its single slot and lists. A section
    whose path is its one list's (the reference images) is that list itself."""

    path: str
    title: str
    single: SingleSpec | None = None
    lists: tuple[ListSpec, ...] = ()

    @property
    def is_list(self) -> bool:
        return self.single is None and len(self.lists) == 1 and self.lists[0].path == self.path

    @property
    def empty(self) -> Any:
        if self.is_list:
            return []
        value: dict[str, Any] = {self.single.leaf: None} if self.single else {}
        value.update({spec.leaf: [] for spec in self.lists})
        return value


IMAGES = "public_card.images"
REFERENCES = "visual_identity.reference_images"
MEDIA = "public_card.media"
PRIMARY = "public_card__images__primary"
COVER = "public_card__media__cover"

CHARACTER_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        IMAGES,
        "Images",
        SingleSpec(PRIMARY, "Primary image"),
        (ListSpec("public_card__images__gallery", "Gallery", IMAGE, ImageSlotFormSet),),
    ),
    SectionSpec(
        REFERENCES,
        "Reference images",
        None,
        (
            ListSpec(
                "visual_identity__reference_images",
                "Reference images",
                IMAGE,
                ReferenceImageFormSet,
            ),
        ),
    ),
)
SCENARIO_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        MEDIA,
        "Media",
        SingleSpec(COVER, "Cover"),
        (
            ListSpec("public_card__media__gallery", "Gallery", IMAGE, ImageSlotFormSet),
            ListSpec("public_card__media__videos", "Videos", VIDEO, VideoFormSet),
        ),
    ),
)

STORY_IMAGES = "images"
# #72: the add-character form's images (``POST /stories/{id}/characters`` ``images``).
STORY_CHARACTER_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        STORY_IMAGES,
        "Images",
        None,
        (ListSpec(STORY_IMAGES, "Images", IMAGE, StoryImageFormSet),),
    ),
)
ALL_SECTIONS = (*CHARACTER_SECTIONS, *SCENARIO_SECTIONS, *STORY_CHARACTER_SECTIONS)


def known_fields() -> dict[str, tuple[str, int | None]]:
    """Every slot field the picker and the row partial accept: name → (kind, list limit; ``None``
    for a single slot)."""
    found: dict[str, tuple[str, int | None]] = {}
    for section in ALL_SECTIONS:
        if section.single is not None:
            found[section.single.name] = (section.single.kind, None)
        for spec in section.lists:
            found[spec.prefix] = (spec.kind, spec.limit)
    return found


def list_spec(prefix: str) -> ListSpec | None:
    for section in ALL_SECTIONS:
        for spec in section.lists:
            if spec.prefix == prefix:
                return spec
    return None


# --- the draft's media as the sections see it -----------------------------------------------------


def character_media(draft: CatalogCharacter) -> dict[str, Any]:
    """The character's two sections, normalised to ids (the edit snapshot's original)."""
    images = draft.images
    identity = draft.visual_identity
    return {
        IMAGES: {
            "primary": images.primary.media_id if images.primary else None,
            "gallery": [image.media_id for image in images.gallery],
        },
        REFERENCES: [
            {"media_id": r.media_id, "audience": r.audience}
            for r in (identity.reference_images if identity else [])
        ],
    }


def scenario_media(draft: Scenario) -> dict[str, Any]:
    """The scenario's ``public_card.media``, normalised (a blank caption is ``None``)."""
    media = draft.public_card.media
    return {
        MEDIA: {
            "cover": media.cover,
            "gallery": list(media.gallery),
            "videos": [
                {"media_id": v.media_id, "caption": norm_text(v.caption)} for v in media.videos
            ],
        }
    }


def media_ids(original: Mapping[str, Any]) -> list[str]:
    """Every id a set of sections holds, in order, de-duplicated."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, Mapping):
            if isinstance(value.get("media_id"), str):
                found.append(value["media_id"])
                return
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(dict(original))
    return list(dict.fromkeys(found))


def _management(prefix: str, total: int, limit: int) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": str(limit + 1),
    }


def section_data(specs: Iterable[SectionSpec], original: Mapping[str, Any]) -> dict[str, list[str]]:
    """``original``'s sections as a POST carries them: each list's rows plus one blank row."""
    data: dict[str, list[str]] = {}
    for section in specs:
        value = original.get(section.path, section.empty)
        if section.single is not None:
            single = value.get(section.single.leaf) if isinstance(value, Mapping) else None
            data[section.single.name] = [single or ""]
        for spec in section.lists:
            if section.is_list:
                items = value if isinstance(value, list) else []
            else:
                items = value.get(spec.leaf, []) if isinstance(value, Mapping) else []
            for index, item in enumerate(items):
                row = item if isinstance(item, Mapping) else {"media_id": item}
                for key, cell in row.items():
                    data[f"{spec.prefix}-{index}-{key}"] = ["" if cell is None else str(cell)]
                data[f"{spec.prefix}-{index}-{ORDER}"] = [str(index + 1)]
            data[f"{spec.prefix}-{len(items)}-{ORDER}"] = [str(len(items) + 1)]
            for key, cell in _management(spec.prefix, len(items) + 1, spec.limit).items():
                data[key] = [cell]
    return data


# --- row buttons ----------------------------------------------------------------------------------


def _renumber(data: QueryDict, prefix: str) -> None:
    try:
        total = int(data.get(f"{prefix}-TOTAL_FORMS", "0"))
    except ValueError:
        return
    for index in range(max(0, total)):
        data[f"{prefix}-{index}-{ORDER}"] = str(index + 1)


def media_op(data: QueryDict, specs: Iterable[SectionSpec]) -> QueryDict | None:
    """The posted form after a media row button, or ``None`` when ``op`` names none:
    ``remove:<primary|cover>`` empties it, ``add:<list>`` appends a blank row (up to the limit
    plus the blank one), ``up:<list>-<n>`` / ``down:<list>-<n>`` swap row ``n`` with its
    neighbour (``ORDER`` renumbered to the new rendered order). The ``op`` key is dropped."""
    kind, _, target = (data.get(OP) or "").partition(":")
    specs = tuple(specs)
    singles = {s.single.name for s in specs if s.single is not None}
    lists = {spec.prefix: spec for s in specs for spec in s.lists}
    edited: QueryDict | None = None
    if kind == "remove" and target in singles:
        edited = data.copy()
        edited[target] = ""
    elif kind == "add" and target in lists:
        spec = lists[target]
        request = data.copy()
        request[ADD] = "1"
        edited = edit_rows(request, target, max_rows=spec.limit + 1, min_rows=0)
        _renumber(edited, target)
    elif kind in (UP, DOWN):
        prefix, _, index = target.rpartition("-")
        if prefix in lists and index.isdecimal():
            request = data.copy()
            request[kind] = index
            edited = edit_rows(request, prefix, max_rows=lists[prefix].limit + 1, min_rows=0)
            _renumber(edited, prefix)
    if edited is None:
        return None
    edited.pop(OP, None)
    for button in (ADD, UP, DOWN):
        edited.pop(button, None)
    return edited


# --- rendering ------------------------------------------------------------------------------------


def slot_row_url(field_name: str, index: int | None, kind: str) -> str:
    params: dict[str, str] = {"field": field_name, "kind": kind}
    if index is not None:
        params["index"] = str(index)
    return f"{reverse('media:slot-row')}?{urlencode(params)}"


def picker_url(field_name: str, index: int | None, kind: str) -> str:
    params: dict[str, str] = {"field": field_name, "kind": kind}
    if index is not None:
        params["index"] = str(index)
    return f"{reverse('media:picker')}?{urlencode(params)}"


@dataclass(frozen=True)
class SlotRow:
    """One slot as ``_slot_row.html`` renders it: its input names and values, the looked-up
    ``info`` (``None`` when blank), its errors and report notes."""

    field: str  # the formset prefix, or the single field's name
    index: int | None  # ``None`` for the primary image or the cover
    kind: str
    path: str  # the Engine path (``public_card.images.gallery[2]``): the anchor's source
    media_id: str = ""
    info: SlotInfo | None = None
    audience: str | None = None  # a reference row: the posted audience ("" unset)
    audience_choices: tuple[tuple[str, str], ...] = ()
    caption: str | None = None  # a video row: the posted caption
    order: str = ""
    deleted: bool = False
    errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    can_up: bool = False
    can_down: bool = False
    disabled: bool = False

    @property
    def anchor(self) -> str:
        return field_anchor(self.path)

    @property
    def list_anchor(self) -> str:
        """What the picker request includes: the whole list's inputs (its "in use" marks), or
        the single slot's own."""
        if self.index is None:
            return self.anchor
        return field_anchor(self.field.replace(SEP, "."))

    @property
    def number(self) -> int:
        return (self.index or 0) + 1

    def name(self, part: str) -> str:
        if self.index is None:
            return self.field
        return f"{self.field}-{self.index}-{part}"

    @property
    def id_name(self) -> str:
        return self.name("media_id")

    @property
    def is_reference(self) -> bool:
        return self.field == "visual_identity__reference_images"

    @property
    def is_video(self) -> bool:
        return self.kind == VIDEO

    @property
    def is_list(self) -> bool:
        return self.index is not None

    @property
    def row_url(self) -> str:
        return slot_row_url(self.field, self.index, self.kind)

    @property
    def picker_url(self) -> str:
        return picker_url(self.field, self.index, self.kind)

    @property
    def upload_url(self) -> str:
        return f"{reverse('media:upload')}?{urlencode({'kind': self.kind})}"

    @property
    def placeholder(self) -> str:
        """A video's caption placeholder: the asset's own caption."""
        return (self.info.caption or "") if self.info is not None else ""


def audience_choices(stored: str | None) -> tuple[tuple[str, str], ...]:
    choices = [("", CHOOSE_LABEL), *((a, a) for a in AUDIENCES)]
    if stored and stored not in AUDIENCES:
        choices.append((stored, f"{stored} (unknown)"))
    return tuple(choices)


@dataclass(frozen=True)
class ShownList:
    spec: ListSpec
    rows: tuple[SlotRow, ...]
    management: Any  # the formset's management form
    errors: tuple[str, ...]
    can_add: bool
    disabled: bool

    @property
    def anchor(self) -> str:
        return field_anchor(self.spec.path)

    @property
    def upload_url(self) -> str:
        return f"{reverse('media:upload')}?{urlencode({'kind': self.spec.kind})}"


@dataclass(frozen=True)
class ShownSection:
    spec: SectionSpec
    single: SlotRow | None
    lists: tuple[ShownList, ...]
    errors: tuple[str, ...] = ()  # refusals and report notes naming the section itself

    @property
    def anchor(self) -> str:
        return field_anchor(self.spec.path)

    @property
    def fieldset_id(self) -> str:
        """The fieldset's id: the section's anchor, unless its one list carries that anchor."""
        return f"{self.anchor}-section" if self.spec.is_list else self.anchor


class SinglesForm(forms.Form):
    """The sections' single slots (primary image, cover), one :class:`MediaSlotField` each."""

    def __init__(self, data: QueryDict, names: Iterable[str]) -> None:
        super().__init__(data)
        for name in names:
            self.fields[name] = MediaSlotField()


@dataclass
class _Placed:
    """Errors and report notes the sections hold beyond the forms' own."""

    section_errors: dict[str, list[str]] = field(default_factory=dict)
    notes: dict[tuple[str, int | None], list[str]] = field(default_factory=dict)


class MediaSections:
    """The media sections of one editor, bound to ``data`` (a POST, or :func:`section_data` of
    the draft): a section ``data`` does not carry is filled from ``original``, unchanged.
    ``infos`` is the per-slot metadata the page looked up (``console.media_slots``)."""

    def __init__(
        self,
        data: QueryDict | Mapping[str, list[str]],
        specs: tuple[SectionSpec, ...],
        *,
        original: Mapping[str, Any],
        infos: Mapping[str, SlotInfo],
        disabled: bool = False,
    ) -> None:
        self.specs = specs
        self.original = {s.path: original.get(s.path, s.empty) for s in specs}
        self.infos = dict(infos)
        self.disabled = disabled
        self.data = self._merged(data)
        self.singles = SinglesForm(
            self.data, [s.single.name for s in specs if s.single is not None]
        )
        self.formsets: dict[str, SlotFormSet] = {
            spec.prefix: spec.formset(self.data, prefix=spec.prefix)
            for section in specs
            for spec in section.lists
        }
        self._placed = _Placed()

    def _merged(self, data: QueryDict | Mapping[str, list[str]]) -> QueryDict:
        merged = QueryDict(mutable=True)
        if isinstance(data, QueryDict):
            for key in data:
                merged.setlist(key, data.getlist(key))
        else:
            for key, values in data.items():
                merged.setlist(key, list(values))
        base = section_data(self.specs, self.original)
        for section in self.specs:
            if section.single is not None and section.single.name not in merged:
                merged.setlist(section.single.name, base[section.single.name])
            for spec in section.lists:
                if f"{spec.prefix}-TOTAL_FORMS" in merged:
                    continue
                start = f"{spec.prefix}-"
                for key in [k for k in merged if k.startswith(start)]:
                    del merged[key]
                for key, values in base.items():
                    if key.startswith(start):
                        merged.setlist(key, values)
        return merged

    # --- validation ------------------------------------------------------------------------

    def is_valid(self) -> bool:
        results = [self.singles.is_valid(), *(fs.is_valid() for fs in self.formsets.values())]
        return all(results)

    def quiet(self) -> None:
        """Show the bound state without its errors (a first render, a row button)."""
        quieted: Any = self.singles
        quieted._errors = ErrorDict()
        for formset in self.formsets.values():
            quieted = formset
            quieted._errors = []
            quieted._non_form_errors = ErrorList()
            for form in formset.forms:
                quieted = form
                quieted._errors = ErrorDict()
                if not hasattr(form, "cleaned_data"):
                    form.cleaned_data = {}

    # --- what is sent ----------------------------------------------------------------------

    def value(self, section: SectionSpec) -> Any:
        """The section as cleaned, in the original's normalised shape."""
        if section.is_list:
            return self.formsets[section.lists[0].prefix].values()
        value: dict[str, Any] = {}
        if section.single is not None:
            value[section.single.leaf] = self.singles.cleaned_data.get(section.single.name) or None
        for spec in section.lists:
            value[spec.leaf] = self.formsets[spec.prefix].values()
        return value

    def changed(self, *, skip: Iterable[str] = ()) -> dict[str, Any]:
        """``{path: value}`` of each section that differs from the original (order matters)."""
        skipped = set(skip)
        found: dict[str, Any] = {}
        for section in self.specs:
            if section.path in skipped:
                continue
            value = self.value(section)
            if value != self.original[section.path]:
                found[section.path] = value
        return found

    def require_audiences(self, changed: Mapping[str, Any]) -> bool:
        """When the reference section changed, every sent reference image needs its audience
        (else "Choose perceivable or restricted" at the row). Returns whether all have one."""
        ok = True
        for section in self.specs:
            if section.path != REFERENCES or REFERENCES not in changed:
                continue
            for form in self.formsets[section.lists[0].prefix].sent_forms():
                if not form.cleaned_data.get("audience"):
                    form.add_error("audience", AUDIENCE_TEXT)
                    ok = False
        return ok

    def patch(self, patch: Mapping[str, Any], changed: Mapping[str, Any]) -> dict[str, Any]:
        """``patch`` with each changed section set whole at its path (overriding the diff's
        subtree there; a reference section merges with the patch's other ``visual_identity``
        keys)."""
        out: dict[str, Any] = dict(patch)
        for path, value in changed.items():
            _set(out, path, value)
        return out

    def create_parts(self, changed: Mapping[str, Any]) -> dict[str, Any]:
        """``{path: value}`` of the sections a create sends: each changed (so non-empty) one,
        with ``None`` and ``[]`` left out (pass it to :meth:`patch` with the create body)."""
        parts: dict[str, Any] = {}
        for path, value in changed.items():
            if isinstance(value, Mapping):
                value = {k: v for k, v in value.items() if v not in (None, [])}
            if value:
                parts[path] = value
        return parts

    # --- Engine errors and reports ---------------------------------------------------------

    def _target(self, path: str) -> tuple[str, SlotFormSet | None, int | None, str | None] | None:
        """What an Engine path names: ``(section path, formset or None, sent index, rest)``."""
        path = path.removeprefix(BODY_PREFIX)
        for section in self.specs:
            if section.single is not None and path == section.single.path:
                return section.path, None, None, None
            for spec in section.lists:
                if path == spec.path:
                    return section.path, self.formsets[spec.prefix], None, None
                if path.startswith(spec.path):
                    match = _INDEX.match(path[len(spec.path) :])
                    if match:
                        index = int(match["a"] or match["b"])
                        return section.path, self.formsets[spec.prefix], index, match["rest"]
            if path == section.path:
                return section.path, None, None, ""
        return None

    def place(self, path: str, message: str, code: str = "", reason: object = None) -> bool:
        """Place one refusal at its slot; ``False`` when ``path`` names none of the sections.
        ``reason``: the refusal's ``details.reason``."""
        target = self._target(path)
        if target is None:
            return False
        text = refusal_text(code, message, reason)
        section_path, formset, index, rest = target
        if formset is None:
            spec = next(s for s in self.specs if s.path == section_path)
            if rest == "" or spec.single is None:
                self._placed.section_errors.setdefault(section_path, []).append(text)
            else:
                self.singles.add_error(spec.single.name, text)
            return True
        sent = formset.sent_forms()
        if index is None or index >= len(sent):
            formset.non_form_errors().append(text if index is None else f"Row {index + 1}: {text}")
            return True
        row = sent[index]
        name = (rest or "media_id").split(".", 1)[0]
        row.add_error(name if name in row.fields else "media_id", text)
        return True

    def place_media_id(self, media_id: str, message: str) -> int:
        """Mark every slot holding ``media_id``; returns how many."""
        marked = 0
        for section in self.specs:
            single = section.single
            if single is not None and self.singles.cleaned_data.get(single.name) == media_id:
                self.singles.add_error(single.name, message)
                marked += 1
            for spec in section.lists:
                for form in self.formsets[spec.prefix].sent_forms():
                    if form.cleaned_data.get("media_id") == media_id:
                        form.add_error("media_id", message)
                        marked += 1
        return marked

    def place_error(self, error: EngineError) -> Callable[[str, str, str], bool]:
        """The hook a main form's ``place_error`` calls per failure (``extra=``). A refusal with
        no path but a ``details.media_id`` is placed here first, at every slot holding it."""
        media_id = error.details.get("media_id")
        if (
            isinstance(media_id, str)
            and not isinstance(error.details.get("field"), str)
            and not error.details.get("errors")
        ):
            reason = error.details.get("reason")
            self.place_media_id(media_id, refusal_text(error.code, error.message, reason))
        return partial(self.place, reason=error.details.get("reason"))

    def place_report(self, report: ValidationReport) -> int:
        """Note each report error beside its slot (a ``media_id``-only entry at every slot
        holding it); returns how many were placed."""
        placed = 0
        for entry in report.errors:
            text = RETIRED_TEXT if entry.code == MEDIA_RETIRED else _entry_text(entry)
            location = entry.location
            target = self._target(location.field or "") if location.field else None
            if target is not None:
                self._placed.notes.setdefault(self._note_key(*target), []).append(text)
                placed += 1
            elif location.media_id:
                for key in self._holding(location.media_id):
                    self._placed.notes.setdefault(key, []).append(text)
                    placed += 1
        return placed

    def _note_key(
        self, section_path: str, formset: SlotFormSet | None, index: int | None, rest: str | None
    ) -> tuple[str, int | None]:
        """Where a report note shows: a row (by its rendered index), a single slot, a list or
        the section itself."""
        if formset is None:
            spec = next(s for s in self.specs if s.path == section_path)
            if rest == "" or spec.single is None:
                return (section_path, None)
            return (spec.single.name, None)
        prefix = formset.prefix or ""
        if index is None:
            return (prefix, None)
        sent = formset.sent_forms()
        if index < len(sent):
            return (prefix, formset.forms.index(sent[index]))
        return (prefix, None)

    def _holding(self, media_id: str) -> list[tuple[str, int | None]]:
        keys: list[tuple[str, int | None]] = []
        for section in self.specs:
            single = section.single
            if single is not None and self._raw_id(single.name) == media_id:
                keys.append((single.name, None))
            for spec in section.lists:
                for index, form in enumerate(self.formsets[spec.prefix].forms):
                    if form.raw("media_id").upper() == media_id:
                        keys.append((spec.prefix, index))
        return keys

    def _raw_id(self, name: str) -> str:
        return str(self.data.get(name) or "").strip().upper()

    # --- rendering -------------------------------------------------------------------------

    def info(self, media_id: str) -> SlotInfo | None:
        if not media_id:
            return None
        return self.infos.get(media_id) or SlotInfo(media_id)

    def _errors(self, form: forms.BaseForm, *names: str) -> tuple[str, ...]:
        errors = form.errors if form.is_bound else ErrorDict()
        found = [str(e) for name in names for e in errors.get(name, [])]
        found += [str(e) for e in errors.get("__all__", [])] if form is not self.singles else []
        return tuple(dict.fromkeys(found))

    def _single_row(self, spec: SingleSpec) -> SlotRow:
        media_id = self._raw_id(spec.name)
        return SlotRow(
            field=spec.name,
            index=None,
            kind=spec.kind,
            path=spec.path,
            media_id=media_id,
            info=self.info(media_id) if is_ulid(media_id) else None,
            errors=self._errors(self.singles, spec.name),
            notes=tuple(self._placed.notes.get((spec.name, None), ())),
            disabled=self.disabled,
        )

    def _list_rows(self, spec: ListSpec) -> tuple[SlotRow, ...]:
        formset = self.formsets[spec.prefix]
        total = len(formset.forms)
        rows: list[SlotRow] = []
        for index, form in enumerate(formset.forms):
            media_id = form.raw("media_id").upper()
            audience = form.raw("audience") if "audience" in form.fields else None
            caption = form.raw("caption") if "caption" in form.fields else None
            rows.append(
                SlotRow(
                    field=spec.prefix,
                    index=index,
                    kind=spec.kind,
                    path=f"{spec.path}[{index}]",
                    media_id=media_id,
                    info=self.info(media_id) if is_ulid(media_id) else None,
                    audience=audience,
                    audience_choices=audience_choices(audience) if audience is not None else (),
                    caption=caption,
                    order=form.raw(ORDER) or str(index + 1),
                    deleted=bool(form.raw(DELETE)),
                    errors=self._errors(form, "media_id", "audience", "caption"),
                    notes=tuple(self._placed.notes.get((spec.prefix, index), ())),
                    can_up=index > 0,
                    can_down=index < total - 1,
                    disabled=self.disabled,
                )
            )
        return tuple(rows)

    def shown(self) -> list[ShownSection]:
        shown: list[ShownSection] = []
        for section in self.specs:
            lists = []
            for spec in section.lists:
                formset = self.formsets[spec.prefix]
                errors = [str(e) for e in formset.non_form_errors()]
                errors += self._placed.notes.get((spec.prefix, None), [])
                lists.append(
                    ShownList(
                        spec,
                        self._list_rows(spec),
                        formset.management_form,
                        tuple(errors),
                        len(formset.forms) <= spec.limit,
                        self.disabled,
                    )
                )
            errors = self._placed.section_errors.get(section.path, [])
            if not section.is_list:
                errors = [*errors, *self._placed.notes.get((section.path, None), [])]
            shown.append(
                ShownSection(
                    section,
                    self._single_row(section.single) if section.single else None,
                    tuple(lists),
                    tuple(errors),
                )
            )
        return shown

    def by_path(self) -> dict[str, ShownSection]:
        """The shown sections keyed by path with ``.`` → ``_`` (for templates)."""
        return {s.spec.path.replace(".", "_"): s for s in self.shown()}

    def anchors(self) -> list[str]:
        """Every anchor the sections render: sections, lists, the singles and each row."""
        found: list[str] = []
        for section in self.specs:
            found.append(field_anchor(section.path))
            if section.single is not None:
                found.append(field_anchor(section.single.path))
            for spec in section.lists:
                found.append(field_anchor(spec.path))
                found += [
                    field_anchor(f"{spec.path}[{i}]")
                    for i in range(len(self.formsets[spec.prefix].forms))
                ]
        return found


def _entry_text(entry: ReportEntry) -> str:
    return f"{entry.code}: {entry.rule}" if entry.rule else entry.code


def _set(doc: dict[str, Any], path: str, value: Any) -> None:
    """Set ``value`` at dotted ``path`` in ``doc``, creating (or copying) objects on the way;
    a ``None`` on the way (a cleared group) is left as it is."""
    *groups, leaf = path.split(".")
    node = doc
    for group in groups:
        child = node.get(group)
        if child is None and group in node:
            return  # the group is being removed (``visual_identity: null``): it wins
        child = dict(child) if isinstance(child, Mapping) else {}
        node[group] = child
        node = child
    node[leaf] = value


def form_field_name(value: str) -> bool:
    """Whether ``value`` is a well-formed slot field name (``a_b__c_d``)."""
    return bool(FIELD_NAME.match(value))

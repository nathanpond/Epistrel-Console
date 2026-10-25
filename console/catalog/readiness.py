"""What a catalog character's saved draft still needs before it can publish (#72).

The character edit page shows "Before this can be published: …" above the form, listing each
field the Engine's publish requires that the **saved** draft (the edit page's GET, never the
re-rendered input) lacks, in form order, each linking to its ``#f-<path>`` anchor. The rules
mirror the Engine's character content check (``core.catalog.checks.character_content``):

- the strings of its ``PUBLISH_REQUIRED`` (name, public description, appearance, personality,
  values & motivations, speech style, vocabulary) are missing when unset or blank, and each of
  the five disposition axes when unset;
- the true age when unset; the content rating when unset, when the ratings list does not hold it
  ("content rating (missing)") or holds it retired ("content rating (retired)"). When the
  ratings read failed a set rating counts as present, with a note that it was not checked;
- an audience on every reference image ("reference image N: audience");
- a primary image when the deployment's ``require_primary_image`` is on (one ``/admin/settings``
  read; ``None`` when it failed, and the item is left out);
- with a visual identity, its preferred image model, base appearance prompt, negative prompt and
  seed (the Engine's ``IDENTITY_REQUIRED``).

Lists (likes, dislikes, mannerisms, …) may be empty, and retired images are left to #71's tags.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from console.engine.choices import MISSING_SUFFIX, RETIRED_SUFFIX, RatingChoices, error_text
from console.engine.models import CatalogCharacter
from console.forms.catalog_character import AXES, draft_to_write
from console.forms.media_picker import IMAGES, REFERENCES, character_media
from console.reports import field_anchor

RATING_PATH = "metadata.content_rating_id"
# (label, Engine path) in form order: the card, the definition, the dispositions, the
# interaction style. The primary image (after the card) and the rest are added in between.
_CARD = (("name", "public_card.name"), ("public description", "public_card.public_description"))
_DEFINITION = (
    ("true age", "definition.true_age"),
    ("appearance", "definition.appearance"),
    ("personality", "definition.personality"),
    ("values & motivations", "definition.values_motivations"),
)
_STYLE = (
    ("speech style", "definition.interaction_style.speech_style"),
    ("vocabulary", "definition.interaction_style.vocabulary"),
)
_IDENTITY = (
    ("visual identity: preferred image model", "visual_identity.preferred_image_model"),
    ("visual identity: base appearance prompt", "visual_identity.base_appearance_prompt"),
    ("visual identity: negative prompt", "visual_identity.negative_prompt"),
    ("visual identity: seed", "visual_identity.seed"),
)


@dataclass(frozen=True)
class ReadinessItem:
    """One thing the draft lacks: its label and the form anchor it links to."""

    label: str
    field_id: str


@dataclass(frozen=True)
class Readiness:
    """The banner: the missing items and notes on what could not be checked. Hidden when both
    are empty."""

    items: tuple[ReadinessItem, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def shown(self) -> bool:
        return bool(self.items or self.notes)

    def to_json(self) -> dict[str, Any]:
        return {
            "items": [[item.label, item.field_id] for item in self.items],
            "notes": list(self.notes),
        }

    @classmethod
    def from_json(cls, value: object) -> Readiness:
        """The banner as an edit snapshot carries it (anything malformed: no banner)."""
        if not isinstance(value, Mapping):
            return cls()
        try:
            items = tuple(ReadinessItem(str(a), str(b)) for a, b in value.get("items") or [])
            notes = tuple(str(n) for n in value.get("notes") or [])
        except TypeError, ValueError:
            return cls()
        return cls(items, notes)


def _at(document: Mapping[str, Any], path: str) -> Any:
    found: Any = document
    for part in path.split("."):
        if not isinstance(found, Mapping):
            return None
        found = found.get(part)
    return found


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _item(label: str, path: str) -> ReadinessItem:
    return ReadinessItem(label, field_anchor(path))


def _rating(rating_id: object, ratings: RatingChoices) -> tuple[ReadinessItem | None, str]:
    """The content rating's item (``None`` when it is fine) and a note when unchecked."""
    if _missing(rating_id):
        return _item("content rating", RATING_PATH), ""
    if ratings.error is not None:
        return None, f"content rating not checked ({error_text(ratings.error)})"
    label = dict(ratings.choices).get(str(rating_id))
    if label is None or label.endswith(MISSING_SUFFIX):
        return _item("content rating (missing)", RATING_PATH), ""
    if label.endswith(RETIRED_SUFFIX):
        return _item("content rating (retired)", RATING_PATH), ""
    return None, ""


def missing_for_publish(
    draft: CatalogCharacter, ratings: RatingChoices, *, require_primary_image: bool | None
) -> Readiness:
    """What ``draft`` (the saved draft) still needs before a publish, in form order.
    ``ratings`` is the edit page's rating read (``rating_choices`` with the draft's rating as
    ``current``); ``require_primary_image`` the deployment setting (``None``: not read)."""
    document = draft_to_write(draft)
    media = character_media(draft)
    items = [_item(label, path) for label, path in _CARD if _missing(_at(document, path))]
    images = media.get(IMAGES) or {}
    if require_primary_image and not images.get("primary"):
        items.append(_item("primary image", "public_card.images.primary"))
    items += [_item(label, path) for label, path in _DEFINITION if _missing(_at(document, path))]
    dispositions = _at(document, "definition.dispositions") or {}
    for axis in AXES:
        value = dispositions.get(axis)
        if not isinstance(value, Mapping) or _missing(value.get("setting")):
            label = f"disposition: {axis.replace('_', ' ')}"
            items.append(_item(label, f"definition.dispositions.{axis}"))
    items += [_item(label, path) for label, path in _STYLE if _missing(_at(document, path))]
    notes: list[str] = []
    rating_item, note = _rating(_at(document, RATING_PATH), ratings)
    if rating_item is not None:
        items.append(rating_item)
    if note:
        notes.append(note)
    if document.get("visual_identity") is not None:
        items += [_item(label, path) for label, path in _IDENTITY if _missing(_at(document, path))]
    for index, reference in enumerate(media.get(REFERENCES) or []):
        if isinstance(reference, Mapping) and not reference.get("audience"):
            label = f"reference image {index + 1}: audience"
            items.append(_item(label, f"{REFERENCES}[{index}]"))
    return Readiness(tuple(items), tuple(notes))

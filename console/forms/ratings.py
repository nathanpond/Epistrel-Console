"""The content-rating form (#49): create (``RatingFields``) and edit (``RatingReplacement``).

Bounds are the recorded schema's: name 1 to 80, description ≤ 4000, each guidance ≤ 8000, display
order a 32-bit integer, at most 256 policy ids and 256 parents. Everything else (unknown ids,
an inheritance loop) is left to the Engine's 422.

The policy and parent options are the caller's ``(id, label)`` lists. ``None`` means the list
could not be read: the field is then left out and an edit resends the rating's current value.
A new rating has no policy field at all: rating policies are scoped to an existing rating, so
none can exist yet (:data:`CREATE_POLICY_NOTE`).

:func:`unchanged` is the no-change check: text compared with CRLF → LF and surrounding
whitespace stripped (empty and null alike), ids as sets, display order as an int.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from django import forms

from console.engine.models import ContentRatingSummary

NAME_MAX = 80
DESCRIPTION_MAX = 4000
GUIDANCE_MAX = 8000
IDS_MAX = 256
ORDER_MIN, ORDER_MAX = -(2**31), 2**31 - 1
TEXT_FIELDS = ("name", "description", "prompt_guidance", "image_guidance")
CREATE_POLICY_NOTE = "Save the rating, then add rating policies under Guardrails → Policies"
POLICIES_UNAVAILABLE = "The policies could not be read: the rating's policies are kept as they are."
PARENTS_UNAVAILABLE = (
    "The content ratings could not be read: what this rating inherits from is kept as it is."
)

type Options = Sequence[tuple[str, str]] | None


def normalised(value: object) -> str:
    """Text as the no-change check compares it: CRLF → LF, stripped; ``None`` is empty."""
    return "" if value is None else str(value).replace("\r\n", "\n").strip()


def rating_initial(rating: ContentRatingSummary) -> dict[str, Any]:
    """The edit form's starting values: the rating's latest version."""
    return {
        "name": rating.name,
        "description": rating.description,
        "display_order": rating.display_order,
        "prompt_guidance": rating.prompt_guidance,
        "image_guidance": rating.image_guidance,
        "policy_ids": list(rating.policy_ids),
        "inherits_from": list(rating.inherits_from),
    }


def replacement_of(rating: ContentRatingSummary, **changes: Any) -> dict[str, Any]:
    """A ``RatingReplacement`` body that resends ``rating`` as it is, with ``changes``."""
    body: dict[str, Any] = {
        "name": rating.name,
        "description": rating.description,
        "display_order": rating.display_order,
        "prompt_guidance": rating.prompt_guidance,
        "image_guidance": rating.image_guidance,
        "policy_ids": list(rating.policy_ids),
        "inherits_from": list(rating.inherits_from),
    }
    body.update(changes)
    return body


def unchanged(body: Mapping[str, Any], rating: ContentRatingSummary) -> bool:
    """True when the replacement ``body`` would write what ``rating`` already holds."""
    current = replacement_of(rating)
    for name in TEXT_FIELDS:
        if normalised(body.get(name)) != normalised(current[name]):
            return False
    for name in ("policy_ids", "inherits_from"):
        if set(body.get(name) or ()) != set(current[name]):
            return False
    return int(body["display_order"]) == int(current["display_order"])


def _text(label: str, max_length: int, *, rows: int, required: bool = False) -> forms.CharField:
    return forms.CharField(
        label=label,
        max_length=max_length,
        required=required,
        widget=forms.Textarea(attrs={"rows": rows, "cols": 80}),
    )


class RatingForm(forms.Form):
    """A rating's fields. ``creating`` drops the policy picker and makes the display order
    optional (blank: the Engine puts the rating last)."""

    name = forms.CharField(label="Name", max_length=NAME_MAX)
    description = _text("Description", DESCRIPTION_MAX, rows=3)
    display_order = forms.IntegerField(
        label="Display order", min_value=ORDER_MIN, max_value=ORDER_MAX
    )
    prompt_guidance = _text("Prompt guidance", GUIDANCE_MAX, rows=8)
    image_guidance = _text("Image guidance", GUIDANCE_MAX, rows=4)
    policy_ids = forms.MultipleChoiceField(
        label="Policies",
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Live policies scoped to this rating (create them under Guardrails → Policies).",
    )
    inherits_from = forms.MultipleChoiceField(
        label="Inherits from",
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="This rating also applies the policies of every rating it inherits from. "
        "None ticked: it inherits nothing.",
    )

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        policies: Options,
        parents: Options,
        creating: bool,
        initial: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(data, initial=dict(initial) if initial is not None else None)
        self.creating = creating
        self.policy_options = list(policies) if policies is not None else None
        self.parent_options = list(parents) if parents is not None else None
        if creating:
            self.fields["display_order"].required = False
            self.fields["display_order"].help_text = "Optional. Blank: placed last (max + 10)."
        self._offer("policy_ids", None if creating else self.policy_options)
        self._offer("inherits_from", self.parent_options)

    def _offer(self, name: str, options: Options) -> None:
        """Set a picker's choices, or leave the field out when there are none to offer."""
        if options is None:
            del self.fields[name]
            return
        field = self.fields[name]
        assert isinstance(field, forms.MultipleChoiceField)  # noqa: S101 - declared above
        field.choices = list(options)

    @property
    def policy_note(self) -> str:
        if self.creating:
            return CREATE_POLICY_NOTE
        return POLICIES_UNAVAILABLE if self.policy_options is None else ""

    @property
    def parents_note(self) -> str:
        return PARENTS_UNAVAILABLE if self.parent_options is None else ""

    def _clean_text(self, name: str) -> str:
        return normalised(self.cleaned_data.get(name))

    def clean_name(self) -> str:
        return self._clean_text("name")

    def clean_description(self) -> str:
        return self._clean_text("description")

    def clean_prompt_guidance(self) -> str:
        return self._clean_text("prompt_guidance")

    def clean_image_guidance(self) -> str:
        return self._clean_text("image_guidance")

    def _clean_ids(self, name: str, options: Options) -> list[str]:
        chosen = set(self.cleaned_data.get(name) or ())
        if len(chosen) > IDS_MAX:
            raise forms.ValidationError(f"Choose at most {IDS_MAX}.")
        return [value for value, _ in options or () if value in chosen]  # in option order

    def clean_policy_ids(self) -> list[str]:
        return self._clean_ids("policy_ids", self.policy_options)

    def clean_inherits_from(self) -> list[str]:
        return self._clean_ids("inherits_from", self.parent_options)

    def _common(self) -> dict[str, Any]:
        data = self.cleaned_data
        return {name: data[name] for name in TEXT_FIELDS}

    def fields_body(self) -> dict[str, Any]:
        """The ``RatingFields`` body: a blank display order is left out (the Engine places
        the rating last); no policies yet."""
        body = self._common()
        if self.cleaned_data.get("display_order") is not None:
            body["display_order"] = self.cleaned_data["display_order"]
        if "inherits_from" in self.fields:
            body["inherits_from"] = self.cleaned_data["inherits_from"]
        return body

    def replacement(self, rating: ContentRatingSummary) -> dict[str, Any]:
        """The ``RatingReplacement`` body; a list whose options could not be read is resent
        as ``rating`` holds it."""
        body = replacement_of(rating, **self._common())
        body["display_order"] = self.cleaned_data["display_order"]
        for name in ("policy_ids", "inherits_from"):
            if name in self.fields:
                body[name] = self.cleaned_data[name]
        return body

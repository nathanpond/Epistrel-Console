"""The play view's story-character forms (#72): add a character to a running story, and promote
one of its characters to a catalog draft.

The bounds are the Engine's (``AdHocCharacter``, ``PromoteRequest``), checked before any call:
on add, a name of 1 to 80 characters, a definition of 1 to 20,000, a true age of 0 to 150, a safe
fallback line of at most 500 and a deflection style of 1 to 2,000 (blank: not sent, for both);
on promote, a name of 1 to 120 (always sent) and an owner (a ``PrincipalField``, always sent).
The add form's participation role (#61, C8) is C7's "Engine default (reactive)" or one of the
three roles; "Fixed" with no role is C8's local error.
Django's stripping stays on, so a whitespace-only name or definition is a required error. CRLF
line ends become LF before a length is checked. Cast size and duplicate names are the Engine's.

Each field's id is its Engine path's report anchor (``f-<name>``), so a refusal names it.
"""

from collections.abc import Sequence
from typing import Any

from django import forms

from console.forms.principal import PrincipalField
from console.participation import (
    CHOOSE_ROLE,
    FIXED_LABEL,
    FIXED_TITLE,
    Role,
    is_role,
    participation_error,
    role_choices,
)

NAME_MAX = 80
DEFINITION_MAX = 20000
SAFE_FALLBACK_MAX = 500
DEFLECTION_MAX = 2000
TRUE_AGE_MIN = 0
TRUE_AGE_MAX = 150
PROMOTE_NAME_MAX = 120
DEFINITION_ROWS = 10
AUTO_ID = "f-%s"


def _lf(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _bounded(value: str, limit: int) -> str:
    if len(value) > limit:
        raise forms.ValidationError(
            f"Ensure this value has at most {limit} characters (it has {len(value)})."
        )
    return value


class AddCharacterForm(forms.Form):
    """``POST /stories/{id}/characters``: the character's own fields, in the page's order, plus
    the story revision the form was opened at (hidden; sent as the expected revision)."""

    name = forms.CharField(label="Name", max_length=NAME_MAX)
    # The lengths of the long texts are checked after CRLF becomes LF.
    definition = forms.CharField(
        label="Definition",
        widget=forms.Textarea(attrs={"rows": DEFINITION_ROWS, "maxlength": DEFINITION_MAX}),
        help_text=f"Free text, up to {DEFINITION_MAX} characters; checked by the input gate",
    )
    true_age = forms.IntegerField(
        label="True age",
        min_value=TRUE_AGE_MIN,
        max_value=TRUE_AGE_MAX,
        help_text=f"{TRUE_AGE_MIN}\u2013{TRUE_AGE_MAX}",
        error_messages={"required": "Enter a true age."},
    )
    safe_fallback_text = forms.CharField(
        label="Safe fallback text",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "maxlength": SAFE_FALLBACK_MAX}),
        help_text=f"optional, up to {SAFE_FALLBACK_MAX} characters",
    )
    deflection_style = forms.CharField(
        label="Deflection style",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": DEFLECTION_MAX}),
        help_text=f"optional, up to {DEFLECTION_MAX} characters; checked by the input gate",
    )
    # #61: ``participation`` ({role, fixed}) is sent only when a role is chosen.
    participation_role = forms.ChoiceField(
        label="Participation role", required=False, choices=role_choices(engine_default=True)
    )
    participation_fixed = forms.BooleanField(
        label=FIXED_LABEL, required=False, help_text=FIXED_TITLE
    )
    expected_revision = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("auto_id", AUTO_ID)
        super().__init__(*args, **kwargs)

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        data = self.cleaned_data
        error = participation_error(
            data.get("participation_role") or None, bool(data.get("participation_fixed"))
        )
        if error is not None and "participation_role" not in self.errors:
            self.add_error("participation_role", error)
        return cleaned

    @property
    def participation(self) -> tuple[Role, bool] | None:
        """``(role, fixed)`` when a role was chosen, else ``None`` (the Engine's default)."""
        role = self.cleaned_data.get("participation_role")
        if not is_role(role):
            return None
        return role, bool(self.cleaned_data.get("participation_fixed"))

    def clean_definition(self) -> str:
        return _bounded(_lf(self.cleaned_data.get("definition") or ""), DEFINITION_MAX)

    def clean_safe_fallback_text(self) -> str | None:
        value = _bounded(_lf(self.cleaned_data.get("safe_fallback_text") or ""), SAFE_FALLBACK_MAX)
        return value or None

    def clean_deflection_style(self) -> str | None:
        value = _bounded(_lf(self.cleaned_data.get("deflection_style") or ""), DEFLECTION_MAX)
        return value or None

    def add_kwargs(self, images: Sequence[str]) -> dict[str, Any]:
        """``add_story_character``'s keyword arguments (bar the idempotency key)."""
        data = self.cleaned_data
        return {
            "name": data["name"],
            "definition": data["definition"],
            "true_age": data["true_age"],
            "safe_fallback_text": data["safe_fallback_text"],
            "deflection_style": data["deflection_style"],
            "images": list(images),
            "expected_revision": data.get("expected_revision"),
            "participation": self.participation,
        }


class PromoteForm(forms.Form):
    """``POST /stories/{id}/characters/{cid}/promote``: the new entry's name and owner (both
    always sent) and its content rating, whose first option (value ``""``) leaves the field
    out so the Engine applies the story's rating. No seed memories (M4)."""

    name = forms.CharField(label="Name", max_length=PROMOTE_NAME_MAX)
    owner = PrincipalField(label="Owner")
    content_rating_id = forms.ChoiceField(label="Content rating", required=False)

    def __init__(
        self, *args: Any, rating_choices: Sequence[tuple[str, str]], **kwargs: Any
    ) -> None:
        kwargs.setdefault("auto_id", AUTO_ID)
        super().__init__(*args, **kwargs)
        field = self.fields["content_rating_id"]
        if isinstance(field, forms.ChoiceField):
            field.choices = list(rating_choices)

    def promote_kwargs(self) -> dict[str, Any]:
        """``promote_story_character``'s keyword arguments (bar the idempotency key)."""
        data = self.cleaned_data
        return {
            "name": data["name"],
            "owner": data["owner"],
            "content_rating_id": data.get("content_rating_id") or None,
        }


class ParticipationForm(forms.Form):
    """The cast panel's role form (#61): ``PATCH …/participation``'s role and fixed (both always
    sent), the signed ``shown`` (the row as the page showed it) and the per-row key (read by
    ``posted_idempotency_key``). Each row's form ids are ``cast-<cid>-<field>``."""

    role = forms.ChoiceField(
        label="Role",
        choices=role_choices(engine_default=False),
        error_messages={"required": CHOOSE_ROLE, "invalid_choice": CHOOSE_ROLE},
    )
    fixed = forms.BooleanField(
        label=FIXED_LABEL, required=False, widget=forms.CheckboxInput(attrs={"title": FIXED_TITLE})
    )
    shown = forms.CharField(required=False, widget=forms.HiddenInput)

    @property
    def role_and_fixed(self) -> tuple[Role, bool]:
        role = self.cleaned_data["role"]
        if not is_role(role):  # the choice field allows only the three
            raise ValueError(role)
        return role, bool(self.cleaned_data.get("fixed"))

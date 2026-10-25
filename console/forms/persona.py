"""Persona forms (#67): the list's GET filters, and the create and edit forms on one base.

The bounds are the Engine's (``PersonaCreate``/``PersonaPatch``), checked before any call: name
1 to 120 characters, gender blank (not set) or 1 to 40, age 0 to 150, description 1 to 4,000.
Django's stripping stays on, so a whitespace-only name or description is a required error and a
whitespace-only gender is blank. The description's CRLF line ends become LF before its length is
checked, before it is sent and before the edit diff compares it.

The create form adds ``owner`` (a :class:`~console.forms.principal.PrincipalField`, last, pre-filled
by the view with the acting principal and always sent). The edit form has no owner: a transfer
is #79's. The edit's merge patch is :func:`console.merge_patch.diff` between the persona as the
page read it and the cleaned form, so clearing gender sends ``null`` and no change sends nothing.
"""

from typing import Any

from django import forms

from console.forms.catalog import CURSOR_MAX, RELATION_NAME_PATTERN
from console.forms.principal import PrincipalField
from console.merge_patch import diff

NAME_MAX = 120
GENDER_MAX = 40
AGE_MIN = 0
AGE_MAX = 150
DESCRIPTION_MAX = 4000
DESCRIPTION_ROWS = 8
FIELDS = ("name", "gender", "age", "description")


class PersonaFilterForm(forms.Form):
    """The list's filters: an owner (``system`` allowed, never remembered) and one relation, a
    name plus an optional principal (``relation=<name>:<principal>`` or ``<name>:``)."""

    owner = PrincipalField(allow_system=True, remember=False, required=False, label="Owner")
    relation = forms.CharField(required=False, label="Relation", max_length=64)
    relation_principal = PrincipalField(
        allow_system=True, remember=False, required=False, label="with principal"
    )
    cursor = forms.CharField(required=False, max_length=CURSOR_MAX, widget=forms.HiddenInput)

    def clean_relation(self) -> str:
        value: str = (self.cleaned_data.get("relation") or "").strip()
        if value and not RELATION_NAME_PATTERN.match(value):
            raise forms.ValidationError("A relation name is 1 to 64 letters, digits, '_' or '-'.")
        return value

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        data = self.cleaned_data
        if (
            data.get("relation_principal")
            and not data.get("relation")
            and "relation" not in self.errors
        ):
            self.add_error("relation", "Enter a relation name")
        return cleaned

    @property
    def any_filter(self) -> bool:
        """Whether an owner or relation filter is set (paging is not a filter)."""
        if not self.is_bound or not self.is_valid():
            return False
        return bool(self.cleaned_data.get("owner") or self.cleaned_data.get("relation"))

    def query(self) -> dict[str, str | None]:
        """``list_personas``'s keyword arguments for a valid form."""
        data = self.cleaned_data
        relation = data.get("relation") or ""
        return {
            "owner": data.get("owner") or None,
            "relation": f"{relation}:{data.get('relation_principal') or ''}" if relation else None,
            "cursor": data.get("cursor") or None,
        }


class PersonaBaseForm(forms.Form):
    """A persona's own fields, in the page's order: Name, Gender, Age, Description."""

    name = forms.CharField(label="Name", max_length=NAME_MAX)
    gender = forms.CharField(
        label="Gender", required=False, max_length=GENDER_MAX, help_text="optional"
    )
    age = forms.IntegerField(
        label="Age",
        min_value=AGE_MIN,
        max_value=AGE_MAX,
        help_text=f"The persona's true age, {AGE_MIN}\u2013{AGE_MAX}",
        error_messages={"required": "Enter an age."},
    )
    # The length is checked in ``clean_description``, after CRLF becomes LF.
    description = forms.CharField(
        label="Description",
        widget=forms.Textarea(attrs={"rows": DESCRIPTION_ROWS, "maxlength": DESCRIPTION_MAX}),
    )

    def clean_gender(self) -> str | None:
        value: str = self.cleaned_data.get("gender") or ""
        return value or None

    def clean_description(self) -> str:
        value: str = (self.cleaned_data.get("description") or "").replace("\r\n", "\n")
        if len(value) > DESCRIPTION_MAX:
            raise forms.ValidationError(
                f"Ensure this value has at most {DESCRIPTION_MAX} characters (it has {len(value)})."
            )
        return value

    def values(self) -> dict[str, Any]:
        """The cleaned persona fields (``gender`` ``None`` when blank)."""
        return {name: self.cleaned_data[name] for name in FIELDS}


class PersonaCreateForm(PersonaBaseForm):
    """Create: the persona's fields plus its owner (always sent)."""

    owner = PrincipalField(label="Owner")

    def create_kwargs(self) -> dict[str, Any]:
        """``create_persona``'s keyword arguments (bar the idempotency key)."""
        return {**self.values(), "owner": self.cleaned_data["owner"]}


class PersonaEditForm(PersonaBaseForm):
    """Edit: the persona's fields only (the owner is shown read-only; transfer is #79)."""

    def changes(self, original: dict[str, Any]) -> dict[str, Any]:
        """The merge patch from ``original`` (the persona's fields as the page read them) to
        the form: changed fields only, a cleared gender as ``None`` (sent as ``null``)."""
        return diff({name: original.get(name) for name in FIELDS}, self.values())


def initial_of(persona: Any) -> dict[str, Any]:
    """A persona's fields as the edit form's initial values (and the diff's original)."""
    return {name: getattr(persona, name) for name in FIELDS}

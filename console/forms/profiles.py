"""Engine-profile forms.

- :class:`ProfileFilterForm` (#78): the list's GET filters: owner, one relation (#64 convention
  7, the persona list's fields) and "Show deleted" (``?deleted=1`` → ``include_deleted=true``).
- :class:`ProfileEditForm` / :class:`ProfileCreateForm` (#84): name (and, on create, owner)
  above the settings generated from the profile schema
  (``console.forms.profile_schema_form``).
- :class:`ProfileCopyForm` (#84): a copy's name and owner.

Names are trimmed; empty after trimming is "Enter a name", and 1 to 120 characters is checked
after trimming, before any Engine call.
"""

from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from console.forms.persona import PersonaFilterForm
from console.forms.principal import PrincipalField
from console.forms.profile_schema_form import SchemaSettingsForm

NAME_MAX = 120
ENTER_NAME = "Enter a name"
NAME_TOO_LONG = f"Use at most {NAME_MAX} characters"
COPY_SUFFIX = " (copy)"


class ProfileFilterForm(PersonaFilterForm):
    deleted = forms.BooleanField(required=False, label="Show deleted")

    @property
    def show_deleted(self) -> bool:
        return self.is_valid() and bool(self.cleaned_data.get("deleted"))

    def list_query(self) -> dict[str, Any]:
        """``list_engine_profiles``'s keyword arguments for a valid form."""
        base = super().query()
        return {
            "owner": base["owner"],
            "relation": (base["relation"],) if base["relation"] else (),
            "include_deleted": bool(self.cleaned_data.get("deleted")),
            "cursor": base["cursor"],
        }


def clean_profile_name(value: Any) -> str:
    """A profile name trimmed, 1 to 120 characters."""
    name = ("" if value is None else str(value)).strip()
    if not name:
        raise ValidationError(ENTER_NAME)
    if len(name) > NAME_MAX:
        raise ValidationError(NAME_TOO_LONG)
    return name


def copy_name(source: str) -> str:
    """ "<source> (copy)", the source truncated so the whole fits 120 characters."""
    return f"{source[: NAME_MAX - len(COPY_SUFFIX)].rstrip()}{COPY_SUFFIX}"


class _NameField(forms.CharField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("strip", False)
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.TextInput(attrs={"size": 50}))
        super().__init__(**kwargs)

    def clean(self, value: Any) -> str:
        return clean_profile_name(value)


class ProfileEditForm(SchemaSettingsForm):
    """An existing profile: its name above the generated settings (the owner is #79's)."""

    name = _NameField(label="Name")


class ProfileCreateForm(ProfileEditForm):
    """A new profile: name, owner (pre-filled with the acting principal, always sent) and the
    generated settings, every one starting unset."""

    owner = PrincipalField(label="Owner")


class ProfileCopyForm(forms.Form):
    """A copy's name (default "<name> (copy)") and owner (default the acting principal)."""

    name = _NameField(label="Name")
    owner = PrincipalField(label="Owner")

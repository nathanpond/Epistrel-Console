"""The engine-profile list's GET filters (#78): owner, one relation (#64 convention 7, the
persona list's fields) and "Show deleted" (``?deleted=1`` → ``include_deleted=true``)."""

from typing import Any

from django import forms

from console.forms.persona import PersonaFilterForm


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

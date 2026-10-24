"""The configuration-change list's filters (#50 the deployment's; #54 reuses it for a story's).

``setting`` is a select of the names the recorded schema's enum lists, plus a free-text
"prefixed key" (``policy:<id>`` …) checked against the schema's pattern, so a value the Engine
would refuse is a form error and no call is made. ``since`` is read in UTC.
"""

from datetime import UTC, datetime
from typing import Any

from django import forms

from console.config_changes import SettingFilter
from console.engine.models import CONFIG_CLASSES

ANY = ""


class ConfigChangeFilterForm(forms.Form):
    setting = forms.ChoiceField(required=False, label="Setting")
    prefixed = forms.CharField(
        required=False,
        label="or prefixed key",
        max_length=300,
        help_text="e.g. policy:<id>, rating:<id>, model_config:<purpose>",
    )
    change_class = forms.ChoiceField(
        required=False,
        label="Class",
        choices=[(ANY, "any"), *((c, c) for c in CONFIG_CLASSES)],
    )
    since = forms.CharField(
        required=False,
        label="Since (UTC)",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": "1"}),
    )
    after = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, data: Any = None, *, settings: SettingFilter) -> None:
        super().__init__(data)
        self.setting_filter = settings
        field = self.fields["setting"]
        if isinstance(field, forms.ChoiceField):
            field.choices = [(ANY, "any"), *((name, name) for name in settings.names)]

    def clean_prefixed(self) -> str:
        value: str = self.cleaned_data.get("prefixed", "").strip()
        if value and not self.setting_filter.accepts_prefixed(value):
            pattern = self.setting_filter.prefixed
            rule = pattern.pattern if pattern is not None else "a prefixed key"
            raise forms.ValidationError(f"Not a prefixed setting key (must match {rule}).")
        return value

    def clean_since(self) -> str:
        value: str = self.cleaned_data.get("since", "").strip()
        if not value:
            return ""
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            raise forms.ValidationError("Enter a date and time (UTC).") from None
        moment = moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        if self.cleaned_data.get("setting") and self.cleaned_data.get("prefixed"):
            self.add_error("prefixed", "Choose a setting or type a prefixed key, not both.")
        return cleaned

    def query(self) -> dict[str, str | None]:
        """The client call's filters (``setting``, ``change_class``, ``since``, ``after``)."""
        data = self.cleaned_data
        return {
            "setting": data.get("setting") or data.get("prefixed") or None,
            "change_class": data.get("change_class") or None,
            "since": data.get("since") or None,
            "after": data.get("after") or None,
        }

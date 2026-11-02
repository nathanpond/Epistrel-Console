"""A story's binding to an external-data provider (#247): the ids the provider's manifest
substitutes for its ``{binding.<key>}`` placeholders.

- :func:`clean_bound_id` is the one bound-id rule: a value is sent **exactly as typed** (another
  system's identifier, never trimmed or case-folded); a value of only whitespace is blank
  (``None``); more than 256 characters is :data:`ID_TOO_LONG`. #259's creation rows import it, so
  an id reads the same whether bound at creation or later.
- :class:`BindingForm` asks for exactly the ids the manifest substitutes: one required
  ``id__<key>`` field per key of ``console.providers.binding_keys`` (in that order), then one
  optional field per existing id whose key the manifest no longer uses (blank drops it). A
  manifest with no ``{binding.<key>}`` gives one ``external_ids`` JSON box instead (D14). No
  field carries an HTML ``maxlength`` or ``required``, so the server's errors show.
- :meth:`BindingForm.external_ids` is the whole map to send: a ``PUT`` replaces the binding.

Key and length rules beyond the JSON box's basic shape are the Engine's (``validation_error``,
placed at the key by the view).
"""

import re
from collections.abc import Mapping
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.html import format_html

from console.forms.fields import JsonBoxField

ID_PREFIX = "id__"
JSON_FIELD = "external_ids"
ID_MAX = 256
IDS_MAX = 32
ID_TOO_LONG = "Up to 256 characters"
IDS_TOO_MANY = "Up to 32 ids"
ID_NOT_STRING = "Each id is a string"
KEY_INVALID = "Ids are named with letters, digits and _ (up to 64)"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")
JSON_HELP = 'A JSON object of id names to ids, e.g. {"store": "s-9"}'


def required_text(key: str) -> str:
    return f"Enter the id this provider's manifest substitutes for `{{binding.{key}}}`"


def field_name(key: str) -> str:
    return ID_PREFIX + key


def clean_bound_id(raw: str | None) -> str | None:
    """``None`` for a missing or whitespace-only value, else ``raw`` untrimmed; over 256
    characters is a ``ValidationError(ID_TOO_LONG)``."""
    if raw is None or not raw.strip():
        return None
    if len(raw) > ID_MAX:
        raise ValidationError(ID_TOO_LONG, code="too_long")
    return raw


class BoundIdField(forms.CharField):
    """One id, cleaned by :func:`clean_bound_id`; ``needed`` makes a blank value an error."""

    def __init__(self, *, key: str, needed: bool, **kwargs: Any) -> None:
        self.key = key
        self.needed = needed
        kwargs.update(required=False, strip=False)
        super().__init__(**kwargs)

    def widget_attrs(self, widget: forms.Widget) -> dict[str, Any]:
        return {}  # no maxlength (C10): a longer id shows the server's error

    def clean(self, value: Any) -> str | None:
        cleaned = clean_bound_id(value if isinstance(value, str) else None)
        if cleaned is None and self.needed:
            raise ValidationError(required_text(self.key), code="required")
        return cleaned


class BindingForm(forms.Form):
    """The bind form for one provider: ``keys`` the manifest's binding keys, ``existing`` the
    story's current ids there (``None``: no binding)."""

    use_required_attribute = False

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        keys: list[str],
        existing: Mapping[str, str] | None,
    ) -> None:
        super().__init__(data)
        self.keys = list(keys)
        self.existing = dict(existing) if existing is not None else None
        current = self.existing or {}
        self.kept = [k for k in current if k not in self.keys]
        if self.keys:
            for key in self.keys:
                self.fields[field_name(key)] = BoundIdField(
                    key=key,
                    needed=True,
                    label=format_html("<code>{}</code>", key),
                    initial=current.get(key),
                )
            for key in self.kept:
                self.fields[field_name(key)] = BoundIdField(
                    key=key,
                    needed=False,
                    label=format_html(
                        "<code>{}</code> (not used by the manifest — blank drops it)", key
                    ),
                    initial=current.get(key),
                )
        else:
            self.fields[JSON_FIELD] = JsonBoxField(
                dict,
                empty={},
                required=False,
                label=format_html("<code>{}</code>", JSON_FIELD),
                help_text=JSON_HELP,
                initial=self.existing,
            )

    @property
    def json_mode(self) -> bool:
        return not self.keys

    def clean_external_ids(self) -> dict[str, Any]:
        ids: dict[str, Any] = self.cleaned_data.get(JSON_FIELD) or {}
        if any(not isinstance(v, str) for v in ids.values()):
            raise ValidationError(ID_NOT_STRING, code="not_string")
        if any(not KEY_PATTERN.fullmatch(k) for k in ids):
            raise ValidationError(KEY_INVALID, code="key")
        if len(ids) > IDS_MAX:
            raise ValidationError(IDS_TOO_MANY, code="too_many")
        return ids

    def external_ids(self) -> dict[str, str]:
        """The full map to send (after ``is_valid()``): the manifest's keys in order, then the
        kept unused keys, blank ones dropped; or the JSON box's object as parsed."""
        if self.json_mode:
            return dict(self.cleaned_data.get(JSON_FIELD) or {})
        ids: dict[str, str] = {}
        for key in [*self.keys, *self.kept]:
            value = self.cleaned_data.get(field_name(key))
            if isinstance(value, str):
                ids[key] = value
        return ids

    def unchanged(self) -> bool:
        """A binding exists and :meth:`external_ids` equals it (order-insensitive)."""
        return self.existing is not None and self.external_ids() == self.existing

"""The catalog lists' GET filter form (#65 characters; the scenario list, #68, reuses it).

Every filter is checked against the Engine's own rules before any call (#43): a value the Engine
would refuse is a field error and no list is read. Labels are comma-separated (#64 convention 6)
and sent as repeated ``label`` parameters (every one must match). The relation filter is a name
plus an optional principal, sent as ``relation=<name>:<principal>`` or ``<name>:`` (#64
convention 7). Neither ``owner`` nor the relation principal is added to ``KnownPrincipal`` (GET
filters never are).

The content-rating select comes from :func:`console.engine.choices.rating_choices` (retired
included). When that read failed, a ``content_rating`` already in the URL is kept as a hidden
field and still applied. The listing-status select offers ``/admin/settings``'s
``listing_status_values``; when that read failed, it is a free-text input.
"""

import re
from typing import Any

from django import forms

from console.engine.choices import RatingChoices
from console.forms.principal import PrincipalField

ANY = ""
PUBLISHED = "published"
STATUS_CHOICES = [(PUBLISHED, "published"), ("draft", "draft"), ("archived", "archived")]
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")
LABELS_MAX = 30
Q_MAX = 200
RELATION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CURSOR_MAX = 256
Q_HINT = "Searches published cards only"


def split_labels(value: str) -> list[str]:
    """Comma-separated labels: trimmed, blanks dropped, de-duplicated in order."""
    return list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


class CatalogFilterForm(forms.Form):
    """A catalog list's filters. Build it with the page's two lookups: ``ratings`` (the
    content-rating options, or the read's error) and ``listing_values`` (``None`` when
    ``/admin/settings`` could not be read)."""

    q = forms.CharField(
        required=False,
        label="Search",
        max_length=Q_MAX,
        help_text=Q_HINT,
        widget=forms.TextInput(attrs={"type": "search"}),
    )
    label = forms.CharField(
        required=False,
        label="Labels",
        help_text="Comma-separated; every label must match",
    )
    content_rating = forms.ChoiceField(required=False, label="Content rating")
    listing_status = forms.ChoiceField(required=False, label="Listing status")
    owner = PrincipalField(allow_system=True, remember=False, required=False, label="Owner")
    relation = forms.CharField(required=False, label="Relation", max_length=64)
    relation_principal = PrincipalField(
        allow_system=True, remember=False, required=False, label="with principal"
    )
    status = forms.ChoiceField(required=False, label="Status", choices=STATUS_CHOICES)
    cursor = forms.CharField(required=False, max_length=CURSOR_MAX, widget=forms.HiddenInput)

    def __init__(
        self,
        data: Any = None,
        *,
        ratings: RatingChoices,
        listing_values: list[str] | None,
    ) -> None:
        super().__init__(data)
        self.ratings = ratings
        self.listing_values = listing_values
        if ratings.ok:
            self._choices("content_rating", [(ANY, "any"), *ratings.choices])
        else:
            self.fields["content_rating"] = forms.CharField(
                required=False, widget=forms.HiddenInput
            )
        if listing_values is not None:
            self._choices("listing_status", [(ANY, "any"), *((v, v) for v in listing_values)])
        else:
            self.fields["listing_status"] = forms.CharField(
                required=False, label="Listing status", max_length=40
            )

    def _choices(self, name: str, choices: list[tuple[str, str]]) -> None:
        field = self.fields[name]
        if isinstance(field, forms.ChoiceField):
            field.choices = choices

    def clean_q(self) -> str:
        value: str = self.cleaned_data.get("q") or ""
        return value

    def clean_label(self) -> list[str]:
        labels = split_labels(self.cleaned_data.get("label") or "")
        bad = [label for label in labels if not LABEL_PATTERN.match(label)]
        if bad:
            raise forms.ValidationError(
                "Labels are 1 to 40 letters, digits, spaces, '_' or '-': "
                + ", ".join(repr(label) for label in bad)
            )
        if len(labels) > LABELS_MAX:
            raise forms.ValidationError(f"At most {LABELS_MAX} labels ({len(labels)} given).")
        return labels

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
            self.add_error("relation", "Enter a relation name for the principal.")
        return cleaned

    @property
    def any_filter(self) -> bool:
        """Whether any filter (not paging, not the default status) is set."""
        if not self.is_bound or not self.is_valid():
            return False
        data = self.cleaned_data
        keys = ("q", "label", "content_rating", "listing_status", "owner", "relation")
        return any(data.get(key) for key in keys) or data.get("status") not in (ANY, PUBLISHED)

    def query(self) -> dict[str, Any]:
        """The client's keyword arguments (``list_catalog_characters``) for a valid form; the
        default status (published) is not sent."""
        data = self.cleaned_data
        relation = data.get("relation") or ""
        status = data.get("status") or PUBLISHED
        return {
            "q": data.get("q") or None,
            "labels": data.get("label") or [],
            "content_rating": data.get("content_rating") or None,
            "listing_status": data.get("listing_status") or None,
            "owner": data.get("owner") or None,
            "relations": [f"{relation}:{data.get('relation_principal') or ''}"] if relation else [],
            "status": None if status == PUBLISHED else status,
            "cursor": data.get("cursor") or None,
        }

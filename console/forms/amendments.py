"""The Amendments page's status filter (#302, E22).

:class:`AmendmentFilterForm` is a GET form with one field, Status: "All" (blank, initial: no
``status`` is sent), "Active" (``active``) and "Undone" (``reverted``, E5's word for it). A value
outside the three is the field error :data:`STATUS_INVALID`, and the page makes no list read.
It carries no cursor, so filtering restarts at the first page.
"""

from django import forms

ALL = ""
STATUS_CHOICES = (
    (ALL, "All"),
    ("active", "Active"),
    ("reverted", "Undone"),
)
STATUS_INVALID = "Choose All, Active or Undone"


class AmendmentFilterForm(forms.Form):
    status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        required=False,
        initial=ALL,
        label="Status",
        error_messages={"invalid_choice": STATUS_INVALID},
    )

    def chosen(self) -> str | None:
        """The status to send (a valid form): ``None`` for All."""
        value: str = self.cleaned_data.get("status") or ""
        return value or None

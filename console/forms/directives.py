"""The Play page's Direct tab form (#300; FR-DIRECT-1,2,6, FR-LITE-8; E4, C7, C10).

:class:`DirectForm` is the direction's text plus an optional kind, mixed with E4's
:class:`~console.forms.rewrites.RewriteModesForm` (the modes only matter when the direction routes
to a rewrite session):

- ``text`` — a textarea, required, stripped, at most :data:`TEXT_MAX` characters *after*
  stripping (the Engine's ``strip_whitespace`` then ``max_length``), with no HTML ``maxlength``
  (C10);
- ``kind`` — a select whose first choice ("Let the Engine decide") sends nothing (C7):
  :data:`KIND_CHOICES` on a structured story, :data:`LITE_KIND_CHOICES` on a Lite one
  (``pipeline == "lite"``, from the Play page's signed snapshot). The wire values are the same on
  both: ``ephemeral`` and ``durable``. A value outside the choices is a field error.

:meth:`DirectForm.body` is ``{"text", **modes()}`` plus ``kind`` only when one was chosen.
"""

from typing import Any

from django import forms

from console.forms.rewrites import RewriteModesForm

TEXT_MAX = 4000  # the Engine's ``DirectiveRequest.text`` max_length (stripped)
TEXT_REQUIRED = "Say what should happen"
TEXT_TOO_LONG = "At most 4,000 characters"
TEXT_HELP = (
    "Direct the story as its author: guidance for the next reply, a change to the world now, or "
    "a correction to the past"
)
LITE = "lite"
EPHEMERAL, DURABLE = "ephemeral", "durable"
ENGINE_DECIDES = ("", "Let the Engine decide")
KIND_CHOICES = (
    ENGINE_DECIDES,
    (EPHEMERAL, "Steer the next reply only"),
    (DURABLE, "Lasting change"),
)
LITE_KIND_CHOICES = (
    ENGINE_DECIDES,
    (EPHEMERAL, "Nudge the next reply only"),
    (DURABLE, "Lasting author fact"),
)


def kind_choices(pipeline: str | None) -> tuple[tuple[str, str], ...]:
    """Lite's labels on a Lite story, else the structured ones (an unread pipeline included)."""
    return LITE_KIND_CHOICES if pipeline == LITE else KIND_CHOICES


class DirectForm(RewriteModesForm):
    """The Direct tab's form: ``text``, ``kind`` and E4's modes."""

    text = forms.CharField(
        label="Direction",
        help_text=TEXT_HELP,
        widget=forms.Textarea(attrs={"rows": 4}),
        error_messages={"required": TEXT_REQUIRED},
    )
    kind = forms.ChoiceField(choices=KIND_CHOICES, required=False, initial="", label="Kind")

    field_order = ("text", "kind", "resolution", "apply_directly")

    def __init__(self, data: Any = None, *, pipeline: str | None = None, **kwargs: Any) -> None:
        super().__init__(data, **kwargs)
        kind = self.fields["kind"]
        if isinstance(kind, forms.ChoiceField):
            kind.choices = kind_choices(pipeline)

    def clean_text(self) -> str:
        text: str = self.cleaned_data["text"]
        if len(text) > TEXT_MAX:
            raise forms.ValidationError(TEXT_TOO_LONG)
        return text

    def body(self) -> dict[str, Any]:
        """``POST …/directives``'s body (a valid form)."""
        body: dict[str, Any] = {"text": self.cleaned_data["text"], **self.modes()}
        if self.cleaned_data.get("kind"):
            body["kind"] = self.cleaned_data["kind"]
        return body

"""The message feedback form (#83): a rating, tags and text, checked before any Engine call.

The tag choices are built per request from the deployment's ``feedback_tags`` (raw, in the
configured order); a pre-filled tag no longer configured is added last, labelled "(no longer
configured)", and counts toward the ten. The text is CRLF→LF normalised and trimmed, then
counted in code points (Python ``len``) against the deployment's ``feedback_text_max_chars``.
"""

from collections.abc import Sequence
from typing import Any, cast

from django import forms

from console.feedback import norm_tag, norm_text

RATINGS = (1, 2, 3, 4, 5)
TAGS_MAX = 10
RATING_ERROR = "Choose a rating from 1 to 5"
TAGS_ERROR = f"Choose at most {TAGS_MAX} tags"
STALE_SUFFIX = " (no longer configured)"


def tag_choices(
    configured: Sequence[str], current: Sequence[str]
) -> tuple[list[tuple[str, str]], list[str]]:
    """The checkbox choices (configured tags, then the current ones no longer configured) and
    the current tags as choice values (a current tag matching a configured one, by the Engine's
    normalisation, ticks that configured box)."""
    by_norm = {norm_tag(t): t for t in configured}
    choices = [(t, t) for t in configured]
    initial = []
    for tag in current:
        known = by_norm.get(norm_tag(tag))
        if known is None:
            choices.append((tag, f"{tag}{STALE_SUFFIX}"))
            by_norm[norm_tag(tag)] = tag
            known = tag
        initial.append(known)
    return choices, initial


class FeedbackForm(forms.Form):
    rating = forms.TypedChoiceField(
        choices=[(n, str(n)) for n in RATINGS],
        coerce=int,
        widget=forms.RadioSelect,
        error_messages={"required": RATING_ERROR, "invalid_choice": RATING_ERROR},
    )
    tags = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    text = forms.CharField(required=False, strip=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(
        self,
        data: Any = None,
        *,
        configured: Sequence[str],
        current_tags: Sequence[str] = (),
        cap: int,
        initial: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(data, initial=initial)
        self.cap = cap
        choices, current = tag_choices(configured, current_tags)
        cast(forms.MultipleChoiceField, self.fields["tags"]).choices = choices
        if data is None:
            self.initial.setdefault("tags", current)
        self.fields["text"].widget.attrs["maxlength"] = str(cap)
        self.unknown: frozenset[str] = frozenset()  # normalised tags the Engine refused

    def clean_tags(self) -> list[str]:
        tags: list[str] = self.cleaned_data.get("tags") or []
        if len(tags) > TAGS_MAX:
            raise forms.ValidationError(TAGS_ERROR)
        return tags

    def clean_text(self) -> str | None:
        text = norm_text(self.cleaned_data.get("text"))
        if text is not None and len(text) > self.cap:
            raise forms.ValidationError(f"Text is {len(text)} characters; the limit is {self.cap}.")
        return text

    def mark_unknown(self, tags: Sequence[str]) -> None:
        """Mark the boxes of the tags the Engine refused (422 ``unknown_feedback_tag``)."""
        self.unknown = frozenset(norm_tag(t) for t in tags)

    def boxes(self) -> list[tuple[Any, bool]]:
        """Each tag checkbox with whether the Engine refused it."""
        return [(box, norm_tag(str(box.data["value"])) in self.unknown) for box in self["tags"]]

"""The history browser's controls (#52): a GET form, so every view is a shareable URL.

Each control maps to exactly one ``GET /stories/{id}/history`` query parameter
(:meth:`HistoryForm.query`); an optional control left blank sends nothing. The page size is
required (50, 200 or 1000; default 50) and always sent as ``limit``.

Input the form refuses makes no history call: a page size outside the three, a non-integer or
negative turn, and audit records asked for without the omniscient viewpoint (the Engine's
403 ``omniscient_required``, refused here first). A well-formed ``as_of`` above the head passes
through, so the Engine's 422 shows.
"""

from collections.abc import Sequence
from typing import Any

from django import forms
from django.http import QueryDict

from console.cast import OMNISCIENT, Character, character_choices, viewpoint_for

PAGE_SIZES = (50, 200, 1000)
DEFAULT_PAGE_SIZE = 50
AUDIT_NEEDS_OMNISCIENT = "Audit records need the omniscient viewpoint"
DATALIST_ALL_UP_TO = 500  # every turn is listed up to this head; above it, a sample
DATALIST_STEP = 50
DATALIST_TAIL = 50


class HistoryForm(forms.Form):
    """The controls. ``cast`` fills the viewpoint and actor selects (``GET /stories/{id}``)."""

    viewpoint = forms.ChoiceField(required=False, label="Viewpoint")
    as_of = forms.IntegerField(required=False, min_value=0, label="As of turn")
    actor_character_id = forms.ChoiceField(required=False, label="Actor")
    include_audit = forms.BooleanField(required=False, label="Include audit")
    include_summary = forms.BooleanField(required=False, label="Include summary")
    limit = forms.TypedChoiceField(
        choices=[(str(n), str(n)) for n in PAGE_SIZES],
        coerce=int,
        initial=str(DEFAULT_PAGE_SIZE),
        label="Page size",
    )
    before_turn = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)
    variant = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, data: QueryDict | dict[str, Any], *, cast: Sequence[Character]) -> None:
        bound = data.copy()
        if not bound.get("limit"):
            bound["limit"] = str(DEFAULT_PAGE_SIZE)  # the required control's default
        super().__init__(bound)
        characters = character_choices(cast)
        viewpoint = self.fields["viewpoint"]
        if isinstance(viewpoint, forms.ChoiceField):
            viewpoint.choices = [
                ("", "story default"),
                *[(viewpoint_for(cid), label) for cid, label in characters],
                (OMNISCIENT, OMNISCIENT),
            ]
        actor = self.fields["actor_character_id"]
        if isinstance(actor, forms.ChoiceField):
            actor.choices = [("", "any"), *characters]

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        if cleaned and cleaned.get("include_audit") and cleaned.get("viewpoint") != OMNISCIENT:
            self.add_error("include_audit", AUDIT_NEEDS_OMNISCIENT)
        return cleaned

    def query(self) -> dict[str, Any]:
        """``get_history``'s keyword arguments (valid form only); ``None``/``False`` values
        are left out of the request by the client."""
        data = self.cleaned_data
        return {
            "viewpoint": data["viewpoint"] or None,
            "as_of": data["as_of"],
            "limit": data["limit"],
            "before_turn": data["before_turn"],
            "actor_character_id": data["actor_character_id"] or None,
            "variant": data["variant"] or None,
            "include_audit": bool(data["include_audit"]),
            "include_summary": bool(data["include_summary"]),
        }

    def value(self, name: str) -> str:
        """The submitted (raw) value of a control, for links that keep it."""
        found = self.data.get(name, "")
        return found if isinstance(found, str) else ""

    @property
    def omniscient(self) -> bool:
        return self.value("viewpoint") == OMNISCIENT


def as_of_options(last: int) -> list[int]:
    """The as-of datalist for turns 0…``last``: every turn up to 500; above that, 0, every
    50th turn and the last 50 turns."""
    if last < 0:
        return [0]
    if last <= DATALIST_ALL_UP_TO:
        return list(range(last + 1))
    sampled = set(range(0, last + 1, DATALIST_STEP))
    sampled.update(range(last - DATALIST_TAIL + 1, last + 1))
    return sorted(sampled)

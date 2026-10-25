"""The offboarding plan form (#77): who to offboard, an action per type, and the transfer target.

Field names are ``principal``, ``actions__<type>`` (``stories`` … ``engine_profiles``),
``actions__relations`` and ``transfer_to``; "Edit plan" links back with the same names as query
parameters. Every refusal the Console can see is made here, before any Engine call: a blank or
reserved principal, a transfer target missing while some type is Transfer (or naming the
principal itself), and a plan whose every action is Keep. A transfer target given with no
Transfer action is ignored (not sent).
"""

from typing import Any

from django import forms

from console.engine.models import (
    OFFBOARDING_TYPES,
    OWNED_ACTIONS,
    RELATION_ACTIONS,
    OffboardingActions,
)
from console.forms.principal import PrincipalField

PREFIX = "actions__"
RELATIONS = "relations"
TITLES = {
    "stories": "Stories",
    "characters": "Characters",
    "scenarios": "Scenarios",
    "personas": "Personas",
    "media": "Media",
    "engine_profiles": "Engine profiles",
    RELATIONS: "Relations",
}
# The rule notes shown beside the selects (#77's wording).
COMPANIONS = "Companions follow their character"
MEDIA_MOVES = "Media used by a transferred catalog entry moves with it"
STORY_DELETE = "Deleting a story deletes it for every participant"
RETIRES = "Deleting media or a profile retires it"
DEFAULT_PROFILE = "The default profile is never deleted"
NOTES: dict[str, tuple[str, ...]] = {
    "stories": (STORY_DELETE,),
    "characters": (COMPANIONS,),
    "media": (MEDIA_MOVES, RETIRES),
    "engine_profiles": (RETIRES, DEFAULT_PROFILE),
}
ACTION_LABELS = {
    "delete": "Delete",
    "transfer": "Transfer",
    "keep": "Keep",
    "remove": "Remove",
}
TRANSFER_REQUIRED = "Required when any type is Transfer."
TRANSFER_SELF = "Can't be the principal being offboarded."
ALL_KEEP = "Choose at least one action other than Keep"
TRANSFER_HELP = "Required when any type is Transfer. Used only by Transfer."


def field_name(kind: str) -> str:
    return f"{PREFIX}{kind}"


def _select(kind: str, values: tuple[str, ...]) -> forms.ChoiceField:
    return forms.ChoiceField(
        label=TITLES[kind],
        choices=[(v, ACTION_LABELS[v]) for v in values],
        initial="keep",
        help_text="; ".join(NOTES.get(kind, ())),
    )


class OffboardingForm(forms.Form):
    principal = PrincipalField(allow_system=False, label="Principal")
    transfer_to = PrincipalField(
        allow_system=False, required=False, label="Transfer to", help_text=TRANSFER_HELP
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for kind in OFFBOARDING_TYPES:
            self.fields[field_name(kind)] = _select(kind, OWNED_ACTIONS)
        self.fields[field_name(RELATIONS)] = _select(RELATIONS, RELATION_ACTIONS)
        # Principal, the selects in type order, then the transfer target.
        self.order_fields(
            ["principal", *(field_name(k) for k in (*OFFBOARDING_TYPES, RELATIONS)), "transfer_to"]
        )

    def action_fields(self) -> list[forms.BoundField]:
        return [self[field_name(k)] for k in (*OFFBOARDING_TYPES, RELATIONS)]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if any(field_name(k) not in cleaned for k in (*OFFBOARDING_TYPES, RELATIONS)):
            return cleaned  # a select's own error already shows
        actions = self.actions()
        if actions.all_keep:
            raise forms.ValidationError(ALL_KEEP, code="all_keep")
        if actions.transfers and "transfer_to" in cleaned:
            target = cleaned["transfer_to"]
            if not target:
                self.add_error("transfer_to", forms.ValidationError(TRANSFER_REQUIRED, "required"))
            elif target == cleaned.get("principal"):
                self.add_error("transfer_to", forms.ValidationError(TRANSFER_SELF, "self"))
        return cleaned

    def actions(self) -> OffboardingActions:
        """The cleaned selects as :class:`OffboardingActions`."""
        values = {k: self.cleaned_data[field_name(k)] for k in (*OFFBOARDING_TYPES, RELATIONS)}
        return OffboardingActions(**values)

    def transfer_target(self) -> str | None:
        """The transfer target to send: only when some type is Transfer."""
        target: str = self.cleaned_data.get("transfer_to") or ""
        return target if self.actions().transfers and target else None


def form_data(body: dict[str, Any]) -> dict[str, str]:
    """The form's data (and "Edit plan"'s query) for a request body sent before."""
    actions = body.get("actions") if isinstance(body.get("actions"), dict) else {}
    data = {"principal": str(body.get("principal") or "")}
    for kind in (*OFFBOARDING_TYPES, RELATIONS):
        value = actions.get(kind) if isinstance(actions, dict) else None
        data[field_name(kind)] = value if isinstance(value, str) else "keep"
    transfer_to = body.get("transfer_to")
    data["transfer_to"] = transfer_to if isinstance(transfer_to, str) else ""
    return data

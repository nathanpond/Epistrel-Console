"""The New scheduled event form (#253; C4, C7, C10, C12, D6, D8): ``POST
/stories/{id}/scheduled-events``'s ``ScheduledEventCreate``.

:class:`ScheduledEventForm` fields, in page order:

- ``proposition`` ("Event"): required, trimmed, single-line, inner whitespace kept; at most
  :data:`NAME_MAX` code points after trimming (:data:`NAME_REQUIRED`, :data:`NAME_TOO_LONG`).
- ``planned`` ("Planned"): D6's ``PlannedField`` (an int offset with no upper bound, C10, or
  ``"HH:MM"`` sent as given), required.
- ``owner``: "Engine default (World)" (``""``, sends nothing), "World", "Author" (C7: an explicit
  World is sent). Anything else is :data:`OWNER_INVALID`. A character's own plans come from its
  turns (FR-CLOCK-19), so no character is offered.
- ``participants`` and ``shared_owners`` ("Shared with"): checkboxes of the shell's cast in cast
  order, C4 labels with the player marked; values are raw character ids. A value outside the
  cast is C4's ``UNKNOWN_CHARACTER``; duplicates are dropped before the
  :data:`MAX_CHARACTERS` count (:data:`TOO_MANY_CHARACTERS`). With ``cast=None`` (the story read
  failed) both fields are absent, and a POST still carrying values for them is refused with
  :data:`CAST_UNCHECKED` (no call).
- ``place`` ("Place id"): optional, trimmed, upper-cased, checked with ``console.ids.is_ulid``
  (:data:`PLACE_INVALID`).
- Hidden: ``idempotency_key`` (``console.confirm.KEY_FIELD``; a fresh ULID per unbound render)
  and D8's ``expected_revision`` (``initial_revision``; blank when unknown).

No HTML ``min``/``max``/``maxlength`` (C10). :meth:`ScheduledEventForm.body` is ``{proposition,
planned}`` plus each optional member only when given (convention 5: never ``null`` or ``[]``),
character lists in cast order.

:class:`RescheduleForm` (#262) is ``PATCH …/scheduled-events/{event_id}``'s
``ScheduledEventReschedule``: an optional ``planned`` (blank keeps the time) and the
``shared_owners`` checkboxes, initially the event's own (``current``); a current owner outside the
cast stays as one more option, "<short id> (not in the cast)" (C13). The rendered checkboxes are
marked by the hidden :data:`SHOWN_FIELD`, so a form rendered without them (the story read failed)
never sends a shared-owner change. :meth:`RescheduleForm.changes` gives the client's keyword
arguments: only what differs from the signed snapshot, the shared owners compared as a set (C9).
"""

from collections.abc import Sequence
from typing import Any

from django import forms

from console.cast import Character, character_choices
from console.confirm import KEY_FIELD
from console.forms.scenario import keep_order
from console.forms.world import PlannedField
from console.ids import is_ulid, new_ulid
from console.inspect import short_id
from console.wording import UNKNOWN_CHARACTER
from console.world import EXPECTED_REVISION_FIELD

NAME_MAX = 200
MAX_CHARACTERS = 20
PLAYER_SUFFIX = " (player)"

NAME_REQUIRED = "Enter the event's name"
NAME_TOO_LONG = "At most 200 characters."
TOO_MANY_CHARACTERS = "At most 20 characters"
OWNER_INVALID = "Pick World or Author"
PLACE_INVALID = "Enter a 26-character place id (digits and capital letters, no I, L, O or U)"
CAST_UNCHECKED = (
    "Your participant and shared-owner choices couldn't be checked; reload and try again"
)

PLANNED_HELP = (
    "Minutes from the story's start (e.g. 1140), or a time of day (HH:MM): its next occurrence"
)
PARTICIPANTS_HELP = (
    "They take part in the event; with stops on, one being present can stop an advance"
)
SHARED_HELP = "They may change the event as its owner may"
PLACE_HELP = "A place entity's id; copy one from Scene or Canon"

OWNER_CHOICES = [("", "Engine default (World)"), ("world", "World"), ("author", "Author")]
CHARACTER_FIELDS = ("participants", "shared_owners")


def cast_choices(cast: Sequence[Character]) -> list[tuple[str, str]]:
    """``(character id, C4 label)`` in cast order, the player marked " (player)" (D4's
    ``viewpoint_choices`` labelling, with raw ids as values)."""
    players = {c.id for c in cast if c.is_player}
    return [
        (cid, f"{label}{PLAYER_SUFFIX}" if cid in players else label)
        for cid, label in character_choices(cast)
    ]


class _CharactersField(forms.MultipleChoiceField):
    """Cast checkboxes: an unknown value is C4's ``UNKNOWN_CHARACTER``; duplicates dropped (first
    kept) before the count."""

    widget = forms.CheckboxSelectMultiple

    def __init__(self, **kwargs: Any) -> None:
        messages = {"invalid_choice": UNKNOWN_CHARACTER, **kwargs.pop("error_messages", {})}
        super().__init__(required=False, error_messages=messages, **kwargs)

    def clean(self, value: Any) -> list[str]:
        cleaned: list[str] = super().clean(value)
        unique = list(dict.fromkeys(cleaned))
        if len(unique) > MAX_CHARACTERS:
            raise forms.ValidationError(TOO_MANY_CHARACTERS, code="max_characters")
        return unique


class ScheduledEventForm(forms.Form):
    """The New scheduled event form (module docstring)."""

    proposition = forms.CharField(
        label="Event",
        strip=True,
        error_messages={"required": NAME_REQUIRED},
    )
    planned = PlannedField(label="Planned", help_text=PLANNED_HELP)
    owner = forms.ChoiceField(
        label="Owner",
        required=False,
        choices=OWNER_CHOICES,
        error_messages={"invalid_choice": OWNER_INVALID},
    )
    participants = _CharactersField(label="Participants", help_text=PARTICIPANTS_HELP)
    shared_owners = _CharactersField(label="Shared with", help_text=SHARED_HELP)
    place = forms.CharField(label="Place id", required=False, strip=True, help_text=PLACE_HELP)
    idempotency_key = forms.CharField(required=False, widget=forms.HiddenInput)
    expected_revision = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(
        self,
        data: Any = None,
        *,
        cast: Sequence[Character] | None,
        initial_revision: int | None,
        idempotency_key: str | None = None,
    ) -> None:
        initial = {
            KEY_FIELD: idempotency_key or new_ulid(),  # #43: a fresh key per render
            EXPECTED_REVISION_FIELD: "" if initial_revision is None else str(initial_revision),
        }
        super().__init__(data, initial=initial)
        self.cast = cast
        if cast is None:
            for name in CHARACTER_FIELDS:
                del self.fields[name]
        else:
            choices = cast_choices(cast)
            for name in CHARACTER_FIELDS:
                field = self.fields[name]
                if isinstance(field, forms.MultipleChoiceField):
                    field.choices = choices

    def visible(self) -> list[forms.BoundField]:
        """The visible fields, in page order."""
        return [
            self[name] for name in self.fields if name not in (KEY_FIELD, EXPECTED_REVISION_FIELD)
        ]

    def clean_proposition(self) -> str:
        name: str = self.cleaned_data["proposition"]
        if len(name) > NAME_MAX:
            raise forms.ValidationError(NAME_TOO_LONG, code="max_length")
        return name

    def clean_place(self) -> str | None:
        text: str = self.cleaned_data.get("place") or ""
        if not text:
            return None
        place = text.upper()
        if not is_ulid(place):
            raise forms.ValidationError(PLACE_INVALID, code="invalid")
        return place

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        if self.cast is None and self.data is not None:
            getlist = getattr(self.data, "getlist", None)
            for name in CHARACTER_FIELDS:
                values = getlist(name) if getlist is not None else [self.data.get(name)]
                if any(isinstance(v, str) and v.strip() for v in values):
                    raise forms.ValidationError(CAST_UNCHECKED, code="cast_unchecked")
        return cleaned

    def _in_cast_order(self, ids: Sequence[str]) -> list[str]:
        order = {c.id: i for i, c in enumerate(self.cast or ())}
        return sorted(ids, key=lambda cid: order.get(cid, len(order)))

    def body(self) -> dict[str, Any]:
        """The ``ScheduledEventCreate`` body (module docstring); call after ``is_valid()``."""
        data = self.cleaned_data
        body: dict[str, Any] = {"proposition": data["proposition"], "planned": data["planned"]}
        if data.get("owner"):
            body["owner"] = data["owner"]
        participants = data.get("participants") or []
        if participants:
            body["participants"] = self._in_cast_order(participants)
        if data.get("place"):
            body["place"] = data["place"]
        shared = data.get("shared_owners") or []
        if shared:
            body["shared_owners"] = self._in_cast_order(shared)
        return body


# --- reschedule (#262) ---------------------------------------------------------------------------

SNAPSHOT_FIELD = "snapshot"
SHOWN_FIELD = "shared_owners_shown"
RESCHEDULE_PLANNED_HELP = (
    "Blank keeps its time. A new time makes it pending again, and it may stop the clock once more"
)
NOT_IN_CAST = "{id} (not in the cast)"
SHARED_UNCHECKED = "Your shared-owner choices couldn't be checked; reload and try again"
RESCHEDULE_HIDDEN = (KEY_FIELD, EXPECTED_REVISION_FIELD, SNAPSHOT_FIELD, SHOWN_FIELD)


class RescheduleForm(forms.Form):
    """The Reschedule form (module docstring)."""

    planned = PlannedField(label="Planned", required=False, help_text=RESCHEDULE_PLANNED_HELP)
    shared_owners = _CharactersField(label="Shared with", help_text=SHARED_HELP)
    idempotency_key = forms.CharField(required=False, widget=forms.HiddenInput)
    expected_revision = forms.CharField(required=False, widget=forms.HiddenInput)
    snapshot = forms.CharField(required=False, widget=forms.HiddenInput)
    shared_owners_shown = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(
        self,
        data: Any = None,
        *,
        cast: Sequence[Character] | None,
        current: Sequence[str],
        initial_revision: int | None = None,
        snapshot: str = "",
        idempotency_key: str | None = None,
    ) -> None:
        initial: dict[str, Any] = {
            KEY_FIELD: idempotency_key or new_ulid(),  # #43: a fresh key per render
            EXPECTED_REVISION_FIELD: "" if initial_revision is None else str(initial_revision),
            SNAPSHOT_FIELD: snapshot,
            SHOWN_FIELD: "" if cast is None else "1",
            "shared_owners": list(current),
        }
        super().__init__(data, initial=initial)
        self.cast = cast
        self.current = tuple(current)
        if cast is None:
            del self.fields["shared_owners"]
            return
        choices = cast_choices(cast)
        known = {cid for cid, _ in choices}
        extras = [cid for cid in dict.fromkeys(current) if cid not in known]
        choices += [(cid, NOT_IN_CAST.replace("{id}", short_id(cid))) for cid in extras]
        field = self.fields["shared_owners"]
        if isinstance(field, forms.MultipleChoiceField):
            field.choices = choices
        self.choice_order = [cid for cid, _ in choices]

    def visible(self) -> list[forms.BoundField]:
        """The visible fields, in page order."""
        return [self[name] for name in self.fields if name not in RESCHEDULE_HIDDEN]

    def _shown(self) -> bool:
        """Whether the page this POST came from rendered the shared-owner checkboxes."""
        return self.data is not None and self.data.get(SHOWN_FIELD) == "1"

    def _posted_owners(self) -> list[str]:
        getlist = getattr(self.data, "getlist", None)
        values = getlist("shared_owners") if getlist is not None else []
        return [v for v in values if isinstance(v, str) and v.strip()]

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        if self.cast is None and self._shown() and set(self._posted_owners()) != set(self.current):
            raise forms.ValidationError(SHARED_UNCHECKED, code="cast_unchecked")
        return cleaned

    def changes(self, snapshot_owners: Sequence[str]) -> dict[str, Any]:
        """The ``reschedule_scheduled_event`` keyword arguments (call after ``is_valid()``):
        ``planned`` when given; ``shared_owners`` only when the ticked set differs from
        ``snapshot_owners``, in their order with new ones appended in choice order (``[]`` when
        every one is unticked). ``{}``: nothing to send."""
        data = self.cleaned_data
        changes: dict[str, Any] = {}
        if data.get("planned") is not None:
            changes["planned"] = data["planned"]
        if self.cast is None or not self._shown() or "shared_owners" not in data:
            return changes
        ticked: list[str] = data.get("shared_owners") or []
        if set(ticked) != set(snapshot_owners):
            order = {cid: i for i, cid in enumerate(self.choice_order)}
            in_order = sorted(ticked, key=lambda cid: order.get(cid, len(order)))
            changes["shared_owners"] = keep_order(snapshot_owners, in_order)
        return changes

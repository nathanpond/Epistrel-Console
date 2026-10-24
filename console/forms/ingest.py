"""The ingest form (#51): a batch of ``POST /stories/{id}/ingest`` events from the play view.

Each row is one ``IngestEvent``: a ``player_turn`` (an actor's say and/or do) or a
``narration`` (text, no actor), with its own ``client_event_id`` (a fresh ULID per new row,
editable). Every field stays on every row whatever the kind; the body mapping drops the ones
that do not apply. The bounds are the recorded schema's: 1-50 events, texts up to 8,000
characters, ids of 1-255 characters from ``[A-Za-z0-9._:-]``. A row with only its kind and id
is blank and dropped; a batch with no other row is refused without an Engine call.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.validators import RegexValidator
from django.forms import formset_factory
from django.forms.formsets import BaseFormSet

from console.cast import CastMember, character_choices, default_actor
from console.forms.play import INPUT_MAX, NEED_INPUT
from console.ids import new_ulid

INGEST_PREFIX = "ingest"
EVENTS_MIN, EVENTS_MAX = 1, 50
CLIENT_EVENT_ID_MAX = 255  # IngestEvent.client_event_id (the schema's bound)
CLIENT_EVENT_ID_PATTERN = r"^[A-Za-z0-9._:-]+$"
PLAYER_TURN = "player_turn"
NARRATION = "narration"
KIND_CHOICES = [(PLAYER_TURN, "Player turn"), (NARRATION, "Narration")]
NEED_TEXT = "Narration needs text."
NEED_ACTOR = "A player turn needs an actor."
NEED_ID = "Give the event a client event id."
NEED_EVENT = "Add at least one event: say or do for a player turn, or text for a narration."
DUPLICATE_ID = "This client event id is already used by an earlier row of this batch."
TEXT_FIELDS = ("say", "do", "text")


def _normalised(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class IngestEventForm(forms.Form):
    """One event row. ``cast`` fills the actor select (the Engine ids from the story read).
    Field names follow ``IngestEvent``'s, so the Engine's ``events[i].<field>`` paths land on
    them."""

    kind = forms.ChoiceField(label="Kind", choices=KIND_CHOICES)
    actor_character_id = forms.ChoiceField(label="Actor (player turn only)", required=False)
    say = forms.CharField(
        label="Say (player turn only)",
        max_length=INPUT_MAX,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    do = forms.CharField(
        label="Do (player turn only)",
        max_length=INPUT_MAX,
        required=False,
        widget=forms.Textarea(attrs={"rows": 1}),
    )
    text = forms.CharField(
        label="Text (narration only)",
        max_length=INPUT_MAX,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    client_event_id = forms.CharField(
        label="Client event id",
        max_length=CLIENT_EVENT_ID_MAX,
        required=False,  # required on a row that is sent (clean)
        validators=[
            RegexValidator(
                CLIENT_EVENT_ID_PATTERN, "Use only letters, digits, '.', '_', ':' and '-'."
            )
        ],
    )

    def __init__(self, *args: Any, cast: Sequence[CastMember] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["actor_character_id"]
        assert isinstance(field, forms.ChoiceField)  # noqa: S101 - declared above
        field.choices = character_choices(list(cast))

    @property
    def is_blank(self) -> bool:
        """Only the kind and id given (say, do and text empty): the row is not sent."""
        return not any((self[name].value() or "").strip() for name in TEXT_FIELDS)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        for name in TEXT_FIELDS:
            if isinstance(data.get(name), str):
                data[name] = _normalised(data[name])
        if self.is_blank:
            return data
        if not data.get("client_event_id") and "client_event_id" not in self.errors:
            self.add_error("client_event_id", NEED_ID)
        kind = data.get("kind")
        if kind == PLAYER_TURN:
            actor = "actor_character_id"
            if not data.get(actor) and actor not in self.errors:
                self.add_error(actor, NEED_ACTOR)
            if not (data.get("say") or data.get("do")) and not self._text_errors():
                raise forms.ValidationError(NEED_INPUT)
        elif kind == NARRATION and not data.get("text") and "text" not in self.errors:
            self.add_error("text", NEED_TEXT)
        return data

    def _text_errors(self) -> bool:
        return "say" in self.errors or "do" in self.errors

    def event(self) -> dict[str, Any]:
        """This row as an ``IngestEvent`` body: a narration omits actor, say and do; a player
        turn omits text, and a blank say or do. Call after ``is_valid()``."""
        data = self.cleaned_data
        event: dict[str, Any] = {"client_event_id": data["client_event_id"], "kind": data["kind"]}
        if data["kind"] == NARRATION:
            event["text"] = data["text"]
            return event
        event["actor_character_id"] = data["actor_character_id"]
        for name in ("say", "do"):
            if data[name]:
                event[name] = data[name]
        return event


# ``BaseFormSet`` is generic only in django-stubs, not at runtime.
if TYPE_CHECKING:
    IngestFormSetBase = BaseFormSet[IngestEventForm]
else:
    IngestFormSetBase = BaseFormSet


class BaseIngestFormSet(IngestFormSetBase):
    """The batch: 1-50 rows (``validate_max`` refuses a forced 51st), blank rows dropped,
    a client event id used twice refused on its second row."""

    def sent_forms(self) -> list[IngestEventForm]:
        """The rows that become ``events[]``, in order, so the Engine's ``events[i]`` is
        ``sent_forms()[i]``."""
        return [form for form in self.forms if not form.is_blank]

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        sent = self.sent_forms()
        if not sent:
            raise forms.ValidationError(NEED_EVENT)
        seen: set[str] = set()
        for form in sent:
            event_id = form.cleaned_data.get("client_event_id")
            if event_id in seen:
                form.add_error("client_event_id", DUPLICATE_ID)
            elif event_id:
                seen.add(event_id)

    def events(self) -> list[dict[str, Any]]:
        """The request's ``events``; call after ``is_valid()``."""
        return [form.event() for form in self.sent_forms()]


# formset_factory builds a subclass of ``formset=``; the stubs type it as the generic base.
IngestFormSet = cast(
    type[BaseIngestFormSet],
    formset_factory(
        IngestEventForm,
        formset=BaseIngestFormSet,
        extra=0,
        min_num=EVENTS_MIN,
        validate_min=True,
        max_num=EVENTS_MAX,
        validate_max=True,
    ),
)


def new_row(cast_members: Sequence[CastMember]) -> dict[str, list[str]]:
    """A new row's values: a player turn by the default actor, with a fresh client event id."""
    actor = default_actor(list(cast_members))
    return {
        "kind": [PLAYER_TURN],
        "actor_character_id": [actor or ""],
        "client_event_id": [new_ulid()],
    }


def ingest_formset(cast_members: Sequence[CastMember], data: Any = None) -> BaseIngestFormSet:
    """The ingest rows bound to ``data``, or one fresh row (:func:`new_row`) when unbound."""
    kwargs: dict[str, Any] = {"prefix": INGEST_PREFIX, "form_kwargs": {"cast": cast_members}}
    if data is not None:
        return IngestFormSet(data, **kwargs)
    initial = [{name: values[0] for name, values in new_row(cast_members).items()}]
    return IngestFormSet(initial=initial, **kwargs)

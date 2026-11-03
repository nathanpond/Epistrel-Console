"""The ingest form (#51, #260): a batch of ``POST /stories/{id}/ingest`` events from the play view.

Each row is one ``IngestEvent``: a ``player_turn`` (an actor's say and/or do), a ``narration``
(text, no actor), a ``state_change`` (#260: a State set by value or delta, with a reason) or an
``outcome`` (#260: the consumer's report of an external effect, by outbox id or effect id), with
its own ``client_event_id`` (a fresh ULID per new row, editable). Every field stays on every row
whatever the kind, grouped per kind (:attr:`IngestEventForm.FIELD_GROUPS`); the body mapping
drops the ones that do not apply. The bounds are the recorded schema's: 1-50 events, texts up to
8,000 characters, ids of 1-255 characters from ``[A-Za-z0-9._:-]``, a State name of 1-80, a
reason of at most 200, at most 20 resulting values. A row with only its kind, id, actor and
character is blank and dropped; a batch with no other row is refused without an Engine call.
Outcomes are ingested in a batch of their own (:data:`MIXED_OUTCOMES`, checked locally since the
Engine's refusal names no row).
"""

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.core.validators import RegexValidator
from django.forms import formset_factory
from django.forms.formsets import BaseFormSet

from console.cast import CastMember, character_choices, default_actor
from console.forms.fields import JsonBoxField
from console.forms.play import INPUT_MAX, NEED_INPUT
from console.forms.world import DeltaField, ScalarValueField
from console.ids import is_ulid, new_ulid

INGEST_PREFIX = "ingest"
EVENTS_MIN, EVENTS_MAX = 1, 50
CLIENT_EVENT_ID_MAX = 255  # IngestEvent.client_event_id (the schema's bound)
CLIENT_EVENT_ID_PATTERN = r"^[A-Za-z0-9._:-]+$"
PLAYER_TURN = "player_turn"
NARRATION = "narration"
STATE_CHANGE = "state_change"
OUTCOME = "outcome"
KIND_CHOICES = [
    (PLAYER_TURN, "Player turn"),
    (NARRATION, "Narration"),
    (STATE_CHANGE, "State change"),
    (OUTCOME, "Outcome"),
]
OUTCOME_CHOICES = [
    ("", "—"),
    ("confirmed", "Confirmed"),
    ("failed", "Failed"),
    ("unknown", "Unknown"),
]
STATE_MAX = 80  # IngestEvent.state
REASON_MAX = 200  # IngestEvent.reason
RESULTING_VALUES_MAX = 20  # IngestEvent.resulting_values: entries
VALUE_NAME_MAX = 80  # … keys
VALUE_TEXT_MAX = 200  # … text values
NEED_TEXT = "Narration needs text."
NEED_ACTOR = "A player turn needs an actor."
NEED_ID = "Give the event a client event id."
NEED_EVENT = "Add at least one event: say or do for a player turn, or text for a narration."
DUPLICATE_ID = "This client event id is already used by an earlier row of this batch."
NEED_STATE = "A state change names its State."
NEED_REASON = "A state change needs a reason."
BOTH_CHANGE = "Give a value or a delta, not both."
NO_CHANGE = "Give a value, or a delta for a number State."
BOTH_IDS = "Name the outbox id or the effect id, not both."
NO_ID = "Name the outbox id (an outbox effect) or the effect id (an effect the consumer applies)."
NEED_OUTCOME = "Choose the outcome: confirmed, failed or unknown."
ID_INVALID = "Enter a 26-character id (digits and capital letters, no I, L, O or U)"
TOO_MANY_VALUES = "Give at most 20 resulting values."
VALUE_NAME_INVALID = "Each resulting value's name is 1\u201380 characters."
VALUE_INVALID = "Each resulting value is a number, true/false, or text of at most 200 characters"
MIXED_OUTCOMES = (
    "Outcomes are ingested in a batch of their own — remove the other events, or ingest them "
    "separately."
)
RESULTING_VALUES_HELP = 'A JSON object, e.g. {"balance": 4500}'
NO_SCOPE = ("", "— (a story State)")
TEXT_FIELDS = ("say", "do", "text")
# What makes a row not blank: any of these filled (the kind, id, actor and character don't).
INPUT_FIELDS = (
    *TEXT_FIELDS,
    "state",
    "value",
    "delta",
    "reason",
    "effect_id",
    "outbox_id",
    "outcome",
    "resulting_values",
)


def _normalised(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


class EngineIdField(forms.CharField):
    """An Engine id the operator pastes (an outbox id or an effect id): trimmed and upper-cased,
    then the Engine's ULID pattern (:data:`ID_INVALID`); blank → ``""``."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> Any:
        text = super().to_python(value)
        if not text:
            return ""
        upper = str(text).upper()
        if not is_ulid(upper):
            raise forms.ValidationError(ID_INVALID, code="invalid")
        return upper


def _resulting_value_ok(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= VALUE_TEXT_MAX
    return False  # null, objects, lists


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
    state = forms.CharField(label="State (state change only)", max_length=STATE_MAX, required=False)
    scope_key = forms.ChoiceField(label="Character (state change only)", required=False)
    value = ScalarValueField(label="Value (state change only)")
    delta = DeltaField(label="Delta (state change only)")
    reason = forms.CharField(
        label="Reason (state change only)", max_length=REASON_MAX, required=False
    )
    effect_id = EngineIdField(label="Effect id (outcome only)")
    outbox_id = EngineIdField(label="Outbox id (outcome only)")
    outcome = forms.ChoiceField(
        label="Outcome (outcome only)", choices=OUTCOME_CHOICES, required=False
    )
    resulting_values = JsonBoxField(
        dict,
        empty=None,
        label="Resulting values (outcome only)",
        required=False,
        help_text=RESULTING_VALUES_HELP,
        widget=forms.Textarea(attrs={"rows": 2, "spellcheck": "false"}),
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
        scope = self.fields["scope_key"]
        assert isinstance(scope, forms.ChoiceField)  # noqa: S101 - declared above
        scope.choices = [NO_SCOPE, *character_choices(list(cast))]

    # The template's layout: these first, then one ``<fieldset class="kind-fields">`` per kind.
    HEAD_FIELDS = ("kind", "client_event_id")
    FIELD_GROUPS = (
        ("Player turn", ("actor_character_id", "say", "do")),
        ("Narration", ("text",)),
        ("State change", ("state", "scope_key", "value", "delta", "reason")),
        ("Outcome", ("effect_id", "outbox_id", "outcome", "resulting_values")),
    )

    def head_fields(self) -> list[forms.BoundField]:
        return [self[name] for name in self.HEAD_FIELDS]

    def field_groups(self) -> list[tuple[str, list[forms.BoundField]]]:
        """``(legend, bound fields)`` per kind, in :attr:`FIELD_GROUPS` order."""
        return [(legend, [self[name] for name in names]) for legend, names in self.FIELD_GROUPS]

    @property
    def is_blank(self) -> bool:
        """None of :data:`INPUT_FIELDS` filled (only the kind, id, actor and character given):
        the row is not sent. ``resulting_values`` counts by its raw text, so ``{}`` alone is not
        blank."""
        return not any(str(self[name].value() or "").strip() for name in INPUT_FIELDS)

    def clean_resulting_values(self) -> dict[str, Any] | None:
        """The schema's bounds on ``resulting_values``; ``{}`` counts as not given."""
        values = self.cleaned_data.get("resulting_values")
        if not values:
            return None
        if len(values) > RESULTING_VALUES_MAX:
            raise forms.ValidationError(TOO_MANY_VALUES)
        if any(not 1 <= len(name) <= VALUE_NAME_MAX for name in values):
            raise forms.ValidationError(VALUE_NAME_INVALID)
        if not all(_resulting_value_ok(value) for value in values.values()):
            raise forms.ValidationError(VALUE_INVALID)
        return dict(values)

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
        elif kind == STATE_CHANGE:
            self._clean_state_change(data)
        elif kind == OUTCOME:
            self._clean_outcome(data)
        return data

    def _missing(self, data: dict[str, Any], name: str) -> bool:
        """``name`` was not given (and has no error of its own)."""
        value = data.get(name)
        return (value is None or value == "") and name not in self.errors

    def _given(self, data: dict[str, Any], name: str) -> bool:
        value = data.get(name)
        return (value is not None and value != "") or name in self.errors

    def _clean_state_change(self, data: dict[str, Any]) -> None:
        if self._missing(data, "state"):
            self.add_error("state", NEED_STATE)
        if self._missing(data, "reason"):
            self.add_error("reason", NEED_REASON)
        value, delta = self._given(data, "value"), self._given(data, "delta")
        if value and delta:
            self.add_error(None, BOTH_CHANGE)
        elif not value and not delta:
            self.add_error(None, NO_CHANGE)

    def _clean_outcome(self, data: dict[str, Any]) -> None:
        effect, outbox = self._given(data, "effect_id"), self._given(data, "outbox_id")
        if effect and outbox:
            self.add_error(None, BOTH_IDS)
        elif not effect and not outbox:
            self.add_error(None, NO_ID)
        if self._missing(data, "outcome"):
            self.add_error("outcome", NEED_OUTCOME)

    def _text_errors(self) -> bool:
        return "say" in self.errors or "do" in self.errors

    def event(self) -> dict[str, Any]:
        """This row as an ``IngestEvent`` body, only its kind's fields: a narration its text; a
        player turn its actor and a non-blank say or do; a state change its State, reason, value
        or delta and a chosen character (``scope_key``); an outcome its outbox id or effect id,
        its outcome and non-empty resulting values. Call after ``is_valid()``."""
        data = self.cleaned_data
        event: dict[str, Any] = {"client_event_id": data["client_event_id"], "kind": data["kind"]}
        if data["kind"] == NARRATION:
            event["text"] = data["text"]
            return event
        if data["kind"] == STATE_CHANGE:
            event.update(state=data["state"], reason=data["reason"])
            if data.get("scope_key"):
                event["scope_key"] = data["scope_key"]
            if data.get("value") is not None:
                event["value"] = data["value"]
            else:
                event["delta"] = data["delta"]
            return event
        if data["kind"] == OUTCOME:
            for name in ("outbox_id", "effect_id"):
                if data.get(name):
                    event[name] = data[name]
            event["outcome"] = data["outcome"]
            if data.get("resulting_values"):
                event["resulting_values"] = data["resulting_values"]
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
    a client event id used twice refused on its second row, and outcomes alone in their batch
    (:data:`MIXED_OUTCOMES`)."""

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
        kinds = {form.cleaned_data.get("kind") for form in sent}
        if OUTCOME in kinds and len(kinds) > 1:
            raise forms.ValidationError(MIXED_OUTCOMES)
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

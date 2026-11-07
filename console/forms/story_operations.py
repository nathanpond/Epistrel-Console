"""The story Operations page's filters (#324; E22; owner answer 13a).

:class:`StoryOperationsForm` is a GET form over ``GET /stories/{id}/operations``'s filters:

- **Label**, **Kind** and **Status**: "any" (initial, sends nothing), then the recorded schema's
  query-parameter enums. The labels are #292's :data:`~console.engine.models.OperationLabel`
  (``typing.get_args``); the kinds and statuses are :data:`OPERATION_KINDS` /
  :data:`OPERATION_STATUSES` here, since E15 keeps ``OperationSummary.kind``/``status`` plain
  ``str`` and #292 defined no alias for them (a test pins all three against the recording).
  Each option is C3-humanised (a staged turn reads D13's "Staged turn", the ``inter_turn`` kind
  :data:`INTER_TURN_TEXT`) with the raw value in ``title``. A value outside the choices is
  Django's default ``invalid_choice`` error, and the page makes no list read.
- **Since (UTC)** / **Until (UTC)**: ``datetime-local`` inputs (step 1), parsed as
  ``ConfigChangeFilterForm.clean_since`` does (a naive value is UTC, an aware one converted).
  :meth:`StoryOperationsForm.query` passes each as an aware ``datetime`` (#292's client formats
  it). Until before Since is :data:`WINDOW_INVERTED` on Until; Until equal to Since is allowed
  (it matches nothing). The inputs echo the raw value typed.

The cursor (``after_id``) stays out of the form: the view reads it, checks ``is_ulid`` and drops
a bad one (E22), so a filter submit always restarts at the first page.
"""

from datetime import UTC, datetime
from typing import Any, get_args

from django import forms

from console.effects import STAGED_LABEL
from console.engine.models import OperationLabel
from console.wording import humanised

ANY = ""
ANY_TEXT = "any"
OPERATION_KINDS: tuple[str, ...] = (
    "turn",
    "inter_turn",
    "genesis",
    "consolidation",
    "rewrite_session",
    "backfill",
    "import",
    "control",
    "maintenance",
)
"""The recorded ``OperationKind`` enum (``list_operations``'s ``kind`` query parameter)."""
OPERATION_STATUSES: tuple[str, ...] = ("queued", "in_flight", "committed", "abandoned", "failed")
"""The recorded ``OperationStatus`` enum (``list_operations``'s ``status`` query parameter)."""
OPERATION_LABELS: tuple[str, ...] = get_args(OperationLabel)
"""The recorded ``OperationLabel`` enum, from #292's alias."""

STAGED_TURN = "staged_turn"
INTER_TURN = "inter_turn"
INTER_TURN_TEXT = "Inter-turn slot"
WINDOW_INVERTED = "Until is before Since"
TIME_INVALID = "Enter a date and time (UTC)."
FILTERS = ("label", "kind", "status", "since", "until")


def kind_text(value: str) -> str:
    """An operation kind as shown: ``inter_turn`` reads :data:`INTER_TURN_TEXT`, any other
    (known or not) humanised (C3)."""
    return INTER_TURN_TEXT if value == INTER_TURN else humanised(value)


def label_text(value: str) -> str:
    """An Engine label as shown: D13's "Staged turn", any other humanised (C3)."""
    return STAGED_LABEL if value == STAGED_TURN else humanised(value)


class _TitledSelect(forms.Select):
    """A select whose options carry their raw value in ``title`` (the "any" option none)."""

    def create_option(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        option: dict[str, Any] = super().create_option(*args, **kwargs)
        if option["value"]:
            option["attrs"]["title"] = option["value"]
        return option


def _choices(values: tuple[str, ...], text: Any) -> list[tuple[str, str]]:
    return [(ANY, ANY_TEXT), *((value, text(value)) for value in values)]


def _moment(value: str) -> datetime | None:
    """A typed date and time as an aware UTC ``datetime``; ``None`` for blank."""
    value = value.strip()
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise forms.ValidationError(TIME_INVALID) from None
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def _time_field(label: str) -> forms.CharField:
    return forms.CharField(
        required=False,
        label=label,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": "1"}),
    )


class StoryOperationsForm(forms.Form):
    label = forms.ChoiceField(
        choices=_choices(OPERATION_LABELS, label_text),
        required=False,
        initial=ANY,
        label="Label",
        widget=_TitledSelect,
    )
    kind = forms.ChoiceField(
        choices=_choices(OPERATION_KINDS, kind_text),
        required=False,
        initial=ANY,
        label="Kind",
        widget=_TitledSelect,
    )
    status = forms.ChoiceField(
        choices=_choices(OPERATION_STATUSES, humanised),
        required=False,
        initial=ANY,
        label="Status",
        widget=_TitledSelect,
    )
    since = _time_field("Since (UTC)")
    until = _time_field("Until (UTC)")

    def clean_since(self) -> datetime | None:
        return _moment(self.cleaned_data.get("since", ""))

    def clean_until(self) -> datetime | None:
        return _moment(self.cleaned_data.get("until", ""))

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        since = self.cleaned_data.get("since")
        until = self.cleaned_data.get("until")
        if since is not None and until is not None and until < since:
            self.add_error("until", WINDOW_INVERTED)
        return cleaned

    def query(self) -> dict[str, Any]:
        """The client call's filters (a valid form): ``label``, ``kind``, ``status`` and the
        aware ``since``/``until``, each only when set."""
        data = self.cleaned_data
        return {name: data[name] for name in FILTERS if data.get(name) not in (None, "")}

    def raw_filters(self) -> dict[str, str]:
        """The non-blank filter values as typed, for paging links and "Clear filters" (a valid
        form: they are the values sent)."""
        return {name: value for name in FILTERS if (value := str(self.data.get(name, "")).strip())}

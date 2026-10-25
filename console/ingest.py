"""The play view's ingest tab (#51): what it shows, and where an Engine refusal of a batch goes.

The tab (``templates/console/play/_ingest.html``) shows the event rows, the last batch's results
and, for a refusal that names no row, the refusal in the inspector's wording (#48). An Engine
error naming an event, by ``events[i]`` path, locator or index, is placed on that row of the rows
as sent (blank rows were dropped before sending, so ``events[i]`` is ``sent[i]``):

- ``client_event_conflict``: ``details.client_event_ids[]`` (``client_event_id`` and ``index``;
  a flat ``client_event_id`` / ``index`` is read too) → that row's client event id;
- ``validation_error``: ``events[i].<field>`` → the field (the form's names are the schema's),
  ``events[i]`` → the row; ``details.unknown_actor`` (``{"events[i]": id}``) → the actor;
- ``unsupported_event_kind``: ``details.locator`` → the kind;
- ``erased_subject_referenced`` and ``guardrail_violation`` (each violation's ``locator``) →
  the row's own errors.

Anything that names no row, or a row the batch does not have, goes to the batch's errors above
the rows.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django import forms

from console.cast import CastMember, character_labels
from console.engine.client import EngineError
from console.engine.models import IngestResponse
from console.engine_form_errors import apply_engine_errors
from console.forms.ingest import (
    EVENTS_MAX,
    NARRATION,
    BaseIngestFormSet,
    IngestEventForm,
)
from console.turn_reports import spend_text, violation_rows

if TYPE_CHECKING:
    from console.views.play import Inspector

INGEST_ARCHIVED_NOTE = "Archived — unarchive to ingest"
ERASED_EVENT = "An event refers to an erased subject — nothing was written"
GUARDRAIL_REFUSAL = "Input rejected by a guardrail — nothing was written."
ON_ROWS = "Nothing was written — see the marked rows."
CONFLICT = "client_event_conflict: {id} was already ingested with a different payload."
UNKNOWN_ACTOR = "Not a character of this story: {id}."
EVENTS = "events"
_EVENT = re.compile(r"^(?:body\.)?events\[(?P<index>\d+)\](?:\.(?P<rest>.+))?$")
KIND_LABELS = {"player_turn": "player turn", NARRATION: "narration"}


@dataclass(frozen=True)
class ResultRow:
    """One ingested event as the results table shows it."""

    client_event_id: str
    kind: str
    actor: str  # the actor's name for a player turn, else ""
    operation_id: str
    turn: int
    replayed: bool


def result_rows(
    response: IngestResponse, events: Sequence[dict[str, Any]], cast: Sequence[CastMember]
) -> tuple[ResultRow, ...]:
    """The results beside the events sent (the Engine answers in request order)."""
    labels = character_labels(cast)
    rows = []
    for index, result in enumerate(response.results):
        event = events[index] if index < len(events) else {}
        actor_id = event.get("actor_character_id")
        rows.append(
            ResultRow(
                result.client_event_id,
                KIND_LABELS.get(str(event.get("kind")), str(event.get("kind", "—"))),
                labels.get(actor_id, actor_id) if isinstance(actor_id, str) else "",
                result.operation_id,
                result.turn,
                result.replayed,
            )
        )
    return tuple(rows)


class BatchErrors(forms.Form):
    """No fields: collects the batch-level errors of an Engine refusal (``apply_engine_errors``
    needs a form for what names no row)."""

    def __init__(self) -> None:
        super().__init__({})
        self.is_valid()


@dataclass(frozen=True)
class IngestView:
    """The ingest tab as rendered: a fresh idempotency key per render (#43)."""

    story_id: str
    formset: BaseIngestFormSet
    snapshot: str
    idempotency_key: str
    viewpoint: str  # the turn form's viewpoint value, kept for the transcript re-fetch
    timeout_s: int  # the browser's wait for the POST
    disabled: bool = False
    note: str = ""
    show_errors: bool = True  # False after an add/remove re-render
    batch_errors: tuple[str, ...] = ()
    outcome: Inspector | None = None  # a refusal (or a queued batch), in #48's wording
    results: tuple[ResultRow, ...] = ()
    timeline_revision: int | None = None
    gate: str = ""

    @property
    def can_add(self) -> bool:
        return len(self.formset.forms) < EVENTS_MAX

    @property
    def can_remove(self) -> bool:
        return len(self.formset.forms) > 1


def gate_line(response: IngestResponse) -> str:
    return f"Input gate: {spend_text(len(response.model_calls), response.elapsed_ms)}"


# --- Placing a refusal on the rows -----------------------------------------------------------


def event_index(value: object) -> int | None:
    """The event an ``events[i]`` path or locator, or a bare index, names."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = _EVENT.match(value)
        if match:
            return int(match["index"])
    return None


def _details_index(details: dict[str, Any]) -> int | None:
    for key in ("index", "locator", "field", "path"):
        found = event_index(details.get(key))
        if found is not None:
            return found
    return None


def place_ingest_error(
    exc: EngineError, sent: Sequence[IngestEventForm], batch: BatchErrors
) -> int:
    """Add ``exc``'s errors to the rows ``sent`` (or ``batch``); returns how many landed on a
    row. Call after the formset validated."""
    if exc.code == "client_event_conflict":
        return _conflicts(exc, sent, batch)
    if exc.code == "erased_subject_referenced":
        index = _details_index(exc.details)
        if index is not None and index < len(sent):
            sent[index].add_error(None, ERASED_EVENT)
            return 1
        return 0
    if exc.code == "guardrail_violation":
        placed = 0
        for violation in violation_rows(_violations(exc.details)):
            for locator in violation.locators:
                index = event_index(locator)
                if index is not None and index < len(sent):
                    sent[index].add_error(None, f"Guardrail {violation.name}: {violation.message}")
                    placed += 1
        return placed
    if exc.code == "unsupported_event_kind":
        index = _details_index(exc.details)
        if index is not None and index < len(sent):
            sent[index].add_error("kind", exc.message)
            return 1
        batch.add_error(None, f"{exc.code}: {exc.message}")
        return 0
    if exc.code == "validation_error":
        return _validation(exc, sent, batch)
    return 0


def _violations(details: dict[str, Any]) -> list[dict[str, Any]]:
    found = details.get("violations")
    return [v for v in found if isinstance(v, dict)] if isinstance(found, list) else []


def _conflicts(exc: EngineError, sent: Sequence[IngestEventForm], batch: BatchErrors) -> int:
    listed = exc.details.get("client_event_ids")
    items = [i for i in listed if isinstance(i, dict)] if isinstance(listed, list) else []
    if not items and ("client_event_id" in exc.details or "index" in exc.details):
        items = [exc.details]
    placed = 0
    for item in items:
        event_id = item.get("client_event_id")
        row = _row_with_id(sent, event_id) if isinstance(event_id, str) else None
        if row is None:
            index = event_index(item.get("index"))
            row = sent[index] if index is not None and index < len(sent) else None
        shown = event_id if isinstance(event_id, str) else (row and _id_of(row)) or "—"
        message = CONFLICT.format(id=shown)
        if row is None:
            batch.add_error(None, message)
        else:
            row.add_error("client_event_id", message)
            placed += 1
    if not items:
        batch.add_error(None, f"{exc.code}: {exc.message}")
    return placed


def _id_of(row: IngestEventForm) -> str:
    value = row.cleaned_data.get("client_event_id")
    return value if isinstance(value, str) else ""


def _row_with_id(sent: Sequence[IngestEventForm], event_id: str) -> IngestEventForm | None:
    return next((row for row in sent if _id_of(row) == event_id), None)


def _validation(exc: EngineError, sent: Sequence[IngestEventForm], batch: BatchErrors) -> int:
    unknown = exc.details.get("unknown_actor")
    if isinstance(unknown, dict) and unknown:
        placed = 0
        for locator, actor in unknown.items():
            index = event_index(locator)
            message = UNKNOWN_ACTOR.format(id=actor)
            if index is not None and index < len(sent):
                sent[index].add_error("actor_character_id", message)
                placed += 1
            else:
                batch.add_error(None, f"{locator}: {message}")
        return placed
    before = _row_error_count(sent)
    apply_engine_errors(exc, batch, formsets={EVENTS: list(sent)})
    return _row_error_count(sent) - before


def _row_error_count(sent: Sequence[IngestEventForm]) -> int:
    return sum(len(errors) for row in sent for errors in row.errors.values())

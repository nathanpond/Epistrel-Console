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

#260 adds the ``state_change`` and ``outcome`` kinds' refusals:

- ``effect_not_found``: ``details.locator`` → that row's ``outbox_id`` or ``effect_id`` (the one
  ``details`` names), :data:`EFFECT_NOT_FOUND`;
- the State refusals raised at the batch's commit, with ``details.state`` and no locator
  (``state_unpermitted``, ``state_readonly_external``, ``state_derived``,
  ``state_constraint_violation``, and ``not_found`` with ``details.state``) → the ``state``
  field of the one sent ``state_change`` row naming that State, in D9's wording
  (``world_refusal(exc, story_id, "ingest events", effective)``); none or several such rows →
  above the rows. ``not_found`` without ``details.state`` places nothing (the inspector's panel);
- a commit-time ``validation_error`` naming a State member in ``details.field`` (``state``,
  ``scope_key``, ``value``, ``delta``, ``reason``) and no row → that field of the batch's one
  ``state_change`` row, else above the rows;
- ``module_off`` naming ``states`` → above the rows, D1's reason and the Config link
  (:attr:`BatchErrors.link`);
- ``validation_error`` with ``reason: mixed_outcomes`` → :data:`MIXED_OUTCOMES` above the rows.

:func:`states_gate` is D1's ``states`` gate on a ``state_change`` row, read from the play
page's signed snapshot (#245's ``PlayStory.states``); outcomes are never gated.

Anything that names no row, or a row the batch does not have, goes to the batch's errors above
the rows.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from django import forms
from django.utils.safestring import SafeString

from console.cast import CastMember, character_labels
from console.effects import outbox_list_notice_html, outbox_url
from console.engine.client import EngineError
from console.engine.models import IngestResponse
from console.engine_form_errors import apply_engine_errors
from console.forms.ingest import (
    EVENTS_MAX,
    MIXED_OUTCOMES,
    NARRATION,
    OUTCOME,
    STATE_CHANGE,
    BaseIngestFormSet,
    IngestEventForm,
)
from console.inspect import PIPELINE, SETTINGS, STATES, Unavailable, module_gate
from console.turn_reports import spend_text, violation_rows
from console.wording import character_label, humanised
from console.world import signed_delta_text, world_refusal

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
KIND_LABELS = {
    "player_turn": "player turn",
    NARRATION: "narration",
    STATE_CHANGE: "state change",
    OUTCOME: "outcome",
}
EFFECT_NOT_FOUND = "This story has no external effect {id}."
INGEST_VERB = "ingest events"  # D9's verb phrase for an ingest batch's refusals
CONFIG_LINK = "Config"
STATE_CODES = frozenset(
    {
        "state_unpermitted",
        "state_readonly_external",
        "state_derived",
        "state_constraint_violation",
    }
)
STATE_MEMBERS = frozenset({"state", "scope_key", "value", "delta", "reason"})


class WorldSource(Protocol):
    """What :func:`states_gate` reads from the play page's snapshot (``PlayStory``)."""

    @property
    def pipeline(self) -> str | None: ...

    @property
    def states(self) -> str | None: ...


def world_effective(source: WorldSource) -> dict[str, Any]:
    """The minimal effective mapping (``pipeline``, ``states.level``) the snapshot stands for,
    each key left out when unknown (as ``console.multi_turn.effective_of`` does for #200)."""
    effective: dict[str, Any] = {}
    if source.pipeline is not None:
        effective[SETTINGS[PIPELINE]] = source.pipeline
    if source.states is not None:
        effective["states"] = {"level": source.states}
    return effective


def states_gate(source: WorldSource) -> Unavailable | None:
    """D1's ``states`` gate (at ``prompt_only``) on a ``state_change`` row; ``None``: open.
    An unread level is ``GATE_UNREAD`` (C6)."""
    return module_gate(STATES, world_effective(source))


@dataclass(frozen=True)
class ResultRow:
    """One ingested event as the results table shows it. ``what``: a state change's
    "<state> <delta or value>[ · <character>]", or an outcome's id (``outcome`` then holds
    its humanised outcome, ``what_url`` the Reconciliation link of an outbox id); "" for the
    turn kinds. ``turn_shown``: the Engine's turn, ``None`` ("—") for an outcome (turn 0: no
    narrative position)."""

    client_event_id: str
    kind: str
    actor: str  # the actor's name for a player turn, else ""
    operation_id: str
    turn: int
    replayed: bool
    turn_shown: int | None = None
    what: str = ""
    what_url: str | None = None
    outcome: str = ""
    from_outbox: bool = False


def _value_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _state_change_text(event: dict[str, Any], names: dict[str, str]) -> str:
    delta = event.get("delta")
    change = signed_delta_text(delta) if delta is not None else _value_text(event.get("value"))
    text = f"{event.get('state', '')} {change}"
    scope = event.get("scope_key")
    if isinstance(scope, str) and scope:
        text += f" · {character_label(scope, names)}"
    return text


def result_rows(
    response: IngestResponse,
    events: Sequence[dict[str, Any]],
    cast: Sequence[CastMember],
    story_id: str,
) -> tuple[ResultRow, ...]:
    """The results beside the events sent (the Engine answers in request order)."""
    labels = character_labels(cast)
    rows = []
    for index, result in enumerate(response.results):
        event = events[index] if index < len(events) else {}
        kind = event.get("kind")
        actor_id = event.get("actor_character_id")
        extra: dict[str, Any] = {"turn_shown": result.turn}
        if kind == STATE_CHANGE:
            extra["what"] = _state_change_text(event, labels)
        elif kind == OUTCOME:
            outbox = event.get("outbox_id")
            shown = outbox if isinstance(outbox, str) and outbox else event.get("effect_id")
            extra.update(
                turn_shown=None,
                what=str(shown or ""),
                what_url=outbox_url(story_id, outbox),
                outcome=humanised(str(event.get("outcome", ""))),
                from_outbox=isinstance(outbox, str) and bool(outbox),
            )
        rows.append(
            ResultRow(
                result.client_event_id,
                KIND_LABELS.get(str(kind), str(event.get("kind", "—"))),
                labels.get(actor_id, actor_id) if isinstance(actor_id, str) else "",
                result.operation_id,
                result.turn,
                result.replayed,
                **extra,
            )
        )
    return tuple(rows)


class BatchErrors(forms.Form):
    """No fields: collects the batch-level errors of an Engine refusal (``apply_engine_errors``
    needs a form for what names no row)."""

    def __init__(self) -> None:
        super().__init__({})
        self.is_valid()
        self.link: tuple[str, str] | None = None  # (text, url) shown beside the batch errors


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
    transcript_player_view: str = ""  # #196: the transcript's requested player view
    states_gate: Unavailable | None = None  # #260: why a state_change row can't be sent
    batch_link: tuple[str, str] | None = None  # (text, url): a module_off refusal's Config link

    @property
    def outbox_notice(self) -> SafeString | None:
        """D11's notice, under results naming an outbox id."""
        if any(row.from_outbox for row in self.results):
            return outbox_list_notice_html(story_id=self.story_id)
        return None

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
    exc: EngineError,
    sent: Sequence[IngestEventForm],
    batch: BatchErrors,
    *,
    story_id: str = "",
    effective: dict[str, Any] | None = None,
) -> int:
    """Add ``exc``'s errors to the rows ``sent`` (or ``batch``); returns how many landed on a
    row. Call after the formset validated. ``story_id`` and ``effective`` (the snapshot's
    :func:`world_effective`) word the State refusals (D9)."""
    if exc.code == "effect_not_found":
        return _effect_not_found(exc, sent, batch)
    if exc.code in STATE_CODES or (exc.code == "not_found" and _details_state(exc) is not None):
        return _state_refusal(exc, sent, batch, story_id, effective)
    if exc.code == "module_off":
        if exc.details.get("module") == STATES:
            refusal = world_refusal(exc, story_id, INGEST_VERB, effective)
            if refusal is not None:
                batch.add_error(None, refusal.text)
                if refusal.url:
                    batch.link = (CONFIG_LINK, refusal.url)
        return 0
    if exc.code == "validation_error" and exc.details.get("reason") == "mixed_outcomes":
        batch.add_error(None, MIXED_OUTCOMES)
        return 0
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
        return _validation(exc, sent, batch, story_id, effective)
    return 0


def _effect_not_found(exc: EngineError, sent: Sequence[IngestEventForm], batch: BatchErrors) -> int:
    field = next((name for name in ("outbox_id", "effect_id") if name in exc.details), None)
    wanted = exc.details.get(field) if field is not None else None
    message = EFFECT_NOT_FOUND.format(id=wanted if isinstance(wanted, str) and wanted else "—")
    index = event_index(exc.details.get("locator"))
    if index is None or index >= len(sent):
        batch.add_error(None, message)
        return 0
    sent[index].add_error(field, message)
    return 1


def _details_state(exc: EngineError) -> str | None:
    state = exc.details.get("state")
    return state if isinstance(state, str) and state else None


def _state_rows(sent: Sequence[IngestEventForm]) -> list[IngestEventForm]:
    return [row for row in sent if row.cleaned_data.get("kind") == STATE_CHANGE]


def _state_refusal(
    exc: EngineError,
    sent: Sequence[IngestEventForm],
    batch: BatchErrors,
    story_id: str,
    effective: dict[str, Any] | None,
) -> int:
    """A commit-time State refusal: on the one ``state_change`` row naming ``details.state``
    (exact match on the stripped name, whatever its character), else above the rows."""
    refusal = world_refusal(exc, story_id, INGEST_VERB, effective)
    if refusal is None:
        return 0
    state = _details_state(exc)
    matching = [row for row in _state_rows(sent) if row.cleaned_data.get("state") == state]
    if state is not None and len(matching) == 1:
        matching[0].add_error("state", refusal.text)
        return 1
    batch.add_error(None, refusal.text)
    return 0


def _state_member(exc: EngineError) -> str | None:
    """The State member a commit-time ``validation_error`` names (no row, no State)."""
    field = exc.details.get("field")
    if not isinstance(field, str) or "state" in exc.details:
        return None
    member = field.removeprefix("body.")
    return member if member in STATE_MEMBERS else None


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


def _validation(
    exc: EngineError,
    sent: Sequence[IngestEventForm],
    batch: BatchErrors,
    story_id: str = "",
    effective: dict[str, Any] | None = None,
) -> int:
    member = _state_member(exc)
    rows = _state_rows(sent)
    if member is not None and len(rows) == 1:
        refusal = world_refusal(exc, story_id, INGEST_VERB, effective)
        rows[0].add_error(member, refusal.text if refusal is not None else exc.message)
        return 1
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

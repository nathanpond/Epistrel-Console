"""The M6 client operations (#232): States, State proposals, the world clock, scheduled events,
the provider registry and bindings, external values, reconciliation, the correction and the
catch-up admin reads, against the schema-validating fake Engine.

Fixtures: ``tests/fixtures/engine/m6/`` (D16: one cast, ``story.json``: the player Mara, and
Alice and Bob, every other file reusing their ids)."""

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
from django.http import HttpRequest
from django.test import RequestFactory, override_settings

from console.engine.client import (
    EXPECTED_REVISION_HEADER,
    MUTATION_TIMEOUT_S,
    EngineClient,
    EngineError,
    turn_timeout,
)
from console.engine.models import (
    AnalysisQueued,
    ClockAdvanced,
    ClockSettingsWritten,
    Compensated,
    ExternalFacts,
    ExternalRead,
    FactBody,
    ListedMemory,
    MemoryList,
    Model,
    NarrativePosition,
    Notice,
    ProposalApproved,
    ProposalDismissed,
    ProviderBindings,
    ProviderBindingWritten,
    ProviderDetail,
    ProviderList,
    ProviderMutation,
    Reconciliation,
    ReconciliationListing,
    RegenerateResult,
    RequiredEffect,
    ScheduledEvent,
    ScheduledEvents,
    ScheduledEventWritten,
    SettledNow,
    StateDeclared,
    StateDefinitionWritten,
    StateProposals,
    StateView,
    StateWritten,
    StoryState,
    StoryStates,
    WorldClock,
)
from console.engine_access import engine_client_for
from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import ENGINE, engine_fixture
from tests.play.helpers import error_json

URL = "http://engine.test"
SERVICE_KEY = "service-key-for-the-m6-client-test-0000"
PRINCIPAL = "console:test"
STORY = "01JM6ST0000000000000000001"
MARA, ALICE, BOB = (f"01JM6CH{'0' * 17}{n:02d}" for n in (1, 2, 3))
PROPOSAL = "01JM6PP0000000000000000001"
EVENT = "01JM6EV0000000000000000001"
OUTBOX = "01JM6BX0000000000000000002"
FACT = "01JM6FA0000000000000000002"
CURSOR = "01JM6MM0000000000000000002"
KEY = "01JM6KY0000000000000000099"
PROFILE = "01JM6PF0000000000000000001"

STATES = "/stories/{story_id}/states"
STATE = "/stories/{story_id}/states/{name}"
DEFINITION = "/stories/{story_id}/states/{name}/definition"
PROPOSALS = "/stories/{story_id}/state-proposals"
ANALYZE = "/stories/{story_id}/state-proposals/analyze"
APPROVE = "/stories/{story_id}/state-proposals/{proposal_id}/approve"
DISMISS = "/stories/{story_id}/state-proposals/{proposal_id}/dismiss"
CLOCK = "/stories/{story_id}/clock"
ADVANCE = "/stories/{story_id}/clock/advance"
EVENTS = "/stories/{story_id}/scheduled-events"
EVENT_ROUTE = "/stories/{story_id}/scheduled-events/{event_id}"
PROVIDERS = "/admin/external-providers"
PROVIDER = "/admin/external-providers/{name}"
BINDINGS = "/stories/{story_id}/providers"
BINDING = "/stories/{story_id}/providers/{name}/binding"
EXTERNAL = "/stories/{story_id}/inspect/external-facts"
RECONCILIATION = "/stories/{story_id}/reconciliation"
SETTLE = "/stories/{story_id}/reconciliation/{outbox_id}/settle-now"
COMPENSATE = "/stories/{story_id}/reconciliation/{outbox_id}/compensate"
CORRECT = "/stories/{story_id}/correct"
MEMORIES = "/admin/stories/{story_id}/memories"
FACT_ROUTE = "/admin/stories/{story_id}/facts/{fact_id}"

SET_BODY = {"delta": 5, "reason": "Mara sells the rope"}
DECLARATION = {"name": "rope", "type": "number", "initial_value": 3, "unit": "coils"}
EVENT_BODY = {"proposition": "The harbourmaster's meeting", "planned": 240, "owner": "author"}
EFFECT = {"state": "bank_balance", "delta": 50, "reason": "Refund the harbour fee"}
CORRECTION = {"turn": 4, "player_input": "Where did Bob hide the ledger?"}
REGISTRATION = {"name": "bank", "trust_tier": "authoritative", "transport": "http"}


def m6(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m6/{name}"))


def client(fake: FakeEngine) -> EngineClient:
    return EngineClient(URL, SERVICE_KEY, transport=fake.transport, principal=PRINCIPAL)


def query(request: httpx.Request) -> list[tuple[str, str]]:
    return parse_qsl(request.url.query.decode(), keep_blank_values=True)


def body(request: httpx.Request) -> Any:
    return json.loads(request.content) if request.content else None


def read_timeout(request: httpx.Request) -> float:
    timeout: dict[str, float] = request.extensions["timeout"]
    return timeout["read"]


@dataclass(frozen=True)
class Op:
    """One M6 operation: its client method and how a test calls it, the route it answers on,
    the fixture and status served, and the model it parses (``None``: no body)."""

    name: str
    method: str
    route: str
    fixture: str | None
    status: int
    model: type[Model] | None
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    path: str = ""  # the request path the call sends


def _slot(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {"expected_revision": None, "idempotency_key": KEY, **kwargs}


S = f"/stories/{STORY}"
OPS = [
    Op("get_states", "GET", STATES, "states", 200, StoryStates, (STORY,), path=f"{S}/states"),
    Op(
        "get_state",
        "GET",
        STATE,
        "state_view",
        200,
        StateView,
        (STORY, "gold"),
        path=f"{S}/states/gold",
    ),
    Op(
        "set_state",
        "PUT",
        STATE,
        "state_written",
        200,
        StateWritten,
        (STORY, "gold", SET_BODY),
        _slot({}),
        f"{S}/states/gold",
    ),
    Op(
        "declare_state",
        "POST",
        STATES,
        "state_declared",
        201,
        StateDeclared,
        (STORY, DECLARATION),
        _slot({}),
        f"{S}/states",
    ),
    Op(
        "update_state_definition",
        "PATCH",
        DEFINITION,
        "state_definition_written",
        200,
        StateDefinitionWritten,
        (STORY, "gold", {"constraints": {"max": 500}}),
        {"idempotency_key": KEY},
        f"{S}/states/gold/definition",
    ),
    Op(
        "list_state_proposals",
        "GET",
        PROPOSALS,
        "state_proposals",
        200,
        StateProposals,
        (STORY,),
        path=f"{S}/state-proposals",
    ),
    Op(
        "analyze_state_proposals",
        "POST",
        ANALYZE,
        "analysis_queued",
        202,
        AnalysisQueued,
        (STORY,),
        {"idempotency_key": KEY},
        f"{S}/state-proposals/analyze",
    ),
    Op(
        "approve_state_proposal",
        "POST",
        APPROVE,
        "proposal_approved",
        201,
        ProposalApproved,
        (STORY, PROPOSAL),
        _slot({}),
        f"{S}/state-proposals/{PROPOSAL}/approve",
    ),
    Op(
        "dismiss_state_proposal",
        "POST",
        DISMISS,
        "proposal_dismissed",
        200,
        ProposalDismissed,
        (STORY, PROPOSAL),
        {"idempotency_key": KEY},
        f"{S}/state-proposals/{PROPOSAL}/dismiss",
    ),
    Op("get_clock", "GET", CLOCK, "clock", 200, WorldClock, (STORY,), path=f"{S}/clock"),
    Op(
        "update_clock",
        "PATCH",
        CLOCK,
        "clock_settings_written",
        200,
        ClockSettingsWritten,
        (STORY, {"mode": "manual"}),
        {"idempotency_key": KEY},
        f"{S}/clock",
    ),
    Op(
        "advance_clock",
        "POST",
        ADVANCE,
        "clock_advanced",
        200,
        ClockAdvanced,
        (STORY,),
        _slot({"reason": "The tide turns", "minutes": 60}),
        f"{S}/clock/advance",
    ),
    Op(
        "list_scheduled_events",
        "GET",
        EVENTS,
        "scheduled_events",
        200,
        ScheduledEvents,
        (STORY,),
        path=f"{S}/scheduled-events",
    ),
    Op(
        "create_scheduled_event",
        "POST",
        EVENTS,
        "scheduled_event_written",
        201,
        ScheduledEventWritten,
        (STORY, EVENT_BODY),
        _slot({}),
        f"{S}/scheduled-events",
    ),
    Op(
        "reschedule_scheduled_event",
        "PATCH",
        EVENT_ROUTE,
        "scheduled_event_written",
        200,
        ScheduledEventWritten,
        (STORY, EVENT),
        _slot({"planned": 300}),
        f"{S}/scheduled-events/{EVENT}",
    ),
    Op(
        "cancel_scheduled_event",
        "DELETE",
        EVENT_ROUTE,
        "scheduled_event_written",
        200,
        ScheduledEventWritten,
        (STORY, EVENT),
        _slot({}),
        f"{S}/scheduled-events/{EVENT}",
    ),
    Op("list_providers", "GET", PROVIDERS, "providers", 200, ProviderList, path=PROVIDERS),
    Op(
        "get_provider",
        "GET",
        PROVIDER,
        "provider",
        200,
        ProviderDetail,
        ("bank",),
        path=f"{PROVIDERS}/bank",
    ),
    Op(
        "register_provider",
        "POST",
        PROVIDERS,
        "provider_mutation",
        201,
        ProviderMutation,
        (REGISTRATION,),
        {"idempotency_key": KEY},
        PROVIDERS,
    ),
    Op(
        "replace_provider",
        "PUT",
        PROVIDER,
        "provider_mutation",
        200,
        ProviderMutation,
        ("bank", {"trust_tier": "advisory", "transport": "http"}),
        {"idempotency_key": KEY},
        f"{PROVIDERS}/bank",
    ),
    Op(
        "delete_provider",
        "DELETE",
        PROVIDER,
        None,
        204,
        None,
        ("bank",),
        {"idempotency_key": KEY},
        f"{PROVIDERS}/bank",
    ),
    Op(
        "list_provider_bindings",
        "GET",
        BINDINGS,
        "bindings",
        200,
        ProviderBindings,
        (STORY,),
        path=f"{S}/providers",
    ),
    Op(
        "bind_provider",
        "PUT",
        BINDING,
        "binding_written",
        200,
        ProviderBindingWritten,
        (STORY, "bank", {"account_id": "acct-7731"}),
        {"idempotency_key": KEY},
        f"{S}/providers/bank/binding",
    ),
    Op(
        "inspect_external_facts",
        "GET",
        EXTERNAL,
        "external_facts",
        200,
        ExternalFacts,
        (STORY,),
        path=f"{S}/inspect/external-facts",
    ),
    Op(
        "get_reconciliation",
        "GET",
        RECONCILIATION,
        "reconciliation",
        200,
        Reconciliation,
        (STORY,),
        path=f"{S}/reconciliation",
    ),
    Op(
        "settle_now",
        "POST",
        SETTLE,
        "settled_now",
        200,
        SettledNow,
        (STORY, OUTBOX),
        _slot({}),
        f"{S}/reconciliation/{OUTBOX}/settle-now",
    ),
    Op(
        "compensate",
        "POST",
        COMPENSATE,
        "compensated",
        201,
        Compensated,
        (STORY, OUTBOX, EFFECT),
        _slot({}),
        f"{S}/reconciliation/{OUTBOX}/compensate",
    ),
    Op(
        "correct",
        "POST",
        CORRECT,
        "corrected",
        200,
        RegenerateResult,
        (STORY, CORRECTION),
        _slot({}),
        f"{S}/correct",
    ),
    Op(
        "list_story_memories",
        "GET",
        MEMORIES,
        "memories_page1",
        200,
        MemoryList,
        (STORY,),
        path=f"/admin{S}/memories",
    ),
    Op(
        "get_fact",
        "GET",
        FACT_ROUTE,
        "fact",
        200,
        FactBody,
        (STORY, FACT),
        path=f"/admin{S}/facts/{FACT}",
    ),
]
BY_NAME = {op.name: op for op in OPS}
SLOT_WRITES = (
    "set_state",
    "declare_state",
    "approve_state_proposal",
    "advance_clock",
    "create_scheduled_event",
    "reschedule_scheduled_event",
    "cancel_scheduled_event",
    "settle_now",
    "compensate",
)
CONFIGURATION_WRITES = (
    "update_state_definition",
    "update_clock",
    "analyze_state_proposals",
    "dismiss_state_proposal",
    "bind_provider",
    "register_provider",
    "replace_provider",
    "delete_provider",
)


def serve(fake: FakeEngine, op: Op) -> None:
    fake.respond(op.method, op.route, op.status, json=m6(op.fixture) if op.fixture else None)


def call(engine: EngineClient, op: Op, **overrides: Any) -> Any:
    method: Callable[..., Any] = getattr(engine, op.name)
    return method(*op.args, **{**op.kwargs, **overrides})


# --- every operation round-trips (D16) --------------------------------------------------------


def test_thirty_operations_are_tabled() -> None:
    assert len(OPS) == 30
    assert len(BY_NAME) == 30
    assert len(SLOT_WRITES) == 9


@pytest.mark.parametrize("op", OPS, ids=lambda op: op.name)
def test_each_operation_round_trips(fake_engine: FakeEngine, op: Op) -> None:
    serve(fake_engine, op)
    answer = call(client(fake_engine), op)
    if op.model is None:
        assert answer is None
    else:
        assert op.fixture is not None
        assert isinstance(answer, op.model)
        assert answer == op.model.from_json(m6(op.fixture))
    [request] = fake_engine.requests
    assert request.method == op.method
    assert request.url.path == op.path
    if op.method != "GET":
        assert request.headers["Idempotency-Key"] == KEY


# --- the models: D15 --------------------------------------------------------------------------

LENIENT: list[tuple[str, type[StoryStates | ScheduledEvents | Reconciliation], str, Any]] = [
    ("states", StoryStates, "states", StoryState),
    ("scheduled_events", ScheduledEvents, "events", ScheduledEvent),
    ("reconciliation", Reconciliation, "listings", ReconciliationListing),
]


@pytest.mark.parametrize(("name", "model", "key", "item"), LENIENT)
def test_lenient_pages_keep_a_malformed_entry_raw(
    name: str, model: type[StoryStates | ScheduledEvents | Reconciliation], key: str, item: Any
) -> None:
    raw = m6(f"{name}_bad_item")
    page = model.from_json(raw)
    assert page is not None
    typed = getattr(page, key)
    assert typed == [item.from_json(raw[key][0])]  # the good entry, typed
    assert page.unparsed == {key: raw[key]}  # the raw list, both entries
    good = model.from_json(m6(name))  # **Complement:** the good fixture
    assert good is not None
    assert good.unparsed == {}
    assert len(getattr(good, key)) == len(m6(name)[key])


@pytest.mark.parametrize(
    "name",
    [
        "states_alice",
        "scheduled_events_alice",
        "state_proposals",
        "external_facts",
        "providers",
        "bindings",
        "memories_page1",
    ],
)
def test_the_other_lenient_pages_parse_whole(name: str) -> None:
    model: type[Model] = {
        "states_alice": StoryStates,
        "scheduled_events_alice": ScheduledEvents,
        "state_proposals": StateProposals,
        "external_facts": ExternalFacts,
        "providers": ProviderList,
        "bindings": ProviderBindings,
        "memories_page1": MemoryList,
    }[name]
    page = model.from_json(m6(name))
    assert page is not None
    assert page.unparsed == {}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("name", "model", "key"),
    [
        ("state_proposals", StateProposals, "proposals"),
        ("external_facts", ExternalFacts, "items"),
        ("providers", ProviderList, "providers"),
        ("bindings", ProviderBindings, "bindings"),
        ("memories_page1", MemoryList, "memories"),
    ],
)
def test_one_bad_entry_never_fails_the_page(name: str, model: type[Model], key: str) -> None:
    """D15: each read page drops a malformed entry into ``unparsed``; a bad item in a nested
    list (an evidence beat, a citing fact) drops its whole entry."""
    raw = m6(name)
    raw[key].append({"nonsense": True})
    page = model.from_json(raw)
    assert page is not None
    assert len(getattr(page, key)) == len(raw[key]) - 1
    assert page.unparsed == {key: raw[key]}  # type: ignore[attr-defined]


def test_a_bad_inner_item_drops_its_entry() -> None:
    raw = m6("state_proposals")
    del raw["proposals"][0]["evidence"][1]["turn"]
    page = StateProposals.from_json(raw)
    assert page is not None
    assert [p.status for p in page.proposals] == ["dismissed"]
    raw = m6("external_facts")
    raw["items"][0]["facts"][0]["fact_id"] = 7
    facts = ExternalFacts.from_json(raw)
    assert facts is not None
    assert [i.source for i in facts.items] == ["last_known"]


def test_a_malformed_clock_fails_the_states_read(fake_engine: FakeEngine) -> None:
    """Only ``states`` is lenient: a ``clock`` that doesn't parse is ``bad_response``."""
    raw = m6("states")
    del raw["clock"]["settings"]["mode"]
    assert StoryStates.from_json(raw) is None
    fake_engine.side_effect("GET", STATES, lambda _: httpx.Response(200, json=raw))
    with pytest.raises(EngineError) as caught:
        client(fake_engine).get_states(STORY)
    assert caught.value.code == "bad_response"


@pytest.mark.parametrize("name", [*SLOT_WRITES, "update_clock", "bind_provider"])
def test_a_write_answer_that_does_not_parse_is_a_bad_response(
    fake_engine: FakeEngine, name: str
) -> None:
    """Write answers are strict (D15): one missing ``timeline_revision`` is #43's
    ``bad_response``."""
    op = BY_NAME[name]
    assert op.fixture is not None
    raw = m6(op.fixture)
    del raw["timeline_revision"]
    status = op.status
    fake_engine.side_effect(op.method, op.route, lambda _: httpx.Response(status, json=raw))
    with pytest.raises(EngineError) as caught:
        call(client(fake_engine), op)
    assert caught.value.code == "bad_response"


def test_analyze_refuses_a_non_202() -> None:
    """As ``consolidate_story`` does: any 2xx but 202 is ``bad_response`` (the schema declares
    no 200, so a plain transport answers it)."""
    raw = m6("analysis_queued")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=raw))
    engine = EngineClient(URL, SERVICE_KEY, transport=transport, principal=PRINCIPAL)
    with pytest.raises(EngineError) as caught:
        engine.analyze_state_proposals(STORY, idempotency_key=KEY)
    assert caught.value.code == "bad_response"


def test_a_redacted_listing_parses() -> None:
    page = Reconciliation.from_json(m6("reconciliation"))
    assert page is not None
    redacted = page.listings[-1]
    assert isinstance(redacted, ReconciliationListing)
    assert (redacted.effect, redacted.value, redacted.outcome) == (None, None, None)
    assert redacted.reverification is None
    assert redacted.compensated_by is None
    late, compensated = page.listings[1], page.listings[2]
    assert late.reverification is not None
    assert late.reverification.reasons == ["precondition:ownership"]
    assert compensated.compensated_by == "01JM6BX0000000000000000004"


def test_state_values_stay_raw() -> None:
    page = StoryStates.from_json(m6("states"))
    assert page is not None
    gold, trust, door, bank = page.states
    assert trust.values is not None
    assert trust.values[BOB] is None
    assert trust.values == {MARA: 0.9, ALICE: 0.4, BOB: None}
    assert trust.value is None
    assert door.value is False  # not None
    assert gold.value == 120
    assert bank.owner == "external"
    raw = m6("states")["states"][0]
    raw["value"] = {"a": 1}
    shaped = StoryState.from_json(raw)
    assert shaped is not None
    assert shaped.value == {"a": 1}
    del raw["value"]
    absent = StoryState.from_json(raw)
    assert absent is not None
    assert absent.value is None


def test_unknown_enums_are_kept() -> None:
    state = m6("states")["states"][0]
    state["owner"] = "consumer_owned"
    event = m6("scheduled_events")["events"][0]
    event["label"] = "rumour"
    listing = m6("reconciliation")["listings"][0]
    listing["reason"] = "later_reason"
    clock = m6("clock")
    clock["mode"] = "tidal"
    parsed_state = StoryState.from_json(state)
    parsed_event = ScheduledEvent.from_json(event)
    parsed_listing = ReconciliationListing.from_json(listing)
    parsed_clock = WorldClock.from_json(clock)
    assert parsed_state is not None
    assert parsed_state.owner == "consumer_owned"
    assert parsed_event is not None
    assert parsed_event.label == "rumour"
    assert parsed_listing is not None
    assert parsed_listing.reason == "later_reason"
    assert parsed_clock is not None
    assert parsed_clock.mode == "tidal"
    event["label"] = 7  # **Complement:** an open enum is still a string
    assert ScheduledEvent.from_json(event) is None


def test_slot_positions_are_five_part() -> None:
    written = StateWritten.from_json(m6("state_written"))
    assert written is not None
    assert written.slot_position == NarrativePosition(3, 0, 2, 0, 0)
    assert written.slot_position.slot_k == 2
    assert written.position == "3⁺2"
    raw = m6("state_written")
    del raw["slot"]["beat"]
    partial = StateWritten.from_json(raw)
    assert partial is not None  # the answer still parses
    assert partial.slot_position is None
    advanced = ClockAdvanced.from_json(m6("clock_advanced"))
    assert advanced is not None
    assert advanced.slot_position == NarrativePosition(3, 0, 2, 0, 0)
    queued = SettledNow.from_json(m6("settled_now_queued"))
    assert queued is not None
    assert queued.slot is None
    assert queued.slot_position is None
    states = StoryStates.from_json(m6("states"))
    assert states is not None
    assert states.states[0].since == NarrativePosition(3, 0, 1, 0, 0)
    assert states.states[2].since is None  # door_open: the initial value
    for name, model in (
        ("state_declared", StateDeclared),
        ("proposal_approved", ProposalApproved),
        ("settled_now", SettledNow),
        ("compensated", Compensated),
    ):
        answer = model.from_json(m6(name))
        assert answer is not None
        assert answer.slot_position == NarrativePosition(3, 0, 2, 0, 0)


def test_external_facts_view_their_propositions() -> None:
    page = ExternalFacts.from_json(m6("external_facts"))
    assert page is not None
    live, last_known = page.items
    assert isinstance(live, ExternalRead)
    assert live.facts[0].proposition_view is not None
    assert last_known.value is None
    assert page.next_after_id == last_known.event_id
    assert page.output_viewpoint == "omniscient"
    raw = m6("external_facts")
    raw["items"][0]["facts"][0]["proposition"] = {"nonsense": 1}
    kept = ExternalFacts.from_json(raw)
    assert kept is not None
    assert kept.items[0].facts[0].proposition == {"nonsense": 1}
    assert kept.items[0].facts[0].proposition_view is None


def test_the_subclassed_models_append_fields() -> None:
    detail = ProviderDetail.from_json(m6("provider"))
    mutation = ProviderMutation.from_json(m6("provider_mutation"))
    assert detail is not None
    assert mutation is not None
    assert detail.config_revision == 4
    assert mutation.config_revision == 5
    assert mutation.idempotency_key is None
    page = MemoryList.from_json(m6("memories_page1"))
    assert page is not None
    ordinary, reflection = page.memories
    assert isinstance(ordinary, ListedMemory)
    assert ordinary.consolidation is None
    assert reflection.consolidation is not None
    assert reflection.consolidation.source_memory_ids == [ordinary.id]
    assert page.next_cursor == CURSOR
    written = ProviderBindingWritten.from_json(m6("binding_written"))
    assert written is not None
    assert [w.code for w in written.warnings] == ["below_safety_minimum"]
    assert written.activated_required_providers == ["bank"]


# --- queries ----------------------------------------------------------------------------------


def test_states_reads_send_the_viewpoint(fake_engine: FakeEngine) -> None:
    for op in (BY_NAME["get_states"], BY_NAME["get_state"], BY_NAME["list_scheduled_events"]):
        serve(fake_engine, op)
    engine = client(fake_engine)
    engine.get_states(STORY)
    engine.get_states(STORY, viewpoint=f"character:{ALICE}")
    engine.get_state(STORY, "gold")
    engine.list_scheduled_events(STORY)
    engine.list_scheduled_events(STORY, viewpoint=f"character:{ALICE}")
    sent = [query(r) for r in fake_engine.requests]
    assert sent == [
        [("viewpoint", "omniscient")],
        [("viewpoint", f"character:{ALICE}")],
        [("viewpoint", "omniscient")],
        [("viewpoint", "omniscient")],
        [("viewpoint", f"character:{ALICE}")],
    ]
    for request in fake_engine.requests:  # **Complement**
        assert "actor_character_id" not in dict(query(request))


def test_get_state_as_of_is_sent_as_given(fake_engine: FakeEngine) -> None:
    serve(fake_engine, BY_NAME["get_state"])
    engine = client(fake_engine)
    view = engine.get_state(STORY, "gold", as_of="3+1")
    assert view.as_of == "3+1"
    engine.get_state(STORY, "gold")  # **Complement:** no as_of
    given, bare = fake_engine.requests
    assert given.url.query.decode() == "viewpoint=omniscient&as_of=3%2B1"
    assert dict(query(given))["as_of"] == "3+1"
    assert bare.url.query.decode() == "viewpoint=omniscient"


@pytest.mark.parametrize(
    ("name", "kwargs", "expected"),
    [
        ("inspect_external_facts", {}, {}),
        (
            "inspect_external_facts",
            {"as_of": 0, "limit": 5, "after_id": CURSOR},
            {"as_of": "0", "after_id": CURSOR},
        ),
        ("inspect_external_facts", {"as_of": 3, "after_id": ""}, {"as_of": "3"}),
        ("list_story_memories", {}, {}),
        (
            "list_story_memories",
            {
                "kind": "reflection",
                "retrieval_tier": "hot",
                "holder_character_id": ALICE,
                "from_turn": 0,
                "to_turn": 3,
                "quarantined": False,
                "pinned": True,
                "limit": 7,
                "cursor": CURSOR,
            },
            {
                "kind": "reflection",
                "retrieval_tier": "hot",
                "holder_character_id": ALICE,
                "from_turn": "0",
                "to_turn": "3",
                "quarantined": "false",
                "pinned": "true",
                "cursor": CURSOR,
            },
        ),
        ("list_story_memories", {"kind": "", "cursor": ""}, {"kind": ""}),
    ],
)
def test_omniscient_reads_send_only_given_filters(
    fake_engine: FakeEngine, name: str, kwargs: dict[str, Any], expected: dict[str, str]
) -> None:
    """**Complement:** ``from_turn=0``, ``quarantined=False`` and ``as_of=0`` are sent; an
    empty cursor (``after_id``, ``cursor``) is not; an empty string filter is."""
    op = BY_NAME[name]
    serve(fake_engine, op)
    getattr(client(fake_engine), name)(STORY, **kwargs)
    [request] = fake_engine.requests
    sent = query(request)
    assert sent[:2] == [("viewpoint", "omniscient"), ("limit", str(kwargs.get("limit", 100)))]
    assert dict(sent[2:]) == expected
    assert len(sent[2:]) == len(expected)


def test_get_fact_is_omniscient(fake_engine: FakeEngine) -> None:
    serve(fake_engine, BY_NAME["get_fact"])
    fact = client(fake_engine).get_fact(STORY, FACT)
    assert fact.tx_to is not None  # a superseded version
    [request] = fake_engine.requests
    assert query(request) == [("viewpoint", "omniscient")]


def test_list_stories_profile_filters_only_when_given(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/stories", 200, json=engine_fixture("list_stories"))
    engine = client(fake_engine)
    engine.list_stories()
    engine.list_stories("active")
    engine.list_stories(engine_profile=PROFILE)
    engine.list_stories("archived", engine_profile=PROFILE, engine_profile_version=3)
    engine.list_stories(engine_profile_version=2)  # passed through unchecked
    bare, status, profile, versioned, version_only = (
        r.url.query.decode() for r in fake_engine.requests
    )
    assert bare == ""  # **Complement:** the bare call's query is main's
    assert status == "status=active"
    assert profile == f"engine_profile={PROFILE}"
    assert versioned == f"status=archived&engine_profile={PROFILE}&engine_profile_version=3"
    assert version_only == "engine_profile_version=2"


# --- headers and bodies -----------------------------------------------------------------------


@pytest.mark.parametrize("name", [*SLOT_WRITES, "correct"])
def test_slot_writes_send_expected_revision_only_when_given(
    fake_engine: FakeEngine, name: str
) -> None:
    op = BY_NAME[name]
    serve(fake_engine, op)
    engine = client(fake_engine)
    call(engine, op, expected_revision=7)
    call(engine, op, expected_revision=None)
    given, bare = fake_engine.requests
    assert given.headers[EXPECTED_REVISION_HEADER] == "7"
    assert EXPECTED_REVISION_HEADER not in bare.headers  # **Complement**


@pytest.mark.parametrize("name", CONFIGURATION_WRITES)
def test_configuration_writes_never_send_expected_revision(
    fake_engine: FakeEngine, name: str
) -> None:
    """**Complement:** the configuration writes, the analysis and the dismissal take no
    expected revision at all."""
    op = BY_NAME[name]
    serve(fake_engine, op)
    call(client(fake_engine), op)
    [request] = fake_engine.requests
    assert EXPECTED_REVISION_HEADER not in request.headers
    with pytest.raises(TypeError):
        call(client(fake_engine), op, expected_revision=7)


def test_write_bodies(fake_engine: FakeEngine) -> None:
    for name in (
        "set_state",
        "declare_state",
        "approve_state_proposal",
        "advance_clock",
        "create_scheduled_event",
        "reschedule_scheduled_event",
        "compensate",
        "bind_provider",
        "update_state_definition",
        "update_clock",
        "register_provider",
        "replace_provider",
        "correct",
    ):
        serve(fake_engine, BY_NAME[name])
    engine = client(fake_engine)
    slot: dict[str, Any] = {"expected_revision": None, "idempotency_key": KEY}
    engine.set_state(STORY, "gold", SET_BODY, **slot)
    engine.declare_state(STORY, DECLARATION, **slot)
    engine.approve_state_proposal(STORY, PROPOSAL, **slot)
    engine.approve_state_proposal(STORY, PROPOSAL, definition={"unit": "flasks"}, **slot)
    engine.advance_clock(STORY, reason="Wait", minutes=30, **slot)
    engine.advance_clock(STORY, reason="Wait", to="18:00", **slot)
    engine.advance_clock(STORY, reason="Wait", to=600, **slot)
    engine.create_scheduled_event(STORY, EVENT_BODY, **slot)
    engine.reschedule_scheduled_event(STORY, EVENT, shared_owners=[], **slot)
    engine.reschedule_scheduled_event(STORY, EVENT, planned="09:30", shared_owners=[ALICE], **slot)
    engine.reschedule_scheduled_event(STORY, EVENT, **slot)
    engine.compensate(STORY, OUTBOX, EFFECT, **slot)
    engine.bind_provider(STORY, "bank", {"account_id": "acct-7731"}, idempotency_key=KEY)
    engine.update_state_definition(STORY, "gold", {"unit": None}, idempotency_key=KEY)
    engine.update_clock(STORY, {"pacing_table": None}, idempotency_key=KEY)
    engine.register_provider(REGISTRATION, idempotency_key=KEY)
    engine.replace_provider("bank", {"trust_tier": "advisory"}, idempotency_key=KEY)
    engine.correct(STORY, CORRECTION, **slot)
    assert [body(r) for r in fake_engine.requests] == [
        SET_BODY,
        {"definition": DECLARATION},
        {},
        {"definition": {"unit": "flasks"}},
        {"reason": "Wait", "minutes": 30},
        {"reason": "Wait", "to": "18:00"},
        {"reason": "Wait", "to": 600},
        EVENT_BODY,
        {"shared_owners": []},
        {"planned": "09:30", "shared_owners": [ALICE]},
        {},
        {"effect": EFFECT},
        {"external_ids": {"account_id": "acct-7731"}},
        {"unit": None},
        {"pacing_table": None},
        REGISTRATION,
        {"trust_tier": "advisory"},
        CORRECTION,
    ]
    correct = fake_engine.requests[-1]
    assert correct.url.query.decode() == ""  # the declared viewpoint query is left out
    assert "Prefer" not in correct.headers


@pytest.mark.parametrize(
    "name",
    ["dismiss_state_proposal", "settle_now", "cancel_scheduled_event", "analyze_state_proposals"],
)
def test_bodyless_writes_send_no_body(fake_engine: FakeEngine, name: str) -> None:
    """**Complement** of ``test_write_bodies``."""
    op = BY_NAME[name]
    serve(fake_engine, op)
    call(client(fake_engine), op)
    [request] = fake_engine.requests
    assert request.content == b""


def test_path_segments_are_encoded(fake_engine: FakeEngine) -> None:
    for name in ("get_state", "set_state", "update_state_definition", "get_provider"):
        serve(fake_engine, BY_NAME[name])
    engine = client(fake_engine)
    engine.get_state(STORY, "gold coins/2")
    engine.set_state(STORY, "gold coins/2", SET_BODY, expected_revision=None, idempotency_key=KEY)
    engine.update_state_definition(STORY, "gold coins/2", {}, idempotency_key=KEY)
    engine.get_provider("a/b")
    paths = [r.url.raw_path.decode().split("?", 1)[0] for r in fake_engine.requests]
    assert paths == [
        f"{S}/states/gold%20coins%2F2",
        f"{S}/states/gold%20coins%2F2",
        f"{S}/states/gold%20coins%2F2/definition",
        f"{PROVIDERS}/a%2Fb",
    ]


def test_delete_provider_answers_none_on_204(fake_engine: FakeEngine) -> None:
    fake_engine.respond("DELETE", PROVIDER, 204)
    assert client(fake_engine).delete_provider("bank", idempotency_key=KEY) is None
    refusal = error_json("provider_in_use", "in use", stories=[STORY])
    fake_engine.respond("DELETE", PROVIDER, 409, json=refusal)
    with pytest.raises(EngineError) as caught:
        client(fake_engine).delete_provider("bank", idempotency_key=KEY)
    assert caught.value.code == "provider_in_use"


def test_correct_parses_the_regenerated_body(fake_engine: FakeEngine) -> None:
    serve(fake_engine, BY_NAME["correct"])
    answer = client(fake_engine).correct(
        STORY, CORRECTION, expected_revision=13, idempotency_key=KEY
    )
    assert isinstance(answer, RegenerateResult)
    assert answer.previous_variant == 1
    [notice] = answer.notices
    assert isinstance(notice, Notice)
    assert notice.kind == "implausible_knowledge"
    assert notice.correctable is False
    [required] = answer.reconciliation_required
    assert isinstance(required, RequiredEffect)
    assert required.outbox_id == m6("reconciliation")["listings"][0]["outbox_id"]
    assert answer.unparsed == {}


@pytest.mark.parametrize(("name", "module"), [("get_states", "states"), ("advance_clock", "clock")])
def test_m6_reads_raise_module_off_with_its_module(
    fake_engine: FakeEngine, name: str, module: str
) -> None:
    op = BY_NAME[name]
    refusal = error_json("module_off", f"{module} is off", module=module)
    fake_engine.respond(op.method, op.route, 409, json=refusal)
    with pytest.raises(EngineError) as caught:
        call(client(fake_engine), op)
    assert caught.value.code == "module_off"
    assert caught.value.details["module"] == module


# --- timeouts and the operation log (#44) ----------------------------------------------------


def test_slot_writes_wait_the_turn_timeout(fake_engine: FakeEngine) -> None:
    for op in OPS:
        serve(fake_engine, op)
    engine = client(fake_engine)
    for op in OPS:
        call(engine, op)
    by_name = dict(zip((op.name for op in OPS), fake_engine.requests, strict=True))
    assert read_timeout(by_name["set_state"]) == turn_timeout(None)
    assert read_timeout(by_name["update_clock"]) == MUTATION_TIMEOUT_S
    for name in SLOT_WRITES:
        assert read_timeout(by_name[name]) == turn_timeout(None), name
    for name in CONFIGURATION_WRITES:
        assert read_timeout(by_name[name]) == MUTATION_TIMEOUT_S, name
    for op in OPS:
        if op.method == "GET":
            assert read_timeout(by_name[op.name]) == engine.timeout, op.name


def _operator_request(user: Any) -> HttpRequest:
    request = RequestFactory().post("/")
    request.user = user
    return request


@pytest.mark.django_db
def test_m6_writes_are_recorded(fake_engine: FakeEngine, operator: Any) -> None:
    writes = [op for op in OPS if op.method != "GET"]
    assert len(writes) == 18
    for op in writes:
        serve(fake_engine, op)
    keys = [f"01JM6KY{'0' * 17}{n:02d}" for n in range(len(writes))]
    with override_settings(ENGINE_URL=URL, ENGINE_SERVICE_KEY=SERVICE_KEY):
        engine = engine_client_for(_operator_request(operator))
        engine._transport = fake_engine.transport
        for op, key in zip(writes, keys, strict=True):
            call(engine, op, idempotency_key=key)
    rows = {r.kind: r for r in EngineOperation.objects.order_by("id")}
    assert list(rows) == [op.name for op in writes]
    for op, key in zip(writes, keys, strict=True):
        row = rows[op.name]
        assert row.idempotency_key == key, op.name
        if op.path.startswith("/stories/"):
            assert (row.entity_type, row.entity_id, row.story_id) == ("story", STORY, STORY)
    for name in ("register_provider", "replace_provider", "delete_provider"):
        assert (rows[name].entity_type, rows[name].entity_id) == ("external_provider", "bank")
    assert rows["analyze_state_proposals"].outcome == EngineOperation.Outcome.QUEUED
    assert rows["set_state"].outcome == EngineOperation.Outcome.OK
    for request in fake_engine.requests:
        assert EXPECTED_REVISION_HEADER not in request.headers  # none was given


def test_registration_about_without_a_name() -> None:
    from console.engine.client import (
        RecordedAbout,
        provider_about,
        provider_registration_about,
    )

    assert provider_registration_about({"registration": REGISTRATION}) == RecordedAbout(
        "external_provider", "bank", None
    )
    cases: list[dict[str, Any]] = [{"registration": {}}, {"registration": {"name": 7}}, {}]
    for arguments in cases:
        assert provider_registration_about(arguments) == RecordedAbout(
            "external_provider", None, None
        )
    assert provider_about({"name": "shop"}) == RecordedAbout("external_provider", "shop", None)


def test_the_fixtures_share_one_cast() -> None:
    """D16: every M6 fixture reuses ``story.json``'s cast."""
    story = m6("story")
    assert story["id"] == STORY
    assert [(c["id"], c["name"], c["is_player"]) for c in story["characters"]] == [
        (MARA, "Mara", True),
        (ALICE, "Alice", False),
        (BOB, "Bob", False),
    ]
    assert all(c["weight"] == "major" for c in story["characters"])
    assert set(m6("states")["states"][1]["values"]) == {MARA, ALICE, BOB}
    assert m6("clock") == m6("states")["clock"]
    assert m6("compensated")["compensates"] == m6("reconciliation")["listings"][2]["outbox_id"]
    assert (ENGINE / "m6" / "story.json").is_file()

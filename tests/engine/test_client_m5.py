"""The M5 client operations (#186): the knowledge reads, the belief write, the character
controls and the new parameters on existing calls, against the schema-validating fake Engine.

Fixtures: ``tests/fixtures/engine/m5/`` (one cast, ``story.json``: the player Mara, and Alice,
Bob and Carol, every other file reusing their ids)."""

import copy
import json
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
    EXPOSURE_STATES,
    BeliefItem,
    Beliefs,
    BeliefWritten,
    CharacterWeighted,
    Divergences,
    Exposures,
    IdentityItem,
    NarrativePosition,
    ParticipationSet,
    Proposition,
)
from console.engine.timeouts import STORY_CHARACTER_ADD
from console.engine_access import engine_client_for
from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import ENGINE, engine_fixture
from tests.play.helpers import error_json

URL = "http://engine.test"
SERVICE_KEY = "service-key-for-the-m5-client-test-0000"
PRINCIPAL = "console:test"
STORY = "01JM5ST0000000000000000001"
MARA, ALICE, BOB, CAROL = (f"01JM5CH{'0' * 17}{n:02d}" for n in (1, 2, 3, 4))
WARDEN = "01JM5EN0000000000000000001"
PROP = "01JM5PR0000000000000000001"
CURSOR = "01JM5PR0000000000000000009"
KEY = "01JM5KY0000000000000000099"

BELIEFS = "/stories/{story_id}/inspect/beliefs"
DIVERGENCES = "/stories/{story_id}/inspect/divergences"
EXPOSURES = "/stories/{story_id}/inspect/exposures"
BELIEF_WRITE = "/admin/stories/{story_id}/beliefs"
PARTICIPATION = "/stories/{story_id}/characters/{character_id}/participation"
WEIGHT = "/stories/{story_id}/characters/{character_id}/weight"
MODEL = "/stories/{story_id}/entities/{entity_id}/model"
NOT_FOUND = error_json("not_found", "no such story")


def m5(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m5/{name}"))


def client(fake: FakeEngine) -> EngineClient:
    return EngineClient(URL, SERVICE_KEY, transport=fake.transport, principal=PRINCIPAL)


def query(request: httpx.Request) -> list[tuple[str, str]]:
    return parse_qsl(request.url.query.decode(), keep_blank_values=True)


def body(request: httpx.Request) -> Any:
    return json.loads(request.content)


def read_timeout(request: httpx.Request) -> float:
    timeout: dict[str, float] = request.extensions["timeout"]
    return timeout["read"]


# --- the models: C2 ---------------------------------------------------------------------------

PAGES: list[tuple[str, type[Beliefs | Divergences | Exposures], tuple[str, ...]]] = [
    ("beliefs_bad_item", Beliefs, ("beliefs", "identities")),
    ("divergences_bad_item", Divergences, ("items",)),
    ("exposures_bad_item", Exposures, ("items",)),
]


@pytest.mark.parametrize(("name", "model", "keys"), PAGES)
def test_a_malformed_item_is_kept_raw_not_fatal(
    name: str, model: type[Beliefs | Divergences | Exposures], keys: tuple[str, ...]
) -> None:
    raw = m5(name)
    page = model.from_json(raw)
    assert page is not None
    for key in keys:
        typed = getattr(page, key)
        assert len(typed) == 1  # the good entry
        assert typed[0] == type(typed[0]).from_json(raw[key][0])
        assert page.unparsed[key] == raw[key]  # the raw list, both entries
    assert set(page.unparsed) == set(keys)


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("beliefs_page1", Beliefs),
        ("beliefs_empty", Beliefs),
        ("divergences", Divergences),
        ("exposures", Exposures),
    ],
)
def test_a_good_page_has_nothing_unparsed(
    name: str, model: type[Beliefs | Divergences | Exposures]
) -> None:
    """**Complement:** with the good fixtures, ``unparsed`` is empty."""
    page = model.from_json(m5(name))
    assert page is not None
    assert page.unparsed == {}


def test_an_unparseable_proposition_leaves_the_row() -> None:
    raw = m5("beliefs_page1")["beliefs"][0]
    good = BeliefItem.from_json(raw)
    assert good is not None
    assert good.proposition_view == Proposition.from_json(raw["proposition"])
    assert good.proposition_view is not None
    raw["proposition"] = {"nonsense": 1}
    item = BeliefItem.from_json(raw)
    assert item is not None
    assert item.proposition == {"nonsense": 1}
    assert item.proposition_view is None


def test_an_unknown_identity_source_is_kept() -> None:
    raw = m5("beliefs_page1")
    raw["identities"][0]["source"] = "rumour"
    page = Beliefs.from_json(raw)
    assert page is not None
    assert page.identities[0].source == "rumour"
    assert page.unparsed == {}
    assert IdentityItem.from_json({"entities": [], "source": "rumour", "record_id": PROP})


def test_the_pages_typed_views() -> None:
    beliefs = Beliefs.from_json(m5("beliefs_page1"))
    assert beliefs is not None
    assert beliefs.character == ALICE
    assert beliefs.next_after == beliefs.beliefs[0].proposition_id
    assert beliefs.identities[0].record_id == beliefs.beliefs[0].belief_id
    assert beliefs.beliefs[0].since == NarrativePosition(2, 0, 0, 3, 0)
    assert beliefs.output_viewpoint == "omniscient"
    divergences = Divergences.from_json(m5("divergences"))
    assert divergences is not None
    lie, revealed = divergences.items
    assert lie.believed is None
    assert lie.believed_view is None
    assert lie.asserted_view is not None
    assert revealed.believed_view is not None
    assert revealed.revealed_by_event_id
    exposures = Exposures.from_json(m5("exposures"))
    assert exposures is not None
    assert [e.state for e in exposures.items] == list(EXPOSURE_STATES)
    assert exposures.items[0].source is None
    assert exposures.items[1].proposition_view is not None


def test_a_write_answer_that_does_not_parse_is_a_bad_response(fake_engine: FakeEngine) -> None:
    """Write answers are strict (C2): one that doesn't parse is #43's ``bad_response``."""
    raw = m5("belief_written")
    assert BeliefWritten.from_json(raw) is not None
    del raw["belief"]["held_from"]["beat"]
    assert BeliefWritten.from_json(raw) is None
    fake_engine.side_effect("POST", BELIEF_WRITE, lambda _: httpx.Response(201, json=raw))
    with pytest.raises(EngineError) as caught:
        client(fake_engine).write_belief(STORY, {"holder": ALICE}, idempotency_key=KEY)
    assert caught.value.code == "bad_response"


# --- the knowledge reads ----------------------------------------------------------------------


def test_belief_read_sends_omniscient_and_only_given_filters(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", BELIEFS, 200, json=m5("beliefs_page1"))
    page = client(fake_engine).inspect_beliefs(STORY, ALICE)
    assert isinstance(page, Beliefs)
    assert page.beliefs[0].provenance == "testimony"
    [request] = fake_engine.requests
    assert request.url.path == f"/stories/{STORY}/inspect/beliefs"
    assert request.url.query.decode() == f"viewpoint=omniscient&character={ALICE}&limit=100"
    assert read_timeout(request) == client(fake_engine).timeout


def test_belief_read_filters_and_cursor(fake_engine: FakeEngine) -> None:
    """**Complement:** a filter is sent only when given; ``as_of=0`` is sent as ``0``; an empty
    ``after`` is not."""
    fake_engine.respond("GET", BELIEFS, 200, json=m5("beliefs_empty"))
    engine = client(fake_engine)
    engine.inspect_beliefs(STORY, MARA, as_of=0, after="")
    engine.inspect_beliefs(STORY, MARA, proposition_id=PROP, as_of=3, limit=7, after=CURSOR)
    first, second = (dict(query(r)) for r in fake_engine.requests)
    assert first == {"viewpoint": "omniscient", "character": MARA, "limit": "100", "as_of": "0"}
    assert second == {
        "viewpoint": "omniscient",
        "character": MARA,
        "limit": "7",
        "proposition_id": PROP,
        "as_of": "3",
        "after": CURSOR,
    }


@pytest.mark.parametrize(
    ("method", "route", "fixture", "kwargs", "expected"),
    [
        ("inspect_divergences", DIVERGENCES, "divergences", {}, {}),
        (
            "inspect_divergences",
            DIVERGENCES,
            "divergences",
            {"speaker": BOB, "subject": WARDEN, "as_of": 0, "limit": 5, "after_id": CURSOR},
            {"speaker": BOB, "subject": WARDEN, "as_of": "0", "limit": "5", "after_id": CURSOR},
        ),
        ("inspect_divergences", DIVERGENCES, "divergences", {"after_id": ""}, {}),
        ("inspect_exposures", EXPOSURES, "exposures", {}, {}),
        (
            "inspect_exposures",
            EXPOSURES,
            "exposures",
            {"character": CAROL, "state": "resolved", "as_of": 0, "after_id": CURSOR},
            {"character": CAROL, "state": "resolved", "as_of": "0", "after_id": CURSOR},
        ),
        (
            "inspect_exposures",
            EXPOSURES,
            "exposures",
            {"state": "unresolved", "after_id": ""},
            {"state": "unresolved"},
        ),
    ],
)
def test_divergence_and_exposure_reads_send_only_given_filters(
    fake_engine: FakeEngine,
    method: str,
    route: str,
    fixture: str,
    kwargs: dict[str, Any],
    expected: dict[str, str],
) -> None:
    fake_engine.respond("GET", route, 200, json=m5(fixture))
    page = getattr(client(fake_engine), method)(STORY, **kwargs)
    assert isinstance(page, Divergences | Exposures)
    assert len(page.items) == 2
    [request] = fake_engine.requests
    assert request.url.path == route.replace("{story_id}", STORY)
    sent = query(request)
    assert sent[:2] == [("viewpoint", "omniscient"), ("limit", str(kwargs.get("limit", 100)))]
    assert dict(sent[2:]) == {k: v for k, v in expected.items() if k != "limit"}


@pytest.mark.parametrize(
    ("method", "route", "args"),
    [
        ("inspect_beliefs", BELIEFS, (ALICE,)),
        ("inspect_divergences", DIVERGENCES, ()),
        ("inspect_exposures", EXPOSURES, ()),
    ],
)
def test_reads_raise_module_off_with_its_module(
    fake_engine: FakeEngine, method: str, route: str, args: tuple[str, ...]
) -> None:
    refusal = error_json("module_off", "the story keeps no partitions", module="partitions")
    fake_engine.respond("GET", route, 409, json=refusal)
    with pytest.raises(EngineError) as caught:
        getattr(client(fake_engine), method)(STORY, *args)
    assert caught.value.code == "module_off"
    assert caught.value.details["module"] == "partitions"


# --- the writes -------------------------------------------------------------------------------


def belief_request() -> dict[str, Any]:
    return {
        "holder": ALICE,
        "proposition": m5("belief_written")["belief"]["proposition"],
        "confidence_scalar": 0.6,
        "disclosure": "secret",
    }


def test_write_belief_sends_the_body_as_given_and_parses_201(fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", BELIEF_WRITE, 201, json=m5("belief_written"))
    request_body = belief_request()
    written = client(fake_engine).write_belief(STORY, request_body, idempotency_key=KEY)
    assert isinstance(written, BeliefWritten)
    assert written.belief.holder_character_id == ALICE
    assert written.belief.proposition_view is not None
    assert written.closed == ["01JM5BE0000000000000000001"]
    assert written.transition.antecedent["kind"] == "record"
    [request] = fake_engine.requests
    assert request.method == "POST"
    assert request.url.path == f"/admin/stories/{STORY}/beliefs"
    assert body(request) == request_body
    assert request.headers["Idempotency-Key"] == KEY
    assert EXPECTED_REVISION_HEADER not in request.headers  # **Complement**
    assert read_timeout(request) == turn_timeout(None)


def test_participation_and_weight_bodies(fake_engine: FakeEngine) -> None:
    fake_engine.respond("PATCH", PARTICIPATION, 200, json=m5("participation_set"))
    fake_engine.respond("POST", WEIGHT, 200, json=m5("character_weighted"))
    engine = client(fake_engine)
    role = engine.set_participation(STORY, ALICE, role="reactive", fixed=True, idempotency_key=KEY)
    assert isinstance(role, ParticipationSet)
    assert role.character.participation is not None
    assert (role.character.participation.role, role.character.participation.fixed) == (
        "reactive",
        True,
    )
    engine.set_participation(STORY, ALICE, role="primary", fixed=False, idempotency_key=KEY)
    weighted = engine.set_weight(STORY, BOB, "minor", idempotency_key=KEY)
    assert isinstance(weighted, CharacterWeighted)
    assert weighted.character.weight == "minor"
    fixed, unfixed, weight = fake_engine.requests
    assert fixed.method == "PATCH"
    assert fixed.url.path == f"/stories/{STORY}/characters/{ALICE}/participation"
    assert body(fixed) == {"role": "reactive", "fixed": True}
    assert body(unfixed) == {"role": "primary", "fixed": False}
    assert weight.url.path == f"/stories/{STORY}/characters/{BOB}/weight"
    assert body(weight) == {"weight": "minor"}
    for request in fake_engine.requests:
        assert request.headers["Idempotency-Key"] == KEY
        assert EXPECTED_REVISION_HEADER not in request.headers
        assert read_timeout(request) == MUTATION_TIMEOUT_S


def test_model_entity_omits_a_missing_definition(fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", MODEL, 201, json=m5("entity_modeled"))
    engine = client(fake_engine)
    modeled = engine.model_entity(STORY, WARDEN, true_age=45, idempotency_key=KEY)
    assert isinstance(modeled, CharacterWeighted)
    assert modeled.character.id == WARDEN
    assert modeled.character.weight == "minor"
    engine.model_entity(STORY, WARDEN, true_age=45, definition="The warden.", idempotency_key=KEY)
    bare, defined = fake_engine.requests
    assert bare.url.path == f"/stories/{STORY}/entities/{WARDEN}/model"
    assert body(bare) == {"true_age": 45}
    assert body(defined) == {"true_age": 45, "definition": "The warden."}
    assert read_timeout(bare) == STORY_CHARACTER_ADD
    assert EXPECTED_REVISION_HEADER not in bare.headers


# --- new parameters on existing calls ---------------------------------------------------------


def test_history_player_view_only_when_given(fake_engine: FakeEngine) -> None:
    fake_engine.respond(
        "GET", "/stories/{story_id}/history", 200, json=engine_fixture("get_history")
    )
    engine = client(fake_engine)
    engine.get_history(STORY)
    engine.get_history(STORY, viewpoint="omniscient", as_of=2)
    engine.get_history(STORY, player_view="character")
    engine.get_history(STORY, viewpoint=f"character:{ALICE}", player_view="story")
    default, viewed, character, story = (r.url.query.decode() for r in fake_engine.requests)
    assert default == ""  # **Complement:** the default call's query is main's
    assert viewed == "viewpoint=omniscient&as_of=2"
    assert character == "player_view=character"
    assert dict(parse_qsl(story)) == {"viewpoint": f"character:{ALICE}", "player_view": "story"}


def test_assign_version_only_when_given(fake_engine: FakeEngine) -> None:
    fake_engine.respond("PUT", "/stories/{story_id}/engine-profile", 404, json=NOT_FOUND)
    engine = client(fake_engine)
    profile = "01JM5PF0000000000000000001"
    for version in (None, "latest", 1):
        with pytest.raises(EngineError):
            engine.assign_engine_profile(
                STORY, profile, ignore_warnings=False, idempotency_key=KEY, version=version
            )
    bare, latest, pinned = (body(r) for r in fake_engine.requests)
    assert bare == {"profile_id": profile, "ignore_warnings": False}
    assert latest == {"profile_id": profile, "ignore_warnings": False, "version": "latest"}
    assert pinned == {"profile_id": profile, "ignore_warnings": False, "version": 1}


def test_fork_and_truncate_slot_only_when_given(fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", "/stories/{story_id}/truncate", 404, json=NOT_FOUND)
    fake_engine.respond("POST", "/stories/{story_id}/fork", 404, json=NOT_FOUND)
    engine = client(fake_engine)
    for slot in (None, 0, 2):
        with pytest.raises(EngineError):
            engine.truncate(STORY, 3, expected_revision=None, idempotency_key=KEY, slot=slot)
        with pytest.raises(EngineError):
            engine.fork(
                STORY,
                at_turn=3,
                name="Branch",
                expected_revision=None,
                idempotency_key=KEY,
                slot=slot,
            )
    sent = [(r.url.path.rsplit("/", 1)[-1], body(r)) for r in fake_engine.requests]
    assert sent == [
        ("truncate", {"to_turn": 3}),
        ("fork", {"at_turn": 3, "name": "Branch"}),
        ("truncate", {"to_turn": 3, "slot": 0}),
        ("fork", {"at_turn": 3, "name": "Branch", "slot": 0}),
        ("truncate", {"to_turn": 3, "slot": 2}),
        ("fork", {"at_turn": 3, "name": "Branch", "slot": 2}),
    ]


def test_add_character_participation_only_when_given(fake_engine: FakeEngine) -> None:
    fake_engine.respond("POST", "/stories/{story_id}/characters", 404, json=NOT_FOUND)
    engine = client(fake_engine)
    common: dict[str, Any] = {
        "name": "Dale",
        "definition": "A delivery driver.",
        "true_age": 52,
        "expected_revision": None,
        "idempotency_key": KEY,
    }
    for participation in (None, ("silent", False), ("primary", True)):
        with pytest.raises(EngineError):
            engine.add_story_character(STORY, **common, participation=participation)
    bare, silent, primary = (body(r) for r in fake_engine.requests)
    main = {"name": "Dale", "definition": "A delivery driver.", "true_age": 52}
    assert bare == main  # **Complement:** without it the body is main's
    assert silent == {**main, "participation": {"role": "silent", "fixed": False}}
    assert primary == {**main, "participation": {"role": "primary", "fixed": True}}


# --- the operation log (#44) ------------------------------------------------------------------


def _operator_request(user: Any) -> HttpRequest:
    request = RequestFactory().post("/")
    request.user = user
    return request


@pytest.mark.django_db
def test_m5_writes_are_recorded_on_the_story(fake_engine: FakeEngine, operator: Any) -> None:
    fake_engine.respond("POST", BELIEF_WRITE, 201, json=m5("belief_written"))
    fake_engine.respond("PATCH", PARTICIPATION, 200, json=m5("participation_set"))
    fake_engine.respond("POST", WEIGHT, 200, json=m5("character_weighted"))
    fake_engine.respond("POST", MODEL, 201, json=m5("entity_modeled"))
    with override_settings(ENGINE_URL=URL, ENGINE_SERVICE_KEY=SERVICE_KEY):
        engine = engine_client_for(_operator_request(operator))
        engine._transport = fake_engine.transport
        keys = [f"01JM5KY{'0' * 17}{n:02d}" for n in range(11, 15)]
        engine.write_belief(STORY, belief_request(), idempotency_key=keys[0])
        engine.set_participation(STORY, ALICE, role="reactive", fixed=True, idempotency_key=keys[1])
        engine.set_weight(STORY, BOB, "minor", idempotency_key=keys[2])
        engine.model_entity(STORY, WARDEN, true_age=45, idempotency_key=keys[3])
    rows = list(EngineOperation.objects.order_by("id"))
    assert [r.kind for r in rows] == [
        "write_belief",
        "set_participation",
        "set_weight",
        "model_entity",
    ]
    for row, key in zip(rows, keys, strict=True):
        assert row.entity_type == "story"
        assert row.entity_id == STORY
        assert row.story_id == STORY
        assert row.idempotency_key == key
        assert row.outcome == EngineOperation.Outcome.OK
    for request in fake_engine.requests:
        assert EXPECTED_REVISION_HEADER not in request.headers
    belief, modeled = rows[0].response, rows[3].response
    assert belief is not None
    assert modeled is not None
    assert belief["operation_id"] == m5("belief_written")["operation_id"]
    assert modeled["character"]["id"] == WARDEN


def test_the_fixtures_are_where_the_page_stories_expect_them() -> None:
    """The cast every M5 fixture reuses is ``story.json``'s."""
    story = m5("story")
    assert [(c["id"], c["name"], c["is_player"]) for c in story["characters"]] == [
        (MARA, "Mara", True),
        (ALICE, "Alice", False),
        (BOB, "Bob", False),
        (CAROL, "Carol", False),
    ]
    assert all(c["weight"] == "major" for c in story["characters"])
    assert (ENGINE / "m5" / "story.json").is_file()
    assert story["id"] == STORY

"""#52's client additions: ``get_history`` with every parameter, ``list_variants`` and the
parsed audit, summary and policy fields."""

import pytest

from console.engine.client import EngineClient
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.history.helpers import HISTORY, MARA, STORY, VARIANTS, policy, settings_json


@pytest.fixture
def client(fake_engine: FakeEngine) -> EngineClient:
    return EngineClient(
        "http://engine.test", "k", transport=fake_engine.transport, principal="console:nate"
    )


def test_get_history_sends_every_parameter(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", HISTORY, 200, json=engine_fixture("get_history_audit"))
    history = client.get_history(
        STORY,
        viewpoint="omniscient",
        as_of=0,
        limit=1000,
        before_turn=3,
        actor_character_id=MARA,
        variant="3:2",
        include_audit=True,
        include_summary=True,
    )
    (request,) = fake_engine.requests
    assert dict(request.url.params) == {
        "viewpoint": "omniscient",
        "as_of": "0",
        "limit": "1000",
        "before_turn": "3",
        "actor_character_id": MARA,
        "variant": "3:2",
        "include_audit": "1",
        "include_summary": "1",
    }
    audit = history.audit_entries
    assert audit is not None
    assert len(audit) == 3
    assert {a.kind for a in audit} == {"guardrail_output_rejected"}
    assert history.summary is not None
    assert history.summary[0].covers_from == 1


def test_false_and_none_are_left_out(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", HISTORY, 200, json=engine_fixture("get_history"))
    history = client.get_history(STORY, include_audit=False, include_summary=False)
    (request,) = fake_engine.requests
    assert request.url.query == b""
    assert history.audit_entries is None


def test_list_variants(client: EngineClient, fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", VARIANTS, 200, json=engine_fixture("list_variants"))
    chains = client.list_variants(STORY)
    assert [(c.turn, c.variant, c.membership, c.requires_check) for c in chains] == [
        (1, 1, "active", False),
        (3, 2, "dormant", True),
    ]
    assert chains[1].parent is not None
    assert chains[1].parent.turn == 1


def test_settings_parse_effective_and_inactive_policies(
    client: EngineClient, fake_engine: FakeEngine
) -> None:
    fake_engine.respond(
        "GET",
        "/stories/{story_id}/settings",
        200,
        json=settings_json(effective_policies=[policy("a", None)]),
    )
    settings = client.get_story_settings(STORY)
    assert [(p.id, p.name) for p in settings.effective] == [("a", None)]
    assert [p.id for p in settings.inactive] == ["minors_no_sexual_content"]

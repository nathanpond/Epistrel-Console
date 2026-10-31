"""The shared M5 test support (#188, C14): the seeded Structured v2 effective configuration and the
Alice/Bob/Pat cast pass the schema-validating fake Engine; ``with_settings`` varies a setting; the
fake answers by query string.

``effective_config_structured_v2.json`` is the Engine's ``structured_settings()`` laid over
``profile_defaults()`` (``core/seeds/profiles.py``), and its ``capabilities`` are
``capability_names`` of that configuration, both computed once at Engine ``origin/main``
7d181b35fa64692f6cdbe31e8260042d57d3c784."""

import json
from typing import Any

import httpx
import pytest

from tests.fakes.engine import FakeEngine, UnconfiguredEngineCall
from tests.inspect.helpers import (
    ALICE,
    BOB,
    EFFECTIVE,
    FIXTURES,
    PAT,
    STORY,
    STORY_PATH,
    story_cast,
    structured_v2,
    with_settings,
)

BASE = "http://engine.test"
BELIEFS = "/stories/{story_id}/inspect/beliefs"


def client(fake: FakeEngine) -> httpx.Client:
    return httpx.Client(transport=fake.transport, base_url=BASE)


def test_v2_effective_config_passes_the_fake() -> None:
    fake = FakeEngine()
    body = structured_v2()
    fake.respond("GET", EFFECTIVE, 200, json=body)
    answered = client(fake).get(f"/stories/{STORY}/effective-config").json()
    assert answered == body
    effective = body["effective"]
    assert effective["character_knowledge"]["partitions"] is True
    assert effective["character_knowledge"]["nested_beliefs"] == {"enabled": True, "depth": 2}
    assert effective["character_knowledge"]["testimony"] == "model"
    assert effective["perception"] == {"level": "full"}
    assert effective["multi_character"]["renders"] == "per_character"
    assert body["profile"]["version"] == 2


def test_story_cast_passes_the_fake() -> None:
    fake = FakeEngine()
    body = story_cast()
    fake.respond("GET", STORY_PATH, 200, json=body)
    assert client(fake).get(f"/stories/{STORY}").json() == body
    cast = [(c["id"], c["name"], c["is_player"]) for c in body["characters"]]
    assert cast == [(ALICE, "Alice", False), (BOB, "Bob", False), (PAT, "Pat", True)]
    assert all("participation" not in c for c in body["characters"])


def test_with_settings_copies_and_sets_dotted_paths() -> None:
    source = structured_v2()
    before = json.loads(json.dumps(source))
    changed = with_settings(
        source,
        {
            "perception.level": "co_location",
            "brand.new.switch": True,
            "character_knowledge.nested_beliefs": None,
            "pipeline.level": "x",  # a non-object step is replaced by {}
        },
    )
    assert source == before  # the source is not mutated
    effective: dict[str, Any] = changed["effective"]
    assert effective["perception"]["level"] == "co_location"
    assert effective["brand"] == {"new": {"switch": True}}
    assert "nested_beliefs" not in effective["character_knowledge"]
    assert effective["pipeline"] == {"level": "x"}
    assert changed["capabilities"] == source["capabilities"]
    assert changed["fingerprint"] == source["fingerprint"]


def beliefs_page(after: str | None) -> dict[str, Any]:
    page: dict[str, Any] = json.loads(
        (FIXTURES.parent / "engine" / "m5" / "beliefs_page1.json").read_text()
    )
    page["next_after"] = after
    return page


def test_fake_matches_canned_answers_by_query() -> None:
    fake = FakeEngine()
    first, second, default = beliefs_page("C2"), beliefs_page(None), beliefs_page("D")
    fake.respond("GET", BELIEFS, 200, json=default)
    fake.respond("GET", BELIEFS, 200, json=first, query={"character": ALICE})
    fake.respond("GET", BELIEFS, 200, json=second, query={"character": ALICE, "after": "C2"})
    http = client(fake)
    path = f"/stories/{STORY}/inspect/beliefs"
    # Page 2 asked first: the answer follows the query, not the call order.
    assert http.get(path, params={"character": ALICE, "after": "C2"}).json() == second
    assert http.get(path, params={"character": ALICE}).json() == first
    assert http.get(path, params={"after": "C2", "character": ALICE, "x": "1"}).json() == second
    # Complement: an unmatched query gets the query-less answer.
    assert http.get(path, params={"character": BOB}).json() == default
    assert http.get(path).json() == default
    assert len(fake.requests) == 5


def test_fake_query_answers_replace_and_survive_a_plain_answer() -> None:
    fake = FakeEngine()
    fake.respond("GET", BELIEFS, 200, json=beliefs_page("old"), query={"after": "A"})
    fake.respond("GET", BELIEFS, 200, json=beliefs_page("new"), query={"after": "A"})
    http = client(fake)
    path = f"/stories/{STORY}/inspect/beliefs"
    assert http.get(path, params={"after": "A"}).json()["next_after"] == "new"
    with pytest.raises(UnconfiguredEngineCall, match="after=B"):
        http.get(path, params={"after": "B"})
    fake.respond("GET", BELIEFS, 200, json=beliefs_page("plain"))
    assert http.get(path, params={"after": "A"}).json()["next_after"] == "new"
    assert http.get(path, params={"after": "B"}).json()["next_after"] == "plain"


def test_fake_query_matches_any_value_of_a_repeated_parameter_and_validates() -> None:
    fake = FakeEngine()
    fake.respond("GET", BELIEFS, 200, json=beliefs_page(None), query={"character": BOB})
    http = client(fake)
    path = f"/stories/{STORY}/inspect/beliefs"
    answered = http.get(path, params=[("character", ALICE), ("character", BOB)])
    assert answered.status_code == 200
    with pytest.raises(Exception, match="required"):
        fake.respond("GET", BELIEFS, 200, json={"nope": 1}, query={"character": ALICE})

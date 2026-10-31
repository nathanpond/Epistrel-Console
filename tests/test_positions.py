"""#187 convention 15 (C1): five-part positions through ``console/positions.py``."""

from dataclasses import dataclass

import pytest

from console.engine.models import AuthorPosition, NarrativePosition
from console.inspect import history_turn_url
from console.positions import (
    POSITION_MAX,
    knowledge_position_text,
    knowledge_position_url,
    position_step,
    valid_minutes_text,
)

STORY = "01HZY000000000000000000STY"


@dataclass(frozen=True)
class Pos:
    turn: int = 12
    variant: int = 0
    slot_k: int = 0
    beat: int = 3
    step: int = 40000


@pytest.mark.parametrize(
    ("position", "text"),
    [
        (Pos(), "turn 12 · beat 3"),
        (Pos(variant=2), "turn 12 · v2 · beat 3"),
        (Pos(slot_k=1), "turn 12⁺¹ · beat 3"),
        (Pos(slot_k=12), "turn 12⁺¹² · beat 3"),
        (Pos(beat=0), "turn 12 · start"),
        (Pos(slot_k=1, beat=POSITION_MAX), "turn 12⁺¹ · after the turn"),
        (Pos(variant=2, slot_k=3, beat=0), "turn 12⁺³ · v2 · start"),
    ],
)
def test_knowledge_position_text(position: Pos, text: str) -> None:
    assert knowledge_position_text(position) == text


def test_position_max_is_the_engines() -> None:
    assert POSITION_MAX == 32767


def test_step_is_never_in_the_text_and_reads_in_more() -> None:
    position = Pos(step=40000)
    assert "40000" not in knowledge_position_text(position)
    assert position_step(position) == ("Step", "40000")


def test_url_carries_the_variant_only_when_not_zero() -> None:
    assert knowledge_position_url(STORY, Pos()) == history_turn_url(STORY, 12)
    assert knowledge_position_url(STORY, Pos()).endswith("#turn-12-0")
    assert knowledge_position_url(STORY, Pos(variant=2)) == history_turn_url(STORY, 12, 2)


def test_a_slot_position_links_its_turn_with_its_variant() -> None:
    url = knowledge_position_url(STORY, Pos(slot_k=1, variant=2))
    assert url == history_turn_url(STORY, 12, 2)
    assert "before_turn=13" in url
    assert url.endswith("#turn-12-2")


@pytest.mark.parametrize(
    ("valid_from", "valid_to", "text"),
    [(540, None, "[540, …) min"), (None, None, "[…, …) min"), (0, 60, "[0, 60) min")],
)
def test_valid_minutes_text(valid_from: int | None, valid_to: int | None, text: str) -> None:
    assert valid_minutes_text(valid_from, valid_to) == text


def test_a_none_position_is_none() -> None:
    assert knowledge_position_text(None) is None
    assert knowledge_position_url(STORY, None) is None
    assert position_step(None) is None


def test_the_engine_models_fit() -> None:
    narrative = NarrativePosition(turn=4, variant=1, slot_k=0, beat=2, step=7)
    author = AuthorPosition(turn=4, variant=0, slot_k=0, beat=POSITION_MAX, step=0)
    assert knowledge_position_text(narrative) == "turn 4 · v1 · beat 2"
    assert knowledge_position_text(author) == "turn 4 · after the turn"
    assert position_step(narrative) == ("Step", "7")

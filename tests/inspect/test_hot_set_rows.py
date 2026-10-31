"""``console.hot_set`` (#140), table-driven: the Counts and Pins rows' union, order and zero-fill,
headroom and markers, and the episode and budget formatting."""

from typing import Any

import pytest

from console.cast import CastMember
from console.engine.models import HotSetBody
from console.hot_set import (
    AT_CEILING,
    FRACTION_UNAVAILABLE,
    OVER_BUDGET,
    OVER_CEILING,
    budget_rows,
    character_budget,
    character_ids,
    episode_rows,
    hot_set_rows,
    minor_share_row,
)

A, B, C, Z = "A" * 26, "B" * 26, "C" * 26, "Z" * 26
CAST = [CastMember(B, "Bea", False), CastMember(A, "Al", True)]

BUDGETS = {
    "memories_per_character": 10,
    "vectors_per_story": 100,
    "importance_floor": 0.1,
    "retirement_max_lag": 1,
    "retain_retired_vectors": True,
    "retain_dormant_vectors": False,
    "max_pinned_per_character": 2,
    "max_pinned_per_story": 5,
}


def body(
    counters: dict[str, int] | None = None,
    stored: dict[str, int] | None = None,
    by_character: dict[str, int] | None = None,
    *,
    story_vectors: int = 0,
    stored_vectors: int | None = 0,
    memory_pins: int | None = None,
    text_pins: int = 0,
    episodes: list[dict[str, Any]] | None = None,
    per_character: int | None = None,
) -> HotSetBody:
    pins = by_character or {}
    raw = {
        "story_id": "S" * 26,
        "episodic": True,
        "counters": {"characters": counters or {}, "story_vectors": story_vectors},
        "stored_counters": None
        if stored_vectors is None
        else {"characters": stored or {}, "story_vectors": stored_vectors},
        "budgets": BUDGETS
        if per_character is None
        else {**BUDGETS, "memories_per_character": per_character},
        "pins": {
            "memory_pins": sum(pins.values()) if memory_pins is None else memory_pins,
            "text_pins": text_pins,
            "by_character": pins,
        },
        "head_turn": 10,
        "open_episodes": episodes or [],
        "recent_retirements": [],
        "timeline_revision": 1,
    }
    parsed = HotSetBody.from_json(raw)
    assert parsed is not None
    return parsed


@pytest.mark.parametrize(
    ("cast", "maps", "expected"),
    [
        (CAST, [], [B, A]),  # cast order, not sorted
        (CAST, [{Z: 1, C: 1}], [B, A, C, Z]),  # then the others sorted
        (CAST, [{A: 1}, {C: 1}], [B, A, C]),  # union of every map
        (None, [{Z: 1}, {A: 1, B: 1}], [A, B, Z]),  # no cast: every id sorted
        ([], [], []),
    ],
)
def test_character_ids(cast: Any, maps: list[dict[str, int]], expected: list[str]) -> None:
    assert character_ids(cast, *maps) == expected


@pytest.mark.parametrize(
    ("hot", "headroom", "marker"),
    [
        (0, "10 left", None),
        (9, "1 left", None),
        (10, "0 left", None),  # at budget: not over
        (11, "over by 1", OVER_BUDGET),
        (1010, "over by 1,000", OVER_BUDGET),
    ],
)
def test_headroom_and_over_budget(hot: int, headroom: str, marker: str | None) -> None:
    row = hot_set_rows(body({A: hot}, {A: hot}), CAST).counts[1]
    assert (row.character_id, row.headroom, row.marker) == (A, headroom, marker)


@pytest.mark.parametrize(
    ("counters", "stored", "stored_vectors", "expected"),
    [
        ({A: 3}, {A: 3}, 0, {B: (0, 0, None), A: (3, 3, None)}),
        ({A: 3}, {A: 5}, 0, {B: (0, 0, None), A: (3, 5, "differs (+2)")}),
        ({A: 3}, {}, 0, {B: (0, 0, None), A: (3, 0, "differs (\u22123)")}),  # zero-fill
        ({}, {C: 1}, 0, {B: (0, 0, None), A: (0, 0, None), C: (0, 1, "differs (+1)")}),
        ({A: 3}, None, None, {B: (0, None, None), A: (3, None, None)}),  # not stored yet
    ],
)
def test_counts_zero_fill_and_differs(
    counters: dict[str, int],
    stored: dict[str, int] | None,
    stored_vectors: int | None,
    expected: dict[str, tuple[int, int | None, str | None]],
) -> None:
    rows = hot_set_rows(body(counters, stored, stored_vectors=stored_vectors), CAST)
    assert {r.character_id: (r.hot, r.stored, r.differs) for r in rows.counts} == expected


def test_counts_carry_labels_and_the_player() -> None:
    rows = hot_set_rows(body({Z: 1}), CAST).counts
    assert [(r.label, r.is_player) for r in rows] == [("Bea", False), ("Al", True), (None, False)]


def test_no_cast_leaves_every_label_out() -> None:
    rows = hot_set_rows(body({A: 1}, {B: 1}), None).counts
    assert [(r.character_id, r.label) for r in rows] == [(A, None), (B, None)]


def test_the_story_vectors_row() -> None:
    row = hot_set_rows(body(story_vectors=101, stored_vectors=99), CAST).story_vectors
    assert (row.label, row.headroom, row.marker, row.differs) == (
        "Story vectors",
        "over by 1",
        OVER_BUDGET,
        "differs (\u22122)",
    )


@pytest.mark.parametrize(
    ("held", "marker"), [(0, None), (1, None), (2, AT_CEILING), (3, OVER_CEILING)]
)
def test_pin_markers(held: int, marker: str | None) -> None:
    rows = hot_set_rows(body(by_character={A: held}), CAST).pins
    assert [(r.character_id, r.pins, r.marker) for r in rows] == [
        (B, 0, None),
        (A, held, marker),
    ]


@pytest.mark.parametrize(
    ("memory_pins", "text_pins", "total", "marker", "holderless"),
    [
        (2, 0, 2, None, 0),  # equal to the per-character sum: no story-level pins
        (3, 1, 4, None, 1),
        (3, 2, 5, AT_CEILING, 1),
        (4, 2, 6, OVER_CEILING, 2),
        (1, 0, 1, None, 0),  # below the sum (never sent): never negative
    ],
)
def test_story_pins(
    memory_pins: int, text_pins: int, total: int, marker: str | None, holderless: int
) -> None:
    pins = hot_set_rows(
        body(by_character={A: 2}, memory_pins=memory_pins, text_pins=text_pins), CAST
    ).story_pins
    assert (pins.total, pins.ceiling, pins.marker, pins.holderless) == (
        total,
        5,
        marker,
        holderless,
    )


def episode(scope: str, scope_id: str, deadline: int) -> dict[str, Any]:
    return {
        "id": "E" * 26,
        "scope": scope,
        "scope_id": scope_id,
        "opened_turn": 1,
        "deadline_turn": deadline,
        "excess_at_open": 2,
        "excess": 1,
    }


@pytest.mark.parametrize(
    ("deadline", "due", "turns"), [(8, True, 2), (10, True, 0), (11, False, 1), (15, False, 5)]
)
def test_episode_due_and_turns(deadline: int, due: bool, turns: int) -> None:
    (row,) = episode_rows(
        body(episodes=[episode("story", "S" * 26, deadline)]).open_episodes, 10, CAST
    )
    assert (row.due, row.turns, row.scope_label, row.is_story) == (due, turns, "Story", True)


def test_episode_scope_ids_and_labels() -> None:
    episodes = body(
        episodes=[episode("character", A, 11), episode("character", Z, 11), episode("odd", B, 11)]
    ).open_episodes
    rows = episode_rows(episodes, 10, CAST)
    assert [(r.scope_label, r.label, r.is_player) for r in rows] == [
        ("Character", "Al", True),
        ("Character", None, False),
        (None, None, False),  # an unknown scope names no character
    ]


def test_budget_rows_format_and_source() -> None:
    rows = budget_rows(body().budgets)
    assert [(r.field, r.value, r.title, r.source, r.setting) for r in rows] == [
        (
            "memories_per_character",
            "10",
            None,
            "story dial",
            "memory.hot_set.memories_per_character",
        ),
        ("vectors_per_story", "100", None, "story dial", "memory.hot_set.vectors_per_story"),
        ("importance_floor", "0.100", None, "story dial", "memory.hot_set.importance_floor"),
        ("retirement_max_lag", "1 turn", None, "story dial", "memory.hot_set.retirement_max_lag"),
        (
            "retain_retired_vectors",
            "yes",
            "true",
            "story dial",
            "memory.hot_set.retain_retired_vectors",
        ),
        (
            "retain_dormant_vectors",
            "no",
            "false",
            "story dial",
            "memory.hot_set.retain_dormant_vectors",
        ),
        ("max_pinned_per_character", "2", None, "deployment ceiling", None),
        ("max_pinned_per_story", "5", None, "deployment ceiling", None),
    ]


# --- minor characters' scaled budgets (#168) ---------------------------------------------------


@pytest.mark.parametrize(
    ("per_character", "fraction", "budget"),
    [(20, 0.2, 4), (25, 0.3, 7), (3, 0.2, 1), (100, 0.29, 28), (20, 1.0, 20)],
)
def test_character_budget_matches_engine_rounding(
    per_character: int, fraction: float, budget: int
) -> None:
    """Engine ``Budgets.character_budget``: the float product truncated, at least 1 (100 x 0.29
    is 28.999..., which ``round`` would make 29)."""
    assert character_budget(per_character, fraction, True) == budget
    assert character_budget(per_character, fraction, False) == per_character


def test_minor_row_is_judged_against_scaled_budget() -> None:
    rows = hot_set_rows(
        body({A: 5, B: 5}, per_character=20), CAST, minors=frozenset({B}), fraction=0.2
    )
    minor, major = rows.counts
    assert (minor.character_id, minor.minor, minor.budget) == (B, True, 4)
    assert (minor.headroom, minor.marker) == ("over by 1", OVER_BUDGET)
    assert minor.scaled_from == (20, 0.2)
    assert minor.fraction_text == "0.2"
    assert (major.character_id, major.minor, major.budget) == (A, False, 20)
    assert (major.headroom, major.marker, major.scaled_from) == ("15 left", None, None)


def test_minor_row_with_the_fraction_unknown_has_no_verdict() -> None:
    rows = hot_set_rows(body({B: 50}, per_character=20), CAST, minors=frozenset({B}))
    minor = rows.counts[0]
    assert (minor.minor, minor.budget, minor.marker) == (True, None, None)
    assert minor.headroom == FRACTION_UNAVAILABLE


def test_major_rows_and_story_vectors_are_unscaled() -> None:
    """Complement: majors, an id outside ``minors``, unknown weights and the Story vectors row
    keep the unscaled budget."""
    payload = body({A: 5, B: 5, Z: 5}, per_character=20, story_vectors=150)
    for minors in (None, frozenset(), frozenset({C})):
        rows = hot_set_rows(payload, CAST, minors=minors, fraction=0.2)
        assert [(r.budget, r.minor, r.scaled_from) for r in rows.counts] == [(20, False, None)] * 3
    rows = hot_set_rows(payload, CAST, minors=frozenset({A, B, Z}), fraction=0.2)
    vectors = rows.story_vectors
    assert (vectors.budget, vectors.minor, vectors.scaled_from) == (100, False, None)
    assert (vectors.headroom, vectors.marker) == ("over by 50", OVER_BUDGET)


def test_pins_are_not_scaled() -> None:
    """Complement: a minor's pins are held to the unscaled per-character ceiling."""
    rows = hot_set_rows(
        body({B: 1}, by_character={B: 2}, per_character=20),
        CAST,
        minors=frozenset({B}),
        fraction=0.2,
    )
    [pin] = [p for p in rows.pins if p.character_id == B]
    assert (pin.ceiling, pin.marker) == (2, AT_CEILING)
    assert rows.story_pins.ceiling == 5


def test_minor_share_row() -> None:
    payload = body(per_character=2000)
    row = minor_share_row(payload.budgets, 0.2)
    assert (row.field, row.label, row.value) == (
        "minor_hot_budget_fraction",
        "Minor characters' share",
        "0.2 \u2192 400 per minor",
    )
    assert (row.source, row.setting, row.url) == (
        "deployment setting",
        "minor_hot_budget_fraction",
        "/engine/settings/",
    )
    assert minor_share_row(payload.budgets, 1).value == "1.0 \u2192 2,000 per minor"
    unavailable = minor_share_row(payload.budgets, None)
    assert (unavailable.value, unavailable.title) == ("\u2014", None)

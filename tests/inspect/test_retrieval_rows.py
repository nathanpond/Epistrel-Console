"""The Retrieval tab's rows (#141, ``console/retrieval.py``) and its form
(``console/forms/inspect.py``), at the unit level: highlighting over code points, the display
filters, the legend and the form's query."""

from typing import Any

import pytest
from django import forms

from console.cast import CastMember
from console.engine.models import MentionBody, RetrievalBody, RetrievedMemoryBody
from console.forms.inspect import RetrievalForm, as_of_help
from console.retrieval import (
    HIDDEN_BY_FILTER,
    NOT_RETRIEVED,
    Filters,
    Names,
    Segment,
    candidates,
    decimal3,
    legend,
    memory_matches,
    mention_rows,
)
from tests.inspect.helpers import STORY

MARA = CastMember("01JB2Z8Y5Q3W6K9M1N4P7R0T3A", "Mara", True)
TOBIN = CastMember("01JB2Z8Y5Q3W6K9M1N4P7R0T3B", "Tobin", False)
CAST = [MARA, TOBIN]


def mention(start: int, end: int, entity: str | None = None) -> MentionBody:
    return MentionBody([start, end], "name", entity, None if entity else "unknown_name")


def test_spans_are_code_points_and_sorted_by_start() -> None:
    q = "😀 Mara met Tobin"
    segments, rows = mention_rows(
        q, [mention(11, 16, TOBIN.id), mention(2, 6, MARA.id)], Names(CAST)
    )
    assert segments == [
        Segment("😀 "),
        Segment("Mara", 2, "Name · Mara (player)"),
        Segment(" met "),
        Segment("Tobin", 1, "Name · Tobin"),
    ]
    assert [r.span_text for r in rows] == ["Tobin", "Mara"]  # the Engine's order


def test_an_overlap_with_an_earlier_start_loses_even_when_listed_first() -> None:
    _, rows = mention_rows("abcdefgh", [mention(2, 6), mention(0, 4)], Names(None))
    assert [r.highlighted for r in rows] == [False, True]
    assert rows[0].span_text == "cdef"


def test_a_whole_question_span_leaves_no_plain_segment() -> None:
    segments, _ = mention_rows("Mara", [mention(0, 4, MARA.id)], Names(None))
    assert segments == [Segment("Mara", 1, f"Name · {MARA.id}")]


@pytest.mark.parametrize(
    ("memory_id", "wanted", "matches"),
    [
        ("01JB2Z8Y5Q3W6K9M1N4P7R0M01", "01jb2z8y5q3w6k9m1n4p7r0m01", True),
        ("01JB2Z8Y5Q3W6K9M1N4P7R0M01", "4P7R0M01", True),
        ("01JB2Z8Y5Q3W6K9M1N4P7R0M01", "…4P7R0M01", True),
        ("01JB2Z8Y5Q3W6K9M1N4P7R0M01", "P7R0M01", False),  # under 8
        ("01JB2Z8Y5Q3W6K9M1N4P7R0M01", "", False),
        ("01JB2Z8Y5Q3W6K9M1N4P7R0M01", "4P7R0M02", False),
    ],
)
def test_memory_matches(memory_id: str, wanted: str, matches: bool) -> None:
    assert memory_matches(memory_id, wanted) is matches


def memory(memory_id: str, kind: str = "episodic", tier: str = "hot") -> RetrievedMemoryBody:
    return RetrievedMemoryBody(
        memory_id, kind, "text", 1, 0, None, None, [], tier, 0.5, ["vector"], 0.5, 0.5, 0.5, 0.5
    )


def body(**lists: list[RetrievedMemoryBody]) -> RetrievalBody:
    parsed = RetrievalBody.from_json(
        {
            "position": [1, 0],
            "resolution": {
                "mentions": [],
                "entities": [],
                "event_type": None,
                "window": None,
                "turns": None,
                "time_reason": None,
            },
            "episodes": [],
            "participants": [],
            "assessments": [],
            "memories": [],
            "profiles": [],
            "canon": [],
            "context": {},
            "rehydrated": [],
            "degradation": [],
            "skipped": [],
            "model_calls": 0,
            "embedding_model_version": None,
            "timings": {},
        }
    )
    assert parsed is not None
    return RetrievalBody(**{**parsed.__dict__, **lists})


def test_the_memory_notes() -> None:
    answer = body(memories=[memory("A" * 26, "semantic")], episodes=[memory("B" * 26)])
    found = candidates(STORY, answer, Names(None), Filters(memory="Z" * 26), {})
    assert found.memory_note == NOT_RETRIEVED
    found = candidates(STORY, answer, Names(None), Filters(kind="episodic", memory="A" * 26), {})
    assert found.memory_note == HIDDEN_BY_FILTER
    assert (found.shown, found.total) == (0, 2)
    found = candidates(STORY, answer, Names(None), Filters(memory="A" * 26), {})
    assert found.memory_note is None
    assert [len(t.rows) for t in found.tables] == [1, 0, 0]
    assert [t.hidden for t in found.tables] == [False, True, False]


def test_decimal3_and_the_legend() -> None:
    assert decimal3(0.81234) == "0.812"
    assert decimal3(1) == "1.000"
    assert decimal3(None) == "—"
    assert decimal3(True) == "—"
    shown = legend(
        {"memory": {"episodic": {"weight_similarity": 0.6, "recency_half_life_turns": 1200}}}
    )
    assert (shown.similarity, shown.recency, shown.importance) == ("0.600", "—", "—")
    assert shown.half_life == "1,200"
    assert legend({}).half_life == "—"


def form(data: dict[str, Any], cast: list[CastMember] | None = CAST) -> RetrievalForm:
    found = RetrievalForm(data, cast=cast, head=5)
    return found


def test_the_query_leaves_the_player_and_blanks_out() -> None:
    f = form({"q": "x", "actor": MARA.id})
    assert f.is_valid()
    assert f.query() == {
        "q": "x",
        "as_of": None,
        "actor_character_id": None,
        "include_quarantined": False,
    }
    f = form({"q": "x", "actor": TOBIN.id, "as_of": "2", "include_quarantined": "1"})
    assert f.is_valid()
    assert f.query() == {
        "q": "x",
        "as_of": 2,
        "actor_character_id": TOBIN.id,
        "include_quarantined": True,
    }


def test_an_unknown_cast_offers_the_default_and_the_requested_id() -> None:
    f = form({"q": "x", "actor": "SOMEONE"}, cast=None)
    actor = f.fields["actor"]
    assert isinstance(actor, forms.ChoiceField)
    assert actor.choices == [("", "Player (default)"), ("SOMEONE", "SOMEONE")]
    assert f.is_valid()
    assert f.query()["actor_character_id"] == "SOMEONE"
    f = form({"q": "x"}, cast=None)
    assert f.is_valid()
    assert f.query()["actor_character_id"] is None


def test_as_of_help() -> None:
    assert as_of_help(5) == "blank = head (turn 5)"
    assert as_of_help(-1) == "blank = head (no turns yet)"
    assert as_of_help(None) == "blank = head"

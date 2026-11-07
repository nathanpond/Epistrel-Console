"""E4's rewrite modes and answers forms (#294): ``console/forms/rewrites.py``, and
``console.rewrites.resolution_mode_text`` reading a stored mode back."""

from typing import Any

import pytest

from console.engine.client import EngineError
from console.engine.models import RewriteSession
from console.forms.rewrites import RESOLUTION_CHOICES, AnswersForm, RewriteModesForm
from console.rewrites import QuestionView, resolution_mode_text, session_view
from tests.fixtures import engine_fixture

STORY = "01JM7ST0000000000000000001"
Q1 = "01JM7QN0000000000000000001"
Q2 = "01JM7QN0000000000000000002"


def questions(*extra: dict[str, Any], answered_first: bool = False) -> tuple[QuestionView, ...]:
    body = engine_fixture("m7/rewrite_open")
    if answered_first:
        body["questions"][0].update(
            answer_option_id="o1",
            accepted_recommendation=True,
            answered_by="console:nate",
            answered_at="2026-11-05T10:01:00Z",
        )
    for question in extra:
        body["questions"].append({**body["questions"][0], **question})
    parsed = RewriteSession.from_json(body)
    assert parsed is not None
    return session_view(parsed, STORY, {}).questions


OPTIONAL = {
    "id": Q2,
    "required": False,
    "severity": "low",
    "accepted_recommendation": None,
    "answer_option_id": None,
    "answered_by": None,
    "answered_at": None,
}


def test_the_resolution_choices() -> None:
    assert RESOLUTION_CHOICES == (
        ("synchronous", "Ask me about every conflict"),
        ("best_effort", "Decide for me (least change)"),
        ("threshold_high", "Ask only about high-severity conflicts"),
        ("threshold_low", "Ask about high- and low-severity conflicts"),
    )


def test_modes_defaults() -> None:
    form = RewriteModesForm(data={"resolution": "synchronous"})
    assert form.is_valid()
    assert form.modes() == {"preview": "preview_first", "resolution_mode": "synchronous"}
    unbound = RewriteModesForm()
    assert unbound["resolution"].value() == "synchronous"
    assert unbound["apply_directly"].value() in (None, False)
    assert unbound.fields["apply_directly"].label == "Apply directly (skip the preview)"


@pytest.mark.parametrize(
    ("posted", "mode"),
    [
        ("best_effort", "best_effort"),
        ("threshold_high", {"threshold": "high"}),
        ("threshold_low", {"threshold": "low"}),
    ],
)
def test_modes_apply_directly_and_thresholds(posted: str, mode: Any) -> None:
    form = RewriteModesForm(data={"resolution": posted, "apply_directly": "on"})
    assert form.is_valid()
    assert form.modes() == {"preview": "apply_directly", "resolution_mode": mode}


def test_a_bogus_resolution_is_invalid() -> None:
    form = RewriteModesForm(data={"resolution": "bogus"})
    assert not form.is_valid()
    assert "resolution" in form.errors


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("synchronous", "Ask me about every conflict"),
        ("best_effort", "Decide for me (least change)"),
        ({"threshold": "high"}, "Ask only about high-severity conflicts"),
        ({"threshold": "low"}, "Ask about high- and low-severity conflicts"),
        ({"threshold": "mid"}, '{"threshold":"mid"}'),
        ("threshold_high", '"threshold_high"'),
        (None, "null"),
    ],
)
def test_resolution_mode_text(value: Any, text: str) -> None:
    assert resolution_mode_text(value) == text


def answered(form: AnswersForm) -> list[dict[str, Any]]:
    assert form.is_valid(), form.errors
    return form.answers()


def test_an_untouched_required_question_sends_the_recommended_option() -> None:
    form = AnswersForm(questions(), data={f"choice_{Q1}": "o1", f"text_{Q1}": ""})
    assert answered(form) == [{"question_id": Q1, "option_id": "o1"}]


def test_text_wins_over_the_radio() -> None:
    form = AnswersForm(questions(), data={f"choice_{Q1}": "o2", f"text_{Q1}": "He copied it"})
    assert answered(form) == [{"question_id": Q1, "text": "He copied it"}]
    assert form.text_question_ids() == [Q1]


def test_whitespace_text_is_blank() -> None:
    form = AnswersForm(questions(), data={f"choice_{Q1}": "o2", f"text_{Q1}": "   "})
    assert answered(form) == [{"question_id": Q1, "option_id": "o2"}]


def test_accept_all_sends_accept_recommended_for_every_unanswered_question() -> None:
    shown = questions(OPTIONAL, answered_first=True)
    form = AnswersForm(shown, data={"accept_all": "1"})
    assert answered(form) == [{"question_id": Q2, "accept_recommended": True}]
    both = AnswersForm(questions(OPTIONAL), data={"accept_all": "1"})
    assert answered(both) == [
        {"question_id": Q1, "accept_recommended": True},
        {"question_id": Q2, "accept_recommended": True},
    ]


def test_an_untouched_optional_question_is_omitted() -> None:
    data = {f"choice_{Q1}": "o1", f"choice_{Q2}": "o1", f"text_{Q2}": ""}
    form = AnswersForm(questions(OPTIONAL), data=data)
    assert answered(form) == [{"question_id": Q1, "option_id": "o1"}]
    changed = AnswersForm(questions(OPTIONAL), data={**data, f"choice_{Q2}": "o2"})
    assert answered(changed)[1] == {"question_id": Q2, "option_id": "o2"}


def test_overlong_text_is_a_field_error() -> None:
    form = AnswersForm(questions(), data={f"choice_{Q1}": "o1", f"text_{Q1}": "x" * 2001})
    assert not form.is_valid()
    assert f"text_{Q1}" in form.errors
    fits = AnswersForm(questions(), data={f"choice_{Q1}": "o1", f"text_{Q1}": "x" * 2000})
    assert fits.is_valid()


def test_a_required_question_with_no_recommendation_needs_an_answer() -> None:
    shown = questions(
        {**OPTIONAL, "required": True, "recommended_option_id": "", "recommendation_reason": ""},
        answered_first=True,
    )
    assert shown[1].recommended_id is None
    form = AnswersForm(shown, data={"accept_all": "1"})
    assert not form.is_valid()
    assert f"choice_{Q2}" in form.errors
    texted = AnswersForm(shown, data={"accept_all": "1", f"text_{Q2}": "Keep it"})
    assert answered(texted) == [{"question_id": Q2, "text": "Keep it"}]


def test_an_answered_question_has_no_fields() -> None:
    form = AnswersForm(questions(OPTIONAL, answered_first=True))
    assert set(form.fields) == {f"choice_{Q2}", f"text_{Q2}"}
    rows = form.question_rows
    assert [(r.question.id, r.choice is None) for r in rows] == [(Q1, True), (Q2, False)]
    assert rows[1].selected == "o1"  # unbound: the recommended initial


def test_apply_engine_error_places_invalid_answer() -> None:
    form = AnswersForm(questions(), data={f"choice_{Q1}": "o1"})
    assert form.is_valid()
    named = EngineError("invalid_answer", "No such option", {"question_id": Q1}, 422)
    assert form.apply_engine_error(named) is True
    assert form.errors[f"choice_{Q1}"] == ["No such option"]
    above = AnswersForm(questions(), data={f"choice_{Q1}": "o1"})
    assert above.is_valid()
    assert above.apply_engine_error(EngineError("invalid_answer", "Bad answer", {}, 422))
    assert above.non_field_errors() == ["Bad answer"]
    other = AnswersForm(questions(), data={f"choice_{Q1}": "o1"})
    assert other.is_valid()
    assert other.apply_engine_error(EngineError("rewrite_noop", "x", {}, 422)) is False

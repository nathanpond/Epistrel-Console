"""#187 conventions 16 to 18 and 24: ``console/wording.py`` (importing it is the cycle check)."""

from dataclasses import dataclass
from typing import Any

import pytest
from django.template import Context, Template

from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import OmniscientStoryCharacter
from console.story_pause import check_url, paused_refusal
from console.templatetags.console_format import humanise
from console.timeline import reload_message
from console.views.play import error_inspector
from console.wording import (
    ERASED_TEXT,
    UNKNOWN_CHARACTER,
    WriteRefusal,
    archived_refusal,
    busy_notice,
    character_label,
    humanised,
    minor_ids,
    write_refusal,
)

STORY = "01HZY000000000000000000STY"
OTHER = "01HZY000000000000000000TH0"
JOB = "01HZY000000000000000000J0B"
ANA = "01HZY00000000000000000ANA0"
BEN = "01HZY00000000000000000BEN0"
VERB = "change a character's role"


def _error(code: str, details: dict[str, Any] | None = None) -> EngineError:
    return EngineError(code, "refused", details, 409)


# --- humanised -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [("unresolved_entity", "Unresolved entity"), ("testimony", "Testimony"), ("", "")],
)
def test_humanised(value: str, text: str) -> None:
    assert humanised(value) == text


def test_the_old_import_still_works() -> None:
    from console.retrieval import humanised as moved
    from console.views.story_settings import ERASED_TEXT as erased

    assert moved is humanised
    assert erased == ERASED_TEXT == "This text refers to an erased subject — nothing was saved"


def test_the_humanise_filter_is_unchanged() -> None:
    assert humanise("very_cautious") == "very cautious"
    assert humanise(None) == "—"
    assert humanise("") == "—"


@pytest.mark.parametrize(
    ("value", "text"),
    [("very_cautious", "Very cautious"), (None, "—"), ("", "—"), (3, "3"), ("<b>", "&lt;b&gt;")],
)
def test_the_humanised_filter(value: Any, text: str) -> None:
    rendered = Template("{% load console_format %}{{ value|humanised }}").render(
        Context({"value": value})
    )
    assert rendered == text


# --- character labels -----------------------------------------------------------------------------


def test_character_label() -> None:
    names = {ANA: "Ana (player)", BEN: "Ben"}
    assert character_label(BEN, names) == "Ben"
    assert character_label(OTHER, names) == "…00000TH0"
    assert character_label(ANA, names, frozenset({ANA})) == "Ana (player) (minor)"
    assert character_label(OTHER, {}, frozenset({OTHER})) == "…00000TH0 (minor)"
    assert character_label(BEN, {BEN: ""}) == "…0000BEN0"
    assert character_label("", names) == "—"
    assert character_label(None, names) == "—"


@dataclass(frozen=True)
class Member:
    id: str
    weight: str


def test_minor_ids_skips_a_missing_or_unknown_weight() -> None:
    cast = [Member(ANA, "minor"), Member(BEN, "major"), Member(OTHER, "background")]
    assert minor_ids(cast) == frozenset({ANA})
    assert minor_ids(None) == frozenset()
    character = OmniscientStoryCharacter(
        id=BEN, name="Ben", is_player=False, true_age=30, promoted_to=[]
    )  # no weight sent: the model's default ``major``
    assert minor_ids([character]) == frozenset()


def test_unknown_character_wording() -> None:
    assert UNKNOWN_CHARACTER == "That character isn't in this story's cast"


# --- write refusals -------------------------------------------------------------------------------


def test_story_archived() -> None:
    assert archived_refusal(VERB) == (
        "This story is archived — unarchive it to change a character's role."
    )
    assert write_refusal(_error("story_archived"), STORY, VERB) == WriteRefusal(
        archived_refusal(VERB), ""
    )


def test_story_paused_uses_the_given_story_id() -> None:
    exc = _error(
        "story_paused", {"story_id": OTHER, "job_id": JOB, "progress": {"done": 2, "total": 5}}
    )
    refusal = write_refusal(exc, STORY, VERB)
    paused = paused_refusal(exc, STORY)
    assert refusal == WriteRefusal(paused.text, paused.url)
    assert paused.text == "The story is paused for a check (2/5)"
    assert refusal.url == check_url(STORY, JOB)


@pytest.mark.parametrize(
    ("details", "text"),
    [
        ({"reason": "in_flight"}, "Story busy — another turn is running"),
        ({"reason": "queue_full", "queue_depth": 3}, "Story busy — queue full (3 waiting)"),
        ({"reason": "queue_full"}, "Story busy — queue full (— waiting)"),
        ({}, "Story busy"),
    ],
)
def test_story_busy(details: dict[str, Any], text: str) -> None:
    exc = _error("story_busy", details)
    assert busy_notice(details) == text
    assert write_refusal(exc, STORY, VERB) == WriteRefusal(text, "")
    assert error_inspector(exc, "key").notice == busy_notice(exc.details)


def test_revision_conflict() -> None:
    exc = _error("revision_conflict", {"current_revision": 7})
    assert write_refusal(exc, STORY, VERB) == WriteRefusal(reload_message(7), "")


def test_erased_subject_referenced() -> None:
    exc = _error("erased_subject_referenced", {"erasure_id": JOB})
    assert write_refusal(exc, STORY, VERB) == WriteRefusal(ERASED_TEXT, "")


@pytest.mark.parametrize(
    "exc",
    [
        _error("idempotency_conflict"),
        _error("something_new"),
        EngineUnreachable("transport error"),
    ],
)
def test_other_errors_fall_through_to_the_panel(exc: EngineClientError) -> None:
    assert write_refusal(exc, STORY, VERB) is None

"""#53: refusals. A stale page (409 ``revision_conflict``) shows only the reload line and
changes nothing; ``nothing_to_undo`` points at Variants and Fork; ``nothing_to_regenerate`` and
regenerate's ``erased_subject_referenced`` show their messages; a 423 shows the busy message
(fork included); ``unknown_chain`` lists its candidates."""

from typing import Any

import pytest
from django.test import Client

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.timeline.helpers import (
    HEAD,
    PLAY,
    REV,
    STORY,
    error_json,
    flash,
    path,
    posted,
    regenerate_posted,
    url,
)

pytestmark = pytest.mark.django_db

RELOAD = f"The story changed since you loaded this page (now revision {REV + 1}) — reload"
OPERATIONS: list[tuple[str, str, list[Any], dict[str, str]]] = [
    ("regenerate", "regenerate", [], {}),
    ("truncate", "rewind", [1], {}),
    ("untruncate", "undo-rewind", [], {}),
    ("swap", "swap", [], {"chain": "3:2"}),
    ("fork", "fork", [2], {}),
]


def _data(route: str, extra: dict[str, str]) -> dict[str, str]:
    if route == "regenerate":
        return regenerate_posted()
    if route == "fork":
        return posted(new_name="Harbour (fork at turn 2)")
    return posted(**extra)


@pytest.mark.parametrize(("action", "route", "args", "extra"), OPERATIONS)
def test_stale_page_shows_only_the_reload_message(
    operator_client: Client,
    engine: FakeEngine,
    action: str,
    route: str,
    args: list[Any],
    extra: dict[str, str],
) -> None:
    conflict = error_json(
        "revision_conflict",
        f"expected timeline revision {REV}, the story is at {REV + 1}",
        current_revision=REV + 1,
        attempts=0,
        reason="expected_revision",
    )
    engine.respond("POST", path(action), 409, json=conflict)
    response = operator_client.post(url(route, *args, **extra), _data(route, extra))
    assert response.status_code == 200
    page = response.content.decode()
    assert RELOAD in page
    assert f'class="button reload" href="{PLAY}"' in page
    assert "Engine error" not in page  # only the reload line
    assert flash(response) == []  # no success flash: nothing happened
    # Exactly one call: the refused operation, recorded with its error (#44).
    assert len(engine.requests) == 1
    row = EngineOperation.objects.get()
    assert (row.http_status, row.error_code) == (409, "revision_conflict")


def test_nothing_to_undo_offers_variants_and_fork(
    operator_client: Client, engine: FakeEngine
) -> None:
    refused = error_json(
        "nothing_to_undo",
        "there is no truncation to undo",
        story_id=STORY,
        options=["swap", "fork"],
        dormant_chains=[{"turn": 5, "variant": 1}],
    )
    engine.respond("POST", path("untruncate"), 409, json=refused)
    page = operator_client.post(url("undo-rewind"), posted()).content.decode()
    assert "Nothing to undo — there is no rewind to reverse." in page
    assert "New turns were added since the rewind — swap to the old chain instead" in page
    assert f'href="/stories/{STORY}/history/#variants">Variants</a>' in page
    assert f'href="/stories/{STORY}/fork/{HEAD}/?' in page


def test_nothing_to_undo_without_options_has_no_links(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST", path("untruncate"), 409, json=error_json("nothing_to_undo", story_id=STORY)
    )
    page = operator_client.post(url("undo-rewind"), posted()).content.decode()
    assert "Nothing to undo — there is no rewind to reverse." in page
    assert "swap to the old chain instead" not in page
    assert "#variants" not in page


def test_nothing_to_regenerate_shows_its_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    refused = error_json(
        "nothing_to_regenerate",
        "the latest turn has no reply to regenerate",
        story_id=STORY,
        turn=5,
    )
    engine.respond("POST", path("regenerate"), 422, json=refused)
    page = operator_client.post(url("regenerate"), regenerate_posted()).content.decode()
    assert "Nothing to regenerate — the latest turn has no reply." in page
    assert "nothing_to_regenerate" in page  # the Engine's code and message, as #48 shows them


def test_regenerate_erased_subject_shows_48s_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    refused = error_json(
        "erased_subject_referenced", "refers to an erased subject", erasure_id="E1", match_count=2
    )
    engine.respond("POST", path("regenerate"), 409, json=refused)
    page = operator_client.post(url("regenerate"), regenerate_posted()).content.decode()
    assert "This turn refers to an erased subject — nothing was written" in page


def test_regenerate_validation_error_stays_on_the_confirm_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    """A stale head: the Engine's 422 shows on the confirm page, the inputs kept."""
    refused = error_json(
        "validation_error",
        "only the latest turn (6) can be regenerated",
        field="body.turn",
        errors=[{"field": "body.turn", "message": "only the latest turn (6) can be regenerated"}],
    )
    engine.respond("POST", path("regenerate"), 422, json=refused)
    page = operator_client.post(
        url("regenerate"), regenerate_posted(temperature="0.5")
    ).content.decode()
    assert "only the latest turn (6) can be regenerated" in page
    assert 'value="0.5"' in page
    assert "Replace the latest reply?" in page


@pytest.mark.parametrize(("action", "route", "args", "extra"), OPERATIONS)
def test_busy_shows_the_busy_message(
    operator_client: Client,
    engine: FakeEngine,
    action: str,
    route: str,
    args: list[Any],
    extra: dict[str, str],
) -> None:
    busy = error_json("story_busy", "a turn is running", reason="in_flight", operation_id="01OP")
    engine.respond("POST", path(action), 423, json=busy, headers={"Retry-After": "2"})
    page = operator_client.post(url(route, *args, **extra), _data(route, extra)).content.decode()
    assert "Story busy — another turn is running" in page
    assert "Nothing was changed." in page


def test_unknown_chain_lists_its_candidates(operator_client: Client, engine: FakeEngine) -> None:
    refused = error_json(
        "unknown_chain",
        "turn 3 continues in more than one way",
        variant_chain={"turn": 3, "variant": 1},
        candidates=[{"turn": 4, "variant": 1}, {"turn": 4, "variant": 2}],
    )
    engine.respond("POST", path("swap"), 409, json=refused)
    page = operator_client.post(url("swap", chain="3:1"), posted(chain="3:1")).content.decode()
    assert "choose one" in page
    for variant in (1, 2):
        assert f"&amp;chain=4%3A{variant}" in page
        assert f"Swap to turn 4 · variant {variant}" in page
    assert "unknown_chain" in page


def test_archived_refusal_shows_the_engines_answer(
    operator_client: Client, engine: FakeEngine
) -> None:
    refused = error_json("story_archived", "story is archived; unarchive it to change it")
    engine.respond("POST", path("truncate"), 409, json=refused)
    page = operator_client.post(url("rewind", 1), posted()).content.decode()
    assert "story_archived" in page

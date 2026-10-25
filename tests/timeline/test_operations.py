"""#53: each timeline operation from the UI — its confirm GET makes no Engine call, its POST
sends the exact body with ``X-Epistrel-Expected-Revision``, success redirects to the play view
with the new revision in the flash, and the operation is recorded (#44)."""

import pytest
from django.test import Client

from console.engine.models import Story
from console.models import EngineOperation
from console.views.play import PlayStory
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.history.helpers import answer
from tests.play.helpers import snapshot
from tests.timeline.helpers import (
    HEAD,
    KEY,
    MARA,
    NAME,
    PLAY,
    REV,
    REVISION_HEADER,
    STORY,
    TOBIN,
    answer_play,
    body_of,
    calls,
    fixture,
    flash,
    html,
    logged,
    path,
    posted,
    regenerate_posted,
    timeline_story,
    url,
)

pytestmark = pytest.mark.django_db


# --- Confirm pages make no Engine call ------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "args", "extra", "text"),
    [
        ("rewind", [3], {}, "Rewind"),
        ("undo-rewind", [], {}, "Undo the last rewind"),
        ("swap", [], {"chain": "3:2"}, "Make chain turn 3 · variant 2 the active timeline"),
    ],
)
def test_confirm_get_makes_no_engine_call(
    operator_client: Client,
    engine: FakeEngine,
    route: str,
    args: list[int],
    extra: dict[str, str],
    text: str,
) -> None:
    page = html(operator_client, url(route, *args, **extra))
    assert text in page
    assert f'name="rev" value="{REV}"' in page
    assert 'name="idempotency_key"' in page
    assert engine.requests == []


def test_regenerate_confirm_get_makes_no_engine_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = html(operator_client, url("regenerate", snapshot=snapshot()))
    assert (
        "Replace the latest reply? The current one is kept as a dormant variant you can swap "
        "back to."
    ) in page
    assert "<details" in page  # the optional parameters, collapsed
    assert "Keep recorded" in page
    assert "Mara" in page  # the cast came from the signed snapshot
    assert engine.requests == []


def test_rewind_confirm_names_the_turn_and_counts_later_turns(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = html(operator_client, url("rewind", 3))
    assert "to turn 3?" in page
    assert f"{HEAD - 3} later turns become inactive." in page
    start = html(operator_client, url("rewind", 0))
    assert "to the start (turn 0)?" in start
    assert f"{HEAD} later turns become inactive." in start
    one = html(operator_client, url("rewind", HEAD - 1))
    assert "1 later turn becomes inactive." in one
    assert engine.requests == []


# --- Each POST: exact body, expected revision, PRG flash, recorded -----------------------------


def test_regenerate_sends_the_head_turn_and_expected_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", path("regenerate"), 200, json=fixture("regenerate"))
    response = operator_client.post(url("regenerate"), regenerate_posted())
    (request,) = calls(engine, "POST", f"/stories/{STORY}/regenerate")
    assert body_of(request) == {"turn": HEAD}  # blank parameters keep the recorded ones
    assert request.headers[REVISION_HEADER] == str(REV)
    assert request.headers["Idempotency-Key"] == KEY
    assert "Prefer" not in request.headers  # the Engine never queues a regenerate
    assert response.status_code == 302
    assert response["Location"].startswith(f"{PLAY}?turn=5&viewpoint=")
    assert response["Location"].endswith("#turn-5")
    (line,) = flash(response)
    assert "Regenerated turn 5: variant 2 is active, previous variant 1" in line
    assert "timeline revision 7" in line
    row = logged("regenerate")
    assert row.request["headers"][REVISION_HEADER] == str(REV)
    assert row.timeline_revision == 7


def test_regenerate_sends_only_the_overrides_given(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", path("regenerate"), 200, json=fixture("regenerate"))
    data = regenerate_posted(
        length="short", temperature="0.4", output_viewpoint=f"character:{TOBIN}"
    )
    data["render_character_ids"] = MARA
    operator_client.post(url("regenerate"), data)
    (request,) = calls(engine, "POST", f"/stories/{STORY}/regenerate")
    assert body_of(request) == {
        "turn": HEAD,
        "prompt_params": {"length": "short", "temperature": 0.4},
        "output_viewpoint": f"character:{TOBIN}",
        "render_character_ids": [MARA],
    }


def test_regenerate_refuses_bad_parameters_without_calling(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.post(url("regenerate"), regenerate_posted(temperature="3"))
    assert response.status_code == 200
    assert "less than or equal to 2" in response.content.decode()
    assert engine.requests == []


def test_rewind_sends_to_turn_and_expected_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", path("truncate"), 200, json=fixture("truncate"))
    response = operator_client.post(url("rewind", 1), posted())
    (request,) = calls(engine, "POST", f"/stories/{STORY}/truncate")
    assert body_of(request) == {"to_turn": 1}
    assert request.headers[REVISION_HEADER] == str(REV)
    assert response.status_code == 302
    assert response["Location"] == PLAY
    (line,) = flash(response)
    assert "Rewound to turn 1: next turn index 2, 0 discarded operations" in line
    assert "timeline revision 9" in line
    assert "operation 01JA0000000000000000000OP2" in line
    assert logged("truncate").request["headers"][REVISION_HEADER] == str(REV)


def test_undo_rewind_sends_an_empty_body(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("untruncate"), 200, json=fixture("untruncate"))
    response = operator_client.post(url("undo-rewind"), posted())
    (request,) = calls(engine, "POST", f"/stories/{STORY}/untruncate")
    assert request.content == b""
    assert request.headers[REVISION_HEADER] == str(REV)
    (line,) = flash(response)
    assert "Rewind undone: restored head turn 5, 2 restored operations" in line
    assert "timeline revision 9" in line
    assert logged("untruncate").timeline_revision == 9


def test_undo_rewind_after_new_turns_is_a_swap(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("untruncate"), 200, json=fixture("untruncate_swapped"))
    (line,) = flash(operator_client.post(url("undo-rewind"), posted()))
    assert "new turns had been added since the rewind" in line
    assert "chain turn 4 · variant 1 is active again" in line


def test_swap_sends_the_chain_and_expected_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", path("swap"), 200, json=fixture("swap"))
    response = operator_client.post(url("swap", chain="4:2"), posted(chain="4:2"))
    (request,) = calls(engine, "POST", f"/stories/{STORY}/swap")
    assert body_of(request) == {"to_variant_chain": {"turn": 4, "variant": 2}}
    assert request.headers[REVISION_HEADER] == str(REV)
    (line,) = flash(response)
    assert "Swapped: chain turn 4 · variant 2 is active (1 operations activated, 2 dormant)" in line
    assert "timeline revision 13" in line
    assert logged("swap").request["body"] == {"to_variant_chain": {"turn": 4, "variant": 2}}


def test_swap_to_the_active_chain_says_already_active(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", path("swap"), 200, json=fixture("swap_active"))
    response = operator_client.post(url("swap", chain="4:2"), posted(chain="4:2", rev="13"))
    (line,) = flash(response)
    assert line.startswith("Already active — chain turn 4 · variant 2")


def test_swap_answer_on_another_revision_is_not_already_active(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: the same chain active but the revision moved (another chain was active)."""
    engine.respond("POST", path("swap"), 200, json=fixture("swap_active"))
    (line,) = flash(operator_client.post(url("swap", chain="4:2"), posted(chain="4:2")))
    assert line.startswith("Swapped")


@pytest.mark.parametrize(
    ("route", "args", "extra"),
    [("rewind", [1], {}), ("undo-rewind", [], {}), ("swap", [], {"chain": "4:2"})],
)
@pytest.mark.parametrize("rev", ["", "six"])
def test_missing_revision_is_a_form_error_with_no_call(
    operator_client: Client,
    engine: FakeEngine,
    route: str,
    args: list[int],
    extra: dict[str, str],
    rev: str,
) -> None:
    response = operator_client.post(url(route, *args, **extra), posted(rev=rev, **extra))
    assert response.status_code == 200
    assert "This page carries no timeline revision" in response.content.decode()
    assert engine.requests == []


def test_a_bad_chain_is_a_form_error_with_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.post(url("swap", chain="x"), posted(chain="0:1"))
    assert "A chain is turn:variant" in response.content.decode()
    assert engine.requests == []


# --- Where the play view and the history page offer them ----------------------------------------


def test_play_view_offers_each_operation_where_it_applies(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_play(engine)
    page = html(operator_client, PLAY)
    query = f"rev=4&amp;head=3&amp;name={NAME}&amp;next=%2Fstories%2F{STORY}%2F"
    for turn in (0, 1, 2):
        assert f'href="/stories/{STORY}/rewind/{turn}/?{query}"' in page
    assert f"/stories/{STORY}/rewind/3/" not in page  # not on the head turn
    for turn in (0, 1, 2, 3):
        assert f'href="/stories/{STORY}/fork/{turn}/?{query}"' in page
    assert ">Rewind to start<" in page
    assert f'href="/stories/{STORY}/swap/?{query}&amp;chain=3%3A2"' in page
    assert "chain=3%3A1" not in page  # never to the active chain
    assert page.count(f"/stories/{STORY}/regenerate/?") == 1  # the head turn's reply
    assert f'href="/stories/{STORY}/undo-rewind/?{query}"' in page
    assert ">1 dormant chain</a>" in page
    assert f"/stories/{STORY}/history/#variants" in page
    assert f'href="/stories/{STORY}/delete/?' in page  # #45's confirm page


def test_play_view_hides_undo_without_undo_available(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_play(engine, story=timeline_story(undo_available=False, dormant_chains=[]))
    page = html(operator_client, PLAY)
    assert "undo-rewind" not in page
    assert "dormant chain" not in page


def test_regenerate_needs_a_reply_on_the_head_turn(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: a head turn without a character reply offers no Regenerate."""
    answer_play(engine)
    history = engine_fixture("get_history_audit")
    history["items"] = [
        i for i in history["items"] if not (i["turn"] == 3 and i["channel"] == "message")
    ]
    engine.respond("GET", "/stories/{story_id}/history", 200, json=history)
    assert "/regenerate/" not in html(operator_client, PLAY)


def test_archived_story_disables_all_but_fork(operator_client: Client, engine: FakeEngine) -> None:
    answer_play(engine, story=timeline_story(status="archived", archived_at="2026-10-25T00:00:00Z"))
    page = html(operator_client, PLAY)
    assert "Archived — unarchive to change the timeline" in page
    assert "/rewind/" not in page
    assert "/swap/" not in page
    assert "/regenerate/" not in page
    assert "/undo-rewind/" not in page
    assert '<button type="button" class="rewind" disabled' in page
    assert f'href="/stories/{STORY}/fork/1/?' in page  # Fork stays enabled


def test_turn_parameter_selects_that_turn_in_the_inspector(
    operator_client: Client, engine: FakeEngine
) -> None:
    history = engine_fixture("get_history_audit")
    op = next(i["operation_id"] for i in history["items"] if i["turn"] == 2)
    EngineOperation.objects.create(
        kind="regenerate",
        http_method="POST",
        path=f"/stories/{STORY}/regenerate",
        story_id=STORY,
        engine_operation_id=op,
        http_status=200,
        outcome=EngineOperation.Outcome.OK,
        request={"body": {"turn": 2}, "headers": {}},
        response={**fixture("regenerate"), "turn": 2},
        principal="console:nate",
        elapsed_ms=5,
    )
    answer_play(engine)
    page = html(operator_client, f"{PLAY}?turn=2")
    assert "Previous variant" in page
    assert f"<code>{op}</code>" in page


def test_history_variants_offer_swap_and_fork(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    variants = engine_fixture("list_variants")
    page = html(operator_client, f"/stories/{STORY}/history/")
    for chain in variants["items"]:
        key = f"{chain['turn']}%3A{chain['variant']}"
        fork = f"/stories/{STORY}/fork/{chain['tip_turn']}/?"
        assert fork in page
        assert f"variant_chain={key}" in page
        swap = f"&amp;chain={key}"
        assert (swap in page) == (chain["membership"] != "active")
    assert 'id="variants"' in page


def test_snapshot_round_trip_matches_the_story() -> None:
    """The regenerate link's snapshot is the play page's own (cast and deadline)."""
    parsed = Story.from_json(timeline_story())
    assert parsed is not None
    play = PlayStory.of(parsed, 45.0)
    assert PlayStory.unsigned(play.signed(), STORY) == play

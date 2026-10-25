"""#53: fork asks for no confirmation (it only creates). Its form pre-fills "<name> (fork at turn
T)"; the POST sends ``at_turn``, the name and the expected revision, and redirects to the new
story, whose play view shows "Forked from *Name* at turn T" and the character id mapping once."""

import pytest
from django.test import Client

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import answer_page, story_json
from tests.timeline.helpers import (
    KEY,
    NAME,
    REV,
    REVISION_HEADER,
    STORY,
    body_of,
    calls,
    flash,
    html,
    logged,
    path,
    posted,
    recheck_json,
    url,
)

pytestmark = pytest.mark.django_db

FORK = engine_fixture("fork")
NEW = FORK["story_id"]


def test_fork_form_has_no_confirm_step(operator_client: Client, engine: FakeEngine) -> None:
    page = html(operator_client, url("fork", 2))
    assert f'value="{NAME} (fork at turn 2)"' in page
    assert 'maxlength="200"' in page
    assert ">Fork</button>" in page
    assert "Are you sure" not in page
    assert 'class="confirm"' not in page
    assert engine.requests == []


def test_fork_redirects_to_the_new_story_with_the_banner(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", path("fork"), 201, json=FORK)
    response = operator_client.post(url("fork", 2), posted(new_name="Harbour (fork at turn 2)"))
    (request,) = calls(engine, "POST", f"/stories/{STORY}/fork")
    assert body_of(request) == {"at_turn": 2, "name": "Harbour (fork at turn 2)"}
    assert request.headers[REVISION_HEADER] == str(REV)
    assert request.headers["Idempotency-Key"] == KEY
    assert response.status_code == 302
    assert response["Location"] == f"/stories/{NEW}/"
    (line,) = flash(response)
    assert "Forked Harbour at turn 2 into Harbour (fork at turn 2)" in line
    row = logged("fork")  # the operation is recorded (#44, #132)
    assert (row.http_method, row.http_status, row.outcome) == ("POST", 201, "ok")
    assert row.path == f"/stories/{STORY}/fork"
    assert row.request["body"] == {"at_turn": 2, "name": "Harbour (fork at turn 2)"}
    assert row.idempotency_key == KEY

    answer_page(engine, story=story_json(id=NEW, name="Harbour (fork at turn 2)"))
    page = html(operator_client, f"/stories/{NEW}/")
    assert "Forked from <em>" in page
    assert f"{NAME}</a></em> at turn 2" in page
    for character in FORK["characters"]:
        assert f"<td>{character['name']}</td><td><code>{character['source_id']}</code></td>" in page
        assert f"<code>{character['id']}</code>" in page
    assert "Forked from" not in html(operator_client, f"/stories/{NEW}/")  # shown once


def test_fork_from_a_variants_row_sends_the_chain(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", path("fork"), 201, json=FORK)
    page = html(operator_client, url("fork", 3, variant_chain="3:2"))
    assert 'name="variant_chain" value="3:2"' in page
    operator_client.post(url("fork", 3), posted(new_name="Side", variant_chain="3:2"))
    (request,) = calls(engine, "POST", f"/stories/{STORY}/fork")
    assert body_of(request) == {
        "at_turn": 3,
        "name": "Side",
        "variant_chain": {"turn": 3, "variant": 2},
    }


@pytest.mark.parametrize(
    ("name", "error"), [("", "This field is required."), ("x" * 201, "at most 200")]
)
def test_fork_name_is_checked_before_calling(
    operator_client: Client, engine: FakeEngine, name: str, error: str
) -> None:
    response = operator_client.post(url("fork", 2), posted(new_name=name))
    assert response.status_code == 200
    assert error in response.content.decode()
    assert engine.requests == []


def test_fork_busy_shows_the_busy_message(operator_client: Client, engine: FakeEngine) -> None:
    busy = {"error": {"code": "story_busy", "message": "busy", "details": {"reason": "in_flight"}}}
    engine.respond("POST", path("fork"), 423, json=busy)
    page = operator_client.post(url("fork", 2), posted(new_name="F")).content.decode()
    assert "Story busy — another turn is running" in page


def test_fork_recheck_returns_to_the_source(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("fork"), 202, json=recheck_json())
    response = operator_client.post(url("fork", 2), posted(new_name="F", variant_chain="3:2"))
    assert response["Location"] == f"/stories/{STORY}/"
    assert "Guardrail re-check started" in flash(response)[0]

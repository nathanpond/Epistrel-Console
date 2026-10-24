"""Archive, unarchive and delete (#45): a confirm page (no Engine call), then one POST."""

from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.stories.helpers import calls, summary

pytestmark = pytest.mark.django_db

STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KEY"
NEXT = "/stories/?status=active"
ACTIONS = {
    "archive": ("POST", f"/stories/{STORY}/archive", "/stories/{story_id}/archive"),
    "unarchive": ("POST", f"/stories/{STORY}/unarchive", "/stories/{story_id}/unarchive"),
    "delete": ("DELETE", f"/stories/{STORY}", "/stories/{story_id}"),
}
WORDING = {
    "archive": "Turns will be refused until it is unarchived.",
    "unarchive": "Turns will be accepted again.",
    "delete": "This permanently deletes the story and all its history in the Engine.",
}


def confirm_url(action: str, **query: str) -> str:
    params = {"name": "Harbour", "next": NEXT, **query}
    return f"/stories/{STORY}/{action}/?{urlencode(params)}"


def error(status: int, code: str, message: str = "refused") -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": {"story_id": STORY}}}


def answer(engine: FakeEngine, action: str) -> None:
    method, _, template = ACTIONS[action]
    if action == "delete":
        engine.respond(method, template, 204)
    else:
        engine.respond(method, template, 200, json=engine_fixture(f"{action}_story"))


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_confirm_page_makes_no_engine_call(
    operator_client: Client, engine: FakeEngine, action: str
) -> None:
    response = operator_client.get(confirm_url(action))
    html = response.content.decode()
    assert response.status_code == 200
    assert engine.requests == []
    assert "<em>Harbour</em>" in html
    assert WORDING[action] in html
    assert f"<code>{STORY}</code>" in html  # the id, named
    assert '<a href="/stories/?status=active">Cancel</a>' in html
    assert 'name="idempotency_key" value="' in html
    assert 'name="name" value="Harbour"' in html


def test_confirm_page_complement_post_does_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, "archive")
    operator_client.post(confirm_url("archive"), {"idempotency_key": KEY, "name": "Harbour"})
    assert len(engine.requests) == 1


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_cancel_goes_back_without_calling_the_engine(
    operator_client: Client, engine: FakeEngine, action: str
) -> None:
    html = operator_client.get(confirm_url(action, next="https://evil.example/")).content.decode()
    assert '<a href="/stories/">Cancel</a>' in html  # an off-site next falls back to the list
    assert engine.requests == []


@pytest.mark.parametrize("action", ["archive", "unarchive"])
def test_post_calls_the_engine_once_and_returns_to_next(
    operator_client: Client, engine: FakeEngine, action: str
) -> None:
    answer(engine, action)
    response = operator_client.post(
        f"/stories/{STORY}/{action}/",
        {"idempotency_key": KEY, "name": "Harbour", "next": NEXT},
    )
    assert response.status_code == 302
    assert response["Location"] == NEXT
    method, path, _ = ACTIONS[action]
    (request,) = calls(engine)
    assert (request.method, request.url.path) == (method, path)
    assert request.headers["Idempotency-Key"] == KEY
    engine.respond("GET", "/stories", 200, json={"items": [summary(STORY, "Harbour", "active")]})
    engine.respond("GET", "/stories/{story_id}", 200, json=engine_fixture("get_story"))
    html = operator_client.get(NEXT).content.decode()
    verb = "Archived" if action == "archive" else "Unarchived"
    assert f"{verb} Harbour — status" in html


def test_delete_calls_the_engine_once_and_flashes(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, "delete")
    response = operator_client.post(
        f"/stories/{STORY}/delete/",
        {"idempotency_key": KEY, "name": "Harbour", "next": f"/stories/{STORY}/"},
    )
    assert response.status_code == 302
    assert response["Location"] == "/stories/"  # never back to the deleted story's page
    (request,) = calls(engine)
    assert (request.method, request.url.path) == ("DELETE", f"/stories/{STORY}")
    engine.respond("GET", "/stories", 200, json={"items": []})
    assert "Deleted Harbour" in operator_client.get("/stories/").content.decode()


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_each_post_is_recorded_in_the_operation_log(
    operator_client: Client, engine: FakeEngine, action: str
) -> None:
    answer(engine, action)
    operator_client.post(f"/stories/{STORY}/{action}/", {"idempotency_key": KEY})
    row = EngineOperation.objects.get()
    assert row.kind == f"{action}_story"
    assert row.story_id == STORY
    assert row.idempotency_key == KEY
    assert row.outcome == "ok"


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_story_busy_renders_the_error_panel(
    operator_client: Client, engine: FakeEngine, action: str
) -> None:
    method, _, template = ACTIONS[action]
    engine.respond(method, template, 423, json=error(423, "story_busy", "a turn is running"))
    response = operator_client.post(f"/stories/{STORY}/{action}/", {"idempotency_key": KEY})
    html = response.content.decode()
    assert response.status_code == 200
    assert "Engine error 423 story_busy" in html
    assert "a turn is running" in html
    assert len(engine.requests) == 1


def test_forced_archive_of_a_paused_story_shows_the_refusal(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        "/stories/{story_id}/archive",
        409,
        json=error(409, "story_paused", "the story is paused while a check runs"),
    )
    html = operator_client.post(f"/stories/{STORY}/archive/", {"idempotency_key": KEY}).content
    assert "Engine error 409 story_paused" in html.decode()


@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_not_found_is_an_error_not_success(
    operator_client: Client, engine: FakeEngine, action: str
) -> None:
    method, _, template = ACTIONS[action]
    engine.respond(method, template, 404, json=error(404, "not_found", "no such story"))
    response = operator_client.post(f"/stories/{STORY}/{action}/", {"idempotency_key": KEY})
    assert response.status_code == 200
    assert "Engine error 404 not_found" in response.content.decode()


def test_a_post_without_a_valid_key_gets_a_fresh_one(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, "archive")
    operator_client.post(f"/stories/{STORY}/archive/", {"idempotency_key": "bad key!"})
    (request,) = calls(engine)
    assert len(request.headers["Idempotency-Key"]) == 26


def test_name_is_display_only_and_escaped(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(confirm_url("delete", name="<b>x</b>")).content.decode()
    assert "<em>&lt;b&gt;x&lt;/b&gt;</em>" in html
    assert engine.requests == []


def test_unnamed_story_is_named_by_its_id(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(f"/stories/{STORY}/archive/").content.decode()
    assert f"Archive <em>{STORY}</em>?" in html


def test_detail_page_shows_the_header_and_actions(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories/{story_id}", 200, json=engine_fixture("get_story"))
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "<h1>Harbour</h1>" in html
    assert "<li>Mara (player)</li>" in html
    assert "<li>Tobin</li>" in html
    assert "Teen" in html
    assert f"/stories/{STORY}/archive/?name=Harbour&amp;next=%2Fstories%2F{STORY}%2F" in html
    assert f"/stories/{STORY}/delete/" in html

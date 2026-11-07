"""An importing story (#295, E10): ``ImportingStoryMiddleware`` turns a 409 ``story_importing``
from any story-URL view into ``stories/importing.html``, and ``write_refusal`` words it."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings

from console.engine.client import EngineClient, EngineError
from console.engine_access import engine_client_for
from console.middleware import ImportingStoryMiddleware
from console.portability import IMPORTING_TEXT, importing_refusal, is_importing
from console.views import play as play_views
from console.wording import write_refusal
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.play.helpers import error_json

STORY = "01JM7ST0000000000000000003"
JOB = "01JM7JB0000000000000000007"
HTMX = {"HX-Request": "true"}
TEXT = f"{IMPORTING_TEXT} (Guardrails, 3/9)"


def importing_json(**changes: Any) -> dict[str, Any]:
    details: dict[str, Any] = {
        "story_id": STORY,
        "job_id": JOB,
        "stage": "guardrails",
        "progress": {"done": 3, "total": 9},
    }
    details.update(changes)
    return error_json("story_importing", "the story is being imported", **details)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    answer_relations(fake)
    answer_effective_config(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(play_views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def test_the_middleware_sits_right_after_the_held_one(settings: Any) -> None:
    middleware = list(settings.MIDDLEWARE)
    held = middleware.index("console.middleware.HeldStoryMiddleware")
    assert middleware[held + 1] == "console.middleware.ImportingStoryMiddleware"


def test_the_play_page_of_an_importing_story(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories/{story_id}", 409, json=importing_json())
    response = operator_client.get(f"/stories/{STORY}/?tab=ingest")
    html = response.content.decode()
    assert response.status_code == 409
    assert "<title>Story importing</title>" in html
    assert "<h1>Story importing</h1>" in html
    assert f'Story <code title="{STORY}">…00000003</code>' in html
    # The real urlconf has no ``engine:import-job`` yet (#321): plain text.
    assert f'<p class="warning importing-message" role="status">{TEXT}</p>' in html
    assert f'<a class="retry" href="/stories/{STORY}/?tab=ingest">Retry</a>' in html
    assert "Engine error" not in html


@override_settings(ROOT_URLCONF="tests.urls_with_m7_pages")
def test_the_import_job_is_linked_once_its_page_exists(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories/{story_id}", 409, json=importing_json())
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert f'<a class="import-job" href="/engine/import/{JOB}/">{TEXT}</a>' in html


def test_an_htmx_request_is_redirected_to_the_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement.**"""
    engine.respond("GET", "/stories/{story_id}", 409, json=importing_json())
    response = operator_client.get(f"/stories/{STORY}/", headers=HTMX)
    assert response.status_code == 200
    assert response["HX-Redirect"] == f"/stories/{STORY}/"


def test_a_paused_story_still_reaches_the_usual_handling(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** a 409 ``story_paused`` isn't the importing page."""
    paused = error_json("story_paused", "paused", story_id=STORY, job_id=JOB, cause="rating_check")
    engine.respond("GET", "/stories/{story_id}", 409, json=paused)
    response = operator_client.get(f"/stories/{STORY}/")
    html = response.content.decode()
    assert "Story importing" not in html
    assert "story_paused" in html  # #43's panel


def test_a_view_without_a_story_id_passes_the_exception_on(rf: RequestFactory) -> None:
    """**Complement.**"""
    middleware = ImportingStoryMiddleware(lambda r: None)  # type: ignore[arg-type,return-value]
    exc = EngineError("story_importing", "x", {"job_id": JOB}, 409)
    request = rf.get("/engine/")
    request.resolver_match = None
    assert middleware.process_exception(request, exc) is None
    other = rf.get(f"/stories/{STORY}/")
    other.resolver_match = type("M", (), {"kwargs": {"story_id": STORY}})()
    assert middleware.process_exception(other, ValueError("x")) is None
    assert middleware.process_exception(other, EngineError("story_busy", "x", {}, 409)) is None


def _exc(**changes: Any) -> EngineError:
    body = importing_json(**changes)["error"]
    return EngineError(body["code"], body["message"], body["details"], 409)


def test_write_refusal_words_story_importing() -> None:
    refusal = write_refusal(_exc(), STORY, "play a turn")
    assert refusal is not None
    assert refusal.text == TEXT
    assert refusal.url == ""  # engine:import-job unregistered
    assert is_importing(_exc())


@pytest.mark.parametrize(
    ("changes", "text"),
    [
        ({"stage": None}, f"{IMPORTING_TEXT} (3/9)"),
        ({"progress": {"done": 0, "total": 0}}, f"{IMPORTING_TEXT} (Guardrails)"),
        ({"stage": "", "progress": None}, IMPORTING_TEXT),
        ({"progress": {"done": "3", "total": 9}}, f"{IMPORTING_TEXT} (Guardrails)"),
    ],
)
def test_a_missing_stage_or_count_is_dropped(changes: dict[str, Any], text: str) -> None:
    assert importing_refusal(_exc(**changes)).text == text


@override_settings(ROOT_URLCONF="tests.urls_with_m7_pages")
def test_the_refusal_links_the_import_job_under_the_stub_urlconf() -> None:
    assert importing_refusal(_exc()).url == f"/engine/import/{JOB}/"

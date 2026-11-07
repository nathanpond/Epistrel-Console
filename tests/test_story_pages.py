"""E11's page frames (#296, ``console/views/story_pages.py``): the story frame's two reads in
order, held / importing / not-found / failed reads, an Engine error escaping ``build``, the title
and the current nav link, the pause banner, and the Engine frame's own held handling.

The frames are reached through ``tests/urls_with_m7_pages.py``'s ``stories:rewrites`` and
``engine:certifications`` stubs, whose module-level ``recording_build`` records each context in
``BUILT`` and answers the test's ``BUILD`` callable when one is set."""

import re
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import Client, override_settings

from console.engine.client import EngineClient, EngineError
from console.engine_access import engine_client_for
from console.held import HELD_TEXT
from console.portability import IMPORTING_TEXT
from console.views import engine_panel as panel_views
from console.views import story_pages
from console.views.story_pages import StoryPageContext, page_label
from tests import urls_with_m7_pages as m7
from tests.fakes.engine import FakeEngine
from tests.fakes.story_config import answer_effective_config
from tests.fixtures import engine_fixture
from tests.play.helpers import HTMX, STORY, error_json, story_json

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/rewrites/"
ENGINE_URL = "/engine/certifications/"
STORY_READ = f"/stories/{STORY}"
CONFIG_READ = f"/stories/{STORY}/effective-config"
SETTINGS_READ = f"/stories/{STORY}/settings"
NOT_FOUND = '<p class="not-found">Not found (it may have been deleted or truncated)</p>'
JOB = "01JM7JB0000000000000000009"


@pytest.fixture(autouse=True)
def recording() -> Iterator[None]:
    m7.BUILT.clear()
    m7.BUILD.clear()
    yield
    m7.BUILT.clear()
    m7.BUILD.clear()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    fake.respond("GET", "/stories/{story_id}", 200, json=story_json())
    answer_effective_config(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    monkeypatch.setattr(panel_views, "engine_client_for", client_with_fake)
    with override_settings(
        ENGINE_URL="http://engine.test", ROOT_URLCONF="tests.urls_with_m7_pages"
    ):
        yield fake


def paths(engine: FakeEngine) -> list[str]:
    return [request.url.path for request in engine.requests]


def build_with(build: Any) -> None:
    m7.BUILD.append(build)


def raising(exc: Exception) -> Any:
    def build(ctx: Any) -> Mapping[str, Any]:
        raise exc

    return build


def story_ctx() -> StoryPageContext:
    (ctx,) = m7.BUILT
    assert isinstance(ctx, StoryPageContext)
    return ctx


# --- the reads ------------------------------------------------------------------------------------


def test_the_frame_reads_story_then_config(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", "/stories/{story_id}/settings", 200, json=engine_fixture("get_story_settings")
    )

    def build(ctx: StoryPageContext) -> Mapping[str, Any]:
        ctx.client.get_story_settings(ctx.story_id)
        return {}

    build_with(build)
    response = operator_client.get(URL)
    assert response.status_code == 200
    assert paths(engine) == [STORY_READ, CONFIG_READ, SETTINGS_READ]
    ctx = story_ctx()
    assert ctx.story is not None
    assert ctx.story.id == STORY
    assert ctx.story_name == "Harbour"
    assert ctx.config is not None
    assert ctx.story_error is None
    assert ctx.config_error is None


def test_the_frame_makes_no_other_read(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** with a ``build`` that reads nothing, the frame's two reads alone."""
    operator_client.get(URL)
    assert paths(engine) == [STORY_READ, CONFIG_READ]
    assert len(m7.BUILT) == 1


def test_a_held_story_renders_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        "/stories/{story_id}",
        503,
        json=error_json("erasure_pending", "held"),
        headers={"Retry-After": "5"},
    )
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 503
    assert "<title>Held story — Epistrel Console</title>" in html
    assert f"{HELD_TEXT} — retry in 5 s" in html
    assert m7.BUILT == []  # Complement: build is never called
    assert paths(engine) == [STORY_READ]


def test_an_importing_story_renders_importing(operator_client: Client, engine: FakeEngine) -> None:
    body = error_json("story_importing", "importing", story_id=STORY, job_id=JOB)
    engine.respond("GET", "/stories/{story_id}", 409, json=body)
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 409
    assert "<title>Story importing</title>" in html
    assert IMPORTING_TEXT in html
    assert m7.BUILT == []  # Complement: build is never called


def test_a_held_config_read_renders_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", "/stories/{story_id}/effective-config", 503, json=error_json("erasure_pending")
    )
    response = operator_client.get(URL)
    assert response.status_code == 503
    assert m7.BUILT == []


def test_a_missing_story_is_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories/{story_id}", 404, json=error_json("not_found", "no story"))
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 404
    assert NOT_FOUND in html
    assert f"<title>Rewrite · {STORY}</title>" in html
    assert "<h2>Rewrite</h2>" in html
    assert 'class="story-pages"' not in html  # no nav
    assert 'class="story-links"' not in html
    assert "Engine error" not in html
    assert m7.BUILT == []
    assert paths(engine) == [STORY_READ]  # the config read is skipped


def test_another_failed_story_read_still_builds(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("GET", "/stories/{story_id}", OSError("down"))
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert f"<h1>{STORY}</h1>" in html
    assert f'<p class="story-header"><code>{STORY}</code></p>' in html
    assert 'class="engine-error' in html
    ctx = story_ctx()
    assert ctx.story is None
    assert ctx.story_error is not None
    assert ctx.config is not None


def test_a_failed_config_read_still_renders(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", "/stories/{story_id}/effective-config", 404, json=error_json("not_found", "gone")
    )
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    ctx = story_ctx()
    assert ctx.config is None
    assert isinstance(ctx.config_error, EngineError)
    assert ctx.config_error.http_status == 404
    assert "<h1>Harbour</h1>" in html
    assert NOT_FOUND not in html  # a config 404 is not the story's not-found
    assert 'class="engine-error' not in html  # each page decides what config_error shows


# --- build's errors -------------------------------------------------------------------------------


LIMIT = EngineError("rewrite_session_limit", "too many open sessions", {"limit": 5}, 409)


def test_an_engine_error_in_build_is_the_panel_in_the_frame(
    operator_client: Client, engine: FakeEngine
) -> None:
    build_with(raising(LIMIT))
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<h1>Harbour</h1>" in html
    assert "<h2>Rewrite</h2>" in html
    assert 'class="engine-error' in html
    assert "<code>rewrite_session_limit</code>" in html


def test_an_engine_error_in_build_on_htmx_is_the_panel_alone(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** an HTMX request gets the panel alone."""
    build_with(raising(LIMIT))
    response = operator_client.get(URL, headers=HTMX)
    html = response.content.decode()
    assert response.status_code == 200
    assert html.startswith('<div class="engine-error')
    assert "<code>rewrite_session_limit</code>" in html
    assert "<h1>" not in html


def test_a_held_answer_in_build_reaches_the_middleware(
    operator_client: Client, engine: FakeEngine
) -> None:
    build_with(raising(EngineError("erasure_pending", "held", {}, 503)))
    assert operator_client.get(URL).status_code == 503


def test_a_build_response_is_answered_as_is(operator_client: Client, engine: FakeEngine) -> None:
    build_with(lambda ctx: HttpResponse("built", status=202))
    response = operator_client.get(URL)
    assert response.status_code == 202
    assert response.content == b"built"


# --- the frame ------------------------------------------------------------------------------------


def test_the_title_and_current_link(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(URL).content.decode()
    assert "<title>Rewrite · Harbour</title>" in html
    assert "<h1>Harbour</h1>" in html
    assert "<h2>Rewrite</h2>" in html
    nav = re.search(
        r'<span class="story-pages" role="navigation" aria-label="Story pages">(.*?)</span>', html
    )
    assert nav is not None
    assert nav.group(1).startswith(f'<a href="{URL}" aria-current="page">Rewrite</a> · ')
    assert nav.group(1).count("aria-current") == 1
    assert f'<code>{STORY}</code> · <span class="status" title="active">Active</span>' in html
    assert "Timeline revision 1" in html


def test_the_story_nav_follows_config_in_the_link_line(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(URL).content.decode()
    S = f"/stories/{STORY}/"
    assert (
        f'<a class="config-tab" href="{S}config/">Config</a> · <span class="story-pages" '
        'role="navigation" aria-label="Story pages">'
        f'<a href="{S}rewrites/" aria-current="page">Rewrite</a> · '
        f'<a href="{S}amendments/">Amendments</a> · <a href="{S}backfill/">Backfill</a> · '
        f'<a href="{S}export/">Export</a> · <a href="{S}operations/">Operations</a></span></p>'
    ) in html


def test_the_story_nav_shows_backfill_under_the_real_urlconf(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#308 registered ``stories:backfill``: under the real urlconf its page's nav holds the
    registered rows (#297's Rewrite, #302's Amendments, #319's Export and #324's Operations
    too), Backfill marked current."""
    from console.views import backfill as backfill_views

    patched = vars(story_pages)["engine_client_for"]  # the fixture's fake-wired client
    monkeypatch.setattr(backfill_views, "engine_client_for", patched)
    engine.respond(
        "GET", "/stories/{story_id}/backfill", 200, json=engine_fixture("m7/backfill_extracting")
    )
    S = f"/stories/{STORY}/"
    with override_settings(ROOT_URLCONF="epistrel_console.urls"):
        html = operator_client.get(f"{S}backfill/").content.decode()
    assert (
        f'<a class="config-tab" href="{S}config/">Config</a> · <span class="story-pages" '
        'role="navigation" aria-label="Story pages">'
        f'<a href="{S}rewrites/">Rewrite</a> · <a href="{S}amendments/">Amendments</a> · '
        f'<a href="{S}backfill/" aria-current="page">Backfill</a> · '
        f'<a href="{S}export/">Export</a> · <a href="{S}operations/">Operations</a></span></p>'
    ) in html


def test_a_paused_story_shows_the_banner(operator_client: Client, engine: FakeEngine) -> None:
    pause = {"job_id": JOB, "cause": "backfill", "progress": {"done": 2, "total": 7}}
    engine.respond("GET", "/stories/{story_id}", 200, json=story_json(status="paused", pause=pause))
    html = operator_client.get(URL).content.decode()
    assert html.count('id="pause-banner"') == 1  # base.html's, not a second copy
    assert "Paused: backfill in progress (2/7 chunks)" in html
    assert html.index('id="pause-banner"') < html.index("<header>")


def test_the_label_falls_back_to_the_humanised_url_name() -> None:
    assert page_label((), "stories:prune-confirm", None) == "Prune confirm"
    assert page_label((("stories:rewrites", "Rewrite"),), "stories:rewrite", None) == "Rewrite"
    assert page_label((), "stories:prune", "Prune …1234") == "Prune …1234"


# --- the Engine frame -----------------------------------------------------------------------------


def test_engine_page_handles_erasure_pending_itself(
    operator_client: Client, engine: FakeEngine
) -> None:
    held = EngineError("erasure_pending", "held", {}, 503, retry_after="7")
    build_with(raising(held))
    response = operator_client.get(ENGINE_URL)
    html = response.content.decode()
    assert response.status_code == 503
    assert response["Retry-After"] == "7"
    assert "<title>Certifications · Engine</title>" in html
    assert "<h1>Engine</h1>" in html
    assert f'<p class="warning held-message" role="status">{HELD_TEXT} — retry in 7 s</p>' in html
    assert f'<a class="retry" href="{ENGINE_URL}">Retry</a>' in html
    assert "Delete" not in html
    # HTMX: the fragment alone.
    fragment = operator_client.get(ENGINE_URL, headers=HTMX)
    assert fragment.status_code == 503
    assert fragment.content.decode().lstrip().startswith('<p class="warning held-message"')


def test_engine_page_renders_other_errors_as_the_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    build_with(raising(EngineError("forbidden", "no", {}, 403)))
    response = operator_client.get(ENGINE_URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<code>forbidden</code>" in html
    assert "<h2>Certifications</h2>" in html
    htmx = operator_client.get(ENGINE_URL, headers=HTMX).content.decode()
    assert htmx.startswith('<div class="engine-error')


def test_engine_page_reads_nothing_and_marks_its_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(ENGINE_URL).content.decode()
    assert paths(engine) == []
    assert (
        '<a href="/engine/providers/">Providers</a> · <span class="engine-pages" '
        'role="navigation" aria-label="Engine pages"><a href="/engine/import/">Import a bundle</a>'
        ' · <a href="/engine/certifications/" aria-current="page">Certifications</a> · '
        '<a href="/engine/evals/">Eval runs</a></span></p>'
    ) in html


# --- the stubbed nav demo -------------------------------------------------------------------------


def test_the_engine_home_shows_the_engine_nav(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories", 200, json=engine_fixture("list_stories"))
    html = operator_client.get("/engine/").content.decode()
    assert (
        '>Providers</a> · <span class="engine-pages" role="navigation" aria-label="Engine pages">'
        '<a href="/engine/import/">Import a bundle</a> · '
        '<a href="/engine/certifications/">Certifications</a> · '
        '<a href="/engine/evals/">Eval runs</a></span> · <a href="/models/'
    ) in html

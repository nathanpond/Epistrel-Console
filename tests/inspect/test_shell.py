"""The Inspect area's shell (#135): the tab bar gated by the story's effective configuration, the
greyed panel (zero calls to a greyed tab's routes), a route's 409 ``module_off``, the
effective-config failures, and the "Inspect" links from the story and history pages.

The tests run on ``tests.inspect.urls``, which stubs any tab whose own story hasn't registered
it; since #58 every tab is registered for real, so that urlconf matches the Console's own."""

import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, override_settings
from django.urls import reverse

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.held import held_text
from console.inspect import Tab, Unavailable
from console.views import history as history_views
from console.views import inspect as views
from console.views import play as play_views
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.history import helpers as history_helpers
from tests.inspect.helpers import EFFECTIVE, STORY, STORY_PATH, lite, structured
from tests.play.helpers import answer_page, error_json, story_json

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/inspect/"
CONFIG_URL = f"/stories/{STORY}/config/"
RETRIEVE = f"/stories/{STORY}/retrieve"
LEDGER = f"/stories/{STORY}/inspect/ledger"
ENGINE_TABS = (Tab.MEMORY, Tab.RETRIEVAL, Tab.CANON, Tab.LEDGER, Tab.SCENE)
SCENE_BODY: dict[str, Any] = {
    "turn": 0,
    "variant": None,
    "viewpoint": "omniscient",
    "location": None,
    "present": [],
}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    answer_relations(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (views, play_views, history_views):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    fake.respond("GET", STORY_PATH, 200, json=story_json())
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="tests.inspect.urls"):
        yield fake


def tab_url(tab: Tab) -> str:
    return URL if tab == Tab.OVERVIEW else f"{URL}{tab.value}/"


def get(client: Client, tab: Tab = Tab.OVERVIEW) -> Any:
    response = client.get(tab_url(tab))
    assert response.status_code == 200
    return response


def bar(html: str) -> str:
    found = re.search(r'<nav class="tabs" aria-label="Inspect tabs">(.*?)</nav>', html, re.S)
    assert found is not None
    return found.group(1)


def paths(engine: FakeEngine) -> list[str]:
    return [request.url.path for request in engine.requests]


def answer_config(engine: FakeEngine, body: dict[str, Any]) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=body)


# --- the bar --------------------------------------------------------------------------------------


def test_structured_story_enables_every_tab(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, structured())
    html = get(operator_client).content.decode()
    tabs = bar(html)
    assert "disabled" not in tabs
    assert tabs.startswith(f'<a class="active" aria-current="page" href="{URL}">Overview</a>')
    for tab in Tab:
        assert f'href="{tab_url(tab)}">{tab.label}</a>' in tabs
    labels = re.findall(r">(\w+)</(?:a|span)>", tabs)
    assert labels == [tab.label for tab in Tab]  # bar order
    assert "<title>Inspect · Overview · Harbour</title>" in html
    assert "<h1>Inspect · Harbour</h1>" in html
    assert f'<a href="/stories/{STORY}/">← Story</a>' in html
    # One effective-config read and one story read, in that order.
    assert paths(engine) == [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]


def test_lite_story_greys_the_engine_tabs(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, lite())
    tabs = bar(get(operator_client).content.decode())
    for tab in ENGINE_TABS:
        assert f'href="{tab_url(tab)}"' not in tabs  # not an <a>
    assert (
        '<span class="disabled" aria-disabled="true" title="Needs the structured pipeline '
        '(pipeline: structured; this story runs lite)">Canon</span>'
    ) in tabs
    assert (
        '<span class="disabled" aria-disabled="true" title="Needs the episodic module '
        '(memory.episodic.enabled is off under this story&#x27;s profile)">Memory</span>'
    ) in tabs
    for label in ("Retrieval", "Ledger", "Scene"):
        assert re.search(
            rf'<span class="disabled" aria-disabled="true" title="[^"]+">{label}<', tabs
        )
    assert f'<a href="{tab_url(Tab.PREDICATES)}">Predicates</a>' in tabs


def test_a_tab_whose_url_is_not_registered_is_absent(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured())
    # Every tab is registered for real now: Overview (#151), Memory (#140), Retrieval (#141),
    # Canon (#137), Predicates (#136), Ledger (#148) and Scene (#58).
    with override_settings(ROOT_URLCONF="epistrel_console.urls"):
        tabs = bar(get(operator_client).content.decode())
    assert tabs.startswith(f'<a class="active" aria-current="page" href="{URL}">Overview</a>')
    for tab in Tab:
        assert f'href="{tab_url(tab)}">{tab.label}</a>' in tabs
    # A urlconf without a tab's URL leaves that tab out of the bar (#64's nav rule).
    with override_settings(ROOT_URLCONF="tests.inspect.urls_without_retrieval"):
        tabs = bar(get(operator_client).content.decode())
    assert ">Retrieval<" not in tabs
    assert f'href="{tab_url(Tab.SCENE)}">Scene</a>' in tabs


# --- a greyed tab opened directly -----------------------------------------------------------------


def test_a_greyed_tab_renders_the_panel_with_zero_calls_to_its_route(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, lite())
    response = get(operator_client, Tab.RETRIEVAL)
    html = response.content.decode()
    assert RETRIEVE not in paths(engine)
    assert paths(engine) == [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
    assert "built" not in response.context  # build was not called
    assert (
        '<span class="disabled" aria-disabled="true" title="Needs the episodic module '
        '(memory.episodic.enabled is off under this story&#x27;s profile)" '
        'aria-current="page">Retrieval</span>'
    ) in bar(html)
    assert '<div class="panel unavailable">' in html
    assert "<h2>Not available for this story</h2>" in html
    assert (
        "<p>Needs the <code>episodic</code> module (<code>memory.episodic.enabled</code> is off "
        "under this story&#x27;s profile)</p>"
    ) in html
    assert f'<a href="{CONFIG_URL}">Change it on the story\'s Config page</a>' in html
    assert "config-changed" not in html
    assert "<title>Inspect · Retrieval · Harbour</title>" in html


def test_a_greyed_ledger_on_lite_names_the_pipeline(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, lite(commitment__enabled=True))
    html = get(operator_client, Tab.LEDGER).content.decode()
    assert LEDGER not in paths(engine)
    assert (
        "<p>Needs the structured pipeline (<code>pipeline: structured</code>; this story runs "
        "<code>lite</code>)</p>"
    ) in html


def test_an_enabled_tab_calls_build(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, structured())
    engine.respond("GET", "/stories/{story_id}/scene", 200, json=SCENE_BODY)
    response = get(operator_client, Tab.SCENE)
    assert f"/stories/{STORY}/scene" in paths(engine)  # the Scene tab's build made its read
    assert response.context["scene"].header == "Turn 0 · as Omniscient"
    assert '<a class="active" aria-current="page" href="' in bar(response.content.decode())


# --- 409 module_off from a tab's route ------------------------------------------------------------


def test_module_off_from_a_route_renders_the_greyed_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured())
    engine.respond(
        "GET",
        "/stories/{story_id}/inspect/ledger",
        409,
        json=error_json("module_off", "commitment is off", module="commitment"),
    )
    response = get(operator_client, Tab.LEDGER)
    html = response.content.decode()
    assert LEDGER in paths(engine)
    assert 'class="engine-error"' not in html  # not the generic error panel
    assert '<div class="panel unavailable">' in html
    assert (
        "<p>Needs the <code>commitment</code> module (<code>commitment.enabled</code> is off "
        "under this story&#x27;s profile)</p>"
    ) in html
    assert (
        '<p class="config-changed">The story\'s configuration changed since this page '
        f'loaded — <a href="{tab_url(Tab.LEDGER)}">reload</a></p>'
    ) in html
    # The bar is not recomputed: Ledger stays an enabled, active link.
    assert f'<a class="active" aria-current="page" href="{tab_url(Tab.LEDGER)}">Ledger</a>' in (
        bar(html)
    )


def test_module_off_without_a_module_names_the_tabs_rule(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured())
    engine.respond(
        "GET", "/stories/{story_id}/inspect/ledger", 409, json=error_json("module_off", "off")
    )
    html = get(operator_client, Tab.LEDGER).content.decode()
    assert "<p>Needs the <code>commitment</code> module" in html


def test_other_errors_from_build_are_the_error_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured())
    # The Ledger tab (#148) renders its own read's errors: a 503 is #43's panel there.
    engine.respond(
        "GET",
        "/stories/{story_id}/inspect/ledger",
        503,
        json=error_json("database_unavailable", "down"),
    )
    html = get(operator_client, Tab.LEDGER).content.decode()
    assert "Engine error 503 database_unavailable" in html
    assert "panel unavailable" not in html


# --- effective-config failures --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "headline"),
    [
        (404, "not_found", "Engine error 404 not_found"),
        (503, "database_unavailable", "Engine error 503 database_unavailable"),
    ],
)
def test_effective_config_error_replaces_the_bar(
    operator_client: Client, engine: FakeEngine, status: int, code: str, headline: str
) -> None:
    engine.respond("GET", EFFECTIVE, status, json=error_json(code, "refused"))
    for tab in Tab:
        engine.reset()
        response = get(operator_client, tab)
        html = response.content.decode()
        assert headline in html
        assert 'aria-label="Inspect tabs"' not in html
        assert "panel unavailable" not in html
        assert "<h1>Inspect · Harbour</h1>" in html  # the header keeps the name
        assert paths(engine) == [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
        assert "built" not in response.context


def test_an_unknown_story_is_convention_10s_not_found_on_every_tab(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#177: both shell reads 404 (the story is gone): the not-found line, not #43's panel."""
    engine.respond("GET", EFFECTIVE, 404, json=error_json("not_found", "no story"))
    engine.respond("GET", STORY_PATH, 404, json=error_json("not_found", "no story"))
    for tab in Tab:
        engine.reset()
        response = get(operator_client, tab)
        html = response.content.decode()
        assert '<p class="not-found">Not found (it may have been deleted or truncated)</p>' in html
        assert "Engine error" not in html
        assert 'aria-label="Inspect tabs"' not in html
        assert paths(engine) == [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
        assert "built" not in response.context


def test_effective_config_unreachable_replaces_the_bar(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("GET", EFFECTIVE, httpx.ConnectError("refused"))
    response = get(operator_client, Tab.LEDGER)
    html = response.content.decode()
    assert "Engine unreachable" in html
    assert 'aria-label="Inspect tabs"' not in html
    assert LEDGER not in paths(engine)


def test_effective_config_erasure_pending_is_the_held_wrapper(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        EFFECTIVE,
        503,
        json=error_json("erasure_pending", "held"),
        headers={"Retry-After": "30"},
    )
    response = operator_client.get(tab_url(Tab.LEDGER))
    assert response.status_code == 503
    html = response.content.decode()
    assert held_text("30") in html
    assert 'aria-label="Inspect tabs"' not in html
    assert LEDGER not in paths(engine)


def test_a_failed_story_read_degrades_the_name_to_the_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured())
    engine.respond("GET", STORY_PATH, 503, json=error_json("erasure_pending", "held"), headers={})
    html = get(operator_client).content.decode()
    assert f"<h1>Inspect · {STORY}</h1>" in html
    assert f"<title>Inspect · Overview · {STORY}</title>" in html
    assert 'aria-label="Inspect tabs"' in html


# --- the shell ------------------------------------------------------------------------------------


def test_inspect_follows_the_acting_principal_shell(
    operator_client: Client, engine: FakeEngine
) -> None:
    operator_client.post("/acting-as/", {"principal": "", "other": "alice", "next": "/"})
    answer_config(engine, structured())
    html = get(operator_client).content.decode()
    assert "Acting as <strong><code>alice</code></strong>" in html
    assert all(
        request.headers.get("X-Epistrel-Principal") == "alice" for request in engine.requests
    )


def test_inline_unavailable_is_one_notice_line() -> None:
    reason = Unavailable("episodic", "memory.episodic.enabled", "Needs the `episodic` <module>")
    html = render_to_string(
        "console/inspect/_unavailable.html",
        {"unavailable": reason, "config_url": CONFIG_URL, "inline": True},
    )
    assert html.strip() == (
        '<p class="notice unavailable">Needs the <code>episodic</code> &lt;module&gt; — '
        f'<a href="{CONFIG_URL}">Change it on the story\'s Config page</a></p>'
    )


# --- the Inspect links ----------------------------------------------------------------------------


def test_the_play_page_links_inspect_after_history(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_effective_config(engine)
    answer_page(engine)
    html = operator_client.get(reverse("stories:detail", args=[STORY])).content.decode()
    assert (
        f'<a class="history-link" href="/stories/{STORY}/history/">History</a> '
        f'<a class="inspect-link" href="{URL}">Inspect</a>'
    ) in html


def test_the_history_page_links_inspect_after_play(
    operator_client: Client, engine: FakeEngine
) -> None:
    history_helpers.answer(engine)
    answer_effective_config(
        engine
    )  # #181: a Lite turn's input asks whether the story is structured
    html = history_helpers.page(operator_client)
    assert (
        f'<a href="/stories/{STORY}/">Play</a> · <a class="inspect-link" href="{URL}">Inspect</a>'
        " · "
    ) in html


def test_an_archived_story_links_inspect(operator_client: Client, engine: FakeEngine) -> None:
    answer_effective_config(engine)
    answer_page(engine, story=story_json(status="archived", archived_at="2026-10-01T00:00:00Z"))
    html = operator_client.get(reverse("stories:detail", args=[STORY])).content.decode()
    assert f'<a class="inspect-link" href="{URL}">Inspect</a>' in html

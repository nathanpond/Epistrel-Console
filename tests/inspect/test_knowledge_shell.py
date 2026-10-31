"""The Knowledge tab's shell (#189): its partitions gate in the bar, zero knowledge reads while it
is greyed, the landing's redirect to the first registered section, the sub-navigation, the
nested-belief notice and the empty shell.

The section tests run on ``tests.inspect.urls_knowledge_exposures_only`` (only a stub
``inspect:exposures``) and ``tests.inspect.urls_knowledge_no_sections`` (none)."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, STORY, STORY_PATH, structured_v2, with_settings
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

INSPECT = f"/stories/{STORY}/inspect/"
KNOWLEDGE = f"{INSPECT}knowledge/"
EXPOSURES_URL = f"{KNOWLEDGE}exposures/"
PROBE_URL = f"{KNOWLEDGE}probe/"
SHELL_READS = [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
KNOWLEDGE_ROUTES = ("/inspect/beliefs", "/inspect/divergences", "/inspect/exposures")
EXPOSURES_ONLY = "tests.inspect.urls_knowledge_exposures_only"
NO_SECTIONS = "tests.inspect.urls_knowledge_no_sections"
NOTICE = (
    '<p class="notice">Shows first-order beliefs. Nested beliefs aren\'t readable through the '
    "Engine's API yet "
    '(<a href="https://github.com/nathanpond/Epistrel/issues/606">nathanpond/Epistrel#606</a>).</p>'
)
NOTICE_START = '<p class="notice">Shows first-order beliefs.'
EMPTY = '<p class="none">No Knowledge sections are available yet.</p>'
PARTITIONS_OFF_REASON = (
    "Needs the partitions module (character_knowledge.partitions is off under this story"
    "&#x27;s profile)"
)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", STORY_PATH, 200, json=story_json())
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF=EXPOSURES_ONLY):
        yield fake


def partitions_off(**effective: Any) -> dict[str, Any]:
    """The v2 configuration with partitions and every module it gates off (a valid profile)."""
    return with_settings(
        structured_v2(**effective),
        {
            "character_knowledge.partitions": False,
            "character_knowledge.nested_beliefs.enabled": False,
            "character_knowledge.lazy_resolution": False,
            "character_knowledge.testimony": "off",
            "perception.level": "none",
        },
    )


def answer_config(engine: FakeEngine, body: dict[str, Any]) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=body)


def paths(engine: FakeEngine) -> list[str]:
    return [r.url.path for r in engine.requests]


def page(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def bar(html: str) -> str:
    found = re.search(r'<nav class="tabs" aria-label="Inspect tabs">(.*?)</nav>', html, re.S)
    assert found is not None
    return found.group(1)


def subnav(html: str) -> str | None:
    found = re.search(r'<nav class="subtabs" aria-label="Knowledge sections">(.*?)</nav>', html)
    return found.group(1) if found is not None else None


# --- the bar --------------------------------------------------------------------------------------


def test_knowledge_tab_enabled_under_v2(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, structured_v2())
    tabs = bar(page(operator_client, INSPECT))
    assert f'<a href="{KNOWLEDGE}">Knowledge</a>' in tabs
    labels = re.findall(r">(\w+)</(?:a|span)>", tabs)
    assert labels.index("Knowledge") == labels.index("Canon") + 1
    assert "disabled" not in tabs


def test_knowledge_enabled_while_ledger_greyed(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, structured_v2(commitment__enabled=False))
    tabs = bar(page(operator_client, INSPECT))
    assert f'<a href="{KNOWLEDGE}">Knowledge</a>' in tabs
    assert re.search(r'<span class="disabled"[^>]*>Ledger</span>', tabs)


@pytest.mark.parametrize("url", [KNOWLEDGE, EXPOSURES_URL], ids=["landing", "exposures section"])
def test_greyed_knowledge_tab_makes_zero_knowledge_calls(
    operator_client: Client, engine: FakeEngine, url: str
) -> None:
    answer_config(engine, partitions_off())
    html = page(operator_client, url)
    tabs = bar(html)
    assert f'href="{KNOWLEDGE}"' not in tabs  # a span.disabled, not an <a>
    assert (
        f'<span class="disabled" aria-disabled="true" title="{PARTITIONS_OFF_REASON}" '
        'aria-current="page">Knowledge</span>'
    ) in tabs
    assert '<div class="panel unavailable">' in html
    assert paths(engine) == SHELL_READS
    assert not any(route in p for p in paths(engine) for route in KNOWLEDGE_ROUTES)
    assert NOTICE_START not in html
    assert subnav(html) is None


def test_lite_knowledge_reason_names_the_pipeline(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, partitions_off(pipeline="lite"))
    html = page(operator_client, KNOWLEDGE)
    assert (
        '<span class="disabled" aria-disabled="true" title="Needs the partitions module, which '
        'needs the structured pipeline (pipeline: structured; this story runs lite)" '
        'aria-current="page">Knowledge</span>'
    ) in bar(html)
    assert paths(engine) == SHELL_READS


# --- the landing ----------------------------------------------------------------------------------


def test_knowledge_redirects_to_the_first_registered_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured_v2())
    response = operator_client.get(f"{KNOWLEDGE}?character=X&as_of=3")
    assert response.status_code == 302
    assert response["Location"] == EXPOSURES_URL  # the query string dropped
    assert paths(engine) == SHELL_READS  # after the shell's two reads and the gate
    head = operator_client.head(KNOWLEDGE)
    assert head.status_code == 302
    assert head["Location"] == EXPOSURES_URL


def test_knowledge_with_no_sections_renders_the_shell(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured_v2())
    with override_settings(ROOT_URLCONF=NO_SECTIONS):
        html = page(operator_client, KNOWLEDGE)
    assert NOTICE in html
    assert EMPTY in html
    assert subnav(html) is None
    assert "<h2>" not in html
    assert f'<a class="active" aria-current="page" href="{KNOWLEDGE}">Knowledge</a>' in bar(html)
    # The shell calls no Engine route beyond its two reads.
    assert paths(engine) == SHELL_READS


def test_subnav_marks_the_current_section(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, structured_v2())
    html = page(operator_client, EXPOSURES_URL)
    assert (
        subnav(html)
        == f'<a class="active" aria-current="page" href="{EXPOSURES_URL}">Exposures</a>'
    )
    assert "<h2>Exposures</h2>" in html
    assert html.index("<h2>Exposures</h2>") < html.index(NOTICE_START)
    assert NOTICE in html
    # The tab bar's Knowledge link is the landing itself (the redirect picks the section).
    assert f'<a class="active" aria-current="page" href="{KNOWLEDGE}">Knowledge</a>' in bar(html)
    assert paths(engine) == SHELL_READS


def test_greyed_knowledge_does_not_redirect(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, partitions_off())
    response = operator_client.get(KNOWLEDGE)
    assert response.status_code == 200
    html = response.content.decode()
    assert '<div class="panel unavailable">' in html
    assert NOTICE_START not in html
    assert subnav(html) is None


def test_a_section_reads_409_module_off_renders_the_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured_v2())
    engine.respond(
        "GET",
        "/stories/{story_id}/inspect/exposures",
        409,
        json=error_json("module_off", module="partitions"),
    )
    html = page(operator_client, PROBE_URL)
    assert '<div class="panel unavailable">' in html
    assert "The story's configuration changed since this page loaded" in html
    assert NOTICE_START not in html
    assert subnav(html) is None


def test_nested_belief_notice_links_the_engine_request(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured_v2())
    html = page(operator_client, EXPOSURES_URL)
    link = '<a href="https://github.com/nathanpond/Epistrel/issues/606">nathanpond/Epistrel#606</a>'
    assert link in html  # same tab: no target
    assert html.count(NOTICE_START) == 1

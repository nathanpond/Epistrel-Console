"""The World and Effects tabs' shells (#239, D3): their place in the bar, the World landing's
redirect to the first open section, the sub-navigation (greyed sections keep their reasons), the
current-section rule, a greyed section's zero-read short-circuit, the empty Effects shell, and the
Knowledge sub-navigation left as it was.

The section tests run on ``tests.inspect.urls_world_stub_sections`` (stub ``inspect:states`` and
``inspect:clock``) and ``tests.inspect.urls_world_states_only`` (only the stub States)."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.inspect import EFFECTS_SECTIONS, WORLD_SECTIONS, Tab, knowledge_sections, sections
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.inspect import urls_world_stub_sections as stubs
from tests.inspect.helpers import EFFECTIVE, STORY, STORY_PATH, structured_v2, structured_v3
from tests.play.helpers import story_json

pytestmark = pytest.mark.django_db

INSPECT = f"/stories/{STORY}/inspect/"
WORLD = f"{INSPECT}world/"
EFFECTS = f"{INSPECT}effects/"
STATES_URL = f"{WORLD}states/"
CLOCK_URL = f"{WORLD}clock/"
PROBE_URL = f"{WORLD}state/probe/"
OTHER_URL = f"{WORLD}other/"
SHELL_READS = [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
STUB_SECTIONS = "tests.inspect.urls_world_stub_sections"
STATES_ONLY = "tests.inspect.urls_world_states_only"
STATES_OFF_TITLE = "Needs states.level at prompt_only or above (this story runs off)"
STATES_OFF_PANEL = (
    '<p class="notice unavailable">Needs <code>states.level</code> at <code>prompt_only</code> or '
    "above (this story runs <code>off</code>)"
)
GREYED_STATES = f'<span class="disabled" aria-disabled="true" title="{STATES_OFF_TITLE}"'
WORLD_EMPTY = '<p class="none">No World sections are available yet.</p>'
EFFECTS_EMPTY = '<p class="none">No Effects sections are available yet.</p>'
# The Knowledge sub-navigation (#189) as rendered on main before #239 (all three sections
# registered, Beliefs current): it keeps its own inline nav, not _subtabs.html.
KNOWLEDGE_GOLDEN = (
    '<nav class="subtabs" aria-label="Knowledge sections">'
    f'<a class="active" aria-current="page" href="{INSPECT}knowledge/beliefs/">Beliefs</a>'
    f'<a href="{INSPECT}knowledge/divergences/">Divergences</a>'
    f'<a href="{INSPECT}knowledge/exposures/">Exposures</a></nav>\n'
    "<h2>Beliefs</h2>\n"
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
    stubs.BUILT.clear()
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF=STUB_SECTIONS):
        yield fake


def answer_config(engine: FakeEngine, body: dict[str, Any]) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=body)


def states_off() -> dict[str, Any]:
    return structured_v3(states__level="off")


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


def subnav(html: str, label: str = "World sections") -> str | None:
    found = re.search(rf'<nav class="subtabs" aria-label="{label}">(.*?)</nav>', html)
    return found.group(1) if found is not None else None


# --- the bar --------------------------------------------------------------------------------------


def test_world_and_effects_tabs_sit_after_knowledge(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, states_off())  # a section greyed: the tabs themselves never are
    tabs = bar(page(operator_client, INSPECT))
    labels = re.findall(r">(\w+)</(?:a|span)>", tabs)
    assert labels[labels.index("Knowledge") + 1 : labels.index("Knowledge") + 4] == [
        "World",
        "Effects",
        "Predicates",
    ]
    assert f'<a href="{WORLD}">World</a>' in tabs
    assert f'<a href="{EFFECTS}">Effects</a>' in tabs
    assert "disabled" not in tabs
    assert [tab.value for tab in Tab][5:7] == ["world", "effects"]


def test_section_registries_are_d3s_table() -> None:
    assert WORLD_SECTIONS == (
        ("inspect:states", "States", "states", "prompt_only"),
        ("inspect:state-proposals", "Proposed States", "states", "prompt_only"),
        ("inspect:clock", "Clock", None, None),
        ("inspect:scheduled-events", "Scheduled events", None, None),
        ("inspect:external-values", "External values", None, None),
    )
    assert EFFECTS_SECTIONS == (
        ("inspect:reconciliation", "Reconciliation", None, None),
        ("inspect:effects-trail", "Effects trail", "states", "on"),
    )
    with pytest.raises(KeyError):
        sections(Tab.KNOWLEDGE, STORY, None, None)


# --- the landing ----------------------------------------------------------------------------------


def test_world_redirects_to_the_first_open_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured_v3())
    response = operator_client.get(f"{WORLD}?viewpoint=omniscient&as_of=3")
    assert response.status_code == 302
    assert response["Location"] == STATES_URL  # the query string dropped
    assert paths(engine) == SHELL_READS
    assert stubs.BUILT == []
    head = operator_client.head(WORLD)
    assert (head.status_code, head["Location"]) == (302, STATES_URL)


def test_world_skips_a_greyed_first_section(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, states_off())
    response = operator_client.get(WORLD)
    assert response.status_code == 302
    assert response["Location"] == CLOCK_URL
    assert paths(engine) == SHELL_READS


def test_world_with_no_open_section_renders_the_shell(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, states_off())
    with override_settings(ROOT_URLCONF=STATES_ONLY):
        html = page(operator_client, WORLD)
    assert subnav(html) == f"{GREYED_STATES}>States</span>"
    assert WORLD_EMPTY in html
    assert "<h2>" not in html
    assert f'<a class="active" aria-current="page" href="{WORLD}">World</a>' in bar(html)
    assert paths(engine) == SHELL_READS
    assert stubs.BUILT == []


def test_world_shell_without_sections(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, structured_v3())
    with override_settings(ROOT_URLCONF="tests.inspect.urls_world_no_sections"):
        html = page(operator_client, WORLD)  # no section registered: no redirect
    assert subnav(html) is None
    assert WORLD_EMPTY in html
    assert paths(engine) == SHELL_READS


def test_effects_shell_without_sections(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, structured_v3())
    html = page(operator_client, EFFECTS)
    assert EFFECTS_EMPTY in html
    assert subnav(html, "Effects sections") is None
    assert "<h2>" not in html
    assert "<title>Inspect · Effects · Harbour</title>" in html
    assert f'<a class="active" aria-current="page" href="{EFFECTS}">Effects</a>' in bar(html)
    assert paths(engine) == SHELL_READS


# --- a greyed section -----------------------------------------------------------------------------


def test_greyed_section_makes_no_section_read(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, states_off())
    html = page(operator_client, STATES_URL)
    assert STATES_OFF_PANEL in html
    assert f'<a href="/stories/{STORY}/config/">Change it on the story' in html
    assert "<h2>States</h2>" in html
    assert (
        subnav(html)
        == f'{GREYED_STATES} aria-current="page">States</span><a href="{CLOCK_URL}">Clock</a>'
    )
    assert WORLD_EMPTY not in html
    assert stubs.BUILT == []  # the section's build (its read) never ran
    # Complement: the fake's call log holds only the shell's two reads.
    assert paths(engine) == SHELL_READS


def test_a_page_below_a_greyed_section_makes_no_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, states_off())
    html = page(operator_client, PROBE_URL)
    assert STATES_OFF_PANEL in html
    assert stubs.BUILT == []
    assert paths(engine) == SHELL_READS


def test_an_open_section_calls_build(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, states_off())
    html = page(operator_client, CLOCK_URL)
    assert stubs.BUILT == ["inspect:clock"]
    assert "<h2>Clock</h2>" in html
    assert "notice unavailable" not in html
    assert WORLD_EMPTY in html  # the stub fills no world_content
    assert "<title>Inspect · World · Harbour</title>" in html


# --- the sub-navigation ---------------------------------------------------------------------------


def test_subnav_marks_the_current_section(operator_client: Client, engine: FakeEngine) -> None:
    answer_config(engine, structured_v3())
    html = page(operator_client, STATES_URL)
    assert subnav(html) == (
        f'<a class="active" aria-current="page" href="{STATES_URL}">States</a>'
        f'<a href="{CLOCK_URL}">Clock</a>'
    )
    assert "<h2>States</h2>" in html
    assert html.index("<h2>States</h2>") > html.index('aria-label="World sections"')
    assert stubs.BUILT == ["inspect:states"]
    # Complement: a page below a section passing current="inspect:states" marks States …
    probe = page(operator_client, PROBE_URL)
    assert subnav(probe) == subnav(html)
    assert "<h2>States</h2>" in probe
    # … and one passing nothing (its URL name is no section) marks none.
    other = page(operator_client, OTHER_URL)
    assert subnav(other) == f'<a href="{STATES_URL}">States</a><a href="{CLOCK_URL}">Clock</a>'
    assert 'aria-current="page" href="/stories' not in (subnav(other) or "")
    assert "<h2>" not in other
    assert stubs.BUILT == ["inspect:states", "inspect:state-probe", "inspect:world-other"]


def test_subtabs_partial_renders_nothing_without_sections() -> None:
    html = render_to_string(
        "console/inspect/_subtabs.html", {"sections": [], "label": "World sections"}
    )
    assert html.strip() == ""


def test_knowledge_unchanged(operator: Any) -> None:
    with override_settings(ROOT_URLCONF="epistrel_console.urls"):
        links = knowledge_sections(STORY, "beliefs")
        html = render_to_string(
            "console/inspect/knowledge.html",
            {
                "knowledge_sections": links,
                "tabs": [],
                "story_id": STORY,
                "tab": Tab.KNOWLEDGE,
            },
        )
    start = html.index('<nav class="subtabs"')
    assert html[start : start + len(KNOWLEDGE_GOLDEN)] == KNOWLEDGE_GOLDEN
    assert all(link.unavailable is None for link in links)
    assert [link.name for link in links] == ["beliefs", "divergences", "exposures"]


def test_an_unread_config_greys_a_gated_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_config(engine, structured_v2())  # an M5-shaped config: the States gate reads unread
    html = page(operator_client, STATES_URL)
    assert "Couldn&#x27;t read this story&#x27;s configuration — reload" in html
    assert stubs.BUILT == []
    assert paths(engine) == SHELL_READS

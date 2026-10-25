"""Browse engine profiles, their versions and schema-described settings (#78)."""

import copy
import json
import re
from collections.abc import Callable, Iterator
from html import unescape
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console import profile_schema
from console.engine.client import EngineClient
from console.engine.models import ProfileSchema
from console.engine_access import engine_client_for
from console.profile_schema import ABSENT, settings_view
from console.views import profiles as profile_views
from console.views import stories as story_views
from tests.fakes.engine import FakeEngine, recorded_schema
from tests.fakes.ownership import answer_one_relation, answer_relations, shows_panel
from tests.fixtures import engine_fixture
from tests.ratings.helpers import calls, error, text

pytestmark = pytest.mark.django_db

LITE = "00000000000000000000PRF001"
OTHER = "01JPRF00000000000000000002"
LIST = "/engine-profiles"
ITEM = "/engine-profiles/{profile_id}"
VERSIONS = "/engine-profiles/{profile_id}/versions"
SCHEMA = "/engine-profiles/schema"
SETTINGS = "/admin/settings"
STORIES = "/stories"
STORY = "/stories/{story_id}"
URL = "/profiles/"
DETAIL = f"/profiles/{LITE}/"
SCHEMA_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "profile_schema.json"


def schema_body() -> dict[str, Any]:
    """The real ``GET /engine-profiles/schema`` answer, captured from the Engine."""
    body: dict[str, Any] = json.loads(SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    return body


def schema_model(body: dict[str, Any] | None = None) -> ProfileSchema:
    parsed = ProfileSchema.from_json(body if body is not None else schema_body())
    assert parsed is not None
    return parsed


def profile(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_engine_profile"))
    return {**body, **changes}


def deployment(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_deployment_settings"))
    return {**body, **changes}


def page(*items: dict[str, Any], next_cursor: str | None = None) -> dict[str, Any]:
    return {"items": list(items), "next_cursor": next_cursor}


def widened_schema() -> dict[str, Any]:
    """The recorded schema with ``EngineProfile.settings`` open to any key: a later Engine's
    profile (a module the recorded schema does not know)."""
    doc = recorded_schema()
    doc["components"]["schemas"]["EngineProfile"]["properties"]["settings"] = {
        "type": "object",
        "additionalProperties": True,
    }
    return doc


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    profile_schema.clear_schema_cache()
    yield
    profile_schema.clear_schema_cache()


@pytest.fixture
def make_engine(
    monkeypatch: pytest.MonkeyPatch, operator: Any
) -> Iterator[Callable[[dict[str, Any] | None], FakeEngine]]:
    """A fake Engine (optionally built from another schema) behind the profile and story
    views, through the real recording client."""

    def make(doc: dict[str, Any] | None = None) -> FakeEngine:
        fake = FakeEngine(doc)
        answer_relations(fake)  # #79: the profile page reads its relations

        def client_with_fake(request: HttpRequest) -> EngineClient:
            client = engine_client_for(request)
            client._transport = fake.transport
            return client

        monkeypatch.setattr(profile_views, "engine_client_for", client_with_fake)
        monkeypatch.setattr(story_views, "engine_client_for", client_with_fake)
        return fake

    with override_settings(ENGINE_URL="http://engine.test"):
        yield make


@pytest.fixture
def engine(make_engine: Callable[[dict[str, Any] | None], FakeEngine]) -> FakeEngine:
    fake = make_engine(None)
    fake.respond("GET", SETTINGS, 200, json=deployment())
    fake.respond("GET", SCHEMA, 200, json=schema_body())
    fake.respond("GET", ITEM, 200, json=profile())
    return fake


def get(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def params(engine: FakeEngine, path: str = LIST) -> list[tuple[str, str]]:
    (listed,) = calls(engine, "GET", path)
    return list(listed.url.params.multi_items())


def row_html(html: str, row_id: str) -> str:
    match = re.search(rf'<tr id="{re.escape(row_id)}"[^>]*>(.*?)</tr>', html, re.S)
    assert match, f"no row {row_id}"
    return match.group(1)


def cells(html: str, row_id: str) -> list[str]:
    return [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html(html, row_id), re.S)]


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


# --- list ---------------------------------------------------------------------------------------


def test_the_list_shows_each_profile_and_marks_the_deployment_default(
    operator_client: Client, engine: FakeEngine
) -> None:
    other = profile(id=OTHER, name="Mine", preset=False, owner_principal="alice", current_version=3)
    engine.respond("GET", LIST, 200, json=page(profile(), other))
    html = get(operator_client, URL)
    lite = cells(html, f"profile-{LITE}")
    assert lite == ["Lite", LITE, "2", "preset", "default", "system", "—"]
    mine = cells(html, f"profile-{OTHER}")
    assert mine == ["Mine", OTHER, "3", "—", "—", "alice", "—"]
    assert f'href="/profiles/{LITE}/"' in html
    assert params(engine) == [("limit", "50")]


def test_the_default_marker_follows_the_deployment_setting(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SETTINGS, 200, json=deployment(default_engine_profile_id=OTHER))
    other = profile(id=OTHER, name="Mine", preset=False)
    engine.respond("GET", LIST, 200, json=page(profile(), other))
    html = get(operator_client, URL)
    assert "default" not in cells(html, f"profile-{LITE}")
    assert "default" in cells(html, f"profile-{OTHER}")


def test_without_the_deployment_settings_the_list_warns_and_marks_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SETTINGS, 503, json=error("engine_unavailable"))
    engine.respond("GET", LIST, 200, json=page(profile()))
    html = get(operator_client, URL)
    assert "Couldn&#x27;t read the deployment default" in html
    assert "default" not in cells(html, f"profile-{LITE}")


def test_the_filters_are_sent_to_the_engine(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json=page())
    get(operator_client, f"{URL}?owner=alice&relation=editor&relation_principal=bob&deleted=1")
    assert params(engine) == [
        ("limit", "50"),
        ("owner", "alice"),
        ("relation", "editor:bob"),
        ("include_deleted", "true"),
    ]


def test_a_relation_without_a_principal_is_sent_as_name_colon(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 200, json=page())
    get(operator_client, f"{URL}?relation=viewer")
    assert ("relation", "viewer:") in params(engine)


@pytest.mark.parametrize(
    "query",
    ["owner=a%0Ab", "relation=bad%20name", "relation_principal=bob"],
)
def test_an_invalid_filter_makes_no_engine_call(
    operator_client: Client, engine: FakeEngine, query: str
) -> None:
    html = get(operator_client, f"{URL}?{query}")
    assert 'class="errorlist' in html
    assert engine.requests == []


def test_deleted_profiles_are_listed_on_request_and_muted(
    operator_client: Client, engine: FakeEngine
) -> None:
    gone = profile(id=OTHER, name="Old", preset=False, deleted_at="2026-10-21T08:00:00Z")
    engine.respond("GET", LIST, 200, json=page(profile(), gone))
    html = get(operator_client, f"{URL}?deleted=1")
    assert re.search(rf'<tr id="profile-{OTHER}" class="deleted muted">', html)
    assert "2026-10-21 08:00:00 UTC" in cells(html, f"profile-{OTHER}")[-1]
    assert 'name="deleted" value="1" checked' in html


def test_an_empty_list_offers_show_deleted(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json=page())
    engine.respond("GET", ITEM, 200, json=profile(deleted_at="2026-10-21T08:00:00Z"))
    html = get(operator_client, URL)
    assert "No profiles" in text(html)
    assert "Deleted profiles are hidden — Show deleted" in text(html)
    assert 'href="?deleted=1"' in html
    # The default is not on the page and is deleted: say so.
    assert f"The deployment default {LITE} is deleted" in text(html)


def test_the_next_page_keeps_the_filters_and_apply_drops_the_cursor(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 200, json=page(profile(), next_cursor="c2"))
    html = get(operator_client, f"{URL}?owner=system")
    link = re.search(r'<a class="next-page" href="\?([^"]+)"', html)
    assert link
    assert unescape(link.group(1)) == "owner=system&cursor=c2"
    assert 'name="cursor"' not in html  # Apply submits the filters only

    engine.reset()
    get(operator_client, f"{URL}?{unescape(link.group(1))}")
    assert ("cursor", "c2") in params(engine)
    assert ("owner", "system") in params(engine)


def test_a_stale_cursor_shows_the_panel_and_start_over(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 422, json=engine_fixture("error_cursor_invalid"))
    html = get(operator_client, f"{URL}?owner=system&cursor=stale")
    assert "cursor_invalid" in html
    assert '<a class="start-over" href="?owner=system">Start over</a>' in html


# --- detail -------------------------------------------------------------------------------------


def test_every_schema_module_renders_as_a_block_in_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=profile())
    html = get(operator_client, DETAIL)
    modules = list(schema_body()["schema"]["properties"])
    assert re.findall(r'<section class="settings-block" id="block-([^"]+)">', html) == modules
    assert "Version 2 of 2" in text(html)
    assert "Deployment default" in text(html)
    assert "preset" in text(html)


def test_the_delivered_modules_table_lists_every_delivered_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=profile())
    html = get(operator_client, DETAIL)
    delivered = schema_body()["delivered"]
    assert re.findall(r'<tr id="delivered-([^"]+)">', html) == list(delivered)
    assert cells(html, "delivered-testimony") == ["testimony", "model, defaulted"]


def test_an_omitted_setting_shows_not_set_and_the_deployment_default(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=profile())
    html = get(operator_client, DETAIL)
    # Lite leaves the episodic retrieval out; the deployment default (k: 8) shows beside it.
    row = cells(html, "f-memory-episodic-k")
    assert row[1:4] == ["not set", "8", "8"]
    assert 'class="muted not-set"' in row_html(html, "f-memory-episodic-k")
    assert "behavioural" in row[4]
    assert "quality-relevant" in row[4]
    # A set value, and an explicit null distinct from "not set".
    assert cells(html, "f-memory-rolling_summary-window_messages")[1] == "30"
    assert "<code>null</code>" in row_html(html, "f-budgets-model_calls")


def test_badges_emphasise_data_producing_interpretive_and_relevant(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=profile())
    html = get(operator_client, DETAIL)
    pipeline = row_html(html, "f-pipeline")
    assert '<span class="tag tag-strong">data-producing</span>' in pipeline
    assert '<span class="tag">prospective</span>' in pipeline
    perception = row_html(html, "f-perception-level")
    assert '<span class="tag tag-strong">interpretive</span>' in perception
    pins = row_html(html, "f-pins-enabled")
    assert '<span class="tag">quality-irrelevant</span>' in pins


def test_map_rows_cover_keys_from_the_version_and_the_deployment_defaults(
    operator_client: Client, engine: FakeEngine
) -> None:
    settings = profile()["settings"]
    settings["models"]["purposes"] = {"generation": {"model": "big"}}
    engine.respond("GET", ITEM, 200, json=profile(settings=settings))
    html = get(operator_client, DETAIL)
    assert cells(html, "f-models-purposes-generation-model")[1] == "big"
    # guardrail_check is in the deployment defaults only.
    assert cells(html, "f-models-purposes-guardrail_check-model")[1:3] == ["not set", "null"]
    # A map entry takes the map path's tags.
    assert "behavioural" in cells(html, "f-models-purposes-generation-model")[4]


def test_a_module_a_later_engine_delivers_renders_without_console_changes(
    operator_client: Client, make_engine: Callable[[dict[str, Any] | None], FakeEngine]
) -> None:
    """Complement: a schema with an extra ``future_x`` module renders it, and a setting the
    schema does not describe goes to "Other settings"."""
    engine = make_engine(widened_schema())
    body = schema_body()
    body["schema"]["properties"]["future_x"] = {
        "type": "object",
        "title": "Future X",
        "properties": {"mode": {"type": "string", "enum": ["a", "b"], "default": "a"}},
    }
    body["tags"]["future_x.mode"] = {
        "tag": "data_producing",
        "class": "interpretive",
        "quality": "relevant",
    }
    body["delivered"]["future_x"] = ["on"]
    engine.respond("GET", SCHEMA, 200, json=body)
    engine.respond("GET", SETTINGS, 200, json=deployment())
    settings = {**profile()["settings"], "future_x": {"mode": "b"}, "mystery": {"deep": [1]}}
    engine.respond("GET", ITEM, 200, json=profile(settings=settings))
    html = get(operator_client, DETAIL)
    assert '<section class="settings-block" id="block-future_x">' in html
    row = cells(html, "f-future_x-mode")
    assert row[1:4] == ["b", "—", "a"]
    assert "one of: a, b" in row[0]
    assert "data-producing" in row[4]
    other = html.split('<section class="other-settings">', 1)[1]
    assert "<code>mystery</code>" in other
    assert "future_x" not in other
    assert cells(html, "delivered-future_x") == ["future_x", "on"]


def test_a_version_query_reads_that_version(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=profile(version=1))
    html = get(operator_client, f"{DETAIL}?version=1")
    (read,) = calls(engine, "GET", f"/engine-profiles/{LITE}")
    assert read.url.params["version"] == "1"
    assert "Version 1 of 2" in text(html)
    assert f'<a class="latest" href="/profiles/{LITE}/">latest</a>' in html
    assert f"/profiles/{LITE}/versions/?viewed=1" in html


def test_an_unknown_version_shows_the_engines_404_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 404, json=error("not_found", "no such version"))
    html = get(operator_client, f"{DETAIL}?version=9")
    assert "not_found" in html
    assert 'class="engine-error"' in html


@pytest.mark.parametrize("raw", ["abc", "0", "-2", "1.5"])
def test_an_invalid_version_is_ignored_with_a_flash(
    operator_client: Client, engine: FakeEngine, raw: str
) -> None:
    engine.respond("GET", ITEM, 200, json=profile())
    response = operator_client.get(f"{DETAIL}?version={raw}")
    assert response.status_code == 200
    (read,) = calls(engine, "GET", f"/engine-profiles/{LITE}")
    assert "version" not in read.url.params
    assert "Ignored invalid version" in response.content.decode()


def test_a_deleted_profile_shows_its_banner(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=profile(deleted_at="2026-10-21T08:00:00Z"))
    html = get(operator_client, DETAIL)
    assert 'class="banner banner-warning deleted-banner"' in html
    assert "2026-10-21 08:00:00 UTC" in html
    assert "Deployment default" in text(html)  # kept for a deleted default


def test_the_detail_page_links_the_stories_using_it(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=profile())
    html = get(operator_client, DETAIL)
    assert f'<a class="stories-using" href="/stories/?profile={LITE}">' in html


def test_without_the_schema_settings_show_raw(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", SCHEMA, 503, json=error("engine_unavailable"))
    engine.respond("GET", ITEM, 200, json=profile())
    html = get(operator_client, DETAIL)
    assert "Couldn&#x27;t read the profile schema — settings shown raw" in html
    assert 'class="settings-block"' not in html
    other = html.split('<section class="other-settings">', 1)[1]
    for key in profile()["settings"]:
        assert f"<code>{key}</code>" in other
    assert "Version 2 of 2" in text(html)


def test_without_the_deployment_settings_defaults_show_a_dash(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SETTINGS, 503, json=error("engine_unavailable"))
    engine.respond("GET", ITEM, 200, json=profile())
    html = get(operator_client, DETAIL)
    assert "Couldn&#x27;t read the deployment default" in html
    assert cells(html, "f-memory-episodic-k")[2] == "—"
    assert "Deployment default" not in re.sub(r"<th>Deployment default</th>", "", html)


def test_the_schema_is_cached_and_a_failure_is_not(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SCHEMA, 503, json=error("engine_unavailable"))
    engine.respond("GET", ITEM, 200, json=profile())
    get(operator_client, DETAIL)
    engine.respond("GET", SCHEMA, 200, json=schema_body())
    get(operator_client, DETAIL)
    get(operator_client, DETAIL)
    assert len(calls(engine, "GET", SCHEMA)) == 2  # the failure was retried, then cached


# --- versions -----------------------------------------------------------------------------------


def test_the_versions_list_is_newest_first(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=profile())
    engine.respond("GET", VERSIONS, 200, json=engine_fixture("list_engine_profile_versions"))
    html = get(operator_client, f"/profiles/{LITE}/versions/?viewed=1")
    assert re.findall(r'<tr id="version-(\d+)"', html) == ["2", "1"]
    assert cells(html, "version-2")[0] == "2 latest"
    assert cells(html, "version-2")[2] == "console:nate"
    assert '<tr id="version-1" class="viewed" aria-current="true">' in html
    assert f'href="/profiles/{LITE}/?version=1"' in html


def test_the_versions_page_highlights_nothing_when_opened_directly(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=profile())
    engine.respond("GET", VERSIONS, 200, json=engine_fixture("list_engine_profile_versions"))
    html = get(operator_client, f"/profiles/{LITE}/versions/")
    assert 'class="viewed"' not in html


# --- stories using a profile --------------------------------------------------------------------


def _story(story_id: str, name: str, profile_id: str, version: int) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_story"))
    body.update(id=story_id, name=name, engine_profile={"id": profile_id, "version": version})
    return body


def test_the_stories_filter_narrows_by_profile_and_counts_failed_reads(
    operator_client: Client, engine: FakeEngine
) -> None:
    summaries = engine_fixture("list_stories")["items"]
    first = summaries[0]
    using = {**first, "id": "01JB2Z8Y5Q3W6K9M1N4P7R0T2A", "name": "Uses Lite"}
    other = {**first, "id": "01JB2Z8Y5Q3W6K9M1N4P7R0T2B", "name": "Uses mine"}
    broken = {**first, "id": "01JB2Z8Y5Q3W6K9M1N4P7R0T2C", "name": "Unreadable"}
    engine.respond("GET", STORIES, 200, json={"items": [using, other, broken]})
    engine.respond("GET", ITEM, 200, json=profile())
    bodies = {
        using["id"]: _story(using["id"], "Uses Lite", LITE, 1),
        other["id"]: _story(other["id"], "Uses mine", OTHER, 3),
    }

    def answer(request: httpx.Request) -> httpx.Response:
        story_id = request.url.path.rsplit("/", 1)[1]
        if story_id in bodies:
            return httpx.Response(200, json=bodies[story_id])
        return httpx.Response(503, json=error("engine_unavailable"))

    engine.side_effect("GET", STORY, answer)
    html = get(operator_client, f"/stories/?profile={LITE}")
    assert "Showing stories using Lite" in text(html)
    assert "Uses Lite" in html
    assert "Uses mine" not in html.split("couldn")[0]
    assert '<td class="profile-version">1</td>' in html
    assert "1 story couldn't be checked" in text(html)
    unchecked = html.split('<details class="unchecked">', 1)[1]
    assert "Unreadable" in unchecked
    assert broken["id"] in unchecked
    assert 'name="profile" value="00000000000000000000PRF001"' in html  # the status filter keeps it
    assert '<a class="clear-profile" href="?">Clear</a>' in html


def test_the_stories_filter_ands_the_status_and_shows_the_empty_state(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORIES, 200, json={"items": []})
    engine.respond("GET", ITEM, 404, json=error("not_found"))
    html = get(operator_client, f"/stories/?profile={OTHER}&status=archived")
    assert ("status", "archived") in params(engine, STORIES)
    assert "No stories use this profile." in html
    assert f"Showing stories using {OTHER}" in text(html)
    assert "Couldn&#x27;t read this profile" in html
    assert '<a class="clear-profile" href="?status=archived">Clear</a>' in html


def test_the_story_list_without_the_filter_has_no_profile_column(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORIES, 200, json={"items": []})
    html = get(operator_client, "/stories/")
    assert "Profile version" not in html
    assert calls(engine, "GET", f"/engine-profiles/{LITE}") == []


# --- the walker -----------------------------------------------------------------------------------


def _rows(view: Any) -> dict[str, Any]:
    return {row.path: row for block in view.blocks for row in block.rows}


def test_the_walker_handles_any_of_mismatches_depth_and_refs() -> None:
    deep: dict[str, Any] = {"type": "string"}
    for _ in range(10):
        deep = {"type": "object", "properties": {"n": deep}}
    body = {
        "schema": {
            "type": "object",
            "properties": {
                "choice": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
                "count": {"type": "integer", "minimum": 1.0, "maximum": 50.0, "title": "Count"},
                "ref": {"$ref": "#/$defs/x"},
                "deep": deep,
            },
        },
        "tags": {"count": {"tag": "behavioral", "class": "prospective", "quality": "relevant"}},
        "delivered": {},
        "purposes": ["generation"],
    }
    settings = {"choice": "x", "count": "many", "ref": {"a": 1}, "deep": {}}
    view = settings_view(schema_model(body), settings, {})
    rows = _rows(view)
    assert rows["choice"].value.kind == "json"
    assert rows["choice"].value.text == '"x"'
    assert rows["count"].mismatch
    assert rows["count"].value.text == '"many"'
    assert rows["count"].constraints == "min 1 · max 50"
    assert "ref" not in rows
    assert [(o.path, o.value) for o in view.other] == [("ref", {"a": 1})]
    deepest = max(row.depth for row in rows.values())
    assert deepest == profile_schema.MAX_DEPTH
    assert rows["deep" + ".n" * 8].kind == "leaf"
    assert rows["deep"].kind == "group"
    assert rows["deep"].value.kind == "blank"
    assert rows["deep.n"].untagged is False  # a group is never "untagged"
    assert rows["deep" + ".n" * 8].untagged
    assert view.purposes == ("generation",)


def test_unwalked_tags_go_to_more() -> None:
    body = schema_body()
    body["tags"]["nowhere.at_all"] = {
        "tag": "behavioral",
        "class": "prospective",
        "quality": "irrelevant",
    }
    view = settings_view(schema_model(body), {}, None)
    assert [path for path, _ in view.unmatched_tags] == ["nowhere.at_all"]
    assert view.has_more
    rows = _rows(view)
    assert rows["memory.episodic.k"].deployment.kind == "absent"  # defaults unknown


def test_absent_is_not_none() -> None:
    assert ABSENT is not None
    assert repr(ABSENT) == "ABSENT"


def test_the_detail_page_includes_the_ownership_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#79 fills #78's slot: the panel sits between the stories link and the settings."""
    engine.respond("GET", ITEM, 200, json=profile())
    answer_one_relation(engine, "engine_profile", LITE)
    html = get(operator_client, DETAIL)
    assert shows_panel(html, "engine_profile", LITE)
    assert html.index('class="stories-using"') < html.index('id="ownership"')
    assert html.index('id="ownership"') < html.index("<h2>Settings</h2>")

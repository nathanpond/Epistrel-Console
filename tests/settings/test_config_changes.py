"""A story's configuration changes on its settings page (#54): grouped rows, filters, paging."""

import copy
import re
from typing import Any

from django.test import Client

from console.config_changes import setting_filter
from console.engine_api_check import RECORDED
from console.story_settings import SETTABLE, SUPERSEDED, setting_groups
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.settings.helpers import STORY, URL, answer_page, calls, section

CHANGES_PATH = f"/stories/{STORY}/config-changes"


def _changes_params(engine: FakeEngine) -> dict[str, str]:
    [request] = calls(engine, "GET", CHANGES_PATH)
    return dict(request.url.params)


def _page(
    client: Client, engine: FakeEngine, query: dict[str, str] | None = None, **page: Any
) -> str:
    changes = copy.deepcopy(engine_fixture("list_story_config_changes"))
    changes.update(page)
    answer_page(engine, changes=changes)
    html: str = client.get(URL, query or {}).content.decode()
    return section(html, "config-changes")


def test_rows_are_grouped_by_revision_newest_first(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)

    headers = re.findall(r'<tr class="revision">\s*<th colspan="6">Config revision (\d+)', html)
    assert headers == ["2", "1"]
    assert html.count('<tr class="change">') == 3
    assert re.search(r"<code>&quot;reject&quot;</code>\s*→\s*<code>&quot;queue&quot;</code>", html)
    assert '<a href="/operations/?operator=nate">console:nate</a>' in html
    assert _changes_params(engine) == {"limit": "50"}


def test_filters_pass_through(operator_client: Client, engine: FakeEngine) -> None:
    _page(
        operator_client,
        engine,
        {"setting": "turn_concurrency", "change_class": "prospective"},
        next=None,
    )

    assert _changes_params(engine) == {
        "limit": "50",
        "setting": "turn_concurrency",
        "class": "prospective",
    }


def test_a_policy_key_filters_by_policy(operator_client: Client, engine: FakeEngine) -> None:
    _page(operator_client, engine, {"prefixed": "policy:01M5R8954SQXFAN5N439FEG48J"}, next=None)

    assert _changes_params(engine)["setting"] == "policy:01M5R8954SQXFAN5N439FEG48J"


def test_an_invalid_filter_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine, {"setting": "not_a_setting"})

    assert calls(engine, "GET", CHANGES_PATH) == []
    assert "Select a valid choice." in html


def test_next_pages_on_with_the_filters_kept(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine, {"change_class": "prospective"})

    assert "change_class=prospective&amp;after=01M5R895CURSOR0000000000000#config-changes" in html
    assert "First page" not in html

    engine.requests.clear()
    paged = _page(
        operator_client,
        engine,
        {"change_class": "prospective", "after": "01M5R895CURSOR0000000000000"},
        next=None,
    )

    assert _changes_params(engine) == {
        "limit": "50",
        "class": "prospective",
        "after": "01M5R895CURSOR0000000000000",
    }
    assert '<a href="?change_class=prospective#config-changes">First page</a>' in paged
    assert ">Next</a>" not in paged


def test_the_setting_select_is_grouped_and_has_no_since(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)

    for title in ("Settings", "Historical", "Profile overrides", "Rating and profile"):
        assert f'<optgroup label="{title}">' in html
    assert 'name="since"' not in html


def test_the_groups_cover_the_recorded_enum() -> None:
    names = setting_filter(RECORDED, "/stories/{story_id}/config-changes").names
    groups = dict(setting_groups(names))

    grouped = [value for options in groups.values() for value, _ in options]
    assert sorted(grouped) == sorted(names)
    assert {value for value, _ in groups["Settings"]} == set(SETTABLE)
    historical = {value.split(".", 1)[0] for value, _ in groups["Historical"]}
    assert historical == set(SUPERSEDED)
    assert "Other" not in groups

"""The deployment's configuration changes (#50): filters, paging and revision groups."""

import copy
import re
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from console.config_changes import revision_groups, setting_filter, value_text
from console.engine.models import ConfigChanges
from console.engine_api_check import RECORDED
from tests.engine_panel.helpers import CHANGES, error
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

URL = "/engine/config-changes/"


def _answer(engine: FakeEngine, **changes: Any) -> dict[str, Any]:
    page: dict[str, Any] = copy.deepcopy(engine_fixture("list_admin_config_changes"))
    page.update(changes)
    engine.respond("GET", CHANGES, 200, json=page)
    return page


def _params(engine: FakeEngine) -> dict[str, str]:
    [request] = engine.requests
    return dict(request.url.params)


def test_the_list_groups_rows_by_config_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    _answer(engine)

    html = operator_client.get(URL).content.decode()

    headers = re.findall(r'<tr class="revision">\s*<th colspan="6">Config revision (\d+)', html)
    assert headers == ["3", "2", "1"]
    assert html.count('<tr class="change">') == 7
    assert re.search(r"<code>18</code>\s*→\s*<code>21</code>", html)
    assert "<td><code>minor_age_threshold</code></td>\n      <td>interpretive</td>" in html
    # The signed-in operator nate exists: his actor links to his operations.
    assert '<a href="/operations/?operator=nate">console:nate</a>' in html
    assert _params(engine) == {"limit": "50"}
    assert "Next</a>" in html  # the fixture has a next cursor
    assert "First page" not in html


def test_an_unknown_console_actor_is_plain_text(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = copy.deepcopy(engine_fixture("list_admin_config_changes"))
    for item in page["items"]:
        item["actor"] = "console:someone-else"
    page["items"][0]["actor"] = "ops-bot"
    engine.respond("GET", CHANGES, 200, json=page)

    html = operator_client.get(URL).content.decode()

    assert '<td class="actor">console:someone-else</td>' in html
    assert '<td class="actor">ops-bot</td>' in html
    assert "/operations/?operator=" not in html


def test_filters_are_passed_through(operator_client: Client, engine: FakeEngine) -> None:
    _answer(engine, next=None)

    html = operator_client.get(
        URL,
        {
            "setting": "minor_age_threshold",
            "change_class": "interpretive",
            "since": "2026-10-01T09:30",
        },
    ).content.decode()

    assert _params(engine) == {
        "limit": "50",
        "setting": "minor_age_threshold",
        "class": "interpretive",
        "since": "2026-10-01T09:30:00Z",
    }
    assert "Next</a>" not in html


def test_a_prefixed_key_is_passed_through(operator_client: Client, engine: FakeEngine) -> None:
    _answer(engine, items=[], next=None)

    html = operator_client.get(
        URL, {"prefixed": "policy:01JB2Z8Y5Q3W6K9M1N4P7R0POL"}
    ).content.decode()

    assert _params(engine)["setting"] == "policy:01JB2Z8Y5Q3W6K9M1N4P7R0POL"
    assert "No configuration changes." in html


@pytest.mark.parametrize(
    "query",
    [
        {"prefixed": "bogus"},
        {"prefixed": "story:01J"},
        {"prefixed": "policy:a b"},
        {"setting": "not_a_setting"},
        {"setting": "minor_age_threshold", "prefixed": "policy:x"},
        {"since": "yesterday"},
        {"change_class": "sometimes"},
    ],
)
def test_an_invalid_filter_is_a_form_error_with_no_call(
    operator_client: Client, engine: FakeEngine, query: dict[str, str]
) -> None:
    html = operator_client.get(URL, query).content.decode()

    assert 'class="errorlist' in html
    assert engine.requests == []


def test_paging_follows_next_and_keeps_the_filters(
    operator_client: Client, engine: FakeEngine
) -> None:
    _answer(engine)
    html = operator_client.get(URL, {"change_class": "prospective"}).content.decode()
    link = re.search(r'<a href="\?([^"]+)">Next</a>', html)
    assert link is not None
    engine.reset()
    _answer(engine, next=None)

    second = operator_client.get(f"{URL}?{link.group(1).replace('&amp;', '&')}").content.decode()

    assert _params(engine) == {"limit": "50", "class": "prospective", "after": "MDJfY3Vyc29y"}
    assert '<a href="?change_class=prospective">First page</a>' in second


def test_an_engine_error_renders_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CHANGES, 503, json=error("database_unavailable", "down"))
    html = operator_client.get(URL).content.decode()
    assert "Engine error 503 database_unavailable" in html
    assert '<form method="get"' in html


# --- the shared helpers (#54 reuses them) ------------------------------------------------------


def test_the_setting_filter_comes_from_the_recorded_schema() -> None:
    found = setting_filter(RECORDED, CHANGES)
    assert "minor_age_threshold" in found.names
    assert "lite_window_messages" in found.names  # historical keys stay filterable
    assert "profile_defaults.budgets.model_calls" in found.names
    assert found.accepts_prefixed("rating:01JB2Z8Y5Q3W6K9M1N4P7R0RAT")
    assert not found.accepts_prefixed("story:x")
    assert setting_filter({}, CHANGES).names == ()


def test_long_values_are_cut_with_the_full_value_kept() -> None:
    short = value_text(["a"])
    assert (short.short, short.truncated) == ('["a"]', False)
    long = value_text(["x" * 200])
    assert long.truncated
    assert len(long.short) == 120
    assert long.short.endswith("…")
    assert "x" * 200 in long.full


@pytest.mark.django_db
def test_revision_groups_keep_engine_order_and_split_by_revision() -> None:
    get_user_model().objects.create_user("nate")
    page = ConfigChanges.from_json(engine_fixture("list_admin_config_changes"))
    assert page is not None

    groups = revision_groups(page.items)

    assert [g.config_revision for g in groups] == [3, 2, 1]
    assert [len(g.rows) for g in groups] == [2, 4, 1]
    assert groups[0].actors == ["console:nate"]
    assert groups[0].rows[0].actor_url == "/operations/?operator=nate"

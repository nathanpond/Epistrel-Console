"""Edit the deployment's profile defaults in the schema-generated form (#84)."""

import copy
import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.test import Client

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.profile_helpers import (
    LITE,
    SETTINGS,
    STORY,
    STORY_A,
    body_of,
    fake_engine,
    field,
    fresh_cache,
    null,
    posted,
    story,
)
from tests.ratings.helpers import calls, error, text

pytestmark = pytest.mark.django_db

URL = "/engine/profile-defaults/edit/"
ROOT = "profile_defaults"
WINDOW = "memory.rolling_summary.window_messages"


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    yield from fresh_cache()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    for fake in fake_engine(monkeypatch):
        saved: dict[str, Any] = copy.deepcopy(engine_fixture("update_admin_settings"))
        fake.respond("PUT", SETTINGS, 200, json={**saved, "config_revision": 12})
        yield fake


def f(path: str) -> str:
    return field(path, ROOT)


def page(client: Client, engine: FakeEngine) -> Any:
    response = client.get(URL)
    assert response.status_code == 200
    engine.reset()
    return response


def test_the_form_is_generated_and_prefilled_from_the_settings(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.get(URL)
    html = response.content.decode()
    form = response.context["form"]
    assert form[f(WINDOW)].value() == "30"
    assert form[f("pipeline")].value() == "lite"
    assert form[f("pins.enabled")].value() == "true"
    assert form[null("budgets.model_calls", ROOT)].value() is True
    assert '"generation"' in form[f("models.purposes")].value()
    assert f'name="{f(WINDOW)}"' in html
    assert "schema default: 30" in html
    assert 'name="other_settings"' not in html
    assert [r.url.path for r in engine.requests if r.url.path.startswith("/admin")] == [SETTINGS]


def test_save_sends_only_the_changed_leaves_as_nested_objects(
    operator_client: Client, engine: FakeEngine
) -> None:
    shown = page(operator_client, engine)
    data = posted(shown, **{f(WINDOW): "24", f("repair.polish.enabled"): "false"})
    response = operator_client.post(URL, data)
    assert response.status_code == 302
    assert response["Location"] == "/engine/settings/"
    (put,) = calls(engine, "PUT", SETTINGS)
    assert body_of(put) == {
        "profile_defaults": {
            "memory": {"rolling_summary": {"window_messages": 24}},
            "repair": {"polish": {"enabled": False}},
        }
    }
    assert put.headers["Idempotency-Key"] == data["idempotency_key"]
    (flash,) = [str(m) for m in get_messages(response.wsgi_request)]
    assert flash.startswith("Saved profile defaults — config revision 12")
    rows = EngineOperation.objects.filter(kind="update_admin_settings")
    assert rows.count() == 1
    assert rows.get().outcome == "ok"


def test_the_null_checkbox_sends_null_as_a_value(
    operator_client: Client, engine: FakeEngine
) -> None:
    shown = page(operator_client, engine)
    data = posted(
        shown,
        **{null("models.generation.top_p", ROOT): "on", f("models.generation.top_p"): "0.95"},
    )
    operator_client.post(URL, data)
    (put,) = calls(engine, "PUT", SETTINGS)
    assert body_of(put) == {"profile_defaults": {"models": {"generation": {"top_p": None}}}}


def test_unticking_null_with_a_value_sends_the_value(
    operator_client: Client, engine: FakeEngine
) -> None:
    shown = page(operator_client, engine)
    data = posted(
        shown, **{null("budgets.model_calls", ROOT): None, f("budgets.model_calls"): "12"}
    )
    operator_client.post(URL, data)
    (put,) = calls(engine, "PUT", SETTINGS)
    assert body_of(put) == {"profile_defaults": {"budgets": {"model_calls": 12}}}


def test_nothing_changed_makes_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    shown = page(operator_client, engine)
    response = operator_client.post(URL, posted(shown, **{f(WINDOW): "30.0"}))
    assert response.status_code == 302
    assert [str(m) for m in get_messages(response.wsgi_request)] == [
        "No changes — nothing was sent."
    ]
    assert engine.requests == []
    assert not EngineOperation.objects.exists()


def test_clearing_a_stored_value_is_a_form_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    shown = page(operator_client, engine)
    response = operator_client.post(
        URL,
        posted(
            shown,
            **{
                f(WINDOW): "",
                null("models.generation.temperature", ROOT): None,
                f("models.generation.temperature"): "",
            },
        ),
    )
    form = response.context["form"]
    assert form.errors[f("models.generation.temperature")] == ["Tick “null” to set null"]
    assert "can't be left unset" in form.errors[f(WINDOW)][0]
    assert engine.requests == []


def test_a_value_outside_the_bounds_makes_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    shown = page(operator_client, engine)
    response = operator_client.post(URL, posted(shown, **{f(WINDOW): "0"}))
    assert response.context["form"].errors[f(WINDOW)] == ["Must be within 1\u2013500."]
    assert engine.requests == []


def test_invalid_profile_lists_profiles_and_stories_linked(
    operator_client: Client, engine: FakeEngine
) -> None:
    shown = page(operator_client, engine)
    engine.respond("GET", STORY, 200, json=story(STORY_A, "Harbour"))
    engine.respond(
        "PUT",
        SETTINGS,
        422,
        json=error(
            "invalid_profile",
            "profile versions in use could not run under these defaults",
            missing_dependencies=[],
            undelivered=[],
            profiles=[f"{LITE}@2"],
            stories=[STORY_A],
            count=3,
        ),
    )
    response = operator_client.post(URL, posted(shown, **{f("pins.enabled"): "false"}))
    html = response.content.decode()
    assert response.status_code == 200
    assert f'<a href="/profiles/{LITE}/?version=2"><code>{LITE}@2</code></a>' in html
    assert f'<a href="/stories/{STORY_A}/">Harbour</a>' in html
    assert "These defaults would leave 3 configurations unable to run" in text(html)
    row = EngineOperation.objects.get(kind="update_admin_settings")
    assert (row.outcome, row.error_code) == ("error", "invalid_profile")


def test_a_validation_error_is_placed_at_its_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    shown = page(operator_client, engine)
    engine.respond(
        "PUT",
        SETTINGS,
        422,
        json=error("validation_error", "too small", field=f"body.profile_defaults.{WINDOW}"),
    )
    response = operator_client.post(URL, posted(shown, **{f(WINDOW): "2"}))
    assert response.context["form"].errors[f(WINDOW)] == ["too small"]


def test_an_expired_page_rereads_and_writes_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    shown = page(operator_client, engine)
    response = operator_client.post(URL, posted(shown, snapshot="forged", **{f(WINDOW): "24"}))
    assert "This form expired" in response.content.decode()
    assert calls(engine, "PUT") == []


def test_the_deployment_settings_table_links_here(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", "/admin/content-ratings", 200, json=engine_fixture("list_content_ratings")
    )
    html = operator_client.get("/engine/settings/").content.decode()
    section = re.search(r'<section class="profile-defaults">.*?</section>', html, re.S)
    assert section is not None
    assert f'<a class="edit-profile-defaults" href="{URL}">Edit profile defaults</a>' in section[0]

"""Create, edit, copy and delete engine profiles, validated and impact-checked first (#84)."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.test import Client

from console import profile_schema, twostage
from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.profile_helpers import (
    COPY,
    FP_AFTER,
    FP_BEFORE,
    ITEM,
    LIST,
    LITE,
    OTHER,
    SCHEMA,
    SETTINGS,
    STORY,
    STORY_A,
    STORY_B,
    VALIDATE,
    affected,
    body_of,
    deleted_body,
    deployment,
    edit_body,
    fake_engine,
    field,
    fresh_cache,
    impact,
    low_cap_warning,
    null,
    other_profile,
    posted,
    profile,
    schema_body,
    story,
    validation,
    written,
)
from tests.ratings.helpers import calls, error, hidden, text

pytestmark = pytest.mark.django_db

NEW = "/profiles/new/"
EDIT = f"/profiles/{OTHER}/edit/"
APPLY = f"/profiles/{OTHER}/apply/"
DETAIL = f"/profiles/{OTHER}/"
WINDOW = "memory.rolling_summary.window_messages"
POLISH = "repair.polish.max_regenerations"
OTHER_PATH = f"/engine-profiles/{OTHER}"


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    yield from fresh_cache()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    for fake in fake_engine(monkeypatch):
        fake.respond("GET", ITEM, 200, json=other_profile())
        fake.respond("POST", VALIDATE, 200, json=validation())
        yield fake


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def writes(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method in ("POST", "PUT", "DELETE")]


def edit_page(client: Client, engine: FakeEngine) -> Any:
    page = client.get(EDIT)
    assert page.status_code == 200
    engine.reset()
    return page


def new_page(client: Client, engine: FakeEngine) -> Any:
    page = client.get(NEW)
    assert page.status_code == 200
    engine.reset()
    return page


# --- the form pages -------------------------------------------------------------------------------


def test_new_page_starts_unset_with_owner_prefilled(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = operator_client.get(NEW)
    html = page.content.decode()
    form = page.context["form"]
    assert not form.is_bound
    assert all(
        form[form.field_name(leaf)].value() in (None, []) for leaf in form.leaf_by_name.values()
    )
    assert form["owner"].value() == "console:nate"
    assert 'name="owner"' in html
    assert f'name="{field(WINDOW)}"' in html
    assert 'placeholder="default: 30"' in html  # the deployment's profile_defaults value
    assert "Settings not in this deployment&#x27;s schema" in html
    assert 'hx-post="/profiles/new/"' in html
    assert re.search(r'<details class="schema-group" id="f-memory">', html)  # closed: nothing set


def test_edit_page_prefills_stored_keys_owner_read_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = operator_client.get(EDIT)
    html = page.content.decode()
    assert page.context["form"][field(WINDOW)].value() == "10"
    assert page.context["form"][field(POLISH)].value() is None
    assert 'name="owner"' not in html
    assert "Owner:" in text(html)
    assert 'href="/principals/alice/"' in html
    assert re.search(r'<details class="schema-group" id="f-memory" open>', html)


def test_presets_have_edit_and_deleted_profiles_do_not(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=profile())
    html = operator_client.get(f"/profiles/{LITE}/").content.decode()
    assert f'href="/profiles/{LITE}/edit/"' in html
    assert operator_client.get(f"/profiles/{LITE}/edit/").status_code == 200
    # Lite is the deployment default: Delete is shown disabled.
    assert '<button type="button" class="danger delete" disabled>Delete</button>' in html
    assert "Deployment default — choose another default first" in html

    engine.respond("GET", ITEM, 200, json=other_profile(deleted_at="2026-10-24T09:00:00Z"))
    deleted = operator_client.get(DETAIL).content.decode()
    assert f'href="{EDIT}"' not in deleted
    assert "/delete/" not in deleted
    assert f'href="/profiles/{OTHER}/copy/?version=3"' in deleted
    assert "Deleted profiles can&#x27;t be edited — copy it to change it" in deleted
    response = operator_client.get(EDIT)
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    assert flashes(response) == ["Deleted profiles can't be edited — copy it to change it"]


def test_a_past_version_offers_edit_latest(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=other_profile(version=2))
    html = operator_client.get(f"{DETAIL}?version=2").content.decode()
    assert f'<a class="button edit" href="{EDIT}">Edit latest (v3)</a>' in html
    assert f'href="/profiles/{OTHER}/copy/?version=2"' in html


# --- validate -------------------------------------------------------------------------------------


def test_validate_places_findings_at_their_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    engine.respond(
        "POST",
        VALIDATE,
        422,
        json=error(
            "invalid_profile",
            "the profile cannot run",
            missing_dependencies=[
                {
                    "module": "episodic",
                    "level": "on",
                    "requires": [{"module": "rolling_summary", "levels": []}],
                }
            ],
            undelivered=[{"module": "perception", "level": "full", "setting": "perception.level"}],
        ),
    )
    data = posted(page, **{field("perception.level"): "full", "action": "validate"})
    response = operator_client.post(EDIT, data)
    html = response.content.decode()
    (sent,) = calls(engine, "POST", f"{OTHER_PATH}/validate")
    assert body_of(sent) == {
        "settings": {
            "memory": {"rolling_summary": {"window_messages": 10}},
            "perception": {"level": "full"},
        }
    }
    assert "Not valid — 2 problems" in text(html)
    assert response.context["form"].errors[field("perception.level")] == [
        "Not delivered on this deployment: `perception` at `full`"
    ]
    assert re.search(
        r'id="f-memory-episodic" open>.*?<ul class="errorlist group-errors"><li>`episodic` at '
        r"`on` needs `rolling_summary` at any level</li>",
        html,
        re.S,
    )
    assert writes(engine) == [sent]  # validate alone: no re-read, no write
    row = EngineOperation.objects.get(kind="validate_engine_profile")
    assert (row.entity_type, row.entity_id, row.idempotency_key) == ("engine_profile", OTHER, "")


def test_warnings_show_beside_their_field_and_do_not_block(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    engine.respond("POST", VALIDATE, 200, json=validation(warnings=[low_cap_warning()]))
    response = operator_client.post(EDIT, posted(page, action="validate"))
    html = response.content.decode()
    assert "Valid, with 1 warning" in text(html)
    assert (
        "Below safety minimum: `model_calls` 1; a turn needs at least 3 "
        "(reservation: generation=1, guardrail_input=1, guardrail_output=1)"
    ) in html


def test_htmx_validate_answers_the_form_alone(operator_client: Client, engine: FakeEngine) -> None:
    page = edit_page(operator_client, engine)
    response = operator_client.post(
        EDIT, posted(page, action="validate"), headers={"HX-Request": "true"}
    )
    html = response.content.decode()
    assert html.lstrip().startswith("<form") or '<form method="post"' in html[:400]
    assert "<html" not in html
    assert "Valid" in text(html)


def test_create_page_validate_posts_to_the_deployment_default(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = new_page(operator_client, engine)
    data = posted(page, name="Lite-short", action="validate", **{field(WINDOW): "10"})
    response = operator_client.post(NEW, data)
    (sent,) = writes(engine)
    assert sent.url.path == f"/engine-profiles/{LITE}/validate"
    assert body_of(sent) == {"settings": {"memory": {"rolling_summary": {"window_messages": 10}}}}
    assert "Valid" in text(response.content.decode())


def test_create_when_the_default_is_unreadable(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", SETTINGS, 503, json=error("unavailable"))
    page = new_page(operator_client, engine)
    html = page.content.decode()
    assert "Can&#x27;t read the deployment default — Save will let the Engine check" in html
    assert re.search(r'value="validate"[^>]*disabled', html)
    engine.respond("POST", LIST, 201, json=written())
    data = posted(page, name="Lite-short", action="save")
    response = operator_client.post(NEW, data)
    assert response.status_code == 302
    assert [r.url.path for r in writes(engine)] == ["/engine-profiles"]  # no pre-validate


# --- create ---------------------------------------------------------------------------------------


def test_create_sends_name_owner_and_sparse_settings(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = new_page(operator_client, engine)
    engine.respond("POST", LIST, 201, json=written())
    data = posted(page, name="  Lite-short ", action="save", **{field(WINDOW): "10"})
    response = operator_client.post(NEW, data)
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    validate, create = writes(engine)
    assert validate.url.path.endswith("/validate")
    assert body_of(create) == {
        "name": "Lite-short",
        "settings": {"memory": {"rolling_summary": {"window_messages": 10}}},
        "owner": "console:nate",
    }
    assert create.headers["Idempotency-Key"] == data["idempotency_key"]
    assert flashes(response) == [f"Created Lite-short ({OTHER}), version 1"]
    row = EngineOperation.objects.get(kind="create_engine_profile")
    assert (row.entity_type, row.entity_id) == ("engine_profile", OTHER)


def test_invalid_validation_blocks_save_with_no_write(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = new_page(operator_client, engine)
    engine.respond(
        "POST",
        VALIDATE,
        422,
        json=error(
            "invalid_profile",
            "cannot run",
            missing_dependencies=[],
            undelivered=[{"module": "perception", "level": "full", "setting": "perception.level"}],
        ),
    )
    data = posted(page, name="X", action="save", **{field("perception.level"): "full"})
    response = operator_client.post(NEW, data)
    assert response.status_code == 200
    assert [r.url.path for r in writes(engine)] == [f"/engine-profiles/{LITE}/validate"]
    assert "Not valid — 1 problem" in text(response.content.decode())

    edit = edit_page(operator_client, engine)
    response = operator_client.post(
        EDIT, posted(edit, action="save", **{field("perception.level"): "full"})
    )
    assert [r.method for r in writes(engine)] == ["POST"]  # validate only, no PUT
    assert response.status_code == 200


def test_warnings_alone_let_create_save_write(operator_client: Client, engine: FakeEngine) -> None:
    page = new_page(operator_client, engine)
    engine.respond("POST", VALIDATE, 200, json=validation(warnings=[low_cap_warning()]))
    engine.respond("POST", LIST, 201, json=written())
    response = operator_client.post(NEW, posted(page, name="Lite-short", action="save"))
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    validate, create = writes(engine)
    assert validate.url.path == f"/engine-profiles/{LITE}/validate"
    assert (create.method, create.url.path) == ("POST", "/engine-profiles")
    assert "Below safety minimum" in " ".join(flashes(response))


def test_warnings_alone_let_edit_save_write(operator_client: Client, engine: FakeEngine) -> None:
    page = edit_page(operator_client, engine)
    engine.respond("POST", VALIDATE, 200, json=validation(warnings=[low_cap_warning()]))
    engine.respond(
        "PUT",
        ITEM,
        200,
        json=edit_body(True, other_profile(version=4, current_version=4), impact(affected())),
    )
    response = operator_client.post(EDIT, posted(page, action="save", **{field(POLISH): "0"}))
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    validate, put = writes(engine)
    assert (validate.method, validate.url.path) == ("POST", f"{OTHER_PATH}/validate")
    assert (put.method, put.url.path) == ("PUT", OTHER_PATH)
    assert body_of(put)["ignore_warnings"] is False
    assert "Below safety minimum" in " ".join(flashes(response))


def test_create_invalid_profile_is_placed_at_its_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SETTINGS, 503, json=error("unavailable"))
    page = new_page(operator_client, engine)
    engine.respond(
        "POST",
        LIST,
        422,
        json=error(
            "invalid_profile",
            "cannot run",
            undelivered=[{"module": "commitment", "level": "on", "setting": "commitment.enabled"}],
            missing_dependencies=[
                {"module": "pins", "level": "on", "requires": [{"module": "x", "levels": ["on"]}]}
            ],
        ),
    )
    data = posted(page, name="X", action="save", **{field("commitment.enabled"): "true"})
    response = operator_client.post(NEW, data)
    form = response.context["form"]
    assert form.errors[field("commitment.enabled")] == [
        "Not delivered on this deployment: `commitment` at `on`"
    ]
    assert form.group_errors["pins"] == ["`pins` at `on` needs `x` at `on`"]


def test_a_validation_error_from_the_engine_lands_on_its_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = new_page(operator_client, engine)
    engine.respond(
        "POST",
        LIST,
        422,
        json=error("validation_error", "too big", field=f"body.settings.{WINDOW}"),
    )
    response = operator_client.post(NEW, posted(page, name="X", action="save"))
    assert response.context["form"].errors[field(WINDOW)] == ["too big"]


def test_the_not_in_schema_textarea_carries_undelivered_modules(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = new_page(operator_client, engine)
    engine.respond(
        "POST",
        VALIDATE,
        422,
        json=error(
            "invalid_profile",
            "cannot run",
            missing_dependencies=[],
            undelivered=[{"module": "structured", "level": "on", "setting": "structured.level"}],
        ),
    )
    data = posted(
        page, name="X", action="validate", other_settings='{"structured": {"level": "on"}}'
    )
    response = operator_client.post(NEW, data)
    (sent,) = writes(engine)
    assert body_of(sent) == {"settings": {"structured": {"level": "on"}}}
    assert response.context["form"].errors["other_settings"] == [
        "structured.level: Not delivered on this deployment: `structured` at `on`"
    ]


@pytest.mark.parametrize(
    ("name", "message"), [("   ", "Enter a name"), ("x" * 121, "Use at most 120 characters")]
)
def test_names_are_trimmed_and_checked_before_any_call(
    operator_client: Client, engine: FakeEngine, name: str, message: str
) -> None:
    page = new_page(operator_client, engine)
    response = operator_client.post(NEW, posted(page, name=name, action="save"))
    assert response.context["form"].errors["name"] == [message]
    assert engine.requests == []


# --- edit -----------------------------------------------------------------------------------------


def test_no_change_makes_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    page = edit_page(operator_client, engine)
    response = operator_client.post(EDIT, posted(page, action="save"))
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    assert flashes(response) == ["No change"]
    assert engine.requests == []


def test_sparse_send_and_applied_flash(operator_client: Client, engine: FakeEngine) -> None:
    page = edit_page(operator_client, engine)
    engine.respond(
        "PUT",
        ITEM,
        200,
        json=edit_body(True, other_profile(version=4, current_version=4), impact(affected())),
    )
    data = posted(page, action="save", **{field(POLISH): "0"})
    response = operator_client.post(EDIT, data)
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    reread, validate, put = [r for r in engine.requests if r.url.path.startswith(OTHER_PATH)]
    assert (reread.method, validate.method, put.method) == ("GET", "POST", "PUT")
    assert body_of(put) == {
        "name": "Lite-short",
        "settings": {
            "memory": {"rolling_summary": {"window_messages": 10}},
            "repair": {"polish": {"max_regenerations": 0}},
        },
        "ignore_warnings": False,
    }
    assert flashes(response) == ["Saved — version 4; 1 story follows it from its next turn"]
    row = EngineOperation.objects.get(kind="update_engine_profile")
    assert (row.entity_type, row.entity_id, row.outcome) == ("engine_profile", OTHER, "ok")


def test_a_cleared_stored_key_is_dropped(operator_client: Client, engine: FakeEngine) -> None:
    page = edit_page(operator_client, engine)
    engine.respond(
        "PUT", ITEM, 200, json=edit_body(True, other_profile(version=4, current_version=4))
    )
    operator_client.post(EDIT, posted(page, action="save", **{field(WINDOW): ""}))
    (put,) = calls(engine, "PUT")
    assert body_of(put)["settings"] == {}


def test_a_name_only_edit_is_renamed_without_a_new_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    engine.respond("PUT", ITEM, 200, json=edit_body(True, other_profile(name="Brisk")))
    response = operator_client.post(EDIT, posted(page, name="Brisk", action="save"))
    assert flashes(response) == ["Renamed — no new version"]


def test_applied_with_nothing_changed_says_so(operator_client: Client, engine: FakeEngine) -> None:
    page = edit_page(operator_client, engine)
    engine.respond("PUT", ITEM, 200, json=edit_body(True, other_profile()))
    response = operator_client.post(
        EDIT, posted(page, action="save", **{field(WINDOW): "10.0", field(POLISH): "2"})
    )
    assert flashes(response) == ["No change — the Engine found nothing to save"]


def test_a_concurrent_version_change_shows_the_banner_and_no_put(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    engine.respond("GET", ITEM, 200, json=other_profile(version=5, current_version=5))
    response = operator_client.post(EDIT, posted(page, action="save", **{field(POLISH): "0"}))
    html = response.content.decode()
    assert "This profile changed since you loaded it (now version 5)" in html
    assert response.context["form"][field(POLISH)].value() == "0"  # input kept
    assert writes(engine) == []


def test_a_failed_reread_shows_the_panel_and_writes_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    engine.respond("GET", ITEM, 503, json=error("unavailable", "down"))
    response = operator_client.post(EDIT, posted(page, action="save", **{field(POLISH): "0"}))
    assert "Engine error 503 unavailable" in response.content.decode()
    assert writes(engine) == []


def test_profile_deleted_on_save_redirects(operator_client: Client, engine: FakeEngine) -> None:
    page = edit_page(operator_client, engine)
    engine.respond("PUT", ITEM, 422, json=error("profile_deleted", "deleted", reason="deleted"))
    response = operator_client.post(EDIT, posted(page, action="save", **{field(POLISH): "0"}))
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    assert flashes(response) == ["This profile was deleted — copy it to change it"]
    row = EngineOperation.objects.get(kind="update_engine_profile")
    assert (row.outcome, row.error_code) == ("error", "profile_deleted")


def test_invalid_profile_with_stories_lists_them(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    engine.respond("GET", STORY, 200, json=story(STORY_A, "Harbour"))
    engine.respond(
        "PUT",
        ITEM,
        422,
        json=error(
            "invalid_profile",
            "overrides cannot run",
            missing_dependencies=[],
            undelivered=[],
            stories=[STORY_A],
            count=1200,
        ),
    )
    response = operator_client.post(EDIT, posted(page, action="save", **{field(POLISH): "0"}))
    html = response.content.decode()
    assert "1200 stories' overrides can't run under this change" in html
    assert "Not valid" not in html
    assert "Valid" not in text(html).split("Owner")[1][:200]
    assert f'<a href="/stories/{STORY_A}/">Harbour</a>' in html


def test_a_changed_schema_rerenders_without_writing(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    changed = schema_body()
    changed["delivered"]["extra"] = ["on"]
    engine.respond("GET", SCHEMA, 200, json=changed)
    profile_schema.clear_schema_cache()  # the next read sees the new schema
    response = operator_client.post(EDIT, posted(page, action="save", **{field(POLISH): "0"}))
    assert "The profile schema changed — check your input" in response.content.decode()
    assert writes(engine) == []


def test_a_tampered_page_rereads_and_writes_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    response = operator_client.post(EDIT, posted(page, action="save", snapshot="forged"))
    assert "This form expired" in response.content.decode()
    assert writes(engine) == []


# --- impact and "Apply anyway" --------------------------------------------------------------------


def held(engine: FakeEngine) -> None:
    engine.respond("GET", STORY, 200, json=story(STORY_A, "Harbour"))
    report = impact(
        affected(),
        affected(
            STORY_B,
            capabilities_gained=["episodic"],
            capabilities_lost=[],
            backfill={
                "needed": True,
                "estimate": {"model_calls": 1200, "tokens": 345678, "duration_s": 75.0},
            },
            upgrade_unavailable=True,
        ),
        warnings=["polish_off"],
        guarantees_lost=["G-PIN-1"],
        recommendations_unmet=[
            {"capability": "polish", "recommended_by": "01JSCN0000000000000000000A@2"}
        ],
        affected_count=1003,
    )
    engine.respond("PUT", ITEM, 200, json=edit_body(False, None, report))


def test_applied_false_renders_the_impact_page(operator_client: Client, engine: FakeEngine) -> None:
    page = edit_page(operator_client, engine)
    held(engine)
    response = operator_client.post(EDIT, posted(page, action="save", **{field(POLISH): "0"}))
    html = response.content.decode()
    shown = text(html)
    assert response.status_code == 200
    assert "Impact report" in shown
    assert "polish_off" in shown
    assert "G-PIN-1" in shown
    # #57: the lost guarantee opens its row of the profile's table at the version loaded.
    assert (
        f'<a href="/profiles/{OTHER}/?version=3#g-G-PIN-1" target="_blank" rel="noopener"' in html
    )
    assert "Lost by at least one following story" in shown
    assert 'href="/catalog/scenarios/01JSCN0000000000000000000A/?version=2"' in html
    assert f'<a href="/stories/{STORY_A}/">Harbour <code>{STORY_A}</code></a>' in html
    assert (
        "This story has history and Lite can&#x27;t backfill — it keeps its current configuration"
    ) in html
    assert "1,200 model calls, 345,678 tokens, ~1m 15s" in shown
    assert f'<code title="{FP_BEFORE}">{FP_BEFORE[:12]}</code> → <code title="{FP_AFTER}">' in html
    assert "…and 1001 more (the Engine lists the first 1,000)" in shown
    assert "Apply anyway" in shown
    assert "uncertified" in shown


def test_an_edit_no_story_follows_shows_the_top_level_block(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    report = impact(
        warnings=["data_producing_increase"],
        fingerprint_before=FP_BEFORE,
        fingerprint_after=FP_AFTER,
        capabilities_after=["rolling_summary", "episodic"],
        quality_status_after={"status": "uncertified"},
    )
    engine.respond("PUT", ITEM, 200, json=edit_body(False, None, report))
    response = operator_client.post(EDIT, posted(page, action="save", **{field(POLISH): "0"}))
    shown = text(response.content.decode())
    assert "No story follows this profile" in shown
    assert "Capabilities after rolling_summary , episodic" in shown
    assert FP_BEFORE[:12] in shown


def test_apply_anyway_resends_with_ignore_warnings_and_a_fresh_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    held(engine)
    first_data = posted(page, action="save", **{field(POLISH): "0"})
    impact_page = operator_client.post(EDIT, first_data)
    html = impact_page.content.decode()
    token = hidden(html, "confirm_token")
    key = re.findall(r'name="idempotency_key" value="([^"]+)"', html)[-1]
    assert key != first_data["idempotency_key"]
    (first_put,) = calls(engine, "PUT")
    engine.reset()
    engine.respond(
        "PUT",
        ITEM,
        200,
        json=edit_body(
            True, other_profile(version=4, current_version=4), impact(affected(), affected(STORY_B))
        ),
    )
    response = operator_client.post(APPLY, {"confirm_token": token, "idempotency_key": key})
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    reread, put = engine.requests[-2:]
    assert reread.method == "GET"
    assert body_of(put) == {**body_of(first_put), "ignore_warnings": True}
    assert put.headers["Idempotency-Key"] == key != first_put.headers["Idempotency-Key"]
    assert flashes(response) == ["Saved — version 4; 2 stories follow it from their next turn"]


def test_apply_anyway_with_an_expired_token_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.reset()
    response = operator_client.post(APPLY, {"confirm_token": "nope", "idempotency_key": "k"})
    assert response.status_code == 302
    assert response["Location"] == EDIT
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert engine.requests == []


def test_a_422_after_apply_anyway_rerenders_the_edit_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = edit_page(operator_client, engine)
    held(engine)
    html = operator_client.post(
        EDIT, posted(page, action="save", **{field(POLISH): "0"})
    ).content.decode()
    engine.respond(
        "PUT", ITEM, 422, json=error("validation_error", "bad", field=f"body.settings.{POLISH}")
    )
    response = operator_client.post(
        APPLY, {"confirm_token": hidden(html, "confirm_token"), "idempotency_key": "k1"}
    )
    form = response.context["form"]
    assert form.errors[field(POLISH)] == ["bad"]
    assert form[field(POLISH)].value() == "0"


# --- copy -----------------------------------------------------------------------------------------


def test_copy_sends_the_viewed_version_and_lands_on_the_copy(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=other_profile(version=2))
    page = operator_client.get(f"/profiles/{OTHER}/copy/?version=2")
    html = page.content.decode()
    assert page.context["form"]["name"].value() == "Lite-short (copy)"
    assert page.context["form"]["owner"].value() == "console:nate"
    assert f'action="/profiles/{OTHER}/copy/?version=2"' in html
    copy_id = "01JPRF00000000000000000009"
    engine.respond("POST", COPY, 201, json=written(id=copy_id, name="Mine"))
    engine.reset()
    response = operator_client.post(
        f"/profiles/{OTHER}/copy/?version=2",
        {
            "name": " Mine ",
            "owner": "bob",
            "source_name": "Lite-short",
            "idempotency_key": hidden(html, "idempotency_key"),
        },
    )
    assert response.status_code == 302
    assert response["Location"] == f"/profiles/{copy_id}/"
    (sent,) = writes(engine)
    assert sent.url.path == f"{OTHER_PATH}/copy"
    assert dict(sent.url.params) == {"version": "2"}
    assert body_of(sent) == {"name": "Mine", "owner": "bob"}
    assert flashes(response) == [f"Copied Lite-short v2 to Mine ({copy_id}), version 1"]
    row = EngineOperation.objects.get(kind="copy_engine_profile")
    assert (row.entity_type, row.entity_id) == ("engine_profile", copy_id)


def test_a_failed_copy_is_recorded_on_the_source(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", COPY, 404, json=error("not_found", "gone"))
    response = operator_client.post(
        f"/profiles/{OTHER}/copy/?version=3", {"name": "Mine", "owner": "bob"}
    )
    assert "Engine error 404 not_found" in response.content.decode()
    row = EngineOperation.objects.get(kind="copy_engine_profile")
    assert row.entity_id == OTHER


def test_copy_default_name_fits_120_characters(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=other_profile(name="n" * 120))
    page = operator_client.get(f"/profiles/{OTHER}/copy/")
    name = page.context["form"]["name"].value()
    assert len(name) == 120
    assert name.endswith(" (copy)")


# --- delete ---------------------------------------------------------------------------------------


def test_the_delete_confirm_page_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    engine.reset()
    html = operator_client.get(f"/profiles/{OTHER}/delete/?name=Lite-short").content.decode()
    assert engine.requests == []
    assert "Delete" in html
    assert "Lite-short" in html
    assert (
        "Stories using it keep running on their pinned version; it can&#x27;t be assigned again."
        in html
    )


def test_delete_flashes_the_warnings(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY, 200, json=story(STORY_A, "Harbour"))
    engine.respond(
        "DELETE",
        ITEM,
        200,
        json=deleted_body(
            warnings=[
                {"kind": "profile_in_use", "story_id": STORY_A, "scenario_id": None},
                {
                    "kind": "profile_recommended",
                    "story_id": None,
                    "scenario_id": "01JSCN0000000000000000000A",
                },
            ],
            stories_using=30,
            scenarios_recommending=1,
        ),
    )
    response = operator_client.post(
        f"/profiles/{OTHER}/delete/", {"name": "Lite-short", "idempotency_key": "k1"}
    )
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    (flash,) = flashes(response)
    assert (
        f"Deleted Lite-short ({OTHER}). Stories using it: 30; scenarios recommending it: 1."
        in flash
    )
    assert f'<a href="/stories/{STORY_A}/">Harbour</a>, …and 5 more' in flash
    assert '<a href="/catalog/scenarios/01JSCN0000000000000000000A/">' in flash
    (sent,) = writes(engine)
    assert (sent.method, sent.headers["Idempotency-Key"]) == ("DELETE", "k1")


def test_deleting_the_default_is_refused_with_a_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "DELETE",
        ITEM,
        409,
        json=error("validation_error", "default", id=OTHER, reason="is_default"),
    )
    response = operator_client.post(f"/profiles/{OTHER}/delete/", {"name": "Lite-short"})
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    (flash,) = flashes(response)
    assert flash.startswith(
        "The deployment default can&#x27;t be deleted — choose another default first"
    )
    assert 'href="/engine/settings/"' in flash


def test_another_409_is_the_generic_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", ITEM, 409, json=error("conflict", "busy"))
    response = operator_client.post(f"/profiles/{OTHER}/delete/", {"name": "Lite-short"})
    assert "Engine error 409 conflict" in response.content.decode()


def test_the_list_links_to_new(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json={"items": [other_profile()], "next_cursor": None})
    html = operator_client.get("/profiles/").content.decode()
    assert '<a class="button new-profile" href="/profiles/new/">New profile</a>' in html


def test_the_null_checkbox_sends_null_on_save(operator_client: Client, engine: FakeEngine) -> None:
    page = edit_page(operator_client, engine)
    engine.respond(
        "PUT", ITEM, 200, json=edit_body(True, other_profile(version=4, current_version=4))
    )
    operator_client.post(EDIT, posted(page, action="save", **{null("budgets.model_calls"): "on"}))
    (put,) = calls(engine, "PUT")
    assert body_of(put)["settings"]["budgets"] == {"model_calls": None}


def test_deployment_unreadable_still_shows_schema_defaults(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SETTINGS, 503, json=error("unavailable"))
    html = operator_client.get(EDIT).content.decode()
    assert 'placeholder="schema default: 30"' in html
    assert deployment()  # the helper stays importable for the other modules

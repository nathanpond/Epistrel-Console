"""A story's effective configuration and fingerprint, its profile assignment and its overrides
(#85)."""

import copy
import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import reverse

from console.engine.client import EngineClient
from console.engine.models import TurnReport
from console.engine_access import engine_client_for
from console.forms.story_overrides import field_of
from console.impact import short_fingerprint
from console.models import EngineOperation
from console.story_settings import config_url as settings_config_url
from console.views import play, story_config
from tests.fakes.engine import FakeEngine
from tests.fakes.guarantees import answer_guarantees
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import EFFECTIVE, effective_config
from tests.play.helpers import (
    HTMX,
    OPERATION,
    QUEUED_OP,
    RESPOND,
    answer_page,
    operation_json,
    post_turn,
    respond_json,
    story_json,
)
from tests.profile_helpers import (
    ITEM,
    LIST,
    LITE,
    OTHER,
    SCHEMA,
    affected,
    edit_body,
    fresh_cache,
    impact,
    other_profile,
    profile,
    schema_body,
)
from tests.ratings.helpers import error, hidden, text

pytestmark = pytest.mark.django_db

STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
CONFIG = f"/stories/{STORY}/config/"
OVERRIDES_PAGE = f"/stories/{STORY}/config/overrides/"
OVERRIDES_APPLY = f"/stories/{STORY}/config/overrides/apply/"
ASSIGN = f"/stories/{STORY}/config/profile/"
ASSIGN_APPLY = f"/stories/{STORY}/config/profile/apply/"
PREVIEW = "/stories/{story_id}/impact-preview"
PATCH = "/stories/{story_id}/profile-overrides"
PUT = "/stories/{story_id}/engine-profile"
WINDOW = "memory.rolling_summary.window_messages"
ENABLED = "memory.rolling_summary.enabled"
EPISODIC_K = "memory.episodic.k"
EPISODIC_ON = "memory.episodic.enabled"
TEMPERATURE = "models.generation.temperature"
PURPOSES = "models.purposes"
FP_NOW = "sha256:" + "c" * 64
FP_NEW = "sha256:" + "d" * 64
OVERRIDDEN = {"memory": {"rolling_summary": {"window_messages": 12}}}


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    yield from fresh_cache()


def config_body(overrides: dict[str, Any] | None = None, revision: int = 3, **changes: Any) -> Any:
    """The story's effective configuration: Lite v2 (following) and ``overrides`` laid over the
    deployment defaults."""
    body = effective_config(story_id=STORY, fingerprint=FP_NOW, **changes)
    settings = copy.deepcopy(OVERRIDDEN if overrides is None else overrides)
    effective = copy.deepcopy(body["deployment_defaults"])
    _merge(effective, settings)
    body["overrides"] = {"revision": revision, "settings": settings}
    body["effective"] = effective
    return body


def _merge(target: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def profiles_page(*items: dict[str, Any]) -> dict[str, Any]:
    return {"items": list(items) or [profile(), other_profile()], "next_cursor": None}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    answer_relations(fake)
    answer_guarantees(fake)  # #57: the Config page reads the story's guarantee table

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (story_config, play):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=config_body())
    fake.respond("GET", ITEM, 200, json=profile())
    fake.respond("GET", LIST, 200, json=profiles_page())
    fake.respond("GET", SCHEMA, 200, json=schema_body())
    fake.respond("GET", "/stories/{story_id}", 200, json=story_json())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def requests(engine: FakeEngine, method: str, suffix: str = "") -> list[Any]:
    return [r for r in engine.requests if r.method == method and r.url.path.endswith(suffix)]


def sent(request: Any) -> Any:
    return json.loads(request.content)


def row(html: str, path: str) -> str:
    anchor = "f-" + re.sub(r"[.\[\]]", "-", path)
    match = re.search(rf'<tr id="{anchor}"[^>]*>(.*?)</tr>', html, re.S)
    assert match is not None, path
    return match.group(0)


def form_data(response: Any, **changes: Any) -> dict[str, Any]:
    """The rendered overrides form as a POST would send it, ``changes`` laid over (``None``
    drops a field)."""
    context = response.context
    form = context["form"]
    data: dict[str, Any] = {}
    for name, bound_field in form.fields.items():
        value = form[name].value()
        if bound_field.__class__.__name__ == "BooleanField":
            if value:
                data[name] = "on"
            continue
        data[name] = "" if value is None else value
    data["snapshot"] = context["snapshot"]
    for name, value in changes.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
    return data


def form_page(client: Client, engine: FakeEngine) -> Any:
    page = client.get(OVERRIDES_PAGE)
    assert page.status_code == 200
    engine.reset()
    return page


def preview(client: Client, engine: FakeEngine, report: dict[str, Any], **changes: Any) -> Any:
    page = form_page(client, engine)
    engine.respond("POST", PREVIEW, 200, json=report)
    return client.post(OVERRIDES_PAGE, form_data(page, **changes))


def written(applied: bool, result: dict[str, Any] | None, report: dict[str, Any]) -> Any:
    return {**edit_body(applied, result, report), "timeline_revision": 5}


# --- the Config page ------------------------------------------------------------------------------


def test_config_shows_profile_fingerprint_quality_and_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(CONFIG).content.decode()
    shown = text(html)
    assert f'<a href="/profiles/{LITE}/?version=2">Lite</a> v2 — follows latest' in html
    assert f'<dd class="fingerprint"><code>{FP_NOW}</code></dd>' in html
    assert '<span class="quality ">uncertified</span>' in html
    assert "Overrides revision 3 — Edit overrides" in shown
    assert "rolling_summary , pins , polish" in shown


def test_the_source_column_names_each_layer(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(CONFIG).content.decode()
    window = row(html, WINDOW)
    assert '<td class="layer-override effective">12</td>' in window
    assert '<td class="layer-profile">30</td>' in window
    assert '<td class="source">override</td>' in window
    enabled = row(html, ENABLED)
    assert '<td class="layer-profile effective">true</td>' in enabled
    assert '<td class="layer-override">—</td>' in enabled
    assert '<td class="source">profile</td>' in enabled
    k = row(html, EPISODIC_K)
    assert '<td class="layer-default effective">8</td>' in k
    assert '<td class="layer-profile">—</td>' in k
    assert '<td class="source">default</td>' in k
    assert "quality-" in k  # #78's tag badges beside each row


def test_an_override_equal_to_the_profile_still_names_the_override(
    operator_client: Client, engine: FakeEngine
) -> None:
    same = {"memory": {"rolling_summary": {"window_messages": 30}}}
    engine.respond("GET", EFFECTIVE, 200, json=config_body(same))
    html = operator_client.get(CONFIG).content.decode()
    assert '<td class="source">override</td>' in row(html, WINDOW)


def test_the_profile_column_reads_the_running_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    pinned = config_body(profile={"id": LITE, "version": 1, "pinned": True, "deleted": True})
    engine.respond("GET", EFFECTIVE, 200, json=pinned)
    html = operator_client.get(CONFIG).content.decode()
    [read] = requests(engine, "GET", f"/engine-profiles/{LITE}")
    assert read.url.params["version"] == "1"
    assert "pinned at v1" in html
    assert '<span class="badge deleted">deleted</span>' in html
    assert f'href="/profiles/{LITE}/?version=1"' in html


def test_a_failed_profile_read_degrades_its_column(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 503, json=error("database_unavailable"))
    html = operator_client.get(CONFIG).content.decode()
    assert "Couldn&#x27;t read profile" in html or "Couldn't read profile" in html
    assert '<td class="layer-profile">unavailable</td>' in row(html, ENABLED)
    assert '<td class="source">profile or default: true</td>' in row(html, ENABLED)
    assert '<td class="source">override</td>' in row(html, WINDOW)


def test_a_failed_schema_read_keeps_the_rows_without_badges(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SCHEMA, 503, json=error("database_unavailable"))
    html = operator_client.get(CONFIG).content.decode()
    assert '<td class="source">override</td>' in row(html, WINDOW)
    assert "no tags or schema defaults" in html


def test_a_failed_effective_config_read_is_the_page_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 404, json=error("not_found", "no such story"))
    html = operator_client.get(CONFIG).content.decode()
    assert "Engine error 404 not_found" in html


def test_the_assign_select_lists_profiles_with_the_current_disabled(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(CONFIG).content.decode()
    assert f'<option value="{LITE}" disabled>Lite · v2 · {LITE[:6]} (current)</option>' in html
    assert f'<option value="{OTHER}">Lite-short · v3 · {OTHER[:6]}</option>' in html


def test_a_deleted_current_profile_is_listed_first(
    operator_client: Client, engine: FakeEngine
) -> None:
    gone = profile(deleted_at="2026-10-20T00:00:00Z")
    engine.respond("GET", LIST, 200, json=profiles_page(other_profile(), gone))
    html = operator_client.get(CONFIG).content.decode()
    select = html.split('<select id="assign-profile-id"', 1)[1].split("</select>", 1)[0]
    options = re.findall(r'<option value="([^"]*)"', select)
    assert options[:3] == ["", LITE, OTHER]
    assert "Lite · deleted (current)" in html


def test_a_failed_profiles_list_replaces_the_select(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 503, json=error("database_unavailable"))
    html = operator_client.get(CONFIG).content.decode()
    assert "Profiles unavailable — reload" in html
    assert 'name="profile_id"' not in html


def test_the_settings_pages_see_config_link_resolves_here(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#54's "Moved to engine profile overrides — see Config" link targets this page."""
    assert settings_config_url(STORY) == CONFIG == reverse("stories:config", args=[STORY])
    assert operator_client.get(settings_config_url(STORY)).status_code == 200


# --- the overrides form ---------------------------------------------------------------------------


def test_the_form_prefills_overrides_and_shows_what_others_inherit(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = operator_client.get(OVERRIDES_PAGE)
    html = page.content.decode()
    assert page.context["form"][field_of(WINDOW)].value() == "12"
    assert f'name="remove__{field_of(WINDOW)}"' in html
    assert 'placeholder="inherits: 8 (default)"' in html  # episodic.k: not in the profile
    assert f'name="remove__{field_of(EPISODIC_K)}"' not in html
    assert "0 (no overrides)" not in html
    assert "Overrides revision 3" in html


def test_booleans_are_tri_state_without_an_override(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", EFFECTIVE, 200, json=config_body({"memory": {"episodic": {"enabled": True}}})
    )
    form = operator_client.get(OVERRIDES_PAGE).context["form"]
    assert [v for v, _ in form.fields[field_of(ENABLED)].choices] == ["", "true", "false"]
    assert form.fields[field_of(ENABLED)].choices[0][1] == "— no override —"
    assert [v for v, _ in form.fields[field_of(EPISODIC_ON)].choices] == ["true", "false"]
    assert form[field_of(EPISODIC_ON)].value() == "true"


def test_nullable_settings_offer_no_null_box(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(OVERRIDES_PAGE).content.decode()
    assert f'name="null__{field_of(TEMPERATURE)}"' not in html
    assert "To set null, edit the profile" in html


def test_preview_sends_exactly_the_patch_and_no_write(
    operator_client: Client, engine: FakeEngine
) -> None:
    report = impact(affected(fingerprint_before=FP_NOW, fingerprint_after=FP_NEW))
    response = preview(
        operator_client, engine, report, **{field_of(WINDOW): "20", field_of(EPISODIC_K): "4"}
    )
    assert response.status_code == 200
    [call] = requests(engine, "POST", "/impact-preview")
    assert sent(call) == {
        "overrides": {"memory": {"rolling_summary": {"window_messages": 20}, "episodic": {"k": 4}}}
    }
    assert requests(engine, "PATCH") == []
    assert requests(engine, "PUT") == []
    html = response.content.decode()
    assert short_fingerprint(FP_NOW) in html
    assert short_fingerprint(FP_NEW) in html
    assert "Capabilities after" in html
    assert ">Apply</button>" in html  # no warnings: plain Apply
    row = EngineOperation.objects.get(kind="impact_preview")
    assert row.entity_type == "story"
    assert row.entity_id == STORY


def test_the_preview_links_lost_guarantees_to_the_config_table(
    operator_client: Client, engine: FakeEngine
) -> None:
    lost = ["verification", "contradiction_rules", "claim_detectors"]
    report = impact(affected(), guarantees_lost=lost)
    html = preview(operator_client, engine, report, **{field_of(WINDOW): "20"}).content.decode()
    for g in lost:
        assert (
            f'<a href="{CONFIG}#g-{g}" target="_blank" rel="noopener" '
            f'title="Opens this row of the guarantee table in a new tab"><code>{g}</code></a>'
        ) in html
    assert "Lost by at least one following story" not in html  # story mode


def test_nothing_changed_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    page = form_page(operator_client, engine)
    response = operator_client.post(OVERRIDES_PAGE, form_data(page))
    assert "Nothing to preview" in response.content.decode()
    assert engine.requests == []


def test_remove_override_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    preview(operator_client, engine, impact(), **{f"remove__{field_of(WINDOW)}": "on"})
    [call] = requests(engine, "POST", "/impact-preview")
    assert sent(call) == {"overrides": {"memory": {"rolling_summary": {"window_messages": None}}}}


def test_emptying_a_prefilled_input_is_a_form_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = form_page(operator_client, engine)
    response = operator_client.post(OVERRIDES_PAGE, form_data(page, **{field_of(WINDOW): ""}))
    assert "Tick &#x27;remove override&#x27; to remove it" in response.content.decode()
    assert engine.requests == []


def test_json_subtree_removal_sends_null_for_the_removed_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    purposes = {"generation": {"model": "big"}, "lite_summary": {"model": "small"}}
    engine.respond("GET", EFFECTIVE, 200, json=config_body({"models": {"purposes": purposes}}))
    kept = json.dumps({"generation": {"model": "big"}})
    preview(operator_client, engine, impact(), **{field_of(PURPOSES): kept})
    [call] = requests(engine, "POST", "/impact-preview")
    assert sent(call) == {"overrides": {"models": {"purposes": {"lite_summary": None}}}}


def test_an_empty_object_in_a_json_subtree_is_no_change(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = form_page(operator_client, engine)
    response = operator_client.post(OVERRIDES_PAGE, form_data(page, **{field_of(PURPOSES): "{}"}))
    assert "Nothing to preview" in response.content.decode()
    assert engine.requests == []


def test_a_preview_validation_error_is_placed_at_its_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = form_page(operator_client, engine)
    engine.respond(
        "POST",
        PREVIEW,
        422,
        json=error(
            "validation_error",
            "too big",
            errors=[{"field": f"body.overrides.{WINDOW}", "message": "at most 500"}],
        ),
    )
    response = operator_client.post(OVERRIDES_PAGE, form_data(page, **{field_of(WINDOW): "20"}))
    html = response.content.decode()
    match = re.search(r'id="f-memory-rolling_summary-window_messages".*?</div>', html, re.S)
    assert match is not None
    assert "at most 500" in match.group(0)


# --- applying overrides ---------------------------------------------------------------------------


def _preview_page(client: Client, engine: FakeEngine, report: dict[str, Any]) -> str:
    response = preview(client, engine, report, **{field_of(WINDOW): "20"})
    engine.reset()
    return str(response.content.decode())


def _apply(client: Client, html: str, url: str = OVERRIDES_APPLY) -> Any:
    return client.post(
        url,
        {
            "confirm_token": hidden(html, "confirm_token"),
            "idempotency_key": hidden(html, "idempotency_key"),
        },
    )


def test_apply_sends_the_patch_with_the_revision_shown(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _preview_page(operator_client, engine, impact(affected(fingerprint_after=FP_NEW)))
    result = config_body({"memory": {"rolling_summary": {"window_messages": 20}}}, revision=4)
    result["fingerprint"] = FP_NEW
    engine.respond("PATCH", PATCH, 200, json=written(True, result, impact()))
    response = _apply(operator_client, html)
    assert response.status_code == 302
    assert response["Location"] == CONFIG
    [call] = requests(engine, "PATCH")
    assert sent(call) == {
        "settings": {"memory": {"rolling_summary": {"window_messages": 20}}},
        "expected_revision": 3,
        "ignore_warnings": False,
    }
    assert "X-Epistrel-Expected-Revision" not in call.headers
    assert call.headers["Idempotency-Key"] == hidden(html, "idempotency_key")
    [flash] = flashes(response)
    assert f'Applied — fingerprint <code title="{FP_NEW}">{"d" * 12}</code>' in flash
    assert "takes effect from the next turn" in flash


def test_a_held_apply_offers_apply_anyway_with_ignore_warnings(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _preview_page(operator_client, engine, impact())
    held = impact(affected(capabilities_gained=["episodic"]), warnings=["data_producing_increase"])
    engine.respond("PATCH", PATCH, 200, json=written(False, None, held))
    again = _apply(operator_client, html).content.decode()
    assert "Apply anyway" in again
    assert "nothing was written" in again
    assert hidden(again, "idempotency_key") != hidden(html, "idempotency_key")
    engine.reset()
    engine.respond("PATCH", PATCH, 200, json=written(True, config_body(revision=4), impact()))
    _apply(operator_client, again)
    [call] = requests(engine, "PATCH")
    assert sent(call)["ignore_warnings"] is True


def test_a_warned_preview_applies_with_ignore_warnings(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _preview_page(operator_client, engine, impact(warnings=["capability_lost"]))
    assert "Apply anyway" in html
    engine.respond("PATCH", PATCH, 200, json=written(True, config_body(revision=4), impact()))
    _apply(operator_client, html)
    [call] = requests(engine, "PATCH")
    assert sent(call)["ignore_warnings"] is True


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("overrides_revision", "Overrides changed since you loaded this page — reload"),
        ("timeline_revision", "The story changed — reload"),
    ],
)
def test_a_revision_conflict_names_what_moved(
    operator_client: Client, engine: FakeEngine, reason: str, message: str
) -> None:
    html = _preview_page(operator_client, engine, impact())
    engine.respond("PATCH", PATCH, 409, json=error("revision_conflict", reason=reason))
    page = _apply(operator_client, html)
    assert message in page.content.decode()
    assert page.context["form"][field_of(WINDOW)].value() == "20"  # the input kept


def test_an_archived_story_disables_apply(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories/{story_id}", 200, json=story_json(status="archived"))
    html = _preview_page(operator_client, engine, impact())
    assert "Archived — unarchive to apply" in html
    assert 'name="confirm_token"' not in html


def test_story_busy_and_archived_refusals_are_worded(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _preview_page(operator_client, engine, impact())
    engine.respond("PATCH", PATCH, 409, json=error("story_archived"))
    page = _apply(operator_client, html).content.decode()
    assert "This story is archived — unarchive it to change its configuration" in page
    engine.respond("PATCH", PATCH, 423, json=error("story_busy", "a turn is running"))
    page = _apply(operator_client, html).content.decode()
    assert "HTTP 423 story_busy" in page


def test_an_expired_apply_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.post(
        OVERRIDES_APPLY, {"confirm_token": "bad", "idempotency_key": "k"}
    )
    assert response.status_code == 302
    assert engine.requests == []


def test_kept_previous_configuration_when_upgrade_unavailable_and_fingerprint_unchanged(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _preview_page(operator_client, engine, impact())
    report = impact(affected(upgrade_unavailable=True), fingerprint_before=FP_NOW)
    engine.respond("PATCH", PATCH, 200, json=written(True, config_body(), report))
    [flash] = flashes(_apply(operator_client, html))
    assert flash == (
        "Applied, but this story keeps its previous configuration (Lite can't backfill history)"
    )


def test_no_kept_note_when_the_fingerprint_changed(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _preview_page(operator_client, engine, impact())
    report = impact(affected(upgrade_unavailable=True), fingerprint_before=FP_NOW)
    result = config_body(revision=4)
    result["fingerprint"] = FP_NEW
    engine.respond("PATCH", PATCH, 200, json=written(True, result, report))
    [flash] = flashes(_apply(operator_client, html))
    assert "keeps its previous configuration" not in flash
    assert flash.startswith("Applied — fingerprint")


# --- assigning a profile --------------------------------------------------------------------------


def _assign_preview(client: Client, engine: FakeEngine, report: dict[str, Any]) -> str:
    engine.respond("GET", ITEM, 200, json=other_profile())
    engine.respond("POST", PREVIEW, 200, json=report)
    response = client.post(ASSIGN, {"profile_id": OTHER})
    engine.reset()
    return str(response.content.decode())


def test_assign_previews_then_puts(operator_client: Client, engine: FakeEngine) -> None:
    html = _assign_preview(operator_client, engine, impact())
    assert "Runs Lite-short at its latest version (v3) from the next turn" in html
    assert requests(engine, "PUT") == []
    result = config_body(profile={"id": OTHER, "version": 3, "pinned": False, "deleted": False})
    result["fingerprint"] = FP_NEW
    engine.respond("PUT", PUT, 200, json=written(True, result, impact()))
    response = _apply(operator_client, html, ASSIGN_APPLY)
    [call] = requests(engine, "PUT")
    assert sent(call) == {"profile_id": OTHER, "ignore_warnings": False}
    assert "X-Epistrel-Expected-Revision" not in call.headers
    assert flashes(response)[0].startswith("Applied — fingerprint")


def test_assign_preview_body_names_only_the_profile(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", PREVIEW, 200, json=impact())
    operator_client.post(ASSIGN, {"profile_id": OTHER})
    [call] = requests(engine, "POST", "/impact-preview")
    assert sent(call) == {"profile_id": OTHER}
    assert requests(engine, "PUT") == []
    assert requests(engine, "PATCH") == []


def test_assigning_the_current_profile_flashes_no_change(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _assign_preview(operator_client, engine, impact())
    engine.respond("PUT", PUT, 200, json=written(True, config_body(), impact()))
    [flash] = flashes(_apply(operator_client, html, ASSIGN_APPLY))
    assert flash == "No change — the story already runs this configuration"


def test_invalid_profile_with_overrides_names_the_profile(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _assign_preview(operator_client, engine, impact())
    engine.respond(
        "PUT",
        PUT,
        422,
        json=error("invalid_profile", "window too large for this profile", overrides=OVERRIDDEN),
    )
    page = _apply(operator_client, html, ASSIGN_APPLY).content.decode()
    assert (
        "This story&#x27;s overrides can&#x27;t run on Lite-short: window too large for this "
        "profile" in page
    )
    assert f"<code>{WINDOW}: 12</code>" in page
    assert re.search(rf'<option value="{OTHER}" selected>', page)


def test_a_deleted_profile_asks_for_another(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", PREVIEW, 422, json=error("profile_deleted", reason="deleted"))
    page = operator_client.post(ASSIGN, {"profile_id": OTHER}).content.decode()
    assert "That profile was deleted — pick another" in page
    assert requests(engine, "PUT") == []


def test_a_put_revision_conflict_says_the_story_changed(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _assign_preview(operator_client, engine, impact())
    engine.respond("PUT", PUT, 409, json=error("revision_conflict", reason="timeline_revision"))
    assert (
        "The story changed — reload" in _apply(operator_client, html, ASSIGN_APPLY).content.decode()
    )


# --- the play header and the turn report ----------------------------------------------------------


def test_the_story_header_shows_the_short_fingerprint_linked_to_config(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    html = operator_client.get(reverse("stories:detail", args=[STORY])).content.decode()
    assert (
        f'<span id="story-config"><a class="config-link" href="{CONFIG}">'
        f'<code class="fingerprint" title="{FP_NOW}">{"c" * 12}</code> '
        '<span class="quality ">uncertified</span></a></span>'
    ) in html
    assert f'<a class="config-tab" href="{CONFIG}">Config</a>' in html


def test_a_failed_header_read_degrades_only_its_block(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    engine.respond("GET", EFFECTIVE, 503, json=error("database_unavailable"))
    response = operator_client.get(reverse("stories:detail", args=[STORY]))
    assert response.status_code == 200
    assert "config unavailable" in response.content.decode()


def test_the_header_block_updates_out_of_band_after_a_turn(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    engine.respond("POST", RESPOND, 200, json=respond_json())
    html = post_turn(operator_client)
    fingerprint = respond_json()["fingerprint"]
    assert '<span id="story-config" hx-swap-oob="true">' in html
    assert f'title="{fingerprint}">{fingerprint[7:19]}</code>' in html
    assert requests(engine, "GET", "/effective-config") == []  # from the turn's own report


def test_the_turn_report_shows_the_configuration_it_ran_under(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    engine.respond("POST", RESPOND, 200, json=respond_json())
    html = text(post_turn(operator_client))
    body = respond_json()
    assert f"Fingerprint {body['fingerprint'][7:19]}" in html
    assert "Quality status uncertified" in html
    assert f"Engine profile {LITE}@1" in html
    assert "Config revision 0" in html


def test_an_old_turn_report_shows_dashes() -> None:
    from django.template.loader import render_to_string

    report = TurnReport.from_json({"model_calls": [], "elapsed_ms": 5})
    html = text(render_to_string("console/_turn_report.html", {"report": report}))
    assert "Fingerprint — Quality status — Engine profile — Config revision —" in html


def test_short_fingerprint_strips_the_prefix() -> None:
    assert short_fingerprint(FP_NOW) == "c" * 12
    assert short_fingerprint("a" * 64) == "a" * 12
    assert short_fingerprint(None) == ""


def _queue_and_poll(client: Client, engine: FakeEngine, op: dict[str, Any]) -> str:
    engine.respond(
        "POST",
        RESPOND,
        202,
        json={"operation_id": QUEUED_OP, "position": 1},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    answer_page(engine)
    post_turn(client)
    engine.respond("GET", OPERATION, 200, json=op)
    engine.reset()
    url = reverse("stories:poll", args=[STORY, QUEUED_OP])
    response = client.get(url, {"deadline": "45", "position": "1"}, headers=HTMX)
    return str(response.content.decode())


def test_a_polled_turn_refreshes_the_header_from_its_result(
    operator_client: Client, engine: FakeEngine
) -> None:
    done = _queue_and_poll(
        operator_client,
        engine,
        operation_json("committed", result={"status": 200, "body": respond_json()}),
    )
    assert '<span id="story-config" hx-swap-oob="true">' in done
    assert respond_json()["fingerprint"][7:19] in done
    assert requests(engine, "GET", "/effective-config") == []


def test_a_polled_turn_without_a_result_re_reads_the_header(
    operator_client: Client, engine: FakeEngine
) -> None:
    done = _queue_and_poll(operator_client, engine, operation_json("committed"))
    assert '<span id="story-config" hx-swap-oob="true">' in done
    assert len(requests(engine, "GET", "/effective-config")) == 1
    assert "c" * 12 in done


def test_revision_zero_says_no_overrides(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=config_body({}, revision=0))
    assert "0 (no overrides)" in operator_client.get(CONFIG).content.decode()


def test_an_invalid_profile_preview_shows_the_overrides_lines(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = form_page(operator_client, engine)
    engine.respond(
        "POST", PREVIEW, 422, json=error("invalid_profile", "can't run", overrides=OVERRIDDEN)
    )
    html = operator_client.post(OVERRIDES_PAGE, form_data(page, **{field_of(WINDOW): "20"}))
    shown = html.content.decode()
    assert f"<code>{WINDOW}: 12</code>" in shown
    assert "can&#x27;t run" in shown

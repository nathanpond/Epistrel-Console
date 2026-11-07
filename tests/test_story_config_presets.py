"""#307: the Config page's preset picker (E13), the upgrade-mode choice on the confirm step
(E14), a started backfill (the 202) and a forward-only gap."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.choices import PROFILES_MAX
from console.engine.client import ENGINE_PROFILES_PAGE, EngineClient
from console.engine.models import ImpactReport
from console.engine_access import engine_client_for
from console.forms.story_overrides import field_of
from console.models import EngineOperation
from console.upgrades import PRESET_IDS
from console.views import story_config
from console.views.story_config import (
    PRESETS_UNAVAILABLE,
    PROFILES_UNAVAILABLE,
    model_json,
    needs_upgrade_mode,
)
from tests.fakes.engine import FakeEngine
from tests.fakes.guarantees import answer_guarantees
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import EFFECTIVE, effective_config
from tests.fixtures import engine_fixture
from tests.profile_helpers import (
    ITEM,
    LIST,
    LITE,
    OTHER,
    SCHEMA,
    affected,
    fresh_cache,
    impact,
    other_profile,
    profile,
    schema_body,
)
from tests.ratings.helpers import error, hidden, text
from tests.test_story_config import form_data

pytestmark = pytest.mark.django_db

M7_URLS = "tests.urls_with_m7_pages"
STORY = "01JM7ST0000000000000000001"
CONFIG = f"/stories/{STORY}/config/"
ASSIGN = f"/stories/{STORY}/config/profile/"
ASSIGN_APPLY = f"/stories/{STORY}/config/profile/apply/"
OVERRIDES_PAGE = f"/stories/{STORY}/config/overrides/"
OVERRIDES_APPLY = f"/stories/{STORY}/config/overrides/apply/"
VERSION_CHANGE = f"/stories/{STORY}/config/version/"
BACKFILL_PAGE = f"/stories/{STORY}/backfill/"
PREVIEW = "/stories/{story_id}/impact-preview"
PUT = "/stories/{story_id}/engine-profile"
PATCH = "/stories/{story_id}/profile-overrides"
VERSIONS = "/engine-profiles/{profile_id}/versions"
LITE_ID, STANDARD_ID, FULL_ID, MAX_ID = PRESET_IDS
CUSTOM = "01JPRF00000000000000000003"
WINDOW = "memory.rolling_summary.window_messages"
FP_NEW = "sha256:" + "d" * 64
HELD = "The Engine held this change for confirmation"
STARTED = (
    "Backfill started — the story is paused while it re-derives turns 1–3; the new "  # noqa: RUF001
    "configuration applies when it completes"
)


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def presets_answer() -> dict[str, Any]:
    """#292's presets, shuffled (the Engine's order isn't the ladder)."""
    body: dict[str, Any] = m7("presets")
    by_id = {p["id"]: p for p in body["items"]}
    body["items"] = [by_id[MAX_ID], by_id[LITE_ID], by_id[FULL_ID], by_id[STANDARD_ID]]
    return body


def full_list() -> dict[str, Any]:
    """Every profile, newest first: two customs interleaved with the four presets."""
    presets = {p["id"]: p for p in m7("presets")["items"]}
    custom = other_profile(id=CUSTOM, name="Tuned", current_version=5, version=5)
    items = [custom, presets[FULL_ID], other_profile(), presets[LITE_ID]]
    items += [presets[STANDARD_ID], presets[MAX_ID]]
    return {"items": items, "next_cursor": None}


def config_body(**changes: Any) -> Any:
    return effective_config(story_id=STORY, **changes)


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    yield from fresh_cache()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    answer_relations(fake)
    answer_guarantees(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_config, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=config_body())
    fake.respond("GET", ITEM, 200, json=profile())
    fake.respond("GET", LIST, 200, json=full_list())
    fake.respond("GET", LIST, 200, json=presets_answer(), query={"preset": "true"})
    fake.respond("GET", SCHEMA, 200, json=schema_body())
    fake.respond("GET", "/stories/{story_id}", 200, json=m7("story"))
    fake.respond("GET", VERSIONS, 200, json=engine_fixture("list_engine_profile_versions"))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def requests(engine: FakeEngine, method: str, suffix: str = "") -> list[Any]:
    return [r for r in engine.requests if r.method == method and r.url.path.endswith(suffix)]


def sent(request: Any) -> Any:
    return json.loads(request.content)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def optgroups(html: str) -> list[tuple[str, list[str]]]:
    select = html.split('<select id="assign-profile-id"', 1)[1].split("</select>", 1)[0]
    return [
        (label, re.findall(r'<option value="([^"]*)"', body))
        for label, body in re.findall(r'<optgroup label="([^"]*)">(.*?)</optgroup>', select, re.S)
    ]


def preset_rows(html: str) -> dict[str, str]:
    table = html.split('<table class="presets">', 1)[1].split("</table>", 1)[0]
    return dict(re.findall(r'<tr id="preset-([^"]+)">(.*?)</tr>', table, re.S))


def backfill_report() -> dict[str, Any]:
    """#292's one-story impact with ``backfill.needed`` (turns 1–3)."""  # noqa: RUF002
    report: dict[str, Any] = m7("impact_preview_backfill")["impact"]
    return report


def no_backfill_report() -> dict[str, Any]:
    return impact(affected(STORY))


def preview_full(client: Client, engine: FakeEngine, report: dict[str, Any]) -> str:
    engine.respond("GET", ITEM, 200, json=m7("presets")["items"][1])
    engine.respond("POST", PREVIEW, 200, json=report)
    response = client.post(ASSIGN, {"profile_id": FULL_ID})
    assert response.status_code == 200
    engine.reset()
    return str(response.content.decode())


def confirm(client: Client, html: str, url: str = ASSIGN_APPLY, **extra: str) -> Any:
    data = {
        "confirm_token": hidden(html, "confirm_token"),
        "idempotency_key": hidden(html, "idempotency_key"),
        **extra,
    }
    return client.post(url, data)


def applied_write(report: dict[str, Any], **result: Any) -> dict[str, Any]:
    body = config_body(
        profile={"id": FULL_ID, "version": 4, "pinned": False, "deleted": False},
        fingerprint=FP_NEW,
        **result,
    )
    return {
        "timeline_revision": 5,
        "config_revision": 9,
        "applied": True,
        "result": body,
        "impact": report,
        "idempotency_key": "01JM7KY0000000000000000009",
    }


# --- the picker -----------------------------------------------------------------------------------


def test_picker_lists_presets_first(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(CONFIG).content.decode()
    groups = optgroups(html)
    assert [label for label, _ in groups] == ["Presets", "Other profiles"]
    assert groups[0][1] == [LITE_ID, STANDARD_ID, FULL_ID, MAX_ID]
    assert f'<option value="{LITE_ID}" disabled>Lite (preset) · v2 (current)</option>' in html
    assert f'<option value="{FULL_ID}">Full (preset) · v4</option>' in html
    # Complement: no preset in "Other profiles", and the customs keep the list's order.
    assert groups[1][1] == [CUSTOM, OTHER]
    assert not set(groups[1][1]) & set(PRESET_IDS)
    assert f'<option value="{CUSTOM}">Tuned · v5 · {CUSTOM[:6]}</option>' in html
    [read] = [r for r in requests(engine, "GET", LIST) if r.url.params.get("preset")]
    assert read.url.params["preset"] == "true"
    assert read.url.params["limit"] == str(ENGINE_PROFILES_PAGE)
    assert "cursor" not in read.url.params


def test_preset_table_rows(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(CONFIG).content.decode()
    rows = preset_rows(html)
    assert list(rows) == [LITE_ID, STANDARD_ID, FULL_ID, MAX_ID]
    assert "<th>Preset</th><th>Version</th><th>Quality</th><th>Guarantees</th>" in html
    full = rows[FULL_ID]
    assert "<td>Full (preset)</td><td>v4</td>" in full
    assert '<span class="quality quality-certified" title="certified">Certified</span>' in full
    assert "Floors passed: NFR-QUAL-2.false_contradiction" in unescape(full)
    lite = rows[LITE_ID]
    assert re.search(r'<span class="quality[^"]*" title="uncertified">Uncertified</span>', lite)
    assert '<span class="quality-floors">' not in lite
    for preset_id, row in rows.items():
        assert f'<a href="/profiles/{preset_id}/#guarantees">Guarantees</a>' in row
    for preset_id in (STANDARD_ID, FULL_ID, MAX_ID):
        assert (
            f'<button type="submit" name="profile_id" value="{preset_id}">Preview</button>'
            in (rows[preset_id])
        )
    # Complement: the current preset has no button, and no guarantee table was read.
    assert "<button" not in lite
    assert "(current)" in lite
    assert not [
        r for r in engine.requests if re.fullmatch(r"/engine-profiles/[^/]+/guarantees", r.url.path)
    ]


def test_a_preset_button_previews_that_preset(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", PREVIEW, 200, json=no_backfill_report())
    operator_client.post(ASSIGN, {"profile_id": STANDARD_ID, "profile_choice": OTHER})
    [call] = requests(engine, "POST", "/impact-preview")
    assert sent(call) == {"profile_id": STANDARD_ID}


def test_preset_read_fails_select_still_groups(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 503, json=error("database_unavailable"), query={"preset": "true"})
    html = operator_client.get(CONFIG).content.decode()
    assert PRESETS_UNAVAILABLE in html
    assert '<table class="presets">' not in html
    groups = optgroups(html)
    assert groups[0] == ("Presets", [LITE_ID, STANDARD_ID, FULL_ID, MAX_ID])
    assert groups[1] == ("Other profiles", [CUSTOM, OTHER])


def test_a_failed_full_list_keeps_the_preset_table(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 503, json=error("database_unavailable"))
    engine.respond("GET", LIST, 200, json=presets_answer(), query={"preset": "true"})
    html = operator_client.get(CONFIG).content.decode()
    assert PROFILES_UNAVAILABLE in html
    assert '<select id="assign-profile-id"' not in html
    assert list(preset_rows(html)) == [LITE_ID, STANDARD_ID, FULL_ID, MAX_ID]
    assert f'name="profile_id" value="{FULL_ID}">Preview</button>' in html
    assert PRESETS_UNAVAILABLE not in html


def test_both_reads_failing_render_no_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 503, json=error("database_unavailable"))
    engine.respond("GET", LIST, 503, json=error("database_unavailable"), query={"preset": "true"})
    html = operator_client.get(CONFIG).content.decode()
    assert PROFILES_UNAVAILABLE in html
    assert PRESETS_UNAVAILABLE in html
    assert 'class="assign-form"' not in html


def test_no_presets_shows_one_line_and_no_empty_group(
    operator_client: Client, engine: FakeEngine
) -> None:
    customs = {"items": [other_profile()], "next_cursor": None}
    engine.respond("GET", LIST, 200, json=customs)
    engine.respond(
        "GET", LIST, 200, json={"items": [], "next_cursor": None}, query={"preset": "true"}
    )
    html = operator_client.get(CONFIG).content.decode()
    assert '<p class="muted no-presets">No presets</p>' in html
    assert [label for label, _ in optgroups(html)] == ["Other profiles"]


def test_too_many_keeps_the_presets_table(operator_client: Client, engine: FakeEngine) -> None:
    many = [
        other_profile(id=f"01JPRF{n:020d}", name=f"Custom {n}") for n in range(PROFILES_MAX + 1)
    ]
    engine.respond("GET", LIST, 200, json={"items": many, "next_cursor": None})
    html = operator_client.get(CONFIG).content.decode()
    assert '<input type="text" id="assign-profile-id" name="profile_choice" value="">' in html
    assert "Too many to list — enter the id" in html
    assert list(preset_rows(html)) == [LITE_ID, STANDARD_ID, FULL_ID, MAX_ID]
    assert '<select id="assign-profile-id"' not in html


def test_enter_in_the_id_input_previews_the_typed_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(CONFIG).content.decode()
    form = html.split('class="assign-form">', 1)[1].split("</form>", 1)[0]
    first = re.search(r"<button[^>]*>", form)
    assert first is not None
    assert first.group(0) == (
        '<button type="submit" class="visually-hidden" tabindex="-1" aria-hidden="true">'
    )
    assert form.index(first.group(0)) < form.index('<table class="presets">')
    # An implicit submission posts the default (unnamed) button: only the typed choice.
    engine.respond("POST", PREVIEW, 200, json=no_backfill_report())
    operator_client.post(ASSIGN, {"profile_choice": OTHER})
    [call] = requests(engine, "POST", "/impact-preview")
    assert sent(call) == {"profile_id": OTHER}


# --- the upgrade-mode choice ----------------------------------------------------------------------


def test_preview_offers_upgrade_mode_when_a_backfill_is_needed(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview_full(operator_client, engine, backfill_report())
    assert '<fieldset class="upgrade-mode">' in html
    assert "<legend>Earlier turns</legend>" in html
    radios = re.findall(r'<input type="radio" name="upgrade_mode"[^>]*>', html)
    assert len(radios) == 2
    [chosen] = [r for r in radios if 'value="backfill"' in r]
    assert "checked" in chosen
    assert all('form="config-confirm"' in r for r in radios)
    assert (
        '<form method="post" action="' + ASSIGN_APPLY + '" class="two-stage" id="config-confirm">'
        in html
    )
    assert "Backfill the earlier turns (the story pauses while it runs)" in html
    assert "Forward only (earlier turns keep a coverage gap)" in html
    assert (
        '<p class="backfill-line">12 model calls, 18,000 tokens, ~40 s · turns 1–3</p>'  # noqa: RUF001
        in html
    )
    assert ">Apply anyway</button>" in html


def test_preview_without_a_backfill_offers_no_mode(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview_full(operator_client, engine, no_backfill_report())
    assert 'name="upgrade_mode"' not in html
    assert 'id="config-confirm"' not in html
    assert '<fieldset class="upgrade-mode">' not in html


def test_an_archived_storys_preview_leaves_out_the_mode(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories/{story_id}", 200, json={**m7("story"), "status": "archived"})
    html = preview_full(operator_client, engine, backfill_report())
    assert 'name="upgrade_mode"' not in html
    assert "Archived — unarchive to apply" in html


def test_a_one_turn_backfill_reads_turn(operator_client: Client, engine: FakeEngine) -> None:
    report = backfill_report()
    report["affected_stories"][0]["backfill"]["range"] = {"from_turn": 3, "to_turn": 3}
    html = preview_full(operator_client, engine, report)
    assert "~40 s · turn 3</p>" in html


@pytest.mark.parametrize("mode", ["forward_only", "backfill"])
def test_apply_sends_the_chosen_mode(
    operator_client: Client, engine: FakeEngine, mode: str
) -> None:
    html = preview_full(operator_client, engine, backfill_report())
    engine.respond("PUT", PUT, 200, json=applied_write(backfill_report()))
    response = confirm(operator_client, html, upgrade_mode=mode)
    assert response.status_code == 302
    [call] = requests(engine, "PUT")
    assert sent(call) == {"profile_id": FULL_ID, "ignore_warnings": True, "upgrade_mode": mode}


def test_a_choice_free_confirm_sends_mains_body(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview_full(operator_client, engine, no_backfill_report())
    engine.respond("PUT", PUT, 200, json=applied_write(no_backfill_report()))
    confirm(operator_client, html, upgrade_mode="forward_only")  # not offered: ignored
    [call] = requests(engine, "PUT")
    assert sent(call) == {"profile_id": FULL_ID, "ignore_warnings": False}


@pytest.mark.parametrize(
    ("posted", "message"),
    [
        ({}, "This field is required."),
        ({"upgradeMode": "forward_only"}, "This field is required."),
        ({"upgrade_mode": "upgradeMode"}, "Select a valid choice."),
    ],
    ids=["absent", "camel-case", "invalid"],
)
def test_missing_mode_rerenders_without_a_call(
    operator_client: Client, engine: FakeEngine, posted: dict[str, str], message: str
) -> None:
    html = preview_full(operator_client, engine, backfill_report())
    key = hidden(html, "idempotency_key")
    response = confirm(operator_client, html, **posted)
    assert response.status_code == 200
    page = response.content.decode()
    assert message in unescape(page)
    assert re.search(r'<input type="radio" name="upgrade_mode"[^>]*form="config-confirm"', page)
    assert hidden(page, "idempotency_key") == key
    assert "Preview: assign Full" in page
    assert "turns 1–3" in page  # noqa: RUF001
    # Complement: no write and no fresh report.
    assert requests(engine, "PUT") == []
    assert requests(engine, "POST") == []
    # The re-rendered token still confirms.
    engine.respond("PUT", PUT, 200, json=applied_write(backfill_report()))
    confirm(operator_client, page, upgrade_mode="forward_only")
    [call] = requests(engine, "PUT")
    assert sent(call)["upgrade_mode"] == "forward_only"


def test_a_held_rerender_keeps_the_posted_mode(operator_client: Client, engine: FakeEngine) -> None:
    html = preview_full(operator_client, engine, backfill_report())
    engine.respond("PUT", PUT, 200, json=m7("impact_preview_backfill"))
    page = confirm(operator_client, html, upgrade_mode="forward_only").content.decode()
    assert HELD in page
    [chosen] = re.findall(
        r'<input type="radio" name="upgrade_mode" value="forward_only"[^>]*>', page
    )
    assert "checked" in chosen


def test_the_signed_report_round_trips(operator_client: Client) -> None:
    for name in ("impact_preview_backfill", "upgrade_pinned_impact", "assign_backfill_started"):
        parsed = ImpactReport.from_json(m7(name)["impact"])
        assert parsed is not None
        assert ImpactReport.from_json(json.loads(json.dumps(model_json(parsed)))) == parsed
    pinned = ImpactReport.from_json(m7("upgrade_pinned_impact")["impact"])
    assert pinned is not None
    assert pinned.certifications_lost
    needed = ImpactReport.from_json(backfill_report())
    assert needed is not None
    assert needs_upgrade_mode(needed)
    free = ImpactReport.from_json(no_backfill_report())
    assert free is not None
    assert not needs_upgrade_mode(free)


def test_overrides_apply_sends_the_mode(operator_client: Client, engine: FakeEngine) -> None:
    page = operator_client.get(OVERRIDES_PAGE)
    assert page.status_code == 200
    engine.reset()
    engine.respond("POST", PREVIEW, 200, json=backfill_report())
    response = operator_client.post(OVERRIDES_PAGE, form_data(page, **{field_of(WINDOW): "20"}))
    html = response.content.decode()
    assert re.search(r'<input type="radio" name="upgrade_mode" value="backfill"[^>]*checked', html)
    assert f'action="{OVERRIDES_APPLY}" class="two-stage" id="config-confirm"' in html
    engine.reset()
    engine.respond("PATCH", PATCH, 200, json=applied_write(backfill_report()))
    confirm(operator_client, html, OVERRIDES_APPLY, upgrade_mode="forward_only")
    [call] = requests(engine, "PATCH")
    body = sent(call)
    assert body["upgrade_mode"] == "forward_only"
    assert body["ignore_warnings"] is True
    assert body["settings"] == {"memory": {"rolling_summary": {"window_messages": 20}}}


def test_overrides_missing_mode_rerenders_without_a_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = operator_client.get(OVERRIDES_PAGE)
    engine.respond("POST", PREVIEW, 200, json=backfill_report())
    html = operator_client.post(
        OVERRIDES_PAGE, form_data(page, **{field_of(WINDOW): "20"})
    ).content.decode()
    engine.reset()
    again = confirm(operator_client, html, OVERRIDES_APPLY).content.decode()
    assert "This field is required." in again
    assert "Change sent" in again
    assert "window_messages" in again
    assert requests(engine, "PATCH") == []


def test_version_change_offers_the_mode(operator_client: Client, engine: FakeEngine) -> None:
    config = operator_client.get(CONFIG).content.decode()
    section = config.split('class="version-form">', 1)[1]
    engine.reset()
    engine.respond("PUT", PUT, 200, json=m7("impact_preview_backfill"))
    response = operator_client.post(
        VERSION_CHANGE,
        {
            "version": "1",
            "confirm_token": hidden(section, "confirm_token"),
            "idempotency_key": hidden(section, "idempotency_key"),
        },
    )
    html = response.content.decode()
    [first] = requests(engine, "PUT")
    assert "upgrade_mode" not in sent(first)
    assert re.search(r'<input type="radio" name="upgrade_mode" value="backfill"[^>]*checked', html)
    assert "Preview: pin to v1" in html
    engine.reset()
    engine.respond("PUT", PUT, 200, json=applied_write(backfill_report()))
    confirm(operator_client, html, upgrade_mode="forward_only")
    [call] = requests(engine, "PUT")
    assert sent(call) == {
        "profile_id": LITE,
        "version": 1,
        "ignore_warnings": True,
        "upgrade_mode": "forward_only",
    }


# --- the two outcomes -----------------------------------------------------------------------------


def engine_shaped_202() -> dict[str, Any]:
    """The Engine's documented 202: ``applied: false``, no result, the backfill started."""
    body = m7("assign_backfill_started")
    return {**body, "applied": False, "result": None}


@pytest.mark.parametrize(
    "answer", [m7("assign_backfill_started"), engine_shaped_202()], ids=["fixture", "applied-false"]
)
def test_backfill_202_redirects_to_the_backfill(
    operator_client: Client, engine: FakeEngine, answer: dict[str, Any]
) -> None:
    with override_settings(ROOT_URLCONF=M7_URLS):
        html = preview_full(operator_client, engine, backfill_report())
        engine.respond("PUT", PUT, 202, json=answer)
        response = confirm(operator_client, html, upgrade_mode="backfill")
        assert response.status_code == 302
        assert response["Location"] == BACKFILL_PAGE
        [flash] = flashes(response)
    assert flash == (
        f'{STARTED} · <a href="{BACKFILL_PAGE}">Follow the backfill</a> — idempotency key '
        "<code>01JM7KY0000000000000000001</code>"
    )
    # Complement: never the held preview.
    assert HELD not in response.content.decode()
    assert HELD not in flash
    assert len(requests(engine, "PUT")) == 1
    assert EngineOperation.objects.filter(kind="assign_engine_profile").count() == 1


def test_backfill_202_before_the_backfill_page_lands_on_config(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview_full(operator_client, engine, backfill_report())
    engine.respond("PUT", PUT, 202, json=engine_shaped_202())
    response = confirm(operator_client, html, upgrade_mode="backfill")
    assert response.status_code == 302
    assert response["Location"] == CONFIG
    [flash] = flashes(response)
    assert flash.startswith(f"{STARTED} · Follow the backfill — idempotency key <code>")
    assert "<a " not in flash


def test_backfill_202_without_a_range_drops_the_turns(
    operator_client: Client, engine: FakeEngine
) -> None:
    report = backfill_report()
    del report["affected_stories"][0]["backfill"]["range"]
    html = preview_full(operator_client, engine, report)
    answer = engine_shaped_202()
    del answer["impact"]["affected_stories"][0]["backfill"]["range"]
    engine.respond("PUT", PUT, 202, json=answer)
    [flash] = flashes(confirm(operator_client, html, upgrade_mode="backfill"))
    assert flash.startswith(
        "Backfill started — the story is paused; the new configuration applies when it completes"
    )


def test_forward_only_flash_names_the_gap(operator_client: Client, engine: FakeEngine) -> None:
    html = preview_full(operator_client, engine, backfill_report())
    report = backfill_report()
    report["affected_stories"][0]["forward_only_gap"] = {
        "capabilities": ["episodic"],
        "through_turn": 3,
    }
    engine.respond("PUT", PUT, 200, json=applied_write(report))
    with override_settings(ROOT_URLCONF=M7_URLS):
        [flash] = flashes(confirm(operator_client, html, upgrade_mode="forward_only"))
    assert flash.endswith(
        ". Turns through 3 keep a coverage gap for: <code>episodic</code> — fill it from the "
        f'<a href="{BACKFILL_PAGE}">Backfill page</a>'
    )
    assert flash.startswith("Applied — fingerprint")
    assert "idempotency key" not in flash


def test_without_a_gap_the_flash_is_mains(operator_client: Client, engine: FakeEngine) -> None:
    html = preview_full(operator_client, engine, backfill_report())
    engine.respond("PUT", PUT, 200, json=applied_write(backfill_report()))
    [flash] = flashes(confirm(operator_client, html, upgrade_mode="forward_only"))
    assert flash == (
        f'Applied — fingerprint <code title="{FP_NEW}">{"d" * 12}</code>; takes effect from the '
        "next turn"
    )


def test_the_gap_sentence_is_plain_text_before_the_backfill_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview_full(operator_client, engine, backfill_report())
    report = backfill_report()
    report["affected_stories"][0]["forward_only_gap"] = {
        "capabilities": ["episodic", "commitment"],
        "through_turn": 3,
    }
    engine.respond("PUT", PUT, 200, json=applied_write(report))
    [flash] = flashes(confirm(operator_client, html, upgrade_mode="forward_only"))
    assert flash.endswith(
        "<code>episodic</code>, <code>commitment</code> — fill it from the Backfill page"
    )


def test_paused_by_backfill_refusal_uses_s3b_text(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview_full(operator_client, engine, backfill_report())
    refusal = error(
        "story_paused",
        "paused",
        story_id=STORY,
        cause="backfill",
        job_id="01JM7JB0000000000000000003",
    )
    engine.respond("PUT", PUT, 409, json=refusal)
    page = confirm(operator_client, html, upgrade_mode="backfill").content.decode()
    shown = text(page)
    assert "Paused: backfill in progress" in shown
    assert "/rating-check/" not in page
    assert "check/01JM7JB" not in page
    assert HELD not in page

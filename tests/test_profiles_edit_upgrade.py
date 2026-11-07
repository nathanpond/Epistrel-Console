"""A profile edit's "Apply anyway" asks the upgrade mode when its report needs a backfill, sends
it, and names the backfills the edit queued (#311)."""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.test import Client, override_settings
from django.utils.html import escape
from django.utils.safestring import SafeString

from console import twostage
from console.forms.upgrades import UPGRADE_MODE_HELP
from console.views import profile_edit
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.golden import normalise_html
from tests.profile_helpers import (
    ITEM,
    OTHER,
    STORY,
    VALIDATE,
    affected,
    body_of,
    edit_body,
    fake_engine,
    field,
    fresh_cache,
    impact,
    other_profile,
    posted,
    story,
    validation,
)
from tests.ratings.helpers import calls, hidden, text

pytestmark = pytest.mark.django_db

EDIT = f"/profiles/{OTHER}/edit/"
APPLY = f"/profiles/{OTHER}/apply/"
DETAIL = f"/profiles/{OTHER}/"
POLISH = "repair.polish.max_regenerations"
M7_PAGES = "tests.urls_with_m7_pages"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "profile_edit_m6"

ST1 = "01JM7ST0000000000000000001"
ST2 = "01JM7ST0000000000000000002"
NAMES = {ST1: "Harbour", ST2: "Lantern"}


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    yield from fresh_cache()


def _named_story(request: httpx.Request) -> httpx.Response:
    story_id = request.url.path.rsplit("/", 1)[-1]
    if story_id not in NAMES:
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "no story"}})
    return httpx.Response(200, json=story(story_id, NAMES[story_id]))


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    for fake in fake_engine(monkeypatch):
        fake.respond("GET", ITEM, 200, json=other_profile())
        fake.respond("POST", VALIDATE, 200, json=validation())
        fake.side_effect("GET", STORY, _named_story)
        yield fake


def m7(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m7/{name}"))
    return body


def flashes(response: Any) -> list[Any]:
    return [m.message for m in get_messages(response.wsgi_request)]


def puts(engine: FakeEngine) -> list[Any]:
    return calls(engine, "PUT")


def backfill_held() -> dict[str, Any]:
    """#292's held edit: one following story with history needs a backfill (ST1), one doesn't."""
    return m7("profile_edit_backfill_impact")


def behavioural_held() -> dict[str, Any]:
    """A budget-only edit's held report: no story needs a backfill."""
    report = impact(affected(ST1), affected(ST2), warnings=["polish_off"])
    return edit_body(False, None, report)


def first_stage(client: Client, engine: FakeEngine, held: dict[str, Any]) -> str:
    """The edit page, a Save that the Engine holds with ``held``: the impact page's HTML."""
    page = client.get(EDIT)
    assert page.status_code == 200
    engine.respond("PUT", ITEM, 200, json=held)
    response = client.post(EDIT, posted(page, action="save", **{field(POLISH): "0"}))
    assert response.status_code == 200
    html: str = response.content.decode()
    engine.reset()
    return html


def confirm(html: str, **extra: str) -> dict[str, str]:
    return {
        "confirm_token": hidden(html, "confirm_token"),
        "idempotency_key": hidden(html, "idempotency_key"),
        **extra,
    }


def applied(*backfills: dict[str, Any], version: int = 4, **impact_changes: Any) -> dict[str, Any]:
    body = m7("profile_edit_backfills_applied")
    body["result"] = other_profile(version=version, current_version=version)
    body["backfills"] = list(backfills)
    body["impact"].update(impact_changes)
    return body


def queued(story_id: str, position: int | None, n: int) -> dict[str, Any]:
    return {
        "story_id": story_id,
        "id": f"01JM7BF000000000000000000{n}",
        "job_id": f"01JM7JB000000000000000000{n}",
        "status": "queued",
        "queue_position": position,
    }


def radios(html: str) -> list[str]:
    return re.findall(r'<input type="radio" name="upgrade_mode"[^>]*>', html)


# --- the choice on the impact page ----------------------------------------------------------------


def test_impact_page_offers_the_mode_when_a_backfill_is_needed(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    shown = text(html)
    found = radios(html)
    assert len(found) == 2
    assert all('form="profile-apply"' in r for r in found)
    (checked,) = [r for r in found if "checked" in r]
    assert 'value="backfill"' in checked
    assert '<form method="post" action="/profiles/' in html
    assert 'class="two-stage" id="profile-apply"' in html
    assert html.count("Apply anyway") == 1  # the partial's own form is not rendered
    assert escape(UPGRADE_MODE_HELP) in html
    assert "1 following story gains data-producing capability with history" in shown
    # The fieldset sits above the confirm form's buttons.
    assert html.index('class="upgrade-mode"') < html.index('id="profile-apply"')
    # #310's columns still render through the shared partial.
    assert '<td class="mode">' in html
    assert '<td class="gap">' in html
    base = twostage.unsign(profile_edit.APPLY_KIND, hidden(html, "confirm_token")).base
    assert base["upgrade_choice"] is True
    assert base["impact"]["affected_stories"][0]["backfill"]["needed"] is True


def test_the_count_line_counts_several_and_the_unlisted_tail(
    operator_client: Client, engine: FakeEngine
) -> None:
    held = backfill_held()
    stories = held["impact"]["affected_stories"]
    stories[1]["backfill"] = copy.deepcopy(stories[0]["backfill"])
    held["impact"]["affected_count"] = 1002
    shown = text(first_stage(operator_client, engine, held))
    assert (
        "2 following stories gain data-producing capability with history "
        "(of the first 1,000 listed)"
    ) in shown


def test_a_behavioural_only_impact_renders_mains_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = first_stage(operator_client, engine, behavioural_held())
    assert radios(html) == []
    assert 'id="profile-apply"' not in html
    golden = (GOLDEN / "impact_behavioural.html").read_text()
    start = html.index('<section class="impact">')
    end = html.index("</section>", start) + len("</section>")
    region = re.sub(r'value="[^"]{40,}"', 'value="TOKEN"', html[start:end])
    assert normalise_html(region) == normalise_html(golden)
    base = twostage.unsign(profile_edit.APPLY_KIND, hidden(html, "confirm_token")).base
    assert "upgrade_choice" not in base
    assert "impact" not in base


# --- the choice is sent ---------------------------------------------------------------------------


def test_apply_sends_the_mode(operator_client: Client, engine: FakeEngine) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    engine.respond("PUT", ITEM, 200, json=applied())
    response = operator_client.post(APPLY, confirm(html, upgrade_mode="forward_only"))
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    (put,) = puts(engine)
    sent = body_of(put)
    assert sent["upgrade_mode"] == "forward_only"
    assert sent["ignore_warnings"] is True
    assert "upgradeMode" not in sent


def test_a_choice_free_payload_sends_no_mode(operator_client: Client, engine: FakeEngine) -> None:
    html = first_stage(operator_client, engine, behavioural_held())
    engine.respond("PUT", ITEM, 200, json=edit_body(True, other_profile(version=4)))
    # A stray posted mode is ignored without the signed choice.
    response = operator_client.post(APPLY, confirm(html, upgrade_mode="forward_only"))
    assert response.status_code == 302
    (put,) = puts(engine)
    sent = body_of(put)
    assert "upgrade_mode" not in sent
    assert sent["ignore_warnings"] is True


@pytest.mark.parametrize("mode", [None, "", "sideways"])
def test_missing_mode_rerenders_without_confirming(
    operator_client: Client, engine: FakeEngine, mode: str | None
) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    token = hidden(html, "confirm_token")
    data = confirm(html) if mode is None else confirm(html, upgrade_mode=mode)
    response = operator_client.post(APPLY, data)
    page = response.content.decode()
    assert response.status_code == 200
    assert 'class="errorlist' in page
    assert len(radios(page)) == 2
    assert "Impact report" in text(page)
    assert hidden(page, "confirm_token") == token  # the posted token, unchanged
    assert hidden(page, "idempotency_key") != data["idempotency_key"]  # E21: a fresh key
    # No write at all: main's version-check GET and the story-label reads are page reads.
    assert puts(engine) == []
    assert {r.method for r in engine.requests} <= {"GET"}


def test_missing_mode_complement_the_fake_saw_no_put(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    operator_client.post(APPLY, confirm(html))
    assert [r for r in engine.requests if r.method != "GET"] == []


def test_a_moved_version_keeps_mains_path_before_the_mode_check(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    engine.respond("GET", ITEM, 200, json=other_profile(version=9, current_version=9))
    response = operator_client.post(APPLY, confirm(html))
    assert "now version 9" in text(response.content.decode())
    assert puts(engine) == []


def test_an_unrebuildable_signed_report_reads_expired(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    payload = twostage.unsign(profile_edit.APPLY_KIND, hidden(html, "confirm_token"))
    forged = twostage.sign(
        profile_edit.APPLY_KIND, OTHER, payload.body, {**payload.base, "impact": "nope"}
    )
    response = operator_client.post(APPLY, {"confirm_token": forged, "idempotency_key": "k"})
    assert response.status_code == 302
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert puts(engine) == []


def test_a_reheld_confirm_resets_the_choice(operator_client: Client, engine: FakeEngine) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    engine.respond("PUT", ITEM, 200, json=backfill_held())
    response = operator_client.post(APPLY, confirm(html, upgrade_mode="forward_only"))
    page = response.content.decode()
    assert response.status_code == 200
    (checked,) = [r for r in radios(page) if "checked" in r]
    assert 'value="backfill"' in checked
    base = twostage.unsign(profile_edit.APPLY_KIND, hidden(page, "confirm_token")).base
    assert base["upgrade_choice"] is True


# --- the applied flash ----------------------------------------------------------------------------


def test_backfills_flash_in_queue_order(operator_client: Client, engine: FakeEngine) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    engine.respond("PUT", ITEM, 200, json=applied(queued(ST2, 2, 2), queued(ST1, 1, 1)))
    with override_settings(ROOT_URLCONF=M7_PAGES):
        response = operator_client.post(APPLY, confirm(html, upgrade_mode="backfill"))
    (flash,) = flashes(response)
    assert isinstance(flash, SafeString)
    assert flash == (
        "Saved — version 4; 1 story follows it from its next turn; backfills queued for 2 "
        "stories — each pauses only while its own runs: "
        f'<a href="/stories/{ST1}/backfill/">Harbour</a> (#1), '
        f'<a href="/stories/{ST2}/backfill/">Lantern</a> (#2)'
    )


def test_backfills_flash_is_plain_text_while_backfill_is_unregistered(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_edit, "backfill_url", lambda story_id: None)
    html = first_stage(operator_client, engine, backfill_held())
    engine.respond(
        "PUT",
        ITEM,
        200,
        json=applied(queued(ST1, 1, 1), queued("01JM7ST0000000000000000009", None, 9)),
    )
    response = operator_client.post(APPLY, confirm(html, upgrade_mode="backfill"))
    (flash,) = flashes(response)
    assert "<a" not in flash
    assert flash.endswith(
        "backfills queued for 2 stories — each pauses only while its own runs: "
        "Harbour (#1), 01JM7ST0000000000000000009"
    )


def test_no_backfills_flash_equals_mains(operator_client: Client, engine: FakeEngine) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    engine.respond("PUT", ITEM, 200, json=applied())
    response = operator_client.post(APPLY, confirm(html, upgrade_mode="backfill"))
    (flash,) = flashes(response)
    assert flash == "Saved — version 4; 1 story follows it from its next turn"
    assert not isinstance(flash, SafeString)


def test_backfills_flash_caps_its_links(operator_client: Client, engine: FakeEngine) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    many = [queued(ST1, n, 1) | {"id": f"01JM7BF{n:019d}"} for n in range(1, 28)]
    engine.respond("PUT", ITEM, 200, json=applied(*many))
    response = operator_client.post(APPLY, confirm(html, upgrade_mode="backfill"))
    (flash,) = flashes(response)
    assert "backfills queued for 27 stories" in flash
    assert "(#25), …and 2 more" in flash
    assert "(#26)" not in flash
    assert len([r for r in engine.requests if r.url.path.startswith("/stories/")]) == 1


def test_forward_only_gap_sentence(operator_client: Client, engine: FakeEngine) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    body = applied()
    body["impact"]["affected_stories"][0]["forward_only_gap"] = {
        "capabilities": ["commitment"],
        "through_turn": 3,
    }
    engine.respond("PUT", ITEM, 200, json=body)
    response = operator_client.post(APPLY, confirm(html, upgrade_mode="forward_only"))
    (flash,) = flashes(response)
    assert flash == (
        "Saved — version 4; 1 story follows it from its next turn; earlier turns keep a "
        "coverage gap (fill it from each story's Backfill page)"
    )


def test_a_gap_after_a_backfill_choice_adds_no_gap_sentence(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = first_stage(operator_client, engine, backfill_held())
    body = applied()
    body["impact"]["affected_stories"][0]["forward_only_gap"] = {
        "capabilities": ["commitment"],
        "through_turn": 3,
    }
    engine.respond("PUT", ITEM, 200, json=body)
    response = operator_client.post(APPLY, confirm(html, upgrade_mode="backfill"))
    (flash,) = flashes(response)
    assert "coverage gap" not in flash


def test_the_applied_fixture_round_trips(operator_client: Client, engine: FakeEngine) -> None:
    """#292's applied fixture as the Engine answers it (one backfill queued, #1)."""
    html = first_stage(operator_client, engine, backfill_held())
    engine.respond("PUT", ITEM, 200, json=m7("profile_edit_backfills_applied"))
    response = operator_client.post(APPLY, confirm(html, upgrade_mode="backfill"))
    (flash,) = flashes(response)
    # stories:backfill is registered (#308), so the real urlconf links it too.
    assert str(flash).endswith(f'<a href="/stories/{ST1}/backfill/">Harbour</a> (#1)')
    (message,) = get_messages(response.wsgi_request)
    assert "safe" in message.extra_tags.split()
    assert json.loads(puts(engine)[0].content)["upgrade_mode"] == "backfill"

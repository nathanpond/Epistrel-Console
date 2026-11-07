"""Upgrade a profile's pinned stories from its page, after their impact (#310, E8)."""

import json
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.core import signing
from django.http import HttpRequest
from django.test import Client, override_settings

from console import twostage
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import profile_edit, profile_upgrade, profiles
from tests.backfill.helpers import m7
from tests.fakes.engine import FakeEngine
from tests.fakes.guarantees import answer_guarantees
from tests.fakes.ownership import answer_relations
from tests.profile_helpers import SCHEMA, SETTINGS, deployment, fresh_cache, schema_body, story
from tests.ratings.helpers import calls, error, hidden, text

pytestmark = pytest.mark.django_db

FULL = "00000000000000000000PRF002"
STORY_1 = "01JM7ST0000000000000000001"
STORY_2 = "01JM7ST0000000000000000002"
URL = f"/profiles/{FULL}/upgrade-pinned/"
DETAIL = f"/profiles/{FULL}/"
ITEM = "/engine-profiles/{profile_id}"
UPGRADE = "/engine-profiles/{profile_id}/upgrade-pinned"
UPGRADE_PATH = f"/engine-profiles/{FULL}/upgrade-pinned"
STORY = "/stories/{story_id}"
M7_URLS = "tests.urls_with_m7_pages"


def full(**changes: Any) -> dict[str, Any]:
    (found,) = [p for p in m7("presets")["items"] if p["id"] == FULL]
    return {**found, **changes}


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

    for module in (profiles, profile_edit, profile_upgrade):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    fake.respond("GET", SETTINGS, 200, json=deployment())
    fake.respond("GET", SCHEMA, 200, json=schema_body())
    fake.respond("GET", ITEM, 200, json=full())
    fake.respond("GET", STORY, 200, json=story(STORY_1, "Harbour"))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def held_answer(engine: FakeEngine) -> None:
    engine.respond("POST", UPGRADE, 200, json=m7("upgrade_pinned_impact"))


def applied(*, both: bool = True) -> dict[str, Any]:
    """``upgrade_pinned_applied``; ``both`` adds the impact fixture's second pinned story (no
    history, so nothing queued), as the Demo's two-story upgrade."""
    answer = m7("upgrade_pinned_applied")
    if both:
        second = m7("upgrade_pinned_impact")["impact"]["affected_stories"][1]
        answer["impact"]["affected_stories"].append(second)
        answer["impact"]["affected_count"] = 2
    return answer


def applied_answer(engine: FakeEngine, *, both: bool = True) -> None:
    engine.respond("POST", UPGRADE, 200, json=applied(both=both))


def upgrades(engine: FakeEngine) -> list[Any]:
    return calls(engine, "POST", UPGRADE_PATH)


def body(request: Any) -> Any:
    return json.loads(request.content)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def form_key(html: str) -> str:
    return hidden(html, "idempotency_key")


def first_stage(client: Client, mode: str = "forward_only") -> Any:
    page = client.get(URL)
    return client.post(
        URL, {"upgrade_mode": mode, "idempotency_key": form_key(page.content.decode())}
    )


def confirm_data(html: str, **extra: str) -> dict[str, str]:
    two_stage = html[html.index('class="two-stage"') :]
    return {
        "confirm_token": hidden(two_stage, "confirm_token"),
        "idempotency_key": hidden(two_stage, "idempotency_key"),
        **extra,
    }


# --- the action and the form ---------------------------------------------------------------------


def test_the_profile_page_links_the_action_after_copy(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(DETAIL).content.decode()
    actions = html[html.index('class="profile-actions"') :]
    actions = actions[: actions.index("</p>")]
    link = f'<a class="button upgrade-pinned" href="{URL}">Upgrade pinned stories…</a>'
    assert link in actions
    assert actions.index('class="button copy"') < actions.index(link)


def test_get_reads_only_the_profile(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(URL)
    html = response.content.decode()
    shown = text(html)
    assert response.status_code == 200
    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("GET", f"/engine-profiles/{FULL}")
    ]
    assert "Upgrade pinned stories — Full" in shown
    assert (
        "Moves every story pinned to an older version of Full to v4, after showing what changes "
        "for each"
    ) in shown
    assert response.context["form"]["upgrade_mode"].value() == "backfill"
    assert re.search(r'value="backfill"[^>]*checked', html)
    assert "Only matters when the change turns on something that produces data" in shown
    assert "Show the impact" in shown
    # Complement: the GET writes nothing.
    assert calls(engine, "POST") == []


def test_the_mode_link_preselects_and_an_invalid_one_is_ignored(
    operator_client: Client, engine: FakeEngine
) -> None:
    chosen = operator_client.get(f"{URL}?upgrade_mode=forward_only").context["form"]
    assert chosen["upgrade_mode"].value() == "forward_only"
    ignored = operator_client.get(f"{URL}?upgrade_mode=sideways").context["form"]
    assert ignored["upgrade_mode"].value() == "backfill"


def test_malformed_profile_id_404_no_call(operator_client: Client, engine: FakeEngine) -> None:
    for method in ("get", "post"):
        response = getattr(operator_client, method)("/profiles/not-an-id/upgrade-pinned/")
        assert response.status_code == 404
        assert "No such profile" in text(response.content.decode())
    assert engine.requests == []


def test_deleted_profile_has_no_action(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=full(deleted_at="2026-11-01T09:00:00Z"))
    detail = operator_client.get(DETAIL).content.decode()
    assert "Upgrade pinned stories" not in detail
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert "This profile is deleted — assign the stories another profile instead" in text(html)
    assert 'name="upgrade_mode"' not in html
    # A first stage that finds it deleted sends nothing.
    engine.reset()
    posted = operator_client.post(URL, {"upgrade_mode": "backfill"})
    assert "This profile is deleted" in text(posted.content.decode())
    assert upgrades(engine) == []


def test_invalid_mode_no_call(operator_client: Client, engine: FakeEngine) -> None:
    held_answer(engine)
    for data in ({"upgrade_mode": "sideways"}, {}):
        response = operator_client.post(URL, data)
        html = response.content.decode()
        assert response.status_code == 200
        assert 'class="errorlist' in html
        assert 'name="upgrade_mode"' in html
    assert upgrades(engine) == []


def test_an_unknown_profile_reads_no_such_profile(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 404, json=error("not_found", "no such profile"))
    response = operator_client.get(URL)
    assert response.status_code == 404
    assert "No such profile" in text(response.content.decode())


# --- the first stage ------------------------------------------------------------------------------


def test_first_stage_sends_the_mode_without_ignore(
    operator_client: Client, engine: FakeEngine
) -> None:
    held_answer(engine)
    page = operator_client.get(URL)
    key = form_key(page.content.decode())
    operator_client.post(URL, {"upgrade_mode": "forward_only", "idempotency_key": key})
    (sent,) = upgrades(engine)
    assert body(sent) == {"ignore_warnings": False, "upgrade_mode": "forward_only"}
    assert sent.headers["Idempotency-Key"] == key
    # Complement: never the camel-case names.
    raw = sent.content.decode()
    assert "ignoreWarnings" not in raw
    assert "upgradeMode" not in raw


def test_impact_page_signs_the_confirm(operator_client: Client, engine: FakeEngine) -> None:
    held_answer(engine)
    response = first_stage(operator_client)
    html = response.content.decode()
    shown = text(html)
    assert response.status_code == 200
    assert (
        "The Engine held this upgrade for confirmation: nothing was written. Review what it does "
        "to each pinned story under Forward only, then upgrade anyway or go back."
    ) in shown
    assert "Upgrade mode: Forward only" in shown
    assert "Impact report" in shown
    assert "Upgrade anyway" in shown
    # The per-story Mode and Gap columns, and the certification the change loses.
    assert '<td class="mode">Forward only</td>' in html
    assert '<td class="gap">No data for commitment through turn 4</td>' in html
    assert "Certifications lost" in shown
    assert "Certified → Uncertified" in shown
    # Cancel goes to the profile page; "Change the upgrade mode" back to the form.
    two_stage = html[html.index('class="two-stage"') :]
    assert f'<a class="button" href="{DETAIL}">Cancel</a>' in two_stage
    assert f'href="{URL}?upgrade_mode=forward_only">Change the upgrade mode</a>' in html
    payload = twostage.unsign("upgrade-pinned", hidden(two_stage, "confirm_token"))
    assert payload.target_id == FULL
    assert payload.body == {"ignore_warnings": True, "upgrade_mode": "forward_only"}
    assert payload.base["name"] == "Full"
    assert payload.base["current_version"] == 4
    # A fresh key for the confirm, not the first stage's.
    assert hidden(two_stage, "idempotency_key") != upgrades(engine)[0].headers["Idempotency-Key"]


def test_one_label_read_names_every_story(operator_client: Client, engine: FakeEngine) -> None:
    held_answer(engine)
    html = first_stage(operator_client).content.decode()
    story_reads = sorted(
        r.url.path for r in calls(engine, "GET") if r.url.path.startswith("/stories/")
    )
    assert story_reads == [f"/stories/{STORY_1}", f"/stories/{STORY_2}"]
    assert f'<a href="/stories/{STORY_1}/">Harbour <code>{STORY_1}</code></a>' in html


# --- confirm and result --------------------------------------------------------------------------


def test_confirm_resends_and_lists_backfills(operator_client: Client, engine: FakeEngine) -> None:
    held_answer(engine)
    html = first_stage(operator_client).content.decode()
    data = confirm_data(html)
    applied_answer(engine)
    engine.reset()
    response = operator_client.post(URL, data)
    page = response.content.decode()
    shown = text(page)
    assert response.status_code == 200
    (sent,) = upgrades(engine)
    assert body(sent) == {"ignore_warnings": True, "upgrade_mode": "forward_only"}
    assert sent.headers["Idempotency-Key"] == data["idempotency_key"]
    # No profile read on confirm: the facts come from the token.
    assert [r for r in engine.requests if r.url.path == f"/engine-profiles/{FULL}"] == []
    assert "Upgraded 2 pinned stories to v4" in shown
    assert "Upgrade mode: Forward only" in shown
    assert "Queued backfills" in shown
    table = page[page.index('<table class="queued-backfills">') :]
    table = table[: table.index("</table>")]
    assert f'<a href="/stories/{STORY_1}/">Harbour <code>{STORY_1}</code></a>' in table
    assert "<code>01JM7JB0000000000000000002</code>" in table
    assert '<td class="queue-position">1</td>' in table
    assert (
        "Each story keeps its previous version, unpaused, until its backfill starts; it pauses "
        "only while its own runs"
    ) in shown
    assert "Upgrade anyway" not in shown
    assert "Change the upgrade mode" not in shown
    assert f"Idempotency key {data['idempotency_key']}" in shown


def test_applied_at_once_goes_straight_to_the_result(
    operator_client: Client, engine: FakeEngine
) -> None:
    applied_answer(engine, both=False)
    html = first_stage(operator_client, "backfill").content.decode()
    shown = text(html)
    assert "Upgraded 1 pinned story to v4" in shown
    assert "Upgrade mode: Backfill" in shown
    assert "confirm_token" not in html
    # The Backfill cell's range suffix.
    assert "12 model calls, 18,000 tokens, ~40 s (turns 1–3)" in shown  # noqa: RUF001


def test_a_tampered_mode_beside_the_token_is_ignored(
    operator_client: Client, engine: FakeEngine
) -> None:
    held_answer(engine)
    html = first_stage(operator_client).content.decode()
    applied_answer(engine)
    engine.reset()
    operator_client.post(URL, confirm_data(html, upgrade_mode="backfill"))
    (sent,) = upgrades(engine)
    assert body(sent)["upgrade_mode"] == "forward_only"


def test_an_expired_token_makes_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    held_answer(engine)
    html = first_stage(operator_client).content.decode()
    data = confirm_data(html)
    engine.reset()
    stale = signing.loads(data["confirm_token"], salt="console.twostage.upgrade-pinned")
    for token in (
        "forged",
        twostage.sign("upgrade-pinned", "01JPRF00000000000000000002", stale["body"], stale["base"]),
        twostage.sign("profile-apply", FULL, stale["body"], stale["base"]),
        twostage.sign(
            "upgrade-pinned", FULL, {"ignore_warnings": True, "upgrade_mode": "x"}, stale["base"]
        ),
    ):
        response = operator_client.post(URL, {**data, "confirm_token": token})
        assert response.status_code == 302
        assert response["Location"] == URL
        assert twostage.EXPIRED_TEXT in flashes(response)
    assert engine.requests == []


def test_an_old_token_has_expired(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    held_answer(engine)
    data = confirm_data(first_stage(operator_client).content.decode())
    engine.reset()
    monkeypatch.setattr(twostage, "MAX_AGE_S", -1)
    unsign = twostage.unsign
    monkeypatch.setattr(
        twostage, "unsign", lambda kind, token, max_age=-1: unsign(kind, token, max_age=max_age)
    )
    response = operator_client.post(URL, data)
    assert twostage.EXPIRED_TEXT in flashes(response)
    assert engine.requests == []


@override_settings(ROOT_URLCONF=M7_URLS)
def test_backfill_link_d19(operator_client: Client, engine: FakeEngine) -> None:
    applied_answer(engine)
    html = first_stage(operator_client).content.decode()
    assert (
        f'<td class="backfill-link"><a href="/stories/{STORY_1}/backfill/">Backfill</a></td>'
        in html
    )


def test_backfill_link_plain_without_the_page(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Complement (D19): with ``stories:backfill`` unregistered the cell is plain text."""
    monkeypatch.setattr(profile_upgrade, "backfill_url", lambda story_id: None)
    applied_answer(engine)
    html = first_stage(operator_client).content.decode()
    assert '<td class="backfill-link">Backfill</td>' in html


def test_queued_backfills_sort_by_position_nulls_last() -> None:
    from console.engine.models import QueuedBackfill

    def queued(story_id: str, position: int | None) -> QueuedBackfill:
        return QueuedBackfill(
            "01JM7BF0000000000000000001", "01JM7JB0000000000000000001", "queued", story_id, position
        )

    rows = profile_upgrade.queued_rows(
        [queued("C", None), queued("B", 2), queued("A", 1), queued("D", None)], {}
    )
    assert [r.story_id for r in rows] == ["A", "B", "C", "D"]
    assert [r.queue_text for r in rows] == ["1", "2", "—", "—"]
    assert rows[0].status_text == "Queued"


def test_none_pinned(operator_client: Client, engine: FakeEngine) -> None:
    answer = m7("upgrade_pinned_applied")
    answer["impact"]["affected_stories"] = []
    answer["impact"]["affected_count"] = 0
    answer["backfills"] = []
    engine.respond("POST", UPGRADE, 200, json=answer)
    response = first_stage(operator_client)
    html = response.content.decode()
    assert "No story is pinned to an older version of Full — nothing to upgrade" in text(html)
    # Complement: no confirm form, and only one POST.
    assert "confirm_token" not in html
    assert len(calls(engine, "POST")) == 1


def test_a_reheld_confirm_shows_the_new_report(operator_client: Client, engine: FakeEngine) -> None:
    held_answer(engine)
    first = confirm_data(first_stage(operator_client).content.decode())
    again = m7("upgrade_pinned_impact")
    again["impact"]["affected_stories"][0]["queue_position"] = 3
    engine.respond("POST", UPGRADE, 200, json=again)
    response = operator_client.post(URL, first)
    html = response.content.decode()
    shown = text(html)
    assert (
        "The Engine held this upgrade again: the report changed since you saw it, and nothing was "
        "written. Review it, then upgrade anyway or go back."
    ) in shown
    assert '<td class="mode">Forward only · queue #3</td>' in html
    assert "Change the upgrade mode" in shown
    second = confirm_data(html)
    assert second["confirm_token"] != first["confirm_token"]
    assert second["idempotency_key"] != first["idempotency_key"]


def test_refusals_make_no_second_call(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", UPGRADE, 404, json=error("not_found", "no such profile"))
    response = first_stage(operator_client)
    assert response.status_code == 404
    assert "No such profile" in text(response.content.decode())
    assert len(upgrades(engine)) == 1


def test_a_confirm_refusal_keeps_the_confirm(operator_client: Client, engine: FakeEngine) -> None:
    held_answer(engine)
    data = confirm_data(first_stage(operator_client).content.decode())
    engine.reset()
    engine.fail("POST", UPGRADE, httpx.ConnectError("down"))
    response = operator_client.post(URL, data)
    html = response.content.decode()
    assert 'class="engine-error' in html
    again = confirm_data(html)
    # An unknown outcome keeps the same token and key, so a resubmit replays.
    assert again == data
    assert len(upgrades(engine)) == 1
    assert [r for r in engine.requests if r.url.path.startswith("/stories/")] == []
    assert "Certifications lost" in text(html)


def test_recorded_against_the_profile(operator_client: Client, engine: FakeEngine) -> None:
    held_answer(engine)
    first_stage(operator_client)
    row = EngineOperation.objects.get(kind="upgrade_pinned_stories")
    assert (row.entity_type, row.entity_id) == ("engine_profile", FULL)


def test_the_token_carries_the_report() -> None:
    """``base`` round-trips the report it was signed with (a failed confirm re-renders it)."""
    from console.engine.models import ImpactReport

    report = ImpactReport.from_json(m7("upgrade_pinned_impact")["impact"])
    assert report is not None
    facts = profile_upgrade.Facts(FULL, "Full", 4)
    rebuilt = ImpactReport.from_json(json.loads(json.dumps(facts.base(report)["report"])))
    assert rebuilt == report

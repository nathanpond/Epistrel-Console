"""Offboard a principal after a dry run, and follow the job to its report (#77)."""

import json
import re
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from django.contrib.auth.models import User
from django.core import signing
from django.http import HttpRequest
from django.test import Client, override_settings

from console import twostage
from console.engine.client import EngineClient, EngineError, EngineUnreachable
from console.engine_access import engine_client_for
from console.job_poll import RETRYING_TEXT, poll_failure
from console.models import EngineOperation, KnownPrincipal
from console.views import offboarding
from console.views.offboarding import (
    APPLY_ANYWAY,
    NOT_FOUND,
    NOTHING,
    START_LABEL,
    counts_table,
    shown_report,
)
from tests.fakes.engine import FakeEngine
from tests.ratings.helpers import error, text

pytestmark = pytest.mark.django_db

OFFBOARD = "/admin/offboarding"
JOB = "/admin/offboarding/{job_id}"
NEW = "/offboarding/new/"
START = "/offboarding/start/"
JOB_ID = "01JQ0000000000000000J0B001"
STORY = "01JQ0000000000000000ST0RY1"
MEDIA = "01JQ0000000000000000MED1A1"
CHAR = "01JQ0000000000000000CHAR01"
SCEN = "01JQ0000000000000000SCEN01"
PROFILE = "01JQ0000000000000000PR0F01"
TYPES = ("stories", "characters", "scenarios", "personas", "media", "engine_profiles")


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the offboarding pages talk to, through the real recording client."""
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(offboarding, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def counts(**changes: dict[str, int]) -> dict[str, dict[str, int]]:
    base: dict[str, dict[str, int]] = {t: {"delete": 0, "transfer": 0, "keep": 0} for t in TYPES}
    base["relations"] = {"remove": 0, "keep": 0}
    base.update(changes)
    return base


def warning(kind: str = "shared_story", **changes: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "entity_type": "story",
        "entity_id": STORY,
        "principals": ["carol"],
        **changes,
    }


def plan(warnings: list[dict[str, Any]] | None = None, **changes: Any) -> dict[str, Any]:
    return {
        "applied": False,
        "config_revision": 7,
        "counts": counts(stories={"delete": 0, "transfer": 2, "keep": 0}),
        "warnings": warnings or [],
        "idempotency_key": "k",
        **changes,
    }


def queued(**changes: Any) -> dict[str, Any]:
    return {
        "applied": True,
        "config_revision": 8,
        "job_id": JOB_ID,
        "counts": counts(stories={"delete": 0, "transfer": 2, "keep": 0}),
        "warnings": [warning()],
        "idempotency_key": "k2",
        **changes,
    }


def job(status: str = "running", **changes: Any) -> dict[str, Any]:
    return {
        "job_id": JOB_ID,
        "principal": "alice",
        "status": status,
        "progress": {"done": 1, "total": 3},
        "current": {"type": "stories", "id": STORY} if status == "running" else None,
        "counts": counts(stories={"delete": 0, "transfer": 2, "keep": 0}),
        "report": None,
        "error": None,
        "request": {
            "principal": "alice",
            "actions": {"stories": "transfer", "media": "delete"},
            "transfer_to": "bob",
        },
        "created_by": "console:nate",
        "created_at": "2026-10-25T10:00:00Z",
        "finished_at": None,
        **changes,
    }


def report(**changes: Any) -> dict[str, Any]:
    return {
        "deleted": {"media": [MEDIA], "stories": []},
        "transferred": {
            "stories": [{"id": STORY, "to": "bob"}],
            "scenarios": [{"id": SCEN, "to": "bob", "companion_of": CHAR}],
            "media": [{"id": MEDIA, "to": "bob", "reason": "referenced"}],
        },
        "removed": {"relations": [{"entity_type": "story", "id": STORY, "relations": ["player"]}]},
        "kept": {},
        "skipped": {
            "personas": [{"id": "P1", "reason": "name_taken"}],
            "engine_profiles": [{"id": PROFILE, "reason": "default_profile"}],
            "stories": [{"id": STORY, "reason": "weird_reason"}],
        },
        "remaining": {
            "owned": {"stories": [], "media": [MEDIA]},
            "owned_counts": {"stories": 0, "media": 1},
            "related": {},
            "related_counts": {},
        },
        **changes,
    }


def form(**changes: str) -> dict[str, str]:
    data = {"principal": "alice", "transfer_to": ""}
    for kind in (*TYPES, "relations"):
        data[f"actions__{kind}"] = "keep"
    data.update(changes)
    return data


def offboard_calls(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == OFFBOARD]


def sent(request: httpx.Request) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(request.content)
    return body


def token_of(html: str) -> str:
    match = re.search(r'name="confirm_token" value="([^"]*)"', html)
    assert match, "no start payload"
    return match.group(1)


def preview(client: Client, engine: FakeEngine, answer: dict[str, Any], **changes: str) -> str:
    engine.respond("POST", OFFBOARD, 200, json=answer)
    response = client.post(NEW, form(actions__stories="transfer", transfer_to="bob", **changes))
    assert response.status_code == 200
    return response.content.decode()


# --- the form -------------------------------------------------------------------------------------


def test_the_form_prefills_the_principal_and_shows_the_rules(operator_client: Client) -> None:
    html = operator_client.get(NEW, {"principal": "alice"}).content.decode()
    assert 'name="principal" value="alice"' in html
    for kind in (*TYPES, "relations"):
        assert f'name="actions__{kind}"' in html
    assert '<option value="keep" selected>Keep</option>' in html
    for note in (
        "Companions follow their character",
        "Media used by a transferred catalog entry moves with it",
        "Deleting a story deletes it for every participant",
        "Deleting media or a profile retires it",
        "The default profile is never deleted",
    ):
        assert note in html
    assert "Required when any type is Transfer" in html
    assert "Preview (dry run)" in html
    assert "confirm_token" not in html  # no way to start from the form


def test_the_form_maps_to_actions(operator_client: Client, engine: FakeEngine) -> None:
    preview(operator_client, engine, plan(), actions__media="delete", actions__relations="remove")
    (call,) = offboard_calls(engine)
    assert sent(call) == {
        "principal": "alice",
        "actions": {
            "stories": "transfer",
            "characters": "keep",
            "scenarios": "keep",
            "personas": "keep",
            "media": "delete",
            "engine_profiles": "keep",
            "relations": "remove",
        },
        "transfer_to": "bob",
        "dry_run": True,
        "ignore_warnings": False,
    }
    assert call.headers["X-Epistrel-Principal"] == "console:nate"
    assert set(KnownPrincipal.objects.values_list("principal", flat=True)) == {"alice", "bob"}


def test_a_transfer_target_without_transfer_is_not_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", OFFBOARD, 200, json=plan())
    operator_client.post(NEW, form(actions__media="delete", transfer_to="bob"))
    (call,) = offboard_calls(engine)
    assert "transfer_to" not in sent(call)


def test_the_transfer_target_is_required_client_side(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.post(NEW, form(actions__stories="transfer")).content.decode()
    assert "Required when any type is Transfer." in html
    assert offboard_calls(engine) == []


def test_an_all_keep_plan_is_refused_with_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.post(NEW, form()).content.decode()
    assert "Choose at least one action other than Keep" in html
    assert offboard_calls(engine) == []


def test_transferring_to_the_principal_itself_is_refused_with_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form(actions__stories="transfer", transfer_to=" alice\t")
    html = operator_client.post(NEW, data).content.decode()
    assert "Can&#x27;t be the principal being offboarded." in html
    data = form(principal="system", actions__media="delete")
    assert "is reserved for the Engine" in operator_client.post(NEW, data).content.decode()
    assert offboard_calls(engine) == []


# --- the preview ----------------------------------------------------------------------------------


def test_the_preview_is_a_dry_run_with_counts_and_warnings(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview(operator_client, engine, plan([warning(), warning("mystery_kind")]))
    (call,) = offboard_calls(engine)
    assert sent(call)["dry_run"] is True
    page = text(html)
    assert "Stories 0 2 0" in page
    assert "Relations — — — 0 0" not in page  # relations have no delete/transfer cells
    assert "Will be deleted for everyone — others are related to it" in page
    assert f'<a href="/stories/{STORY}/"><code>{STORY}</code></a>' in html
    assert '<a href="/principals/carol/"><code>carol</code></a>' in html
    assert "<code>mystery_kind</code>" in html
    assert APPLY_ANYWAY in html
    assert START_LABEL not in html
    assert "Edit plan" in html
    assert EngineOperation.objects.get().kind == "offboard"


def test_the_counts_table_takes_unknown_keys_as_columns() -> None:
    table = counts_table({"stories": {"delete": 1, "keep": 2, "frozen": 3}, "gadgets": {"x": 1}})
    assert table.columns == ["delete", "keep", "frozen", "x"]
    assert table.rows == [("Stories", ["1", "2", "3", "—"]), ("gadgets", ["—", "—", "—", "1"])]


def test_edit_plan_round_trips_the_choices(operator_client: Client, engine: FakeEngine) -> None:
    html = preview(operator_client, engine, plan())
    edit = re.search(r'class="edit-plan" href="([^"]+)"', html)
    assert edit
    url = edit.group(1).replace("&amp;", "&")
    page = operator_client.get(url).content.decode()
    assert 'name="transfer_to" value="bob"' in page
    assert '<option value="transfer" selected>Transfer</option>' in page


def test_a_plan_that_changes_nothing_offers_no_start(
    operator_client: Client, engine: FakeEngine
) -> None:
    nothing = plan(counts=counts(stories={"delete": 0, "transfer": 0, "keep": 4}))
    html = preview(operator_client, engine, nothing)
    assert NOTHING in html
    assert "confirm_token" not in html


def test_offboarding_myself_or_the_acting_principal_warns(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview(operator_client, engine, plan(), principal="console:nate")
    assert "You are offboarding your own principal." in html
    operator_client.post("/acting-as/", {"other": "alice", "next": "/"})
    html = preview(operator_client, engine, plan())
    assert "You are offboarding the principal you are acting as." in html


# --- the start ------------------------------------------------------------------------------------


def test_start_without_warnings_sends_the_real_run_under_a_new_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview(operator_client, engine, plan())
    assert START_LABEL in html
    engine.respond("POST", OFFBOARD, 202, json=queued())
    response = operator_client.post(START, {"confirm_token": token_of(html)})
    assert response.status_code == 302
    assert response["Location"] == f"/offboarding/{JOB_ID}/"
    dry, real = offboard_calls(engine)
    assert sent(real) == {**sent(dry), "dry_run": False, "ignore_warnings": False}
    assert dry.headers["Idempotency-Key"] != real.headers["Idempotency-Key"]
    rows = EngineOperation.objects.order_by("id")
    assert [r.outcome for r in rows] == ["ok", "queued"]
    assert rows[1].job_id == JOB_ID
    assert (rows[1].entity_type, rows[1].entity_id) == ("principal", "alice")


def test_apply_anyway_adds_ignore_warnings(operator_client: Client, engine: FakeEngine) -> None:
    html = preview(operator_client, engine, plan([warning()]))
    engine.respond("POST", OFFBOARD, 202, json=queued())
    operator_client.post(START, {"confirm_token": token_of(html)})
    real = offboard_calls(engine)[-1]
    assert sent(real)["ignore_warnings"] is True
    assert sent(real)["dry_run"] is False


def test_the_start_reads_only_the_payload(operator_client: Client, engine: FakeEngine) -> None:
    html = preview(operator_client, engine, plan())
    engine.respond("POST", OFFBOARD, 202, json=queued())
    token = token_of(html)
    operator_client.post(
        START, {"confirm_token": token, "idempotency_key": "TAMPERED", "principal": "mallory"}
    )
    real = offboard_calls(engine)[-1]
    payload = twostage.unsign("offboard", token)
    assert real.headers["Idempotency-Key"] == payload.base["key"]
    assert sent(real)["principal"] == "alice"


def test_a_start_held_by_new_warnings_re_renders_the_preview(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview(operator_client, engine, plan())
    first = token_of(html)
    engine.respond("POST", OFFBOARD, 200, json=plan([warning("profile_in_use")]))
    response = operator_client.post(START, {"confirm_token": first})
    assert response.status_code == 200
    again = response.content.decode()
    assert "Will be retired — other owners&#x27; stories use it" in again
    assert APPLY_ANYWAY in again
    second = token_of(again)
    assert (
        twostage.unsign("offboard", second).base["key"]
        != (twostage.unsign("offboard", first).base["key"])
    )
    assert twostage.unsign("offboard", second).body["ignore_warnings"] is True


def test_posting_the_start_without_a_preview_payload_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    for data in ({}, {"confirm_token": "nonsense"}):
        response = operator_client.post(START, data, follow=True)
        assert twostage.EXPIRED_TEXT in response.content.decode()
    forged = twostage.sign("other-kind", "alice", {"principal": "alice"})
    operator_client.post(START, {"confirm_token": forged})
    assert operator_client.get(START).status_code == 405
    assert offboard_calls(engine) == []


def test_a_payload_from_another_operator_or_acting_principal_is_refused(
    operator_client: Client, engine: FakeEngine
) -> None:
    token = token_of(preview(operator_client, engine, plan()))
    operator_client.post("/acting-as/", {"other": "zed", "next": "/"})
    response = operator_client.post(START, {"confirm_token": token}, follow=True)
    assert twostage.EXPIRED_TEXT in response.content.decode()
    other = User.objects.create_superuser("olga", "olga@example.invalid", "pw-olga-123456")
    second = Client()
    second.force_login(other)
    second.post(START, {"confirm_token": token})
    assert len(offboard_calls(engine)) == 1  # the dry run only


def test_an_expired_payload_is_refused(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = token_of(preview(operator_client, engine, plan()))
    later = time.time() + twostage.MAX_AGE_S + 60
    monkeypatch.setattr(signing, "time", SimpleNamespace(time=lambda: later))
    response = operator_client.post(START, {"confirm_token": token}, follow=True)
    assert twostage.EXPIRED_TEXT in response.content.decode()
    assert len(offboard_calls(engine)) == 1


# --- errors ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "field"),
    [
        ("principal_reserved", "principal"),
        ("transfer_target_required", "transfer_to"),
        ("transfer_target_invalid", "transfer_to"),
    ],
)
def test_field_refusals_are_placed_at_their_fields(
    operator_client: Client, engine: FakeEngine, code: str, field: str
) -> None:
    engine.respond("POST", OFFBOARD, 422, json=error(code, f"refused: {code}"))
    html = operator_client.post(NEW, form(actions__stories="transfer", transfer_to="bob")).content
    page = html.decode()
    block = page[page.index(f'id="f-{field}"') :]
    block = block[: block.index("</p>")]
    assert f"refused: {code}" in block


def test_a_field_refusal_on_start_returns_to_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    token = token_of(preview(operator_client, engine, plan()))
    engine.respond("POST", OFFBOARD, 422, json=error("transfer_target_invalid", "no such target"))
    page = operator_client.post(START, {"confirm_token": token}).content.decode()
    block = page[page.index('id="f-transfer_to"') :]
    assert "no such target" in block[: block.index("</p>")]


def test_an_offboarding_in_progress_links_its_job(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        OFFBOARD,
        409,
        json=error("offboarding_in_progress", principal="alice", job_id=JOB_ID),
    )
    page = operator_client.post(NEW, form(actions__media="delete")).content.decode()
    assert "An offboarding is already running for" in page
    assert f'<a href="/offboarding/{JOB_ID}/">view it</a>' in page
    engine.respond("POST", OFFBOARD, 200, json=plan())
    token = token_of(preview(operator_client, engine, plan()))
    engine.respond("POST", OFFBOARD, 409, json=error("offboarding_in_progress"))
    page = operator_client.post(START, {"confirm_token": token}).content.decode()
    assert "An offboarding is already running for" in page
    assert "view it" not in page
    assert "confirm_token" not in page


def test_a_validation_error_on_the_dry_run_shows_the_panel_and_keeps_input(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", OFFBOARD, 422, json=error("validation_error", "bad"))
    page = operator_client.post(NEW, form(actions__media="delete")).content.decode()
    assert "Engine error 422 validation_error" in page
    assert '<option value="delete" selected>Delete</option>' in page


def test_a_conflict_on_start_shows_the_panel_without_a_payload(
    operator_client: Client, engine: FakeEngine
) -> None:
    token = token_of(preview(operator_client, engine, plan()))
    engine.respond("POST", OFFBOARD, 409, json=error("idempotency_conflict"))
    page = operator_client.post(START, {"confirm_token": token}).content.decode()
    assert "Engine error 409 idempotency_conflict" in page
    assert "confirm_token" not in page
    assert "Edit plan" in page


def test_an_unavailable_start_re_offers_the_same_payload(
    operator_client: Client, engine: FakeEngine
) -> None:
    token = token_of(preview(operator_client, engine, plan()))
    engine.respond("POST", OFFBOARD, 503, json=error("database_unavailable"))
    page = operator_client.post(START, {"confirm_token": token}).content.decode()
    assert "Engine error 503 database_unavailable" in page
    assert token_of(page) == token
    assert ">Retry<" in page
    assert "Stories" in text(page)  # the preview's counts are still shown
    engine.respond("POST", OFFBOARD, 202, json=queued())
    operator_client.post(START, {"confirm_token": token})
    keys = [r.headers["Idempotency-Key"] for r in offboard_calls(engine)[1:]]
    assert keys[0] == keys[1]


# --- the job page ---------------------------------------------------------------------------------


def start_job(client: Client, engine: FakeEngine) -> None:
    token = token_of(preview(client, engine, plan([warning()])))
    engine.respond("POST", OFFBOARD, 202, json=queued())
    client.post(START, {"confirm_token": token})


def test_the_job_page_polls_while_running(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", JOB, 200, json=job("running"))
    html = operator_client.get(f"/offboarding/{JOB_ID}/").content.decode()
    assert f'hx-get="/offboarding/{JOB_ID}/progress/" hx-trigger="every 2s"' in html
    assert '<progress value="1" max="3"></progress>' in html
    assert "1/3" in html
    assert f'<a href="/stories/{STORY}/"><code>{STORY}</code></a>' in html  # current
    assert 'href="/principals/alice/"' in html
    assert 'href="/principals/bob/"' in html
    assert "Refresh" in html
    assert "stories: transfer, media: delete → bob" in html
    partial = operator_client.get(f"/offboarding/{JOB_ID}/progress/").content.decode()
    assert partial.startswith('<div id="job-poll"')
    assert 'hx-trigger="every 2s"' in partial


def test_a_queued_job_waits_for_a_worker(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", JOB, 200, json=job("queued", progress={"done": 0, "total": 0}))
    html = operator_client.get(f"/offboarding/{JOB_ID}/progress/").content.decode()
    assert "Waiting for a worker" in html
    assert "<progress></progress>" in html
    assert "0/0" in html


def test_polling_stops_at_done_and_settles_the_row_once(
    operator_client: Client, engine: FakeEngine
) -> None:
    start_job(operator_client, engine)
    engine.respond(
        "GET", JOB, 200, json=job("done", report=report(), finished_at="2026-10-25T10:01:00Z")
    )
    partial = operator_client.get(f"/offboarding/{JOB_ID}/progress/").content.decode()
    assert "hx-trigger" not in partial
    assert "Refresh" not in partial
    row = EngineOperation.objects.get(job_id=JOB_ID)
    assert row.outcome == "ok"
    assert isinstance(row.response, dict)
    assert row.response["warnings"][0]["kind"] == "shared_story"  # the 202's answer is kept
    page = operator_client.get(f"/offboarding/{JOB_ID}/").content.decode()
    assert "Started over these warnings" in page
    assert EngineOperation.objects.get(job_id=JOB_ID).outcome == "ok"


def test_a_failed_job_shows_its_error_and_partial_report(
    operator_client: Client, engine: FakeEngine
) -> None:
    start_job(operator_client, engine)
    failed = job(
        "failed",
        report=report(),
        error={"class": "OperationalError", "message": "lost connection"},
    )
    engine.respond("GET", JOB, 200, json=failed)
    html = operator_client.get(f"/offboarding/{JOB_ID}/").content.decode()
    assert "<code>OperationalError</code>: lost connection" in html
    assert "Transferred" in html
    assert "hx-trigger" not in html
    row = EngineOperation.objects.get(job_id=JOB_ID)
    assert (row.outcome, row.error_code) == ("error", "OperationalError")


def test_the_report_renders_each_section(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", JOB, 200, json=job("done", report=report()))
    html = operator_client.get(f"/offboarding/{JOB_ID}/").content.decode()
    page = text(html)
    assert "Media — deleted (retired) (1)" in page
    assert "Stories (1)" in page
    assert "To: bob" in page
    assert f"Companion of: {CHAR}" in page
    assert "Reason: referenced" in page
    assert "Target already has one with this name" in page
    assert "Default profile — never deleted" in page
    assert "Reason: weird_reason" in page
    assert "Relations: player" in page
    assert "Still owned" in page
    assert f'<a href="/media/{MEDIA}/"><code>{MEDIA}</code></a>' in html
    sections = re.findall(r'<h3 id="report-(\w+)"', html)
    assert sections == ["deleted", "transferred", "removed", "skipped", "remaining"]


def test_an_empty_report_says_nothing_changed(operator_client: Client, engine: FakeEngine) -> None:
    buckets = ("deleted", "transferred", "removed", "kept", "skipped")
    empty: dict[str, Any] = {k: {"stories": []} for k in buckets}
    assert shown_report(empty).sections == []
    engine.respond("GET", JOB, 200, json=job("done", report=empty))
    html = operator_client.get(f"/offboarding/{JOB_ID}/").content.decode()
    assert "Nothing was changed." in html


def test_a_job_that_is_gone_says_so(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", JOB, 404, json=error("not_found"))
    response = operator_client.get(f"/offboarding/{JOB_ID}/")
    assert response.status_code == 404
    assert NOT_FOUND in response.content.decode()
    partial = operator_client.get(f"/offboarding/{JOB_ID}/progress/").content.decode()
    assert NOT_FOUND in partial
    assert "hx-trigger" not in partial


def test_an_unavailable_engine_keeps_polling(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", JOB, 503, json=error("database_unavailable"))
    partial = operator_client.get(f"/offboarding/{JOB_ID}/progress/").content.decode()
    assert RETRYING_TEXT in partial
    assert 'hx-trigger="every 2s"' in partial


def test_a_non_ulid_job_id_is_a_404_with_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    assert operator_client.get("/offboarding/not-a-job/").status_code == 404
    assert operator_client.get("/offboarding/01jq0000000000000000j0b001/").status_code == 404
    assert engine.requests == []


def test_poll_failure_classifies_errors(rf: Any) -> None:
    request = rf.get("/")
    assert poll_failure(request, EngineUnreachable("timeout")).retry
    assert poll_failure(request, EngineError("database_unavailable", "", {}, 503)).retry
    gone = poll_failure(request, EngineError("not_found", "", {}, 404))
    assert gone.not_found
    assert not gone.retry
    other = poll_failure(request, EngineError("validation_error", "", {}, 422))
    assert other.panel is not None
    assert not other.retry


# --- the list -------------------------------------------------------------------------------------


def test_the_list_shows_started_jobs_only(operator_client: Client, engine: FakeEngine) -> None:
    start_job(operator_client, engine)
    start_job(operator_client, engine)  # replayed 202: one job, newest row wins
    html = operator_client.get("/offboarding/").content.decode()
    assert html.count(f'<a href="/offboarding/{JOB_ID}/">') == 1
    assert "stories: transfer → bob" in html
    assert '<a href="/principals/alice/"><code>alice</code></a>' in html
    assert "queued" in html
    assert 'href="/offboarding/new/"' in html
    assert EngineOperation.objects.filter(kind="offboard").count() == 4  # two dry runs too


def test_the_list_is_empty_without_jobs(operator_client: Client, engine: FakeEngine) -> None:
    preview(operator_client, engine, plan())  # a dry run is not a job
    html = operator_client.get("/offboarding/").content.decode()
    assert "No offboardings started from this Console." in html
    assert engine.requests[-1].url.path == OFFBOARD  # the list made no call


def test_the_nav_and_principal_pages_link_offboarding(operator_client: Client) -> None:
    html = operator_client.get("/offboarding/").content.decode()
    nav = html[html.index('<nav class="shell">') : html.index("</nav>")]
    assert '<a href="/offboarding/" class="active">Offboarding</a>' in nav
    html = operator_client.get("/principals/").content.decode()
    assert '<a class="offboarding" href="/offboarding/">Offboarding</a>' in html

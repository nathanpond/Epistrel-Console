"""Starting a gap fill and finishing a paused backfill from the Backfill page (#309;
FR-PROF-9/10, E8, E21, E24), with ``m7/backfill_preview.json``, ``backfill_started.json``,
``backfill_resume_preview.json`` and ``full_v4()``."""

import json
import re
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.utils.html import escape

from console import twostage
from console.backfill import (
    CAPABILITIES_UNREAD,
    IN_PROGRESS_TEXT,
    NOTHING_TO_FILL,
    PAUSES_LINE,
)
from console.engine.models import Backfill, BackfillEstimate
from console.impact import backfill_text
from console.models import EngineOperation
from tests.backfill.helpers import BACKFILL_PATH, CONFIG_PATH, PAGE, STORY, config, m7
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

START = f"{PAGE}start/"
KEY = "01JM7KY0000000000000000009"
TOKEN = re.compile(r'name="confirm_token" value="([^"]+)"')
TWO_STAGE = re.compile(r'<form method="post" action="[^"]*" class="two-stage".*?</form>', re.S)


@pytest.fixture
def no_backfill(engine: FakeEngine) -> FakeEngine:
    engine.respond("GET", BACKFILL_PATH, 404, json=error_json("backfill_not_found"))
    return engine


def preview_body(**changes: Any) -> dict[str, Any]:
    body = m7("backfill_preview")
    body["backfill"]["range"] = {"from_turn": 1, "to_turn": 5}
    body["backfill"].update(changes)
    return body


def writes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST"]


def bodies(engine: FakeEngine) -> list[Any]:
    return [json.loads(r.content) for r in writes(engine)]


def post(client: Client, **data: Any) -> Any:
    return client.post(START, {"idempotency_key": KEY, **data})


def gap_section(html: str) -> str:
    found = re.search(r'<section class="gap-fill" id="gap-fill">(.*?)</section>', html, re.S)
    assert found is not None, "no #gap-fill section"
    return found.group(1)


def confirm_data(html: str) -> dict[str, str]:
    """What the two-stage form posts (token, its key, the hidden mode)."""
    [form] = TWO_STAGE.findall(html)
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form))


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def estimate_text() -> str:
    found = Backfill(needed=True, estimate=BackfillEstimate(12, 18000, 40.0))
    return backfill_text(found)


# --- the form -----------------------------------------------------------------------------------


def test_gap_fill_form_on_no_backfill_page(
    operator_client: Client, no_backfill: FakeEngine
) -> None:
    html = operator_client.get(PAGE).content.decode()
    section = gap_section(html)
    assert "Fill coverage gaps…" in section
    assert f'action="{START}"' in section
    assert '<input type="hidden" name="mode" value="start">' in section
    boxes = re.findall(r'<input type="checkbox" name="capabilities" value="([^"]+)"', section)
    assert boxes == list(config()["capabilities"])  # the effective config's, in its order
    assert not re.search(r'type="checkbox"[^>]*checked', section)
    assert '<button type="submit">Preview</button>' in section
    assert writes(no_backfill) == []


def test_a_running_backfill_shows_no_gap_fill_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(PAGE).content.decode()  # the extracting fixture
    assert 'id="gap-fill"' not in html
    assert f'action="{START}"' not in html


def test_a_completed_backfill_offers_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_completed"))
    section = gap_section(operator_client.get(PAGE).content.decode())
    assert '<button type="submit">Preview</button>' in section


def test_a_failed_config_read_shows_no_boxes_and_the_note(
    operator_client: Client, no_backfill: FakeEngine
) -> None:
    no_backfill.respond("GET", CONFIG_PATH, 503, json=error_json("unavailable"))
    section = gap_section(operator_client.get(PAGE).content.decode())
    assert 'type="checkbox"' not in section
    assert escape(CAPABILITIES_UNREAD) in section
    assert '<button type="submit">Preview</button>' in section


def test_a_paused_backfill_offers_finish_now_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_paused"))
    section = gap_section(operator_client.get(PAGE).content.decode())
    assert '<input type="hidden" name="mode" value="resume">' in section
    assert '<button type="submit">Finish now…</button>' in section
    assert 'type="checkbox"' not in section
    assert "Preview" not in section


# --- the preview --------------------------------------------------------------------------------


def test_preview_shows_estimate_and_range(operator_client: Client, no_backfill: FakeEngine) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 200, json=preview_body())
    response = post(operator_client, mode="start")
    html = response.content.decode()
    assert response.status_code == 200
    assert bodies(no_backfill) == [{"ignore_warnings": False}]
    section = gap_section(html)
    assert "Capabilities: episodic, commitment" in section
    assert "Turns 1–5" in section  # noqa: RUF001
    assert estimate_text() in section
    assert "12 model calls, 18,000 tokens, ~40 s" in section
    assert escape(PAUSES_LINE) in section
    assert '<button type="submit">Start the gap fill</button>' in section
    assert f'<a class="button" href="{PAGE}">Cancel</a>' in section
    # The complement: the preview POST is the only call; no 202, no queued job row.
    assert len(writes(no_backfill)) == 1
    assert not EngineOperation.objects.filter(outcome="queued").exists()


def test_ticked_capabilities_are_sent(operator_client: Client, no_backfill: FakeEngine) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 200, json=preview_body())
    post(operator_client, mode="start", capabilities=["episodic"])
    assert bodies(no_backfill) == [{"capabilities": ["episodic"], "ignore_warnings": False}]


def test_a_one_turn_range_and_no_capabilities(
    operator_client: Client, no_backfill: FakeEngine
) -> None:
    body = preview_body(range={"from_turn": 4, "to_turn": 4}, capabilities=[])
    no_backfill.respond("POST", BACKFILL_PATH, 200, json=body)
    section = gap_section(post(operator_client, mode="start").content.decode())
    assert "Turn 4" in section
    assert "Capabilities: none" in section


def test_start_resends_the_signed_body(operator_client: Client, no_backfill: FakeEngine) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 200, json=preview_body())
    html = post(operator_client, mode="start", capabilities=["episodic"]).content.decode()
    data = confirm_data(html)
    assert data["mode"] == "start"
    assert data["idempotency_key"] != KEY  # the partial's fresh key (#43)
    no_backfill.respond("POST", BACKFILL_PATH, 202, json=m7("backfill_started"))
    # A box tampered after the preview is ignored: the signed body is what's sent.
    response = operator_client.post(START, {**data, "capabilities": ["commitment"]})
    assert response.status_code == 303
    assert response["Location"] == PAGE
    _, second = writes(no_backfill)
    assert json.loads(second.content) == {"capabilities": ["episodic"], "ignore_warnings": True}
    assert second.headers["Idempotency-Key"] == data["idempotency_key"]
    [flash] = flashes(response)
    assert flash.startswith("Gap fill started — follow it below — idempotency key <code>")


def test_the_202_row_is_queued(operator_client: Client, no_backfill: FakeEngine) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 200, json=preview_body())
    data = confirm_data(post(operator_client, mode="start").content.decode())
    no_backfill.respond("POST", BACKFILL_PATH, 202, json=m7("backfill_started"))
    operator_client.post(START, data)
    assert EngineOperation.objects.filter(outcome="queued").count() == 1


def test_an_expired_token_makes_no_call(operator_client: Client, no_backfill: FakeEngine) -> None:
    response = operator_client.post(
        START, {"confirm_token": "tampered", "idempotency_key": KEY, "mode": "start"}
    )
    assert response.status_code == 303
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert no_backfill.requests == []


def test_a_start_token_cannot_be_replayed_as_a_resume(
    operator_client: Client, no_backfill: FakeEngine
) -> None:
    token = twostage.sign("backfill-start", STORY, {"ignore_warnings": True}, {})
    response = operator_client.post(
        START, {"confirm_token": token, "idempotency_key": KEY, "mode": "resume"}
    )
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert no_backfill.requests == []


def test_another_storys_token_is_expired(operator_client: Client, no_backfill: FakeEngine) -> None:
    token = twostage.sign("backfill-start", "01JM7ST0000000000000000009", {}, {})
    response = operator_client.post(
        START, {"confirm_token": token, "idempotency_key": KEY, "mode": "start"}
    )
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert no_backfill.requests == []


def test_not_needed_has_no_start(operator_client: Client, no_backfill: FakeEngine) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 200, json=preview_body(needed=False))
    html = post(operator_client, mode="start").content.decode()
    section = gap_section(html)
    assert escape(NOTHING_TO_FILL) in section
    assert 'class="two-stage"' not in section
    assert f'<a href="{PAGE}#gap-fill">Back</a>' in section


def test_nothing_to_backfill_gives_the_same_line(
    operator_client: Client, no_backfill: FakeEngine
) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 409, json=error_json("nothing_to_backfill"))
    response = post(operator_client, mode="start")
    section = gap_section(response.content.decode())
    assert response.status_code == 200
    assert escape(NOTHING_TO_FILL) in section
    assert len(writes(no_backfill)) == 1


def test_a_confirm_that_previews_again_renders_a_fresh_token(
    operator_client: Client, no_backfill: FakeEngine
) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 200, json=preview_body())
    data = confirm_data(post(operator_client, mode="start").content.decode())
    no_backfill.respond(
        "POST", BACKFILL_PATH, 200, json=preview_body(range={"from_turn": 1, "to_turn": 6})
    )
    html = operator_client.post(START, data).content.decode()
    assert "Turns 1–6" in gap_section(html)  # noqa: RUF001
    again = confirm_data(html)
    assert again["idempotency_key"] != data["idempotency_key"]


# --- finish now (resume) ------------------------------------------------------------------------


def resumed(**changes: Any) -> dict[str, Any]:
    body = {
        "timeline_revision": 21,
        "applied": True,
        "backfill": {"status": "completed"},
        "conflicts_open": 2,
        "conflicts_made_uncertain": 2,
    }
    body.update(changes)
    return body


def test_resume_two_stage(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_paused"))
    engine.respond("POST", BACKFILL_PATH, 200, json=m7("backfill_resume_preview"))
    response = post(operator_client, mode="resume")
    html = response.content.decode()
    assert response.status_code == 200
    assert bodies(engine) == [{"ignore_warnings": False}]
    section = gap_section(html)
    assert (
        "Finishing now makes the 2 open conflicts uncertain: the story carries each forward as "
        "an open question instead of a settled fact"
    ) in section
    assert "<li>Bob · <code>age</code></li>" in section
    assert "<li>Alice · <code>home</code></li>" in section
    assert '<button type="submit">Finish now</button>' in section
    # The complement: the first stage made no second call.
    assert len(writes(engine)) == 1
    data = confirm_data(html)
    assert data["mode"] == "resume"
    payload = twostage.unsign("backfill-resume", data["confirm_token"])
    assert payload.body == {"ignore_warnings": True}
    engine.respond("POST", BACKFILL_PATH, 200, json=resumed())
    confirmed = operator_client.post(START, data)
    assert confirmed.status_code == 303
    assert confirmed["Location"] == PAGE
    assert bodies(engine)[1] == {"ignore_warnings": True}
    [flash] = flashes(confirmed)
    assert flash.startswith("Backfill finished — 2 conflicts made uncertain — idempotency key")


def test_a_stale_start_on_a_paused_story_renders_the_resume_confirm(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_paused"))
    engine.respond("POST", BACKFILL_PATH, 200, json=m7("backfill_resume_preview"))
    html = post(operator_client, mode="start").content.decode()
    assert confirm_data(html)["mode"] == "resume"
    assert "2 open conflicts uncertain" in html


def test_a_stale_finish_now_on_an_ended_backfill_renders_the_gap_fill_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#352: a "Finish now…" posted after the backfill ended; the Engine answers 409
    ``nothing_to_backfill``, so the page follows it with the gap-fill form (``mode=start``)."""
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_completed"))
    engine.respond("POST", BACKFILL_PATH, 409, json=error_json("nothing_to_backfill"))
    response = post(operator_client, mode="resume")
    section = gap_section(response.content.decode())
    assert response.status_code == 200
    assert escape(NOTHING_TO_FILL) in section
    assert "Fill coverage gaps…" in section
    assert '<input type="hidden" name="mode" value="start">' in section
    assert '<button type="submit">Preview</button>' in section
    # The complement: none of the resume form is left.
    assert "Finish the backfill" not in section
    assert "Finish now" not in section
    assert 'name="mode" value="resume"' not in section
    assert len(writes(engine)) == 1


def test_a_stale_finish_now_confirm_on_an_ended_backfill_renders_the_gap_fill_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#352 at the confirm stage: the resume summary isn't kept; the gap-fill form shows."""
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_paused"))
    engine.respond("POST", BACKFILL_PATH, 200, json=m7("backfill_resume_preview"))
    data = confirm_data(post(operator_client, mode="resume").content.decode())
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_completed"))
    engine.respond("POST", BACKFILL_PATH, 409, json=error_json("nothing_to_backfill"))
    section = gap_section(operator_client.post(START, data).content.decode())
    assert escape(NOTHING_TO_FILL) in section
    assert '<input type="hidden" name="mode" value="start">' in section
    assert '<button type="submit">Preview</button>' in section
    assert "Finish now" not in section
    assert "open conflicts uncertain" not in section
    assert len(writes(engine)) == 2


def test_a_resume_finished_at_once_redirects(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_paused"))
    engine.respond("POST", BACKFILL_PATH, 200, json=resumed(conflicts_made_uncertain=None))
    response = post(operator_client, mode="resume")
    assert response.status_code == 303
    [flash] = flashes(response)
    assert flash.startswith("Backfill finished — idempotency key")


# --- refusals -----------------------------------------------------------------------------------


def test_in_progress_refusal(operator_client: Client, no_backfill: FakeEngine) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 409, json=error_json("backfill_in_progress"))
    response = post(operator_client, mode="start", capabilities=["episodic"])
    section = gap_section(response.content.decode())
    assert response.status_code == 200
    assert IN_PROGRESS_TEXT in section
    assert re.search(r'value="episodic"[^>]*checked', section)  # the bound form keeps the box
    assert len(writes(no_backfill)) == 1
    assert KEY not in section  # an answered refusal mints a fresh key


def test_capability_validation_error_on_checkboxes(
    operator_client: Client, no_backfill: FakeEngine
) -> None:
    no_backfill.respond(
        "POST",
        BACKFILL_PATH,
        422,
        json=error_json(
            "validation_error",
            errors=[{"field": "body.capabilities", "message": "not on: canon_extras"}],
        ),
    )
    response = post(operator_client, mode="start", capabilities=["canon_extras"])
    section = gap_section(response.content.decode())
    fieldset = section.split('<fieldset class="capabilities">', 1)[1].split("</fieldset>", 1)[0]
    assert "not on: canon_extras" in fieldset
    assert 'class="engine-error' not in section
    assert len(writes(no_backfill)) == 1


def test_a_rating_pause_is_c5s_text(operator_client: Client, no_backfill: FakeEngine) -> None:
    no_backfill.respond(
        "POST",
        BACKFILL_PATH,
        409,
        json=error_json("story_paused", cause="rating_check", job_id="01JM7JB0000000000000000002"),
    )
    section = gap_section(post(operator_client, mode="start").content.decode())
    assert "Paused" in section or "paused" in section
    assert len(writes(no_backfill)) == 1


def test_importing_is_e10s_text(operator_client: Client, no_backfill: FakeEngine) -> None:
    no_backfill.respond(
        "POST",
        BACKFILL_PATH,
        409,
        json=error_json("story_importing", job_id="01JM7JB0000000000000000007"),
    )
    response = post(operator_client, mode="start")
    assert "This story is being imported" in gap_section(response.content.decode())


def test_a_confirm_refusal_keeps_the_preview_muted(
    operator_client: Client, no_backfill: FakeEngine
) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 200, json=preview_body())
    data = confirm_data(post(operator_client, mode="start").content.decode())
    no_backfill.respond("POST", BACKFILL_PATH, 409, json=error_json("backfill_in_progress"))
    section = gap_section(operator_client.post(START, data).content.decode())
    assert IN_PROGRESS_TEXT in section
    assert '<div class="muted previewed">' in section
    assert "Turns 1–5" in section  # noqa: RUF001
    assert 'class="two-stage"' not in section
    assert '<button type="submit">Preview</button>' in section


def test_an_unknown_confirm_outcome_keeps_token_and_key(
    operator_client: Client, no_backfill: FakeEngine
) -> None:
    no_backfill.respond("POST", BACKFILL_PATH, 200, json=preview_body())
    data = confirm_data(post(operator_client, mode="start").content.decode())
    no_backfill.fail("POST", BACKFILL_PATH, httpx.ReadTimeout("slow"))
    html = operator_client.post(START, data).content.decode()
    again = confirm_data(html)
    assert again["confirm_token"] == data["confirm_token"]
    assert again["idempotency_key"] == data["idempotency_key"]
    assert 'class="engine-error' in gap_section(html)


def test_fill_it_links_land_on_the_form() -> None:
    """#307's (and #330's, #332's) "fill it from the Backfill page" links: ``#gap-fill``."""
    from console.upgrades import gap_fill_url

    assert gap_fill_url(STORY) == f"{PAGE}#gap-fill"
    assert gap_fill_url("") is None

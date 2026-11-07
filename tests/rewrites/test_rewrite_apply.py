"""#299's apply POST (``stories:rewrite-apply``, ``console/views/rewrite_apply.py``): the Apply
form on the session page, the one apply it sends, the applied flash, the stale plan shown and
never re-applied (E5's sentences, the re-read's preview or the refusal's own), and the other
refusals (E5, C5, D8's key rule)."""

import json
from html import unescape
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.test import Client, override_settings

from console.engine.client import turn_timeout
from console.engine.models import PauseDetails, RewriteApplied
from console.rewrites import (
    APPLY_FAILED_TEXT,
    APPLY_IN_PROGRESS_TEXT,
    BASE_MISMATCH_TEXT,
    BASE_REVISION_REQUIRED_TEXT,
    PIPELINE_CHANGED_TEXT,
    QUESTIONS_PENDING_TEXT,
    RECORD_IN_FLIGHT_TEXT,
    STALE_TEXT,
)
from console.upgrades import backfill_pause_text
from console.views.rewrite_apply import (
    APPLY_LABEL,
    APPLY_LINE,
    FROM_REFUSAL_NOTE,
    applied_flash,
)
from console.wording import ERASED_TEXT
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json
from tests.rewrites.helpers import (
    GET_ONE,
    M7_PAGES,
    ONE_ROUTE,
    SESSION,
    SESSION_URL,
    STORY,
    engine,
    key_in,
    m7,
)

pytestmark = pytest.mark.django_db
__all__ = ["engine"]  # the fixture, re-exported for pytest

KEY = "01JM7KY0000000000000000009"
APPLY_ROUTE = "/stories/{story_id}/rewrites/{session_id}/apply"
APPLY = f"{GET_ONE}/apply"
APPLY_URL = f"{SESSION_URL}apply/"
AMENDMENT = "01JM7AM0000000000000000003"
WRITTEN_ONLY = ("timeline_revision", "apply", "idempotency_key")
REWRITE_ROUTES = (GET_ONE, APPLY)
RECORD_REFS = [{"table": "events", "id": "01JM7EV0000000000000000003"}]
STALE_COUNT_ONCE = "Recomputed 1 time because the story changed"


def session_body(name: str, **changes: Any) -> dict[str, Any]:
    """A session fixture as ``GET …/rewrites/{sid}`` answers it."""
    body: dict[str, Any] = m7(name)
    for key in WRITTEN_ONLY:
        body.pop(key, None)
    body.update(changes)
    return body


def recomputed(base: int = 21, **changes: Any) -> dict[str, Any]:
    """``rewrite_session`` recomputed at ``base`` once (the stale re-read)."""
    body = session_body("rewrite_session", base_revision=base, stale_count=1, **changes)
    body["preview"]["base_revision"] = base
    body["preview"]["plan_hash"] = "sha256:" + "8" * 64
    return body


def served(engine: FakeEngine, *bodies: dict[str, Any]) -> None:
    engine.respond_sequence("GET", ONE_ROUTE, [{"status": 200, "json": body} for body in bodies])


def applied_with(engine: FakeEngine) -> None:
    body = m7("rewrite_applied")
    body["session"]["id"] = SESSION
    engine.respond("POST", APPLY_ROUTE, 200, json=body)


def refused_with(engine: FakeEngine, status: int, code: str, **details: Any) -> None:
    engine.respond("POST", APPLY_ROUTE, status, json=error_json(code, "refused", **details))


def stale_details(**changes: Any) -> dict[str, Any]:
    """An inline ``rewrite_stale`` (E16): the Engine's ``{kind, refs}`` reasons, one carried
    answer, the recomputed preview at 21 and ``stale_count: 1``."""
    preview = recomputed()["preview"]
    details: dict[str, Any] = {
        "base_revision": 21,
        "preview": preview,
        "new_questions": [],
        "carried_answers": [{"question_id": "01JM7QN0000000000000000001", "option_id": "o1"}],
        "reasons": [{"kind": "record_changed", "refs": RECORD_REFS}],
        "stale_count": 1,
    }
    details.update(changes)
    return details


def post(client: Client, data: dict[str, Any] | None = None) -> Any:
    return client.post(APPLY_URL, {"idempotency_key": KEY, "base_revision": "20", **(data or {})})


def page(response: Any, status: int = 200) -> str:
    assert response.status_code == status
    return unescape(response.content.decode())


def rewrite_calls(engine: FakeEngine) -> list[tuple[str, str]]:
    """The call log filtered to the rewrite routes (E24: the frame's reads are allowed)."""
    return [(r.method, r.url.path) for r in engine.requests if r.url.path in REWRITE_ROUTES]


def applies(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST" and r.url.path == APPLY]


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def session_part(html: str) -> str:
    return html.split('<section class="rewrite-session"', 1)[1]


def apply_form(html: str) -> str | None:
    start = html.find(f'<form method="post" action="{APPLY_URL}"')
    if start < 0:
        return None
    return html[start : html.index("</form>", start)]


# --- the form on the session page ---------------------------------------------------------------


def test_apply_form_carries_the_preview_base(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_session"))
    html = page(operator_client.get(SESSION_URL))
    form = apply_form(html)
    assert form is not None
    assert '<input type="hidden" name="base_revision" value="20">' in form
    assert 'name="idempotency_key" value="' in form
    assert f'<p class="apply-line">{APPLY_LINE}</p>' in form
    assert f'<button type="submit">{APPLY_LABEL}</button>' in form
    assert f'Plan <code class="hash" title="sha256:{"7" * 64}">{"7" * 12}</code>' in form
    assert 'name="plan_hash"' not in form  # display only, never posted
    # **Complement:** no preview, or a terminal session, renders no Apply form.
    for name in ("rewrite_open", "rewrite_session_applied"):
        served(engine, session_body(name))
        html = page(operator_client.get(SESSION_URL))
        assert apply_form(html) is None
        assert APPLY_LABEL not in html


def test_apply_form_follows_the_preview_base_not_the_session(
    operator_client: Client, engine: FakeEngine
) -> None:
    """``apply_hidden`` carries the *preview's* ``base_revision``."""
    body = session_body("rewrite_session", base_revision=19)
    served(engine, body)
    form = apply_form(page(operator_client.get(SESSION_URL)))
    assert form is not None
    assert 'name="base_revision" value="20"' in form


# --- the apply ----------------------------------------------------------------------------------


def test_apply_sends_base_revision_once(operator_client: Client, engine: FakeEngine) -> None:
    applied_with(engine)
    post(operator_client)
    (request,) = applies(engine)
    assert list(request.url.params.multi_items()) == [("viewpoint", "omniscient")]
    assert json.loads(request.content) == {"base_revision": 20}
    assert request.headers["Idempotency-Key"] == KEY
    assert request.extensions["timeout"]["read"] == turn_timeout(None)
    # **Complement:** no expected revision, and exactly one POST (no read before the write).
    assert "X-Epistrel-Expected-Revision" not in request.headers
    assert rewrite_calls(engine) == [("POST", APPLY)]


def test_applied_redirects_with_amendment_and_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    applied_with(engine)
    response = post(operator_client)
    assert response.status_code == 303
    assert response["Location"] == SESSION_URL
    (flash,) = flashes(response)
    assert flash == (
        f'Applied: amendment <a href="/stories/{STORY}/amendments/{AMENDMENT}/">'
        f"<code>{AMENDMENT}</code></a> · story revision now 21"  # #302 registered the page
        " — idempotency key <code>01JM7KY0000000000000000001</code>"
    )
    # Under the stub urlconf the amendment is linked (E6). (The stub urlconf shadows the apply
    # route itself, so the flash is built directly.)
    applied = RewriteApplied.from_json(m7("rewrite_applied"))
    assert applied is not None
    with override_settings(ROOT_URLCONF=M7_PAGES):
        linked = applied_flash(applied, STORY, KEY)
    link = f'<a href="/stories/{STORY}/amendments/{AMENDMENT}/"><code>{AMENDMENT}</code></a>'
    assert link in linked


def test_the_applied_session_page(operator_client: Client, engine: FakeEngine) -> None:
    """After the redirect the page shows "Applied", the amendment and the report (E3)."""
    served(engine, session_body("rewrite_session_applied"))
    html = page(operator_client.get(SESSION_URL))
    assert ">Applied</span>" in html
    assert f'amendments/{AMENDMENT}/"><code title="{AMENDMENT}">…00000003</code></a></dd>' in html
    assert "<h3>Report</h3>" in html
    assert "<form" not in session_part(html)


@pytest.mark.parametrize("value", ["", "x", "-1", "1" * 19, "2 "])
def test_missing_base_revision_sends_nothing(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    served(engine, session_body("rewrite_session"))
    html = page(post(operator_client, {"base_revision": value}))
    assert f'<p class="warning">{BASE_REVISION_REQUIRED_TEXT}</p>' in html
    # **Complement:** one session read and no apply.
    assert rewrite_calls(engine) == [("GET", GET_ONE)]
    assert key_in(html) != KEY  # a local-validation failure mints a fresh key (E21)


def test_missing_base_revision_field(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_session"))
    response = operator_client.post(APPLY_URL, {"idempotency_key": KEY})
    assert BASE_REVISION_REQUIRED_TEXT in page(response)
    assert applies(engine) == []


# --- a stale plan -------------------------------------------------------------------------------


def test_stale_plan_rerenders_the_new_preview(operator_client: Client, engine: FakeEngine) -> None:
    refused_with(engine, 409, "rewrite_stale", **stale_details())
    served(engine, recomputed())
    response = post(operator_client)
    html = page(response)
    assert f'<p class="warning stale">{STALE_TEXT} (Record changed)</p>' in html
    assert '<p class="carried">Answers carried over: 1</p>' in html
    assert html.count(STALE_COUNT_ONCE) == 1
    assert "Planned at story revision 21</p>" in html
    form = apply_form(html)
    assert form is not None
    assert '<input type="hidden" name="base_revision" value="21">' in form
    assert key_in(html) != KEY
    # The refs under More: one row per kind, the ref's table humanised and its id.
    assert "<li>Record changed: Events <code>01JM7EV0000000000000000003</code></li>" in html
    # The warning sits above the partial.
    assert html.index("warning stale") < html.index('<section class="rewrite-session"')
    # **Complement:** the apply, then the re-read; no second apply and no success flash.
    assert rewrite_calls(engine) == [("POST", APPLY), ("GET", GET_ONE)]
    assert len(applies(engine)) == 1
    assert flashes(response) == []


def test_stale_with_new_questions_shows_them(operator_client: Client, engine: FakeEngine) -> None:
    question = session_body("rewrite_open")["questions"][0]
    refused_with(engine, 409, "rewrite_stale", **stale_details(new_questions=[question]))
    served(engine, session_body("rewrite_open", stale_count=1))
    html = page(post(operator_client))
    assert STALE_TEXT in html
    assert apply_form(html) is None
    assert html.count(STALE_COUNT_ONCE) == 1
    assert f'<form method="post" action="{SESSION_URL}answers/" class="answers">' in html
    assert "Submit answers" in html
    assert len(applies(engine)) == 1


def test_base_mismatch_wording(operator_client: Client, engine: FakeEngine) -> None:
    details = {
        "reason": "base_mismatch",
        "base_revision": 21,
        "requested_base_revision": 20,
        "current_revision": 22,
        "preview": recomputed()["preview"],
        "stale_count": 1,
    }
    refused_with(engine, 409, "rewrite_stale", **details)
    served(engine, recomputed())
    html = page(post(operator_client))
    assert f'<p class="warning stale">{BASE_MISMATCH_TEXT}</p>' in html
    assert "Answers carried over" not in html
    assert STALE_TEXT not in html
    assert "Requested" not in html
    assert ">22<" not in html
    assert len(applies(engine)) == 1


def test_stale_reread_failure_uses_the_refusal_preview(
    operator_client: Client, engine: FakeEngine
) -> None:
    refused_with(engine, 409, "rewrite_stale", **stale_details())
    engine.respond("GET", ONE_ROUTE, 503, json=error_json("unavailable", "down"))
    html = page(post(operator_client))
    assert f"{STALE_TEXT} (Record changed)" in html
    assert f"Planned at story revision 21 {FROM_REFUSAL_NOTE}</p>" in html
    assert html.count(STALE_COUNT_ONCE) == 1
    warning = html[html.index("warning stale") : html.index('<section class="rewrite-session"')]
    assert STALE_COUNT_ONCE in warning
    # **Complement:** still one apply, and no Apply form (its base is unverified).
    assert rewrite_calls(engine) == [("POST", APPLY), ("GET", GET_ONE)]
    assert apply_form(html) is None
    assert APPLY_LABEL not in html


def test_stale_refusal_preview_with_a_required_question_has_no_answers_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    question = session_body("rewrite_open")["questions"][0]
    refused_with(engine, 409, "rewrite_stale", **stale_details(new_questions=[question]))
    engine.respond("GET", ONE_ROUTE, 503, json=error_json("unavailable", "down"))
    html = page(post(operator_client))
    assert ">Waiting for your answers</span>" in html
    assert 'class="answers"' not in html
    assert apply_form(html) is None


def test_stale_with_neither_preview_shows_the_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    refused_with(engine, 409, "rewrite_stale", **stale_details(preview={"bad": True}))
    engine.respond("GET", ONE_ROUTE, 503, json=error_json("unavailable", "down"))
    html = page(post(operator_client))
    assert STALE_TEXT in html
    assert 'class="engine-error' in html
    assert '<section class="rewrite-session"' not in html
    assert len(applies(engine)) == 1


def test_pipeline_changed_is_abandoned(operator_client: Client, engine: FakeEngine) -> None:
    details = {"reason": "pipeline_changed", "base_revision": 20, "current_revision": 21}
    refused_with(engine, 409, "rewrite_stale", **details)
    served(engine, session_body("rewrite_session", state="abandoned"))
    html = page(post(operator_client))
    assert f'<p class="warning stale">{PIPELINE_CHANGED_TEXT}</p>' in html
    assert ">Abandoned</span>" in html
    assert "<form" not in session_part(html)
    # **Complement:** not the recomputed-plan sentence.
    assert STALE_TEXT not in html
    assert len(applies(engine)) == 1


# --- other refusals -----------------------------------------------------------------------------

PAUSED = {"cause": "backfill", "story_id": STORY, "job_id": "01JM7JB0000000000000000001"}
REFUSALS = [
    pytest.param(
        409,
        "rewrite_session_closed",
        {"state": "applied"},
        "This session has already ended (Applied)",
        id="closed",
    ),
    pytest.param(409, "rewrite_apply_in_progress", {}, APPLY_IN_PROGRESS_TEXT, id="in-progress"),
    pytest.param(
        409,
        "rewrite_questions_pending",
        {"question_ids": ["01JM7QN0000000000000000001"]},
        QUESTIONS_PENDING_TEXT,
        id="questions-pending",
    ),
    pytest.param(
        409,
        "record_in_flight",
        {"record_table": "events", "record_id": "01JM7EV0000000000000000003"},
        RECORD_IN_FLIGHT_TEXT,
        id="in-flight",
    ),
    pytest.param(409, "erased_subject_referenced", {}, ERASED_TEXT, id="erased"),
    pytest.param(
        409,
        "story_paused",
        PAUSED,
        backfill_pause_text("backfill", PauseDetails.from_details(PAUSED)),
        id="paused",
    ),
    pytest.param(503, "rewrite_apply_failed", {}, APPLY_FAILED_TEXT, id="apply-failed"),
]


@pytest.mark.parametrize(("status", "code", "details", "text"), REFUSALS)
def test_other_refusals(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
    text: str,
) -> None:
    refused_with(engine, status, code, **details)
    reread = session_body("rewrite_session")
    if code == "rewrite_apply_failed":
        reread["last_error"] = {"code": "provider_timeout", "message": "the model timed out"}
    served(engine, reread)
    response = post(operator_client)
    html = page(response)
    assert f'<p class="warning">{text}</p>' in html
    assert html.index(text) < html.index('<section class="rewrite-session"')
    assert key_in(html) != KEY
    assert len(applies(engine)) == 1
    assert rewrite_calls(engine) == [("POST", APPLY), ("GET", GET_ONE)]
    if code == "rewrite_apply_failed":
        assert '<p class="warning">Provider timeout: the model timed out</p>' in html
    # **Complement:** no redirect, and no "Applied" text.
    assert flashes(response) == []
    assert "Applied:" not in html


def test_unknown_error_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    refused_with(engine, 422, "later_code")
    served(engine, session_body("rewrite_session"))
    html = page(post(operator_client))
    assert 'class="engine-error' in html
    assert "later_code" in html
    assert '<section class="rewrite-session"' in html
    assert key_in(html) != KEY  # an answered refusal: a fresh key
    assert len(applies(engine)) == 1


def test_transport_failure_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", APPLY_ROUTE, httpx.ReadTimeout("slow"))
    served(engine, session_body("rewrite_session"))
    html = page(post(operator_client))
    assert 'class="engine-error' in html
    assert html.index('class="engine-error') < html.index('<section class="rewrite-session"')
    # The outcome is unknown: the posted key is kept, so a re-click replays (D8); no retry.
    assert key_in(html) == KEY
    assert len(applies(engine)) == 1
    assert rewrite_calls(engine) == [("POST", APPLY), ("GET", GET_ONE)]


def test_session_not_found_makes_no_reread(operator_client: Client, engine: FakeEngine) -> None:
    refused_with(engine, 404, "rewrite_session_not_found")
    html = page(post(operator_client))
    assert "No such rewrite session for this story" in html
    assert 'class="engine-error' in html
    assert rewrite_calls(engine) == [("POST", APPLY)]


def test_a_failed_reread_after_a_refusal(operator_client: Client, engine: FakeEngine) -> None:
    refused_with(engine, 409, "record_in_flight")
    engine.respond("GET", ONE_ROUTE, 503, json=error_json("unavailable", "down"))
    html = page(post(operator_client))
    assert f'<p class="warning">{RECORD_IN_FLIGHT_TEXT}</p>' in html
    assert 'class="engine-error' in html
    assert '<section class="rewrite-session"' not in html
    assert apply_form(html) is None


def test_an_applying_reread_has_no_actions(operator_client: Client, engine: FakeEngine) -> None:
    refused_with(engine, 409, "rewrite_apply_in_progress")
    served(engine, session_body("rewrite_session", state="applying"))
    html = page(post(operator_client))
    assert ">Applying…</span>" in html
    assert "session-actions" not in html
    assert f'<a href="{SESSION_URL}">Reload</a>' in html


def test_character_viewpoint_never_sent(operator_client: Client, engine: FakeEngine) -> None:
    refused_with(engine, 409, "rewrite_stale", **stale_details())
    served(engine, recomputed())
    operator_client.post(
        f"{APPLY_URL}?viewpoint=character&actor_character_id=01JM7CH0000000000000000001",
        {"idempotency_key": KEY, "base_revision": "20"},
    )
    for request in engine.requests:
        if request.url.path in REWRITE_ROUTES:
            assert list(request.url.params.multi_items()) == [("viewpoint", "omniscient")]
            assert "actor_character_id" not in str(request.url)

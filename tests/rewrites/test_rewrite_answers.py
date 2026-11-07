"""#298's answers POST (``stories:rewrite-answers``, ``console/views/rewrite_answers.py``): the
questions form on the session page, the bodies E4 sends, the pre-write re-read and stale-page
guards, the redirect's flashes and the refusals (E5, D8's key rule)."""

import json
import re
from html import unescape
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.test import Client

from console.engine.client import turn_timeout
from console.rewrites import SUPERSEDED_TEXT
from console.views.rewrite_answers import (
    NEW_QUESTION_TEXT,
    NOT_AWAITING_TEXT,
    NOTHING_ANSWERED,
    REJECTED_QUESTION,
    TOO_MANY_ANSWERS_TEXT,
)
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json
from tests.rewrites.helpers import (
    CONFIG_READ,
    GET_ONE,
    ONE_ROUTE,
    SESSION,
    SESSION_URL,
    STORY_READ,
    answer_raw,
    calls,
    engine,
    key_in,
    m7,
    written,
)

pytestmark = pytest.mark.django_db
__all__ = ["engine"]  # the fixture, re-exported for pytest

KEY = "01JM7KY0000000000000000009"
Q1 = "01JM7QN0000000000000000001"
Q2 = "01JM7QN0000000000000000002"
Q3 = "01JM7QN0000000000000000003"
ANSWERS_ROUTE = "/stories/{story_id}/rewrites/{session_id}/answers"
ANSWERS = f"{GET_ONE}/answers"
ANSWER_URL = f"{SESSION_URL}answers/"
WRITTEN_ONLY = ("timeline_revision", "apply", "idempotency_key")
GUARDRAIL = "That text breaks the story's input guardrails (No slurs) — nothing was written"


def session_body(name: str, **changes: Any) -> dict[str, Any]:
    """A session fixture as ``GET …/rewrites/{sid}`` answers it."""
    body: dict[str, Any] = m7(name)
    for key in WRITTEN_ONLY:
        body.pop(key, None)
    body.update(changes)
    return body


def optional_only() -> dict[str, Any]:
    """``rewrite_two_questions`` with the required question already answered."""
    body = session_body("rewrite_two_questions")
    body["questions"][0].update(
        answer_option_id="o1",
        accepted_recommendation=True,
        answered_by="console:nate",
        answered_at="2026-11-05T10:01:00Z",
    )
    return body


def served(engine: FakeEngine, *bodies: dict[str, Any]) -> None:
    """The session read answers ``bodies`` in order (the last repeats)."""
    engine.respond_sequence("GET", ONE_ROUTE, [{"status": 200, "json": body} for body in bodies])


def answered_with(engine: FakeEngine, body: dict[str, Any] | None = None) -> None:
    engine.respond("POST", ANSWERS_ROUTE, 200, json=body or written("rewrite_session"))


def refused_with(engine: FakeEngine, status: int, code: str, **details: Any) -> None:
    engine.respond("POST", ANSWERS_ROUTE, status, json=error_json(code, "refused", **details))


def post(client: Client, data: dict[str, Any] | None = None, url: str = ANSWER_URL) -> Any:
    return client.post(url, {"idempotency_key": KEY, **(data or {})})


def posts(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST"]


def sent_body(engine: FakeEngine) -> Any:
    (request,) = posts(engine)
    return json.loads(request.content)


def flashes(response: Any) -> list[str]:
    """This response's flashes (earlier POSTs' stay queued: no page consumed them)."""
    return [str(m) for m in get_messages(response.wsgi_request)]


def last_flash(response: Any) -> str:
    return flashes(response)[-1]


def page(response: Any, status: int = 200) -> str:
    assert response.status_code == status
    return unescape(response.content.decode())


def fieldset(html: str, question_id: str) -> str:
    start = html.index(f'<fieldset class="question" id="question-{question_id}">')
    return html[start : html.index("</fieldset>", start)]


def above_form(html: str) -> str:
    form = html.split('class="answers">', 1)[1]
    return form.split('<fieldset class="question"', 1)[0]


# --- the form on the session page ---------------------------------------------------------------


def test_form_renders_with_the_recommendation_checked(
    operator_client: Client, engine: FakeEngine
) -> None:
    served(engine, session_body("rewrite_open"))
    html = page(operator_client.get(SESSION_URL))
    assert f'<form method="post" action="{ANSWER_URL}" class="answers">' in html
    question = fieldset(html, Q1)
    assert f'<input type="radio" name="choice_{Q1}" value="o1" checked>' in question
    assert f'<input type="radio" name="choice_{Q1}" value="o2">' in question
    recommended = question.index("Rewrite Bob's line: he only copied the ledger (recommended)")
    why = question.index("Why: Bob's line is his own sincere claim")
    assert recommended < why
    assert f'name="text_{Q1}"' in question
    assert "Text you write here is sent instead of the option picked above" in question
    assert '<button type="submit">Submit answers</button>' in html
    assert '<button type="submit" name="accept_all" value="1">Accept every recommendation' in html
    first = key_in(html)
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", first)
    assert key_in(page(operator_client.get(SESSION_URL))) != first  # a fresh key per render
    # **Complement:** an awaiting-approval session has no answers form, and its answered
    # question no inputs.
    served(engine, session_body("rewrite_session"))
    html = page(operator_client.get(SESSION_URL))
    assert 'class="answers"' not in html
    assert "Submit answers" not in html
    question = fieldset(html, Q1)
    assert 'name="text_' not in question
    assert "Answer: Rewrite Bob's line: he only copied the ledger (recommended)" in question


# --- the bodies (E4) ----------------------------------------------------------------------------


def test_untouched_recommendation_sends_option_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    served(engine, session_body("rewrite_open"))
    answered_with(engine)
    post(operator_client, {f"choice_{Q1}": "o1", f"text_{Q1}": ""})
    assert sent_body(engine) == {"answers": [{"question_id": Q1, "option_id": "o1"}]}
    (request,) = posts(engine)
    assert list(request.url.params.multi_items()) == [("viewpoint", "omniscient")]
    assert request.headers["Idempotency-Key"] == KEY
    assert request.extensions["timeout"]["read"] == turn_timeout(None)
    # **Complement:** no ``accept_recommended`` and no expected revision.
    assert "accept_recommended" not in request.content.decode()
    assert "X-Epistrel-Expected-Revision" not in request.headers


def test_text_wins_over_the_radio(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_open"))
    answered_with(engine)
    post(operator_client, {f"choice_{Q1}": "o2", f"text_{Q1}": "He only copied it"})
    assert sent_body(engine) == {"answers": [{"question_id": Q1, "text": "He only copied it"}]}


def test_whitespace_text_counts_as_no_text(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_open"))
    answered_with(engine)
    post(operator_client, {f"choice_{Q1}": "o2", f"text_{Q1}": "   "})
    assert sent_body(engine) == {"answers": [{"question_id": Q1, "option_id": "o2"}]}


def test_accept_every_recommendation(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_two_questions"))
    answered_with(engine)
    post(
        operator_client,
        {
            f"choice_{Q1}": "o2",
            f"text_{Q1}": "Typed anyway",
            f"choice_{Q2}": "o1",
            "accept_all": "1",
        },
    )
    assert sent_body(engine) == {
        "answers": [
            {"question_id": Q1, "accept_recommended": True},
            {"question_id": Q2, "accept_recommended": True},
        ]
    }


def test_untouched_optional_question_is_left_out(
    operator_client: Client, engine: FakeEngine
) -> None:
    served(engine, session_body("rewrite_two_questions"))
    answered_with(engine)
    post(operator_client, {f"choice_{Q1}": "o2", f"choice_{Q2}": "o1", f"text_{Q2}": ""})
    assert sent_body(engine) == {"answers": [{"question_id": Q1, "option_id": "o2"}]}


def test_nothing_answered_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, optional_only())
    answered_with(engine)
    html = page(post(operator_client, {f"choice_{Q2}": "o1", f"text_{Q2}": ""}))
    assert NOTHING_ANSWERED in above_form(html)
    assert key_in(html) == KEY  # nothing was sent: the key is still unused
    # **Complement:** no ``POST …/answers``.
    assert posts(engine) == []


def test_invalid_form_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_open"))
    answered_with(engine)
    html = page(post(operator_client, {f"choice_{Q1}": "o1", f"text_{Q1}": "x" * 2001}))
    assert "Ensure this value has at most 2000 characters (it has 2001)." in fieldset(html, Q1)
    assert key_in(html) == KEY
    # **Complement:** the one re-read and no POST.
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), ("GET", GET_ONE)]


def test_an_option_the_question_lacks_is_a_field_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    served(engine, session_body("rewrite_open"))
    html = page(post(operator_client, {f"choice_{Q1}": "o9"}))
    assert "Select a valid choice. o9 is not one of the available choices." in html
    assert posts(engine) == []


def test_more_than_fifty_answers_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    body = session_body("rewrite_open")
    first = body["questions"][0]
    body["questions"] = [
        {**first, "id": f"01JM7QN{n:019d}"} for n in range(1, 52)
    ]  # 51 required questions
    served(engine, body)
    answered_with(engine)
    data = {f"choice_{q['id']}": "o1" for q in body["questions"]}
    for button in ({}, {"accept_all": "1"}):
        html = page(post(operator_client, {**data, **button}))
        assert TOO_MANY_ANSWERS_TEXT in above_form(html)
        assert key_in(html) == KEY
    assert posts(engine) == []


# --- the re-read and stale-page guards ------------------------------------------------------------


def test_reread_guard(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_session"))
    answered_with(engine)
    html = page(post(operator_client, {f"choice_{Q1}": "o1"}))
    notice = NOT_AWAITING_TEXT.format(state="Ready to apply")
    assert f'<p class="warning">{notice}</p>' in html
    assert "Planned at story revision 20" in html  # the session as it stands
    assert f'<a href="{SESSION_URL}">Reload</a>' in html  # the session page, not the POST URL
    assert 'class="answers"' not in html
    # **Complement:** no ``POST …/answers``.
    assert posts(engine) == []


def test_a_replaced_question_is_superseded_without_a_post(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = session_body("rewrite_open")
    body["questions"][0]["id"] = Q3
    served(engine, body)
    answered_with(engine)
    html = page(post(operator_client, {f"choice_{Q1}": "o2"}))
    assert SUPERSEDED_TEXT in above_form(html)
    assert f'<input type="radio" name="choice_{Q3}" value="o1" checked>' in html
    assert key_in(html) == KEY
    assert posts(engine) == []


def test_a_new_required_question_stops_the_post(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = session_body("rewrite_two_questions")
    body["questions"][1]["required"] = True
    served(engine, body)
    answered_with(engine)
    html = page(post(operator_client, {f"choice_{Q1}": "o2", f"text_{Q1}": "kept"}))
    assert NEW_QUESTION_TEXT in above_form(html)
    first = fieldset(html, Q1)
    assert f'<input type="radio" name="choice_{Q1}" value="o2" checked>' in first  # kept
    assert "kept</textarea>" in first
    assert f'<input type="radio" name="choice_{Q2}" value="o1" checked>' in fieldset(html, Q2)
    assert key_in(html) == KEY
    assert posts(engine) == []


def test_accept_all_over_other_questions_stops_the_post(
    operator_client: Client, engine: FakeEngine
) -> None:
    served(engine, session_body("rewrite_two_questions"))  # Q2 (optional) is new to the page
    answered_with(engine)
    html = page(post(operator_client, {f"choice_{Q1}": "o1", "accept_all": "1"}))
    assert NEW_QUESTION_TEXT in above_form(html)
    assert posts(engine) == []
    # **Complement:** "Submit answers" over the same page goes through (Q2 is optional).
    post(operator_client, {f"choice_{Q1}": "o1"})
    assert sent_body(engine) == {"answers": [{"question_id": Q1, "option_id": "o1"}]}


def test_a_failed_reread_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_ROUTE, 503, json=error_json("database_unavailable", "down"))
    answered_with(engine)
    html = page(post(operator_client, {f"choice_{Q1}": "o1"}))
    assert 'class="engine-error' in html
    assert posts(engine) == []
    engine.respond(
        "GET", ONE_ROUTE, 404, json=error_json("rewrite_session_not_found", "no such session")
    )
    html = page(post(operator_client, {f"choice_{Q1}": "o1"}), status=404)
    assert '<p class="not-found">No such rewrite session for this story</p>' in html
    assert posts(engine) == []


def test_get_is_not_allowed(operator_client: Client, engine: FakeEngine) -> None:
    assert operator_client.get(ANSWER_URL).status_code == 405
    assert calls(engine) == []


# --- after the answer ----------------------------------------------------------------------------


def test_answered_redirects_with_the_flash(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_open"))
    answered_with(engine, written("rewrite_session", idempotency_key=KEY))
    response = post(operator_client, {f"choice_{Q1}": "o1"})
    assert response.status_code == 303
    assert response["Location"] == SESSION_URL
    tail = f" — idempotency key <code>{KEY}</code>"
    assert flashes(response) == [f"Answers recorded — the plan is ready to review{tail}"]
    # A still-asking answer: the unanswered count.
    answered_with(engine, written("rewrite_two_questions", idempotency_key=KEY))
    response = post(operator_client, {f"choice_{Q1}": "o1"})
    assert last_flash(response) == f"Answers recorded — the Engine has 2 more questions{tail}"
    answered_with(engine, written("rewrite_open", idempotency_key=KEY))
    response = post(operator_client, {f"choice_{Q1}": "o1"})
    assert last_flash(response) == f"Answers recorded — the Engine has 1 more question{tail}"
    # An ``apply``-carrying answer (an ``apply_directly`` session).
    amendment = "01JM7AM0000000000000000003"
    applied = written(
        "rewrite_session_applied",
        idempotency_key=KEY,
        apply={"amendment_id": amendment, "report": {}, "timeline_revision": 21},
    )
    answered_with(engine, applied)
    response = post(operator_client, {f"choice_{Q1}": "o1"})
    assert response.status_code == 303
    assert last_flash(response) == (
        f"Answers recorded and applied: amendment <code>{amendment}</code> · story revision "
        f"now 21{tail}"
    )


def test_zero_more_questions_is_counted(operator_client: Client, engine: FakeEngine) -> None:
    """E21's sim2 refinement: "0 more questions" is said, not dropped."""
    served(engine, session_body("rewrite_open"))
    still = written("rewrite_session", idempotency_key=KEY, state="awaiting_answers")
    answered_with(engine, still)
    response = post(operator_client, {f"choice_{Q1}": "o1"})
    assert last_flash(response).startswith("Answers recorded — the Engine has 0 more questions")


def test_another_written_state_says_it(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_open"))
    answered_with(engine, written("rewrite_cancelled", idempotency_key=KEY))
    response = post(operator_client, {f"choice_{Q1}": "o2"})
    assert response.status_code == 303
    assert flashes(response) == [
        f"Answers recorded — the session is now Cancelled — idempotency key <code>{KEY}</code>"
    ]


# --- refusals ------------------------------------------------------------------------------------


def _two_question_post(operator_client: Client) -> str:
    """An option answer for Q1 then a text answer for Q2 (the text is the first text sent)."""
    return page(
        post(operator_client, {f"choice_{Q1}": "o2", f"choice_{Q2}": "o1", f"text_{Q2}": "x!"})
    )


REFUSALS = [
    pytest.param(
        "rewrite_open",
        (422, "invalid_answer", {"question_id": Q1}),
        lambda html: "refused" in fieldset(html, Q1) and "refused" not in above_form(html),
        id="invalid_answer-on-its-group",
    ),
    pytest.param(
        "rewrite_open",
        (422, "invalid_answer", {}),
        lambda html: "refused" in above_form(html) and "refused" not in fieldset(html, Q1),
        id="invalid_answer-above",
    ),
    pytest.param(
        "rewrite_two_questions",
        (
            422,
            "guardrail_violation",
            {
                "violations": [
                    {"name": "No slurs", "policy_id": "p1", "locator": "body.answers[0].text"}
                ]
            },
        ),
        lambda html: (
            GUARDRAIL in fieldset(html, Q2).split(f'name="text_{Q2}"', 1)[1]
            and GUARDRAIL not in fieldset(html, Q1)
            and GUARDRAIL not in above_form(html)
        ),
        id="guardrail-on-the-second-questions-text",
    ),
    pytest.param(
        "rewrite_two_questions",
        (
            422,
            "guardrail_violation",
            {
                "violations": [
                    {"name": "No slurs", "policy_id": "p1", "locator": "body.answers[3].text"}
                ]
            },
        ),
        lambda html: GUARDRAIL in above_form(html) and GUARDRAIL not in fieldset(html, Q2),
        id="guardrail-past-the-texts-above",
    ),
    pytest.param(
        "rewrite_open",
        (409, "rewrite_question_superseded", {"question_id": Q1, "new_question_id": Q3}),
        lambda html: SUPERSEDED_TEXT in above_form(html) and f"question-{Q3}" in html,
        id="superseded",
    ),
    pytest.param(
        "rewrite_open",
        (409, "rewrite_session_closed", {"state": "cancelled"}),
        lambda html: '<p class="warning">This session has already ended (Cancelled)</p>' in html,
        id="closed",
    ),
    pytest.param(
        "rewrite_open",
        (409, "rewrite_apply_in_progress", {}),
        lambda html: (
            '<p class="warning">This session is already being applied — reload in a moment</p>'
            in html
        ),
        id="apply-in-progress",
    ),
    pytest.param(
        "rewrite_open",
        (422, "validation_error", {"errors": [{"field": "body.answers.0.text", "message": "Bad"}]}),
        lambda html: "Bad" in fieldset(html, Q1) and 'class="engine-error' not in html,
        id="validation_error-on-its-group",
    ),
    pytest.param(
        "rewrite_open",
        (422, "validation_error", {"errors": [{"field": "body.answers", "message": "Bad"}]}),
        lambda html: 'class="engine-error' in html,
        id="validation_error-unmapped-panel",
    ),
]


@pytest.mark.parametrize(("name", "refusal", "shown"), REFUSALS)
def test_refusals(
    operator_client: Client,
    engine: FakeEngine,
    name: str,
    refusal: tuple[int, str, dict[str, Any]],
    shown: Any,
) -> None:
    status, code, details = refusal
    first = session_body(name)
    fresh = session_body(name)
    if code == "rewrite_question_superseded":
        fresh["questions"][0]["id"] = Q3
    if code == "rewrite_session_closed":
        fresh = session_body("rewrite_cancelled")
    served(engine, first, fresh)
    refused_with(engine, status, code, **details)
    if name == "rewrite_two_questions":
        html = _two_question_post(operator_client)
    else:
        html = page(post(operator_client, {f"choice_{Q1}": "o2"}))
    assert shown(html), html
    # **Complement:** 200 (no redirect), one POST (no retry), a fresh key, and the fresh read.
    assert len(posts(engine)) == 1
    if 'name="idempotency_key"' in html:  # a closed session renders no form at all
        assert key_in(html) != KEY
    assert [c for c in calls(engine) if c == ("GET", GET_ONE)] == [("GET", GET_ONE)] * 2


def test_an_erased_subject_goes_on_the_one_text(
    operator_client: Client, engine: FakeEngine
) -> None:
    served(engine, session_body("rewrite_two_questions"))
    refused_with(engine, 409, "erased_subject_referenced")
    html = _two_question_post(operator_client)
    question = fieldset(html, Q2)
    (message,) = re.findall(
        r'<ul class="errorlist"><li>([^<]+)</li></ul>', question.split(f'name="text_{Q2}"')[1]
    )
    assert message not in above_form(html)
    assert len(posts(engine)) == 1


def test_a_refusal_keeps_the_posted_answers(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_open"))
    refused_with(engine, 422, "invalid_answer", question_id=Q1)
    html = page(post(operator_client, {f"choice_{Q1}": "o2", f"text_{Q1}": "my words"}))
    question = fieldset(html, Q1)
    assert f'<input type="radio" name="choice_{Q1}" value="o2" checked>' in question
    assert "my words</textarea>" in question


def test_an_apply_refusal_after_the_answers(operator_client: Client, engine: FakeEngine) -> None:
    """An ``apply_directly`` session: the answers were recorded, the apply refused."""
    asking = session_body("rewrite_open", preview_mode="apply_directly")
    served(engine, asking, session_body("rewrite_session", preview_mode="apply_directly"))
    refused_with(engine, 409, "record_in_flight")
    html = page(post(operator_client, {f"choice_{Q1}": "o1"}))
    assert (
        '<p class="warning">Answers recorded, but applying failed: A turn is writing to what '
        "this plan touches — try again once it ends</p>"
    ) in html
    assert "Planned at story revision 20" in html
    assert len(posts(engine)) == 1


def test_an_unknown_outcome_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_open"))
    engine.fail("POST", ANSWERS_ROUTE, httpx.ConnectError("refused"))
    html = page(post(operator_client, {f"choice_{Q1}": "o2"}))
    assert 'class="engine-error' in html
    assert key_in(html) == KEY  # the answer may have happened: a resubmit replays it
    assert f'<input type="radio" name="choice_{Q1}" value="o2" checked>' in html
    assert [c for c in calls(engine) if c == ("GET", GET_ONE)] == [("GET", GET_ONE)]


def test_a_not_found_answer_is_the_404(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_open"))
    refused_with(engine, 404, "rewrite_session_not_found")
    html = page(post(operator_client, {f"choice_{Q1}": "o1"}), status=404)
    assert '<p class="not-found">No such rewrite session for this story</p>' in html


def test_paused_story_can_answer(operator_client: Client, engine: FakeEngine) -> None:
    story = m7("story")
    story.update(
        status="paused",
        pause={"job_id": "01JM7JB0000000000000000001", "cause": "backfill", "progress": {}},
    )
    answer_raw(engine, "GET", "/stories/{story_id}", story)
    served(engine, session_body("rewrite_open"))
    answered_with(engine)
    html = page(operator_client.get(SESSION_URL))
    assert f'<form method="post" action="{ANSWER_URL}" class="answers">' in html
    response = post(operator_client, {f"choice_{Q1}": "o1"})
    assert response.status_code == 303
    assert len(posts(engine)) == 1


def test_rejected_question_is_listed_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = session_body("rewrite_open")
    body["questions"].append({"id": Q2, "prompt": 7})
    answer_raw(engine, "GET", ONE_ROUTE, body)
    html = page(operator_client.get(SESSION_URL))
    assert REJECTED_QUESTION in html
    assert '{"id":"01JM7QN0000000000000000002","prompt":7}' in html
    assert f'name="choice_{Q1}"' in html
    assert f'name="choice_{Q2}"' not in html
    # It takes no part in the guards: the good question's answer goes through.
    answered_with(engine)
    post(operator_client, {f"choice_{Q1}": "o1"})
    assert sent_body(engine) == {"answers": [{"question_id": Q1, "option_id": "o1"}]}


def test_character_viewpoint_never_sent(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, session_body("rewrite_open"))
    answered_with(engine)
    post(operator_client, {f"choice_{Q1}": "o1"}, url=f"{ANSWER_URL}?viewpoint=character:x")
    rewrite_calls = [r for r in engine.requests if "/rewrites" in r.url.path]
    assert [(r.method, r.url.path) for r in rewrite_calls] == [("GET", GET_ONE), ("POST", ANSWERS)]
    for request in rewrite_calls:
        params = list(request.url.params.multi_items())
        assert params == [("viewpoint", "omniscient")]
        # **Complement:** none carries ``actor_character_id``.
        assert "actor_character_id" not in request.url.query.decode()
        assert "actor_character_id" not in request.content.decode()


def test_session_id_in_the_route_is_a_ulid(operator_client: Client, engine: FakeEngine) -> None:
    url = SESSION_URL.replace(f"{SESSION}/", "not-a-session/answers/")
    assert operator_client.post(url).status_code == 404
    assert calls(engine) == []

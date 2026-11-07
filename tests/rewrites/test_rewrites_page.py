"""#297's Rewrite page (``stories:rewrites``, ``console/views/rewrites.py``): the session list
(reads, rows, state filter, paging, rejected entries, a failed read), the omniscient line, and
opening a session (the body, the flashes, the invalid form, the refusals and D8's key rule),
against the fake Engine over #292's ``m7/`` fixtures."""

import json
import re
from html import unescape
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.test import Client

from console.engine.client import turn_timeout
from console.forms.rewrites import INSTRUCTION_HELP, INSTRUCTION_REQUIRED, INSTRUCTION_TOO_LONG
from console.inspect import LITE_REWRITES_TEXT
from console.paging import CURSOR_DROPPED
from console.rewrites import (
    NO_MORE_SESSIONS,
    NO_SESSIONS,
    NO_SESSIONS_MATCH,
    NOOP_TEXT,
    OMNISCIENT_LINE,
    REACH_FAILED_TEXT,
    UNPARSEABLE_TEXT,
)
from console.story_pause import PAUSED_LINE
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json
from tests.rewrites.helpers import (
    CONFIG_READ,
    LIST,
    LIST_ROUTE,
    SESSION,
    SESSION_URL,
    STORY,
    STORY_READ,
    URL,
    answer_raw,
    calls,
    engine,
    key_in,
    m7,
    queries,
    written,
)

pytestmark = pytest.mark.django_db
__all__ = ["engine"]  # the fixture, re-exported for pytest

INSTRUCTION = "Bobby actually went to UCLA."
KEY = "01JM7KY0000000000000000009"
AMENDMENT = "01JM7AM0000000000000000003"
OPEN = ("POST", LIST)


def listed(engine: FakeEngine, body: Any | None = None) -> None:
    engine.respond("GET", LIST_ROUTE, 200, json=body if body is not None else m7("rewrites"))


def empty(engine: FakeEngine) -> None:
    listed(engine, {"items": [], "next_cursor": None})


def page(client: Client, url: str = URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return str(response.content.decode())


def opened(engine: FakeEngine, body: Any, status: int = 201) -> None:
    engine.respond("POST", LIST_ROUTE, status, json=body)


def post(client: Client, url: str = URL, **data: Any) -> Any:
    form = {"instruction": INSTRUCTION, "resolution": "synchronous", "idempotency_key": KEY}
    form.update(data)
    return client.post(url, {k: v for k, v in form.items() if v is not None})


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def sent_body(engine: FakeEngine) -> Any:
    (request,) = [r for r in engine.requests if r.method == "POST"]
    return json.loads(request.content)


def opens(engine: FakeEngine) -> int:
    return calls(engine).count(OPEN)


def squeezed(html: str) -> str:
    return re.sub(r">\s+<", "><", html)


# --- the reads ------------------------------------------------------------------------------------


def test_list_reads_in_order(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    page(operator_client)
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), ("GET", LIST)]
    # **Complement:** no state and no cursor in the query, and no other call.
    assert queries(engine, "GET", LIST) == [[("viewpoint", "omniscient"), ("limit", "20")]]


def test_the_page_title_heading_and_current_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    listed(engine)
    html = page(operator_client)
    assert "<title>Rewrite · Harbour Ledger</title>" in html
    assert "<h2>Rewrite</h2>" in html
    assert f'<a href="{URL}" aria-current="page">Rewrite</a>' in html


def test_rows_render_state_kind_questions_and_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    listed(engine)
    html = squeezed(page(operator_client))
    rows = re.findall(r"<tr><td>(.*?)</tr>", html)
    assert len(rows) == 3
    first, second, third = rows
    assert 'title="awaiting_answers">Waiting for your answers</span>' in first
    assert "<td>Rewrite</td>" in first
    assert "<td>Bob never forged the ledger; he only copied it</td>" in first
    assert "<td>1 open</td>" in first
    assert f'<a href="{SESSION_URL}"><code>{SESSION}</code></a>' in first
    assert 'title="2026-11-06T10:00:00Z"' in first  # Expires, through engine_time
    assert 'title="awaiting_approval">Ready to apply</span>' in second
    assert "<td>Line edit</td><td>—</td><td>—</td>" in second  # no instruction, no questions
    assert 'title="applied">Applied</span>' in third
    assert "<td>Undo</td>" in third
    assert "3 sessions on this page" in html


def test_a_terminal_session_reads_no_questions_or_expiry(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** a terminal session's Expires and Questions read "—", even with
    ``open_questions > 0``."""
    body = m7("rewrites")
    body["items"] = [{**body["items"][0], "state": "cancelled", "open_questions": 2}]
    listed(engine, body)
    html = squeezed(page(operator_client))
    (row,) = re.findall(r"<tr><td>(.*?)</tr>", html)
    assert 'title="cancelled">Cancelled</span>' in row
    cells = re.findall(r"<td>(.*?)</td>", f"<td>{row}")
    assert cells[3] == "—"  # Questions
    assert cells[4].startswith('<time datetime="2026-11-05T10:00:00Z"')  # Opened
    assert cells[5] == "—"  # Expires
    assert "2 open" not in row
    assert 'title="2026-11-06T10:00:00Z"' not in row
    assert "1 session on this page" in html


def test_state_filter_repeats_state(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = page(operator_client, f"{URL}?state=awaiting_approval&state=awaiting_answers")
    (query,) = queries(engine, "GET", LIST)
    assert query == [
        ("viewpoint", "omniscient"),
        ("state", "awaiting_answers"),
        ("state", "awaiting_approval"),
        ("limit", "20"),
    ]
    assert "3 sessions on this page, matching filters" in html
    assert f'<a href="{URL}">Clear filters</a>' in html
    boxes = re.findall(r'<input type="checkbox" name="state" value="(\w+)"[^>]*>', html)
    assert boxes == [
        "awaiting_answers",
        "awaiting_approval",
        "applying",
        "applied",
        "cancelled",
        "expired",
        "abandoned",
    ]
    assert html.count("checked") == 2


def test_an_out_of_choice_state_is_a_field_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** ``state=bogus`` shows a field error, and the fake saw no list read."""
    listed(engine)
    html = page(operator_client, f"{URL}?state=bogus&state=applied")
    assert "Select a valid choice. bogus is not one of the available choices." in html
    assert ("GET", LIST) not in calls(engine)
    assert "on this page" not in html
    assert re.search(r'value="applied"[^>]*checked', html)  # the valid tick stays ticked


def test_paging_carries_cursor_and_filter(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = page(operator_client, f"{URL}?state=applied&viewpoint=character:x")
    older = f"{URL}?state=applied&amp;cursor=01JM7RW0000000000000000004"
    assert f'<a href="{older}">Older →</a>' in html
    assert "Newest page" not in html  # **Complement:** the first page has no "Newest page"
    engine.reset()
    body = m7("rewrites")
    body["next_cursor"] = None
    listed(engine, body)
    html = page(operator_client, f"{URL}?state=applied&cursor=01JM7RW0000000000000000004")
    (query,) = queries(engine, "GET", LIST)
    assert ("cursor", "01JM7RW0000000000000000004") in query
    assert f'<a href="{URL}?state=applied">Newest page</a>' in html
    assert "Older →" not in html  # a ``next_cursor: null`` answer gives no "Older →"


def test_a_bad_cursor_is_dropped(operator_client: Client, engine: FakeEngine) -> None:
    empty(engine)
    html = page(operator_client, f"{URL}?cursor=not-a-ulid")
    assert CURSOR_DROPPED in html
    (query,) = queries(engine, "GET", LIST)
    assert "cursor" not in dict(query)
    assert NO_SESSIONS in html
    assert "Newest page" not in html


def test_the_empty_texts(operator_client: Client, engine: FakeEngine) -> None:
    empty(engine)
    html = page(operator_client)
    assert f'<p class="empty">{NO_SESSIONS}</p>' in html
    assert "on this page" not in html
    html = page(operator_client, f"{URL}?state=cancelled")
    assert NO_SESSIONS_MATCH in html
    assert NO_SESSIONS not in html
    html = page(operator_client, f"{URL}?cursor=01JM7RW0000000000000000004")
    assert NO_MORE_SESSIONS in html


def test_bad_item_keeps_the_good_rows(operator_client: Client, engine: FakeEngine) -> None:
    answer_raw(engine, "GET", LIST_ROUTE, m7("rewrites_bad_item"))
    html = squeezed(page(operator_client))
    assert '<p class="notice rejected-notice">1 session couldn&#x27;t be read</p>' in html
    assert len(re.findall(r"<tr><td>(.*?)</tr>", html)) == 2
    rejected = re.findall(r'<tr class="rejected"><td colspan="7">(.*?)</td></tr>', html, re.S)
    assert len(rejected) == 1
    assert rejected[0].startswith("This session couldn&#x27;t be read<details>")
    assert "01JM7RW0000000000000000009" in rejected[0]
    assert "<a " not in rejected[0]
    assert "2 sessions on this page" in html


def test_an_all_rejected_page_shows_only_the_notice(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_raw(engine, "GET", LIST_ROUTE, {"items": [{"id": SESSION}], "next_cursor": None})
    html = page(operator_client)
    assert "1 session couldn&#x27;t be read" in html
    assert NO_SESSIONS not in html
    assert "on this page" not in html


def test_list_read_failure_keeps_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST_ROUTE, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client)
    assert "database_unavailable" in html or "down" in html
    assert '<form method="post"' in html
    assert 'name="instruction"' in html
    assert "on this page" not in html
    assert NO_SESSIONS not in html


def test_no_viewpoint_picker(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    html = page(operator_client, f"{URL}?viewpoint=character:01JM7CH0000000000000000002")
    assert 'name="viewpoint"' not in html
    assert OMNISCIENT_LINE in html


def test_the_new_rewrite_form(operator_client: Client, engine: FakeEngine) -> None:
    empty(engine)
    html = page(operator_client)
    assert '<label for="id_instruction">Instruction:</label>' in html
    assert 'rows="4"' in html
    form = html[
        html.index('class="new-rewrite-form"') : html.index(
            "</form>", html.index('class="new-rewrite-form"')
        )
    ]
    assert "maxlength" not in form
    assert INSTRUCTION_HELP.replace("“", "“") in html
    assert "<summary>Advanced</summary>" in html
    assert re.search(r'<input type="checkbox" name="apply_directly"[^>]*>', html)
    assert not re.search(r'name="apply_directly"[^>]*checked', html)
    assert '<option value="synchronous" selected>Ask me about every conflict</option>' in html
    assert '<button type="submit">Plan rewrite</button>' in html
    assert html.index('name="instruction"') < html.index('class="rewrite-filter"')
    key = key_in(html)
    assert key != key_in(page(operator_client))  # a fresh key per render


# --- opening a session ----------------------------------------------------------------------------


def test_open_sends_instruction_and_default_modes(
    operator_client: Client, engine: FakeEngine
) -> None:
    opened(engine, m7("rewrite_open"))
    response = post(operator_client)
    assert response.status_code == 303
    assert response["Location"] == SESSION_URL
    assert sent_body(engine) == {
        "instruction": INSTRUCTION,
        "preview": "preview_first",
        "resolution_mode": "synchronous",
    }
    (request,) = [r for r in engine.requests if r.method == "POST"]
    assert list(request.url.params.multi_items()) == [("viewpoint", "omniscient")]
    assert request.headers["Idempotency-Key"] == KEY
    assert "X-Epistrel-Expected-Revision" not in request.headers
    assert flashes(response) == [
        "The Engine has 1 question before it can plan — idempotency key "
        f"<code>{m7('rewrite_open')['idempotency_key'] or KEY}</code>"
    ]
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), OPEN]


def test_open_waits_the_turn_timeout(operator_client: Client, engine: FakeEngine) -> None:
    """E19: the client waits ``turn_timeout(None)`` for the open."""
    opened(engine, m7("rewrite_open"))
    post(operator_client)
    (request,) = [r for r in engine.requests if r.method == "POST"]
    assert request.extensions["timeout"]["read"] == turn_timeout(None)


def test_open_with_threshold_and_apply_directly(
    operator_client: Client, engine: FakeEngine
) -> None:
    applied = written(
        "rewrite_session_applied",
        apply={
            "amendment_id": "01JM7AM0000000000000000003",
            "report": {"applied": []},
            "timeline_revision": 21,
        },
        timeline_revision=21,
        idempotency_key=KEY,
    )
    opened(engine, applied)
    response = post(operator_client, apply_directly="on", resolution="threshold_high")
    assert response.status_code == 303
    body = sent_body(engine)
    assert (body["preview"], body["resolution_mode"]) == ("apply_directly", {"threshold": "high"})
    assert flashes(response) == [
        f'Applied directly: amendment <a href="/stories/{STORY}/amendments/{AMENDMENT}/">'
        f"<code>{AMENDMENT}</code></a> · story revision "
        f"now 21 — idempotency key <code>{KEY}</code>"
    ]


def test_apply_directly_not_applied_says_so(operator_client: Client, engine: FakeEngine) -> None:
    opened(engine, m7("rewrite_open"))
    response = post(operator_client, apply_directly="on")
    (flash,) = flashes(response)
    assert flash.startswith("Not applied directly: The Engine has 1 question before it can plan")


def test_preview_first_never_reports_applied(operator_client: Client, engine: FakeEngine) -> None:
    opened(engine, written("rewrite_session"))
    response = post(operator_client)
    assert response.status_code == 303
    (flash,) = flashes(response)
    assert (
        flash == f"Planned: review the preview, then apply it — idempotency key <code>{KEY}</code>"
    )
    # **Complement:** no "Applied" text, and the fake saw no apply.
    assert "Applied" not in flash
    assert not [p for m, p in calls(engine) if m == "POST" and p.endswith("/apply")]


def test_another_written_state_names_it(operator_client: Client, engine: FakeEngine) -> None:
    answer_raw(engine, "POST", LIST_ROUTE, written("rewrite_session", state="applying"), 201)
    (flash,) = flashes(post(operator_client))
    assert flash.startswith("Rewrite opened — the session is now Applying…")


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"instruction": "   "}, INSTRUCTION_REQUIRED),
        ({"instruction": "x" * 4001}, INSTRUCTION_TOO_LONG),
        ({"resolution": "bogus"}, "Select a valid choice. bogus is not one of the available"),
    ],
    ids=["blank", "too-long", "bad-resolution"],
)
def test_invalid_form_makes_no_engine_write(
    operator_client: Client, engine: FakeEngine, data: dict[str, Any], error: str
) -> None:
    listed(engine)
    response = post(operator_client, **data)
    assert response.status_code == 200
    html = response.content.decode()
    assert error in unescape(html)
    assert key_in(html) == KEY  # the posted key kept
    # **Complement:** no write, and the list read again.
    assert opens(engine) == 0
    assert ("GET", LIST) in calls(engine)


def test_exactly_4000_characters_is_accepted(operator_client: Client, engine: FakeEngine) -> None:
    opened(engine, m7("rewrite_open"))
    response = post(operator_client, instruction=" " + "x" * 4000 + " ")
    assert response.status_code == 303
    assert sent_body(engine)["instruction"] == "x" * 4000


REFUSALS = [
    pytest.param(422, error_json("rewrite_noop"), NOOP_TEXT, True, id="noop"),
    pytest.param(422, error_json("rewrite_unparseable"), UNPARSEABLE_TEXT, True, id="unparseable"),
    pytest.param(
        422,
        error_json(
            "guardrail_violation",
            violations=[
                {"name": "No real people", "policy_id": "p1", "locator": "body.instruction"},
                {"name": "No slurs", "policy_id": "p2", "locator": "body.instruction"},
            ],
        ),
        "That text breaks the story's input guardrails (No real people, No slurs) — "
        "nothing was written",
        True,
        id="guardrail",
    ),
    pytest.param(
        409,
        error_json("rewrite_session_limit", limit=3, open=3),
        "This story already has the most open rewrite sessions allowed (3) — finish or cancel "
        f'one first <a href="{URL}">Rewrite</a></p>',
        False,
        id="limit",
    ),
    pytest.param(
        409,
        error_json("module_off", module="lite_rewrites"),
        f'|<a href="/stories/{STORY}/config/">Config</a>',
        False,
        id="module-off",
    ),
    pytest.param(
        409,
        error_json("story_paused", cause="rating_check"),
        PAUSED_LINE,
        False,
        id="paused",
    ),
    pytest.param(
        503,
        error_json("rewrite_reach_failed", retryable=True),
        REACH_FAILED_TEXT,
        False,
        id="reach-failed",
    ),
]


@pytest.mark.parametrize(("status", "body", "text", "on_field"), REFUSALS)
def test_refusals_word_and_place(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    body: dict[str, Any],
    text: str,
    on_field: bool,
) -> None:
    listed(engine)
    engine.respond("POST", LIST_ROUTE, status, json=body)
    response = post(operator_client)
    assert response.status_code == 200  # not a redirect
    html = unescape(response.content.decode())
    for part in text.split("|"):
        if part:
            assert part in html
    if body["error"]["code"] == "module_off":
        assert LITE_REWRITES_TEXT.split("(")[0] in html
    if body["error"]["code"] == "story_paused":
        assert 'class="errorlist rewrite-refusal"' in html
    field = html[html.index('class="field field-instruction"') : html.index("Advanced")]
    refusal_above = 'class="errorlist rewrite-refusal"' in html
    assert refusal_above is not on_field
    if on_field:
        assert text.split("|")[0][:30] in field
    # **Complement:** the typed instruction kept, a fresh key, exactly one open, no retry.
    assert f"{INSTRUCTION}</textarea>" in html
    assert key_in(html) != KEY
    assert opens(engine) == 1
    assert ("GET", LIST) in calls(engine)


def test_a_validation_error_lands_on_its_field(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    engine.respond(
        "POST",
        LIST_ROUTE,
        422,
        json=error_json(
            "validation_error",
            "invalid",
            field="body.resolution_mode",
            errors=[{"field": "body.resolution_mode", "message": "Not a mode"}],
        ),
    )
    html = post(operator_client).content.decode()
    modes = html[html.index('id="id_resolution"') :]
    assert "Not a mode" in modes
    assert key_in(html) != KEY


def test_an_unworded_refusal_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    engine.respond("POST", LIST_ROUTE, 409, json=error_json("idempotency_conflict", "reused key"))
    response = post(operator_client)
    html = response.content.decode()
    assert response.status_code == 200
    assert "reused key" in html or "idempotency_conflict" in html
    assert key_in(html) != KEY


def test_transport_failure_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    engine.fail("POST", LIST_ROUTE, httpx.ReadTimeout("slow"))
    response = post(operator_client)
    assert response.status_code == 200
    html = response.content.decode()
    assert "engine-error" in html or "timeout" in html.lower()
    assert key_in(html) == KEY
    assert f"{INSTRUCTION}</textarea>" in html


def test_a_bad_response_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    listed(engine)
    answer_raw(engine, "POST", LIST_ROUTE, {"id": SESSION}, 201)
    html = post(operator_client).content.decode()
    assert key_in(html) == KEY


def test_the_open_writes_when_the_story_read_failed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories/{story_id}", 503, json=error_json("database_unavailable"))
    opened(engine, m7("rewrite_open"))
    response = post(operator_client)
    assert response.status_code == 303
    assert opens(engine) == 1


def test_the_form_action_keeps_only_the_cleaned_filter(
    operator_client: Client, engine: FakeEngine
) -> None:
    listed(engine)
    html = page(operator_client, f"{URL}?state=applied&viewpoint=character:x&junk=1")
    assert f'<form method="post" action="{URL}?state=applied" class="new-rewrite-form">' in html

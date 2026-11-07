"""The line-edit page (#305): its target, the one omniscient history read that shows the line,
the form's validation and body, the opened session's redirect and the refusals. The cast is
#292's ``m7/story.json`` (Mara the player, Alice, Bob); the opened session is
``m7/rewrite_line_edit.json`` (``kind: rewrite`` with no instruction, ``awaiting_approval``)."""

import copy
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.utils.html import escape

from console.forms.rewrites import LINE_REQUIRED, LINE_TOO_LONG, LINE_UNCHANGED
from console.rewrites import (
    BEAT_REQUIRED_TEXT,
    INACTIVE_SWAP_TEXT,
    INACTIVE_TEXT,
    NOOP_TEXT,
    NOT_EDITABLE_TEXT,
    OMNISCIENT_LINE,
    PLANNED_TEXT,
    REACH_FAILED_TEXT,
)
from console.views.line_edit import LINE_UNREAD, NO_LINE_TEXT, SHARED_MESSAGE_NOTE
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import error_json
from tests.rewrites.helpers import (
    CONFIG_READ,
    LIST,
    LIST_ROUTE,
    STORY,
    STORY_READ,
    engine,
    key_in,
    m7,
    written,
)

__all__ = ["engine"]

pytestmark = pytest.mark.django_db

PAGE = f"/stories/{STORY}/history/line/"
HISTORY = f"/stories/{STORY}/history"
HISTORY_ROUTE = "/stories/{story_id}/history"
ALICE = "01JM7CH0000000000000000002"
LINE = "01JM7EV0000000000000000002"
SHARED = "01JM7MM0000000000000000201"
LINE_TEXT = "I forged it."
SESSION_ID = "01JM7RW0000000000000000006"
NEXT = f"/stories/{STORY}/history/?viewpoint=character:01JM7CH0000000000000000002"


def _item(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = copy.deepcopy(engine_fixture("m7/history_as_recorded")["items"][2])
    base.update(fields)
    return base


def line_history() -> dict[str, Any]:
    """An omniscient read as of turn 2: Mara's say + do (one message) and Alice's line."""
    body: dict[str, Any] = m7("history_as_recorded")
    body["view"] = "corrected"
    for key in ("boundary_tx", "erasure_markers", "derived"):
        body.pop(key)
    body["items"] = [
        *body["items"][:2],
        _item(beat=1, text="Show me the ledger, Bob."),
        _item(beat=1, channel="do", text="Holds out a hand."),
        _item(
            beat=2,
            kind="beat_say",
            actor_character_id=ALICE,
            channel="say",
            text=LINE_TEXT,
            message_id=LINE,
        ),
    ]
    return body


def serve(fake: FakeEngine, history: dict[str, Any] | None = None) -> None:
    fake.respond("GET", HISTORY_ROUTE, 200, json=history or line_history())


def opened(fake: FakeEngine) -> None:
    fake.respond("POST", LIST_ROUTE, 201, json=written("rewrite_line_edit"))


def refused(fake: FakeEngine, status: int, code: str, **details: Any) -> None:
    fake.respond("POST", LIST_ROUTE, status, json=error_json(code, "refused", **details))


def get(client: Client, query: str) -> str:
    response = client.get(f"{PAGE}?{query}")
    assert response.status_code == 200
    return response.content.decode()


def post(client: Client, **data: str) -> Any:
    fields = {
        "event_id": LINE,
        "turn": "2",
        "beat": "",
        "next": "",
        "original": LINE_TEXT,
        "new_text": "I copied it.",
        "resolution": "synchronous",
        "idempotency_key": "01JM7KY0000000000000000009",
    }
    fields.update(data)
    return client.post(PAGE, fields)


def requests(fake: FakeEngine) -> list[tuple[str, str]]:
    return [(r.method, r.url.path) for r in fake.requests]


def writes(fake: FakeEngine) -> list[Any]:
    return [r for r in fake.requests if r.method == "POST" and r.url.path == LIST]


def sent(fake: FakeEngine) -> Any:
    found = writes(fake)
    assert len(found) == 1
    return json.loads(found[0].content)


def textarea(html: str) -> str:
    start = html.index('name="new_text"')
    open_end = html.index(">", start) + 1
    return html[open_end : html.index("</textarea>", open_end)]


def new_text_errors(html: str) -> str:
    start = html.index('<div class="field field-new-text">')
    return html[start : html.index("</div>", start)]


# --- GET ------------------------------------------------------------------------------------------


def test_get_reads_the_line_and_prefills(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = get(operator_client, f"event_id={LINE}&turn=2&next={NEXT}")
    assert requests(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), ("GET", HISTORY)]
    history_read = next(r for r in engine.requests if r.url.path == HISTORY)
    assert dict(history_read.url.params) == {
        "viewpoint": "omniscient",
        "as_of": "2",
        "limit": "200",
    }
    assert "Turn 2 · beat 2 · Alice · say" in html
    assert f'<blockquote class="line-text">{LINE_TEXT}</blockquote>' in html
    assert textarea(html) == f"\n{LINE_TEXT}"
    assert f'name="original" value="{LINE_TEXT}"' in html
    assert escape(OMNISCIENT_LINE) in html or OMNISCIENT_LINE in html
    assert "Plan the edit" in html
    assert escape(LINE_UNREAD) not in html
    # Complement: no write, and no viewpoint picker.
    assert not writes(engine)
    assert 'name="viewpoint"' not in html


def test_back_link(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = get(operator_client, f"event_id={LINE}&turn=2")
    assert f'<a href="/stories/{STORY}/history/#turn-2-1">← Back to History</a>' in html
    html = get(operator_client, f"event_id={LINE}&turn=2&next={NEXT}")
    assert f'<a href="{escape(NEXT)}">← Back to History</a>' in html
    unsafe = get(operator_client, f"event_id={LINE}&turn=2&next=https://evil.example/")
    assert "evil.example" not in unsafe.split('<p class="back">')[1]


def test_event_id_is_trimmed_and_upper_cased(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = get(operator_client, f"event_id=%20{LINE.lower()}%20&turn=2")
    assert f'name="event_id" value="{LINE}"' in html
    assert textarea(html) == f"\n{LINE_TEXT}"


def test_shared_message_joins_and_notes(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = get(operator_client, f"event_id={SHARED}&turn=2")
    assert "Turn 2 · beat 1 · Mara · say" in html
    assert "Turn 2 · beat 1 · Mara · do" in html
    assert textarea(html) == "\nShow me the ledger, Bob.\nHolds out a hand."
    assert SHARED_MESSAGE_NOTE in html


def test_unread_line_still_offers_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", HISTORY_ROUTE, 503, json=error_json("database_unavailable", "down"))
    html = get(operator_client, f"event_id={LINE}&turn=2")
    assert escape(LINE_UNREAD) in html
    assert textarea(html) == "\n"
    assert "value=" not in html.split('name="original"')[1].split(">")[0]
    assert '<form method="post" class="line-edit-form">' in html


def test_no_turn_means_no_read(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, f"event_id={LINE}&turn=nope")
    assert escape(LINE_UNREAD) in html
    assert requests(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ)]
    assert f'<a href="/stories/{STORY}/history/">← Back to History</a>' in html


def test_a_line_the_read_lacks_or_erased_is_unread(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    assert escape(LINE_UNREAD) in get(operator_client, "event_id=01JM7EV0000000000000000099&turn=2")
    body = line_history()
    body["items"][-1]["redacted"] = True
    serve(engine, body)
    html = get(operator_client, f"event_id={LINE}&turn=2")
    assert escape(LINE_UNREAD) in html
    assert textarea(html) == "\n"


@pytest.mark.parametrize(
    "query", ["event_id=nope", "event_id=nope&turn=2", "turn=0", "beat=1", "turn=2&beat=x", ""]
)
def test_malformed_target_has_no_form(
    operator_client: Client, engine: FakeEngine, query: str
) -> None:
    html = get(operator_client, query)
    assert escape(NO_LINE_TEXT) in html
    assert "line-edit-form" not in html
    assert "← Back to History" in html
    # Complement: only the frame's two reads.
    assert requests(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ)]


def test_by_turn_with_several_messages_lists_choices(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    html = get(operator_client, "turn=2")
    assert BEAT_REQUIRED_TEXT in html
    assert html.count('<ul class="line-choices">') == 1
    assert f"event_id={SHARED}&amp;turn=2" in html
    assert f"event_id={LINE}&amp;turn=2" in html
    assert textarea(html) == "\n"


def test_by_turn_and_beat_reads_that_line(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = get(operator_client, "turn=2&beat=2")
    assert textarea(html) == f"\n{LINE_TEXT}"
    assert f'value="{LINE}"' not in html.split('name="event_id"')[1].split(">")[0]


# --- POST -----------------------------------------------------------------------------------------


def test_body_by_event_id(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    opened(engine)
    post(operator_client, new_text="It was raining.")
    request = writes(engine)[0]
    assert sent(engine) == {
        "line_edit": {"event_id": LINE, "new_text": "It was raining."},
        "preview": "preview_first",
        "resolution_mode": "synchronous",
    }
    assert dict(request.url.params) == {"viewpoint": "omniscient"}
    # Complement: no instruction, and no turn or beat beside the id.
    assert "instruction" not in sent(engine)
    assert set(sent(engine)["line_edit"]) == {"event_id", "new_text"}


def test_body_by_turn_and_beat(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    opened(engine)
    post(operator_client, event_id="", turn="3", beat="2", original="", new_text="Hm.")
    assert sent(engine)["line_edit"] == {"turn": 3, "beat": 2, "new_text": "Hm."}
    assert "event_id" not in sent(engine)["line_edit"]


def test_body_modes(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    opened(engine)
    post(operator_client, resolution="threshold_high", apply_directly="on")
    body = sent(engine)
    assert body["preview"] == "apply_directly"
    assert body["resolution_mode"] == {"threshold": "high"}


def test_new_text_is_verbatim(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    opened(engine)
    post(operator_client, new_text="  I copied it.\r\n  ")
    assert sent(engine)["line_edit"]["new_text"] == "  I copied it.\n  "


INVALID = {
    "blank": ("   \r\n ", LINE_REQUIRED),
    "too long": ("x" * 8001, LINE_TOO_LONG),
    "unchanged": (LINE_TEXT, LINE_UNCHANGED),
    "unchanged after CRLF": ("a\r\nb", LINE_UNCHANGED),
}


@pytest.mark.parametrize(("text", "error"), INVALID.values(), ids=INVALID.keys())
def test_invalid_form_sends_nothing(
    operator_client: Client, engine: FakeEngine, text: str, error: str
) -> None:
    serve(engine)
    original = "a\nb" if text == "a\r\nb" else LINE_TEXT
    response = post(operator_client, new_text=text, original=original)
    assert response.status_code == 200
    html = response.content.decode()
    assert escape(error) in new_text_errors(html)
    # Complement: no POST …/rewrites.
    assert not writes(engine)
    assert "maxlength" not in html.split('name="new_text"')[1].split(">")[0]


def test_unchanged_is_checked_only_when_read(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    opened(engine)
    post(operator_client, new_text=LINE_TEXT, original="")
    assert sent(engine)["line_edit"]["new_text"] == LINE_TEXT


def test_exactly_8000_characters_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    opened(engine)
    post(operator_client, new_text="x" * 8000)
    assert len(sent(engine)["line_edit"]["new_text"]) == 8000


def test_opened_redirects_to_the_session(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    opened(engine)
    response = post(operator_client)
    assert response.status_code == 303
    assert response["Location"] == f"/stories/{STORY}/rewrites/{SESSION_ID}/"
    flashes = [str(m) for m in get_messages(response.wsgi_request)]
    assert flashes
    assert flashes[-1].startswith(PLANNED_TEXT)
    assert flashes[-1].endswith("</code>")
    # The write is the only call after the frame's two: no read before it.
    assert requests(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), ("POST", LIST)]


def test_post_with_a_tampered_target_writes_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = post(operator_client, event_id="nope")
    assert escape(NO_LINE_TEXT) in response.content.decode()
    assert not writes(engine)


BEATS = [
    {"beat": 1, "event_id": SHARED, "speaker_kind": "player"},
    {"beat": 2, "event_id": LINE, "speaker_kind": "character"},
]
REFUSALS = {
    "line_not_editable": (
        422,
        {"event_kind": "state_change"},
        NOT_EDITABLE_TEXT.format(kind="State change"),
        "above",
    ),
    "beat_required": (422, {"beats": BEATS}, BEAT_REQUIRED_TEXT, "above"),
    "record_inactive": (
        409,
        {
            "record_table": "transcript_messages",
            "record_id": LINE,
            "membership": "dormant",
            "dormant_variant": {"turn": 2, "variant": 1},
        },
        INACTIVE_TEXT.format(membership="Dormant") + INACTIVE_SWAP_TEXT.format(turn=2, variant=1),
        "above",
    ),
    "rewrite_noop": (422, {}, NOOP_TEXT, "new_text"),
    "guardrail_violation": (
        422,
        {"violations": [{"name": "No slurs", "locator": "body.line_edit.new_text"}]},
        "That text breaks the story's input guardrails (No slurs) — nothing was written",
        "new_text",
    ),
    "rewrite_reach_failed": (503, {}, REACH_FAILED_TEXT, "above"),
    "module_off": (
        422,
        {"module": "lite_rewrites"},
        "History rewrites need a planner for this story's pipeline",
        "above",
    ),
}


@pytest.mark.parametrize(
    ("code", "status", "details", "text", "where"),
    [(code, *case) for code, case in REFUSALS.items()],
    ids=REFUSALS.keys(),
)
def test_refusals(
    operator_client: Client,
    engine: FakeEngine,
    code: str,
    status: int,
    details: dict[str, Any],
    text: str,
    where: str,
) -> None:
    serve(engine)
    refused(engine, status, code, **details)
    posted_key = "01JM7KY0000000000000000009"
    response = post(operator_client, new_text="Typed text.")
    assert response.status_code == 200
    html = response.content.decode()
    if where == "new_text":
        assert escape(text) in new_text_errors(html)
    else:
        refusal = html.split('<p class="errorlist rewrite-refusal" role="alert">')[1].split("</p>")[
            0
        ]
        assert escape(text) in refusal
    # Complement: the typed text is kept, a fresh key is minted, and one POST was made.
    assert textarea(html) == "\nTyped text."
    assert key_in(html) != posted_key
    assert len(writes(engine)) == 1
    if code == "beat_required":
        choices = html.split('<ul class="beat-choices">')[1].split("</ul>")[0]
        assert "Beat 1 (Player)" in choices
        assert "Beat 2 (Character)" in choices
        hrefs = [
            parse_qs(urlsplit(h.replace("&amp;", "&")).query) for h in choices.split('href="')[1:]
        ]
        assert [q["event_id"] for q in hrefs] == [[SHARED], [LINE]]
    if code == "record_inactive":
        assert f'<a href="/stories/{STORY}/history/#variants">Variants</a>' in html
    if code == "module_off":
        assert f'<a href="/stories/{STORY}/config/">Config</a>' in html


def test_unknown_outcome_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    refused(engine, 503, "database_unavailable")
    response = post(operator_client)
    html = response.content.decode()
    assert 'class="engine-error' in html
    assert key_in(html) == "01JM7KY0000000000000000009"


def test_character_viewpoint_never_sent(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    opened(engine)
    get(operator_client, f"event_id={LINE}&turn=2&next={NEXT}")
    post(operator_client, next=NEXT)
    for request in engine.requests:
        if request.url.path in (HISTORY, LIST):
            assert request.url.params.get("viewpoint") == "omniscient"
            assert "actor_character_id" not in request.url.params
    assert "actor_character_id" not in json.dumps(sent(engine))

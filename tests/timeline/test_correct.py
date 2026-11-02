"""#263: correct the latest turn's input through ``POST /stories/{id}/correct`` (FR-RESP-14).

Served by the schema-validating fake Engine under #239's Structured v3 and #232's
``m6/story.json`` cast (Mara the player, Alice, Bob; head turn 3, revision 12). The history read
is ``m6/history_correct_turn.json``: turn 3's ``say`` and ``do`` and one correctable notice with
``external_effects``. The answer is #232's ``m6/corrected.json``, moved to turn 3.
"""

import copy
import re
from typing import Any

import httpx
import pytest
from django.test import Client
from django.utils.html import escape

from console.engine.models import EffectiveConfig, Story
from console.forms.timeline import NOTHING_TO_SEND, UNCHANGED_INPUT, sign_original
from console.models import EngineOperation
from console.notices import EXTERNAL_EFFECTS_TEXT, NOT_CORRECTABLE_REASON
from console.timeline import reload_message
from console.views.play import PlayStory
from console.views.timeline import (
    SETUP_TEXT,
    absent_input_text,
    ahead_text,
    already_corrected_text,
    not_latest_text,
    nothing_to_correct_text,
    reconciliation_clause,
    unread_input_text,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import structured_v3
from tests.play.helpers import error_json
from tests.timeline.helpers import REVISION_HEADER, body_of, calls, flash

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
REV = 12
PLAY = f"/stories/{STORY}/"
CORRECT = f"/stories/{STORY}/correct/3/"
CORRECT_PATH = f"/stories/{STORY}/correct"
HISTORY_PATH = f"/stories/{STORY}/history"
EFFECTIVE = "/stories/{story_id}/effective-config"
SAY = "Where did Bob put the key?"
DO = "Mara takes the key from the drawer."
NEW_DO = "Jenni searches the desk."
CORRECTED_OP = "01JM6QP0000000000000000061"
KEY = "01JM6QP0000000000000000K63"


def story(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("m6/story")
    body.update(changes)
    return body


def corrected(**changes: Any) -> dict[str, Any]:
    """``corrected.json`` as turn 3's correction (variant 2, the replaced variant 1)."""
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m6/corrected"))
    body.update(turn=3)
    for beat in body["beats"]:
        beat.update(turn=3)
    for notice in body["notices"]:
        notice.update(turn=3)
    body.update(changes)
    return body


def serve(
    engine: FakeEngine,
    *,
    story_body: dict[str, Any] | None = None,
    history: dict[str, Any] | None = None,
) -> None:
    engine.respond("GET", "/stories/{story_id}", 200, json=story_body or story())
    engine.respond(
        "GET", "/stories/{story_id}/settings", 200, json=engine_fixture("get_story_settings")
    )
    engine.side_effect("GET", EFFECTIVE, lambda request: httpx.Response(200, json=structured_v3()))
    engine.respond(
        "GET",
        "/stories/{story_id}/history",
        200,
        json=history or engine_fixture("m6/history_correct_turn"),
    )


def snapshot() -> str:
    parsed = Story.from_json(story())
    config = EffectiveConfig.from_json(structured_v3())
    assert parsed is not None
    assert config is not None
    return PlayStory.of(parsed, 45.0, config=config).signed()


def get(client: Client, url: str = CORRECT) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def textarea(page: str, name: str) -> str | None:
    match = re.search(rf'<textarea name="{name}"[^>]*>\n?(.*?)</textarea>', page, re.S)
    return match.group(1) if match else None


def hidden(page: str) -> dict[str, str]:
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))


def form_of(page: str, **fields: str) -> dict[str, str]:
    """What the page's Confirm posts: its hidden fields and textareas, then ``fields``."""
    data = {k: v.replace("&quot;", '"').replace("&amp;", "&") for k, v in hidden(page).items()}
    data.pop("csrfmiddlewaretoken", None)
    for name in ("say", "do"):
        found = textarea(page, name)
        data[name] = found if found is not None else ""
    data.update(fields)
    return data


def posted(**fields: str) -> dict[str, str]:
    """A Confirm POST built without a GET: the story read's revision and head, the snapshot and
    the signed original of the history's input."""
    data = {
        "idempotency_key": KEY,
        "rev": str(REV),
        "head": "3",
        "name": "Harbour Ledger",
        "snapshot": snapshot(),
        "original": sign_original(STORY, 3, SAY, DO),
        "next": PLAY,
        "say": SAY,
        "do": DO,
    }
    data.update(fields)
    return data


def correct_calls(engine: FakeEngine) -> list[Any]:
    return calls(engine, "POST", CORRECT_PATH)


def fact(label: str, value: str) -> str:
    return f"<dt>{label}</dt><dd><code>{escape(value)}</code></dd>"


# --- The page -------------------------------------------------------------------------------------


def test_get_prefills_the_turns_input(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    page = get(operator_client)
    assert "Correct turn 3" in page
    assert escape("Replace turn 3's input in ") + "<em>Harbour Ledger</em>?" in page
    assert textarea(page, "say") == escape(SAY)
    assert textarea(page, "do") == escape(DO)
    assert escape("A whisper isn't carried over") in page
    # The notice, read-only: no Fix / Clarify, no Override, the page's own external line.
    assert '<article class="notice-card" id="notice-01JM6NT0000000000000000263">' in page
    assert "Fix / Clarify" not in page
    assert "Override" not in page
    assert f"<p>{escape(EXTERNAL_EFFECTS_TEXT)}</p>" in page
    assert escape("swap back to it from History \u203a Variants.") in page
    assert fact("Actor", "Mara (player)") in page
    assert fact("Expected timeline revision", str(REV)) in page
    assert fact("Turn", "3") in page
    assert '<button type="submit">Correct turn</button>' in page
    # The reads, in order: read_story's three, then one omniscient history at the turn.
    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("GET", f"/stories/{STORY}"),
        ("GET", f"/stories/{STORY}/settings"),
        ("GET", f"/stories/{STORY}/effective-config"),
        ("GET", HISTORY_PATH),
    ]
    (history,) = calls(engine, "GET", HISTORY_PATH)
    assert dict(history.url.params) == {"viewpoint": "omniscient", "as_of": "3", "limit": "200"}
    # Complement: the GET writes nothing.
    assert correct_calls(engine) == []
    assert not [r for r in engine.requests if r.method != "GET"]


def test_head_renders_like_get(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    response = operator_client.head(CORRECT)
    assert response.status_code == 200
    assert len(calls(engine, "GET", HISTORY_PATH)) == 1
    assert correct_calls(engine) == []


def test_turn_without_input_in_history(operator_client: Client, engine: FakeEngine) -> None:
    history = copy.deepcopy(engine_fixture("m6/history_correct_turn"))
    history["items"] = [i for i in history["items"] if i["kind"] != "player_input"]
    serve(engine, history=history)
    page = get(operator_client)
    assert escape(absent_input_text(3)) in page
    assert textarea(page, "say") == ""
    assert hidden(page)["original"] == ""
    assert fact("Actor", "—") in page


# --- POST -----------------------------------------------------------------------------------------


def test_post_sends_turn_and_input_only(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    page = get(operator_client)
    data = form_of(page, say="", do=NEW_DO)
    engine.reset()
    engine.respond("POST", "/stories/{story_id}/correct", 200, json=corrected())
    response = operator_client.post(CORRECT, data)
    (request,) = correct_calls(engine)
    assert body_of(request) == {"turn": 3, "player_input": {"do": NEW_DO}}
    assert request.headers[REVISION_HEADER] == str(REV)
    assert request.headers["Idempotency-Key"] == data["idempotency_key"]
    # With the signed snapshot, the re-check is one plain story read.
    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("GET", f"/stories/{STORY}"),
        ("POST", CORRECT_PATH),
    ]
    assert response.status_code == 302
    assert response["Location"].startswith(f"{PLAY}?turn=3&viewpoint=character%3A{MARA}")
    assert response["Location"].endswith("#turn-3")
    (line,) = flash(response)
    assert line.startswith(
        "Corrected turn 3: variant 2 is active, the replaced variant 1 is kept as a dormant "
        "variant, config revision 6"
    )
    assert "timeline revision 14" in line
    assert f"idempotency key {data['idempotency_key']}" in line
    row = EngineOperation.objects.get(kind="correct")
    assert row.engine_operation_id == CORRECTED_OP


def test_post_without_snapshot_reads_the_story_once(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    engine.respond("POST", "/stories/{story_id}/correct", 200, json=corrected())
    response = operator_client.post(CORRECT, posted(snapshot="", do=NEW_DO))
    assert response.status_code == 302
    assert len(calls(engine, "GET", f"/stories/{STORY}")) == 1
    assert len(correct_calls(engine)) == 1


def test_flash_counts_reconciliation_required(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    engine.respond("POST", "/stories/{story_id}/correct", 200, json=corrected())
    response = operator_client.post(CORRECT, posted(do=NEW_DO))
    (line,) = flash(response)
    assert "dormant variant, config revision 6; 1 external effect stays listed for " in line
    assert reconciliation_clause(0) == ""
    assert reconciliation_clause(2) == "; 2 external effects stay listed for reconciliation"
    # None listed: no clause.
    engine.respond(
        "POST", "/stories/{story_id}/correct", 200, json=corrected(reconciliation_required=[])
    )
    line = flash(operator_client.post(CORRECT, posted(do=NEW_DO)))[-1]
    assert "reconciliation" not in line


def test_unchanged_input_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    """C9: the pre-filled input posted back (whitespace aside) is refused locally."""
    serve(engine)
    page = get(operator_client)
    data = form_of(page)
    data["do"] = f"  {data['do']}  "
    engine.reset()
    response = operator_client.post(CORRECT, data)
    assert response.status_code == 200
    html = response.content.decode()
    assert escape(UNCHANGED_INPUT) in html
    # D8: local invalid input keeps the posted key and revision.
    assert hidden(html)["idempotency_key"] == data["idempotency_key"]
    assert hidden(html)["rev"] == str(REV)
    # Complement: nothing was sent.
    assert correct_calls(engine) == []


def test_tampered_original_skips_the_unchanged_check(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    engine.respond("POST", "/stories/{story_id}/correct", 200, json=corrected())
    other_turn = sign_original(STORY, 2, SAY, DO)
    for original in ("tampered", other_turn, ""):
        response = operator_client.post(CORRECT, posted(original=original))
        assert response.status_code == 302
    assert len(correct_calls(engine)) == 3


def test_blank_input_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    response = operator_client.post(CORRECT, posted(say="   ", do="\n\t"))
    assert response.status_code == 200
    html = response.content.decode()
    assert escape(NOTHING_TO_SEND) in html
    assert html.index(escape(NOTHING_TO_SEND)) < html.index('<textarea name="say"')
    assert correct_calls(engine) == []


def test_too_long_input_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    page = get(operator_client)
    assert not re.search(r"<textarea[^>]*maxlength", page)  # C10: the server says why
    response = operator_client.post(CORRECT, posted(do="x" * 8001))
    assert response.status_code == 200
    assert "at most 8000 characters" in response.content.decode()
    assert correct_calls(engine) == []


# --- No form --------------------------------------------------------------------------------------


def test_not_latest_offers_no_form(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    url = f"/stories/{STORY}/correct/2/"
    page = get(operator_client, url)
    assert f'<p class="warning">{escape(not_latest_text(2))}</p>' in page
    assert "<textarea" not in page
    assert '<button type="submit" disabled>Correct turn</button>' in page
    assert f'href="/stories/{STORY}/history/?before_turn=3#turn-2-0">History at turn 2</a>' in page
    assert f'href="/stories/{STORY}/history/#variants">Variants</a>' in page
    assert fact("Expected timeline revision", str(REV)) in page
    assert calls(engine, "GET", HISTORY_PATH) == []
    # Complement: a forged POST re-checks the head from the story read and sends nothing.
    engine.reset()
    response = operator_client.post(url, posted(do=NEW_DO))
    assert response.status_code == 200
    assert escape(not_latest_text(2)) in response.content.decode()
    assert correct_calls(engine) == []
    assert calls(engine, "GET", HISTORY_PATH) == []


def test_turn_above_the_head_offers_no_form(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    page = get(operator_client, f"/stories/{STORY}/correct/5/")
    assert escape(ahead_text(5, 3)) in page
    assert "<textarea" not in page
    assert calls(engine, "GET", HISTORY_PATH) == []


def test_turn_zero_and_archived(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    page = get(operator_client, f"/stories/{STORY}/correct/0/")
    assert escape(SETUP_TEXT) in page
    assert "<textarea" not in page
    assert calls(engine, "GET", HISTORY_PATH) == []
    # Archived: C5's text, the fields kept but disabled, the button disabled.
    serve(engine, story_body=story(status="archived", archived_at="2026-10-31T10:00:00Z"))
    page = get(operator_client)
    archived = "This story is archived — unarchive it to correct a turn."
    assert f'<p class="warning">{escape(archived)}</p>' in page
    assert '<button type="submit" disabled>Correct turn</button>' in page
    assert re.search(r'<textarea name="do"[^>]* disabled', page)
    assert textarea(page, "do") == escape(DO)
    engine.reset()
    response = operator_client.post(CORRECT, posted(do=NEW_DO))
    assert response.status_code == 200
    assert escape(archived) in response.content.decode()
    assert correct_calls(engine) == []


def test_paused_story_is_blocked(operator_client: Client, engine: FakeEngine) -> None:
    job = "01JM6JB0000000000000000263"
    pause = {"job_id": job, "cause": "rating_check", "progress": {"done": 2, "total": 5}}
    serve(engine, story_body=story(status="paused", pause=pause))
    page = get(operator_client)
    assert '<p class="warning">The story is paused for a check (2/5)</p>' in page
    assert f'href="/stories/{STORY}/rating/checks/{job}/">The check</a>' in page
    assert '<button type="submit" disabled>Correct turn</button>' in page
    response = operator_client.post(CORRECT, posted(do=NEW_DO))
    assert response.status_code == 200
    assert correct_calls(engine) == []


def test_corrected_turn_is_preempted(operator_client: Client, engine: FakeEngine) -> None:
    history = copy.deepcopy(engine_fixture("m6/history_correct_turn"))
    history["turns"][0]["notices"][0]["correctable"] = False
    serve(engine, history=history)
    page = get(operator_client)
    assert f'<p class="warning">{escape(already_corrected_text(3))}</p>' in page
    assert "<textarea" not in page
    assert f'href="/stories/{STORY}/history/#variants">Variants</a>' in page


# --- Refusals -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "details", "headers", "texts"),
    [
        (409, "not_latest_turn", {"turn": 3, "latest": 4}, {}, [not_latest_text(3)]),
        (409, "already_corrected", {"turn": 3}, {}, [already_corrected_text(3)]),
        (409, "revision_conflict", {"current_revision": 13}, {}, [reload_message(13)]),
        (
            423,
            "story_busy",
            {"reason": "in_flight", "operation_id": "01JM6QP0000000000000000099"},
            {"Retry-After": "2"},
            ["Story busy — another turn is running", "<dt>Retry after</dt>"],
        ),
        (
            422,
            "guardrail_violation",
            {"violations": []},
            {},
            ["Input rejected by a guardrail — no reply was added."],
        ),
        (422, "nothing_to_regenerate", {"turn": 3}, {}, [nothing_to_correct_text(3)]),
        (
            422,
            "provider_unbound",
            {"providers": ["bank"]},
            {},
            ["This story has no binding to bank — bind it first"],
        ),
    ],
)
def test_refusals(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
    headers: dict[str, str],
    texts: list[str],
) -> None:
    serve(engine)
    engine.respond(
        "POST",
        "/stories/{story_id}/correct",
        status,
        json=error_json(code, **details),
        headers=headers,
    )
    response = operator_client.post(CORRECT, posted(do=NEW_DO))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Correction refused" in html
    for text in texts:
        assert (escape(text) if "<" not in text else text) in html
    if code == "not_latest_turn":
        assert "History at turn 3</a>" in html
    if code == "already_corrected":
        assert f'href="/stories/{STORY}/history/#variants">Variants</a>' in html
    assert len(correct_calls(engine)) == 1


def test_field_error_beside_do(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    message = "do must not be blank"
    refused = error_json("validation_error", message, field="body.player_input.do")
    engine.respond("POST", "/stories/{story_id}/correct", 422, json=refused)
    moved = story(timeline_revision=REV + 1)
    engine.respond("GET", "/stories/{story_id}", 200, json=moved)
    response = operator_client.post(CORRECT, posted(do=NEW_DO))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Correction refused" not in html
    do_field = html.split('<div class="field field-do">')[1].split("</div>")[0]
    assert message in do_field
    assert textarea(html, "do") == escape(NEW_DO)
    # D8: an answered refusal gets a fresh key and the POST's own story read's revision.
    assert hidden(html)["idempotency_key"] != KEY
    assert hidden(html)["rev"] == str(REV + 1)


def test_other_validation_error_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    refused = error_json("validation_error", "bad turn", field="body.turn")
    engine.respond("POST", "/stories/{story_id}/correct", 422, json=refused)
    html = operator_client.post(CORRECT, posted(do=NEW_DO)).content.decode()
    assert "Correction refused" in html


def test_transport_error_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    engine.side_effect("POST", "/stories/{story_id}/correct", unreachable)
    response = operator_client.post(CORRECT, posted(do=NEW_DO))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'class="engine-error' in html
    assert hidden(html)["idempotency_key"] == KEY
    assert hidden(html)["rev"] == str(REV)
    assert textarea(html, "do") == escape(NEW_DO)


def test_failing_recheck_read_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    engine.respond("GET", "/stories/{story_id}", 404, json=error_json("not_found"))
    response = operator_client.post(CORRECT, posted(do=NEW_DO))
    assert response.status_code == 200
    assert "Correction refused" in response.content.decode()
    assert correct_calls(engine) == []


def test_history_read_failure_keeps_the_form(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    engine.respond(
        "GET", "/stories/{story_id}/history", 503, json=error_json("database_unavailable")
    )
    page = get(operator_client)
    assert escape(unread_input_text(3)) in page
    assert 'class="engine-error' in page
    assert textarea(page, "say") == ""
    assert textarea(page, "do") == ""
    assert hidden(page)["original"] == ""
    assert fact("Actor", "—") in page
    assert '<button type="submit">Correct turn</button>' in page
    # A POST then goes through (no original, so no unchanged check).
    engine.respond("POST", "/stories/{story_id}/correct", 200, json=corrected())
    response = operator_client.post(CORRECT, form_of(page, do=DO))
    assert response.status_code == 302
    assert len(correct_calls(engine)) == 1


# --- After the correction -------------------------------------------------------------------------


def play_history() -> dict[str, Any]:
    """The play page's history after the correction: turn 3 is variant 2, recorded by the
    correction's operation."""
    history: dict[str, Any] = copy.deepcopy(engine_fixture("m6/history_correct_turn"))
    for item in history["items"]:
        item.update(variant=2, operation_id=CORRECTED_OP)
    history["turns"][0].update(variant=2, notices=[])
    return history


def test_logged_and_redirected_inspector(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, history=play_history())
    engine.respond("POST", "/stories/{story_id}/correct", 200, json=corrected())
    location = operator_client.post(CORRECT, posted(do=NEW_DO))["Location"]
    engine.reset()
    page = operator_client.get(location).content.decode()
    inspector = page.split('<div id="inspector">')[1]
    strip = re.search(r'<section class="world-strip">.*?</section>', inspector, re.S)
    assert strip, "no World strip"
    assert '<ul class="not-undone">' in strip.group(0)
    assert "bank" in strip.group(0)
    card = re.search(r'<article class="notice-card".*?</article>', inspector, re.S)
    assert card, "no notice card"
    assert (
        f'<span class="disabled" aria-disabled="true" title="{escape(NOT_CORRECTABLE_REASON)}">'
        "Fix / Clarify</span>"
    ) in card.group(0)
    # Complement: the redirect's reads are ?turn=3's own (the same with no logged row), and
    # nothing is written.
    after = [(r.method, r.url.path) for r in engine.requests]
    assert correct_calls(engine) == []
    EngineOperation.objects.all().delete()
    engine.reset()
    operator_client.get(location)
    assert after == [(r.method, r.url.path) for r in engine.requests]

"""What a sent turn shows (#48): a finished reply, a queued turn's waiting card and its polls,
and each refusal worded per its code. Every outcome is in the operation log (#44)."""

import re
from typing import Any

import httpx
import pytest
from django.test import Client
from django.urls import reverse

from console.inspect import short_id
from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import (
    HTMX,
    KEY,
    OPERATION,
    QUEUED_OP,
    RESPOND,
    STORY,
    TOBIN,
    TURN_OP,
    answer_page,
    calls,
    error_json,
    operation_json,
    post_turn,
    respond_json,
)

pytestmark = pytest.mark.django_db
HISTORY = f"/stories/{STORY}/history"


def oob(html: str, element_id: str) -> str:
    """The out-of-band element ``element_id`` of an answer ("" when absent)."""
    match = re.search(rf'<div id="{element_id}" hx-swap-oob="true">(.*?)\n</div>', html, re.S)
    return match.group(1) if match else ""


def say_value(html: str) -> str:
    match = re.search(r'<textarea name="say"[^>]*>\n?(.*?)</textarea>', html, re.S)
    assert match is not None
    return match.group(1)


def queue_turn(client: Client, engine: FakeEngine, position: int = 1) -> str:
    engine.respond(
        "POST",
        RESPOND,
        202,
        json={"operation_id": QUEUED_OP, "position": position},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    answer_page(engine)
    return post_turn(client)


def poll(client: Client, **query: str) -> str:
    params = {"viewpoint": f"character:{TOBIN}", "deadline": "45", "position": "1", **query}
    url = reverse("stories:poll", args=[STORY, QUEUED_OP])
    response = client.get(url, params, headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


# --- 200 ----------------------------------------------------------------------------------------


def test_a_finished_turn_shows_the_reply_and_report(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answer_page(engine)
    html = post_turn(operator_client, output_viewpoint=f"character:{TOBIN}")
    main = html.split('<div id="turn-status"')[0]
    assert "Turn 1 (variant 1)" in main
    assert "[stub generation" in main  # the reply
    assert "<h3>Turn report</h3>" in main
    assert "<td>generation</td>" in main
    assert "<dt>Elapsed</dt><dd>47 ms</dd>" in main
    assert "max calls 9, used 1" in main
    assert "No violations on the released text." in main
    assert "<dt>Timeline revision</dt><dd>2</dd>" in main
    assert "<dt>Config revision</dt><dd>0</dd>" in main
    assert f"<code>{KEY}</code>" in main
    assert f"<code>{TURN_OP}</code>" in main
    # The transcript is re-fetched at the reply's viewpoint and swapped out-of-band.
    (history,) = calls(engine, "GET", HISTORY)
    assert history.url.params["viewpoint"] == f"character:{TOBIN}"
    assert "Is the ferry late again?" in oob(html, "transcript")
    # Say/do are cleared; every other field keeps its value; a fresh key.
    form = oob(html, "turn-form")
    assert say_value(form) == ""
    assert f'<option value="character:{TOBIN}" selected>' in form
    assert f'value="{KEY}"' not in form
    (row,) = EngineOperation.objects.all()
    assert row.kind == "respond"
    assert row.outcome == "ok"


ROOK = "01JB2Z8Y5Q3W6K9M1N4P7R0T3D"  # not in the cast: its short id
MEMORY = "01JB2Z8Y5Q3W6K9M1N4P7MEM01"


def warnings_row(html: str) -> str:
    match = re.search(r'<dt>Warnings</dt>\s*<dd class="turn-warnings">(.*?)</dd>', html, re.S)
    return match.group(1) if match else ""


def test_turn_report_warnings_name_characters(operator_client: Client, engine: FakeEngine) -> None:
    """#202: the Warnings row after Degradation names a cast character, short-ids one outside
    it, links the quarantined pin's short id into Retrieval and keeps every warning in order."""
    warnings = [
        {"kind": "primary_not_present", "character_id": TOBIN},
        {"kind": "primary_not_present", "character_id": ROOK},
        {"kind": "pin_quarantined", "memory_id": MEMORY},
        {"kind": "tie_break_state_not_yet"},
        {"kind": "tie_break_state_not_yet"},
    ]
    engine.respond("POST", RESPOND, 200, json=respond_json(warnings=warnings))
    answer_page(engine)
    html = post_turn(operator_client).split('<div id="turn-status"')[0]
    assert html.index("<dt>Degradation</dt>") < html.index("<dt>Warnings</dt>")
    assert html.index("<dt>Warnings</dt>") < html.index("<dt>Guardrail</dt>")
    items = re.findall(r"<li>(.*?)</li>", warnings_row(html), re.S)
    retrieval = reverse("inspect:retrieval", args=[STORY])
    assert items == [
        "Tobin was named but isn&#x27;t in the scene — rendered with nothing perceived",
        f"{short_id(ROOK)} was named but isn&#x27;t in the scene — rendered with nothing perceived",
        f'Pinned memory <a class="warning-memory" href="{retrieval}?memory={MEMORY}">'
        f'<code title="{MEMORY}">{short_id(MEMORY)}</code></a> was quarantined by the output gate',
        "The story&#x27;s state tie-break arrives in Engine M6 — request order applied",
        "The story&#x27;s state tie-break arrives in Engine M6 — request order applied",
    ]
    assert ROOK not in warnings_row(html).replace(f'title="{ROOK}"', "")


def test_a_report_without_warnings_has_no_warnings_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: an empty ``warnings`` list shows no Warnings row."""
    engine.respond("POST", RESPOND, 200, json=respond_json(warnings=[]))
    answer_page(engine)
    html = post_turn(operator_client)
    assert "<h3>Turn report</h3>" in html
    assert "<dt>Warnings</dt>" not in html


def test_an_error_path_report_shows_its_warnings(
    operator_client: Client, engine: FakeEngine
) -> None:
    """A 503's report carries warnings too: short ids there (no labels on the error path)."""
    engine.respond(
        "POST",
        RESPOND,
        503,
        json=error_json(
            "model_unavailable",
            "no model is available for 'generation' (rate_limited)",
            purpose="generation",
            reason="rate_limited",
            model_calls=[],
            elapsed_ms=1200,
            warnings=[{"kind": "primary_not_present", "character_id": TOBIN}],
        ),
    )
    html = post_turn(operator_client)
    assert f"<li>{short_id(TOBIN)} was named but isn&#x27;t in the scene" in warnings_row(html)


def test_a_replay_shows_the_same_turn_once(operator_client: Client, engine: FakeEngine) -> None:
    """A double-click's second post is a replay: the transcript is the Engine's, never appended."""
    engine.respond("POST", RESPOND, 200, json=respond_json(), headers={"Idempotent-Replay": "true"})
    answer_page(engine)
    post_turn(operator_client)
    html = post_turn(operator_client)
    assert oob(html, "transcript").count('<li class="item ') == 2  # one say, one reply


# --- 202 and polling ----------------------------------------------------------------------------


def test_a_queued_turn_waits_then_shows_its_result(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = queue_turn(operator_client, engine)
    status = oob(html, "turn-status")
    assert "Waiting — queued, position 1" in status
    assert 'hx-trigger="load delay:1s"' in status
    assert f"/stories/{STORY}/operations/{QUEUED_OP}/poll/" in status
    assert "<fieldset disabled>" in oob(html, "turn-form")
    assert EngineOperation.objects.get().outcome == "queued"

    committed = operation_json(
        "committed",
        result={"status": 200, "body": respond_json()},
        viewpoint=f"character:{TOBIN}",
        timeline_revision=2,
    )
    engine.respond_sequence(
        "GET",
        OPERATION,
        [
            {"status": 200, "json": operation_json("queued")},
            {"status": 200, "json": operation_json("in_flight")},
            {"status": 200, "json": committed},
        ],
    )
    engine.reset()
    first = poll(operator_client)
    assert "Waiting — queued, position 1" in first
    assert 'hx-trigger="load delay:1s"' in first
    second = poll(operator_client)
    assert "Running…" in second
    assert 'hx-trigger="load delay:1s"' in second
    assert "started=2099-01-01T00%3A00%3A00Z" in second
    done = poll(operator_client)
    assert "hx-trigger" not in done.split('<div id="inspector"')[0]  # polling stopped
    assert "Turn 1 committed." in done
    assert "<h3>Turn report</h3>" in oob(done, "inspector")
    assert "Is the ferry late again?" in oob(done, "transcript")
    form = oob(done, "turn-form")
    assert "<fieldset>" in form
    assert say_value(form) == ""
    for request in calls(engine, "GET", f"/operations/{QUEUED_OP}"):
        assert request.url.params["viewpoint"] == f"character:{TOBIN}"
    row = EngineOperation.objects.get()
    assert row.outcome == "ok"
    assert row.timeline_revision == 2
    assert row.config_revision == 0


def test_a_failed_poll_shows_the_error_and_keeps_the_input(
    operator_client: Client, engine: FakeEngine
) -> None:
    queue_turn(operator_client, engine)
    failed = operation_json(
        "failed",
        result={
            "status": 503,
            "body": error_json(
                "model_unavailable", "no model", purpose="generation", reason="timeout"
            ),
        },
    )
    engine.respond("GET", OPERATION, 200, json=failed)
    done = poll(operator_client)
    assert "Turn failed — see the inspector." in done
    inspector = oob(done, "inspector")
    assert "Model unavailable" in inspector
    assert "model_unavailable" in inspector
    assert say_value(oob(done, "turn-form")) == "Is the ferry late again?"
    assert oob(done, "transcript") == ""
    row = EngineOperation.objects.get()
    assert row.outcome == "error"
    assert row.error_code == "model_unavailable"


def test_an_abandoned_poll_without_a_result_shows_its_status(
    operator_client: Client, engine: FakeEngine
) -> None:
    queue_turn(operator_client, engine)
    engine.respond("GET", OPERATION, 200, json=operation_json("abandoned"))
    done = poll(operator_client)
    inspector = oob(done, "inspector")
    assert "Turn abandoned" in inspector
    assert "<code>abandoned</code>" in inspector
    assert EngineOperation.objects.get().error_code == "abandoned"


def test_a_committed_poll_without_a_result_says_so(
    operator_client: Client, engine: FakeEngine
) -> None:
    queue_turn(operator_client, engine)
    engine.respond("GET", OPERATION, 200, json=operation_json("committed"))
    engine.reset()
    done = poll(operator_client)
    assert "result not available from the Engine" in oob(done, "inspector")
    assert calls(engine, "GET", HISTORY)  # the transcript is still re-fetched


def test_polling_stops_after_the_deadline_plus_30_s(
    operator_client: Client, engine: FakeEngine
) -> None:
    queue_turn(operator_client, engine)
    engine.respond(
        "GET", OPERATION, 200, json=operation_json("in_flight", started_at="2000-01-01T00:00:00Z")
    )
    done = poll(operator_client)
    assert "Still running — refresh to check" in done
    assert "hx-trigger" not in done.split('<div id="turn-form"')[0]


def test_a_queued_operation_has_no_cutoff(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", OPERATION, 200, json=operation_json("queued", started_at="2000-01-01T00:00:00Z")
    )
    answer_page(engine)
    assert "Waiting — queued, position 1" in poll(operator_client)


def test_an_unreachable_engine_keeps_polling(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", OPERATION, httpx.ConnectError("down"))
    html = poll(operator_client)
    assert "Engine unreachable — retrying" in html
    assert 'hx-trigger="load delay:1s"' in html


def test_a_404_while_polling_stops_with_the_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", OPERATION, 404, json=error_json("not_found", "no such operation"))
    answer_page(engine)
    html = poll(operator_client)
    assert "Polling stopped" in html
    assert "not_found" in oob(html, "inspector")
    assert 'hx-trigger="load delay:1s"' not in html


def test_polls_are_never_recorded(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", OPERATION, 200, json=operation_json("in_flight"))
    poll(operator_client)
    assert not EngineOperation.objects.exists()


# --- Refusals -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ({"reason": "in_flight", "operation_id": TURN_OP}, "Story busy — another turn is running"),
        (
            {"reason": "queue_full", "operation_id": TURN_OP, "queue_depth": 5},
            "Story busy — queue full (5 waiting)",
        ),
    ],
)
def test_a_busy_story_says_so(
    operator_client: Client, engine: FakeEngine, details: dict[str, Any], expected: str
) -> None:
    engine.respond(
        "POST",
        RESPOND,
        423,
        json=error_json("story_busy", "busy", **details),
        headers={"Retry-After": "12"},
    )
    html = post_turn(operator_client)
    assert expected in html
    assert f"<code>{TURN_OP}</code>" in html
    assert "<dt>Retry after</dt><dd>12 s</dd>" in html
    assert say_value(oob(html, "turn-form")) == "Is the ferry late again?"


def test_an_input_guardrail_rejection_shows_violations_and_adds_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    rejection = engine_fixture("respond_guardrail_violation")  # the live shape (#130)
    assert rejection["error"]["details"]["model_calls"] == 1
    engine.respond("POST", RESPOND, 422, json=rejection)
    html = post_turn(operator_client, do="something the minors policy rejects")
    main = html.split('<div id="turn-status"')[0]
    assert "Input rejected by a guardrail — no reply was added." in main
    assert "<td><code>minors_no_sexual_content</code></td>" in main
    assert "Minors: no sexual content" in main
    # The model calls the input gate consumed: the Engine sends a count and the time (#130).
    assert "<dt>Input gate</dt><dd>1 model call, 3 ms</dd>" in main
    assert "could not be read" not in main
    # Complement: the transcript is not touched (no history read, no swap).
    assert calls(engine, "GET", HISTORY) == []
    assert oob(html, "transcript") == ""
    assert "something the minors policy rejects" in oob(html, "turn-form")
    assert EngineOperation.objects.get().is_guardrail_rejection is True


def test_a_budget_below_the_minimum_shows_it(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        RESPOND,
        422,
        json=error_json(
            "budget_below_safety_minimum", "too small", minimum_required=3, max_calls=1
        ),
    )
    html = post_turn(operator_client, max_calls="1")
    assert "<dt>Minimum required</dt><dd>3</dd>" in html
    assert "<dt>Max calls asked</dt><dd>1</dd>" in html


def test_model_unavailable_shows_its_details(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        RESPOND,
        503,
        json=error_json(
            "model_unavailable",
            "no model is available for 'generation' (rate_limited)",
            purpose="generation",
            reason="rate_limited",
            provider_status=429,
            attempts=3,
            model_calls=[],
            elapsed_ms=1200,
        ),
        headers={"Retry-After": "30"},
    )
    html = post_turn(operator_client)
    for line in (
        "<dt>Purpose</dt><dd>generation</dd>",
        "<dt>Reason</dt><dd>rate_limited</dd>",
        "<dt>Provider status</dt><dd>429</dd>",
        "<dt>Attempts</dt><dd>3</dd>",
        "<dt>Retry after</dt><dd>30 s</dd>",
    ):
        assert line in html
    assert calls(engine, "GET", HISTORY) == []


def test_an_erased_subject_keeps_the_input(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        RESPOND,
        409,
        json=error_json(
            "erased_subject_referenced", "stale", erasure_id="01JB2Z8Y5Q3W6K9M1N4P7R0ER1"
        ),
    )
    html = post_turn(operator_client, do="looks at the harbour")
    assert "This turn refers to an erased subject — nothing was written" in html
    assert calls(engine, "GET", HISTORY) == []
    form = oob(html, "turn-form")
    assert say_value(form) == "Is the ferry late again?"
    assert "looks at the harbour" in form


def test_a_validation_error_is_placed_beside_its_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        RESPOND,
        422,
        json=error_json(
            "validation_error",
            "invalid",
            errors=[{"field": "body.budget.deadline_s", "message": "above the story's deadline"}],
        ),
    )
    html = post_turn(operator_client, deadline_s="500")
    form = oob(html, "turn-form")
    field = form.split('class="field field-deadline_s"')[1].split('class="field ')[0]
    assert "above the story&#x27;s deadline" in field


def test_an_unreachable_engine_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", RESPOND, httpx.ConnectError("down"))
    html = post_turn(operator_client)
    assert "Engine unreachable — connection failed" in html
    assert EngineOperation.objects.get().outcome == "unreachable"


def test_the_turn_timeout_is_the_deadline_plus_15_s(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from console.engine import client as client_module

    seen: list[float | None] = []
    original = client_module.EngineClient._request

    def spy(self: Any, method: str, path: str, **kwargs: Any) -> Any:
        if method == "POST":
            seen.append(kwargs.get("timeout"))
        return original(self, method, path, **kwargs)

    monkeypatch.setattr(client_module.EngineClient, "_request", spy)
    engine.respond("POST", RESPOND, 200, json=respond_json())
    answer_page(engine)
    post_turn(operator_client, deadline_s="20")
    post_turn(operator_client)  # no deadline: the snapshot's turn_deadline (45 s)
    assert seen == [35.0, 60.0]

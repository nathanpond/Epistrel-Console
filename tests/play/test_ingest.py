"""The play view's ingest tab (#51): event rows mapped to ``POST /stories/{id}/ingest``, the
results table, the transcript re-fetched (never appended), and each refusal placed on its row
or in the tab's panel. Every ingest is in the operation log (#44)."""

import json
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from console.cast import CastMember
from console.forms.ingest import (
    DUPLICATE_ID,
    NEED_EVENT,
    NEED_TEXT,
    ingest_formset,
)
from console.forms.play import NEED_INPUT
from console.ingest import ERASED_EVENT
from console.models import EngineOperation
from console.views.play import INGESTED
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import (
    HTMX,
    KEY,
    MARA,
    OPERATION,
    QUEUED_OP,
    STORY,
    TOBIN,
    answer_page,
    calls,
    error_json,
    operation_json,
    snapshot,
    story_json,
)

pytestmark = pytest.mark.django_db
INGEST = "/stories/{story_id}/ingest"
INGEST_PATH = f"/stories/{STORY}/ingest"
HISTORY = f"/stories/{STORY}/history"
OP1 = "01JB2Z8Y5Q3W6K9M1N4P7R0IN1"
OP2 = "01JB2Z8Y5Q3W6K9M1N4P7R0IN2"
CAST = (CastMember(MARA, "Mara", True), CastMember(TOBIN, "Tobin", False))


# --- helpers -------------------------------------------------------------------------------------


def player(
    say: str = "Morning.", do: str = "", event_id: str = "evt-1", **more: str
) -> dict[str, str]:
    return {
        "kind": "player_turn",
        "actor_character_id": MARA,
        "say": say,
        "do": do,
        "text": "",
        "client_event_id": event_id,
        **more,
    }


def narration(text: str = "Fog rolls in.", event_id: str = "evt-2", **more: str) -> dict[str, str]:
    return {
        "kind": "narration",
        "actor_character_id": MARA,
        "say": "",
        "do": "",
        "text": text,
        "client_event_id": event_id,
        **more,
    }


def ingest_data(*rows: dict[str, str], **fields: Any) -> dict[str, Any]:
    """An ingest POST as the tab's form sends it."""
    data: dict[str, Any] = {
        "idempotency_key": KEY,
        "snapshot": snapshot(),
        "output_viewpoint": "default",
        "ingest-TOTAL_FORMS": str(len(rows)),
        "ingest-INITIAL_FORMS": "0",
        "ingest-MIN_NUM_FORMS": "1",
        "ingest-MAX_NUM_FORMS": "50",
    }
    for index, row in enumerate(rows):
        for name, value in row.items():
            data[f"ingest-{index}-{name}"] = value
    data.update(fields)
    return data


def post_ingest(client: Client, *rows: dict[str, str], htmx: bool = True, **fields: Any) -> str:
    response = client.post(
        reverse("stories:ingest", args=[STORY]),
        ingest_data(*rows, **fields),
        headers=HTMX if htmx else {},
    )
    assert response.status_code == 200
    return response.content.decode()


def ingest_json(*replayed: bool) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("ingest")
    for result, flag in zip(body["results"], replayed, strict=False):
        result["replayed"] = flag
    return body


def answer_ingest(engine: FakeEngine, *, replayed: bool = False) -> None:
    """The Engine answers a replay 200, but the recorded schema declares only 201 for ingest,
    and the fake serves only declared statuses: a replay here is a 201 with every event
    ``replayed`` and ``Idempotent-Replay: true`` (all the Console reads)."""
    headers = {"Idempotent-Replay": "true"} if replayed else None
    engine.respond("POST", INGEST, 201, json=ingest_json(replayed, replayed), headers=headers)
    engine.respond("GET", "/stories/{story_id}/history", 200, json=history())


def history() -> dict[str, Any]:
    found: dict[str, Any] = engine_fixture("get_history_ingested")
    return found


def sent(engine: FakeEngine) -> dict[str, Any]:
    (request,) = calls(engine, "POST", INGEST_PATH)
    body: dict[str, Any] = json.loads(request.content)
    return body


def oob(html: str, element_id: str) -> str:
    match = re.search(rf'<div id="{element_id}" hx-swap-oob="true">(.*?)\n</div>', html, re.S)
    return match.group(1) if match else ""


def row(html: str, index: int) -> str:
    """The rendered row ``index`` of the ingest tab."""
    match = re.search(
        rf'<fieldset class="event" id="ingest-row-{index}">(.*?)</fieldset>', html, re.S
    )
    assert match is not None, f"no row {index}"
    return match.group(1)


def ids(html: str) -> list[str]:
    return re.findall(r'name="ingest-\d+-client_event_id"[^>]*value="([^"]*)"', html)


# --- the body mapping (form) -----------------------------------------------------------------


def test_a_narration_omits_actor_say_and_do_and_a_player_turn_omits_text() -> None:
    data = ingest_data(
        player(say="  Morning.\r\nAll well?  ", do="", text="ignored"),
        narration(text="Fog rolls in.", say="ignored", do="ignored"),
    )
    formset = ingest_formset(CAST, data)
    assert formset.is_valid(), formset.errors
    assert formset.events() == [
        {
            "client_event_id": "evt-1",
            "kind": "player_turn",
            "actor_character_id": MARA,
            "say": "Morning.\nAll well?",
        },
        {"client_event_id": "evt-2", "kind": "narration", "text": "Fog rolls in."},
    ]


def test_a_row_with_only_kind_and_id_is_dropped() -> None:
    data = ingest_data(player(say="", event_id="blank"), narration())
    formset = ingest_formset(CAST, data)
    assert formset.is_valid(), formset.errors
    assert [e["client_event_id"] for e in formset.events()] == ["evt-2"]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ((player(say="", do="", text="only text"),), NEED_INPUT),
        ((narration(text="", say="said"),), NEED_TEXT),
        ((player(event_id="evt-1"), narration(event_id="evt-1")), DUPLICATE_ID),
        ((player(event_id="has space"),), "Use only letters, digits"),
        ((player(event_id="x" * 256),), "at most 255 characters"),
        ((player(say="x" * 8001),), "at most 8000 characters"),
        ((player(say=""),), NEED_EVENT),
    ],
)
def test_the_form_refuses_bad_rows_without_an_engine_call(
    operator_client: Client, engine: FakeEngine, rows: tuple[dict[str, str], ...], message: str
) -> None:
    html = post_ingest(operator_client, *rows)
    assert message in html
    assert not calls(engine, "POST")


def test_a_51st_row_is_refused_without_an_engine_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    rows = [player(event_id=f"evt-{i}") for i in range(51)]
    html = post_ingest(operator_client, *rows)
    assert "Please submit at most 50 forms." in html
    assert not calls(engine, "POST")


# --- a recorded batch ------------------------------------------------------------------------


def test_a_batch_is_sent_shows_its_results_and_refetches_the_transcript(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ingest(engine)
    html = post_ingest(operator_client, player(), narration())
    body = sent(engine)
    assert body == {
        "events": [
            {
                "client_event_id": "evt-1",
                "kind": "player_turn",
                "actor_character_id": MARA,
                "say": "Morning.",
            },
            {"client_event_id": "evt-2", "kind": "narration", "text": "Fog rolls in."},
        ]
    }
    (request,) = calls(engine, "POST", INGEST_PATH)
    assert request.headers["Idempotency-Key"] == KEY
    assert "Prefer" not in request.headers
    assert request.extensions["timeout"]["read"] == 45.0 + 15.0 + 2  # deadline + 15 s + 1 s/event
    tab = html.split('<div id="transcript"')[0]
    assert "<h3>Results</h3>" in tab
    first = "<td><code>evt-1</code></td><td>player turn</td><td>Mara</td>"
    assert f"{first}<td><code>{OP1}</code></td><td>1</td>" in tab
    assert (
        f"<td><code>evt-2</code></td><td>narration</td><td>—</td><td><code>{OP2}</code></td><td>2</td>"
        in tab
    )
    assert tab.count('<td class="replayed">no</td>') == 2
    assert "Timeline revision 3. Input gate: 0 model calls, 71 ms." in tab
    # The same rows, the same ids, a fresh key, and "New batch".
    assert ids(tab) == ["evt-1", "evt-2"]
    assert f'value="{KEY}"' not in tab
    assert "New batch" in tab
    # The transcript is re-fetched at the turn form's viewpoint (the story default: Mara).
    (fetched,) = calls(engine, "GET", HISTORY)
    assert fetched.url.params["viewpoint"] == f"character:{MARA}"
    transcript = oob(html, "transcript")
    assert "Morning." in transcript
    assert "Fog rolls in." in transcript
    assert transcript.count("ingested</small>") == 2


def test_the_ingest_is_recorded_in_the_operation_log(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ingest(engine)
    post_ingest(operator_client, player(), narration())
    (logged,) = EngineOperation.objects.all()
    assert logged.kind == "ingest"
    assert logged.outcome == "ok"
    assert logged.http_status == 201
    assert logged.engine_operation_id == OP1
    assert logged.story_id == STORY
    assert logged.replayed is False


def test_a_replayed_batch_shows_replayed_and_appends_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Resending unchanged rows: every row ``replayed: yes``, and the transcript is the Engine's
    history again, not the old one plus the batch (complement: it is re-fetched each time)."""
    answer_ingest(engine)
    first = post_ingest(operator_client, player(), narration())
    answer_ingest(engine, replayed=True)
    again = post_ingest(operator_client, player(), narration())
    tab = again.split('<div id="transcript"')[0]
    assert tab.count('<td class="replayed">yes</td>') == 2
    assert len(calls(engine, "GET", HISTORY)) == 2  # re-fetched after both submits
    assert oob(again, "transcript") == oob(first, "transcript")
    assert oob(again, "transcript").count('<li class="item ') == 2
    replay = EngineOperation.objects.order_by("-pk").first()
    assert replay is not None
    assert replay.replayed is True


def test_without_htmx_the_page_is_rendered_with_the_results(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ingest(engine)
    answer_page(engine, history=history())
    html = post_ingest(operator_client, player(), narration(), htmx=False)
    assert "<h3>Results</h3>" in html
    assert 'aria-current="page">Ingest</a>' in html
    assert "<h2>Inspector</h2>" in html  # the whole play page
    assert "Fog rolls in." in html


# --- refusals --------------------------------------------------------------------------------


def test_a_client_event_conflict_names_the_conflicting_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    conflict = error_json(
        "client_event_conflict",
        "a client_event_id was already ingested with a different payload",
        client_event_ids=[{"client_event_id": "evt-2", "index": 1}],
    )
    engine.respond("POST", INGEST, 409, json=conflict)
    html = post_ingest(operator_client, player(), narration(text="Fog lifts."))
    assert "evt-2 was already ingested with a different payload" in row(html, 1)
    assert "already ingested" not in row(html, 0)
    assert "HTTP 409 client_event_conflict" in html
    assert ids(html) == ["evt-1", "evt-2"]  # rows kept
    assert not calls(engine, "GET", HISTORY)


def test_a_conflict_by_index_alone_lands_on_that_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", INGEST, 409, json=error_json("client_event_conflict", index=0))
    html = post_ingest(operator_client, player(), narration())
    assert "evt-1 was already ingested" in row(html, 0)


def test_a_conflict_naming_no_row_goes_above_the_rows(
    operator_client: Client, engine: FakeEngine
) -> None:
    conflict = error_json(
        "client_event_conflict", client_event_ids=[{"client_event_id": "elsewhere"}]
    )
    engine.respond("POST", INGEST, 409, json=conflict)
    html = post_ingest(operator_client, player())
    assert '<ul class="errorlist nonform batch"><li>client_event_conflict: elsewhere' in html


def test_a_422_field_path_lands_beside_the_field_of_the_sent_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    """``events[1]`` is the second row sent: a blank row before it was dropped."""
    failure = error_json(
        "validation_error",
        "String should have at most 8000 characters",
        field="body.events[1].say",
        errors=[{"field": "body.events[1].say", "message": "too long for the Engine"}],
    )
    engine.respond("POST", INGEST, 422, json=failure)
    html = post_ingest(
        operator_client, player(), player(say="", event_id="blank"), player(event_id="evt-3")
    )
    assert len(sent(engine)["events"]) == 2
    field = row(html, 2).split('class="field field-say"')[1].split("</div>")[0]
    assert "too long for the Engine" in field
    assert "too long" not in row(html, 0)


def test_an_unknown_actor_lands_on_that_rows_actor(
    operator_client: Client, engine: FakeEngine
) -> None:
    failure = error_json(
        "validation_error",
        "the request names characters that are not in the story",
        unknown_actor={"events[0]": MARA},
        field="body",
    )
    engine.respond("POST", INGEST, 422, json=failure)
    html = post_ingest(operator_client, player())
    actor = row(html, 0).split('class="field field-actor_character_id"')[1].split("</div>")[0]
    assert f"Not a character of this story: {MARA}." in actor


def test_an_erased_subject_with_an_event_path_lands_on_that_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    erased = error_json(
        "erased_subject_referenced", "erased", erasure_id="E1", match_count=1, locator="events[1]"
    )
    engine.respond("POST", INGEST, 409, json=erased)
    html = post_ingest(operator_client, player(), narration())
    assert ERASED_EVENT in row(html, 1)
    assert ERASED_EVENT not in row(html, 0)
    assert ids(html) == ["evt-1", "evt-2"]
    assert not calls(engine, "GET", HISTORY)  # nothing appended, nothing re-fetched
    assert 'hx-swap-oob="true"' not in html


def test_an_erased_subject_without_a_path_goes_to_the_tab_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    erased = error_json("erased_subject_referenced", "erased", erasure_id="E1", match_count=2)
    engine.respond("POST", INGEST, 409, json=erased)
    html = post_ingest(operator_client, player(), narration())
    outcome = html.split('<div class="ingest-outcome">')[1].split("<form")[0]
    assert ERASED_EVENT in outcome
    assert "<dt>Erasure</dt><dd>E1</dd>" in outcome
    assert ERASED_EVENT not in row(html, 0) + row(html, 1)
    assert ids(html) == ["evt-1", "evt-2"]
    assert not calls(engine, "GET", HISTORY)


def guardrail(*violations: dict[str, Any]) -> dict[str, Any]:
    return error_json(
        "guardrail_violation",
        "the input violates a guardrail policy",
        violations=list(violations),
        model_calls=[],
        elapsed_ms=3,
    )


def test_a_located_guardrail_violation_lands_on_its_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    violation = {
        "policy_id": "P1",
        "name": "no-dragons",
        "message": "No dragons.",
        "locator": "events[1]",
    }
    engine.respond("POST", INGEST, 422, json=guardrail(violation))
    html = post_ingest(operator_client, player(), narration(text="A dragon lands."))
    assert "Guardrail no-dragons: No dragons." in row(html, 1)
    assert "Input rejected by a guardrail — nothing was written." in html
    assert "<h3>Turn report</h3>" in html  # the gate's model calls (shared partial)


def test_an_unlocated_guardrail_violation_is_listed_in_the_tab(
    operator_client: Client, engine: FakeEngine
) -> None:
    violation = {"policy_id": "P1", "name": "no-dragons", "message": "No dragons."}
    engine.respond("POST", INGEST, 422, json=guardrail(violation))
    html = post_ingest(operator_client, player(), narration(text="A dragon lands."))
    outcome = html.split('<div class="ingest-outcome">')[1].split("<form")[0]
    assert "<td>no-dragons</td><td>No dragons.</td>" in outcome
    assert "Guardrail no-dragons" not in row(html, 0) + row(html, 1)


def test_a_busy_story_uses_the_turn_wording(operator_client: Client, engine: FakeEngine) -> None:
    busy = error_json("story_busy", "busy", reason="in_flight", operation_id=QUEUED_OP)
    engine.respond("POST", INGEST, 423, json=busy, headers={"Retry-After": "2"})
    html = post_ingest(operator_client, player())
    assert "Story busy — another turn is running" in html
    assert "<dt>Retry after</dt><dd>2 s</dd>" in html


def test_a_budget_below_the_safety_minimum_shows_the_minimum(
    operator_client: Client, engine: FakeEngine
) -> None:
    refusal = error_json("budget_below_safety_minimum", "too low", minimum_required=3)
    engine.respond("POST", INGEST, 422, json=refusal)
    html = post_ingest(operator_client, player())
    assert "<dt>Minimum required</dt><dd>3</dd>" in html


def test_another_refusal_renders_the_error_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", INGEST, 409, json=error_json("story_archived", "the story is archived"))
    html = post_ingest(operator_client, player())
    assert "Engine error 409 story_archived" in html
    assert ids(html) == ["evt-1"]


# --- a queued batch --------------------------------------------------------------------------


def test_a_queued_batch_shows_a_waiting_card_that_polls_to_the_end(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        INGEST,
        202,
        json={"operation_id": QUEUED_OP, "position": 1},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    html = post_ingest(operator_client, player(), narration())
    assert "Ingest queued" in html
    assert "Waiting — queued, position 1" in oob(html, "turn-status")
    committed = operation_json(
        "committed", result={"status": 201, "body": ingest_json()}, timeline_revision=3
    )
    engine.respond("GET", OPERATION, 200, json=committed)
    answer_page(engine, history=history())
    polled = operator_client.get(
        reverse("stories:poll", args=[STORY, QUEUED_OP]),
        {"viewpoint": f"character:{MARA}", "deadline": "45"},
        headers=HTMX,
    ).content.decode()
    assert "Ingest recorded." in polled
    assert "2 event(s) recorded." in polled
    assert "Fog rolls in." in oob(polled, "transcript")


# --- rows and tabs ---------------------------------------------------------------------------


def test_add_row_gives_a_fresh_id_and_clears_results(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post_ingest(operator_client, player(), add="1")
    assert len(ids(html)) == 2
    assert ids(html)[0] == "evt-1"
    assert re.fullmatch(r"[0-9A-Z]{26}", ids(html)[1])
    assert '<option value="player_turn" selected>' in row(html, 1)
    assert f'<option value="{MARA}" selected>' in row(html, 1)
    assert "<h3>Results</h3>" not in html
    assert not engine.requests


def test_remove_row_discards_that_row(operator_client: Client, engine: FakeEngine) -> None:
    html = post_ingest(operator_client, player(), narration(), remove="0")
    assert ids(html) == ["evt-2"]
    assert "Remove event" not in html  # the last row stays
    assert not engine.requests


def test_add_row_is_not_offered_at_50_rows(operator_client: Client, engine: FakeEngine) -> None:
    rows = [player(event_id=f"evt-{i}") for i in range(49)]
    html = post_ingest(operator_client, *rows, add="1")
    assert len(ids(html)) == 50
    assert "Add row" not in html
    again = post_ingest(operator_client, *[player(event_id=f"e{i}") for i in range(50)], add="1")
    assert len(ids(again)) == 50


def test_the_ingest_tab_starts_with_one_player_turn_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/", {"tab": "ingest"}).content.decode()
    assert 'aria-current="page">Ingest</a>' in html
    assert len(ids(html)) == 1
    assert re.fullmatch(r"[0-9A-Z]{26}", ids(html)[0])
    assert '<option value="player_turn" selected>' in html
    assert f'<option value="{MARA}" selected>' in html
    assert "Actor (player turn only)" in html
    assert "Text (narration only)" in html
    assert 'name="say"' not in html  # the turn form is not shown


def test_a_tab_link_swaps_only_the_form_area_without_an_engine_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    params = {"tab": "ingest", "snapshot": snapshot(), "output_viewpoint": f"character:{TOBIN}"}
    html = operator_client.get(f"/stories/{STORY}/", params, headers=HTMX).content.decode()
    assert html.lstrip().startswith('<div id="play-form-area">')
    assert f'name="output_viewpoint" value="character:{TOBIN}"' in html
    back = {"tab": "turn", "snapshot": snapshot(), "output_viewpoint": f"character:{TOBIN}"}
    turn = operator_client.get(f"/stories/{STORY}/", back, headers=HTMX).content.decode()
    assert f'<option value="character:{TOBIN}" selected>' in turn
    assert not engine.requests


def test_an_archived_story_disables_the_ingest_tab(
    operator_client: Client, engine: FakeEngine
) -> None:
    archived = story_json(status="archived", archived_at="2026-10-24T22:00:00Z")
    answer_page(engine, story=archived)
    html = operator_client.get(f"/stories/{STORY}/", {"tab": "ingest"}).content.decode()
    assert "Archived — unarchive to ingest" in html
    assert "<fieldset disabled>" in html


def test_an_ingested_turn_has_no_report_even_though_its_log_row_has_model_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ingest(engine)
    post_ingest(operator_client, player(), narration())
    url = reverse("stories:inspector", args=[STORY])
    html = operator_client.get(url, {"operation": OP1, "origin": "ingested"}, headers=HTMX)
    assert INGESTED in html.content.decode()
    answer_page(engine, history=history())
    page = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "Select a turn to see its report." in page  # not the ingest's gate as a "report"

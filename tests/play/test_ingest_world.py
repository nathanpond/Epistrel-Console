"""The ingest tab's ``state_change`` and ``outcome`` rows (#260): their body mapping, local
validation (per kind, ``resulting_values``' bounds, outcomes in a batch of their own), D1's
``states`` gate on a State change, where the Engine's new refusals land, and the results' What
column. The story is #232's ``m6/story.json`` cast (Mara the player, Alice, Bob) under #239's
Structured v3 (``structured_v3()``) or an M6 Lite (``lite_m6()``)."""

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import escape

from console.cast import CastMember
from console.forms.ingest import (
    BOTH_CHANGE,
    BOTH_IDS,
    ID_INVALID,
    MIXED_OUTCOMES,
    NEED_OUTCOME,
    NEED_REASON,
    NEED_STATE,
    NO_CHANGE,
    NO_ID,
    TOO_MANY_VALUES,
    VALUE_INVALID,
    VALUE_NAME_INVALID,
    ingest_formset,
)
from console.ingest import EFFECT_NOT_FOUND
from console.inspect import STATES, module_gate, unavailable_for
from console.templatetags.inspect_tags import code_spans
from console.world import state_not_found_text
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import lite_m6, structured_v3
from tests.play.helpers import KEY, calls, error_json, ingest_data, post_ingest, snapshot

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
BOB = "01JM6CH0000000000000000003"
OUTBOX = "01JM6BX0000000000000000011"
OUTBOX_2 = "01JM6BX0000000000000000012"
EFFECT = "01JM6BX0000000000000000014"
OP_1 = "01JM6QP0000000000000000260"
OP_2 = "01JM6QP0000000000000000261"
CAST = (
    CastMember(MARA, "Mara", True),
    CastMember(ALICE, "Alice", False),
    CastMember(BOB, "Bob", False),
)
INGEST = "/stories/{story_id}/ingest"
INGEST_PATH = f"/stories/{STORY}/ingest"
STATE_UNPERMITTED_TEXT = "This State's authorization doesn't admit the consumer as a source"

EMPTY_ROW = {
    "actor_character_id": "",
    "say": "",
    "do": "",
    "text": "",
    "state": "",
    "scope_key": "",
    "value": "",
    "delta": "",
    "reason": "",
    "effect_id": "",
    "outbox_id": "",
    "outcome": "",
    "resulting_values": "",
}


# --- helpers -------------------------------------------------------------------------------------


def m6_story() -> dict[str, Any]:
    story: dict[str, Any] = engine_fixture("m6/story")
    return story


def m6_snapshot(effective: dict[str, Any] | None = None) -> str:
    return snapshot(story=m6_story(), effective=effective or structured_v3())


def states_off() -> dict[str, Any]:
    return structured_v3(states__level="off")


def state_row(
    state: str = "gold",
    delta: str = "5",
    reason: str = "found a coin",
    event_id: str = "evt-s",
    **more: str,
) -> dict[str, str]:
    return {
        **EMPTY_ROW,
        "kind": "state_change",
        "actor_character_id": MARA,
        "state": state,
        "delta": delta,
        "reason": reason,
        "client_event_id": event_id,
        **more,
    }


def outcome_row(
    outbox_id: str = OUTBOX,
    outcome: str = "confirmed",
    resulting_values: str = '{"balance": 4500}',
    event_id: str = "evt-o",
    **more: str,
) -> dict[str, str]:
    return {
        **EMPTY_ROW,
        "kind": "outcome",
        "actor_character_id": MARA,
        "outbox_id": outbox_id,
        "outcome": outcome,
        "resulting_values": resulting_values,
        "client_event_id": event_id,
        **more,
    }


def narration_row(event_id: str = "evt-n", **more: str) -> dict[str, str]:
    return {
        **EMPTY_ROW,
        "kind": "narration",
        "text": "Fog rolls in.",
        "client_event_id": event_id,
        **more,
    }


def player_row(event_id: str = "evt-p", **more: str) -> dict[str, str]:
    return {
        **EMPTY_ROW,
        "kind": "player_turn",
        "actor_character_id": MARA,
        "say": "Morning.",
        "client_event_id": event_id,
        **more,
    }


def post(client: Client, *rows: dict[str, str], effective: dict[str, Any] | None = None) -> str:
    return post_ingest(client, *rows, story=STORY, snapshot=m6_snapshot(effective))


def events(*rows: dict[str, str]) -> list[dict[str, Any]]:
    formset = ingest_formset(CAST, ingest_data(*rows))
    assert formset.is_valid(), (formset.errors, formset.non_form_errors())
    return formset.events()


def answer(engine: FakeEngine, *results: tuple[str, str, int]) -> None:
    """An ingest answer, one result per ``(client event id, operation id, turn)``, and the
    transcript re-fetch after it."""
    body = {
        "timeline_revision": 7,
        "idempotency_key": KEY,
        "results": [
            {"client_event_id": cid, "operation_id": op, "turn": turn, "replayed": False}
            for cid, op, turn in results
        ],
        "model_calls": [],
        "elapsed_ms": 5,
    }
    engine.respond("POST", INGEST, 201, json=body)
    engine.respond("GET", "/stories/{story_id}/history", 200, json=engine_fixture("get_history"))


def sent_events(engine: FakeEngine) -> list[dict[str, Any]]:
    (request,) = calls(engine, "POST", INGEST_PATH)
    found: list[dict[str, Any]] = json.loads(request.content)["events"]
    return found


def no_ingest(engine: FakeEngine) -> bool:
    return not calls(engine, "POST", INGEST_PATH)


def row(html: str, index: int) -> str:
    """Row ``index`` of the ingest tab, nested kind fieldsets included."""
    start = html.index(f'id="ingest-row-{index}"')
    following = html.find('id="ingest-row-', start + 1)
    end = following if following != -1 else html.index('<button type="submit">Ingest</button>')
    return html[start:end]


def field(row_html: str, name: str) -> str:
    """One field's block (label, input, errors) within a row."""
    return row_html.split(f'class="field field-{name}"')[1].split("</div>")[0]


def batch_errors(html: str) -> str:
    return html.split('<ul class="errorlist nonform batch">')[1].split("</ul>")[0]


def results(html: str) -> str:
    return html.split('<table class="ingest-results">')[1].split("</table>")[0]


# --- the body mapping ----------------------------------------------------------------------------


def test_a_state_change_body() -> None:
    filled = state_row(
        say="ignored", text="ignored", outcome="confirmed", outbox_id=OUTBOX, effect_id=""
    )
    (event,) = events(filled)
    assert event == {
        "client_event_id": "evt-s",
        "kind": "state_change",
        "state": "gold",
        "delta": 5,
        "reason": "found a coin",
    }
    for key in ("actor_character_id", "say", "text", "value", "outbox_id", "outcome"):
        assert key not in event
    (scoped,) = events(state_row(scope_key=ALICE))
    assert scoped["scope_key"] == ALICE
    (text,) = events(state_row(delta="", value='"12"'))
    assert text["value"] == "12"
    assert "delta" not in text


def test_an_outcome_body() -> None:
    filled = outcome_row(say="ignored", state="gold", reason="ignored", delta="5")
    (event,) = events(filled)
    assert event == {
        "client_event_id": "evt-o",
        "kind": "outcome",
        "outbox_id": OUTBOX,
        "outcome": "confirmed",
        "resulting_values": {"balance": 4500},
    }
    for key in ("state", "reason", "delta", "value", "scope_key", "say", "text", "effect_id"):
        assert key not in event
    (lower,) = events(outcome_row(outbox_id=f"  {OUTBOX.lower()} ", resulting_values="{}"))
    assert lower["outbox_id"] == OUTBOX
    assert "resulting_values" not in lower  # {} counts as not given
    (by_effect,) = events(outcome_row(outbox_id="", effect_id=EFFECT, outcome="unknown"))
    assert by_effect["effect_id"] == EFFECT
    assert "outbox_id" not in by_effect


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (state_row(state=""), NEED_STATE),
        (state_row(reason=""), NEED_REASON),
        (state_row(value="3"), BOTH_CHANGE),
        (state_row(delta=""), NO_CHANGE),
        (outcome_row(effect_id=EFFECT), BOTH_IDS),
        (outcome_row(outbox_id=""), NO_ID),
        (outcome_row(outcome=""), NEED_OUTCOME),
        (outcome_row(outbox_id="not-an-id"), ID_INVALID),
        (
            outcome_row(resulting_values=json.dumps({f"v{i}": i for i in range(21)})),
            TOO_MANY_VALUES,
        ),
        (outcome_row(resulting_values='{"balance": {"cents": 1}}'), VALUE_INVALID),
        (outcome_row(resulting_values=json.dumps({"note": "x" * 201})), VALUE_INVALID),
        (outcome_row(resulting_values='{"gone": null}'), VALUE_INVALID),
        (outcome_row(resulting_values='{"big": 1e999}'), VALUE_INVALID),
        (outcome_row(resulting_values=json.dumps({"x" * 81: 1})), VALUE_NAME_INVALID),
        (outcome_row(resulting_values="[]"), "Enter a JSON object"),
    ],
)
def test_kind_rules_refuse_without_a_call(
    operator_client: Client, engine: FakeEngine, bad: dict[str, str], message: str
) -> None:
    html = post(operator_client, bad)
    assert escape(message) in row(html, 0)
    assert no_ingest(engine)


def test_outcomes_in_a_mixed_batch_are_refused(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, outcome_row(), narration_row())
    assert escape(MIXED_OUTCOMES) in html.split('<fieldset class="event"')[0]
    assert no_ingest(engine)
    # Complement: two outcomes alone are sent …
    answer(engine, ("evt-o", OP_1, 0), ("evt-o2", OP_2, 0))
    post(operator_client, outcome_row(), outcome_row(outbox_id=OUTBOX_2, event_id="evt-o2"))
    assert [e["kind"] for e in sent_events(engine)] == ["outcome", "outcome"]
    # … and only outcomes need their own batch.
    engine.requests.clear()
    answer(engine, ("evt-s", OP_1, 5), ("evt-p", OP_2, 6))
    post(operator_client, state_row(), player_row())
    assert [e["kind"] for e in sent_events(engine)] == ["state_change", "player_turn"]


def test_a_state_change_row_alone_is_not_blank(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, ("evt-s", OP_1, 5))
    only = {
        "kind": "state_change",
        "client_event_id": "evt-s",
        "state": "gold",
        "delta": "5",
        "reason": "found a coin",
    }
    post(operator_client, only)
    assert sent_events(engine) == [
        {
            "client_event_id": "evt-s",
            "kind": "state_change",
            "state": "gold",
            "delta": 5,
            "reason": "found a coin",
        }
    ]
    # Complement: a row with only its kind and id (any kind) is dropped, actor and character
    # included.
    blank = [
        {**EMPTY_ROW, "kind": kind, "client_event_id": f"blank-{kind}"}
        for kind in ("player_turn", "narration", "state_change", "outcome")
    ]
    blank[2]["scope_key"] = ALICE
    blank[3]["actor_character_id"] = MARA
    assert [e["client_event_id"] for e in events(*blank, narration_row())] == ["evt-n"]
    # A row whose only input is "{}" is not blank: it is validated, not silently dropped.
    formset = ingest_formset(
        CAST, ingest_data({**blank[3], "resulting_values": "{}"}, narration_row())
    )
    assert not formset.is_valid()
    assert NO_ID in str(formset.forms[0].non_field_errors())


def test_state_change_gated_when_states_off(operator_client: Client, engine: FakeEngine) -> None:
    gate = module_gate(STATES, {"pipeline": "structured", "states": {"level": "off"}})
    assert gate is not None
    reason = code_spans(gate.reason)
    html = post(operator_client, player_row(), state_row(), effective=states_off())
    assert reason in field(row(html, 1), "kind").split("</small>")[-1]  # the error on the Kind
    assert no_ingest(engine)
    assert f'value="{KEY}"' in html  # D8: nothing was sent, the posted key is kept
    # The tab shows the reason beside each row's Kind.
    params = {"tab": "ingest", "snapshot": m6_snapshot(states_off()), "output_viewpoint": ""}
    tab = operator_client.get(f"/stories/{STORY}/", params, headers={"HX-Request": "true"})
    shown = tab.content.decode()
    assert f'<small class="gate-reason">{reason}</small>' in field(row(shown, 0), "kind")
    # Complement: outcomes are never gated, and player turns are sent.
    answer(engine, ("evt-o", OP_1, 0))
    post(operator_client, outcome_row(), effective=states_off())
    assert [e["kind"] for e in sent_events(engine)] == ["outcome"]
    engine.requests.clear()
    answer(engine, ("evt-p", OP_1, 4))
    post(operator_client, player_row(), effective=states_off())
    assert [e["kind"] for e in sent_events(engine)] == ["player_turn"]


def test_the_gate_reads_the_snapshots_states_level(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, ("evt-s", OP_1, 5))
    post(operator_client, state_row(), effective=lite_m6(**{"states.level": "prompt_only"}))
    assert [e["kind"] for e in sent_events(engine)] == ["state_change"]
    engine.requests.clear()
    html = post(operator_client, state_row(), effective=lite_m6())
    assert no_ingest(engine)
    assert "gate-reason" in field(row(html, 0), "kind")


# --- where the Engine's refusals land ------------------------------------------------------------


def test_effect_not_found_lands_on_the_rows_id_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    refusal = error_json("effect_not_found", "unknown", locator="events[1]", outbox_id=OUTBOX_2)
    engine.respond("POST", INGEST, 422, json=refusal)
    html = post(operator_client, outcome_row(), outcome_row(outbox_id=OUTBOX_2, event_id="evt-o2"))
    expected = escape(EFFECT_NOT_FOUND.format(id=OUTBOX_2))
    assert expected in field(row(html, 1), "outbox_id")
    assert expected not in row(html, 0)
    assert f'value="{KEY}"' not in html  # an answered refusal: a fresh key
    # A locator naming no sent row goes above the rows.
    unplaced = error_json("effect_not_found", "unknown", locator="events[5]", effect_id=EFFECT)
    engine.respond("POST", INGEST, 422, json=unplaced)
    above = post(operator_client, outcome_row())
    assert escape(EFFECT_NOT_FOUND.format(id=EFFECT)) in batch_errors(above)


def test_a_state_refusal_lands_on_the_matching_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", INGEST, 422, json=error_json("state_unpermitted", state="gold"))
    html = post(operator_client, state_row(state="silver", event_id="s1"), state_row())
    assert escape(STATE_UNPERMITTED_TEXT) in field(row(html, 1), "state")
    assert escape(STATE_UNPERMITTED_TEXT) not in row(html, 0)
    # Complement: two "gold" rows are ambiguous → above the rows.
    two = post(operator_client, state_row(event_id="g1"), state_row(event_id="g2"))
    assert escape(STATE_UNPERMITTED_TEXT) in batch_errors(two)
    assert escape(STATE_UNPERMITTED_TEXT) not in row(two, 0) + row(two, 1)
    # An undeclared State: D9's STATE_NOT_FOUND under the row naming it.
    engine.respond("POST", INGEST, 404, json=error_json("not_found", state="silver"))
    missing = post(operator_client, state_row(), state_row(state="silver", event_id="s1"))
    assert escape(state_not_found_text("silver")) in field(row(missing, 1), "state")
    assert "This story has no State “silver”" in missing


def test_a_commit_time_state_validation_error_lands_on_the_one_state_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    refusal = error_json(
        "validation_error", "gold is story-scoped", field="scope_key", reason="story_scoped"
    )
    engine.respond("POST", INGEST, 422, json=refusal)
    html = post(operator_client, state_row(scope_key=ALICE))
    assert "gold is story-scoped" in field(row(html, 0), "scope_key")


def test_module_off_goes_above_the_rows(operator_client: Client, engine: FakeEngine) -> None:
    """A stale snapshot: the gate passed (States on), the Engine refused."""
    engine.respond("POST", INGEST, 409, json=error_json("module_off", module="states"))
    html = post(operator_client, state_row())
    effective = {"pipeline": "structured", "states": {"level": "on"}}
    reason = code_spans(unavailable_for(STATES, effective).reason)
    assert reason in batch_errors(html)
    config = reverse("stories:config", args=[STORY])
    assert f'<p class="batch-link"><a href="{config}">Config</a></p>' in html
    assert reason not in row(html, 0)


def test_the_engines_mixed_outcomes_refusal_goes_above_the_rows(
    operator_client: Client, engine: FakeEngine
) -> None:
    refusal = error_json("validation_error", "mixed", field="events", reason="mixed_outcomes")
    engine.respond("POST", INGEST, 422, json=refusal)
    html = post(operator_client, outcome_row())
    assert escape(MIXED_OUTCOMES) in batch_errors(html)


# --- the results ---------------------------------------------------------------------------------


def test_results_show_what_was_ingested(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, ("evt-o", OP_1, 0), ("evt-e", OP_2, 0))
    html = post(
        operator_client,
        outcome_row(),
        outcome_row(outbox_id="", effect_id=EFFECT, outcome="failed", event_id="evt-e"),
    )
    table = results(html)
    link = reverse("inspect:reconciliation", args=[STORY]) + f"#outbox-{OUTBOX}"
    assert (
        f'<td class="what"><a href="{link}"><code class="outbox-id">{OUTBOX}</code></a>'
        " → Confirmed</td>" in table
    )
    assert f'<td class="what"><code class="outbox-id">{EFFECT}</code> → Failed</td>' in table
    assert f"<td><code>{OP_1}</code></td><td>—</td>" in table  # an outcome has no turn
    assert table.count("<td>outcome</td>") == 2
    assert "Every outbox row is listed in " in html  # E17's notice under the results (#325)
    assert "there is no list of every row yet" not in html
    # A state change: "<state> <delta>", its turn as the Engine gives it.
    engine.requests.clear()
    answer(engine, ("evt-s", OP_1, 5), ("evt-v", OP_2, 5))
    again = post(
        operator_client,
        state_row(),
        state_row(state="mood", delta="", value='"calm"', scope_key=ALICE, event_id="evt-v"),
    )
    table = results(again)
    assert f'<td class="what">gold +5</td><td><code>{OP_1}</code></td><td>5</td>' in table
    assert '<td class="what">mood &quot;calm&quot; · Alice</td>' in table
    assert "<td>state change</td>" in table
    assert "Every outbox row is listed in " not in again


def test_the_tab_groups_each_rows_fields_by_kind(
    operator_client: Client, engine: FakeEngine
) -> None:
    params = {"tab": "ingest", "snapshot": m6_snapshot(), "output_viewpoint": ""}
    html = operator_client.get(
        f"/stories/{STORY}/", params, headers={"HX-Request": "true"}
    ).content.decode()
    legends = [
        part.split("</legend>")[0]
        for part in row(html, 0).split('<fieldset class="kind-fields">')[1:]
    ]
    assert legends == [
        "\n          <legend>Player turn",
        "\n          <legend>Narration",
        "\n          <legend>State change",
        "\n          <legend>Outcome",
    ]
    assert "Record player turns, narration, State changes and external outcomes" in html
    assert '<option value="state_change">State change</option>' in html
    assert '<option value="outcome">Outcome</option>' in html
    assert '<option value="" selected>— (a story State)</option>' in html
    assert "State (state change only)" in html
    assert "Outbox id (outcome only)" in html
    assert "gate-reason" not in html  # States on: no gate reason
    for name in ("value", "delta"):
        assert 'min="' not in field(row(html, 0), name)

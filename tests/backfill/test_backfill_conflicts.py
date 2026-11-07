"""Resolving a backfill's conflicts from the Backfill page (#309; FR-PROF-10, E4's body rule,
E21, E24), with ``m7/backfill_paused.json`` and ``backfill_conflict_resolved.json``."""

import json
import re
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.utils.html import escape

from console.backfill import (
    CONFLICT_CLOSED_TEXT,
    CONFLICT_NOT_FOUND_TEXT,
    LAST_RESOLVED_TEXT,
    MALFORMED_CONFLICT_ID,
    NOT_AWAITING_TEXT,
    UNRESOLVABLE_ENTRY_TEXT,
)
from console.forms.backfill import NO_CHOICE, VALUE_HELP, statement_label
from console.portability import IMPORTING_TEXT
from tests.backfill.helpers import BACKFILL_PATH, BACKFILL_READ, PAGE, STORY, m7
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

FIRST = "01JM7CF0000000000000000001"
SECOND = "01JM7CF0000000000000000002"
SM1, SM2, SM3 = (f"01JM7SM000000000000000000{n}" for n in (1, 2, 3))
CONFLICT_PATH = "/stories/{story_id}/backfill/conflicts/{cid}"
KEY = "01JM7KY0000000000000000009"


def url(conflict_id: str = FIRST) -> str:
    return f"{PAGE}conflicts/{conflict_id}/"


@pytest.fixture
def paused(engine: FakeEngine) -> FakeEngine:
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_paused"))
    return engine


def resolved(engine: FakeEngine, **backfill: Any) -> None:
    body = m7("backfill_conflict_resolved")
    if backfill:
        body["backfill"] = backfill
    engine.respond("POST", CONFLICT_PATH, 200, json=body)


def post(client: Client, conflict_id: str = FIRST, **data: str) -> Any:
    return client.post(url(conflict_id), {"idempotency_key": KEY, **data})


def writes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST"]


def sent(engine: FakeEngine) -> Any:
    [request] = writes(engine)
    return json.loads(request.content)


def sections(html: str) -> dict[str, str]:
    return dict(
        re.findall(r'<section class="conflict" id="conflict-([^"]+)">(.*?)</section>', html, re.S)
    )


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def radios(section: str) -> list[tuple[str, bool]]:
    return [
        (value, bool(checked))
        for value, checked in re.findall(
            r'<input type="radio" name="choose" value="([^"]+)" id="[^"]+"( checked)?>', section
        )
    ]


# --- the form -----------------------------------------------------------------------------------


def test_open_conflict_has_a_form_with_the_recommendation_selected(
    operator_client: Client, paused: FakeEngine
) -> None:
    html = operator_client.get(PAGE).content.decode()
    first, second = sections(html).values()
    assert f'action="{url(FIRST)}"' in first
    assert f'action="{url(SECOND)}"' in second
    assert radios(first) == [(SM1, False), (SM2, True)]  # recommended.choose pre-selected
    assert '<button type="submit">Resolve</button>' in first
    assert '<button type="submit" name="accept" value="1">Accept the recommendation</button>' in (
        first
    )
    assert "Or state the value" in first
    assert VALUE_HELP in first
    assert 'maxlength="2000"' in first
    # Labels: "Turn <n> — <speaker>: <value> “<quote>”"; ids never collide across forms.
    assert "Turn 3 — Alice: 52 “Bob&#x27;s fifty-two, whatever he tells you.”" in first
    assert "Turn 2 — Alice: Port Vey “I grew up in Port Vey.”" in second
    assert f'id="conflict-{FIRST}-value"' in first
    assert f'id="conflict-{SECOND}-value"' in second
    # Each form carries its own key.
    keys = re.findall(r'name="idempotency_key" value="([^"]+)"', first + second)
    assert len(keys) == 2
    assert keys[0] != keys[1]


def test_a_resolved_conflict_renders_no_form(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("backfill_paused")
    body["conflict_list"][0] = m7("backfill_conflict_resolved")["conflict"]
    engine.respond("GET", BACKFILL_PATH, 200, json=body)
    html = operator_client.get(f"{PAGE}?conflicts=all").content.decode()
    first, second = sections(html).values()
    assert "<form" not in first
    assert first.count("Resolved by") == 1
    assert second.count("<form") == 1


def test_no_recommendation_preselects_nothing_and_has_no_accept(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = m7("backfill_paused")
    body["conflict_list"][0]["recommended"] = None
    engine.respond("GET", BACKFILL_PATH, 200, json=body)
    first = sections(operator_client.get(PAGE).content.decode())[FIRST]
    assert radios(first) == [(SM1, False), (SM2, False)]
    assert "Accept the recommendation" not in first


def test_a_rejected_entry_cannot_be_resolved_here(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = m7("backfill_paused")
    body["conflict_list"][1] = {"id": "01JM7CF0000000000000000009", "status": "open"}
    engine.side_effect("GET", BACKFILL_PATH, lambda request: httpx.Response(200, json=body))
    html = operator_client.get(PAGE).content.decode()
    assert html.count(escape(UNRESOLVABLE_ENTRY_TEXT)) == 1
    assert html.count('class="resolve-conflict"') == 1


def test_a_non_string_value_is_compact_json_cut_with_a_title() -> None:
    from console.engine.models import ConflictStatement

    long = {"k": "x" * 200}
    statement = ConflictStatement(SM1, None, 4, 1, None, "narration", None, long)
    label, title = statement_label(statement, {})
    assert label.startswith("Turn 4 — Narration: {")
    assert label.endswith("…")
    assert title == json.dumps(long, separators=(",", ":"), sort_keys=True)


# --- the body -----------------------------------------------------------------------------------


def test_accept_sends_only_accept_recommendation(
    operator_client: Client, paused: FakeEngine
) -> None:
    resolved(paused)
    response = post(operator_client, choose=SM1, value="", accept="1")
    assert response.status_code == 303
    assert sent(paused) == {"accept_recommendation": True}


def test_value_wins_over_the_radio(operator_client: Client, paused: FakeEngine) -> None:
    resolved(paused)
    post(operator_client, choose=SM1, value="  the harbour ")
    assert sent(paused) == {"value": "the harbour"}


def test_a_radio_alone_sends_choose(operator_client: Client, paused: FakeEngine) -> None:
    resolved(paused)
    post(operator_client, choose=SM1, value="")
    body = sent(paused)
    assert body == {"choose": SM1}
    assert "value" not in body
    [write] = writes(paused)
    assert write.headers["Idempotency-Key"] == KEY


def test_foreign_statement_id_is_refused_locally(
    operator_client: Client, paused: FakeEngine
) -> None:
    resolved(paused)
    response = post(operator_client, choose=SM3)  # the second conflict's statement
    html = response.content.decode()
    assert response.status_code == 200
    first, second = sections(html).values()
    assert escape(NO_CHOICE) in first
    assert escape(NO_CHOICE) not in second
    # The complement: no POST reached the Engine (reads only, E24).
    assert writes(paused) == []


def test_no_choice_at_all_is_refused_locally(operator_client: Client, paused: FakeEngine) -> None:
    response = post(operator_client)
    assert escape(NO_CHOICE) in sections(response.content.decode())[FIRST]
    assert writes(paused) == []


def test_the_local_check_reads_open_conflicts(operator_client: Client, paused: FakeEngine) -> None:
    resolved(paused)
    post(operator_client, choose=SM1)
    [check] = [r for r in paused.requests if r.method == "GET" and r.url.path == BACKFILL_READ]
    assert dict(check.url.params) == {"conflicts": "open", "limit": "200"}


def test_a_conflict_the_read_missed_goes_to_the_engine(
    operator_client: Client, paused: FakeEngine
) -> None:
    resolved(paused)
    other = "01JM7CF0000000000000000007"
    post(operator_client, other, choose="anything")
    assert sent(paused) == {"choose": "anything"}


def test_malformed_conflict_id_404_no_call(operator_client: Client, paused: FakeEngine) -> None:
    response = post(operator_client, "not-a-ulid", choose=SM1)
    assert response.status_code == 404
    assert escape(MALFORMED_CONFLICT_ID) in response.content.decode()
    assert paused.requests == []


def test_the_conflict_id_is_trimmed_and_upper_cased(
    operator_client: Client, paused: FakeEngine
) -> None:
    resolved(paused)
    post(operator_client, FIRST.lower(), choose=SM1)
    [write] = writes(paused)
    assert write.url.path == f"/stories/{STORY}/backfill/conflicts/{FIRST}"


def test_get_is_not_allowed(operator_client: Client, paused: FakeEngine) -> None:
    assert operator_client.get(url()).status_code == 405
    assert paused.requests == []


# --- the answers --------------------------------------------------------------------------------


def test_resolved_flashes_the_open_count(operator_client: Client, paused: FakeEngine) -> None:
    resolved(paused)
    response = post(operator_client, accept="1")
    assert response["Location"] == f"{PAGE}#conflicts"
    assert flashes(response) == [
        "Conflict resolved — 1 still open — idempotency key <code>01JM7KY0000000000000000001</code>"
    ]


def test_last_conflict_completes(operator_client: Client, paused: FakeEngine) -> None:
    resolved(paused, status="completed", conflicts_open=0)
    response = post(operator_client, value="the harbour")
    assert response.status_code == 303
    assert response["Location"] == f"{PAGE}#backfill-status"
    [flash] = flashes(response)
    assert flash.startswith(f"{LAST_RESOLVED_TEXT} — idempotency key <code>")


def test_resolved_without_a_count_or_with_another_status(
    operator_client: Client, paused: FakeEngine
) -> None:
    resolved(paused, status="reconciling")
    [flash] = flashes(post(operator_client, accept="1"))
    assert flash.startswith("Conflict resolved — the backfill is now Reconciling — idempotency")


REFUSALS = [
    (409, "backfill_conflict_closed", {}, CONFLICT_CLOSED_TEXT),
    (409, "backfill_not_awaiting_conflicts", {}, NOT_AWAITING_TEXT),
    (422, "invalid_resolution", {"field": "body.value"}, "the value does not normalize"),
    (404, "backfill_conflict_not_found", {}, CONFLICT_NOT_FOUND_TEXT),
    (404, "backfill_not_found", {}, CONFLICT_NOT_FOUND_TEXT),
    (409, "story_importing", {"job_id": "01JM7JB0000000000000000007"}, IMPORTING_TEXT),
]


@pytest.mark.parametrize(("status", "code", "details", "text"), REFUSALS)
def test_resolve_refusals(
    operator_client: Client,
    paused: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
    text: str,
) -> None:
    paused.respond(
        "POST",
        CONFLICT_PATH,
        status,
        json=error_json(code, "the value does not normalize", **details),
    )
    response = post(operator_client, value="the harbour")
    html = response.content.decode()
    assert response.status_code == 200
    first, second = sections(html).values()
    assert escape(text) in first
    assert escape(text) not in second
    # The complement: only the one POST was made (the re-render reads, E24).
    assert len(writes(paused)) == 1
    # An answered refusal mints a fresh key (E21).
    assert KEY not in first


def test_invalid_resolution_without_a_value_goes_on_the_radios(
    operator_client: Client, paused: FakeEngine
) -> None:
    paused.respond(
        "POST", CONFLICT_PATH, 422, json=error_json("invalid_resolution", "names none of them")
    )
    first = sections(post(operator_client, accept="1").content.decode())[FIRST]
    errors = first.split('<fieldset class="resolve-choice">', 1)[1].split("</fieldset>", 1)[0]
    assert "names none of them" in errors


def test_an_unknown_outcome_keeps_the_key(operator_client: Client, paused: FakeEngine) -> None:
    paused.fail("POST", CONFLICT_PATH, httpx.ReadTimeout("slow"))
    html = post(operator_client, choose=SM1).content.decode()
    first, second = sections(html).values()
    assert f'name="idempotency_key" value="{KEY}"' in first
    assert KEY not in second
    assert 'class="engine-error' in first


def test_a_refusal_for_a_conflict_gone_from_the_list_shows_above_it(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = m7("backfill_paused")
    del body["conflict_list"][0]
    engine.respond("GET", BACKFILL_PATH, 200, json=body)
    engine.respond("POST", CONFLICT_PATH, 409, json=error_json("backfill_conflict_closed"))
    response = post(operator_client, choose=SM1)
    html = response.content.decode()
    assert response.status_code == 200
    assert f'<p class="warning conflicts-error">{CONFLICT_CLOSED_TEXT}</p>' in html

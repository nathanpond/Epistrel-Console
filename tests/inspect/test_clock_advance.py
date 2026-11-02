"""Advance the world clock between turns (#252): the page, the form, the gate, the write, the
result page and refusals.

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``, revision 12). ``m6/clock.json`` is anchored at offset 130
(+02h 10m), ``m6/clock_unanchored.json`` unanchored and ``m6/clock_external.json`` external
(#243). ``m6/clock_advanced.json`` is 130 → 190 at turn 3⁺² (revision 13); this story adds
``m6/clock_advanced_to_time.json`` (130 → 1080, the next 18:00, at turn 2⁺²). Refusal bodies are
built inline (D16)."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.clock import (
    ADVANCE_BOTH,
    ADVANCE_NEITHER,
    INT_TOO_BIG,
    REASON_REQUIRED,
    REASON_TOO_LONG,
    SKIP_INVALID,
    UNANCHORED_HINT,
)
from console.forms.world import PLANNED_INVALID
from console.inspect import GATE_UNREAD
from console.models import EngineOperation
from console.views import inspect as views
from console.world import CLOCK_EXTERNAL_TEXT
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
KEY = "01JM6QP00000000000000000K1"
CLOCK = "/stories/{story_id}/clock"
ADVANCE = "/stories/{story_id}/clock/advance"
CLOCK_PATH = f"/stories/{STORY}/clock"
ADVANCE_PATH = f"/stories/{STORY}/clock/advance"
CLOCK_URL = f"/stories/{STORY}/inspect/world/clock/"
URL = f"/stories/{STORY}/inspect/world/clock/advance/"
ADVANCED_URL = f"/stories/{STORY}/inspect/world/clock/advanced/"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
WRITES = ("PUT", "POST", "PATCH", "DELETE")
BACKWARD = (
    "That would move the clock backward (it is at +02h 10m, you asked for +01h 00m) — earlier "
    "story-time is reached by rewinding"
)
CLOCK_OFF_REASON = "Needs <code>clock.level</code> at <code>prompt_only</code> or above"
SHELL_PATHS = [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m6/{name}"))
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v3())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond("GET", CLOCK, 200, json=fixture("clock"))
    fake.respond("POST", ADVANCE, 200, json=fixture("clock_advanced"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def advances(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "POST" and r.url.path == ADVANCE_PATH]


def writes(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method in WRITES]


def clock_reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "GET" and r.url.path == CLOCK_PATH]


def sent(engine: FakeEngine) -> dict[str, Any]:
    (advance,) = advances(engine)
    body: dict[str, Any] = json.loads(advance.content)
    return body


def get(client: Client, url: str = URL) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def post(client: Client, **fields: Any) -> str:
    data: dict[str, Any] = {"idempotency_key": KEY, "expected_revision": "12"}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    response = client.post(URL, data)
    assert response.status_code == 200
    return response.content.decode()


def field(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def hidden(html: str, name: str) -> str | None:
    found = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)">', html)
    return found.group(1) if found is not None else None


def refusal(html: str) -> str:
    found = re.search(
        r'<p class="errorlist clock-advance-refusal" role="alert">(.*?)</p>', html, re.S
    )
    assert found is not None
    return found.group(1)


def non_field(html: str) -> list[str]:
    found = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    if found is None:
        return []
    return [unescape(e) for e in re.findall(r'<p class="errorlist">(.*?)</p>', found.group(1))]


def form_html(html: str) -> str:
    assert 'class="clock-advance-form"' in html
    return html.split('class="clock-advance-form"')[1].split("</form>")[0]


def submit(html: str) -> str:
    found = re.search(r'<button type="submit" class="clock-advance-submit"[^>]*>', html)
    assert found is not None
    return found.group(0)


def history_url() -> str:
    return reverse("stories:history", args=[STORY])


# --- the page and the gate ---------------------------------------------------------------------


def test_get_shows_the_clock_and_the_form(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert [r.url.path for r in engine.requests] == [*SHELL_PATHS, CLOCK_PATH]
    display = fixture("clock")["display"]
    assert (
        f'The clock is at <span class="clock" title="offset 130 min">{escape(display)}</span>'
        in html
    )
    assert hidden(html, "expected_revision") == "12"
    key = hidden(html, "idempotency_key")
    assert key is not None
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", key)
    assert "Advance the clock</h3>" in html
    assert "Inspect · Advance the clock · " in html
    assert f'<a class="active" aria-current="page" href="{CLOCK_URL}">Clock</a>' in html
    form = form_html(html)
    for attr in ("min=", "max=", "maxlength"):
        assert attr not in form
    assert 'type="text"' in field(html, "minutes")
    assert 'inputmode="numeric"' in field(html, "minutes")
    assert "Advance by (minutes)" in field(html, "minutes")
    assert "Or advance to" in field(html, "to")
    assert UNANCHORED_HINT.replace("'", "&#x27;") not in html
    assert "Recorded with the advance (1\u2013200 characters)" in field(html, "reason")
    assert "disabled" not in submit(html)
    assert not writes(engine)
    # A fresh key on every render.
    assert hidden(get(operator_client), "idempotency_key") != key


def test_external_clock_has_no_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK, 200, json=fixture("clock_external"))
    html = get(operator_client)
    assert escape(CLOCK_EXTERNAL_TEXT) in html
    # Complement: no form posting to clock/advance/, and no submit.
    assert re.search(r'<form[^>]*action="[^"]*clock/advance/"', html) is None
    assert "clock-advance-submit" not in html
    # External wins over the gate: no gate reason either.
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = get(operator_client)
    assert escape(CLOCK_EXTERNAL_TEXT) in html
    assert "clock.level" not in html.split("Advance the clock</h3>")[1]


def test_unanchored_hint(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK, 200, json=fixture("clock_unanchored"))
    html = get(operator_client)
    assert escape(UNANCHORED_HINT) in field(html, "to")
    assert (
        "Minutes from the story&#x27;s start (e.g. 600), or a time of day (HH:MM): the next time "
        "it comes round. This clock is unanchored" in field(html, "to")
    )


def test_clock_read_failure_keeps_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", CLOCK, httpx.ConnectError("down"))
    html = get(operator_client)
    assert "The clock is at" not in html
    assert "engine-error" in html.split('class="clock-advance-form"')[0]
    assert 'class="clock-advance-form"' in html


def test_gated_link_and_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    section = get(operator_client, CLOCK_URL)
    link = re.search(r'<span class="disabled clock-advance"[^>]*>Advance the clock</span>', section)
    assert link is not None
    assert 'aria-disabled="true"' in link.group(0)
    assert "clock.level" in link.group(0)
    assert f'href="{URL}"' not in section
    assert CLOCK_OFF_REASON in section
    html = get(operator_client)
    assert "disabled" in submit(html)
    assert "clock.level" in submit(html)
    assert CLOCK_OFF_REASON in html
    # Inputs stay editable.
    assert "disabled" not in field(html, "minutes")


def test_advance_link_live_when_open(operator_client: Client, engine: FakeEngine) -> None:
    section = get(operator_client, CLOCK_URL)
    assert f'<a class="clock-advance" href="{URL}">Advance the clock</a>' in section


def test_gated_post_makes_no_write(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = post(operator_client, minutes="30", reason="they wait for the bus")
    assert CLOCK_OFF_REASON in refusal(html)
    assert 'value="they wait for the bus"' in field(html, "reason")
    assert hidden(html, "idempotency_key") == KEY
    # Complement: no advance was sent.
    assert advances(engine) == []
    assert not clock_reads(engine)


def test_gate_unread_post_makes_no_write(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=structured_v3(clock=None))
    html = post(operator_client, minutes="30", reason="r")
    assert refusal(html) == escape(GATE_UNREAD)
    assert advances(engine) == []
    # A failed effective-config read: the shell's panel, and no advance either.
    engine.respond("GET", EFFECTIVE, 503, json=error_json("unavailable"))
    html = post(operator_client, minutes="30", reason="r")
    assert "engine-error" in html
    assert advances(engine) == []


# --- local validation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "where", "message"),
    [
        ({}, None, ADVANCE_NEITHER),
        ({"minutes": "30", "to": "600"}, None, ADVANCE_BOTH),
        ({"minutes": "-30"}, "minutes", SKIP_INVALID),
        ({"minutes": "1.5"}, "minutes", SKIP_INVALID),
        ({"minutes": "2147483648"}, "minutes", INT_TOO_BIG),
        ({"to": "25:00"}, "to", PLANNED_INVALID),
        ({"to": "7:30"}, "to", PLANNED_INVALID),
        ({"to": "+60"}, "to", PLANNED_INVALID),
        ({"minutes": "30", "reason": ""}, "reason", REASON_REQUIRED),
        ({"minutes": "30", "reason": "   "}, "reason", REASON_REQUIRED),
        ({"minutes": "30", "reason": "x" * 201}, "reason", REASON_TOO_LONG),
    ],
)
def test_local_validation_zero_calls(
    operator_client: Client,
    engine: FakeEngine,
    fields: dict[str, str],
    where: str | None,
    message: str,
) -> None:
    data = {"reason": "r", **fields}
    html = post(operator_client, **data)
    if where is None:
        assert non_field(html) == [message]
    else:
        assert escape(message) in field(html, where)
        assert message not in non_field(html)
    if where in ("minutes", "to"):
        assert non_field(html) == []  # one message per mistake
    assert advances(engine) == []


def test_reason_of_200_after_trimming_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, minutes="0", reason=f"  {'é' * 200}  ")
    assert sent(engine) == {"reason": "é" * 200, "minutes": 0}


# --- the write ---------------------------------------------------------------------------------


def lines(html: str) -> list[str]:
    found = re.search(r'<ul class="clock-advanced-lines">(.*?)</ul>', html, re.S)
    assert found is not None
    return [
        unescape(re.sub(r"<[^>]+>", "", item)).strip()
        for item in re.findall(r"<li[^>]*>(.*?)</li>", found.group(1), re.S)
    ]


def expected_lines(reason: str) -> list[str]:
    answer = fixture("clock_advanced")
    return [
        f"Clock: {answer['before']['display']} → {answer['after']['display']}",
        "Advanced by +01h 00m",
        f"Reason: {reason}",
        "Slot: turn 3⁺² · start",
        "Operation: 01JM6QP0000000000000000021",
        "Story revision now 13",
    ]


def test_advance_by_minutes(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    key = hidden(html, "idempotency_key")
    html = post(
        operator_client,
        minutes=" 30 ",
        to="",
        reason="  they wait for the bus ",
        idempotency_key=key,
        expected_revision=hidden(html, "expected_revision"),
    )
    assert sent(engine) == {"reason": "they wait for the bus", "minutes": 30}
    (advance,) = advances(engine)
    assert advance.headers["X-Epistrel-Expected-Revision"] == "12"
    assert advance.headers["Idempotency-Key"] == key
    row = EngineOperation.objects.get()
    assert row.kind == "advance_clock"
    assert "Clock advanced</h3>" in html
    assert lines(html) == expected_lines("they wait for the bus")
    slot = re.search(r'<li class="slot">Slot: <a href="([^"]+)">', html)
    assert slot is not None
    assert f'<a class="operation-link" href="/operations/{row.pk}/">' in html
    assert f'<a class="clock-back" href="{CLOCK_URL}">Back to Clock</a>' in html
    assert f'<a class="advance-again" href="{URL}">Advance again</a>' in html
    assert f'href="{ADVANCED_URL}?report={row.pk}"' in html
    more = html.split('<details class="clock-advanced-more">')[1]
    assert "01JM6CG0000000000000000021" in more
    assert '<dd class="offset-before">130</dd>' in more
    assert '<dd class="offset-after">190</dd>' in more
    assert "Engine position" not in more
    assert f'<a class="active" aria-current="page" href="{CLOCK_URL}">Clock</a>' in html
    assert "Replayed" not in html
    assert '<ul class="messages' not in html


@pytest.mark.parametrize(("typed", "value"), [("600", 600), ("0600", 600), ("18:00", "18:00")])
def test_advance_to_offset_and_to_time(
    operator_client: Client, engine: FakeEngine, typed: str, value: Any
) -> None:
    engine.respond("POST", ADVANCE, 200, json=fixture("clock_advanced_to_time"))
    post(operator_client, to=typed, reason="evening")
    body = sent(engine)
    assert body == {"reason": "evening", "to": value}
    assert type(body["to"]) is type(value)


def test_minutes_zero_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, minutes="0", reason="r")
    assert sent(engine) == {"reason": "r", "minutes": 0}


def test_no_expected_revision_without_story_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("GET", STORY_PATH, httpx.ConnectError("down"))
    html = get(operator_client)
    assert hidden(html, "expected_revision") is None
    assert 'class="clock-advance-form"' in html
    operator_client.post(URL, {"idempotency_key": KEY, "minutes": "30", "reason": "r"})
    (advance,) = advances(engine)
    assert "X-Epistrel-Expected-Revision" not in advance.headers


def test_post_makes_no_clock_read(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, minutes="30", reason="r")
    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("GET", SHELL_PATHS[0]),
        ("GET", SHELL_PATHS[1]),
        ("POST", ADVANCE_PATH),
    ]


def test_report_page_from_the_row(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, minutes="30", reason="they wait for the bus")
    row = EngineOperation.objects.get()
    engine.requests.clear()
    html = get(operator_client, f"{ADVANCED_URL}?report={row.pk}")
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert lines(html) == expected_lines("they wait for the bus")
    assert f'<a class="active" aria-current="page" href="{CLOCK_URL}">Clock</a>' in html
    # A stored request without a reason reads "—".
    row.request = {"body": {"minutes": 30}}
    row.save()
    assert lines(get(operator_client, f"{ADVANCED_URL}?report={row.pk}"))[2] == "Reason: —"


@pytest.mark.parametrize("case", ["story", "kind", "error", "unparsed", "nonint", "missing"])
def test_report_page_complement(operator_client: Client, engine: FakeEngine, case: str) -> None:
    post(operator_client, minutes="30", reason="r")
    row = EngineOperation.objects.get()
    report: object = row.pk
    if case == "story":
        row.story_id = "01JM6ST0000000000000000002"
    elif case == "kind":
        row.kind = "set_state"
    elif case == "error":
        row.outcome = EngineOperation.Outcome.ERROR
    elif case == "unparsed":
        row.response = {"before": "x"}
    elif case == "nonint":
        report = "x"
    elif case == "missing":
        report = row.pk + 1
    row.save()
    engine.requests.clear()
    html = get(operator_client, f"{ADVANCED_URL}?report={report}")
    assert NOT_FOUND in html
    assert f'<a href="{CLOCK_URL}">Back to Clock</a>' in html
    assert "clock-advanced-lines" not in html
    assert not writes(engine)


def test_replayed_row_says_replayed(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        ADVANCE,
        200,
        json=fixture("clock_advanced"),
        headers={"Idempotent-Replay": "true"},
    )
    html = post(operator_client, minutes="30", reason="r")
    assert "Replayed (repeated submit)" in html


# --- refusals ----------------------------------------------------------------------------------


def refuse(
    engine: FakeEngine,
    status: int,
    code: str,
    headers: dict[str, str] | None = None,
    **details: Any,
) -> None:
    engine.respond("POST", ADVANCE, status, json=error_json(code, **details), headers=headers)


def refused_page(client: Client, **fields: Any) -> str:
    data = {"minutes": "", "to": "60", "reason": "they wait for the bus"}
    data.update(fields)
    html = post(client, **data)
    assert 'class="clock-advance-form"' in html
    assert f'value="{data["to"]}"' in field(html, "to") or not data["to"]
    assert 'value="they wait for the bus"' in field(html, "reason")
    assert hidden(html, "idempotency_key") != KEY
    assert "Clock advanced" not in html
    assert '<ul class="messages' not in html
    return html


def test_backward_advance_changes_nothing(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "clock_backward", current=130, attempted=60)
    html = refused_page(operator_client)
    assert refusal(html) == f'{BACKWARD} <a href="{history_url()}">Rewind…</a>'
    assert len(advances(engine)) == 1
    assert "The clock is at" not in html
    # With details.field it sits beside `to`, the Rewind… link after its errors.
    refuse(engine, 422, "clock_backward", current=130, attempted=60, field="body.to")
    html = refused_page(operator_client)
    to = field(html, "to")
    assert BACKWARD in to
    assert to.index(BACKWARD) < to.index(f'<a href="{history_url()}">Rewind…</a>')
    assert "clock-advance-refusal" not in html
    # The clock is unchanged: the Clock section still reads +02h 10m.
    section = get(operator_client, CLOCK_URL)
    assert "Clock: <span" in section
    assert ">+02h 10m (Saturday" in section


def test_clock_unanchored_on_to(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "clock_unanchored", to="18:00")
    html = refused_page(operator_client, to="18:00")
    assert escape(
        "The clock's anchor doesn't fix the time of day, so \"18:00\" can't be placed — "
        "enter minutes from the story's start"
    ) in field(html, "to")
    assert "clock-advance-refusal" not in html


def test_clock_external_above_form(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "clock_external")
    html = refused_page(operator_client)
    assert refusal(html) == escape(CLOCK_EXTERNAL_TEXT)


def test_module_off_is_a_form_refusal(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "module_off", module="clock")
    html = refused_page(operator_client)
    assert "<code>clock.level</code>" in refusal(html)
    assert f'<a href="/stories/{STORY}/config/">Config</a>' in refusal(html)
    # Complement: no re-grey, no extra read.
    assert "disabled" not in submit(html)
    assert [r.url.path for r in engine.requests] == [*SHELL_PATHS, ADVANCE_PATH]


def test_story_busy_with_retry_after(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 423, "story_busy", headers={"Retry-After": "4"}, reason="in_flight")
    html = refused_page(operator_client)
    assert refusal(html) == "Story busy — another turn is running · retry after 4 s"


def test_revision_conflict(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "revision_conflict", current_revision=14)
    engine.respond("GET", STORY_PATH, 200, json={**fixture("story"), "timeline_revision": 14})
    html = refused_page(operator_client, expected_revision="12")
    assert "changed" in refusal(html)
    assert hidden(html, "expected_revision") == "14"
    (advance,) = advances(engine)
    assert advance.headers["X-Epistrel-Expected-Revision"] == "12"


def test_archived(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "story_archived")
    html = refused_page(operator_client)
    assert refusal(html) == "This story is archived — unarchive it to advance the clock."


def test_request_validation_beside_field(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        ADVANCE,
        422,
        json=error_json(
            "validation_error",
            "request validation failed",
            errors=[{"field": "body.reason", "message": "String should have at most 200"}],
        ),
    )
    html = refused_page(operator_client)
    assert "String should have at most 200" in field(html, "reason")


def test_request_validation_at_body_is_non_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        ADVANCE,
        422,
        json=error_json(
            "validation_error",
            "request validation failed",
            errors=[{"field": "body", "message": "exactly one of minutes and to"}],
        ),
    )
    html = refused_page(operator_client)
    assert any("exactly one of minutes and to" in e for e in non_field(html))


def test_other_refusal_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "idempotency_conflict")
    html = refused_page(operator_client)
    assert "engine-error" in html.split('class="clock-advance-form"')[0]


def test_refusal_mints_a_fresh_key(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "clock_backward", current=130, attempted=60)
    html = refused_page(operator_client, expected_revision="9")
    assert hidden(html, "expected_revision") == "12"  # the POST's own story read
    # Complement: an invalid form keeps the posted key and revision.
    html = post(operator_client, minutes="x", reason="r", expected_revision="9")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "9"
    assert 'value="x"' in field(html, "minutes")
    # ... and so does a transport error (the outcome is unknown).
    engine.fail("POST", ADVANCE, httpx.ConnectError("down"))
    html = post(operator_client, minutes="30", reason="r", expected_revision="9")
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "9"
    assert "engine-error" in html.split('class="clock-advance-form"')[0]
    assert 'value="30"' in field(html, "minutes")

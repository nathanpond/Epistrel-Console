"""Request consolidation of a turn window and find what it wrote (#145): the Memory tab's
"Consolidate a window" section, the consolidate page's local checks, the confirm step, the
Engine call and its flash, the operation log row and the refusals.

Runs against the real urlconf on the schema-validating fake Engine. The Memory tab's hot set is
``hot_set_empty.json`` (head turn 3 unless a test changes it); the 202 answer is
``tests/fixtures/inspect/consolidation_queued.json``."""

import copy
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient, EngineError
from console.engine.models import ConsolidationQueued
from console.engine_access import engine_client_for
from console.forms.inspect import ConsolidateForm
from console.held import held_text
from console.models import EngineOperation
from console.views import inspect as views
from console.views import inspect_memory
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, structured
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

MEMORY = f"/stories/{STORY}/inspect/memory/"
URL = f"/stories/{STORY}/inspect/memory/consolidate/"
CONSOLIDATE = "/admin/stories/{story_id}/consolidate"
CONSOLIDATE_PATH = f"/admin/stories/{STORY}/consolidate"
HOT_SET = "/admin/stories/{story_id}/hot-set"
JOB = "01JB2Z8Y5Q3W6K9M1N4P7R0J0B"
OTHER_JOB = "01JB2Z8Y5Q3W6K9M1N4P7R0J0C"
OTHER_STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2W"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0K01"
KEY_2 = "01JB2Z8Y5Q3W6K9M1N4P7R0K02"
QUEUED = (
    f"Consolidation queued (job {JOB}). Its semantic facts and reflections appear in Retrieval "
    "when the worker finishes — use the links under Consolidate a window."
)
JOINED = " This is the job your request of {when} joined: the Engine gives a repeated request"
DETAIL = (
    f"Consolidation queued as job {JOB} — the Engine reports no progress for consolidation "
    "jobs; its outputs appear in Retrieval as semantic facts and reflections when it finishes."
)
SEMANTIC = f'<a href="/stories/{STORY}/inspect/retrieval/?kind=semantic">Find semantic facts</a>'
REFLECTION = f'<a href="/stories/{STORY}/inspect/retrieval/?kind=reflection">Find reflections</a>'
NO_TURNS = "No turns yet — nothing to consolidate."
GREYED = "Needs the <code>consolidation</code> module"


def hot_set(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / "hot_set_empty.json").read_text())
    body = copy.deepcopy(body)
    body.update(over)
    return body


def queued(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / "consolidation_queued.json").read_text())
    body.update(over)
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    monkeypatch.setattr(inspect_memory, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured())
    fake.respond("GET", STORY_PATH, 200, json=story_json())
    fake.respond("GET", HOT_SET, 200, json=hot_set())
    # The Memory tab's minor-budget reads (#168).
    omniscient = json.loads((FIXTURES / "story_omniscient.json").read_text())
    fake.respond("GET", STORY_PATH, 200, json=omniscient, query={"viewpoint": "omniscient"})
    fake.respond("GET", "/admin/settings", 200, json=engine_fixture("get_deployment_settings"))
    fake.respond("POST", CONSOLIDATE, 202, json=queued())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def posts(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == CONSOLIDATE_PATH]


def flashes(response: Any) -> list[tuple[str, str]]:
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def section(html: str) -> str:
    found = re.search(r'<section id="consolidate" class="consolidate">(.*?)</section>', html, re.S)
    assert found is not None
    return found.group(1)


def memory_tab(client: Client) -> str:
    response = client.get(MEMORY)
    assert response.status_code == 200
    return response.content.decode()


def hidden(html: str, name: str) -> str | None:
    found = re.search(rf'<input type="hidden" name="{name}"(?: value="([^"]*)")?', html)
    return (found.group(1) or "") if found else None


def value(html: str, name: str) -> str | None:
    found = re.search(rf'<input type="number" name="{name}"([^>]*)>', html)
    assert found is not None, name
    shown = re.search(r'value="([^"]*)"', found.group(1))
    return shown.group(1) if shown else None


def field_errors(html: str) -> list[str]:
    return re.findall(r'<span (?:id="[^"]*" )?class="errorlist field-error">([^<]*)</span>', html)


def ask(client: Client, **params: str) -> Any:
    return client.get(f"{URL}?{urlencode(params)}")


def confirm(client: Client, key: str = KEY, **over: str) -> Any:
    data = {"from_turn": "1", "to_turn": "3", "head": "3", "idempotency_key": key}
    data.update(confirmed="1", **over)
    return client.post(URL, data)


# --- the Memory tab's section --------------------------------------------------------------------


def test_the_section_is_the_last_on_the_tab(operator_client: Client, engine: FakeEngine) -> None:
    html = memory_tab(operator_client)
    assert html.index("<h2>Browse memories</h2>") < html.index('<section id="consolidate"')
    shown = section(html)
    assert "<h2>Consolidate a window</h2>" in shown
    assert (
        "Ask the Engine to consolidate a window of turns into semantic facts and reflections "
        "now. It runs in the background, and the Engine doesn't report its progress."
    ) in shown
    assert f'<form method="get" action="{URL}" class="consolidate-form">' in shown
    assert value(shown, "from_turn") == "1"
    assert value(shown, "to_turn") == "3"
    assert 'min="1"' in shown
    assert 'max="2147483647"' in shown
    assert hidden(shown, "head") == "3"
    assert re.fullmatch(r"[0-9A-Z]{26}", hidden(shown, "idempotency_key") or "")
    assert '<p class="consolidate-scope">Shared scope</p>' in shown
    assert '<button type="submit">Consolidate…</button>' in shown
    assert SEMANTIC in shown
    assert REFLECTION in shown
    # The section makes no read of its own: the effective config, the story, the hot set and
    # the tab's two minor-budget reads (#168: the omniscient story, the deployment settings).
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        f"/admin/stories/{STORY}/hot-set",
        f"/stories/{STORY}",
        "/admin/settings",
    ]


def test_each_render_mints_its_own_key(operator_client: Client, engine: FakeEngine) -> None:
    first = hidden(section(memory_tab(operator_client)), "idempotency_key")
    second = hidden(section(memory_tab(operator_client)), "idempotency_key")
    assert first
    assert second
    assert first != second


def test_a_failed_hot_set_read_leaves_to_blank_and_no_head(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", HOT_SET, 503, json=error_json("database_unavailable"))
    shown = section(memory_tab(operator_client))
    assert value(shown, "from_turn") == "1"
    assert value(shown, "to_turn") is None
    assert hidden(shown, "head") == ""
    assert SEMANTIC in shown


def test_without_the_capability_the_section_is_the_greyed_note(
    operator_client: Client, engine: FakeEngine
) -> None:
    config = structured()
    config["capabilities"] = [c for c in config["capabilities"] if c != "consolidation"]
    engine.respond("GET", EFFECTIVE, 200, json=config)
    shown = section(memory_tab(operator_client))
    assert '<p class="notice unavailable">' in shown
    assert GREYED in shown
    assert "<form" not in shown
    assert URL not in shown
    assert SEMANTIC in shown
    assert REFLECTION in shown


def test_the_greyed_note_wins_over_no_turns(operator_client: Client, engine: FakeEngine) -> None:
    config = structured()
    config["capabilities"] = [c for c in config["capabilities"] if c != "consolidation"]
    engine.respond("GET", EFFECTIVE, 200, json=config)
    engine.respond("GET", HOT_SET, 200, json=hot_set(head_turn=0))
    shown = section(memory_tab(operator_client))
    assert GREYED in shown
    assert NO_TURNS not in shown


def test_at_head_zero_there_is_nothing_to_consolidate(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", HOT_SET, 200, json=hot_set(head_turn=0))
    shown = section(memory_tab(operator_client))
    assert NO_TURNS in shown
    assert "<form" not in shown
    assert SEMANTIC in shown
    assert REFLECTION in shown


# --- the consolidate page: no call before the confirm --------------------------------------------


def test_a_bare_get_is_the_empty_form(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert value(html, "from_turn") == "1"
    assert value(html, "to_turn") is None
    assert not field_errors(html)
    assert "Shared scope" in html
    assert f'href="{MEMORY}#consolidate"' in html
    assert not engine.requests


@pytest.mark.parametrize(
    ("params", "errors"),
    [
        ({"from_turn": "4", "to_turn": "2"}, ["The window ends before it starts."]),
        ({"from_turn": "0", "to_turn": "2"}, ["Turns start at 1."]),
        ({"from_turn": "1", "to_turn": "-2"}, ["Turns start at 1."]),
        ({"from_turn": "x", "to_turn": "2"}, ["Enter a whole number."]),
        ({"from_turn": "1.5", "to_turn": "2"}, ["Enter a whole number."]),
        ({"from_turn": "1", "to_turn": "2147483648"}, ["At most 2,147,483,647."]),
        ({"from_turn": "", "to_turn": "2"}, ["Enter a turn."]),
        ({"from_turn": "1"}, ["Enter a turn."]),
        ({"from_turn": "1", "to_turn": "9", "head": "3"}, ["Turn 9 is past the head (turn 3)"]),
    ],
)
def test_a_refused_window_makes_no_call(
    operator_client: Client, engine: FakeEngine, params: dict[str, str], errors: list[str]
) -> None:
    response = ask(operator_client, idempotency_key=KEY, **params)
    html = response.content.decode()
    assert response.status_code == 200
    assert field_errors(html) == errors
    # #175: each input's aria-describedby names an element on the page.
    described = re.findall(r'aria-describedby="([^"]+)"', html)
    assert described
    for name in described:
        assert f'id="{name}"' in html, name
    assert '<form method="get"' in html
    assert hidden(html, "idempotency_key") == KEY
    assert "Shared scope" in html
    assert value(html, "from_turn") == (params.get("from_turn") or None)
    assert f'href="{MEMORY}#consolidate"' in html
    assert not engine.requests


def test_the_head_is_carried_on_a_refused_window(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = ask(operator_client, from_turn="5", to_turn="4", head="7").content.decode()
    assert hidden(html, "head") == "7"


@pytest.mark.parametrize("head", ["x", "0", "-1", "2.5"])
def test_a_malformed_head_drops_the_past_head_check(
    operator_client: Client, engine: FakeEngine, head: str
) -> None:
    html = ask(operator_client, from_turn="1", to_turn="9", head=head).content.decode()
    assert not field_errors(html)
    assert "Consolidate turns 1\u20139" in html
    assert hidden(html, "head") is None  # an ignored head isn't carried to the confirm
    assert not engine.requests


def test_a_valid_window_renders_the_confirm_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = ask(operator_client, from_turn="1", to_turn="3", head="3", idempotency_key=KEY)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<title>Consolidate turns 1\u20133" in html
    assert (
        "Consolidate turns 1\u20133 (shared scope)? A window already consolidated is rerun, and "
        "its earlier outputs are superseded."
    ) in html
    assert '<button type="submit" class="danger">Consolidate</button>' in html
    assert f'<a href="{MEMORY}">Cancel</a>' in html
    for name, shown in (
        ("from_turn", "1"),
        ("to_turn", "3"),
        ("head", "3"),
        ("idempotency_key", KEY),
        ("confirmed", "1"),
    ):
        assert hidden(html, name) == shown, name
    assert "<dt>Window</dt><dd><code>turns 1\u20133</code></dd>" in html
    assert "<dt>Scope</dt><dd><code>shared</code></dd>" in html
    assert "A window with nothing to consolidate still queues a job, which writes nothing." in html
    assert "snaps to whole turns, so the outputs may cover fewer turns." in html
    assert not engine.requests


def test_a_post_without_confirmed_is_the_confirm_page_again(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = {"from_turn": "1", "to_turn": "3", "head": "3", "idempotency_key": KEY}
    html = operator_client.post(URL, data).content.decode()
    assert hidden(html, "confirmed") == "1"
    assert hidden(html, "idempotency_key") == KEY
    assert not engine.requests


def test_a_confirm_past_the_carried_head_is_refused(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = confirm(operator_client, to_turn="4").content.decode()
    assert field_errors(html) == ["Turn 4 is past the head (turn 3)"]
    assert not engine.requests


# --- the call ------------------------------------------------------------------------------------


def test_the_call_sends_the_shared_window_with_the_tabs_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    key = hidden(section(memory_tab(operator_client)), "idempotency_key") or ""
    confirm_page = ask(operator_client, from_turn="1", to_turn="3", head="3", idempotency_key=key)
    assert hidden(confirm_page.content.decode(), "idempotency_key") == key
    engine.requests.clear()
    response = confirm(operator_client, key)
    (sent,) = posts(engine)
    assert json.loads(sent.content) == {"from_turn": 1, "to_turn": 3}
    assert sent.headers["Idempotency-Key"] == key
    assert "X-Epistrel-Expected-Revision" not in sent.headers
    assert response.status_code == 302
    assert response["Location"] == f"{MEMORY}#consolidate"
    assert flashes(response) == [("success", QUEUED)]


def test_the_row_stays_queued_and_its_detail_says_so(
    operator_client: Client, engine: FakeEngine
) -> None:
    confirm(operator_client)
    row = EngineOperation.objects.get()
    assert (row.kind, row.entity_type, row.entity_id) == ("consolidate_story", "story", STORY)
    assert (row.outcome, row.http_status, row.job_id) == ("queued", 202, JOB)
    detail = operator_client.get(f"/operations/{row.pk}/").content.decode()
    assert DETAIL in detail
    assert "Rebuild running" not in detail


def test_a_rebuild_row_keeps_the_rebuild_text(operator_client: Client, engine: FakeEngine) -> None:
    row = EngineOperation.objects.create(
        kind="rebuild_story",
        http_method="POST",
        path=f"/admin/stories/{STORY}/rebuild",
        http_status=202,
        outcome="queued",
        response={"job_id": JOB, "timeline_revision": 3},  # before #64's job_id column
        story_id=STORY,
    )
    detail = operator_client.get(f"/operations/{row.pk}/").content.decode()
    assert f"Rebuild running as job {JOB} — the Engine has no job status endpoint" in detail
    assert "Consolidation queued" not in detail


def earlier_row(story_id: str = STORY, job_id: str = JOB, key: str = KEY) -> EngineOperation:
    return EngineOperation.objects.create(
        kind="consolidate_story",
        http_method="POST",
        path=f"/admin/stories/{story_id}/consolidate",
        http_status=202,
        outcome="queued",
        job_id=job_id,
        idempotency_key=key,
        story_id=story_id,
        created_at=datetime(2026, 10, 24, 14, 3, 27, tzinfo=UTC),
    )


def test_a_repeat_that_joins_the_waiting_job_says_so(
    operator_client: Client, engine: FakeEngine
) -> None:
    confirm(operator_client, KEY)
    first = EngineOperation.objects.get()
    EngineOperation.objects.filter(pk=first.pk).update(
        created_at=datetime(2026, 10, 24, 14, 3, 27, tzinfo=UTC)
    )
    response = confirm(operator_client, KEY_2)
    joined = JOINED.format(when="2026-10-24 14:03 UTC")
    level, text = flashes(response)[-1]  # the first request's flash was never shown
    assert level == "success"
    assert text.startswith(QUEUED + joined)
    assert text.endswith(" the job still waiting.")
    rows = EngineOperation.objects.order_by("id")
    assert [(r.outcome, r.job_id) for r in rows] == [("queued", JOB), ("queued", JOB)]


@pytest.mark.parametrize(
    "row",
    [
        {"job_id": OTHER_JOB},  # another job
        {"story_id": OTHER_STORY},  # another story's row with the same job
        {"key": KEY_2},  # the same key: this request, not an earlier one
    ],
)
def test_no_joined_sentence_without_an_earlier_request_of_the_job(
    operator_client: Client, engine: FakeEngine, row: dict[str, str]
) -> None:
    earlier_row(**row)
    response = confirm(operator_client, KEY_2)
    assert flashes(response) == [("success", QUEUED)]


def test_a_replay_says_already_queued_only(operator_client: Client, engine: FakeEngine) -> None:
    earlier_row(key=KEY)  # an earlier request of the job under another key
    engine.respond("POST", CONSOLIDATE, 202, json=queued(), headers={"Idempotent-Replay": "true"})
    response = confirm(operator_client, KEY_2)
    assert flashes(response) == [("info", f"Already queued (repeated submit) — job {JOB}.")]


def test_without_a_row_the_answer_counts_as_fresh(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine.respond("POST", CONSOLIDATE, 202, json=queued(), headers={"Idempotent-Replay": "true"})
    monkeypatch.setattr("console.operation_log.record", lambda observed, operator: None)
    response = confirm(operator_client)
    assert not EngineOperation.objects.exists()
    assert flashes(response) == [("success", QUEUED)]


# --- refusals ------------------------------------------------------------------------------------


def test_scope_mismatch_keeps_the_window(operator_client: Client, engine: FakeEngine) -> None:
    scopes = [{"characters": ["01JB2Z8Y5Q3W6K9M1N4P7R0T3A"]}, {"characters": []}]
    engine.respond(
        "POST", CONSOLIDATE, 422, json=error_json("scope_mismatch", "spans", scopes=scopes)
    )
    response = confirm(operator_client)
    html = response.content.decode()
    assert response.status_code == 422
    assert (
        "These turns span scopes no single consolidation may combine. Pick a narrower window."
    ) in html
    assert value(html, "from_turn") == "1"
    assert value(html, "to_turn") == "3"
    assert hidden(html, "head") == "3"
    assert hidden(html, "idempotency_key") not in ("", KEY)  # a fresh key for a changed window
    assert '<details class="more"><summary>More</summary>' in html
    assert "01JB2Z8Y5Q3W6K9M1N4P7R0T3A" in html


@pytest.mark.parametrize(
    ("details", "named"), [({"module": "consolidation"}, "consolidation"), ({}, "consolidation")]
)
def test_module_off_is_the_greyed_panel(
    operator_client: Client, engine: FakeEngine, details: dict[str, str], named: str
) -> None:
    engine.respond("POST", CONSOLIDATE, 409, json=error_json("module_off", "off", **details))
    response = confirm(operator_client)
    html = response.content.decode()
    assert response.status_code == 409
    assert '<div class="panel unavailable">' in html
    assert f"Needs the <code>{named}</code> module" in html
    assert "The story's configuration changed since this page loaded" in html
    assert f'<a href="{MEMORY}">Back to Memory</a>' in html


@pytest.mark.parametrize(
    ("field", "where"), [("body.to_turn", "to_turn"), ("body.from_turn", "from_turn"), (None, "")]
)
def test_validation_error_lands_on_its_field(
    operator_client: Client, engine: FakeEngine, field: str | None, where: str
) -> None:
    details = {"field": field} if field else {}
    engine.respond(
        "POST", CONSOLIDATE, 422, json=error_json("validation_error", "too far", **details)
    )
    response = confirm(operator_client)
    html = response.content.decode()
    assert response.status_code == 422
    if where:
        assert field_errors(html) == ["too far"]
        assert html.index(f'name="{where}"') < html.index("too far")
    else:
        assert '<p class="errorlist">too far</p>' in html


def test_a_busy_story_is_the_refusal_line(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        CONSOLIDATE,
        423,
        json=error_json("story_busy", "busy", reason="turn_running"),
        headers={"Retry-After": "2"},
    )
    html = confirm(operator_client).content.decode()
    assert '<p class="errorlist consolidate-refusal">A turn is running — try again in 2 s</p>' in (
        html
    )


def test_not_found_is_the_refusal_line(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", CONSOLIDATE, 404, json=error_json("not_found", "gone"))
    html = confirm(operator_client).content.decode()
    assert "Not found (it may have been deleted or truncated)" in html


def test_an_unavailable_database_is_the_error_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", CONSOLIDATE, 503, json=error_json("database_unavailable", "down"))
    html = confirm(operator_client).content.decode()
    assert "Engine error 503 database_unavailable" in html
    assert value(html, "to_turn") == "3"


def test_erasure_pending_is_the_held_wrapper(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", CONSOLIDATE, 503, json=error_json("erasure_pending", "held", story_id=STORY)
    )
    response = confirm(operator_client)
    assert response.status_code == 503
    assert held_text(None) in response.content.decode()


# --- the client ----------------------------------------------------------------------------------


def _client(handler: Any) -> EngineClient:
    return EngineClient(
        "http://engine.test", "k", principal="console:op", transport=httpx.MockTransport(handler)
    )


def test_the_client_sends_a_characters_scope_as_given() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json=queued())

    answer = _client(handler).consolidate_story(
        STORY, 2, 5, characters=["B", "A", "B"], idempotency_key=KEY
    )
    assert answer == ConsolidationQueued(timeline_revision=12, job_id=JOB, idempotency_key=KEY)
    (sent,) = seen
    assert sent.url.path == CONSOLIDATE_PATH
    assert json.loads(sent.content) == {
        "from_turn": 2,
        "to_turn": 5,
        "scope": {"characters": ["B", "A", "B"]},
    }


def test_the_client_refuses_an_empty_characters_scope() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _client(lambda r: httpx.Response(202, json=queued())).consolidate_story(
            STORY, 1, 2, characters=[], idempotency_key=KEY
        )


def test_a_non_202_success_is_bad_response() -> None:
    with pytest.raises(EngineError) as raised:
        _client(lambda r: httpx.Response(200, json=queued())).consolidate_story(
            STORY, 1, 2, idempotency_key=KEY
        )
    assert (raised.value.code, raised.value.http_status) == ("bad_response", 200)


def test_the_form_never_sends_a_scope() -> None:
    form = ConsolidateForm({"from_turn": "1", "to_turn": "2"})
    assert form.is_valid()
    assert form.cleaned_data == {"from_turn": 1, "to_turn": 2, "head": None}
    assert "scope" not in form.fields

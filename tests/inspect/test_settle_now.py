"""Settle a listed external effect now, behind a confirm page (#256).

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``, revision 12). ``m6/settled_now.json`` settles ``BX02`` at
turn 3⁺² (revision 13, operation ``…QP…51``, Engine position ``3⁺2``);
``m6/settled_now_queued.json`` queues it (no slot, no operation). Refusal bodies are built inline
(D16)."""

import copy
import re
from collections.abc import Iterator
from html import unescape
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.inspect import GATE_UNREAD
from console.models import EngineOperation
from console.positions import knowledge_position_text, knowledge_position_url
from console.reconciliation import (
    MALFORMED_OUTBOX_ID,
    QUEUED_STAYS,
    QUEUED_TEXT,
    RESULT_UNAVAILABLE,
)
from console.views import inspect as views
from console.views import inspect_reconciliation
from console.world import five_part
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
OUTBOX = "01JM6BX0000000000000000002"
KEY = "01JM6QP00000000000000000K1"
SETTLE = "/stories/{story_id}/reconciliation/{outbox_id}/settle-now"
SETTLE_PATH = f"/stories/{STORY}/reconciliation/{OUTBOX}/settle-now"
LIST_URL = f"/stories/{STORY}/inspect/effects/reconciliation/"
URL = f"/stories/{STORY}/inspect/effects/reconciliation/{OUTBOX}/settle-now/"
SETTLED_URL = f"/stories/{STORY}/inspect/effects/reconciliation/settled/"
SHELL_PATHS = [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
CONFIG_PATH = f"/stories/{STORY}/effective-config"
QUERY = {
    "expected_revision": "12",
    "provider": "bank",
    "value": "balance",
    "listed_reason": "late_success_after_expiry",
    "next": f"{LIST_URL}#outbox-{OUTBOX}",
}
STATES_OFF_REASON = (
    "Needs <code>states.level</code> at <code>prompt_only</code> or above "
    "(this story runs <code>off</code>)"
)


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
    monkeypatch.setattr(inspect_reconciliation, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v3())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond("POST", SETTLE, 200, json=fixture("settled_now"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def settles(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST" and r.url.path == SETTLE_PATH]


def posts(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST"]


def get(client: Client, url: str = URL, query: dict[str, str] | None = None) -> str:
    target = f"{url}?{urlencode(QUERY if query is None else query)}"
    response = client.get(target)
    assert response.status_code == 200
    return response.content.decode()


def post(client: Client, url: str = URL, **fields: Any) -> str:
    data: dict[str, Any] = {"idempotency_key": KEY, "confirmed": "1", **QUERY}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    response = client.post(url, data)
    assert response.status_code == 200
    return response.content.decode()


def hidden(html: str, name: str) -> str | None:
    found = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)">', html)
    return unescape(found.group(1)) if found is not None else None


def fact(html: str, label: str) -> str:
    found = re.search(rf"<dt>{re.escape(label)}</dt><dd>(.*?)</dd>", html, re.S)
    assert found is not None, label
    return found.group(1)


def error(html: str) -> str:
    found = re.search(r'<p class="errorlist confirm-error">(.*?)</p>', html, re.S)
    assert found is not None
    return found.group(1)


def lines(html: str) -> list[str]:
    found = re.search(r'<ul class="reconciliation-result-lines">(.*?)</ul>', html, re.S)
    assert found is not None
    return re.findall(r"<li [^>]*>(.*?)</li>", found.group(1), re.S)


def more(html: str) -> str:
    found = re.search(r'<details class="reconciliation-result-more">(.*?)</details>', html, re.S)
    assert found is not None
    return found.group(1)


def is_confirm_page(html: str) -> bool:
    return '<div class="confirm">' in html and "reconciliation-result-lines" not in html


# --- the confirm page --------------------------------------------------------------------------


def test_confirm_get_makes_no_engine_call(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert "<title>Settle now — Epistrel Console</title>" in html
    assert f"Settle effect <em>{OUTBOX}</em> now?" in html
    assert "The Engine re-checks it against the story as it stands now" in unescape(html)
    assert "leaves the reconciliation list." in html
    assert fact(html, "Story id") == f"<code>{STORY}</code>"
    assert fact(html, "Outbox id") == f"<code>{OUTBOX}</code>"
    provider = reverse("engine:provider", args=["bank"])
    assert fact(html, "Provider") == f'<a href="{provider}"><code>bank</code></a>'
    assert fact(html, "Provider value") == "<code>balance</code>"
    assert fact(html, "Listed as") == "<code>Late success after expiry</code>"
    assert hidden(html, "expected_revision") == "12"
    assert hidden(html, "confirmed") == "1"
    assert hidden(html, "provider") == "bank"
    assert hidden(html, "listed_reason") == "late_success_after_expiry"
    assert '<button type="submit">Settle now</button>' in html
    assert 'class="danger"' not in html
    # **Complement:** the fake's call log is empty.
    assert engine.requests == []


def test_confirm_facts_dash_when_absent(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, query={})
    for label in ("Provider", "Provider value", "Listed as"):
        assert fact(html, label) == "<code>—</code>"
    assert hidden(html, "expected_revision") is None
    assert hidden(html, "provider") is None
    assert engine.requests == []


def test_url_outbox_id_is_trimmed_and_upper_cased(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = get(operator_client, URL.replace(OUTBOX, OUTBOX.lower()))
    assert fact(html, "Outbox id") == f"<code>{OUTBOX}</code>"
    post(operator_client, URL.replace(OUTBOX, OUTBOX.lower()))
    assert len(settles(engine)) == 1


def test_cancel_link_returns_to_the_row(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, query={**QUERY, "next": f"{LIST_URL}?x=1#outbox-{OUTBOX}"})
    assert f'<a href="{escape(f"{LIST_URL}?x=1#outbox-{OUTBOX}")}">Cancel</a>' in html
    # Without (or with an off-site) next: the row's anchor.
    for query in ({}, {"next": "https://evil.example/"}):
        html = get(operator_client, query=query)
        assert f'<a href="{LIST_URL}#outbox-{OUTBOX}">Cancel</a>' in html
    assert engine.requests == []


def test_post_without_confirmed_rerenders_and_calls_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post(operator_client, confirmed=None)
    assert is_confirm_page(html)
    assert hidden(html, "idempotency_key") == KEY
    # **Complement:** no POST to …/settle-now (and no read either).
    assert settles(engine) == []
    assert engine.requests == []


def test_confirmed_post_sends_the_key_and_expected_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    post(operator_client, expected_revision="11")
    (sent,) = settles(engine)
    assert sent.content == b""
    assert sent.headers["Idempotency-Key"] == KEY
    assert sent.headers["X-Epistrel-Expected-Revision"] == "11"
    assert [(r.method, r.url.path) for r in engine.requests][:2] == [
        ("GET", CONFIG_PATH),
        ("POST", SETTLE_PATH),
    ]
    # **Complement:** a blank or non-digit expected_revision sends no such header.
    for revision in ("", "x"):
        engine.requests.clear()
        post(operator_client, expected_revision=revision)
        (sent,) = settles(engine)
        assert "X-Epistrel-Expected-Revision" not in sent.headers


# --- the result page ---------------------------------------------------------------------------


def test_settled_result_lines(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client)
    row = EngineOperation.objects.get(kind="settle_now")
    position = five_part(fixture("settled_now")["slot"])
    assert position is not None
    slot_text = knowledge_position_text(position)
    assert slot_text == "turn 3⁺² · start"
    outbox_link = f"{LIST_URL}#outbox-{OUTBOX}"
    assert (
        f'Settled: effect <a href="{outbox_link}"><code class="outbox-id">{OUTBOX}</code></a> is '
        "applied</h3>" in html
    )
    operation = reverse("operations:detail", args=[row.pk])
    assert lines(html) == [
        f'<a href="{knowledge_position_url(STORY, position)}">Slot: {slot_text}</a>',
        f'<a href="{operation}">Operation: <code>01JM6QP0000000000000000051</code></a>',
        "Story revision now 13",
        "Re-verified: passed",
    ]
    # The Engine's position string only in More.
    assert html.count("3⁺2") == 1
    assert "<dt>Engine position</dt><dd><code>3⁺2</code></dd>" in more(html)
    assert "<dt>Outcome id</dt><dd><code>01JM60C0000000000000000001</code></dd>" in more(html)
    assert f"<dt>Idempotency key</dt><dd><code>{KEY}</code></dd>" in more(html)
    assert f'<a class="reconciliation-back" href="{LIST_URL}">Back to Reconciliation</a>' in html
    assert f'href="{SETTLED_URL}?report={row.pk}">Link to this result</a>' in html
    assert f'<a class="active" aria-current="page" href="{LIST_URL}">Reconciliation</a>' in html
    assert "<title>Inspect · Settle now · Harbour Ledger" in html
    assert "Reasons:" not in html


def test_queued_result_says_queued(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", SETTLE, 200, json=fixture("settled_now_queued"))
    html = post(operator_client)
    assert escape(QUEUED_TEXT) in html
    assert escape(QUEUED_STAYS) in html
    assert lines(html) == ["Slot: —", "Story revision now 12", "Re-verified: passed"]
    # **Complement:** no "Settled" text and no operation line.
    assert "Settled" not in html
    assert "Operation:" not in html


def test_reverification_and_reasons(operator_client: Client, engine: FakeEngine) -> None:
    body = {**fixture("settled_now"), "reverification": "skipped", "reasons": ["bounds:max"]}
    engine.respond("POST", SETTLE, 200, json=body)
    html = post(operator_client)
    assert lines(html)[-1] == "Re-verified: Skipped"
    assert "Reasons:" in html
    assert "<li>it would break the <code>max</code> bound</li>" in html


def test_other_result_value(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", SETTLE, 200, json={**fixture("settled_now"), "result": "deferred_x"})
    html = post(operator_client)
    assert "Result: Deferred x</h3>" in html
    assert "Settled" not in html
    assert escape(QUEUED_TEXT) not in html
    assert lines(html)[0].startswith("<a href=")


def test_report_rerenders_from_the_log_row(operator_client: Client, engine: FakeEngine) -> None:
    first = post(operator_client)
    row = EngineOperation.objects.get(kind="settle_now")
    engine.requests.clear()
    response = operator_client.get(f"{SETTLED_URL}?report={row.pk}")
    assert response.status_code == 200
    html = response.content.decode()
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert lines(html) == lines(first)
    assert f'<a class="active" aria-current="page" href="{LIST_URL}">Reconciliation</a>' in html


@pytest.mark.parametrize("case", ["kind", "story", "error", "unparsed", "nonint", "missing"])
def test_report_unavailable(operator_client: Client, engine: FakeEngine, case: str) -> None:
    post(operator_client)
    row = EngineOperation.objects.get(kind="settle_now")
    report: object = row.pk
    if case == "kind":
        row.kind = "compensate"
    elif case == "story":
        row.story_id = "01JM6ST0000000000000000002"
    elif case == "error":
        row.outcome = EngineOperation.Outcome.ERROR
    elif case == "unparsed":
        row.response = {"result": 3}
    elif case == "nonint":
        report = "x"
    elif case == "missing":
        report = row.pk + 1
    row.save()
    engine.requests.clear()
    response = operator_client.get(f"{SETTLED_URL}?report={report}")
    assert response.status_code == 200
    html = response.content.decode()
    assert f'{escape(RESULT_UNAVAILABLE)} — <a href="{LIST_URL}">Reconciliation</a>' in html
    assert "reconciliation-result-lines" not in html
    assert [r.url.path for r in engine.requests] == SHELL_PATHS


def test_report_without_param_is_unavailable(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(SETTLED_URL)
    assert response.status_code == 200
    assert escape(RESULT_UNAVAILABLE) in response.content.decode()


def test_replayed_row_says_replayed(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", SETTLE, 200, json=fixture("settled_now"), headers={"Idempotent-Replay": "true"}
    )
    assert "Replayed (repeated submit)" in post(operator_client)


# --- the gate ----------------------------------------------------------------------------------


def test_gate_closed_refuses_without_writing(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = post(operator_client)
    assert is_confirm_page(html)
    config = reverse("stories:config", args=[STORY])
    assert error(html) == f'{STATES_OFF_REASON} — <a href="{config}">Config</a>'
    assert hidden(html, "idempotency_key") == KEY  # a local refusal keeps the posted key
    # **Complement:** the fake saw GET …/effective-config and no POST (C6).
    assert [(r.method, r.url.path) for r in engine.requests] == [("GET", CONFIG_PATH)]
    assert posts(engine) == []


def test_effective_config_read_fails_refuses(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 503, json=error_json("unavailable", "down"))
    html = post(operator_client)
    assert error(html) == escape(GATE_UNREAD)
    assert posts(engine) == []


def test_gate_read_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 503, json=error_json("erasure_pending", "held"))
    response = operator_client.post(URL, {"idempotency_key": KEY, "confirmed": "1", **QUERY})
    assert response.status_code == 503
    assert posts(engine) == []


# --- refusals ----------------------------------------------------------------------------------


def refuse(
    engine: FakeEngine,
    status: int,
    code: str,
    headers: dict[str, str] | None = None,
    **details: Any,
) -> None:
    engine.respond("POST", SETTLE, status, json=error_json(code, **details), headers=headers)


def refused(client: Client) -> str:
    html = post(client)
    assert is_confirm_page(html)
    assert "Settled" not in html
    assert hidden(html, "provider") == "bank"
    assert hidden(html, "expected_revision") == "12"
    return html


def test_settle_refused_changes_nothing(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "settle_refused", reasons=["precondition:ownership", "bounds:max"])
    response = operator_client.post(URL, {"idempotency_key": KEY, "confirmed": "1", **QUERY})
    # **Complement:** 200, not a redirect; no result page; only the one POST (no retry).
    assert response.status_code == 200
    html = response.content.decode()
    assert is_confirm_page(html)
    assert error(html) == (
        "Settling was refused: the seller no longer owns it; it would break the "
        "<code>max</code> bound"
    )
    key = hidden(html, "idempotency_key")
    assert key is not None
    assert key != KEY  # an answered refusal: a fresh key
    assert len(settles(engine)) == 1
    assert len(posts(engine)) == 1


def test_no_listing_links_back(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "not_found", reason="no_listing")
    html = refused(operator_client)
    assert error(html) == (
        "This effect is no longer listed for reconciliation — reload the list — "
        f'<a href="{LIST_URL}">Reconciliation</a>'
    )


@pytest.mark.parametrize(
    ("status", "code", "headers", "details", "expected"),
    [
        (
            409,
            "module_off",
            None,
            {"module": "states"},
            STATES_OFF_REASON.replace("(this story runs <code>off</code>)", ""),
        ),
        (
            423,
            "story_busy",
            {"Retry-After": "7"},
            {"reason": "in_flight"},
            "Story busy — another turn is running · retry after 7 s",
        ),
        (
            409,
            "story_archived",
            None,
            {},
            "This story is archived — unarchive it to settle an effect.",
        ),
        (409, "story_paused", None, {}, "paused"),
        (409, "revision_conflict", None, {"current_revision": 14}, "changed"),
    ],
)
def test_refusals_use_world_refusal(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    headers: dict[str, str] | None,
    details: dict[str, Any],
    expected: str,
) -> None:
    refuse(engine, status, code, headers, **details)
    html = refused(operator_client)
    text = error(html)
    assert expected.strip() in text
    if code == "module_off":
        assert f'— <a href="{reverse("stories:config", args=[STORY])}">Config</a>' in text
    assert hidden(html, "idempotency_key") != KEY
    assert len(settles(engine)) == 1


def test_unknown_error_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "later_code")
    html = refused(operator_client)
    assert 'class="engine-error' in html
    assert "later_code" in html
    assert 'class="errorlist confirm-error"' not in html


def test_unreachable_keeps_the_posted_key(operator_client: Client, engine: FakeEngine) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    engine.side_effect("POST", SETTLE, boom)
    html = refused(operator_client)
    assert 'class="engine-error' in html
    assert hidden(html, "idempotency_key") == KEY


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 503, "erasure_pending")
    response = operator_client.post(URL, {"idempotency_key": KEY, "confirmed": "1", **QUERY})
    assert response.status_code == 503
    assert len(settles(engine)) == 1


# --- a malformed outbox id ---------------------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "post"])
def test_malformed_outbox_id_is_404_with_no_call(
    operator_client: Client, engine: FakeEngine, method: str
) -> None:
    url = URL.replace(OUTBOX, "not-an-id")
    data = {"idempotency_key": KEY, "confirmed": "1"} if method == "post" else {}
    response = getattr(operator_client, method)(url, data)
    assert response.status_code == 404
    html = response.content.decode()
    assert escape(MALFORMED_OUTBOX_ID) in html
    assert f'<a href="{LIST_URL}">← Reconciliation</a>' in html
    assert engine.requests == []

"""Resolve a listed effect as applied elsewhere or abandoned, behind a confirm page (#326).

Runs against the real urlconf on the schema-validating fake Engine, under Full v4 (E16) and
#292's M7 cast (``m7/story.json``, revision 20). ``m7/reconciliation_m7.json`` lists a
request-keyed ``reservation_expired`` row (``RQ…01``, no outbox row), an ``imported_unsettled``
row (``XB…05``) and a ``late_success_after_expiry`` row (``XB…02``); ``m7/listing_resolved.json``
resolves ``RQ…01`` at turn 4⁺¹ (revision 21, operation ``…QP…12``, Engine position ``4⁺1``).
Refusal bodies are built inline (D16)."""

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
    MALFORMED_LISTING_ID,
    RESOLUTION_BLOCKED,
    RESULT_UNAVAILABLE,
)
from console.views import inspect as views
from console.views import inspect_reconciliation
from console.world import five_part
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, full_v4, lite_m6
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM7ST0000000000000000001"
REQUEST = "01JM7RQ0000000000000000001"
IMPORTED = "01JM7XB0000000000000000005"
KEY = "01JM7QP00000000000000000K1"
RESOLVE = "/stories/{story_id}/reconciliation/{listing}/resolve"
LIST_URL = f"/stories/{STORY}/inspect/effects/reconciliation/"
RESOLVED_URL = f"{LIST_URL}resolved/"
SHELL_PATHS = [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
CONFIG_PATH = f"/stories/{STORY}/effective-config"
STATES_OFF_REASON = (
    "Needs <code>states.level</code> at <code>prompt_only</code> or above "
    "(this story runs <code>off</code>)"
)


def url(listing: str = IMPORTED) -> str:
    return f"{LIST_URL}{listing}/resolve/"


def resolve_path(listing: str) -> str:
    return f"/stories/{STORY}/reconciliation/{listing}/resolve"


def query(listing: str = IMPORTED, **changes: str | None) -> dict[str, str]:
    """#249's ``action_query`` for the row ``listing`` names, plus ``resolution``."""
    imported = listing == IMPORTED
    base: dict[str, str | None] = {
        "resolution": "abandoned",
        "expected_revision": "20",
        "provider": "bank",
        "value": "balance",
        "listed_reason": "imported_unsettled" if imported else "reservation_expired",
        "next": f"{LIST_URL}#{'outbox' if imported else 'request'}-{listing}",
        **changes,
    }
    return {k: v for k, v in base.items() if v is not None}


def answer_for(listing: str, resolution: str = "abandoned", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m7/listing_resolved"))
    body.update(request_id=listing, resolution=resolution, **changes)
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
    fake.respond("GET", EFFECTIVE, 200, json=full_v4())
    fake.respond("GET", STORY_PATH, 200, json=engine_fixture("m7/story"))
    fake.respond(
        "POST",
        RESOLVE,
        200,
        json=answer_for(IMPORTED, reasons=["imported_unsettled"]),
    )
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def resolves(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST" and r.url.path.endswith("/resolve")]


def posts(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST"]


def get(client: Client, target: str | None = None, data: dict[str, str] | None = None) -> str:
    response = client.get(f"{target or url()}?{urlencode(query() if data is None else data)}")
    assert response.status_code == 200
    return response.content.decode()


def post(client: Client, target: str | None = None, **fields: Any) -> str:
    listing = IMPORTED if target is None or IMPORTED in target else REQUEST
    data: dict[str, Any] = {"idempotency_key": KEY, "confirmed": "1", **query(listing)}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    response = client.post(target or url(), data)
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
    assert "<title>Resolve effect — Epistrel Console</title>" in html
    assert f"Mark <em>{IMPORTED}</em> as abandoned?" in html
    assert (
        "It will never be applied. Nothing is sent and no outcome is recorded; the listing "
        "closes." in html
    )
    assert "Recorded as an audit event at the next inter-turn slot." in html
    assert "The consumer applied it outside Epistrel" not in html
    assert fact(html, "Story id") == f"<code>{STORY}</code>"
    assert fact(html, "Outbox id") == f"<code>{IMPORTED}</code>"
    provider = reverse("engine:provider", args=["bank"])
    assert fact(html, "Provider") == f'<a href="{provider}"><code>bank</code></a>'
    assert fact(html, "Provider value") == "<code>balance</code>"
    assert fact(html, "Listed as") == "<code>Imported unsettled</code>"
    assert hidden(html, "resolution") == "abandoned"
    assert hidden(html, "expected_revision") == "20"
    assert hidden(html, "confirmed") == "1"
    assert hidden(html, "provider") == "bank"
    assert hidden(html, "value") == "balance"
    assert hidden(html, "listed_reason") == "imported_unsettled"
    assert '<button type="submit">Mark abandoned</button>' in html
    assert 'class="danger"' not in html
    # **Complement:** the fake's call log is empty.
    assert engine.requests == []


def test_applied_elsewhere_on_a_request_row(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, url(REQUEST), query(REQUEST, resolution="applied_elsewhere"))
    assert f"Mark <em>{REQUEST}</em> as applied elsewhere?" in html
    assert (
        "The consumer applied it outside Epistrel. Nothing is sent and no outcome is recorded; "
        "the listing closes." in html
    )
    assert fact(html, "Request id") == f"<code>{REQUEST}</code>"
    assert fact(html, "Listed as") == "<code>Reservation expired</code>"
    assert hidden(html, "resolution") == "applied_elsewhere"
    assert '<button type="submit">Mark applied elsewhere</button>' in html
    assert engine.requests == []


def test_listing_fact_is_neutral_without_a_listed_reason(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = get(operator_client, data={"resolution": "abandoned"})
    assert fact(html, "Listing id") == f"<code>{IMPORTED}</code>"
    for label in ("Provider", "Provider value", "Listed as"):
        assert fact(html, label) == "<code>—</code>"
    assert hidden(html, "expected_revision") is None
    assert f'<a href="{LIST_URL}">Cancel</a>' in html
    assert engine.requests == []


def test_listing_id_is_trimmed_and_upper_cased(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, url(IMPORTED.lower()))
    assert fact(html, "Outbox id") == f"<code>{IMPORTED}</code>"
    post(operator_client, url(IMPORTED.lower()))
    (sent,) = resolves(engine)
    assert sent.url.path == resolve_path(IMPORTED)


def test_cancel_returns_to_the_row(operator_client: Client, engine: FakeEngine) -> None:
    custom = f"{LIST_URL}?x=1#outbox-{IMPORTED}"
    html = get(operator_client, data=query(next=custom))
    assert f'<a href="{escape(custom)}">Cancel</a>' in html
    # Without (or with an off-site) next: the row's anchor by its kind.
    for listing, anchor in ((IMPORTED, f"outbox-{IMPORTED}"), (REQUEST, f"request-{REQUEST}")):
        for nxt in (None, "https://evil.example/"):
            html = get(operator_client, url(listing), query(listing, next=nxt))
            assert f'<a href="{LIST_URL}#{anchor}">Cancel</a>' in html
    assert engine.requests == []


def test_post_without_confirmed_calls_nothing(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, confirmed=None)
    assert is_confirm_page(html)
    assert hidden(html, "idempotency_key") == KEY
    # **Complement:** no POST to …/resolve (and no read either).
    assert resolves(engine) == []
    assert engine.requests == []


def test_confirmed_post_sends_resolution_key_and_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    post(operator_client, expected_revision="19")
    (sent,) = resolves(engine)
    assert sent.url.path == resolve_path(IMPORTED)
    assert sent.content == b'{"resolution":"abandoned"}'
    assert sent.headers["Idempotency-Key"] == KEY
    assert sent.headers["X-Epistrel-Expected-Revision"] == "19"
    assert [(r.method, r.url.path) for r in engine.requests][:2] == [
        ("GET", CONFIG_PATH),
        ("POST", resolve_path(IMPORTED)),
    ]
    # **Complement:** a blank or non-digit expected_revision sends no such header.
    for revision in ("", "x"):
        engine.requests.clear()
        post(operator_client, expected_revision=revision)
        (sent,) = resolves(engine)
        assert "X-Epistrel-Expected-Revision" not in sent.headers


def test_imported_row_sends_its_outbox_id(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, url(IMPORTED), resolution="applied_elsewhere")
    (sent,) = resolves(engine)
    assert sent.url.path == resolve_path(IMPORTED)
    assert sent.content == b'{"resolution":"applied_elsewhere"}'


def test_request_row_sends_its_request_id(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RESOLVE, 200, json=answer_for(REQUEST))
    html = post(operator_client, url(REQUEST))
    (sent,) = resolves(engine)
    assert sent.url.path == resolve_path(REQUEST)
    assert REQUEST in html


def test_bad_resolution_is_blocked(operator_client: Client, engine: FakeEngine) -> None:
    for value in ("settled", "", "Abandoned", None):
        html = get(operator_client, data=query(resolution=value))
        assert is_confirm_page(html)
        assert f"Mark <em>{IMPORTED}</em> as resolved?" in html
        assert f'<p class="warning">{RESOLUTION_BLOCKED}</p>' in html
        assert '<button type="submit" disabled>Resolve</button>' in html
        assert hidden(html, "resolution") is None
        assert "Recorded as an audit event at the next inter-turn slot." in html
        assert "the listing closes" not in html
    # **Complement:** no POST, even with confirmed=1.
    html = post(operator_client, resolution="settled")
    assert is_confirm_page(html)
    assert RESOLUTION_BLOCKED in html
    assert hidden(html, "idempotency_key") == KEY
    assert engine.requests == []


@pytest.mark.parametrize("method", ["get", "post"])
def test_malformed_listing_id_is_404_with_no_call(
    operator_client: Client, engine: FakeEngine, method: str
) -> None:
    data = {"idempotency_key": KEY, "confirmed": "1", **query()} if method == "post" else {}
    response = getattr(operator_client, method)(url("not-an-id"), data)
    assert response.status_code == 404
    html = response.content.decode()
    assert escape(MALFORMED_LISTING_ID) in html
    assert f'<a href="{LIST_URL}">← Reconciliation</a>' in html
    assert engine.requests == []


# --- the result page ---------------------------------------------------------------------------


def test_result_lines(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client)
    row = EngineOperation.objects.get(kind="resolve_listing")
    position = five_part(engine_fixture("m7/listing_resolved")["slot"])
    assert position is not None
    slot_text = knowledge_position_text(position)
    assert f'Resolved: <code class="outbox-id">{IMPORTED}</code> marked abandoned</h3>' in html
    operation = reverse("operations:detail", args=[row.pk])
    assert lines(html) == [
        "Listed as: Imported unsettled",
        f'<a href="{knowledge_position_url(STORY, position)}">Slot: {slot_text}</a>',
        f'<a href="{operation}">Operation: <code>01JM7QP0000000000000000012</code></a>',
        "Story revision now 21",
    ]
    # The Engine's position string only in More.
    assert html.count("4⁺1") == 1
    assert "<dt>Engine position</dt><dd><code>4⁺1</code></dd>" in more(html)
    assert "<dt>Idempotency key</dt><dd><code>01JM7KY0000000000000000001</code></dd>" in more(html)
    assert f'<a class="reconciliation-back" href="{LIST_URL}">Back to Reconciliation</a>' in html
    assert f'href="{RESOLVED_URL}?report={row.pk}">Link to this result</a>' in html
    assert f'<a class="active" aria-current="page" href="{LIST_URL}">Reconciliation</a>' in html
    assert "<title>Inspect · Resolve effect · Harbour Ledger" in html
    assert "Reasons:" not in html


def test_result_lists_every_reason_and_dashes_none(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = answer_for(REQUEST, "applied_elsewhere", reasons=["reservation_expired", "later_x"])
    engine.respond("POST", RESOLVE, 200, json=body)
    html = post(operator_client, url(REQUEST), resolution="applied_elsewhere")
    assert f'Resolved: <code class="outbox-id">{REQUEST}</code> marked applied elsewhere' in html
    assert lines(html)[0] == "Listed as: Reservation expired, Later x"
    engine.respond("POST", RESOLVE, 200, json=answer_for(REQUEST, reasons=[]))
    assert lines(post(operator_client, url(REQUEST)))[0] == "Listed as: —"


def test_report_rerenders_from_the_log_row(operator_client: Client, engine: FakeEngine) -> None:
    first = post(operator_client)
    row = EngineOperation.objects.get(kind="resolve_listing")
    engine.requests.clear()
    response = operator_client.get(f"{RESOLVED_URL}?report={row.pk}")
    assert response.status_code == 200
    html = response.content.decode()
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert lines(html) == lines(first)
    assert f"{IMPORTED}</code> marked abandoned</h3>" in html
    assert "<title>Inspect · Resolve effect · Harbour Ledger" in html


@pytest.mark.parametrize("case", ["kind", "story", "error", "unparsed", "nonint", "missing"])
def test_report_unavailable(operator_client: Client, engine: FakeEngine, case: str) -> None:
    """**Complement** of the report: another story's row, a ``settle_now`` row, a failed or
    unparseable one, or no row at all → the unavailable line."""
    post(operator_client)
    row = EngineOperation.objects.get(kind="resolve_listing")
    report: object = row.pk
    if case == "kind":
        row.kind = "settle_now"
    elif case == "story":
        row.story_id = "01JM7ST0000000000000000002"
    elif case == "error":
        row.outcome = EngineOperation.Outcome.ERROR
    elif case == "unparsed":
        row.response = {"resolution": 3}
    elif case == "nonint":
        report = "x"
    elif case == "missing":
        report = row.pk + 1
    row.save()
    engine.requests.clear()
    response = operator_client.get(f"{RESOLVED_URL}?report={report}")
    assert response.status_code == 200
    html = response.content.decode()
    assert f'{escape(RESULT_UNAVAILABLE)} — <a href="{LIST_URL}">Reconciliation</a>' in html
    assert "reconciliation-result-lines" not in html
    assert [r.url.path for r in engine.requests] == SHELL_PATHS


def test_an_unknown_resolution_in_the_answer_is_humanised(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", RESOLVE, 200, json=answer_for(IMPORTED, "later_value"))
    assert "marked Later value</h3>" in post(operator_client)


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


def test_config_read_failure_refuses(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 503, json=error_json("unavailable", "down"))
    html = post(operator_client)
    assert error(html) == escape(GATE_UNREAD)
    assert posts(engine) == []


# --- refusals ----------------------------------------------------------------------------------


def refuse(
    engine: FakeEngine,
    status: int,
    code: str,
    headers: dict[str, str] | None = None,
    **details: Any,
) -> None:
    engine.respond("POST", RESOLVE, status, json=error_json(code, **details), headers=headers)


def refused(client: Client) -> str:
    response = client.post(url(), {"idempotency_key": KEY, "confirmed": "1", **query()})
    # **Complement:** 200 on the confirm page, not a redirect and never the result page.
    assert response.status_code == 200
    html = response.content.decode()
    assert is_confirm_page(html)
    assert "Resolved:" not in html
    assert hidden(html, "resolution") == "abandoned"
    assert hidden(html, "provider") == "bank"
    assert hidden(html, "expected_revision") == "20"
    return html


def test_no_listing_links_back(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "not_found", reason="no_listing")
    html = refused(operator_client)
    assert error(html) == (
        "This effect is no longer listed for reconciliation — reload the list — "
        f'<a href="{LIST_URL}">Reconciliation</a>'
    )
    assert hidden(html, "idempotency_key") != KEY
    assert len(posts(engine)) == 1


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
            "This story is archived — unarchive it to resolve an effect.",
        ),
        (
            409,
            "story_paused",
            None,
            {"cause": "backfill", "progress": {"chunks_done": 2, "chunks_total": 5}},
            "Paused: backfill in progress",
        ),
        (
            409,
            "story_importing",
            None,
            {"job_id": "01JM7JB0000000000000000007", "stage": "validating"},
            "This story is being imported — its pages open once the import ends",
        ),
        (409, "revision_conflict", None, {"current_revision": 22}, "changed"),
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
    assert expected.strip() in unescape(text) or expected.strip() in text
    if code == "module_off":
        assert f'— <a href="{reverse("stories:config", args=[STORY])}">Config</a>' in text
    if code == "story_importing":
        job = reverse("engine:import-job", args=["01JM7JB0000000000000000007"])
        assert f'— <a href="{job}">See the import</a>' in text
    if code in ("story_busy", "revision_conflict", "story_archived"):
        assert "<a " not in text
    assert hidden(html, "idempotency_key") != KEY
    # **Complement:** one POST only (no retry).
    assert len(resolves(engine)) == 1
    assert len(posts(engine)) == 1


def test_unknown_error_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "later_code")
    html = refused(operator_client)
    assert 'class="engine-error' in html
    assert "later_code" in html
    assert 'class="errorlist confirm-error"' not in html


def test_unreachable_keeps_the_posted_key(operator_client: Client, engine: FakeEngine) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    engine.side_effect("POST", RESOLVE, boom)
    html = refused(operator_client)
    assert 'class="engine-error' in html
    assert hidden(html, "idempotency_key") == KEY


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 503, "erasure_pending")
    response = operator_client.post(url(), {"idempotency_key": KEY, "confirmed": "1", **query()})
    assert response.status_code == 503
    assert len(resolves(engine)) == 1
